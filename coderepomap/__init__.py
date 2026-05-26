"""
coderepomap: Generate layered multi-language code maps for AI assistants.

Three levels of detail:
- L1: Module skeleton (~1k tokens)
- L2: Class signatures (~2k tokens)
- L3: Reference relations (~3k tokens)

Languages: C# (built-in), Lua (via extras). See `pip install coderepomap[csharp,lua]`.

Public API points at the v0.2.0 layered modules. CSharpParser here is the
LanguageParser-compliant subclass (Symbol.id-aware). For the legacy v0.1.0
parser shape, import `coderepomap.csharp.parser.LegacyCSharpParser` explicitly.
"""

__version__ = "0.2.0"
__author__ = "Yoji"

from .core.generator import RepoMapGenerator
from .core.ranker import PageRankRanker

# Importing `coderepomap.csharp` triggers parser registration. We re-export
# the NEW CSharpParser (the LanguageParser subclass) from this top-level
# package, not the legacy class — the legacy class is still reachable as
# `coderepomap.csharp.parser.LegacyCSharpParser` for back-compat.
from . import csharp as _csharp_pkg  # noqa: F401 — side-effect: register
from .csharp.parser import CSharpParser

__all__ = ["RepoMapGenerator", "CSharpParser", "PageRankRanker", "__version__"]
