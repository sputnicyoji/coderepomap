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


@pytest.fixture(scope="module")
def parsed():
    parser = GoParser()
    out = {}
    for f in sorted(FIXTURE.rglob("*.go")):
        if f.name.endswith("_test.go"):
            continue
        rel = f.relative_to(FIXTURE).as_posix()
        syms, refs = parser.parse_file(f, FIXTURE)
        out[rel] = (syms, refs)
    return out


def test_module_path_from_go_mod(parsed):
    """go.mod declares example.com/myapp -> all ids start with that prefix."""
    syms, _ = parsed["main.go"]
    pkgs = [s for s in syms if s.kind == "package"]
    assert any(s.id == "go:example.com/myapp" for s in pkgs)


def test_package_symbol_per_directory(parsed):
    """One package symbol per rel_dir, even though service has two .go files."""
    all_pkgs = []
    for _, (syms, _) in parsed.items():
        all_pkgs.extend(s for s in syms if s.kind == "package")
    pkg_ids = {s.id for s in all_pkgs}
    assert pkg_ids == {
        "go:example.com/myapp",
        "go:example.com/myapp/pkg/service",
        "go:example.com/myapp/pkg/handler",
    }


def test_package_lang_meta_records_clause_name(parsed):
    """`package main` vs `package service` clause is recorded in lang_meta."""
    syms, _ = parsed["pkg/service/service.go"]
    pkg = next(s for s in syms if s.kind == "package")
    assert pkg.lang_meta.get("package_name") == "service"
    assert pkg.lang_meta.get("import_path") == "example.com/myapp/pkg/service"
