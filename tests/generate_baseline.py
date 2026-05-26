"""
Phase -1 baseline generator.

Runs RepoMapGenerator on the fixed fixture and writes output to
tests/baseline/output/. Captures a normalized snapshot in
tests/baseline/snapshot.json so Phase 5 GATE can diff equivalence
without timestamp noise.

Run:
    python tests/generate_baseline.py
"""
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coderepomap.generator import RepoMapGenerator


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "tests" / "baseline" / "config.yaml"
OUTPUT_DIR = REPO_ROOT / "tests" / "baseline" / "output"
SNAPSHOT_PATH = REPO_ROOT / "tests" / "baseline" / "snapshot.json"
GOLDEN_DIR = REPO_ROOT / "tests" / "baseline" / "golden"


import re

VOLATILE_KEYS = {"timestamp", "duration", "generated_at", "git_commit", "git_branch"}
# v0.2.0 ranker_stats additions that didn't exist in v0.1.0 baseline — strip
# during normalize so the Phase 5 GATE checks v0.1.0-compatible fields only.
V02_RANKER_STATS_ADDITIONS = {"display_symbols"}
GENERATED_LINE_RE = re.compile(r"^> Generated:.*$", re.MULTILINE)


def normalize_meta(meta: dict) -> dict:
    """Strip volatile fields + v0.2.0 stats additions from meta for diffing."""
    out = {k: v for k, v in meta.items() if k not in VOLATILE_KEYS}
    rs = out.get("ranker_stats")
    if isinstance(rs, dict):
        out["ranker_stats"] = {
            k: v for k, v in rs.items() if k not in V02_RANKER_STATS_ADDITIONS
        }
    return out


def normalize_markdown(text: str) -> str:
    """Replace volatile lines (date + commit) so markdown can be diffed stably."""
    return GENERATED_LINE_RE.sub("> Generated: <NORMALIZED>", text)


def main():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    gen = RepoMapGenerator(config=config, project_root=REPO_ROOT)
    results = gen.run(verbose=True)

    if not results.get("success"):
        print("Generator failed", file=sys.stderr)
        return 1

    meta_path = OUTPUT_DIR / "repomap-meta.json"
    if not meta_path.exists():
        print(f"Meta not produced at {meta_path}", file=sys.stderr)
        return 1

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    stats = meta.get("stats", {})
    snapshot = {
        "stats": stats,
        "ranker_stats": meta.get("ranker_stats", {}),
        "top_modules": meta.get("top_modules", []),
        "source_path": meta.get("source_path"),
        "project_name": meta.get("project_name"),
        "meta_normalized": normalize_meta(meta),
    }

    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False, sort_keys=True)

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for md_name in ("repomap-L1-skeleton.md", "repomap-L2-signatures.md", "repomap-L3-relations.md"):
        src = OUTPUT_DIR / md_name
        if not src.exists():
            print(f"Missing {src}", file=sys.stderr)
            return 1
        normalized = normalize_markdown(src.read_text(encoding="utf-8"))
        (GOLDEN_DIR / md_name).write_text(normalized, encoding="utf-8")

    print()
    print(f"Snapshot written to {SNAPSHOT_PATH}")
    print(f"Golden markdown written to {GOLDEN_DIR}")
    print(f"  stats: {stats}")
    print(f"  ranker_stats: {snapshot['ranker_stats']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
