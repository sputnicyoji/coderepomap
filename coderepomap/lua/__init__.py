"""coderepomap.lua: Lua language plug-in.

Importing this subpackage registers `LuaParser` with
`coderepomap.core.registry` so `get_parser('lua')` works. Triggered
automatically when the user config declares `lang: lua` or
`langs: [..., lua, ...]`.
"""

from ..core.registry import register
from .parser import LuaParser

register(LuaParser)

__all__ = ["LuaParser"]
