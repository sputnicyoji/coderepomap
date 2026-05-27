"""
Regression tests for the 10 follow-up findings fixed after the post-promise
review iteration.

Each test name encodes the original finding number.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

import coderepomap.csharp  # noqa: F401
import coderepomap.lua  # noqa: F401
from coderepomap.core import crosslang, registry
from coderepomap.core.generator import RepoMapGenerator
from coderepomap.core.graph_builder import _boost_for_symbol  # noqa: F401
from coderepomap.core.parser_base import LanguageParser, Reference, Symbol
from coderepomap.core.ranker import PageRankRanker
from coderepomap.lua.parser import LuaParser


# ---- #3 label collision: same-name classes in different namespaces ----

def test_finding_3_l1_same_name_classes_dont_merge_methods(tmp_path):
    """Two `Player` classes in different namespaces must keep their methods
    attached to the correct namespace (not merged under one entry)."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "GamePlayer.cs").write_text(
        "namespace Game { public class Player { public void Run() {} } }\n",
        encoding="utf-8",
    )
    (src / "UIPlayer.cs").write_text(
        "namespace UI { public class Player { public void Show() {} } }\n",
        encoding="utf-8",
    )
    config = {
        "project_name": "X", "lang": "csharp",
        "source": {"root_path": "src", "exclude_patterns": []},
        "tokens": {"l1_skeleton": 5000, "l2_signatures": 5000, "l3_relations": 5000, "encoding": "cl100k_base"},
        "pagerank": {"alpha": 0.85, "max_iter": 100},
        "output": {
            "directory": str(tmp_path / "out"),
            "files": {"skeleton": "L1.md", "signatures": "L2.md", "relations": "L3.md", "meta": "meta.json"},
        },
        "importance_boost": {"patterns": [], "priority_modules": []},
        "categories": {"Other": {"patterns": []}},
    }
    gen = RepoMapGenerator(config=config, project_root=tmp_path)
    res = gen.run(verbose=False)
    assert res["success"]

    l1 = (Path(config["output"]["directory"]) / "L1.md").read_text(encoding="utf-8")
    # Each Player should appear once with its OWN methods, not merged.
    # GamePlayer.cs row should mention Run; UIPlayer.cs row should mention Show.
    # The crucial property: Run does NOT appear next to UI / Show does NOT appear next to Game.
    lines = [l for l in l1.splitlines() if "Player" in l and "|" in l]
    assert len(lines) >= 1
    # Check no row mixes both Run and Show
    for line in lines:
        assert not ("Run" in line and "Show" in line), (
            f"L1 row merged methods from different-namespace Player classes: {line}"
        )


# ---- #7 phantom from_id no longer creates ghost graph nodes ----

def test_finding_7_unresolved_from_symbol_drops_reference(tmp_path):
    """When the regex fallback / partial parse produces a from_symbol that
    isn't in the file-local index, the reference must be dropped, not
    synthesized into a bogus id."""
    from coderepomap.csharp.parser import (
        LegacyReference,
        _project_references,
    )
    # Simulate a regex-fallback case: from_symbol that has no entry in idx.
    legacy_refs = [
        LegacyReference(
            from_file="x.cs",
            from_symbol="UnknownClass",
            to_symbol="Base",
            ref_type="inherits",
        )
    ]
    idx = {"Base": ["csharp:Some.Base"]}  # only Base is known
    refs = _project_references(legacy_refs, idx)
    # The ref must be dropped — NOT a ghost `csharp:UnknownClass`.
    assert refs == [], (
        f"Unresolvable from_symbol must NOT synthesize a bogus id. Got: {refs}"
    )


# ---- #8 user-supplied file_extensions are honored ----

def test_finding_8_user_file_extensions_merged(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    # Add a .cs and a .csx (extension that the user wants to include).
    (src / "Main.cs").write_text(
        "namespace App { public class Main { public void Go() {} } }\n",
        encoding="utf-8",
    )
    (src / "Script.csx").write_text(
        "namespace App { public class Script { public void Run() {} } }\n",
        encoding="utf-8",
    )
    config = {
        "project_name": "X", "lang": "csharp",
        "source": {
            "root_path": "src",
            "exclude_patterns": [],
            "file_extensions": [".csx"],  # extra ext on top of parser default .cs
        },
        "tokens": {"l1_skeleton": 5000, "l2_signatures": 5000, "l3_relations": 5000, "encoding": "cl100k_base"},
        "pagerank": {"alpha": 0.85, "max_iter": 100},
        "output": {
            "directory": str(tmp_path / "out"),
            "files": {"skeleton": "L1.md", "signatures": "L2.md", "relations": "L3.md", "meta": "meta.json"},
        },
        "importance_boost": {"patterns": [], "priority_modules": []},
        "categories": {"Other": {"patterns": []}},
    }
    gen = RepoMapGenerator(config=config, project_root=tmp_path)
    files = gen.scan()
    paths = [str(sf.path) for sf in files]
    assert any(p.endswith("Main.cs") for p in paths)
    assert any(p.endswith("Script.csx") for p in paths), (
        f"User-supplied source.file_extensions:['.csx'] was ignored. Found: {paths}"
    )


# ---- #9 registry catches generic ImportError too ----

def test_finding_9_registry_catches_import_error():
    """A transitive ImportError (not just ModuleNotFoundError) during the
    subpackage import must surface as a friendly ValueError."""

    def fake_import(name):
        # Simulate tree-sitter ABI mismatch: ImportError (NOT ModuleNotFoundError)
        raise ImportError("/path/to/tree_sitter_x.so: undefined symbol abc")

    with patch.object(registry, "import_parser_module", side_effect=lambda lang: fake_import(lang)):
        snapshot = dict(registry.PARSERS)
        try:
            registry.PARSERS.clear()
            with pytest.raises(ValueError) as exc:
                registry.get_parser("phantomlang")
            assert "phantomlang" in str(exc.value)
        finally:
            registry.PARSERS.clear()
            registry.PARSERS.update(snapshot)


# ---- #10 crosslang Path 2: call kind resolves to module ----

def test_finding_10_crosslang_path2_resolves_call_to_module():
    """A Lua call ref like `lua:foo.bar.unknown_fn` where only `lua:foo.bar`
    module exists must be resolved to the module (resolved=True, to_id=module)."""
    syms = [
        Symbol(id="lua:foo.bar", name="bar", fqn="foo.bar", kind="module",
               file="foo/bar.lua", line=1, lang="lua"),
    ]
    refs = [
        Reference(
            from_id="lua:caller", to_id="lua:foo.bar.unknown_fn",
            file="caller.lua", line=10, kind="call", lang="lua",
            resolved=False, to_external="foo.bar.unknown_fn",
        ),
    ]
    crosslang.resolve(syms, refs, {})
    assert refs[0].resolved is True
    assert refs[0].to_id == "lua:foo.bar"


# ---- #11 Lua multi-return assignment still emits all locals ----

def test_finding_11_lua_multi_return_registers_all_locals(tmp_path):
    """`local A, B = factory()` must register both A and B as local_table
    candidates (so future `A.x()` / `B.y()` calls can resolve module-locally)."""
    f = tmp_path / "x.lua"
    f.write_text(
        "local A, B = factory()\n"
        "function go()\n"
        "  A.foo()\n"
        "  B.bar()\n"
        "end\n",
        encoding="utf-8",
    )
    p = LuaParser()
    syms, refs = p.parse_file(f, tmp_path)
    # Both A and B should produce `call` references with module-local target ids
    # (lua:x.A.foo and lua:x.B.bar), not external unresolved.
    call_refs = [r for r in refs if r.kind == "call"]
    targets = sorted(r.to_id for r in call_refs)
    assert "lua:x.A.foo" in targets, f"A alias was dropped: {targets}"
    assert "lua:x.B.bar" in targets, f"B alias (multi-return tail) was dropped: {targets}"


# ---- #12 Lua tree-sitter parse failure falls back to regex ----

def test_finding_12_lua_ts_parse_failure_falls_back_to_regex(tmp_path):
    """tree-sitter Parser is a C extension object whose attrs are read-only,
    so we can't patch .parse. Instead swap the entire _parser with a stub
    whose .parse raises, then verify the fallback regex path produces output."""
    f = tmp_path / "x.lua"
    f.write_text("function top() end\nlocal X = require 'y'\n", encoding="utf-8")

    class RaisingParser:
        def parse(self, *args, **kwargs):
            raise RuntimeError("simulated TS crash")

    p = LuaParser()
    p._init_parser()  # force initialization
    p._parser = RaisingParser()  # swap in the raising stub
    p._initialized = True  # don't let _init_parser overwrite our stub

    syms, refs = p.parse_file(f, tmp_path)
    assert any(s.kind == "module" for s in syms), "regex fallback didn't emit module"
    assert any(s.kind == "function" and s.name == "top" for s in syms), \
        "regex fallback didn't emit top-level function"
    assert any(r.kind == "require" for r in refs), \
        "regex fallback didn't emit require reference"


# ---- #13 cmd_init warns when template is missing ----

def test_finding_13_cmd_init_warns_on_missing_template(tmp_path, monkeypatch, capsys):
    """When the preset template file doesn't exist, cmd_init must print a
    warning to stderr — silent fallthrough is the bug."""
    monkeypatch.chdir(tmp_path)
    # Force template resolution to return a non-existent path.
    import coderepomap.core.cli as cli_mod

    fake_template = tmp_path / "no_such_template.yaml"  # doesn't exist
    monkeypatch.setattr(cli_mod, "_resolve_init_template", lambda lang, preset: fake_template)

    args = type("A", (), {"force": True, "lang": "csharp", "preset": "unity"})()
    rc = cli_mod.cmd_init(args)
    assert rc == 0
    err = capsys.readouterr().err
    assert "Warning" in err or "template" in err.lower(), (
        f"cmd_init must warn when template missing; got stderr: {err!r}"
    )


# ---- #14 Windows relative_to: ValueError handled, file still parsed ----

def test_finding_14_relative_to_failure_does_not_crash_csharp_parser(tmp_path):
    """If a .cs file is somehow outside the declared base, parse_file must
    fall back to absolute path string and still produce symbols."""
    from coderepomap.csharp.parser import CSharpParser
    p = CSharpParser()
    f = tmp_path / "outsider.cs"
    f.write_text("namespace App { public class Outsider {} }\n", encoding="utf-8")
    # Use a base that's NOT a parent of f, triggering ValueError internally.
    unrelated_base = tmp_path.parent / "elsewhere"
    unrelated_base.mkdir(exist_ok=True)
    syms, refs = p.parse_file(f, unrelated_base)
    # Must NOT raise; symbols may still be produced.
    assert isinstance(syms, list)


def test_finding_14_relative_to_failure_does_not_crash_lua_parser(tmp_path):
    p = LuaParser()
    f = tmp_path / "outsider.lua"
    f.write_text("local M = {}\nreturn M\n", encoding="utf-8")
    unrelated_base = tmp_path.parent / "elsewhere2"
    unrelated_base.mkdir(exist_ok=True)
    syms, refs = p.parse_file(f, unrelated_base)
    assert isinstance(syms, list)


# ---- #15 get_ranked_symbols(limit=0) returns empty list ----

def test_finding_15_limit_zero_returns_empty():
    r = PageRankRanker()
    r.add_symbol("csharp:A", file="a.cs", kind="class", label="A")
    r.add_symbol("csharp:B", file="b.cs", kind="class", label="B")
    out = r.get_ranked_symbols(limit=0)
    assert out == [], f"limit=0 must return empty list, got {len(out)} entries"


def test_finding_15_limit_none_returns_all():
    """limit=None must keep returning all (backward compatibility)."""
    r = PageRankRanker()
    r.add_symbol("csharp:A", file="a.cs", kind="class", label="A")
    r.add_symbol("csharp:B", file="b.cs", kind="class", label="B")
    out = r.get_ranked_symbols(limit=None)
    assert len(out) == 2


def test_build_module_stats_csharp_default_remains_class_only():
    """Byte-compat: a CSharp-only Symbol list still produces class_count, no symbol_count."""
    from coderepomap.core.parser_base import Symbol
    from coderepomap.core.renderer import build_module_stats

    syms = [
        Symbol(id="csharp:A.X", name="X", fqn="A.X", kind="class",
               file="A/X.cs", line=1, lang="csharp"),
        Symbol(id="csharp:A.X.M()", name="M", fqn="A.X.M", kind="method",
               file="A/X.cs", line=2, lang="csharp"),
    ]
    stats = build_module_stats(syms)
    assert stats["A"]["class_count"] == 1
    assert "X" in stats["A"]["classes"]


def test_build_module_stats_go_counts_packages_interfaces_functions():
    """With Go symbols present, module_stats must expose a symbol_count covering
    the language's declared graph_node_kinds, and entries list must include
    non-class entry symbols so the renderer can show them."""
    from coderepomap.core.parser_base import Symbol
    from coderepomap.core.renderer import build_module_stats

    syms = [
        Symbol(id="go:m/pkg/service", name="service", fqn="m/pkg/service",
               kind="package", file="pkg/service/service.go", line=1, lang="go"),
        Symbol(id="go:m/pkg/service.Service", name="Service",
               fqn="m/pkg/service.Service",
               kind="class", file="pkg/service/service.go", line=5, lang="go"),
        Symbol(id="go:m/pkg/service.Runner", name="Runner",
               fqn="m/pkg/service.Runner",
               kind="interface", file="pkg/service/service.go", line=3, lang="go"),
        Symbol(id="go:m/pkg/service.NewService", name="NewService",
               fqn="m/pkg/service.NewService",
               kind="function", file="pkg/service/service.go", line=10, lang="go"),
    ]
    stats = build_module_stats(syms)
    info = stats["pkg"]
    assert info["class_count"] == 1
    assert info["symbol_count"] == 4
    assert {"Service", "Runner", "NewService"} <= set(info["entries"])


def test_render_l1_csharp_only_uses_class_wording():
    """Byte-compat: CSharp-only run uses '({n} classes)' literal in L1."""
    from coderepomap.core.parser_base import Symbol
    from coderepomap.core.renderer import build_module_stats, render_l1
    from coderepomap.core.ranker import PageRankRanker

    syms = [
        Symbol(id="csharp:A.X", name="X", fqn="A.X", kind="class",
               file="A/X.cs", line=1, lang="csharp"),
    ]
    r = PageRankRanker()
    r.add_symbol("csharp:A.X", file="A/X.cs", kind="class",
                 label="X", fqn="A.X", lang="csharp")
    out = render_l1(
        syms, r, build_module_stats(syms),
        {"tokens": {"l1_skeleton": 4000, "encoding": "cl100k_base"},
         "categories": {"Other": {"patterns": []}}},
        project_name="P", git_commit="", today_yyyy_mm_dd="2026-05-26",
    )
    assert "classes" in out
    assert "entry symbols" not in out


def test_render_l1_go_uses_entry_symbol_wording():
    """When non-class kinds dominate a module, wording switches to entry symbols."""
    from coderepomap.core.parser_base import Symbol
    from coderepomap.core.renderer import build_module_stats, render_l1
    from coderepomap.core.ranker import PageRankRanker

    syms = [
        Symbol(id="go:m/pkg/service", name="service", fqn="m/pkg/service",
               kind="package", file="pkg/service/service.go", line=1, lang="go"),
        Symbol(id="go:m/pkg/service.Service", name="Service",
               fqn="m/pkg/service.Service", kind="class",
               file="pkg/service/service.go", line=5, lang="go"),
        Symbol(id="go:m/pkg/service.Runner", name="Runner",
               fqn="m/pkg/service.Runner", kind="interface",
               file="pkg/service/service.go", line=3, lang="go"),
        Symbol(id="go:m/pkg/service.NewService", name="NewService",
               fqn="m/pkg/service.NewService", kind="function",
               file="pkg/service/service.go", line=10, lang="go"),
    ]
    r = PageRankRanker()
    for s in syms:
        r.add_symbol(s.id, file=s.file, kind=s.kind, label=s.name,
                     fqn=s.fqn, lang=s.lang)
    out = render_l1(
        syms, r, build_module_stats(syms),
        {"tokens": {"l1_skeleton": 4000, "encoding": "cl100k_base"},
         "categories": {"Other": {"patterns": []}}},
        project_name="P", git_commit="", today_yyyy_mm_dd="2026-05-26",
    )
    assert "entry symbols" in out


def test_render_l2_go_drops_class_only_filter_when_widened():
    """L2 currently skips any non-class entry. When widened (Go's `package`
    sentinel present in the module), packages/interfaces/functions with a
    signature must also be rendered."""
    from coderepomap.core.parser_base import Symbol
    from coderepomap.core.renderer import render_l2
    from coderepomap.core.ranker import PageRankRanker

    syms = [
        # Go `package` is the structural sentinel that flips widened mode.
        Symbol(id="go:m/pkg/service", name="service",
               fqn="m/pkg/service", kind="package",
               signature="package service",
               file="pkg/service/service.go", line=1, lang="go"),
        Symbol(id="go:m/pkg/service.Runner", name="Runner",
               fqn="m/pkg/service.Runner", kind="interface",
               signature="interface Runner",
               file="pkg/service/service.go", line=3, lang="go"),
    ]
    r = PageRankRanker()
    for s in syms:
        r.add_symbol(s.id, file=s.file, kind=s.kind, label=s.name,
                     fqn=s.fqn, lang=s.lang)
    out = render_l2(
        syms, r,
        {"tokens": {"l2_signatures": 4000, "encoding": "cl100k_base"}},
        project_name="P",
    )
    assert "interface Runner" in out


def test_fix_c12_build_module_stats_skips_non_entry_kind_files():
    """C12: a C# file containing only an enum (no class) must not create a module entry."""
    from coderepomap.core.parser_base import Symbol
    from coderepomap.core.renderer import build_module_stats

    syms = [
        Symbol(id="csharp:Enums.Status", name="Status", fqn="Enums.Status",
               kind="enum", file="Enums/Status.cs", line=1, lang="csharp"),
    ]
    stats = build_module_stats(syms)
    assert "Enums" not in stats


def test_fix_c3_widened_wording_is_per_module():
    """C3: in multi-lang csharp+go, C# modules keep `(N classes)` wording while Go modules use `(N entry symbols)`."""
    from coderepomap.core.parser_base import Symbol
    from coderepomap.core.renderer import build_module_stats, render_l1
    from coderepomap.core.ranker import PageRankRanker

    syms = [
        Symbol(id="csharp:CsMod.Foo", name="Foo", fqn="CsMod.Foo", kind="class",
               file="CsMod/Foo.cs", line=1, lang="csharp"),
        Symbol(id="go:m/pkg/svc", name="svc", fqn="m/pkg/svc", kind="package",
               file="pkg/svc/svc.go", line=1, lang="go"),
        Symbol(id="go:m/pkg/svc.Foo", name="Foo", fqn="m/pkg/svc.Foo", kind="class",
               file="pkg/svc/svc.go", line=3, lang="go"),
    ]
    r = PageRankRanker()
    for s in syms:
        r.add_symbol(s.id, file=s.file, kind=s.kind, label=s.name, fqn=s.fqn, lang=s.lang)
    out = render_l1(
        syms, r, build_module_stats(syms),
        {"tokens": {"l1_skeleton": 4000, "encoding": "cl100k_base"},
         "categories": {"Other": {"patterns": []}}},
        project_name="P", git_commit="", today_yyyy_mm_dd="2026-05-26",
    )
    # C# module keeps class wording
    assert "- CsMod/ (1 classes)" in out
    # Go module gets entry-symbols wording
    assert "(2 entry symbols)" in out or "(2 entry symbol" in out


def test_fix_c8_l1_column_header_switches_with_widened():
    """C8: when widened (any package present), the L1 table column header must say 'Entry Symbol' not 'Entry Class'."""
    from coderepomap.core.parser_base import Symbol
    from coderepomap.core.renderer import build_module_stats, render_l1
    from coderepomap.core.ranker import PageRankRanker

    syms = [
        Symbol(id="go:m/pkg", name="pkg", fqn="m/pkg", kind="package",
               file="pkg/svc.go", line=1, lang="go"),
        Symbol(id="go:m/pkg.Svc", name="Svc", fqn="m/pkg.Svc", kind="class",
               file="pkg/svc.go", line=3, lang="go"),
    ]
    r = PageRankRanker()
    for s in syms:
        r.add_symbol(s.id, file=s.file, kind=s.kind, label=s.name, fqn=s.fqn, lang=s.lang)
    out = render_l1(
        syms, r, build_module_stats(syms),
        {"tokens": {"l1_skeleton": 4000, "encoding": "cl100k_base"},
         "categories": {"Other": {"patterns": []}}},
        project_name="P", git_commit="", today_yyyy_mm_dd="2026-05-26",
    )
    assert "Entry Symbol" in out
    assert "Entry Class |" not in out


def test_fix_c9_l2_module_header_widened_wording():
    """C9: L2 module header must say `(N entry symbols)` when module is widened
    (Go's `package` sentinel present)."""
    from coderepomap.core.parser_base import Symbol
    from coderepomap.core.renderer import render_l2
    from coderepomap.core.ranker import PageRankRanker

    syms = [
        Symbol(id="go:pkg", name="pkg", fqn="pkg",
               kind="package", signature="package pkg",
               file="pkg/x.go", line=1, lang="go"),
        Symbol(id="go:pkg.Runner", name="Runner", fqn="pkg.Runner",
               kind="interface", signature="type Runner interface",
               file="pkg/x.go", line=3, lang="go"),
    ]
    r = PageRankRanker()
    for s in syms:
        r.add_symbol(s.id, file=s.file, kind=s.kind, label=s.name,
                     fqn=s.fqn, lang=s.lang)
    out = render_l2(
        syms, r,
        {"tokens": {"l2_signatures": 4000, "encoding": "cl100k_base"}},
        project_name="P",
    )
    assert "entry symbols" in out or "entry symbol" in out
    assert "classes" not in out.split("##")[1]  # module header line should not say classes


def test_fix_c2_render_l2_csharp_only_keeps_class_filter():
    """C2: when no package symbol present (pure C# or C#-with-interfaces), L2 must drop non-class entries to preserve v0.1.0 baseline."""
    from coderepomap.core.parser_base import Symbol
    from coderepomap.core.renderer import render_l2
    from coderepomap.core.ranker import PageRankRanker

    syms = [
        Symbol(id="csharp:A.Foo", name="Foo", fqn="A.Foo", kind="class",
               signature="public class Foo", file="A/Foo.cs", line=1, lang="csharp"),
        Symbol(id="csharp:A.IFoo", name="IFoo", fqn="A.IFoo", kind="interface",
               signature="public interface IFoo", file="A/Foo.cs", line=10, lang="csharp"),
    ]
    r = PageRankRanker()
    for s in syms:
        r.add_symbol(s.id, file=s.file, kind=s.kind, label=s.name, fqn=s.fqn, lang=s.lang)
    out = render_l2(
        syms, r,
        {"tokens": {"l2_signatures": 4000, "encoding": "cl100k_base"}},
        project_name="P",
    )
    # v0.1.0 byte-compat: C# interfaces should NOT appear in L2 when no Go package context
    assert "public interface IFoo" not in out
    assert "public class Foo" in out


def test_fix_c4_meta_top_modules_uses_entries_count():
    """C4: render_meta.top_modules must use symbol_count / entries (not class_count) when any module is widened."""
    from coderepomap.core.parser_base import Symbol
    from coderepomap.core.renderer import build_module_stats, render_meta
    from coderepomap.core.ranker import PageRankRanker

    syms = [
        Symbol(id="go:m/handler", name="handler", fqn="m/handler",
               kind="package", file="handler/h.go", line=1, lang="go"),
        Symbol(id="go:m/handler.New", name="New", fqn="m/handler.New",
               kind="function", file="handler/h.go", line=3, lang="go"),
        Symbol(id="go:m/handler.Run", name="Run", fqn="m/handler.Run",
               kind="function", file="handler/h.go", line=5, lang="go"),
    ]
    r = PageRankRanker()
    for s in syms:
        r.add_symbol(s.id, file=s.file, kind=s.kind, label=s.name, fqn=s.fqn, lang=s.lang)
    meta = render_meta(syms, [], r, build_module_stats(syms),
                       project_name="P", source_path=".", git_commit="",
                       git_branch="", generated_at_iso="")
    top = meta["top_modules"]
    assert any(t["name"] == "handler" for t in top)
    # When widened, the per-row count must reflect entry symbols (3 here), not class_count (0)
    handler_row = next(t for t in top if t["name"] == "handler")
    assert handler_row.get("entries", handler_row.get("classes")) == 3


def test_fix_token_budget_l1_fills_up_to_cap_for_large_project():
    """L1 entries hard-capped at 20 produces tiny output for large Go projects.
    The renderer should expand its candidate pool when the budget allows."""
    from coderepomap.core.parser_base import Symbol
    from coderepomap.core.renderer import build_module_stats, render_l1
    from coderepomap.core.ranker import PageRankRanker

    syms = [
        Symbol(id="go:m/pkg", name="pkg", fqn="m/pkg", kind="package",
               file="pkg/x.go", line=1, lang="go"),
    ]
    # 100 top-level functions in the same package so the ranker has lots of
    # candidates to potentially show in L1.
    for i in range(100):
        syms.append(Symbol(
            id=f"go:m/pkg.Fn{i}", name=f"Fn{i}", fqn=f"m/pkg.Fn{i}",
            kind="function", file="pkg/x.go", line=10 + i, lang="go",
        ))
    r = PageRankRanker()
    for s in syms:
        r.add_symbol(s.id, file=s.file, kind=s.kind, label=s.name,
                     fqn=s.fqn, lang=s.lang)

    out = render_l1(
        syms, r, build_module_stats(syms),
        {"tokens": {"l1_skeleton": 1500, "encoding": "cl100k_base"},
         "categories": {"Other": {"patterns": []}}},
        project_name="P", git_commit="", today_yyyy_mm_dd="2026-05-27",
    )
    # Count rows in the Core Entry Symbols table. Old code hard-capped at 20.
    # New behavior: fill toward the token cap.
    table_rows = [ln for ln in out.split("\n") if ln.startswith("| ") and "Fn" in ln]
    assert len(table_rows) > 20, \
        f"L1 still hard-capped at 20 even with 100 candidates and 1500-token budget; got {len(table_rows)} rows"


def test_fix_token_budget_l2_fills_up_to_cap_for_large_project():
    """L2 currently shows max 15 modules × 5 entries; for large projects the
    budget often goes mostly unused. Expand the candidate pool, let the
    trim loop enforce the cap."""
    from coderepomap.core.parser_base import Symbol
    from coderepomap.core.renderer import render_l2
    from coderepomap.core.ranker import PageRankRanker

    # 30 modules each with one struct + signature
    syms = []
    for i in range(30):
        syms.append(Symbol(
            id=f"go:m/mod{i}", name=f"mod{i}", fqn=f"m/mod{i}",
            kind="package", file=f"mod{i}/x.go", line=1,
            signature=f"package mod{i}", lang="go",
        ))
        syms.append(Symbol(
            id=f"go:m/mod{i}.S{i}", name=f"S{i}", fqn=f"m/mod{i}.S{i}",
            kind="class", file=f"mod{i}/x.go", line=2,
            signature=f"type S{i} struct", lang="go",
        ))
    r = PageRankRanker()
    for s in syms:
        r.add_symbol(s.id, file=s.file, kind=s.kind, label=s.name,
                     fqn=s.fqn, lang=s.lang)
    out = render_l2(
        syms, r,
        {"tokens": {"l2_signatures": 2000, "encoding": "cl100k_base"}},
        project_name="P",
    )
    module_headers = [ln for ln in out.split("\n") if ln.startswith("## ")]
    assert len(module_headers) > 15, \
        f"L2 still hard-capped at 15 modules even with 30 candidates and 2000-token budget; got {len(module_headers)} module sections"
