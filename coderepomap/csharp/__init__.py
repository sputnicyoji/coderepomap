"""coderepomap.csharp: C# language plug-in.

Importing this subpackage registers `CSharpParser` with `coderepomap.core.registry`
so `get_parser("csharp")` works. Triggered automatically when the user config
declares `lang: csharp` or `langs: [csharp, ...]`.
"""

from ..core.registry import register
from .parser import CSharpParser

register(CSharpParser)

__all__ = ["CSharpParser"]
