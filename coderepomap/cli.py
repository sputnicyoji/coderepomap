"""
Thin shim: re-exports main from coderepomap.core.cli.

Kept for backwards compatibility with any external callers that import
`from coderepomap.cli import main`. The canonical location is
`coderepomap.core.cli`.
"""

from .core.cli import main  # noqa: F401

__all__ = ["main"]
