"""
CLI tests for Phase 6: coderepomap.core.cli migration.
"""

import subprocess
import sys
from pathlib import Path

import pytest


def test_get_templates_dir_resolves_to_core_dir():
    """get_templates_dir() must point at coderepomap/templates/ and contain config.generic.yaml."""
    from coderepomap.core.cli import get_templates_dir

    d = get_templates_dir()
    # Must end with coderepomap/templates (normalise separators)
    parts = d.parts
    assert parts[-1] == 'templates', f"Expected last part 'templates', got {d}"
    assert parts[-2] == 'coderepomap', f"Expected second-last part 'coderepomap', got {d}"
    assert d.is_dir(), f"Directory does not exist: {d}"
    assert (d / 'config.generic.yaml').exists(), f"config.generic.yaml missing in {d}"


def test_get_lang_templates_dir_csharp():
    """get_lang_templates_dir('csharp') must point at coderepomap/csharp/templates/."""
    from coderepomap.core.cli import get_lang_templates_dir

    d = get_lang_templates_dir('csharp')
    parts = d.parts
    assert parts[-1] == 'templates', f"Expected last part 'templates', got {d}"
    assert parts[-2] == 'csharp', f"Expected second-last part 'csharp', got {d}"
    assert d.is_dir(), f"Directory does not exist: {d}"
    assert (d / 'config.csharp.yaml').exists(), f"config.csharp.yaml missing in {d}"
    assert (d / 'config.unity.yaml').exists(), f"config.unity.yaml missing in {d}"


def test_version_string():
    """CLI --version must output 'coderepomap' and '0.2.0'."""
    result = subprocess.run(
        [sys.executable, '-m', 'coderepomap', '--version'],
        capture_output=True, text=True
    )
    output = result.stdout + result.stderr
    assert 'coderepomap' in output, f"Expected 'coderepomap' in version output: {output!r}"
    assert '0.2.0' in output, f"Expected '0.2.0' in version output: {output!r}"


def test_help_no_csharp_only_wording():
    """CLI --help must mention 'multi-language', not 'C# projects' only."""
    result = subprocess.run(
        [sys.executable, '-m', 'coderepomap', '--help'],
        capture_output=True, text=True
    )
    output = result.stdout + result.stderr
    assert 'multi-language' in output, (
        f"Expected 'multi-language' in help output: {output!r}"
    )


def test_init_lua_unity_rejected(tmp_path):
    """'init --lang lua --preset unity' must exit non-zero with a rejection message."""
    result = subprocess.run(
        [sys.executable, '-m', 'coderepomap', 'init', '--lang', 'lua', '--preset', 'unity'],
        capture_output=True, text=True,
        cwd=str(tmp_path)
    )
    assert result.returncode != 0, (
        "Expected non-zero exit code for --lang lua --preset unity"
    )
    combined = result.stdout + result.stderr
    assert 'unity' in combined.lower() and ('csharp' in combined.lower() or 'only' in combined.lower()), (
        f"Expected rejection message in output: {combined!r}"
    )


def test_cli_init_lang_go_creates_config(tmp_path, monkeypatch):
    """`repomap init --lang go` writes config.yaml with `lang: go`."""
    from coderepomap.core import cli
    monkeypatch.chdir(tmp_path)
    args = type("A", (), {"lang": "go", "preset": "generic", "force": False})()
    rc = cli.cmd_init(args)
    assert rc == 0
    cfg = (tmp_path / ".repomap" / "config.yaml").read_text(encoding="utf-8")
    assert "lang: go" in cfg


def test_cli_init_lang_go_with_unity_preset_rejected(tmp_path, monkeypatch):
    """Unity preset is meaningless for Go; CLI must reject it."""
    from coderepomap.core import cli
    monkeypatch.chdir(tmp_path)
    args = type("A", (), {"lang": "go", "preset": "unity", "force": False})()
    rc = cli.cmd_init(args)
    assert rc == 1


def test_pyproject_declares_go_extras():
    import pathlib
    text = pathlib.Path("pyproject.toml").read_text(encoding="utf-8")
    assert "tree-sitter-go" in text
    assert "coderepomap.go" in text
