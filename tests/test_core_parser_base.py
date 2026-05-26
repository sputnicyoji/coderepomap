"""Phase 1: ABC + identity model unit tests."""
from pathlib import Path

import pytest

from coderepomap.core.parser_base import LanguageParser, Reference, Symbol
from coderepomap.core import identity as ident
from coderepomap.core import registry


# ----- Symbol / Reference shape -----

def test_symbol_defaults():
    s = Symbol(id="lua:foo", name="foo", fqn="foo", kind="module", file="foo.lua", line=1)
    assert s.signature == ""
    assert s.container == ""
    assert s.parent == ""
    assert s.lang == ""
    assert s.lang_meta == {}


def test_reference_defaults():
    r = Reference(from_id="csharp:A", to_id="csharp:B", file="A.cs", line=10, kind="inherit")
    assert r.lang == ""
    assert r.resolved is True
    assert r.to_external == ""
    assert r.lang_meta == {}


def test_unresolved_reference_shape():
    r = Reference(
        from_id="lua:foo.bar.baz",
        to_id="",
        file="foo/bar.lua",
        line=42,
        kind="csharp_call",
        lang="lua",
        resolved=False,
        to_external="CS.UnityEngine.GameObject",
    )
    assert not r.resolved
    assert r.to_id == ""
    assert r.to_external == "CS.UnityEngine.GameObject"


# ----- Identity: C# -----

def test_csharp_class_id():
    assert ident.csharp_type_id("Game.Core", "", "GameManager") == "csharp:Game.Core.GameManager"


def test_csharp_class_id_no_namespace():
    assert ident.csharp_type_id("", "", "Helper") == "csharp:Helper"


def test_csharp_nested_class_id():
    assert ident.csharp_type_id("Game.Data", "Container", "Inner") == "csharp:Game.Data.Container.Inner"


def test_csharp_method_id_no_params():
    assert ident.csharp_method_id("Game.Core", "GameManager", "Init", []) == \
        "csharp:Game.Core.GameManager.Init()"


def test_csharp_method_overload_distinguishable():
    a = ident.csharp_method_id("Game.Core", "GameManager", "AddScore", ["int"])
    b = ident.csharp_method_id("Game.Core", "GameManager", "AddScore", ["string", "int"])
    c = ident.csharp_method_id("Game.Core", "GameManager", "AddScore", ["int", "bool"])
    assert a != b != c
    assert a == "csharp:Game.Core.GameManager.AddScore(int)"
    assert b == "csharp:Game.Core.GameManager.AddScore(string,int)"
    assert c == "csharp:Game.Core.GameManager.AddScore(int,bool)"


def test_csharp_method_same_arity_different_types():
    """The bug the param-type signature fixes: arity alone collides."""
    a = ident.csharp_method_id("X", "T", "M", ["int"])
    b = ident.csharp_method_id("X", "T", "M", ["string"])
    assert a != b


def test_csharp_generic_method_id():
    g = ident.csharp_method_id("X", "T", "M", ["List<int>"])
    assert g == "csharp:X.T.M(List<int>)"


def test_csharp_member_id():
    assert ident.csharp_member_id("Game.Core", "GameManager", "Score") == \
        "csharp:Game.Core.GameManager.Score"


# ----- Identity: Lua -----

def test_lua_module_id(tmp_path):
    root = tmp_path / "Assets" / "LuaScripts"
    f = root / "foo" / "bar.lua"
    f.parent.mkdir(parents=True)
    f.write_text("")
    assert ident.lua_module_id_from_path(f, root) == "foo.bar"


def test_lua_module_id_root_file(tmp_path):
    root = tmp_path / "scripts"
    f = root / "main.lua"
    f.parent.mkdir(parents=True)
    f.write_text("")
    assert ident.lua_module_id_from_path(f, root) == "main"


def test_lua_module_symbol_id():
    assert ident.lua_module_symbol_id("foo.bar") == "lua:foo.bar"


def test_lua_top_level_function_id():
    assert ident.lua_function_id("foo.bar", "init") == "lua:foo.bar.init"


def test_lua_table_function_id():
    """function T.f() — no self, uses `.`"""
    assert ident.lua_function_id("foo.bar", "f", table="T") == "lua:foo.bar.T.f"


def test_lua_method_id_uses_hash():
    """function T:m() — implicit self, uses `#`"""
    assert ident.lua_method_id("foo.bar", "T", "m") == "lua:foo.bar.T#m"


def test_lua_method_vs_function_are_distinct_ids():
    """T:m and T.m are intentionally different symbols."""
    a = ident.lua_method_id("mod", "T", "m")
    b = ident.lua_function_id("mod", "m", table="T")
    assert a != b
    assert "#" in a
    assert "#" not in b


def test_lua_field_id():
    assert ident.lua_field_id("foo.bar", "T", "x") == "lua:foo.bar.T.x"


def test_lua_id_prefix_appears_once():
    """No `:` may appear in the body — only as the lang prefix separator."""
    for fn, args in [
        (ident.lua_module_symbol_id, ("foo.bar",)),
        (ident.lua_function_id, ("foo.bar", "f")),
        (ident.lua_function_id, ("foo.bar", "f", "T")),
        (ident.lua_method_id, ("foo.bar", "T", "m")),
        (ident.lua_field_id, ("foo.bar", "T", "x")),
    ]:
        result = fn(*args)
        assert result.count(":") == 1, result
        assert result.startswith("lua:")


# ----- Registry -----

class _StubParser(LanguageParser):
    lang = "stub"
    file_extensions = [".stub"]
    default_excludes = []
    default_boost_patterns = []
    default_categories = {}

    def parse_file(self, path, base):
        return [], []


def test_registry_register_and_get():
    # snapshot + restore so we don't leak state
    snapshot = dict(registry.PARSERS)
    try:
        registry.PARSERS.clear()
        registry.PARSERS.update(snapshot)
        registry.register(_StubParser)
        assert "stub" in registry.PARSERS
        p = registry.get_parser("stub")
        assert isinstance(p, _StubParser)
    finally:
        registry.PARSERS.clear()
        registry.PARSERS.update(snapshot)


def test_registry_unknown_lang_hint():
    with pytest.raises(ValueError) as exc:
        registry.get_parser("klingon")
    msg = str(exc.value)
    assert "klingon" in msg
    assert "pip install coderepomap[klingon]" in msg


def test_registry_duplicate_register_same_class_ok():
    snapshot = dict(registry.PARSERS)
    try:
        registry.PARSERS.clear()
        registry.register(_StubParser)
        # same class, no-op
        registry.register(_StubParser)
        assert registry.PARSERS["stub"] is _StubParser
    finally:
        registry.PARSERS.clear()
        registry.PARSERS.update(snapshot)


def test_registry_duplicate_register_different_class_raises():
    class _OtherStub(LanguageParser):
        lang = "stub"
        file_extensions = [".stub2"]

        def parse_file(self, path, base):
            return [], []

    snapshot = dict(registry.PARSERS)
    try:
        registry.PARSERS.clear()
        registry.register(_StubParser)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(_OtherStub)
    finally:
        registry.PARSERS.clear()
        registry.PARSERS.update(snapshot)


def test_language_parser_default_graph_node_kinds_is_none():
    """Default keeps v0.1.0 class-only graph behavior; subclasses opt into widening."""
    from coderepomap.core.parser_base import LanguageParser

    assert LanguageParser.graph_node_kinds is None


def test_language_parser_subclass_can_set_graph_node_kinds():
    from coderepomap.core.parser_base import LanguageParser

    class _Probe(LanguageParser):
        lang = "probe"
        file_extensions = [".probe"]
        graph_node_kinds = ["class", "function"]

        def parse_file(self, path, base):
            return [], []

    assert _Probe.graph_node_kinds == ["class", "function"]


def test_go_package_id_with_module():
    from coderepomap.core import identity as ident
    assert ident.go_package_id("example.com/myapp", "pkg/service") == \
        "go:example.com/myapp/pkg/service"


def test_go_package_id_without_module():
    from coderepomap.core import identity as ident
    assert ident.go_package_id("", "pkg/service") == "go:pkg/service"


def test_go_package_id_root_dir():
    from coderepomap.core import identity as ident
    assert ident.go_package_id("example.com/myapp", "") == "go:example.com/myapp"


def test_go_package_id_normalizes_windows_separators():
    from coderepomap.core import identity as ident
    # Raw string: literal backslash, not the invalid escape `\s`. Python 3.12+
    # emits SyntaxWarning for the unraw form; some future Python escalates to
    # SyntaxError.
    assert ident.go_package_id("example.com/myapp", r"pkg\service") == \
        "go:example.com/myapp/pkg/service"


def test_go_function_id():
    from coderepomap.core import identity as ident
    assert ident.go_function_id("example.com/myapp", "pkg/service", "NewService") == \
        "go:example.com/myapp/pkg/service.NewService"


def test_go_type_id_and_method_id():
    from coderepomap.core import identity as ident
    assert ident.go_type_id("example.com/myapp", "pkg/service", "Service") == \
        "go:example.com/myapp/pkg/service.Service"
    assert ident.go_method_id("example.com/myapp", "pkg/service", "Service", "Run") == \
        "go:example.com/myapp/pkg/service.Service.Run"


def test_go_member_id_struct_field():
    from coderepomap.core import identity as ident
    assert ident.go_member_id("example.com/myapp", "pkg/service", "Service", "Name") == \
        "go:example.com/myapp/pkg/service.Service.Name"
