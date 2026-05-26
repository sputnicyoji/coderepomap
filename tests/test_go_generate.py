"""End-to-end Go pipeline: scan -> parse -> build -> render.

Per Prior Lesson `tests-pass-is-not-feature-works`, at least one test must
drive the actual generator pipeline (config -> RepoMapGenerator -> save_all)
to catch regressions that unit tests bypass.
"""
from pathlib import Path

import pytest

import coderepomap.go  # noqa: F401
from coderepomap.core.generator import RepoMapGenerator


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "go" / "simple_project"


def _cfg(out_dir: Path) -> dict:
    return {
        "project_name": "GoFixture",
        "lang": "go",
        "source": {"root_path": str(FIXTURE)},
        "tokens": {
            "l1_skeleton": 4000,
            "l2_signatures": 4000,
            "l3_relations": 4000,
            "encoding": "cl100k_base",
        },
        "pagerank": {"alpha": 0.85, "max_iter": 100},
        "output": {
            "directory": str(out_dir),
            "files": {
                "skeleton": "L1.md",
                "signatures": "L2.md",
                "relations": "L3.md",
                "meta": "meta.json",
            },
        },
        "importance_boost": {"patterns": [], "priority_modules": []},
        "categories": {"Other": {"patterns": []}},
    }


def test_go_generate_produces_nonempty_outputs(tmp_path):
    gen = RepoMapGenerator(config=_cfg(tmp_path / "out"), project_root=tmp_path)
    files = gen.scan()
    assert any(sf.path.name == "service.go" for sf in files)
    gen.parse(files, verbose=False)
    gen.build()
    out = gen.save_all(verbose=False)
    l1 = Path(out["l1"]["path"]).read_text(encoding="utf-8")
    l2 = Path(out["l2"]["path"]).read_text(encoding="utf-8")
    l3 = Path(out["l3"]["path"]).read_text(encoding="utf-8")
    assert "Service" in l2 or "Runner" in l2 or "NewService" in l2
    # Widened wording due to package symbols in the graph
    assert "entry symbols" in l1 or "Core Entry Symbols" in l1


def test_go_generate_resolves_imports(tmp_path):
    gen = RepoMapGenerator(config=_cfg(tmp_path / "out"), project_root=tmp_path)
    gen.parse(gen.scan(), verbose=False)
    gen.build()
    resolved_to_ids = {
        r.to_id for r in gen.references
        if r.lang == "go" and r.resolved and r.to_id
    }
    # At least the import edge to pkg/service must resolve
    assert "go:example.com/myapp/pkg/service" in resolved_to_ids
