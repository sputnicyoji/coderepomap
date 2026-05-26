"""
Regression tests for the critical/high-severity findings from the post-mission
code review.

Each test name encodes the finding number so the audit trail is traceable.
"""
import tempfile
from pathlib import Path

import pytest
import yaml

import coderepomap.csharp  # noqa: F401 — register parser
import coderepomap.lua  # noqa: F401 — register parser
from coderepomap import CSharpParser as PublicCSharpParser
from coderepomap.core.generator import RepoMapGenerator
from coderepomap.core.parser_base import LanguageParser, Reference, Symbol


# ---- #1 load_config + discover multi-lang ----

def test_finding_1_load_config_multi_lang_user_config_works(tmp_path):
    """User writes config.yaml with only `langs:` — load_config + generator
    must NOT raise 'config has both lang and langs'.
    """
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "project_name: Mixed\n"
        "langs: [csharp, lua]\n"
        "sources:\n"
        "  csharp:\n"
        "    root_path: cs\n"
        "  lua:\n"
        "    root_path: lua\n",
        encoding="utf-8",
    )
    cfg = RepoMapGenerator.load_config(cfg_path)
    assert "langs" in cfg
    assert "lang" not in cfg, (
        "load_config must drop the default `lang` when user sets `langs` to "
        "avoid the discover() exclusivity check raising."
    )


def test_finding_1_legacy_v01_config_still_falls_back_to_csharp(tmp_path):
    """A v0.1.0 config without `lang` or `langs` must still work as
    single-lang csharp via discover()'s built-in fallback.
    """
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "project_name: Legacy\n"
        "source:\n  root_path: '.'\n",
        encoding="utf-8",
    )
    cfg = RepoMapGenerator.load_config(cfg_path)
    # No explicit lang — discover() will inject csharp; load_config itself
    # doesn't need to add it now that _get_default_config doesn't.
    assert cfg.get("lang") is None or cfg.get("lang") == "csharp"


# ---- #4 public CSharpParser is the LanguageParser subclass ----

def test_finding_4_public_csharp_parser_is_new_contract():
    """`from coderepomap import CSharpParser` MUST return the new
    LanguageParser-compliant class, not LegacyCSharpParser.
    """
    assert issubclass(PublicCSharpParser, LanguageParser)
    # Must produce Symbols with `.id` (new contract)
    fix = Path(__file__).resolve().parent / "fixtures" / "csharp" / "basic"
    parser = PublicCSharpParser()
    syms, refs = parser.parse_file(fix / "Namespace.cs", fix)
    for s in syms:
        assert hasattr(s, "id"), "Public CSharpParser must produce new Symbol with .id"
        assert s.id.startswith("csharp:"), f"Symbol.id must use csharp: prefix, got {s.id!r}"


# ---- #2 cross-file C# inheritance resolves via crosslang ----

def test_finding_2_cross_file_inheritance_resolves(tmp_path):
    """Child.cs inherits Base.cs — the `inherits` edge must become a
    resolved Reference (to_id non-empty) after crosslang.resolve runs.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "Base.cs").write_text(
        "namespace App {\n"
        "  public class BasePlayer { public void Init() {} }\n"
        "}\n",
        encoding="utf-8",
    )
    (src / "Child.cs").write_text(
        "namespace App {\n"
        "  public class ChildPlayer : BasePlayer { public void Run() {} }\n"
        "}\n",
        encoding="utf-8",
    )
    config = {
        "project_name": "X",
        "lang": "csharp",
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
    files = gen.scan()
    gen.parse(files)
    gen.build()

    # Find the ChildPlayer -> BasePlayer inheritance edge
    inh = [r for r in gen.references if r.kind == "inherits"]
    assert len(inh) >= 1
    # Must be resolved (target_id matches Base symbol id)
    resolved = [r for r in inh if r.resolved and r.to_id]
    assert resolved, (
        f"Cross-file inheritance must resolve via crosslang. Got: "
        f"{[(r.from_id, r.to_id, r.to_external, r.resolved) for r in inh]}"
    )
    assert any("BasePlayer" in r.to_id for r in resolved)


# ---- #5 boost uses max, prefix requires uppercase after ----

def test_finding_5_boost_uses_max_not_multiply():
    from coderepomap.core.graph_builder import _boost_for_symbol
    boost = _boost_for_symbol(
        "SManager",
        [{"prefix": "S", "boost": 2.0}, {"suffix": "Manager", "boost": 1.5}],
    )
    assert boost == 2.0, "max boost (2.0) expected, not product (3.0)"


def test_finding_5_boost_prefix_uppercase_guard():
    from coderepomap.core.graph_builder import _boost_for_symbol
    # `Setup` starts with `S` but next char `e` is lowercase — must NOT boost.
    assert _boost_for_symbol("Setup", [{"prefix": "S", "boost": 2.0}]) == 1.0
    # `SManager` next char `M` uppercase — boost applies.
    assert _boost_for_symbol("SManager", [{"prefix": "S", "boost": 2.0}]) == 2.0


# ---- #6 default boost patterns fallback ----

def test_finding_6_default_patterns_fall_back_to_parser_class_attr(tmp_path):
    """When user config has empty importance_boost.patterns, the generator
    must fall back to CSharpParser.default_boost_patterns so Manager/Service
    classes get the v0.1.0-equivalent boost.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "GM.cs").write_text(
        "namespace App {\n"
        "  public class GameManager { public void Init() {} }\n"
        "  public class Helper { public void Util() {} }\n"
        "}\n",
        encoding="utf-8",
    )
    config = {
        "project_name": "X",
        "lang": "csharp",
        "source": {"root_path": "src", "exclude_patterns": []},
        "tokens": {"l1_skeleton": 5000, "l2_signatures": 5000, "l3_relations": 5000, "encoding": "cl100k_base"},
        "pagerank": {"alpha": 0.85, "max_iter": 100},
        "output": {
            "directory": str(tmp_path / "out"),
            "files": {"skeleton": "L1.md", "signatures": "L2.md", "relations": "L3.md", "meta": "meta.json"},
        },
        "importance_boost": {"patterns": [], "priority_modules": []},  # empty!
        "categories": {"Other": {"patterns": []}},
    }
    gen = RepoMapGenerator(config=config, project_root=tmp_path)
    files = gen.scan()
    gen.parse(files)
    gen.build()

    # GameManager must have boost > 1.0 (Manager suffix in parser defaults)
    gm_info = next(
        (info for sid, info in gen.ranker.symbol_info.items() if info["label"] == "GameManager"),
        None,
    )
    assert gm_info is not None
    assert gm_info["boost"] > 1.0, (
        f"GameManager should inherit Manager-suffix boost from "
        f"CSharpParser.default_boost_patterns when user patterns is empty. "
        f"Got boost={gm_info['boost']}."
    )
    # Helper should stay at 1.0
    helper_info = next(
        (info for sid, info in gen.ranker.symbol_info.items() if info["label"] == "Helper"),
        None,
    )
    assert helper_info is not None
    assert helper_info["boost"] == 1.0


# ---- end-to-end: load_config + multi-lang generate works ----

def test_end_to_end_multi_lang_config_via_load_config(tmp_path):
    """Reproduce the original Critical #1: write a real multi-lang config
    to disk, load it through load_config, and run generate. Must not raise.
    """
    project = tmp_path / "proj"
    (project / "cs").mkdir(parents=True)
    (project / "lua").mkdir()
    (project / "cs" / "Game.cs").write_text(
        "namespace UnityEngine {\n  public class GameObject {}\n}\n",
        encoding="utf-8",
    )
    (project / "lua" / "main.lua").write_text(
        "local GO = CS.UnityEngine.GameObject\n"
        "function start()\n"
        "  GO.Find('x')\n"
        "end\n",
        encoding="utf-8",
    )
    cfg_path = project / ".repomap" / "config.yaml"
    cfg_path.parent.mkdir()
    cfg_path.write_text(
        "project_name: Mix\n"
        "langs: [csharp, lua]\n"
        "sources:\n"
        "  csharp:\n    root_path: cs\n    exclude_patterns: []\n"
        "  lua:\n    root_path: lua\n    exclude_patterns: []\n"
        "tokens:\n  l1_skeleton: 5000\n  l2_signatures: 5000\n  l3_relations: 5000\n  encoding: cl100k_base\n"
        "pagerank:\n  alpha: 0.85\n  max_iter: 100\n"
        "output:\n  directory: .repomap/output\n  files:\n    skeleton: L1.md\n    signatures: L2.md\n    relations: L3.md\n    meta: meta.json\n"
        "importance_boost:\n  patterns: []\n  priority_modules: []\n"
        "categories:\n  Other:\n    patterns: []\n"
        "crosslang:\n  enabled: true\n  lua_csharp_call_patterns:\n    - prefix: 'CS.'\n",
        encoding="utf-8",
    )
    cfg = RepoMapGenerator.load_config(cfg_path)
    gen = RepoMapGenerator(config=cfg, project_root=project)
    res = gen.run(verbose=False)
    assert res["success"] is True
    # At least one cross-lang resolved edge (Lua -> C# GameObject)
    xlang = [r for r in gen.references if r.kind == "csharp_call" and r.resolved]
    assert len(xlang) >= 1
