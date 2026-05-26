"""Phase 3: core/ranker.py + core/graph_builder.py."""
from coderepomap.core.parser_base import Reference, Symbol
from coderepomap.core.ranker import PageRankRanker
from coderepomap.core.graph_builder import build_graph, _boost_for_symbol


# ----- PageRankRanker basics -----

def test_ranker_id_keyed_nodes():
    r = PageRankRanker()
    r.add_symbol("csharp:A.Foo", file="A.cs", kind="class", label="Foo", fqn="A.Foo", lang="csharp")
    r.add_symbol("csharp:B.Foo", file="B.cs", kind="class", label="Foo", fqn="B.Foo", lang="csharp")
    # Both `Foo` but different ids — must coexist as two nodes
    stats = r.get_stats()
    assert stats["nodes"] == 2
    assert stats["display_symbols"] == 1  # both have label "Foo"


def test_ranker_add_symbol_requires_id():
    r = PageRankRanker()
    try:
        r.add_symbol("", file="x.cs", kind="class")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError on empty id")


def test_ranker_add_reference_resolved_creates_edge():
    r = PageRankRanker()
    r.add_symbol("csharp:A", file="a.cs", kind="class", label="A")
    r.add_symbol("csharp:B", file="b.cs", kind="class", label="B")
    r.add_reference("csharp:A", "csharp:B", kind="inherits")
    assert r.get_stats()["edges"] == 1


def test_ranker_add_reference_unresolved_dropped():
    """to_id='' must NOT create an edge."""
    r = PageRankRanker()
    r.add_symbol("csharp:A", file="a.cs", kind="class")
    r.add_reference("csharp:A", "", kind="csharp_call")
    assert r.get_stats()["edges"] == 0


def test_ranker_add_reference_empty_from_raises():
    r = PageRankRanker()
    r.add_symbol("csharp:A", file="a.cs", kind="class")
    try:
        r.add_reference("", "csharp:A", kind="x")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError on empty from_id")


def test_ranker_label_defaults_to_id_when_empty():
    r = PageRankRanker()
    r.add_symbol("csharp:Foo", file="f.cs", kind="class")
    info = r.symbol_info["csharp:Foo"]
    assert info["label"] == "csharp:Foo"


def test_ranker_get_ranked_symbols_uses_label():
    r = PageRankRanker()
    r.add_symbol("csharp:A.Manager", file="a.cs", kind="class", boost=2.0, label="Manager")
    r.add_symbol("csharp:B.Util", file="b.cs", kind="class", label="Util")
    r.add_reference("csharp:B.Util", "csharp:A.Manager", kind="uses")
    ranked = r.get_ranked_symbols()
    assert ranked[0][0] == "csharp:A.Manager"
    assert ranked[0][2]["label"] == "Manager"


# ----- boost matching -----

def test_boost_suffix():
    assert _boost_for_symbol("PlayerManager", [{"suffix": "Manager", "boost": 1.5}]) == 1.5


def test_boost_prefix_requires_uppercase_after():
    """v0.1.0-equivalent guard: prefix matches only if next char is uppercase."""
    assert _boost_for_symbol("SAuthService", [{"prefix": "S", "boost": 2.0}]) == 2.0


def test_boost_prefix_skips_lowercase_after():
    """`Setup` should NOT match `prefix: S` because the next char `e` is lowercase."""
    assert _boost_for_symbol("Setup", [{"prefix": "S", "boost": 2.0}]) == 1.0


def test_boost_multiple_match_uses_max():
    """A name matching two patterns gets MAX, not product (v0.1.0 behavior)."""
    boost = _boost_for_symbol(
        "SAuthManager",
        [{"prefix": "S", "boost": 2.0}, {"suffix": "Manager", "boost": 1.5}],
    )
    assert boost == 2.0


def test_boost_no_match_returns_one():
    assert _boost_for_symbol("Helper", [{"suffix": "Manager", "boost": 1.5}]) == 1.0


# ----- build_graph -----

def _mk_sym(sid, name, kind="class", fqn="", file="x.cs"):
    return Symbol(id=sid, name=name, fqn=fqn or sid, kind=kind, file=file, line=1, lang="csharp")


def test_build_graph_only_classes_by_default():
    """v0.1.0 behavior: only `class` kind becomes a node; methods/properties
    do not. Matches the legacy generator filter."""
    syms = [
        _mk_sym("csharp:A", "A", kind="class"),
        _mk_sym("csharp:I", "I", kind="interface"),
        _mk_sym("csharp:A.M", "M", kind="method"),
        _mk_sym("csharp:A.Prop", "Prop", kind="property"),
    ]
    r = PageRankRanker()
    r, unresolved = build_graph(syms, [], r)
    assert r.get_stats()["nodes"] == 1  # only class A
    assert unresolved == []


def test_build_graph_widen_node_kinds():
    syms = [
        _mk_sym("csharp:A", "A", kind="class"),
        _mk_sym("csharp:I", "I", kind="interface"),
    ]
    r = PageRankRanker()
    r, _ = build_graph(syms, [], r, node_kinds=["class", "interface"])
    assert r.get_stats()["nodes"] == 2


def test_build_graph_empty_node_kinds_accepts_all():
    """Empty set = no filter, every Symbol becomes a node."""
    syms = [
        _mk_sym("csharp:A", "A", kind="class"),
        _mk_sym("csharp:A.M", "M", kind="method"),
    ]
    r = PageRankRanker()
    r, _ = build_graph(syms, [], r, node_kinds=[])
    assert r.get_stats()["nodes"] == 2


def test_build_graph_unresolved_references_returned():
    syms = [_mk_sym("csharp:A", "A")]
    refs = [
        Reference(
            from_id="csharp:A", to_id="", file="a.cs", line=10,
            kind="csharp_call", lang="csharp", resolved=False,
            to_external="CS.UnityEngine.GameObject",
        )
    ]
    r = PageRankRanker()
    r, unresolved = build_graph(syms, refs, r)
    assert len(unresolved) == 1
    assert unresolved[0].to_external == "CS.UnityEngine.GameObject"
    assert r.get_stats()["edges"] == 0  # NOT in graph


def test_build_graph_resolved_references_create_edges():
    syms = [
        _mk_sym("csharp:A", "A"),
        _mk_sym("csharp:B", "B"),
    ]
    refs = [Reference(
        from_id="csharp:A", to_id="csharp:B", file="a.cs", line=5,
        kind="inherits", lang="csharp", resolved=True,
    )]
    r = PageRankRanker()
    r, unresolved = build_graph(syms, refs, r)
    assert r.get_stats()["edges"] == 1
    assert unresolved == []


def test_build_graph_applies_boost_patterns():
    syms = [_mk_sym("csharp:X.PlayerManager", "PlayerManager")]
    r = PageRankRanker()
    r, _ = build_graph(syms, [], r, boost_patterns=[{"suffix": "Manager", "boost": 1.5}])
    assert r.symbol_info["csharp:X.PlayerManager"]["boost"] == 1.5


# ----- end-to-end with new CSharpParser -----

def test_ranker_from_csharp_parser_fixture():
    """Smoke test: feed the new C# parser output into the new ranker."""
    from pathlib import Path
    import coderepomap.csharp  # noqa: F401 - registers parser
    from coderepomap.csharp.parser import CSharpParser

    fixture = Path(__file__).resolve().parent / "fixtures" / "csharp" / "basic"
    parser = CSharpParser()
    all_syms, all_refs = [], []
    for f in sorted(fixture.glob("*.cs")):
        s, r = parser.parse_file(f, fixture)
        all_syms.extend(s)
        all_refs.extend(r)

    ranker = PageRankRanker()
    ranker, unresolved = build_graph(all_syms, all_refs, ranker)

    # 5 types in the fixture (4 class + 1 interface + 1 nested = 5)
    # Wait, fixture has: GameManager(class), IManager(interface), HUDPanel(class),
    # BasePanel(class), Container(class), Container.Inner(class) = 6 types
    assert ranker.get_stats()["nodes"] == 6
    # HUDPanel -> BasePanel resolves in-file, GameManager -> IManager resolves
    # in-file, so we expect 2 edges
    assert ranker.get_stats()["edges"] == 2
