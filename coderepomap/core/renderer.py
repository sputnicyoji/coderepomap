#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Markdown / JSON renderers for L1 / L2 / L3 / meta outputs.

Renderer is pure: takes Symbols + References + PageRankRanker + config and
returns strings (or dicts for meta). No file I/O, no git probing — those
live in the orchestrator (`generator.py`).

The output strings here are byte-equivalent to the v0.1.0 generator's
output when fed equivalent data. This is verified by the Phase -1
baseline (`tests/baseline/golden/*.md`).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Optional

from .parser_base import Reference, Symbol
from .ranker import PageRankRanker


# ----- Helpers ------------------------------------------------------------------

def count_tokens(text: str, encoding: str = "cl100k_base") -> int:
    """Token count via tiktoken if installed, else 4-chars-per-token fallback.

    Kept here (not pulled in from the global) so renderers stay self-contained.
    """
    try:
        import tiktoken
        try:
            enc = tiktoken.get_encoding(encoding)
            return len(enc.encode(text))
        except Exception:
            pass
    except ImportError:
        pass
    return len(text) // 4


def _module_from_file(file_path: str) -> str:
    """First path segment is the module name (matches v0.1.0 behavior)."""
    parts = file_path.replace("\\", "/").split("/")
    return parts[0] if parts else "Unknown"


# Symbol kinds that count as "entry" graph nodes for renderer purposes.
# This is renderer-internal, independent of build_graph's runtime node_kinds:
# the renderer always shows packages / interfaces / functions when present in
# the symbol set, while C#-only Symbol lists still produce the legacy
# class_count for byte-compatible v0.1.0 output.
_RENDERER_ENTRY_KINDS = frozenset({"class", "interface", "package", "function"})


def build_module_stats(symbols: List[Symbol]) -> Dict[str, dict]:
    """Module-level statistics for the renderer.

    Each module dict carries:
      - `class_count` (legacy, v0.1.0 byte-compat): number of `kind=="class"` symbols
      - `classes`     (legacy): names of those class symbols
      - `symbol_count` (new): total entry symbols across the widened kind set
      - `entries`     (new): names of those entry symbols (in stable order)
      - `file_count`  (legacy): number of distinct files in this module
    """
    modules: Dict[str, dict] = {}
    for sym in symbols:
        module = _module_from_file(sym.file)
        if module not in modules:
            modules[module] = {
                "class_count": 0,
                "classes": [],
                "symbol_count": 0,
                "entries": [],
                "files": set(),
            }
        info = modules[module]
        info["files"].add(sym.file)
        if sym.kind == "class":
            info["class_count"] += 1
            info["classes"].append(sym.name)
        if sym.kind in _RENDERER_ENTRY_KINDS:
            info["symbol_count"] += 1
            info["entries"].append(sym.name)
    for m in modules.values():
        m["file_count"] = len(m["files"])
        del m["files"]
    return modules


def categorize_module(module: str, categories: dict) -> str:
    for cat_name, cat_config in categories.items():
        if cat_name == "Other":
            continue
        for pattern in cat_config.get("patterns", []):
            if pattern.lower() in module.lower():
                return cat_name
    return "Other"


# ----- L1 -----------------------------------------------------------------------

def render_l1(
    symbols: List[Symbol],
    ranker: PageRankRanker,
    modules: Dict[str, dict],
    config: dict,
    *,
    project_name: str,
    git_commit: str,
    today_yyyy_mm_dd: str,
) -> str:
    """Generate L1 module skeleton, byte-equivalent to v0.1.0."""
    max_tokens = config["tokens"]["l1_skeleton"]
    encoding = config["tokens"].get("encoding", "cl100k_base")

    module_ranks = ranker.get_module_ranks()
    sorted_modules = sorted(
        modules.items(),
        key=lambda x: module_ranks.get(x[0], 0),
        reverse=True,
    )

    lines = [
        f"# {project_name} Repo Map (L1)",
        f"> Generated: {today_yyyy_mm_dd} | Commit: {git_commit[:8] if git_commit else 'unknown'}",
        "",
        f"## Module Overview ({len(modules)} modules)",
        "",
    ]

    categories: Dict[str, list] = defaultdict(list)
    priority_modules = config.get("importance_boost", {}).get("priority_modules", []) or []
    cat_cfg = config.get("categories", {}) or {}

    for module, info in sorted_modules:
        rank = module_ranks.get(module, 0)
        cat = categorize_module(module, cat_cfg)
        categories[cat].append((module, info, rank))

    for cat_name in cat_cfg.keys():
        mods = categories.get(cat_name, [])
        if not mods:
            continue
        lines.append(f"### {cat_name}")
        for module, info, rank in sorted(mods, key=lambda x: x[2], reverse=True)[:10]:
            active = " [Active]" if module in priority_modules else ""
            lines.append(f"- {module}/ ({info['class_count']} classes){active}")
        lines.append("")

    lines.append("### Core Entry Classes")
    lines.append("| Module | Entry Class | Key Methods |")
    lines.append("|--------|-------------|-------------|")

    # Look up methods by (container, parent) match — not by bare label — to
    # avoid merging methods across same-named classes in different namespaces.
    syms_by_id = {s.id: s for s in symbols}
    ranked_symbols = ranker.get_ranked_symbols(limit=20)
    for sid, rank, info in ranked_symbols:
        label = info.get("label", sid)
        cls_sym = syms_by_id.get(sid)
        if cls_sym is not None:
            methods = [
                s.name for s in symbols
                if s.kind == "method"
                and s.parent == cls_sym.name
                and s.container == cls_sym.container
            ]
        else:
            # Implicit graph node (no real Symbol behind it) — best-effort
            # label-based match as v0.1.0 did.
            methods = [s.name for s in symbols if s.parent == label and s.kind == "method"]
        method_str = ", ".join(methods[:3]) if methods else "-"
        file_str = info.get("file", "")
        module = file_str.split("/")[0] if file_str else "-"
        lines.append(f"| {module} | {label} | {method_str} |")

    output = "\n".join(lines)
    while count_tokens(output, encoding) > max_tokens and len(lines) > 10:
        lines.pop(-2)
        output = "\n".join(lines)
    return output


# ----- L2 -----------------------------------------------------------------------

def render_l2(
    symbols: List[Symbol],
    ranker: PageRankRanker,
    config: dict,
    *,
    project_name: str,
) -> str:
    """Generate L2 class signatures, byte-equivalent to v0.1.0."""
    max_tokens = config["tokens"]["l2_signatures"]
    encoding = config["tokens"].get("encoding", "cl100k_base")

    lines = [f"# {project_name} Repo Map (L2)", ""]

    ranked = ranker.get_ranked_symbols()
    # Bucket by module, storing (sid, rank, info) so we can look up the
    # actual Symbol by id later (avoids same-name-cross-namespace collision).
    module_classes: Dict[str, list] = defaultdict(list)
    for sid, rank, info in ranked:
        file = info.get("file", "")
        module = file.split("/")[0] if file else "Unknown"
        module_classes[module].append((sid, rank, info))

    module_ranks = {m: sum(r for _, r, _ in cls) for m, cls in module_classes.items()}
    sorted_modules = sorted(module_ranks.keys(), key=lambda x: module_ranks[x], reverse=True)

    syms_by_id = {s.id: s for s in symbols}
    for module in sorted_modules[:15]:
        classes = module_classes[module]
        total_rank = module_ranks[module]
        lines.append(f"## {module} ({len(classes)} classes, rank: {total_rank:.2f})")
        lines.append("")
        for sid, rank, info in sorted(classes, key=lambda x: x[1], reverse=True)[:5]:
            # Look up class symbol by id so same-named classes in different
            # namespaces stay distinct.
            sym = syms_by_id.get(sid)
            if sym is None or sym.kind != "class":
                continue
            lines.append(f"### {sym.signature}")
            methods = [
                s for s in symbols
                if s.kind == "method"
                and s.parent == sym.name
                and s.container == sym.container
            ]
            for m in methods[:5]:
                lines.append(f"- {m.signature}")
            lines.append("")

    output = "\n".join(lines)
    while count_tokens(output, encoding) > max_tokens and lines:
        while lines and not lines[-1].startswith("##"):
            lines.pop()
        if lines:
            lines.pop()
        output = "\n".join(lines)
    return output


# ----- L3 -----------------------------------------------------------------------

def _label_from_id(sid: str) -> str:
    """Best-effort display name for an id that isn't in symbol_info (implicit node).

    Strips `lang:` prefix and method param parens, then returns the last
    segment after `.` or `#`.
    """
    body = sid.split(":", 1)[1] if ":" in sid else sid
    if "(" in body:
        body = body.split("(", 1)[0]
    if "#" in body:
        return body.rsplit("#", 1)[-1]
    if "." in body:
        return body.rsplit(".", 1)[-1]
    return body


def _label_for(sid: str, ranker: PageRankRanker) -> str:
    """Reverse-lookup symbol display label by id, with id-stripping fallback."""
    info = ranker.symbol_info.get(sid)
    if info:
        return info.get("label", _label_from_id(sid))
    return _label_from_id(sid)


def render_l3(
    references: List[Reference],
    ranker: PageRankRanker,
    unresolved: Optional[List[Reference]] = None,
    *,
    config: dict,
    project_name: str,
) -> str:
    """Generate L3 reference graph, byte-equivalent to v0.1.0 for resolved refs.

    Adds an `## External References` block when `unresolved` is non-empty.
    """
    max_tokens = config["tokens"]["l3_relations"]
    encoding = config["tokens"].get("encoding", "cl100k_base")

    lines = [f"# {project_name} Repo Map (L3)", "", "## Reference Graph", ""]

    # Index by Symbol.id (NOT label) so two same-named classes in different
    # namespaces don't merge their reference lists. Labels are derived for
    # display from each id when we render.
    incoming: Dict[str, list] = defaultdict(list)  # to_id -> [(from_id, kind)]
    outgoing: Dict[str, list] = defaultdict(list)  # from_id -> [(to_id, kind)]
    for ref in references:
        if not ref.resolved or not ref.to_id:
            continue
        incoming[ref.to_id].append((ref.from_id, ref.kind))
        outgoing[ref.from_id].append((ref.to_id, ref.kind))

    ranked = ranker.get_ranked_symbols(limit=30)
    for sid, rank, info in ranked:
        in_refs = incoming.get(sid, [])
        out_refs = outgoing.get(sid, [])
        if not in_refs and not out_refs:
            continue
        label = _label_for(sid, ranker)
        lines.append(f"{label} (refs: {len(in_refs)}, rank: {rank:.2f})")
        for target_sid, ref_kind in out_refs[:5]:
            target = _label_for(target_sid, ranker)
            lines.append(f"  -> {target} ({ref_kind})")
        for source_sid, ref_kind in in_refs[:3]:
            source = _label_for(source_sid, ranker)
            lines.append(f"  <- {source} ({ref_kind})")
        lines.append("")

    if unresolved:
        lines.append("## External References")
        lines.append("")
        for ref in unresolved:
            from_label = _label_for(ref.from_id, ranker)
            target = ref.to_external or "<unknown>"
            extra = ""
            cands = ref.lang_meta.get("candidates") if ref.lang_meta else None
            if cands:
                extra = f"  (ambiguous: {len(cands)} candidates)"
            lines.append(f"- {from_label} -> {target} ({ref.kind}){extra}")
        lines.append("")

    output = "\n".join(lines)
    while count_tokens(output, encoding) > max_tokens and len(lines) > 5:
        lines.pop()
        output = "\n".join(lines)
    return output


# ----- Meta ---------------------------------------------------------------------

def render_meta(
    symbols: List[Symbol],
    references: List[Reference],
    ranker: PageRankRanker,
    modules: Dict[str, dict],
    *,
    project_name: str,
    source_path: str,
    git_commit: str,
    git_branch: str,
    generated_at_iso: str,
) -> dict:
    """JSON-serializable meta. Schema preserved from v0.1.0 + per-lang stats hook."""
    file_count = len({s.file for s in symbols})
    class_count = len([s for s in symbols if s.kind == "class"])
    method_count = len([s for s in symbols if s.kind == "method"])

    return {
        "project_name": project_name,
        "git_commit": git_commit,
        "git_branch": git_branch,
        "generated_at": generated_at_iso,
        "source_path": source_path,
        "stats": {
            "file_count": file_count,
            "class_count": class_count,
            "method_count": method_count,
            "reference_count": len(references),
            "module_count": len(modules),
        },
        "top_modules": [
            {"name": m, "classes": info["class_count"]}
            for m, info in sorted(
                modules.items(), key=lambda x: x[1]["class_count"], reverse=True
            )[:10]
        ],
        "ranker_stats": ranker.get_stats(),
    }


__all__ = [
    "count_tokens",
    "build_module_stats",
    "categorize_module",
    "render_l1",
    "render_l2",
    "render_l3",
    "render_meta",
]
