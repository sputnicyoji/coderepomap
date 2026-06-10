#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""coderepomap.typescript: TypeScript language plug-in.

Importing this subpackage registers `TypeScriptParser` with
`coderepomap.core.registry` so `get_parser("typescript")` works. Triggered
automatically when the user config declares `lang: typescript` or
`langs: [..., typescript, ...]`.
"""

from ..core.registry import register
from .parser import TypeScriptParser

register(TypeScriptParser)

__all__ = ["TypeScriptParser"]
