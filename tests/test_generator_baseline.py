"""
Phase -1 generator baseline.

Re-runs RepoMapGenerator on the fixture and asserts equivalence with the
captured snapshot + golden markdown. Phase 5 GATE re-uses this exact test.
"""
import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coderepomap.generator import RepoMapGenerator
from tests.generate_baseline import normalize_markdown, normalize_meta

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "tests" / "baseline" / "config.yaml"
OUTPUT_DIR = REPO_ROOT / "tests" / "baseline" / "output"
SNAPSHOT_PATH = REPO_ROOT / "tests" / "baseline" / "snapshot.json"
GOLDEN_DIR = REPO_ROOT / "tests" / "baseline" / "golden"


@pytest.fixture(scope="module")
def fresh_run(tmp_path_factory):
    """Run generator into a tmp output, return parsed meta + markdown contents."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    tmp_out = tmp_path_factory.mktemp("baseline_out")
    config["output"]["directory"] = str(tmp_out)

    gen = RepoMapGenerator(config=config, project_root=REPO_ROOT)
    res = gen.run(verbose=False)
    assert res.get("success"), f"generator failed: {res}"

    meta = json.loads((tmp_out / "repomap-meta.json").read_text(encoding="utf-8"))
    md = {
        name: (tmp_out / name).read_text(encoding="utf-8")
        for name in ("repomap-L1-skeleton.md", "repomap-L2-signatures.md", "repomap-L3-relations.md")
    }
    return meta, md


def test_snapshot_stats_equivalent(fresh_run):
    meta, _ = fresh_run
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert meta["stats"] == expected["stats"]
    # ranker_stats may contain v0.2.0 additions (e.g. display_symbols); only
    # v0.1.0 keys must be equivalent.
    expected_rs = expected["ranker_stats"]
    actual_rs = {k: v for k, v in meta["ranker_stats"].items() if k in expected_rs}
    assert actual_rs == expected_rs
    assert meta["top_modules"] == expected["top_modules"]
    assert meta["source_path"] == expected["source_path"]
    assert meta["project_name"] == expected["project_name"]


def test_normalized_meta_equivalent(fresh_run):
    meta, _ = fresh_run
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert normalize_meta(meta) == expected["meta_normalized"]


def test_markdown_golden_equivalent(fresh_run):
    _, md = fresh_run
    for name, content in md.items():
        golden = (GOLDEN_DIR / name).read_text(encoding="utf-8")
        assert normalize_markdown(content) == golden, f"markdown drift in {name}"
