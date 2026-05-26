"""
Phase -1 baseline for csharp parser behavior.

Locks the CURRENT parser output (including known limitations) so the Phase 5
GATE can detect any regression introduced by the coderepomap refactor.

Known existing behaviors that this baseline locks (NOT bugs to fix in this
mission — they will be addressed separately):

- Method overloads share name+container, distinguishable only by `line`.
- Nested-type members carry the inner class name in `parent_class`, losing
  the outer class prefix.
- Read-only auto properties (`{ get; }`) lose their type in `signature`.
- `{ get; private set; }` is reported as `{ get; set; }`.
- Only `inherits` / `implements` references are produced; calls are not.

The Phase 5 GATE asserts that after the coderepomap refactor, the same
inputs produce equivalent symbols/references when projected to the legacy
field names (with documented additions like `Symbol.id`).
"""

from pathlib import Path

import pytest

from coderepomap.parser import CSharpParser


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "csharp" / "basic"


@pytest.fixture(scope="module")
def parsed():
    parser = CSharpParser()
    all_syms = []
    all_refs = []
    for cs in sorted(FIXTURE_DIR.glob("*.cs")):
        syms, refs = parser.parse_file(cs, FIXTURE_DIR)
        all_syms.extend(syms)
        all_refs.extend(refs)
    return all_syms, all_refs


def test_symbol_count(parsed):
    syms, _ = parsed
    assert len(syms) == 20, f"expected 20 symbols, got {len(syms)}"


def test_reference_count(parsed):
    _, refs = parsed
    assert len(refs) == 2, f"expected 2 references, got {len(refs)}"


def test_kind_distribution(parsed):
    syms, _ = parsed
    kinds = {}
    for s in syms:
        kinds[s.kind] = kinds.get(s.kind, 0) + 1
    assert kinds == {
        "class": 5,        # 4 + 1 nested (Inner)
        "interface": 1,
        "method": 9,       # 3 AddScore overloads + others
        "property": 5,
    }, f"unexpected kind distribution: {kinds}"


def test_method_overload_baseline(parsed):
    """3 AddScore overloads share name+container; only `line` differs."""
    syms, _ = parsed
    addscores = [s for s in syms if s.name == "AddScore"]
    assert len(addscores) == 3
    for s in addscores:
        assert s.namespace == "Game.Core"
        assert s.parent_class == "GameManager"
        assert s.kind == "method"
    lines = sorted(s.line for s in addscores)
    assert lines == [22, 27, 32]
    sigs = sorted(s.signature for s in addscores)
    assert sigs == [
        "void AddScore(int delta)",
        "void AddScore(int delta, bool combo)",
        "void AddScore(string source, int delta)",
    ]


def test_nested_class_parent_baseline(parsed):
    """Nested-type members lose the outer class prefix (current behavior)."""
    syms, _ = parsed
    inner = [s for s in syms if s.name == "Inner" and s.kind == "class"]
    assert len(inner) == 1
    assert inner[0].parent_class == "Container"
    # Inner's members: parent_class is "Inner" (outer prefix dropped)
    inner_members = [s for s in syms if s.parent_class == "Inner"]
    assert {s.name for s in inner_members} == {"Value", "Reset"}


def test_readonly_property_signature_baseline(parsed):
    """`public Inner Item { get; }` signature loses the type (current behavior)."""
    syms, _ = parsed
    item = [s for s in syms if s.name == "Item" and s.parent_class == "Container"]
    assert len(item) == 1
    # Locked: type stripped, leaving leading space
    assert item[0].signature.lstrip().startswith("Item")


def test_inheritance_references(parsed):
    _, refs = parsed
    inherits = [r for r in refs if r.ref_type == "inherits"]
    implements = [r for r in refs if r.ref_type == "implements"]
    assert len(inherits) == 1
    assert inherits[0].from_symbol == "HUDPanel"
    assert inherits[0].to_symbol == "BasePanel"
    assert len(implements) == 1
    assert implements[0].from_symbol == "GameManager"
    assert implements[0].to_symbol == "IManager"


def test_no_call_references_baseline(parsed):
    """Current parser does NOT produce call references — locked."""
    _, refs = parsed
    calls = [r for r in refs if r.ref_type in ("calls", "uses")]
    assert calls == []
