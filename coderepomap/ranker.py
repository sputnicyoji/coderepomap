#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PageRank-based Code Ranker

Ranks code symbols by importance using the PageRank algorithm.
More frequently referenced code gets higher ranks.
"""

import sys
from typing import Dict, List, Set, Tuple, Optional
from collections import defaultdict

# Handle encoding for Windows
if sys.stdout:
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass
if sys.stderr:
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False


class PageRankRanker:
    """Ranks code symbols using PageRank algorithm"""

    def __init__(self, alpha: float = 0.85, max_iter: int = 100, tol: float = 1.0e-6):
        """
        Initialize the ranker.

        Args:
            alpha: Damping factor (probability of following a link)
            max_iter: Maximum iterations for PageRank
            tol: Convergence tolerance
        """
        self.alpha = alpha
        self.max_iter = max_iter
        self.tol = tol

        self.graph = nx.DiGraph() if HAS_NETWORKX else None
        self.file_to_symbols: Dict[str, Set[str]] = defaultdict(set)
        self.symbol_to_file: Dict[str, str] = {}
        self.symbol_info: Dict[str, dict] = {}

    def add_symbol(self, name: str, file: str, kind: str, rank_boost: float = 1.0):
        """
        Add a symbol to the graph.

        Args:
            name: Symbol name (usually class name)
            file: File path containing the symbol
            kind: Symbol kind (class, method, etc.)
            rank_boost: Multiplier for this symbol's rank
        """
        self.file_to_symbols[file].add(name)
        self.symbol_to_file[name] = file
        self.symbol_info[name] = {
            'file': file,
            'kind': kind,
            'boost': rank_boost
        }

        if self.graph is not None:
            self.graph.add_node(name, file=file, kind=kind, boost=rank_boost)

    def add_reference(self, from_symbol: str, to_symbol: str, ref_type: str = 'uses'):
        """
        Add a reference (edge) between symbols.

        Args:
            from_symbol: Source symbol
            to_symbol: Target symbol (being referenced)
            ref_type: Type of reference
        """
        if self.graph is not None:
            # Edge goes from referencer to referenced
            # PageRank will give higher rank to symbols with more incoming edges
            self.graph.add_edge(from_symbol, to_symbol, ref_type=ref_type)

    def compute_ranks(self) -> Dict[str, float]:
        """
        Compute PageRank for all symbols.

        Returns:
            Dictionary mapping symbol names to their rank scores
        """
        if not HAS_NETWORKX or self.graph is None or len(self.graph) == 0:
            return self._compute_simple_ranks()

        try:
            # Compute PageRank
            ranks = nx.pagerank(
                self.graph,
                alpha=self.alpha,
                max_iter=self.max_iter,
                tol=self.tol
            )

            # Apply boost multipliers
            for symbol, rank in ranks.items():
                info = self.symbol_info.get(symbol, {})
                boost = info.get('boost', 1.0)
                ranks[symbol] = rank * boost

            return ranks

        except Exception:
            return self._compute_simple_ranks()

    def _compute_simple_ranks(self) -> Dict[str, float]:
        """Fallback ranking based on reference count"""
        ranks = {}
        ref_counts: Dict[str, int] = defaultdict(int)

        # Count incoming references for each symbol
        if self.graph is not None:
            for node in self.graph.nodes():
                ref_counts[node] = self.graph.in_degree(node)

        # Normalize to 0-1 range
        max_refs = max(ref_counts.values()) if ref_counts else 1

        for symbol in self.symbol_info:
            base_rank = ref_counts.get(symbol, 0) / max_refs if max_refs > 0 else 0
            boost = self.symbol_info[symbol].get('boost', 1.0)
            ranks[symbol] = base_rank * boost

        return ranks

    def get_ranked_symbols(self, limit: Optional[int] = None) -> List[Tuple[str, float, dict]]:
        """
        Get symbols sorted by rank.

        Args:
            limit: Maximum number of symbols to return

        Returns:
            List of (symbol_name, rank, info) tuples, sorted by rank descending
        """
        ranks = self.compute_ranks()

        ranked = [
            (name, rank, self.symbol_info.get(name, {}))
            for name, rank in ranks.items()
        ]

        # Sort by rank descending
        ranked.sort(key=lambda x: x[1], reverse=True)

        if limit:
            ranked = ranked[:limit]

        return ranked

    def get_file_ranks(self) -> Dict[str, float]:
        """
        Compute aggregate ranks for files.

        Returns:
            Dictionary mapping file paths to their aggregate rank
        """
        symbol_ranks = self.compute_ranks()
        file_ranks: Dict[str, float] = defaultdict(float)

        for symbol, rank in symbol_ranks.items():
            file = self.symbol_to_file.get(symbol)
            if file:
                file_ranks[file] += rank

        return dict(file_ranks)

    def get_module_ranks(self) -> Dict[str, float]:
        """
        Compute aggregate ranks for modules (top-level directories).

        Returns:
            Dictionary mapping module names to their aggregate rank
        """
        file_ranks = self.get_file_ranks()
        module_ranks: Dict[str, float] = defaultdict(float)

        for file, rank in file_ranks.items():
            # Extract module name (first directory component)
            parts = file.replace('\\', '/').split('/')
            if parts:
                module = parts[0]
                module_ranks[module] += rank

        return dict(module_ranks)

    def get_stats(self) -> dict:
        """Get statistics about the graph"""
        if self.graph is None:
            return {
                'nodes': len(self.symbol_info),
                'edges': 0,
                'has_networkx': False
            }

        return {
            'nodes': self.graph.number_of_nodes(),
            'edges': self.graph.number_of_edges(),
            'has_networkx': True,
            'is_connected': nx.is_weakly_connected(self.graph) if self.graph.number_of_nodes() > 0 else False
        }


class ModuleRanker:
    """
    Higher-level ranker that groups symbols by module.
    Useful for generating L1 skeleton with module priorities.
    """

    def __init__(self):
        self.modules: Dict[str, dict] = {}
        self.ranker = PageRankRanker()

    def add_module(self, name: str, class_count: int, priority_boost: float = 1.0):
        """Add a module with its class count"""
        self.modules[name] = {
            'class_count': class_count,
            'boost': priority_boost,
            'classes': []
        }
        self.ranker.add_symbol(name, name, 'module', priority_boost)

    def add_module_class(self, module: str, class_name: str):
        """Add a class to a module"""
        if module in self.modules:
            self.modules[module]['classes'].append(class_name)

    def add_module_dependency(self, from_module: str, to_module: str):
        """Add a dependency between modules"""
        self.ranker.add_reference(from_module, to_module, 'depends')

    def get_ranked_modules(self) -> List[Tuple[str, float, dict]]:
        """Get modules sorted by importance"""
        ranks = self.ranker.compute_ranks()

        ranked = []
        for name, info in self.modules.items():
            rank = ranks.get(name, 0)
            ranked.append((name, rank, info))

        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked


if __name__ == "__main__":
    # Test the ranker with generic example
    ranker = PageRankRanker()

    # Add some test symbols
    ranker.add_symbol("GameManager", "Managers/GameManager.cs", "class", 2.0)
    ranker.add_symbol("PlayerController", "Player/PlayerController.cs", "class")
    ranker.add_symbol("PlayerData", "Player/PlayerData.cs", "class")
    ranker.add_symbol("EnemyAI", "Enemy/EnemyAI.cs", "class")
    ranker.add_symbol("UIManager", "UI/UIManager.cs", "class")

    # Add references
    ranker.add_reference("GameManager", "PlayerController", "creates")
    ranker.add_reference("GameManager", "EnemyAI", "creates")
    ranker.add_reference("UIManager", "GameManager", "calls")
    ranker.add_reference("PlayerController", "PlayerData", "uses")
    ranker.add_reference("EnemyAI", "PlayerController", "targets")

    print("Stats:", ranker.get_stats())
    print("\nRanked symbols:")
    for name, rank, info in ranker.get_ranked_symbols():
        print(f"  {name}: {rank:.4f} ({info.get('kind', 'unknown')})")
