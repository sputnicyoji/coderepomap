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


def test_function_id_and_kind(parsed):
    syms, _ = parsed["pkg/service/service.go"]
    funcs = [s for s in syms if s.kind == "function"]
    func_ids = {s.id for s in funcs}
    assert "go:example.com/myapp/pkg/service.NewService" in func_ids


def test_function_exported_flag(parsed):
    syms, _ = parsed["pkg/service/service.go"]
    new_service = next(s for s in syms if s.id == "go:example.com/myapp/pkg/service.NewService")
    assert new_service.lang_meta.get("exported") is True


def test_struct_symbol_is_class_kind(parsed):
    syms, _ = parsed["pkg/service/service.go"]
    svc = next(s for s in syms if s.name == "Service")
    assert svc.kind == "class"
    assert svc.id == "go:example.com/myapp/pkg/service.Service"


def test_interface_symbol_is_interface_kind(parsed):
    syms, _ = parsed["pkg/service/service.go"]
    runner = next(s for s in syms if s.name == "Runner")
    assert runner.kind == "interface"
    assert runner.id == "go:example.com/myapp/pkg/service.Runner"


def test_struct_field_emitted(parsed):
    syms, _ = parsed["pkg/service/service.go"]
    fields = [s for s in syms if s.kind == "field" and s.parent == "Service"]
    field_names = {s.name for s in fields}
    assert "Name" in field_names


def test_method_value_receiver(parsed):
    syms, _ = parsed["pkg/service/service.go"]
    describe = next(s for s in syms if s.kind == "method" and s.name == "Describe")
    assert describe.id == "go:example.com/myapp/pkg/service.Service.Describe"
    assert describe.parent == "Service"
    assert describe.lang_meta.get("receiver_kind") == "val"


def test_method_pointer_receiver(parsed):
    syms, _ = parsed["pkg/service/service.go"]
    run = next(s for s in syms if s.kind == "method" and s.name == "Run" and s.parent == "Service")
    assert run.id == "go:example.com/myapp/pkg/service.Service.Run"
    assert run.lang_meta.get("receiver_kind") == "ptr"


def test_no_overload_collision_across_types(parsed):
    syms, _ = parsed["pkg/service/types.go"]
    fmt = next(s for s in syms if s.kind == "method" and s.name == "Format")
    assert fmt.id == "go:example.com/myapp/pkg/service.Result.Format"
    assert fmt.parent == "Result"


def test_struct_embedding_emits_inherits(parsed):
    """`type Service struct { Name string; Result }` -> Service inherits Result."""
    _, refs = parsed["pkg/service/service.go"]
    inherits = [r for r in refs if r.kind == "inherits"]
    edge = next(
        r for r in inherits
        if r.from_id == "go:example.com/myapp/pkg/service.Service"
        and r.to_id == "go:example.com/myapp/pkg/service.Result"
    )
    assert edge.to_external in ("Result",)


def test_import_default_alias(parsed):
    _, refs = parsed["pkg/handler/handler.go"]
    imp = [r for r in refs if r.kind == "import"]
    assert any(
        r.to_id == "go:example.com/myapp/pkg/service"
        and r.to_external == "example.com/myapp/pkg/service"
        for r in imp
    )


def test_import_explicit_alias(parsed):
    _, refs = parsed["main.go"]
    imp = [r for r in refs if r.kind == "import"]
    assert any(
        r.to_id == "go:example.com/myapp/pkg/service"
        for r in imp
    )


def test_import_default_name_differs_from_path_base(tmp_path):
    """Default import uses basename as selector and marks confidence='low'."""
    src_root = tmp_path
    (src_root / "go.mod").write_text("module example.com/x\n", encoding="utf-8")
    sub = src_root / "consumer"
    sub.mkdir()
    (sub / "main.go").write_text(
        'package consumer\n'
        'import "example.com/x/lib/zoo"\n'
        'func F() { zoo.Touch() }\n',
        encoding="utf-8",
    )
    p = GoParser()
    _, refs = p.parse_file(sub / "main.go", src_root)
    imp = next(r for r in refs if r.kind == "import" and r.to_external == "example.com/x/lib/zoo")
    assert imp.lang_meta.get("import_name_confidence") == "low"


def test_import_major_version_suffix(tmp_path):
    """`/v2` is not used as selector name; previous segment is used instead."""
    src_root = tmp_path
    (src_root / "go.mod").write_text("module example.com/x\n", encoding="utf-8")
    sub = src_root / "consumer"
    sub.mkdir()
    (sub / "main.go").write_text(
        'package consumer\n'
        'import "github.com/foo/bar/v2"\n'
        'func F() { bar.Use() }\n',
        encoding="utf-8",
    )
    p = GoParser()
    _, refs = p.parse_file(sub / "main.go", src_root)
    imp = next(r for r in refs if r.kind == "import" and r.to_external == "github.com/foo/bar/v2")
    assert imp.to_id == "go:github.com/foo/bar/v2"


def test_generic_type_id_has_no_brackets(parsed):
    syms, _ = parsed["pkg/service/service.go"]
    box = next(s for s in syms if s.name == "GenericBox")
    assert "[" not in box.id
    assert "T" in box.lang_meta.get("generic_params", [])
