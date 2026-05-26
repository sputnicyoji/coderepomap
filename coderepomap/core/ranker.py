#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PageRank-based symbol ranker, keyed by Symbol.id.

Differences vs. the v0.1.0 `coderepomap.ranker.PageRankRanker`:

- Graph nodes are `Symbol.id` (unique), not bare `Symbol.name`. Two `Player`
  classes in different namespaces / languages cannot collide.
- `symbol_info` carries `label` (display name), `fqn`, `lang`, plus the
  legacy `file` / `kind` / `boost`. Renderers use `label`+`fqn` for output;
  the ranker uses `id` everywhere internally.
- Edges only accept resolved references (`add_reference(from_id, to_id, kind)`
  with both ids non-empty). Unresolved references must NOT enter the graph;
  they go to the L3 "External References" section via the renderer instead.
- `get_stats()` reports both `nodes` (id-distinct count, the actual graph
  size) and `display_symbols` (distinct labels, for human cross-check).

The legacy `coderepomap.ranker` is kept until Phase 4 cleanup so the legacy
generator continues to work unmodified.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

if sys.stdout:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False


class PageRankRanker:
    """Rank symbols by importance using PageRank, with Symbol.id as node key."""

    def __init__(self, alpha: float = 0.85, max_iter: int = 100, tol: float = 1.0e-6):
        self.alpha = alpha
        self.max_iter = max_iter
        self.tol = tol

        self.graph = nx.DiGraph() if HAS_NETWORKX else None
        # All keyed by Symbol.id
        self.symbol_info: Dict[str, dict] = {}
        # file -> set of ids
        self.file_to_symbols: Dict[str, Set[str]] = defaultdict(set)
        # id -> file (1:1)
        self.symbol_to_file: Dict[str, str] = {}

    def add_symbol(
        self,
        symbol_id: str,
        *,
        file: str,
        kind: str,
        boost: float = 1.0,
        label: str = "",
        fqn: str = "",
        lang: str = "",
    ) -> None:
        """Register a symbol under its stable id.

        `label` defaults to the id if empty so older callers transitioning to
        ids don't accidentally lose display text.
        """
        if not symbol_id:
            raise ValueError("symbol_id must be non-empty")
        if not label:
            label = symbol_id
        self.symbol_info[symbol_id] = {
            "label": label,
            "fqn": fqn,
            "lang": lang,
            "file": file,
            "kind": kind,
            "boost": boost,
        }
        self.file_to_symbols[file].add(symbol_id)
        self.symbol_to_file[symbol_id] = file
        if self.graph is not None:
            self.graph.add_node(
                symbol_id,
                file=file,
                kind=kind,
                boost=boost,
                label=label,
                fqn=fqn,
                lang=lang,
            )

    def add_reference(
        self,
        from_id: str,
        to_id: str,
        kind: str = "uses",
    ) -> None:
        """Add a resolved edge. Both endpoints must be non-empty.

        Unresolved references (to_id == "") are silently ignored — the caller
        is expected to route them through the renderer's external section
        instead. We raise on `from_id == ""` because that means the source
        symbol couldn't even be placed in the graph, which is a parser bug.
        """
        if not from_id:
            raise ValueError("from_id must be non-empty (parser bug if you hit this)")
        if not to_id:
            return  # silently drop unresolved
        if self.graph is None:
            return
        self.graph.add_edge(from_id, to_id, kind=kind)

    # --- ranking ---

    def compute_ranks(self) -> Dict[str, float]:
        """PageRank by id. Returns id -> rank."""
        if not HAS_NETWORKX or self.graph is None or len(self.graph) == 0:
            return self._compute_simple_ranks()
        try:
            ranks = nx.pagerank(self.graph, alpha=self.alpha, max_iter=self.max_iter, tol=self.tol)
            for sid in list(ranks.keys()):
                boost = self.symbol_info.get(sid, {}).get("boost", 1.0)
                ranks[sid] = ranks[sid] * boost
            return ranks
        except Exception:
            return self._compute_simple_ranks()

    def _compute_simple_ranks(self) -> Dict[str, float]:
        """Fallback: incoming-edge count, normalized."""
        ref_counts: Dict[str, int] = defaultdict(int)
        if self.graph is not None:
            for n in self.graph.nodes():
                ref_counts[n] = self.graph.in_degree(n)
        max_refs = max(ref_counts.values()) if ref_counts else 1
        out: Dict[str, float] = {}
        for sid, info in self.symbol_info.items():
            base = ref_counts.get(sid, 0) / max_refs if max_refs > 0 else 0
            out[sid] = base * info.get("boost", 1.0)
        return out

    def get_ranked_symbols(self, limit: Optional[int] = None) -> List[Tuple[str, float, dict]]:
        """Return [(id, rank, info)] sorted by rank desc.

        `info["label"]` is what renderers should display.
        """
        ranks = self.compute_ranks()
        ranked = [(sid, r, self.symbol_info.get(sid, {})) for sid, r in ranks.items()]
        ranked.sort(key=lambda x: x[1], reverse=True)
        # `is not None` so limit=0 returns an empty list (not all symbols).
        if limit is not None:
            ranked = ranked[:limit]
        return ranked

    def get_file_ranks(self) -> Dict[str, float]:
        """File -> aggregate rank across its symbols."""
        sym_ranks = self.compute_ranks()
        file_ranks: Dict[str, float] = defaultdict(float)
        for sid, r in sym_ranks.items():
            f = self.symbol_to_file.get(sid)
            if f:
                file_ranks[f] += r
        return dict(file_ranks)

    def get_module_ranks(self) -> Dict[str, float]:
        """Module (top-level dir of the file path) -> aggregate rank."""
        file_ranks = self.get_file_ranks()
        mod: Dict[str, float] = defaultdict(float)
        for f, r in file_ranks.items():
            parts = f.replace("\\", "/").split("/")
            if parts:
                mod[parts[0]] += r
        return dict(mod)

    def get_stats(self) -> dict:
        """Graph stats. Reports both id-distinct nodes and display-distinct labels."""
        display_count = len({info.get("label", "") for info in self.symbol_info.values()})
        if self.graph is None:
            return {
                "nodes": len(self.symbol_info),
                "edges": 0,
                "has_networkx": False,
                "display_symbols": display_count,
            }
        return {
            "nodes": self.graph.number_of_nodes(),
            "edges": self.graph.number_of_edges(),
            "has_networkx": True,
            "is_connected": (
                nx.is_weakly_connected(self.graph) if self.graph.number_of_nodes() > 0 else False
            ),
            "display_symbols": display_count,
        }


__all__ = ["PageRankRanker", "HAS_NETWORKX"]
