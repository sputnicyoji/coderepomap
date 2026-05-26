"""
Legacy compatibility shim for v0.1.0 `coderepomap.parser` import path.

The real C# parser now lives in `coderepomap.csharp.parser`. This module
re-exports the legacy class+dataclass names so the v0.1.0 generator
(`coderepomap.generator`) and the baseline tests continue to work
unmodified until Phase 4 swaps the generator over to the new contract.

This file disappears at Phase 4 cleanup.
"""

from .csharp.parser import (
    LegacyCSharpParser as CSharpParser,
    LegacyReference as Reference,
    LegacySymbol as Symbol,
)

__all__ = ["CSharpParser", "Reference", "Symbol"]
