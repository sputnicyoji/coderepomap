"""
TypeScriptParser unit tests (new contract).

Fixture: tests/fixtures/typescript/simple_project — a small ESM-style project
exercising classes, interfaces, type aliases, enums, arrow consts, methods,
the four import shapes (named / default-less side-effect / namespace /
external), `.js`-suffixed relative specifiers, directory imports through
`index.ts`, re-exports, and heritage clauses.
"""

from pathlib import Path

import pytest

from coderepomap.core.parser_base import LanguageParser
from coderepomap.typescript.parser import TypeScriptParser

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "typescript" / "simple_project"


@pytest.fixture(scope="module")
def parsed():
    """Parse the whole fixture with ONE parser instance (package dedup is
    instance-level, mirroring how the generator caches parsers per lang)."""
    parser = TypeScriptParser()
    symbols = []
    references = []
    for f in sorted(FIXTURE.rglob("*.ts")):
        syms, refs = parser.parse_file(f, FIXTURE)
        symbols.extend(syms)
        references.extend(refs)
    return symbols, references


def _by_id(symbols, sid):
    matches = [s for s in symbols if s.id == sid]
    assert matches, f"no symbol with id {sid!r}; have: {[s.id for s in symbols]}"
    return matches[0]


def _refs(references, **filters):
    out = references
    for key, value in filters.items():
        out = [r for r in out if getattr(r, key) == value]
    return out


# --- contract ----------------------------------------------------------------

def test_typescript_parser_subclasses_languageparser():
    assert issubclass(TypeScriptParser, LanguageParser)


def test_typescript_parser_class_attrs():
    assert TypeScriptParser.lang == "typescript"
    assert ".ts" in TypeScriptParser.file_extensions
    assert ".tsx" in TypeScriptParser.file_extensions
    assert "**/node_modules/**" in TypeScriptParser.default_excludes
    assert "**/*.d.ts" in TypeScriptParser.default_excludes
    for kind in ("package", "module", "class", "interface", "function"):
        assert kind in TypeScriptParser.graph_node_kinds


def test_typescript_parser_auto_registered():
    from coderepomap.core.registry import get_parser

    parser = get_parser("typescript")
    assert isinstance(parser, TypeScriptParser)


# --- structural symbols --------------------------------------------------------

def test_module_symbol_per_file(parsed):
    symbols, _ = parsed
    mod = _by_id(symbols, "typescript:services/user-service")
    assert mod.kind == "module"
    assert mod.name == "user-service"
    assert mod.fqn == "services/user-service"


def test_package_symbol_per_directory_with_trailing_slash(parsed):
    symbols, _ = parsed
    pkg = _by_id(symbols, "typescript:services/")
    assert pkg.kind == "package"
    assert pkg.name == "services"
    packages = [s for s in symbols if s.id == "typescript:services/"]
    assert len(packages) == 1, "package symbol must be deduped per directory"


def test_root_files_have_no_package_symbol(parsed):
    symbols, _ = parsed
    root_packages = [s for s in symbols if s.kind == "package" and s.id == "typescript:/"]
    assert not root_packages


# --- declarations ---------------------------------------------------------------

def test_class_symbol_with_heritage_signature(parsed):
    symbols, _ = parsed
    cls = _by_id(symbols, "typescript:services/user-service.UserService")
    assert cls.kind == "class"
    assert cls.container == "typescript:services/user-service"
    assert cls.lang_meta["exported"] is True
    assert "extends BaseService" in cls.signature
    assert "implements Disposable" in cls.signature


def test_abstract_class_flag(parsed):
    symbols, _ = parsed
    cls = _by_id(symbols, "typescript:services/base.BaseService")
    assert cls.kind == "class"
    assert cls.lang_meta["abstract"] is True


def test_method_modifiers_and_parent_container_pairing(parsed):
    symbols, _ = parsed
    log = _by_id(symbols, "typescript:services/base.BaseService.log")
    assert log.kind == "method"
    assert log.parent == "BaseService"
    cls = _by_id(symbols, "typescript:services/base.BaseService")
    assert log.container == cls.container, (
        "renderer pairs methods to classes via (parent, container) equality"
    )
    assert log.lang_meta["visibility"] == "protected"
    assert "(message: string)" in log.signature


def test_static_method_flag(parsed):
    symbols, _ = parsed
    create = _by_id(symbols, "typescript:services/user-service.UserService.create")
    assert create.lang_meta["static"] is True


def test_arrow_property_is_method(parsed):
    symbols, _ = parsed
    dispose = _by_id(symbols, "typescript:services/user-service.UserService.dispose")
    assert dispose.kind == "method"
    assert dispose.lang_meta["arrow_property"] is True


def test_interface_and_member_signature(parsed):
    symbols, _ = parsed
    user = _by_id(symbols, "typescript:types.User")
    assert user.kind == "interface"
    greet = _by_id(symbols, "typescript:types.User.greet")
    assert greet.kind == "method"
    assert greet.lang_meta["interface_member"] is True
    assert "Promise<string>" in greet.signature


def test_type_alias_is_interface_kind(parsed):
    symbols, _ = parsed
    alias = _by_id(symbols, "typescript:types.UserId")
    assert alias.kind == "interface"
    assert alias.lang_meta["declaration_form"] == "type_alias"


def test_enum_is_class_kind(parsed):
    symbols, _ = parsed
    role = _by_id(symbols, "typescript:types.Role")
    assert role.kind == "class"
    assert role.lang_meta["declaration_form"] == "enum"
    assert role.signature == "enum Role"


def test_function_signature_carries_real_types(parsed):
    symbols, _ = parsed
    fn = _by_id(symbols, "typescript:utils/helpers.formatName")
    assert fn.kind == "function"
    assert fn.lang_meta["exported"] is True
    assert "(first: string, last: string)" in fn.signature
    assert ": string" in fn.signature


def test_arrow_const_is_function(parsed):
    symbols, _ = parsed
    fn = _by_id(symbols, "typescript:utils/helpers.slugify")
    assert fn.kind == "function"
    assert fn.lang_meta["arrow_const"] is True


def test_plain_const_emits_no_symbol(parsed):
    symbols, _ = parsed
    assert not [s for s in symbols if s.id == "typescript:utils/helpers.MAX_USERS"]


def test_non_exported_function_flagged(parsed):
    symbols, _ = parsed
    fn = _by_id(symbols, "typescript:utils/helpers.internalHelper")
    assert fn.lang_meta["exported"] is False


# --- imports ----------------------------------------------------------------------

def test_named_import_strips_js_suffix(parsed):
    _, references = parsed
    refs = _refs(
        references, kind="import",
        from_id="typescript:services/user-service",
        to_id="typescript:services/base.BaseService",
    )
    assert refs and refs[0].resolved is False, (
        "optimistic prediction stays unresolved until crosslang flips it"
    )


def test_type_only_import_flagged(parsed):
    _, references = parsed
    refs = _refs(
        references, kind="import",
        from_id="typescript:services/user-service",
        to_id="typescript:types.User",
    )
    assert refs and refs[0].lang_meta.get("type_only") is True


def test_namespace_import_binds_to_module(parsed):
    _, references = parsed
    refs = _refs(
        references, kind="import",
        from_id="typescript:services/user-service",
        to_id="typescript:utils/helpers",
    )
    assert refs, "namespace import must reference the target module symbol"


def test_external_package_import_stays_external(parsed):
    _, references = parsed
    refs = _refs(
        references, kind="import",
        from_id="typescript:services/user-service",
        to_external="zod",
    )
    assert refs and refs[0].to_id == ""


def test_directory_import_resolves_through_index(parsed):
    _, references = parsed
    refs = _refs(
        references, kind="import",
        from_id="typescript:index",
        to_id="typescript:services/index.UserService",
    )
    assert refs, "`from './services'` must resolve through services/index.ts"


def test_side_effect_import_targets_module(parsed):
    _, references = parsed
    refs = _refs(
        references, kind="import",
        from_id="typescript:index",
        to_id="typescript:polyfill",
    )
    assert refs


def test_reexport_emits_import_refs(parsed):
    _, references = parsed
    refs = _refs(
        references, kind="import",
        from_id="typescript:services/index",
        to_id="typescript:services/base.BaseService",
    )
    assert refs and refs[0].lang_meta.get("reexport") is True
    star = _refs(
        references, kind="import",
        from_id="typescript:services/index",
        to_id="typescript:types",
    )
    assert star, "`export * from` must reference the target module"


# --- heritage -----------------------------------------------------------------------

def test_extends_emits_inherits_ref(parsed):
    _, references = parsed
    refs = _refs(
        references, kind="inherits",
        from_id="typescript:services/user-service.UserService",
        to_id="typescript:services/base.BaseService",
    )
    assert refs


def test_implements_unknown_global_stays_external(parsed):
    _, references = parsed
    refs = _refs(
        references, kind="implements",
        from_id="typescript:services/user-service.UserService",
    )
    assert refs and refs[0].to_id == "" and refs[0].to_external == "Disposable"


# --- body references -------------------------------------------------------------------

def test_call_through_named_import_binding(parsed):
    _, references = parsed
    refs = _refs(
        references, kind="call",
        from_id="typescript:services/user-service",
        to_id="typescript:utils/helpers.formatName",
    )
    assert refs


def test_member_call_through_namespace_binding_targets_module(parsed):
    _, references = parsed
    refs = _refs(
        references, kind="call",
        from_id="typescript:services/user-service",
        to_id="typescript:utils/helpers",
    )
    assert refs and refs[0].to_external == "helpers.slugify"


def test_same_file_call_to_top_level_function(parsed):
    _, references = parsed
    refs = _refs(
        references, kind="call",
        from_id="typescript:utils/helpers",
        to_id="typescript:utils/helpers.internalHelper",
    )
    assert refs


def test_new_expression_emits_uses_ref(parsed):
    _, references = parsed
    refs = _refs(
        references, kind="uses",
        from_id="typescript:services/user-service",
        to_id="typescript:services/user-service.UserService",
    )
    assert refs


def test_external_binding_calls_are_suppressed(parsed):
    _, references = parsed
    refs = [
        r for r in references
        if r.kind == "call" and r.to_external.startswith("z.")
    ]
    assert not refs, "calls through external-package bindings must not emit refs"


def test_builtin_and_local_calls_are_suppressed(parsed):
    _, references = parsed
    assert not [r for r in references if r.to_external.startswith("console.")]


# --- crosslang integration ----------------------------------------------------------

def test_crosslang_resolves_predicted_ids_and_promotes_misses(parsed):
    from coderepomap.core import crosslang

    symbols, references = parsed
    refs = [
        # fresh copies so the module-scoped fixture stays pristine
        type(r)(**{k: getattr(r, k) for k in (
            "from_id", "to_id", "file", "line", "kind", "lang",
            "resolved", "to_external", "lang_meta",
        )})
        for r in references
    ]
    crosslang.resolve(symbols, refs, {})

    exact = [
        r for r in refs
        if r.to_id == "typescript:services/base.BaseService" and r.kind == "inherits"
    ]
    assert exact and exact[0].resolved is True

    # `import { UserService } from './services'` predicts
    # services/index.UserService, which has no symbol — the trailing-segment
    # trim must promote it to the services/index module node.
    promoted = [
        r for r in refs
        if r.from_id == "typescript:index" and r.kind == "import"
        and r.to_id == "typescript:services/index"
    ]
    assert promoted and promoted[0].resolved is True

    external = [r for r in refs if r.to_external == "zod"]
    assert external and external[0].resolved is False


# --- regex fallback ---------------------------------------------------------------------

def test_regex_fallback_covers_top_level_declarations():
    parser = TypeScriptParser()
    # Force the regex path regardless of whether tree-sitter is installed.
    parser._initialized = True
    parser._ts_parser = None
    parser._tsx_parser = None

    symbols = []
    references = []
    for f in sorted(FIXTURE.rglob("*.ts")):
        syms, refs = parser.parse_file(f, FIXTURE)
        symbols.extend(syms)
        references.extend(refs)

    ids = {s.id for s in symbols}
    assert "typescript:services/user-service.UserService" in ids
    assert "typescript:types.User" in ids
    assert "typescript:types.UserId" in ids
    assert "typescript:types.Role" in ids
    assert "typescript:utils/helpers.formatName" in ids
    assert "typescript:utils/helpers.slugify" in ids
    assert "typescript:services/" in ids
    assert "typescript:services/user-service" in ids

    import_refs = [r for r in references if r.kind == "import"]
    assert any(r.to_id == "typescript:services/base" for r in import_refs), (
        "regex mode resolves relative imports at module level"
    )
    assert any(r.to_external == "zod" and r.to_id == "" for r in import_refs)
