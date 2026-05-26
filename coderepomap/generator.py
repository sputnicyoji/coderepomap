"""
Legacy compatibility shim for v0.1.0 `coderepomap.generator` import path.

The real generator now lives in `coderepomap.core.generator`. This module
re-exports `RepoMapGenerator` so callers using `from coderepomap.generator
import RepoMapGenerator` continue to work.

Removed at Phase 4 cleanup (this is the cleanup commit) — kept ONLY because
the legacy CLI (`coderepomap.cli`) imports from here. Phase 6 will switch
the CLI to `coderepomap.core.generator` and this file disappears.
"""

from .core.generator import RepoMapGenerator

__all__ = ["RepoMapGenerator"]
