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


def test_call_cross_package_via_alias(parsed):
    """In main.go: `svc.NewService()` -> Reference to_id pointing at service.NewService."""
    _, refs = parsed["main.go"]
    call = next(
        r for r in refs
        if r.kind == "call" and r.to_id == "go:example.com/myapp/pkg/service.NewService"
    )
    assert call.to_external == "svc.NewService"


def test_call_cross_package_default_selector(parsed):
    """In main.go: `handler.NewHandler()` resolves via default-named import."""
    _, refs = parsed["main.go"]
    assert any(
        r.kind == "call" and r.to_id == "go:example.com/myapp/pkg/handler.NewHandler"
        for r in refs
    )


def test_call_same_package_top_level(parsed):
    """Service.go internals produce at least some Go references."""
    _, refs = parsed["pkg/service/service.go"]
    assert any(r.lang == "go" for r in refs)


def test_call_v2_resolves_through_default_selector(tmp_path):
    """v2-suffix import: bar.Use() resolves to go:github.com/foo/bar/v2.Use"""
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
    call = next(r for r in refs if r.kind == "call" and r.to_external == "bar.Use")
    assert call.to_id == "go:github.com/foo/bar/v2.Use"


def test_regex_fallback_emits_minimum_symbols(tmp_path):
    """Force-disable tree-sitter; regex still produces L1/L2 essentials."""
    src = tmp_path / "main.go"
    src.write_text(
        "package main\n"
        "type Foo struct{}\n"
        "type Bar interface{}\n"
        "func Baz() {}\n"
        "func (f Foo) Quux() {}\n",
        encoding="utf-8",
    )
    p = GoParser()
    p._initialized = True
    p._parser = None
    syms, refs = p.parse_file(src, tmp_path)
    kinds = {(s.kind, s.name) for s in syms}
    assert ("class", "Foo") in kinds
    assert ("interface", "Bar") in kinds
    assert ("function", "Baz") in kinds
    assert ("method", "Quux") in kinds
    assert any(s.kind == "package" for s in syms)


def test_default_excludes_skip_test_files():
    """Source discovery must drop *_test.go by default."""
    from coderepomap.core import source_discovery

    cfg = {"lang": "go", "source": {"root_path": str(FIXTURE)}}
    files = source_discovery.discover(cfg, FIXTURE)
    rels = sorted(str(sf.path.relative_to(sf.root)).replace("\\", "/") for sf in files)
    assert not any(r.endswith("_test.go") for r in rels)
    assert "main.go" in rels
    assert "pkg/service/service.go" in rels


def test_module_path_missing_falls_back_to_rel_dir(tmp_path):
    """No go.mod -> module_path empty -> ids use just the rel dir path."""
    pkg_dir = tmp_path / "alpha"
    pkg_dir.mkdir()
    src = pkg_dir / "x.go"
    src.write_text("package alpha\nfunc Foo() {}\n", encoding="utf-8")
    p = GoParser()
    syms, _ = p.parse_file(src, tmp_path)
    assert any(s.kind == "package" and s.id == "go:alpha" for s in syms)
    assert any(s.id == "go:alpha.Foo" for s in syms)


def test_module_path_resolved_for_subdir(tmp_path):
    """A go.mod at the base resolves correctly for nested files."""
    (tmp_path / "go.mod").write_text("module example.com/x\n\ngo 1.21\n", encoding="utf-8")
    sub = tmp_path / "inner"
    sub.mkdir()
    (sub / "a.go").write_text("package inner\ntype T struct{}\n", encoding="utf-8")
    p = GoParser()
    syms, _ = p.parse_file(sub / "a.go", tmp_path)
    assert any(s.kind == "class" and s.id == "go:example.com/x/inner.T" for s in syms)


def test_fix_c13_generic_receiver_method_extracted(tmp_path):
    """C13: methods on generic receivers `func (s *Service[T]) Run()` must be emitted.

    Previously the receiver-type extraction only handled `type_identifier` and
    `pointer_type(type_identifier)`, silently dropping methods on generic
    types via the `if not recv_type: return` guard.
    """
    src_root = tmp_path
    (src_root / "go.mod").write_text("module example.com/x\n", encoding="utf-8")
    sub = src_root / "pkg"
    sub.mkdir()
    (sub / "g.go").write_text(
        "package pkg\n"
        "type Service[T any] struct{ Value T }\n"
        "func (s *Service[T]) Run() error { return nil }\n"
        "func (s Service[T]) Get() T { return s.Value }\n",
        encoding="utf-8",
    )
    p = GoParser()
    syms, _ = p.parse_file(sub / "g.go", src_root)
    methods = [s for s in syms if s.kind == "method"]
    method_ids = {s.id for s in methods}
    assert "go:example.com/x/pkg.Service.Run" in method_ids, \
        f"Run method on *Service[T] missing; got {method_ids}"
    assert "go:example.com/x/pkg.Service.Get" in method_ids, \
        f"Get method on Service[T] missing; got {method_ids}"


def test_fix_c16_callback_parameter_not_emitted_as_call(tmp_path):
    """C16: a parameter-name call `cb()` inside a function body must NOT emit a
    spurious same-package call Reference. Parameters need to join locals_set."""
    src_root = tmp_path
    (src_root / "go.mod").write_text("module example.com/x\n", encoding="utf-8")
    sub = src_root / "pkg"
    sub.mkdir()
    (sub / "apply.go").write_text(
        "package pkg\n"
        "func Apply(cb func()) { cb() }\n",
        encoding="utf-8",
    )
    p = GoParser()
    _, refs = p.parse_file(sub / "apply.go", src_root)
    # No call ref should target a same-package symbol named `cb` — `cb` is a parameter.
    bad = [r for r in refs if r.kind == "call" and r.to_external == "cb"]
    assert bad == [], f"Spurious parameter-call ref emitted: {bad}"


def test_fix_c19_deep_expression_does_not_recursion_error(tmp_path):
    """C19: a deeply-nested Go AST (long binary chain) must not crash with
    RecursionError. The walker should either be iterative or handle the
    recursion limit gracefully via a try/except + regex fallback."""
    src_root = tmp_path
    (src_root / "go.mod").write_text("module example.com/x\n", encoding="utf-8")
    sub = src_root / "pkg"
    sub.mkdir()
    # 2000 terms — comfortably above Python's default ~1000 recursion limit.
    expr = " + ".join(["1"] * 2000)
    (sub / "deep.go").write_text(
        f"package pkg\nfunc Big() int {{ return {expr} }}\n",
        encoding="utf-8",
    )
    p = GoParser()
    # Must not raise — if RecursionError leaks, the whole generator run dies.
    syms, _ = p.parse_file(sub / "deep.go", src_root)
    # And the function symbol must still be emitted.
    assert any(s.kind == "function" and s.name == "Big" for s in syms)
