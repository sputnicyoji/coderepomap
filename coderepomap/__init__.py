"""
csharp-repomap: Generate layered code maps for C# projects

This tool creates structured code maps optimized for AI assistants,
providing three levels of detail:
- L1: Module skeleton (~1k tokens)
- L2: Class signatures (~2k tokens)
- L3: Reference relations (~3k tokens)
"""

__version__ = "0.1.0"
__author__ = "Yoji"

from .generator import RepoMapGenerator
from .parser import CSharpParser
from .ranker import PageRankRanker

__all__ = ["RepoMapGenerator", "CSharpParser", "PageRankRanker", "__version__"]
