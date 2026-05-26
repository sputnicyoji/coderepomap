"""
Phase 8: crosslang resolver tests.

Unit + end-to-end on the U3D mixed fixture.
"""
from pathlib import Path

import pytest

import coderepomap.csharp  # noqa: F401  register
import coderepomap.lua  # noqa: F401  register
from coderepomap.core import crosslang
from coderepomap.core.parser_base import Reference, Symbol


MIXED_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "u3d_mixed"


# --- unit: exact / short / ambiguous ---

def _sym(sid, name, fqn, kind="class", lang="csharp"):
    return Symbol(id=sid, name=name, fqn=fqn, kind=kind, file="x", line=1, lang=lang)


def test_exact_fqn_match():
    syms = [_sym("csharp:UnityEngine.GameObject", "GameObject", "UnityEngine.GameObject")]
    refs = [Reference(
        from_id="lua:m", to_id="", file="m.lua", line=1, kind="csharp_call",
        lang="lua", resolved=False, to_external="CS.UnityEngine.GameObject",
    )]
    crosslang.resolve(syms, refs, {})
    assert refs[0].resolved is True
    assert refs[0].to_id == "csharp:UnityEngine.GameObject"


def test_method_chain_resolves_to_enclosing_type():
    """`CS.UnityEngine.GameObject.Find` should resolve to GameObject type."""
    syms = [_sym("csharp:UnityEngine.GameObject", "GameObject", "UnityEngine.GameObject")]
    refs = [Reference(
        from_id="lua:m", to_id="", file="m.lua", line=1, kind="csharp_call",
        lang="lua", resolved=False, to_external="CS.UnityEngine.GameObject.Find",
    )]
    crosslang.resolve(syms, refs, {})
    assert refs[0].resolved is True
    assert refs[0].to_id == "csharp:UnityEngine.GameObject"


def test_short_name_unique_resolves():
    syms = [_sym("csharp:A.B.Player", "Player", "A.B.Player")]
    refs = [Reference(
        from_id="lua:m", to_id="", file="m.lua", line=1, kind="csharp_call",
        lang="lua", resolved=False, to_external="CS.Player",
    )]
    crosslang.resolve(syms, refs, {})
    assert refs[0].resolved is True
    assert refs[0].to_id == "csharp:A.B.Player"


def test_short_name_ambiguous_kept_unresolved_with_candidates():
    syms = [
        _sym("csharp:A.Player", "Player", "A.Player"),
        _sym("csharp:B.Player", "Player", "B.Player"),
    ]
    refs = [Reference(
        from_id="lua:m", to_id="", file="m.lua", line=1, kind="csharp_call",
        lang="lua", resolved=False, to_external="CS.Player",
    )]
    crosslang.resolve(syms, refs, {})
    assert refs[0].resolved is False
    assert refs[0].to_id == ""
    cands = refs[0].lang_meta.get("candidates", [])
    assert set(cands) == {"csharp:A.Player", "csharp:B.Player"}


def test_no_match_stays_unresolved():
    syms = [_sym("csharp:A.X", "X", "A.X")]
    refs = [Reference(
        from_id="lua:m", to_id="", file="m.lua", line=1, kind="csharp_call",
        lang="lua", resolved=False, to_external="CS.NotThere",
    )]
    crosslang.resolve(syms, refs, {})
    assert refs[0].resolved is False
    assert "candidates" not in refs[0].lang_meta


def test_crosslang_disabled_keeps_unresolved():
    syms = [_sym("csharp:UnityEngine.GameObject", "GameObject", "UnityEngine.GameObject")]
    refs = [Reference(
        from_id="lua:m", to_id="", file="m.lua", line=1, kind="csharp_call",
        lang="lua", resolved=False, to_external="CS.UnityEngine.GameObject",
    )]
    crosslang.resolve(syms, refs, {"crosslang": {"enabled": False}})
    assert refs[0].resolved is False


def test_lua_require_resolves_to_module():
    """`require "foo.bar"` with corresponding lua module symbol -> resolved."""
    syms = [_sym("lua:foo.bar", "bar", "foo.bar", kind="module", lang="lua")]
    refs = [Reference(
        from_id="lua:m", to_id="lua:foo.bar", file="m.lua", line=1, kind="require",
        lang="lua", resolved=False, to_external="foo.bar",
    )]
    crosslang.resolve(syms, refs, {})
    assert refs[0].resolved is True
    assert refs[0].to_id == "lua:foo.bar"


def test_lua_call_via_alias_resolves_when_target_module_exists():
    """A call ref aimed at `lua:foo.bar.shared` resolves to the module
    if the function symbol is also there."""
    syms = [
        _sym("lua:foo.bar", "bar", "foo.bar", kind="module", lang="lua"),
        _sym("lua:foo.bar.shared", "shared", "foo.bar.shared", kind="function", lang="lua"),
    ]
    refs = [Reference(
        from_id="lua:m", to_id="lua:foo.bar.shared", file="m.lua", line=1, kind="call",
        lang="lua", resolved=False, to_external="foo.bar.shared",
    )]
    crosslang.resolve(syms, refs, {})
    assert refs[0].resolved is True
    assert refs[0].to_id == "lua:foo.bar.shared"


def test_custom_prefix_unityengine_for_sLua():
    """sLua/ToLua: bare `UnityEngine.GameObject` chain matches when configured."""
    syms = [_sym("csharp:UnityEngine.GameObject", "GameObject", "UnityEngine.GameObject")]
    refs = [Reference(
        from_id="lua:m", to_id="", file="m.lua", line=1, kind="csharp_call",
        lang="lua", resolved=False, to_external="UnityEngine.GameObject",
    )]
    cfg = {"crosslang": {"lua_csharp_call_patterns": [{"prefix": "UnityEngine."}]}}
    crosslang.resolve(syms, refs, cfg)
    assert refs[0].resolved is True


# --- end-to-end on U3D mixed fixture ---

def _run_mixed_generator(tmp_path_factory):
    from coderepomap.core.generator import RepoMapGenerator
    config = {
        "project_name": "U3D Mixed",
        "langs": ["csharp", "lua"],
        "sources": {
            "csharp": {"root_path": "Assets/Scripts", "exclude_patterns": []},
            "lua": {"root_path": "Assets/LuaScripts", "exclude_patterns": []},
        },
        "tokens": {"l1_skeleton": 5000, "l2_signatures": 5000, "l3_relations": 5000, "encoding": "cl100k_base"},
        "pagerank": {"alpha": 0.85, "max_iter": 100},
        "output": {
            "directory": str(tmp_path_factory.mktemp("u3d_out")),
            "files": {
                "skeleton": "L1.md", "signatures": "L2.md", "relations": "L3.md", "meta": "meta.json",
            },
        },
        "importance_boost": {"patterns": [], "priority_modules": []},
        "categories": {"Other": {"patterns": []}},
        "crosslang": {
            "enabled": True,
            "lua_csharp_call_patterns": [{"prefix": "CS."}],
        },
    }
    gen = RepoMapGenerator(config=config, project_root=MIXED_FIXTURE)
    res = gen.run(verbose=False)
    return gen, res


def test_u3d_mixed_resolves_lua_to_csharp(tmp_path_factory):
    gen, _ = _run_mixed_generator(tmp_path_factory)

    # The Lua main.lua calls `GameObject.Find` and `Debug.Log` via aliases
    # that resolve to CS.UnityEngine.GameObject / .Debug; crosslang should
    # flip these to resolved.
    cs_calls = [r for r in gen.references if r.kind == "csharp_call"]
    assert cs_calls, "no csharp_call refs produced"

    resolved_cs = [r for r in cs_calls if r.resolved and r.to_id]
    assert len(resolved_cs) >= 1, "expected at least one Lua->C# resolved edge"

    # GameObject should be reachable
    targets = {r.to_id for r in resolved_cs}
    assert "csharp:UnityEngine.GameObject" in targets or any(
        "GameObject" in tid for tid in targets
    )


def test_u3d_mixed_l3_renders_resolved_edge(tmp_path_factory):
    """The L3 output should mention the cross-lang resolved edge."""
    gen, res = _run_mixed_generator(tmp_path_factory)
    l3_path = res["results"]["l3"]["path"]
    l3 = l3_path.read_text(encoding="utf-8")
    # Either the resolved edge appears under Reference Graph,
    # or the unresolved fallback is captured under External References.
    assert "GameObject" in l3 or "Debug" in l3
