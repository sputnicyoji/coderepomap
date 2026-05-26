"""GoParser tests — Phase 1."""
from pathlib import Path

import pytest

import coderepomap.go  # noqa: F401 - triggers register
from coderepomap.core import registry
from coderepomap.core.parser_base import LanguageParser
from coderepomap.go.parser import GoParser


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "go" / "simple_project"


def test_go_parser_subclasses_languageparser():
    assert issubclass(GoParser, LanguageParser)


def test_go_parser_class_attrs():
    assert GoParser.lang == "go"
    assert ".go" in GoParser.file_extensions
    assert "**/vendor/**" in GoParser.default_excludes
    assert "**/*_test.go" in GoParser.default_excludes
    assert set(GoParser.graph_node_kinds) == {"package", "class", "interface", "function"}


def test_go_parser_auto_registered():
    assert "go" in registry.registered_langs()
    p = registry.get_parser("go")
    assert isinstance(p, GoParser)
