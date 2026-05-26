"""coderepomap.go: Go language plug-in.

Importing this subpackage registers `GoParser` with `coderepomap.core.registry`
so `get_parser("go")` works. Triggered automatically when the user config
declares `lang: go` or `langs: [..., go, ...]`.
"""

from ..core.registry import register
from .parser import GoParser

register(GoParser)

__all__ = ["GoParser"]
