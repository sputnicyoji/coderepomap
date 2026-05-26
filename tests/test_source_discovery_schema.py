"""Schema-mismatch guards across both the public API entry points.

Prior to v0.2.x, a config that mixed the single-lang field `lang:` with
the multi-lang field `sources:` would silently fall back to the default
single-lang `source.root_path = "."`, scanning cwd and ignoring the
user's intended root + excludes.

The guard lives in TWO places and BOTH must be tested:
  1. `RepoMapGenerator.load_config(yaml_path)` — the real CLI path, what
     actual users go through.
  2. `source_discovery.discover(config_dict, root)` — a defense-in-depth
     guard for callers that build a config dict directly (tests, library
     consumers bypassing load_config).

A bug found in code review: when only path 2 is tested, path 1 can
silently strip user-set fields during default-merge and re-introduce the
silent-fallback regression undetected. Both paths must stay covered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coderepomap.core.generator import RepoMapGenerator
from coderepomap.core.source_discovery import discover


def test_lang_plus_sources_raises():
    """Single-lang `lang:` with plural `sources:` must raise, not silently
    fall back to cwd scan."""
    config = {
        "lang": "csharp",
        "sources": {
            "csharp": {"root_path": "src"},
        },
    }
    with pytest.raises(ValueError, match=r"single-lang.*sources"):
        discover(config, Path.cwd())


def test_langs_plus_source_raises():
    """Multi-lang `langs:` with singular `source:` must raise."""
    config = {
        "langs": ["csharp"],
        "sources": {"csharp": {"root_path": "src"}},
        "source": {"root_path": "src"},
    }
    with pytest.raises(ValueError, match=r"multi-lang.*source"):
        discover(config, Path.cwd())


def test_correct_single_lang_does_not_raise_on_guard(tmp_path: Path):
    """A correctly shaped single-lang config must not trip the new guard.
    It may still fail later (missing root), but not with the schema error.
    """
    (tmp_path / "src").mkdir()
    config = {
        "lang": "csharp",
        "source": {"root_path": "src"},
    }
    # Should not raise the schema ValueError; may return empty list since
    # the directory has no .cs files.
    result = discover(config, tmp_path)
    assert result == []


def test_correct_multi_lang_does_not_raise_on_guard(tmp_path: Path):
    """A correctly shaped multi-lang config must not trip the new guard."""
    (tmp_path / "cs_src").mkdir()
    config = {
        "langs": ["csharp"],
        "sources": {
            "csharp": {"root_path": "cs_src"},
        },
    }
    result = discover(config, tmp_path)
    assert result == []


# ---------------------------------------------------------------------------
# The real-user path: through RepoMapGenerator.load_config(yaml_file).
# A code review found that load_config used to silently strip user-set
# `sources:` when the user also set `lang:`, defeating the discover() guard
# entirely on the CLI path. These tests pin that the schema check fires at
# load time, not after the user-set fields have been merged-and-popped.
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_config_rejects_lang_plus_sources(tmp_path: Path):
    """Real CLI path: yaml that mixes singular `lang:` with plural
    `sources:` must raise at load time."""
    cfg = _write_yaml(
        tmp_path,
        "lang: csharp\nsources:\n  csharp:\n    root_path: src\n",
    )
    with pytest.raises(ValueError, match=r"single-lang.*sources"):
        RepoMapGenerator.load_config(cfg)


def test_load_config_rejects_langs_plus_source(tmp_path: Path):
    """Real CLI path: yaml that mixes plural `langs:` with singular
    `source:` must raise at load time."""
    cfg = _write_yaml(
        tmp_path,
        "langs: [csharp]\nsource:\n  root_path: src\n",
    )
    with pytest.raises(ValueError, match=r"multi-lang.*source"):
        RepoMapGenerator.load_config(cfg)


def test_load_config_rejects_lang_plus_langs(tmp_path: Path):
    """Real CLI path: yaml that sets both `lang:` and `langs:` must raise,
    not silently prefer one (the pre-fix code popped `lang:` and let
    `discover()` never see the conflict)."""
    cfg = _write_yaml(
        tmp_path,
        "lang: csharp\nlangs: [csharp]\nsources:\n  csharp:\n    root_path: src\n",
    )
    with pytest.raises(ValueError, match=r"both `lang:` and `langs:`"):
        RepoMapGenerator.load_config(cfg)


def test_load_config_rejects_non_mapping_yaml(tmp_path: Path):
    """A yaml whose top level is a list / scalar / string must raise, not
    be silently swallowed into a default-config CWD scan."""
    cfg = _write_yaml(tmp_path, "- lang: csharp\n- source: {root_path: x}\n")
    with pytest.raises(ValueError, match=r"YAML mapping"):
        RepoMapGenerator.load_config(cfg)


def test_load_config_accepts_empty_yaml(tmp_path: Path):
    """Empty yaml file is allowed (uses defaults). Distinct from a malformed
    non-mapping yaml."""
    cfg = _write_yaml(tmp_path, "")
    result = RepoMapGenerator.load_config(cfg)
    assert isinstance(result, dict)


def test_load_config_accepts_correct_single_lang(tmp_path: Path):
    """A well-formed single-lang yaml must pass load_config and discover."""
    (tmp_path / "src").mkdir()
    cfg = _write_yaml(
        tmp_path,
        "lang: csharp\nsource:\n  root_path: src\n",
    )
    result = RepoMapGenerator.load_config(cfg)
    assert result.get("lang") == "csharp"
    # Defense in depth: the default-carried `sources` must not be present
    # after load_config when the user chose single-lang shape.
    assert "sources" not in result
    # And discover must accept the result.
    assert discover(result, tmp_path) == []


def test_load_config_accepts_correct_multi_lang(tmp_path: Path):
    """A well-formed multi-lang yaml must pass load_config and discover."""
    (tmp_path / "cs_src").mkdir()
    cfg = _write_yaml(
        tmp_path,
        "langs: [csharp]\nsources:\n  csharp:\n    root_path: cs_src\n",
    )
    result = RepoMapGenerator.load_config(cfg)
    assert result.get("langs") == ["csharp"]
    # Defense in depth: default-carried `source:` must be stripped.
    assert "source" not in result
    assert discover(result, tmp_path) == []
