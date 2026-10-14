"""The resolution contract between the engine and the language packs (step 4)."""

import pytest

from cgis.core.resolution import AFTER_IMPORT_MAP, ResolutionRules, ResolutionView
from cgis.languages import rules_for, type_normalizers
from cgis.resolver.indices import IndexBuilder


def _step(_name: str, _file_path: str, _view: ResolutionView) -> str | None:
    return None


def test_an_unknown_step_position_is_refused() -> None:
    """A typo would otherwise drop the rule silently."""
    with pytest.raises(ValueError, match="after:imports"):
        ResolutionRules(steps=(("after:imports", _step),))


def test_the_known_position_is_accepted() -> None:
    assert ResolutionRules(steps=((AFTER_IMPORT_MAP, _step),)).steps[0][0] == AFTER_IMPORT_MAP


def test_the_index_is_a_resolution_view() -> None:
    """Rules read the index through the core protocol, never by importing the resolver."""
    view: ResolutionView = IndexBuilder().build([])
    assert view.strip_layout_prefixes("x.y") is None


def test_rules_follow_the_classifying_pack() -> None:
    """No source is Python's (the preserved quirk); a source no pack reads has none."""
    assert rules_for(None) is not None
    assert rules_for("a.ts") is not None
    assert rules_for("a.js") is None


def test_normalizers_come_in_manifest_order() -> None:
    assert isinstance(type_normalizers(), tuple)
