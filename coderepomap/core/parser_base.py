"""
Language-agnostic parser contract.

Defines `Symbol`, `Reference`, and the `LanguageParser` ABC. Per-language parsers
(coderepomap.csharp, coderepomap.lua, ...) implement `LanguageParser` and register
themselves via `coderepomap.core.registry.register`.

Identity model:
- `Symbol.id` is the stable, unique graph-node key. Prefix `<lang>:` + body.
- `Symbol.fqn` is the human-readable language-internal full name (C# namespace
  path or Lua module path).
- `Symbol.name` is the bare display name.
- `Reference.from_id` / `to_id` use `Symbol.id`. Unresolved references keep
  `to_id=""` and put the raw external chain in `to_external`.
- Language-private fields (C# `base_class` / `interfaces` / `modifiers`,
  Lua alias table snapshots, etc.) go into `lang_meta`.

This module has no language-specific knowledge. Adding a new language requires
a new subpackage with a `LanguageParser` subclass — no edits here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple


@dataclass
class Symbol:
    """A code symbol (class, function, module, method, ...) in any language.

    `id` is the unique graph-node key. Two symbols with the same `name` in
    different namespaces / modules / languages MUST have different `id`s.
    """

    id: str
    name: str
    fqn: str
    kind: str
    file: str
    line: int
    signature: str = ""
    container: str = ""
    parent: str = ""
    lang: str = ""
    lang_meta: dict = field(default_factory=dict)


@dataclass
class Reference:
    """A directed edge from one symbol to another.

    Resolved references have non-empty `to_id`. Unresolved references (e.g.
    Lua call into an external C# symbol that the resolver can't pin down)
    keep `to_id=""` and put the original chain in `to_external` for L3
    rendering. `from_id` is always required so the source side stays
    addressable in the graph.
    """

    from_id: str
    to_id: str
    file: str
    line: int
    kind: str
    lang: str = ""
    resolved: bool = True
    to_external: str = ""
    lang_meta: dict = field(default_factory=dict)


class LanguageParser(ABC):
    """Per-language parser plug-in. Subclasses MUST set the four class attrs.

    Conventions:
    - `lang` is the lowercase short code used everywhere (`csharp`, `lua`).
    - `file_extensions` MUST include the leading dot.
    - `default_excludes` are glob patterns merged with user `exclude_patterns`.
    - `default_boost_patterns` / `default_categories` seed the ranker /
      renderer when the user config doesn't override them.
    """

    lang: str = ""
    file_extensions: List[str] = []
    default_excludes: List[str] = []
    default_boost_patterns: List[dict] = []
    default_categories: dict = {}

    @abstractmethod
    def parse_file(
        self, path: Path, base: Path
    ) -> Tuple[List[Symbol], List[Reference]]:
        """Parse a single source file relative to a language-specific base root.

        `base` is the parser's own source-root (set per-language in
        multi-language projects), NOT a global project root.
        """

    def configure(self, config: dict) -> None:
        """Optional hook for parsers to consume language-specific config.

        Called by the generator once after instantiation, before any
        `parse_file`. Default no-op; languages that need config (e.g. Lua's
        `crosslang.lua_csharp_call_patterns`) override.
        """
        return None


__all__ = ["Symbol", "Reference", "LanguageParser"]
