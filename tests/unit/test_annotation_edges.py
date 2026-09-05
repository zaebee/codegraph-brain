"""Annotation positions produce one raw_dep edge per named type (spec D4, D9)."""

from cgis.core.models import EdgeType
from cgis.extractors.python_extractor import PythonExtractor

_RAW_DEP = "raw_dep:"


def _dep_targets(code: str) -> set[str]:
    """Parse source and return every raw_dep: target emitted, without the prefix."""
    _, edges = PythonExtractor().parse(code, "pkg/mod.py")
    return {
        e.target.removeprefix(_RAW_DEP)
        for e in edges
        if e.type == EdgeType.DEPENDS_ON and e.target.startswith(_RAW_DEP)
    }


def test_parameter_annotation_emits_its_type() -> None:
    assert "SearchClient" in _dep_targets("def f(c: SearchClient) -> None: pass\n")


def test_generic_parameter_emits_container_and_argument() -> None:
    targets = _dep_targets("def f(items: list[Node]) -> None: pass\n")
    assert {"list", "Node"} <= targets


def test_return_annotation_emits_its_type() -> None:
    assert "Report" in _dep_targets("def f() -> Report: pass\n")


def test_generic_return_emits_every_argument() -> None:
    targets = _dep_targets("def f() -> dict[str, Edge]: pass\n")
    assert {"dict", "str", "Edge"} <= targets


def test_class_body_annotation_emits_its_type() -> None:
    assert "SearchClient" in _dep_targets("class A:\n    client: SearchClient\n")


def test_annotated_local_assignment_emits_its_type() -> None:
    code = "def f() -> None:\n    x: Report = build()\n"
    assert "Report" in _dep_targets(code)


def test_bare_none_return_emits_nothing() -> None:
    assert _dep_targets("def f() -> None: pass\n") == set()


def test_edge_ids_are_unique_across_names_of_one_annotation() -> None:
    _, edges = PythonExtractor().parse("def f(x: dict[str, Edge]) -> None: pass\n", "pkg/mod.py")
    dep_ids = [e.id for e in edges if e.target.startswith(_RAW_DEP)]
    assert len(dep_ids) == len(set(dep_ids))
