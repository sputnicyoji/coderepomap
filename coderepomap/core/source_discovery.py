"""
Scan source roots and bucket files by language.

Single-language config (legacy v0.1.0 shape):
    lang: csharp
    source:
      root_path: .
      exclude_patterns: [...]

Multi-language config (new):
    langs: [csharp, lua]
    sources:
      csharp:
        root_path: Assets/Scripts
        exclude_patterns: [...]
      lua:
        root_path: Assets/LuaScripts
        exclude_patterns: [...]

In both modes the language plug-in supplies `file_extensions` and merges
its `default_excludes` with the user list.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from .registry import get_parser


@dataclass(frozen=True)
class SourceFile:
    """A single source file scoped to its language's source root."""
    lang: str
    path: Path
    root: Path  # the language-specific source root (NOT global project root)


def _match_pattern(rel_path: str, pattern: str) -> bool:
    rel_path = rel_path.replace("\\", "/")
    pattern = pattern.replace("\\", "/")
    if pattern.startswith("**/"):
        return fnmatch.fnmatch(rel_path, pattern[3:]) or "/" + pattern[3:] in "/" + rel_path
    return fnmatch.fnmatch(rel_path, pattern)


def _scan_one(
    lang: str,
    root: Path,
    extra_excludes: List[str],
    extra_extensions: List[str] = None,
) -> List[SourceFile]:
    """Scan `root` for files matching the language's extensions + any
    user-supplied extras, applying merged excludes.
    """
    parser_cls = type(get_parser(lang))  # class-level attrs are enough; no need to keep instance
    # Merge user extensions on top of parser defaults (no duplicates, preserve order).
    exts = list(parser_cls.file_extensions)
    if extra_extensions:
        for e in extra_extensions:
            if e not in exts:
                exts.append(e)
    excludes = list(parser_cls.default_excludes) + list(extra_excludes)

    if not root.exists():
        raise FileNotFoundError(f"Source root for lang={lang!r} not found: {root}")

    out: List[SourceFile] = []
    for ext in exts:
        for f in root.rglob(f"*{ext}"):
            rel = str(f.relative_to(root))
            if any(_match_pattern(rel, p) for p in excludes):
                continue
            out.append(SourceFile(lang=lang, path=f, root=root))
    return out


def discover(config: dict, project_root: Path) -> List[SourceFile]:
    """Resolve config -> flat list of SourceFile across all configured languages.

    Validates that exactly one of `lang` / `langs` is set. `lang` uses the
    legacy `source` block; `langs` uses the multi-lang `sources` mapping.
    """
    has_lang = "lang" in config
    has_langs = "langs" in config
    if has_lang and has_langs:
        raise ValueError("config has both `lang` and `langs`; pick one")
    if not has_lang and not has_langs:
        # Backward-compat tail: legacy 0.1.0 config without explicit `lang`
        # defaults to C# (the only language v0.1.0 supported).
        has_lang = True
        config = {**config, "lang": "csharp"}

    # Schema mismatch guards: silently falling back to defaults when the user
    # mixed the two shapes (e.g. `lang:` + `sources:`) made roots collapse to
    # cwd and exclude lists become no-ops. Fail loudly instead.
    if has_lang and "sources" in config:
        raise ValueError(
            "config uses single-lang `lang:` but also defines plural `sources:`. "
            "Either drop `sources:` and put settings under singular `source:`, "
            "or switch to multi-lang shape (`langs: [csharp]` + `sources:`)."
        )
    if has_langs and "source" in config:
        raise ValueError(
            "config uses multi-lang `langs:` but also defines singular `source:`. "
            "Either drop `source:` and put settings under plural `sources.<lang>:`, "
            "or switch to single-lang shape (`lang: <lang>` + `source:`)."
        )

    if has_lang:
        lang = config["lang"]
        src = config.get("source", {}) or {}
        root = project_root / src.get("root_path", ".")
        excludes = src.get("exclude_patterns", []) or []
        extra_exts = src.get("file_extensions", []) or []
        return _scan_one(lang, root, excludes, extra_exts)

    # has_langs
    langs = config["langs"]
    sources: Dict[str, dict] = config.get("sources", {}) or {}
    if not isinstance(langs, list) or not langs:
        raise ValueError("`langs` must be a non-empty list")
    missing = [l for l in langs if l not in sources]
    if missing:
        raise ValueError(f"`langs` includes {missing} but `sources` has no entry for them")

    out: List[SourceFile] = []
    for lang in langs:
        cfg = sources[lang] or {}
        root = project_root / cfg.get("root_path", ".")
        excludes = cfg.get("exclude_patterns", []) or []
        extra_exts = cfg.get("file_extensions", []) or []
        out.extend(_scan_one(lang, root, excludes, extra_exts))
    return out


__all__ = ["SourceFile", "discover"]
