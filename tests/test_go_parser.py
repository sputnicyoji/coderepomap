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


def test_fix_c17_go_mod_without_module_line_falls_through_to_parent(tmp_path):
    """C17: a nested go.mod lacking a parseable `module` directive must not
    short-circuit the ancestor walk — the parent's valid go.mod should win.

    Reproducer: base_path = the dir containing the stray go.mod. The walk must
    fall through to the parent dir's valid go.mod.
    """
    # Parent has a valid go.mod
    (tmp_path / "go.mod").write_text("module example.com/root\n\ngo 1.21\n",
                                     encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "go.mod").write_text("// stub during migration\n\ngo 1.21\n",
                                   encoding="utf-8")
    sub = nested / "pkg"
    sub.mkdir()
    (sub / "x.go").write_text("package pkg\nfunc Foo() {}\n", encoding="utf-8")
    p = GoParser()
    syms, _ = p.parse_file(sub / "x.go", nested)
    # The valid parent go.mod must be discovered despite the stray nested one.
    assert any("example.com/root" in s.id for s in syms), \
        f"Parent module path lost to stray go.mod; got ids: {[s.id for s in syms]}"


def test_fix_c25_init_parser_failure_does_not_latch_initialized(tmp_path):
    """C25: when tree-sitter init fails (ImportError / Exception), _initialized
    must NOT be set to True — subsequent calls should be able to retry. This
    matches LegacyCSharpParser's policy and avoids silent regex degradation
    for the entire run when a transient init issue occurs."""
    p = GoParser()
    # Simulate init failure by monkeypatching the import target.
    # The easiest reliable simulation: pre-set _parser=None and _initialized=False,
    # invoke a synthetic failure path manually via a subclass override.
    class FailingParser(GoParser):
        def _init_parser(self) -> bool:
            # Mimic the (corrected) failure path: do NOT latch _initialized.
            self._parser = None
            return False

    fp = FailingParser()
    assert fp._init_parser() is False
    # Second call: still should be allowed to retry (not short-circuited).
    # The CONTRACT is: if _initialized is False, the next caller may retry.
    # Verify the GoParser source enforces this by inspecting the lines.
    import inspect
    src = inspect.getsource(GoParser._init_parser)
    # The corrected source must NOT set _initialized=True inside the except
    # branches. Equivalent semantic check via behavior:
    # repeated calls after failure should not latch a permanent regex state.
    p2 = GoParser()
    # Force first init to "fail" by clobbering tree_sitter_go to raise on import:
    import builtins
    real_import = builtins.__import__
    calls = {"n": 0}
    def fail_first(name, *args, **kwargs):
        if name == "tree_sitter_go" and calls["n"] == 0:
            calls["n"] += 1
            raise ImportError("simulated transient")
        return real_import(name, *args, **kwargs)
    builtins.__import__ = fail_first
    try:
        first = p2._init_parser()
        assert first is False
        # _initialized must allow a second attempt (i.e. either stay False, or
        # be True only when init succeeded). After a FAILED init, _parser is
        # None and a retry policy is required.
        second = p2._init_parser()
        # On retry, the real import succeeds; expect True now.
        assert second is True, "Failed init must not permanently latch regex mode"
    finally:
        builtins.__import__ = real_import


def test_fix_c24_import_tables_removed_or_consumed():
    """C24: _import_tables was dead code (written, never read). Verify it's
    either gone entirely or has a non-trivial reader site."""
    import inspect
    src = inspect.getsource(GoParser)
    if "_import_tables" not in src:
        return  # removed — clean
    raise AssertionError(
        "_import_tables still present in GoParser; expected removal (no consumer)"
    )


def test_fix_c14_regex_fallback_aliased_single_line_import(tmp_path):
    """C14: aliased / blank / dot single-line imports must be captured by regex fallback."""
    src = tmp_path / "main.go"
    src.write_text(
        'package main\n'
        'import svc "example.com/svc"\n'
        'import _ "github.com/lib/pq"\n'
        'import . "fmt"\n'
        'func main() {}\n',
        encoding="utf-8",
    )
    p = GoParser()
    # Force regex path
    p._initialized = True
    p._parser = None
    _, refs = p.parse_file(src, tmp_path)
    paths = {r.to_external for r in refs if r.kind == "import"}
    assert "example.com/svc" in paths, f"aliased import missing: {paths}"
    assert "github.com/lib/pq" in paths, f"blank import missing: {paths}"
    assert "fmt" in paths, f"dot import missing: {paths}"


def test_fix_c15_regex_fallback_emits_signature(tmp_path):
    """C15: regex-fallback Symbols must have a non-empty signature so render_l2
    doesn't produce blank `### ` headers."""
    src = tmp_path / "x.go"
    src.write_text(
        "package x\n"
        "type Foo struct{}\n"
        "type Bar interface{}\n"
        "func Baz() {}\n"
        "func (f Foo) Quux() {}\n",
        encoding="utf-8",
    )
    p = GoParser()
    p._initialized = True
    p._parser = None
    syms, _ = p.parse_file(src, tmp_path)
    for s in syms:
        assert s.signature != "", \
            f"Symbol {s.id} (kind={s.kind}) has empty signature in regex fallback"


def test_fix_c20_regex_import_block_comment_with_paren_does_not_truncate(tmp_path):
    """C20: a `)` inside a `// comment` within an import block must not
    prematurely terminate the regex match."""
    src = tmp_path / "x.go"
    src.write_text(
        'package x\n'
        'import (\n'
        '    "fmt"  // see issue ) here\n'
        '    "os"\n'
        ')\n'
        'func main() {}\n',
        encoding="utf-8",
    )
    p = GoParser()
    p._initialized = True
    p._parser = None
    _, refs = p.parse_file(src, tmp_path)
    paths = {r.to_external for r in refs if r.kind == "import"}
    assert "fmt" in paths
    assert "os" in paths, f"`os` import dropped by comment-) bug; got {paths}"


def test_fix_codegen_pb_go_default_excluded(tmp_path):
    """*.pb.go (protobuf-generated) must be skipped by default_excludes."""
    from coderepomap.core import source_discovery

    (tmp_path / "go.mod").write_text("module example.com/x\n", encoding="utf-8")
    sub = tmp_path / "pb"
    sub.mkdir()
    (sub / "user.go").write_text("package pb\ntype User struct{}\n", encoding="utf-8")
    (sub / "user.pb.go").write_text("package pb\ntype UserGen struct{}\n", encoding="utf-8")
    cfg = {"lang": "go", "source": {"root_path": str(tmp_path)}}
    files = source_discovery.discover(cfg, tmp_path)
    rels = sorted(p.path.relative_to(p.root).as_posix() for p in files)
    assert "pb/user.go" in rels
    assert not any(r.endswith(".pb.go") for r in rels), \
        f"*.pb.go must be excluded by default; got {rels}"


def test_fix_codegen_comment_sentinel_skips_file(tmp_path):
    """A Go file whose head has `Code generated ... DO NOT EDIT.` must emit
    zero symbols (matches the Go community convention for generated code)."""
    src_root = tmp_path
    (src_root / "go.mod").write_text("module example.com/x\n", encoding="utf-8")
    sub = src_root / "gen"
    sub.mkdir()
    (sub / "x.go").write_text(
        "// Code generated by stringer; DO NOT EDIT.\n"
        "package gen\n"
        "type T struct{ F int }\n"
        "func (t T) Run() {}\n",
        encoding="utf-8",
    )
    p = GoParser()
    syms, refs = p.parse_file(sub / "x.go", src_root)
    # Package symbol may remain (gives the importer one node to anchor on),
    # but generated business symbols should be skipped.
    non_pkg = [s for s in syms if s.kind != "package"]
    assert non_pkg == [], f"Generated file leaked symbols: {[s.id for s in non_pkg]}"


def test_fix_codegen_non_generated_file_still_parsed(tmp_path):
    """Sanity: file lacking the generated-comment marker still emits symbols."""
    src_root = tmp_path
    (src_root / "go.mod").write_text("module example.com/x\n", encoding="utf-8")
    sub = src_root / "pkg"
    sub.mkdir()
    (sub / "x.go").write_text(
        "// Package pkg implements business logic.\n"
        "package pkg\n"
        "type T struct{}\n",
        encoding="utf-8",
    )
    p = GoParser()
    syms, _ = p.parse_file(sub / "x.go", src_root)
    assert any(s.name == "T" and s.kind == "class" for s in syms)
