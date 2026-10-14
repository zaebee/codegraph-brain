"""The resolution contract between the engine and the language packs (step 4)."""

import pytest

from cgis.core.models import Node, NodeType
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


def _module(fqn: str, path: str) -> Node:
    return Node(
        id=fqn,
        type=NodeType.FILE,
        name=path.rsplit("/", 1)[-1],
        file_path=path,
        start_line=1,
        end_line=1,
        language="typescript",
    )


def test_typescript_reconciles_through_its_own_pack() -> None:
    """Aliases and workspace packages are the TS pack's facts; the index no longer carries them."""
    nodes = [_module("packages.lib.util", "packages/lib/util.ts")]
    index = IndexBuilder().build(nodes, workspace_packages={"@acme.lib": "packages.lib"})
    assert not hasattr(index, "workspace_packages")
    assert index.resolve_import_target("@acme.lib.util", "apps/web/a.ts") == "packages.lib.util"
    assert index.resolve_import_target("@acme.lib.util", "apps/web/a.py") is None


def test_python_reconciles_by_layout_prefixes() -> None:
    rules = rules_for("app/x.py")
    assert rules is not None
    assert rules.reconcile_import is not None
