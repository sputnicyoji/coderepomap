#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RepoMap Generator orchestrator.

Wires together source_discovery -> language parser plug-ins -> graph_builder
-> renderer. Language-agnostic: no `.cs` / `.lua` strings here, only
delegations into the registry.

Output is byte-equivalent to v0.1.0 when fed an equivalent single-language
C# project. Multi-language projects emit per-language sections in L1/L2/L3
(see Phase 8 crosslang for resolved cross-language edges).
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

if sys.stdout:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
if sys.stderr:
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

from .graph_builder import build_graph
from .parser_base import Reference, Symbol
from .ranker import PageRankRanker
from . import crosslang, registry, renderer, source_discovery


class RepoMapGenerator:
    """Main orchestrator. v0.2.0+ layered implementation."""

    def __init__(self, config: Optional[dict] = None, project_root: Optional[Path] = None):
        self.config = config or self._get_default_config()
        self.project_root = project_root or Path.cwd()

        pr_cfg = self.config.get("pagerank", {}) or {}
        self.ranker = PageRankRanker(
            alpha=pr_cfg.get("alpha", 0.85),
            max_iter=pr_cfg.get("max_iter", 100),
        )

        # Populated by run()
        self.symbols: List[Symbol] = []
        self.references: List[Reference] = []
        self.unresolved_references: List[Reference] = []
        self.modules: Dict[str, dict] = {}

        out_cfg = self.config.get("output", {}) or {}
        output_dir = out_cfg.get("directory", ".repomap/output")
        self.output_dir = self.project_root / output_dir

    # --- Config -------------------------------------------------------------

    @staticmethod
    def _get_default_config() -> dict:
        """Language-agnostic defaults. Parser plug-ins supply file_extensions
        and merge their default_excludes when the user config doesn't.

        NOTE: `lang` is intentionally omitted here. `source_discovery.discover`
        applies the C# single-lang fallback when neither `lang` nor `langs`
        is set. If we put `lang: csharp` here, _deep_merge would carry it into
        any user multi-lang config that defines only `langs:`, triggering the
        "both lang and langs" error in discover.
        """
        return {
            "project_name": "",
            "source": {
                "root_path": ".",
                "exclude_patterns": [],
            },
            "tokens": {
                "l1_skeleton": 1000,
                "l2_signatures": 2000,
                "l3_relations": 3000,
                "encoding": "cl100k_base",
            },
            "pagerank": {"alpha": 0.85, "max_iter": 100},
            "output": {
                "directory": ".repomap/output",
                "files": {
                    "skeleton": "repomap-L1-skeleton.md",
                    "signatures": "repomap-L2-signatures.md",
                    "relations": "repomap-L3-relations.md",
                    "meta": "repomap-meta.json",
                },
            },
            "importance_boost": {"patterns": [], "priority_modules": []},
            "categories": {"Other": {"patterns": []}},
        }

    @staticmethod
    def load_config(config_path: Path) -> dict:
        default = RepoMapGenerator._get_default_config()
        user_loaded: dict = {}
        if HAS_YAML and config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
            # Be strict about config shape. Silently returning defaults on a
            # malformed yaml (list / scalar / parse error) was the upstream
            # symptom that caused the silent fallback to cwd scan.
            if loaded is None:
                pass  # empty file is fine; use defaults
            elif isinstance(loaded, dict):
                user_loaded = loaded
            else:
                raise ValueError(
                    f"config at {config_path} must be a YAML mapping at the "
                    f"top level, got {type(loaded).__name__}"
                )

        # Validate schema BEFORE merging, against the user-set fields only —
        # the default config always carries `source:` and that must not be
        # mistaken for a user-set field when raising mismatch errors.
        u_lang = "lang" in user_loaded
        u_langs = "langs" in user_loaded
        u_source = "source" in user_loaded
        u_sources = "sources" in user_loaded
        if u_lang and u_langs:
            raise ValueError(
                f"config at {config_path} sets both `lang:` and `langs:`; "
                f"pick one (single-lang uses `lang:`+`source:`, multi-lang "
                f"uses `langs:`+`sources:`)."
            )
        if u_lang and u_sources:
            raise ValueError(
                f"config at {config_path} uses single-lang `lang:` but also "
                f"defines plural `sources:`. Either drop `sources:` and put "
                f"settings under singular `source:`, or switch to multi-lang "
                f"shape (`langs: [csharp]` + `sources:`)."
            )
        if u_langs and u_source:
            raise ValueError(
                f"config at {config_path} uses multi-lang `langs:` but also "
                f"defines singular `source:`. Either drop `source:` and put "
                f"settings under plural `sources.<lang>:`, or switch to "
                f"single-lang shape (`lang: <lang>` + `source:`)."
            )

        # Merge AFTER validation. user_loaded shape is now guaranteed sane.
        if user_loaded:
            RepoMapGenerator._deep_merge(default, user_loaded)

        # Strip default-carried fields that don't match the user-chosen shape,
        # so source_discovery doesn't see stale defaults from the other shape.
        # Drive the decision off user_loaded (what the user actually wrote),
        # NOT off `default` (which always carries `source:`).
        if u_langs:
            default.pop("lang", None)
            default.pop("source", None)
        elif u_lang:
            default.pop("langs", None)
            default.pop("sources", None)
        return default

    @staticmethod
    def _deep_merge(base: dict, update: dict) -> None:
        for k, v in update.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                RepoMapGenerator._deep_merge(base[k], v)
            else:
                base[k] = v

    # --- Phases -------------------------------------------------------------

    def scan(self) -> List[source_discovery.SourceFile]:
        """Discover source files via the source_discovery layer."""
        return source_discovery.discover(self.config, self.project_root)

    def parse(self, files: List[source_discovery.SourceFile], verbose: bool = False) -> None:
        """Parse files, populate self.symbols / self.references.

        Per-language parsers are instantiated once and configured from the
        project config before any parsing happens.
        """
        if verbose:
            print(f"Parsing {len(files)} files...")
        parsers_cache: Dict[str, Any] = {}
        for i, sf in enumerate(files):
            if verbose and (i + 1) % 100 == 0:
                print(f"  Processed {i + 1}/{len(files)} files...")
            parser = parsers_cache.get(sf.lang)
            if parser is None:
                parser = registry.get_parser(sf.lang)
                parser.configure(self.config)
                parsers_cache[sf.lang] = parser
            syms, refs = parser.parse_file(sf.path, sf.root)
            self.symbols.extend(syms)
            self.references.extend(refs)
        if verbose:
            print(f"  Total: {len(self.symbols)} symbols, {len(self.references)} references")

    def build(self) -> None:
        """Resolve cross-lang refs, populate the ranker, capture unresolved."""
        # First: post-process references against the full symbol set. This
        # flips Lua-internal refs (require / call) and Lua->C# csharp_call
        # refs from `resolved=False, to_id=""` to `resolved=True, to_id=<id>`
        # when a match exists. Ambiguous CS short-name matches stay
        # unresolved but carry `lang_meta["candidates"]`.
        crosslang.resolve(self.symbols, self.references, self.config)

        boost_patterns = (
            self.config.get("importance_boost", {}).get("patterns", []) or []
        )
        # If the user config didn't set any importance_boost.patterns, fall
        # back to the parser plug-in's `default_boost_patterns`. Without this
        # fallback, fresh installs (or configs generated by `cmd_init` when
        # the template file is missing) produce flat boosts, hiding the
        # plug-in's curated defaults.
        if not boost_patterns:
            seen_langs: List[str] = []
            for sym in self.symbols:
                if sym.lang and sym.lang not in seen_langs:
                    seen_langs.append(sym.lang)
            merged: List[dict] = []
            for lang in seen_langs:
                try:
                    parser = registry.get_parser(lang)
                    merged.extend(list(parser.default_boost_patterns))
                except Exception:
                    pass
            boost_patterns = merged

        _, unresolved = build_graph(
            self.symbols, self.references, self.ranker, boost_patterns
        )
        self.unresolved_references = unresolved
        self.modules = renderer.build_module_stats(self.symbols)

    # --- Rendering -----------------------------------------------------------

    def _git(self, *args: str) -> str:
        try:
            r = subprocess.run(
                ["git", *args],
                capture_output=True, text=True, cwd=str(self.project_root),
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    def save_all(self, verbose: bool = False) -> dict:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        files_cfg = self.config["output"]["files"]
        project_name = self.config.get("project_name", "")
        encoding = self.config["tokens"].get("encoding", "cl100k_base")

        commit = self._git("rev-parse", "HEAD")
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
        today = datetime.now().strftime("%Y-%m-%d")
        iso = datetime.now().isoformat()

        # Source path: multi-lang -> comma-joined; single-lang or legacy
        # (neither `lang` nor `langs` declared) -> `source.root_path`.
        if "langs" in self.config:
            src_path = ",".join(
                self.config.get("sources", {}).get(l, {}).get("root_path", "")
                for l in (self.config.get("langs") or [])
            )
        else:
            src_path = self.config.get("source", {}).get("root_path", "")

        l1 = renderer.render_l1(
            self.symbols, self.ranker, self.modules, self.config,
            project_name=project_name, git_commit=commit, today_yyyy_mm_dd=today,
        )
        l2 = renderer.render_l2(
            self.symbols, self.ranker, self.config, project_name=project_name,
        )
        l3 = renderer.render_l3(
            self.references, self.ranker, self.unresolved_references,
            config=self.config, project_name=project_name,
        )
        meta = renderer.render_meta(
            self.symbols, self.references, self.ranker, self.modules,
            project_name=project_name, source_path=src_path,
            git_commit=commit, git_branch=branch, generated_at_iso=iso,
        )

        l1_path = self.output_dir / files_cfg["skeleton"]
        l2_path = self.output_dir / files_cfg["signatures"]
        l3_path = self.output_dir / files_cfg["relations"]
        meta_path = self.output_dir / files_cfg["meta"]

        l1_path.write_text(l1, encoding="utf-8")
        l2_path.write_text(l2, encoding="utf-8")
        l3_path.write_text(l3, encoding="utf-8")
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

        if verbose:
            for p, content in [(l1_path, l1), (l2_path, l2), (l3_path, l3)]:
                print(f"Generated: {p} ({renderer.count_tokens(content, encoding)} tokens)")
            print(f"Generated: {meta_path}")

        return {
            "l1": {"path": l1_path, "tokens": renderer.count_tokens(l1, encoding)},
            "l2": {"path": l2_path, "tokens": renderer.count_tokens(l2, encoding)},
            "l3": {"path": l3_path, "tokens": renderer.count_tokens(l3, encoding)},
            "meta": {"path": meta_path},
        }

    def run(self, verbose: bool = True) -> dict:
        start = datetime.now()
        project_name = self.config.get("project_name", "") or "Project"
        if verbose:
            print("=" * 60)
            print(f"RepoMap Generator - {project_name}")
            print("=" * 60)

        files = self.scan()
        if verbose:
            srcs = {sf.root for sf in files}
            print(f"Found {len(files)} files across {len(srcs)} source root(s)")
        self.parse(files, verbose)
        self.build()
        results = self.save_all(verbose)
        duration = (datetime.now() - start).total_seconds()
        if verbose:
            print("=" * 60)
            print(f"Done! ({duration:.1f}s)")
        return {
            "success": True,
            "duration": duration,
            "file_count": len(files),
            "results": results,
        }


__all__ = ["RepoMapGenerator"]
