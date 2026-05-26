"""
Phase 7: LuaParser tests.

Covers:
- Auto-registration via `import coderepomap.lua`
- Module symbol per file
- `function M.f()` (function, parent=M) and `function M:m()` (method, parent=M with # in id)
- `local Class = setmetatable({}, {__index = Base})` class with parent
- `local Foo = require "foo.bar"` alias + downstream `Foo.x()` call resolution
- `local GO = CS.UnityEngine.GameObject` alias + `GO.Find()` csharp_call
- Direct `CS.X.Y.Z` chain
- `obj:method()` call style
"""
from pathlib import Path

import pytest

import coderepomap.lua  # noqa: F401 - triggers register
from coderepomap.core import registry
from coderepomap.core.parser_base import LanguageParser
from coderepomap.lua.parser import LuaParser


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "lua" / "basic"


# --- registration ---

def test_lua_parser_subclasses_languageparser():
    assert issubclass(LuaParser, LanguageParser)


def test_lua_parser_class_attrs():
    assert LuaParser.lang == "lua"
    assert ".lua" in LuaParser.file_extensions
    assert "**/test/**" in LuaParser.default_excludes


def test_lua_parser_auto_registered():
    assert "lua" in registry.registered_langs()
    p = registry.get_parser("lua")
    assert isinstance(p, LuaParser)


# --- fixture parsing ---

@pytest.fixture(scope="module")
def parsed():
    parser = LuaParser()
    out = {}
    for f in sorted(FIXTURE.glob("*.lua")):
        syms, refs = parser.parse_file(f, FIXTURE)
        out[f.name] = (syms, refs)
    return out


def test_each_file_emits_module_symbol(parsed):
    for name, (syms, _) in parsed.items():
        mods = [s for s in syms if s.kind == "module"]
        assert len(mods) == 1, f"{name}: expected 1 module, got {len(mods)}"


def test_base_lua_symbols(parsed):
    syms, _ = parsed["base.lua"]
    by_kind = {}
    for s in syms:
        by_kind.setdefault(s.kind, []).append(s)
    assert {s.id for s in by_kind["module"]} == {"lua:base"}
    func_ids = {s.id for s in by_kind.get("function", [])}
    method_ids = {s.id for s in by_kind.get("method", [])}
    assert "lua:base.M.shared" in func_ids
    assert "lua:base.M#helper" in method_ids


def test_method_id_uses_hash_separator(parsed):
    syms, _ = parsed["base.lua"]
    helper = [s for s in syms if s.kind == "method" and s.name == "helper"]
    assert len(helper) == 1
    assert "#helper" in helper[0].id
    assert ":helper" not in helper[0].id.split(":", 1)[1]  # no `:` in body


def test_inherit_lua_class_with_setmetatable(parsed):
    syms, refs = parsed["inherit.lua"]
    classes = [s for s in syms if s.kind == "class"]
    child = [s for s in classes if s.name == "Child"]
    assert len(child) == 1
    assert child[0].parent == "Base"
    assert child[0].lang_meta.get("base") == "Base"


def test_inherit_lua_require_reference(parsed):
    _, refs = parsed["inherit.lua"]
    req = [r for r in refs if r.kind == "require"]
    assert len(req) == 1
    assert req[0].to_external == "base"


def test_inherit_lua_inherits_reference_resolves_via_alias(parsed):
    """`local Base = require "base"` should let the inherits edge target lua:base."""
    _, refs = parsed["inherit.lua"]
    inh = [r for r in refs if r.kind == "inherits"]
    assert len(inh) == 1
    assert inh[0].to_id == "lua:base" or inh[0].to_external == "base"


def test_unity_xlua_csharp_calls(parsed):
    syms, refs = parsed["unity_xlua.lua"]
    cs_calls = [r for r in refs if r.kind == "csharp_call"]
    chains = sorted({r.to_external for r in cs_calls})
    assert "CS.UnityEngine.GameObject.Find" in chains
    assert "CS.UnityEngine.Debug.Log" in chains


def test_unity_xlua_aliased_lua_module_call(parsed):
    """`Base.shared()` after `local Base = require "base"` resolves to lua module."""
    _, refs = parsed["unity_xlua.lua"]
    calls = [r for r in refs if r.kind == "call"]
    base_calls = [r for r in calls if "base" in (r.to_external or "")]
    assert any("base.shared" in (r.to_external or "") for r in base_calls)


# --- direct unit tests ---

def test_module_id_from_relative_path(tmp_path):
    root = tmp_path / "scripts"
    (root / "foo").mkdir(parents=True)
    f = root / "foo" / "bar.lua"
    f.write_text("local M = {}\nreturn M\n")
    parser = LuaParser()
    syms, _ = parser.parse_file(f, root)
    mod = next(s for s in syms if s.kind == "module")
    assert mod.id == "lua:foo.bar"
    assert mod.fqn == "foo.bar"


def test_require_path_normalization_slash(tmp_path):
    f = tmp_path / "main.lua"
    f.write_text('local X = require "a/b/c"\nreturn X\n')
    parser = LuaParser()
    _, refs = parser.parse_file(f, tmp_path)
    req = [r for r in refs if r.kind == "require"]
    assert len(req) == 1
    assert req[0].to_external == "a.b.c"


def test_csharp_prefix_configurable_sLua(tmp_path):
    """sLua / ToLua use bare `UnityEngine.*` without `CS.` prefix."""
    f = tmp_path / "slua_style.lua"
    f.write_text(
        "local GO = UnityEngine.GameObject\n"
        "function start()\n"
        "  GO.Find('x')\n"
        "end\n"
    )
    parser = LuaParser(csharp_call_prefixes=["UnityEngine"])
    _, refs = parser.parse_file(f, tmp_path)
    cs = [r for r in refs if r.kind == "csharp_call"]
    assert len(cs) == 1
    assert cs[0].to_external == "UnityEngine.GameObject.Find"


def test_xlua_default_does_not_match_unityengine_bare():
    """With default CS-only prefixes, `UnityEngine.X` without `CS.` is NOT csharp_call."""
    parser = LuaParser()  # default ["CS"]
    src = "local GO = UnityEngine.GameObject\nGO.Find('x')\n"
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "x.lua"
        f.write_text(src)
        _, refs = parser.parse_file(f, Path(td))
    cs = [r for r in refs if r.kind == "csharp_call"]
    assert cs == []


def test_method_call_via_alias(tmp_path):
    """`Foo:m()` where `local Foo = require "x"` resolves with # form."""
    f = tmp_path / "caller.lua"
    f.write_text(
        'local Foo = require "x"\n'
        "function go()\n"
        "  Foo:do_it()\n"
        "end\n"
    )
    parser = LuaParser()
    _, refs = parser.parse_file(f, tmp_path)
    calls = [r for r in refs if r.kind == "call" and "do_it" in (r.to_external or "")]
    assert len(calls) == 1
    assert "#do_it" in calls[0].to_id


# --- regex fallback ---

def test_regex_fallback_emits_module_and_functions(monkeypatch, tmp_path):
    """Force fallback path by disabling tree-sitter import."""
    f = tmp_path / "x.lua"
    f.write_text(
        "function top()\nend\n"
        "function M.foo()\nend\n"
        "function M:bar()\nend\n"
        'local X = require "y"\n'
    )
    parser = LuaParser()
    # Force regex path
    parser._initialized = True
    parser._parser = None
    syms, refs = parser.parse_file(f, tmp_path)
    names = {(s.kind, s.name) for s in syms}
    assert ("module", "x") in names
    assert ("function", "top") in names
    assert ("function", "foo") in names
    assert ("method", "bar") in names
    req = [r for r in refs if r.kind == "require"]
    assert len(req) == 1
    assert req[0].to_external == "y"
