"""
End-to-end generator pipeline test for the TypeScript plug-in:
scan -> parse -> crosslang resolve -> graph -> render -> save.
"""

from pathlib import Path

from coderepomap.core.generator import RepoMapGenerator

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "typescript" / "simple_project"


def _make_config(tmp_path):
    return {
        "project_name": "TS Fixture",
        "lang": "typescript",
        "source": {
            "root_path": str(FIXTURE),
            "exclude_patterns": [],
        },
        "tokens": {
            "l1_skeleton": 1000,
            "l2_signatures": 2000,
            "l3_relations": 3000,
            "encoding": "cl100k_base",
        },
        "pagerank": {"alpha": 0.85, "max_iter": 100},
        "output": {
            "directory": str(tmp_path / "out"),
            "files": {
                "skeleton": "repomap-L1-skeleton.md",
                "signatures": "repomap-L2-signatures.md",
                "relations": "repomap-L3-relations.md",
                "meta": "repomap-meta.json",
            },
        },
        "importance_boost": {"patterns": [], "priority_modules": []},
        "categories": {
            "Service": {"patterns": ["service"]},
            "Other": {"patterns": []},
        },
    }


def test_typescript_generate_produces_nonempty_outputs(tmp_path):
    gen = RepoMapGenerator(config=_make_config(tmp_path), project_root=tmp_path)
    result = gen.run(verbose=False)
    assert result["success"] is True
    out = tmp_path / "out"
    l1 = (out / "repomap-L1-skeleton.md").read_text(encoding="utf-8")
    l2 = (out / "repomap-L2-signatures.md").read_text(encoding="utf-8")
    l3 = (out / "repomap-L3-relations.md").read_text(encoding="utf-8")
    assert l1.strip() and l2.strip() and l3.strip()
    # package sentinel flips the renderer into widened wording
    assert "entry symbols" in l1
    # L2 lists real signatures, not just class names
    assert "class UserService" in l2


def test_typescript_generate_resolves_imports_into_graph_edges(tmp_path):
    gen = RepoMapGenerator(config=_make_config(tmp_path), project_root=tmp_path)
    gen.run(verbose=False)
    assert gen.ranker.graph is not None
    edges = set(gen.ranker.graph.edges())
    assert (
        "typescript:services/user-service",
        "typescript:services/base.BaseService",
    ) in edges, "named import must become a resolved graph edge"
    assert (
        "typescript:services/user-service.UserService",
        "typescript:services/base.BaseService",
    ) in edges, "extends must become an inherits edge"
    # external imports must NOT appear as graph nodes
    assert not [n for n in gen.ranker.graph.nodes() if n == ""], "empty node leaked"
    unresolved_externals = [
        r for r in gen.unresolved_references if r.to_external == "zod"
    ]
    assert unresolved_externals, "npm imports stay in the unresolved set for L3"
