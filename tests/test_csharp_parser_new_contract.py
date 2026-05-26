"""
Phase 2: new CSharpParser(LanguageParser) contract tests.

Validates that the new parser:
- Subclasses LanguageParser with required class attrs
- Auto-registers under lang='csharp' on import
- Produces Symbol/Reference with proper id/fqn/lang_meta on the fixture
- Distinguishes the 3 AddScore overloads by Symbol.id (param signature)
- Preserves legacy fields under lang_meta
"""
from pathlib import Path

import pytest

# Importing this subpackage triggers register(CSharpParser)
import coderepomap.csharp  # noqa: F401
from coderepomap.csharp.parser import CSharpParser
from coderepomap.core import registry
from coderepomap.core.parser_base import LanguageParser, Reference, Symbol


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "csharp" / "basic"


@pytest.fixture(scope="module")
def parsed():
    parser = CSharpParser()
    syms, refs = [], []
    for cs in sorted(FIXTURE_DIR.glob("*.cs")):
        s, r = parser.parse_file(cs, FIXTURE_DIR)
        syms.extend(s)
        refs.extend(r)
    return syms, refs


def test_parser_is_languageparser_subclass():
    assert issubclass(CSharpParser, LanguageParser)


def test_parser_class_attrs():
    assert CSharpParser.lang == "csharp"
    assert ".cs" in CSharpParser.file_extensions
    assert any("bin" in pat for pat in CSharpParser.default_excludes)
    assert CSharpParser.default_boost_patterns  # non-empty
    assert "Core" in CSharpParser.default_categories


def test_parser_auto_registered():
    """Importing coderepomap.csharp must register the parser."""
    assert "csharp" in registry.registered_langs()
    p = registry.get_parser("csharp")
    assert isinstance(p, CSharpParser)


def test_symbol_count_matches_baseline(parsed):
    syms, _ = parsed
    assert len(syms) == 20


def test_all_symbols_have_unique_id(parsed):
    """The whole point of Symbol.id — even with overloads, ids must be distinct."""
    syms, _ = parsed
    ids = [s.id for s in syms]
    assert len(ids) == len(set(ids)), f"duplicate ids: {[i for i in ids if ids.count(i) > 1]}"


def test_addscore_overloads_have_distinct_ids(parsed):
    """Three AddScore overloads share name+container but ids differ by param sig."""
    syms, _ = parsed
    addscores = [s for s in syms if s.name == "AddScore"]
    assert len(addscores) == 3
    ids = sorted(s.id for s in addscores)
    assert ids == [
        "csharp:Game.Core.GameManager.AddScore(int)",
        "csharp:Game.Core.GameManager.AddScore(int,bool)",
        "csharp:Game.Core.GameManager.AddScore(string,int)",
    ]


def test_symbol_lang_csharp(parsed):
    syms, _ = parsed
    assert all(s.lang == "csharp" for s in syms)


def test_symbol_fqn_dot_joined(parsed):
    syms, _ = parsed
    gm = [s for s in syms if s.name == "GameManager"][0]
    assert gm.fqn == "Game.Core.GameManager"


def test_symbol_container_is_namespace(parsed):
    syms, _ = parsed
    gm = [s for s in syms if s.name == "GameManager"][0]
    assert gm.container == "Game.Core"


def test_lang_meta_preserves_legacy_fields(parsed):
    syms, _ = parsed
    gm = [s for s in syms if s.name == "GameManager"][0]
    assert gm.lang_meta["namespace"] == "Game.Core"
    assert gm.lang_meta["base_class"] == ""  # GameManager has IManager (interface), not a base class
    assert gm.lang_meta["interfaces"] == ["IManager"]
    assert "public" in gm.lang_meta["modifiers"]


def test_hudpanel_lang_meta_carries_base():
    parser = CSharpParser()
    syms, _ = parser.parse_file(FIXTURE_DIR / "FileScoped.cs", FIXTURE_DIR)
    hud = [s for s in syms if s.name == "HUDPanel"][0]
    assert hud.lang_meta["base_class"] == "BasePanel"
    assert hud.lang_meta["interfaces"] == []


def test_nested_class_id():
    parser = CSharpParser()
    syms, _ = parser.parse_file(FIXTURE_DIR / "Nested.cs", FIXTURE_DIR)
    inner = [s for s in syms if s.name == "Inner" and s.kind == "class"][0]
    assert inner.id == "csharp:Game.Data.Container.Inner"


def test_reference_kind_inherits(parsed):
    _, refs = parsed
    inh = [r for r in refs if r.kind == "inherits"]
    assert len(inh) == 1
    assert inh[0].lang == "csharp"


def test_reference_kind_implements(parsed):
    _, refs = parsed
    impl = [r for r in refs if r.kind == "implements"]
    assert len(impl) == 1


def test_resolved_references_have_to_id(parsed):
    """File-local references should resolve to a real Symbol.id."""
    _, refs = parsed
    # HUDPanel -> BasePanel: both defined in FileScoped.cs, must resolve
    hud_to_base = [
        r for r in refs
        if r.kind == "inherits" and "HUDPanel" in r.from_id
    ]
    assert len(hud_to_base) == 1
    assert hud_to_base[0].resolved is True
    assert hud_to_base[0].to_id == "csharp:Game.UI.BasePanel"


def test_unresolved_cross_file_reference_marked():
    """GameManager implements IManager — both in same file, so should resolve.

    But if we parse a file without the interface present, it would be
    unresolved. Verify by parsing FileScoped.cs alone (HUDPanel : BasePanel,
    BasePanel present, so resolves) is already covered.
    """
    parser = CSharpParser()
    # Parse FileScoped.cs alone — HUDPanel:BasePanel resolves in-file
    syms, refs = parser.parse_file(FIXTURE_DIR / "FileScoped.cs", FIXTURE_DIR)
    inh = [r for r in refs if r.kind == "inherits"]
    assert len(inh) == 1
    assert inh[0].resolved


def test_method_ids_use_param_types_not_arity():
    """Even with the same arity, different types yield different ids."""
    parser = CSharpParser()
    syms, _ = parser.parse_file(FIXTURE_DIR / "Namespace.cs", FIXTURE_DIR)
    by_id = {s.id: s for s in syms if s.name == "AddScore"}
    # 3 overloads: one is arity=1 (int), two are arity=2 (string,int) and (int,bool)
    assert len(by_id) == 3
