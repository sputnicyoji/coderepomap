"""
Build a `PageRankRanker` graph from new-contract Symbols / References.

Single entry point: `build_graph(symbols, references, ranker, config)`.
- Adds every Symbol to the ranker, computing its boost from config patterns.
- Adds only RESOLVED References as edges. Unresolved References are returned
  alongside the populated ranker so the renderer can dump them in the
  L3 "External References" section.

This module is language-agnostic; it doesn't import any language plug-in.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from .parser_base import Reference, Symbol
from .ranker import PageRankRanker


def _boost_for_symbol(name: str, boost_patterns: Iterable[dict]) -> float:
    """Match a symbol name against importance_boost patterns.

    Behavior matches v0.1.0 `_calculate_boost`:
    - Multiple matches take the MAX boost (not product) so a class matching
      two patterns doesn't get an exaggerated combined score.
    - `prefix` requires the character AFTER the prefix to be uppercase, so
      `prefix: 'S'` matches `SManager` (next char `M` uppercase) but NOT
      `Setup` (next char `e` lowercase). This avoids over-matching common
      English words.
    """
    best = 1.0
    for pat in boost_patterns:
        boost = float(pat.get("boost", 1.0))
        matched = False
        if "prefix" in pat:
            prefix = pat["prefix"]
            if (
                name.startswith(prefix)
                and len(name) > len(prefix)
                and name[len(prefix)].isupper()
            ):
                matched = True
        if "suffix" in pat and name.endswith(pat["suffix"]):
            matched = True
        if "contains" in pat and pat["contains"] in name:
            matched = True
        if matched and boost > best:
            best = boost
    return best


DEFAULT_NODE_KINDS = frozenset({"class"})  # matches v0.1.0 behavior


def build_graph(
    symbols: List[Symbol],
    references: List[Reference],
    ranker: PageRankRanker,
    boost_patterns: Iterable[dict] = (),
    *,
    node_kinds: Optional[Iterable[str]] = None,
) -> Tuple[PageRankRanker, List[Reference]]:
    """Populate `ranker` with symbols + resolved edges; return ranker + unresolved.

    `node_kinds` controls which Symbol.kind values become graph nodes. Default
    matches v0.1.0 (only `class`). Pass an explicit set to widen, or pass
    `None` (the sentinel) to keep the default, or pass an empty set to add
    every kind. Methods / properties are never graph nodes; they still affect
    file-level rank because their host class is a node.
    """
    boost_patterns_list = list(boost_patterns)
    if node_kinds is None:
        accept_kinds = DEFAULT_NODE_KINDS
    else:
        accept_kinds = frozenset(node_kinds)

    for sym in symbols:
        if accept_kinds and sym.kind not in accept_kinds:
            continue
        boost = _boost_for_symbol(sym.name, boost_patterns_list)
        ranker.add_symbol(
            sym.id,
            file=sym.file,
            kind=sym.kind,
            boost=boost,
            label=sym.name,
            fqn=sym.fqn,
            lang=sym.lang,
        )

    unresolved: List[Reference] = []
    for ref in references:
        if not ref.resolved or not ref.to_id:
            unresolved.append(ref)
            continue
        # add_reference silently drops if to_id is empty; we already filtered
        ranker.add_reference(ref.from_id, ref.to_id, ref.kind)

    return ranker, unresolved


__all__ = ["build_graph"]
