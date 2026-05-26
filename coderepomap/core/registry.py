"""
Language parser registry.

Lazy import + decorator-based registration. CLI / generator call
`get_parser(lang)` which triggers `importlib.import_module('coderepomap.<lang>')`
to run that subpackage's `__init__.py`, which is expected to call
`register(MyParser)` as a side-effect.

If the language subpackage exists but its third-party dependency
(`tree-sitter-c-sharp` / `tree-sitter-lua` / ...) is missing, the parser is
still importable (the language `__init__.py` handles ImportError); calling
`parse_file` falls back to the regex path.

If the language subpackage doesn't exist at all (user asked for an unknown
lang), `get_parser` raises a clear install hint.
"""

from __future__ import annotations

import importlib
from typing import Dict, Type

from .parser_base import LanguageParser


PARSERS: Dict[str, Type[LanguageParser]] = {}


def register(cls: Type[LanguageParser]) -> Type[LanguageParser]:
    """Decorator + plain function: register a parser class under its `lang`.

    Idempotent — re-registering the same class is a no-op. Different classes
    sharing a lang code raises.
    """
    lang = getattr(cls, "lang", "")
    if not lang:
        raise ValueError(f"{cls!r} has no `lang` attribute")
    existing = PARSERS.get(lang)
    if existing is None:
        PARSERS[lang] = cls
    elif existing is not cls:
        raise ValueError(
            f"Lang '{lang}' already registered to {existing!r}, "
            f"refusing to overwrite with {cls!r}"
        )
    return cls


def import_parser_module(lang: str) -> None:
    """Import `coderepomap.<lang>` so its registration side-effect runs.

    Raises ModuleNotFoundError if the subpackage is missing.
    """
    importlib.import_module(f"coderepomap.{lang}")


def get_parser(lang: str) -> LanguageParser:
    """Return a freshly-instantiated parser for `lang`.

    Auto-imports the subpackage on first access. Raises a hint-shaped
    error if the lang isn't registered after import (most likely cause:
    the user installed `coderepomap` without the `[<lang>]` extras).

    Catches both `ModuleNotFoundError` (the subpackage itself is absent)
    AND broader `ImportError` (the subpackage loads but a transitive
    C-extension fails to import, e.g. tree-sitter wheel ABI mismatch).
    """
    if lang not in PARSERS:
        try:
            import_parser_module(lang)
        except ModuleNotFoundError as e:
            raise ValueError(
                f"No parser plug-in for lang '{lang}'. "
                f"Try: pip install coderepomap[{lang}]  (missing: {e.name})"
            ) from e
        except ImportError as e:
            raise ValueError(
                f"Parser plug-in for lang '{lang}' failed to import — likely a "
                f"native-extension ABI mismatch. Try: "
                f"pip install --upgrade coderepomap[{lang}]  "
                f"(error: {e})"
            ) from e
    if lang not in PARSERS:
        raise ValueError(
            f"Parser for '{lang}' loaded its module but did not register. "
            f"This is a plug-in bug."
        )
    return PARSERS[lang]()


def registered_langs() -> list[str]:
    """Snapshot of currently-registered lang codes (for CLI status / debug)."""
    return sorted(PARSERS.keys())


__all__ = ["PARSERS", "register", "import_parser_module", "get_parser", "registered_langs"]
