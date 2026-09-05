"""Annotation positions produce one raw_dep edge per named type (spec D4, D9)."""

from cgis.core.models import Edge, EdgeType
from cgis.extractors.python_extractor import PythonExtractor
from cgis.resolver.engine import ResolverEngine

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


def _resolve(code: str, file_path: str = "pkg/mod.py") -> list[Edge]:
    """Parse and resolve one source string, returning the final edges."""
    nodes, edges = PythonExtractor().parse(code, file_path)
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    return resolved


def _references(code: str) -> set[tuple[str, str]]:
    """Return (source, target) for every REFERENCES edge after resolution."""
    return {(e.source, e.target) for e in _resolve(code) if e.type == EdgeType.REFERENCES}


def test_parameter_annotation_of_a_local_class_becomes_references() -> None:
    code = "class Port:\n    pass\ndef use(p: Port) -> None:\n    pass\n"
    assert ("pkg.mod.use", "pkg.mod.Port") in _references(code)


def test_class_referenced_only_inside_a_generic_still_gets_an_edge() -> None:
    code = "class Item:\n    pass\ndef use(items: list[Item]) -> None:\n    pass\n"
    assert ("pkg.mod.use", "pkg.mod.Item") in _references(code)


def test_return_annotation_of_a_local_class_becomes_references() -> None:
    code = "class Report:\n    pass\ndef build() -> Report:\n    pass\n"
    assert ("pkg.mod.build", "pkg.mod.Report") in _references(code)


def test_stdlib_annotation_produces_no_reference_edge() -> None:
    targets = {t for _, t in _references("def f(x: str) -> None:\n    pass\n")}
    assert targets == set()


def test_unresolvable_annotation_produces_no_reference_edge() -> None:
    targets = {t for _, t in _references("def f(x: Nowhere) -> None:\n    pass\n")}
    assert targets == set()


def test_no_raw_dep_target_survives_resolution() -> None:
    code = "class Port:\n    pass\ndef use(p: Port, items: list[int]) -> None:\n    pass\n"
    assert not [e for e in _resolve(code) if e.target.startswith(_RAW_DEP)]


def test_import_map_hit_with_no_node_produces_no_reference_edge() -> None:
    """A third-party import can make resolve_class_ref return an FQN with no node.

    `from numpy import NDArray` puts "NDArray" -> "numpy.NDArray" in the file's
    import map, so `resolve_class_ref`'s import-map branch returns that FQN via
    its `... or target_fqn` fallback (there is no `numpy.NDArray` node in this
    graph). The membership check in `_resolved_dep_edge` is what keeps this
    resolved-but-nodeless FQN from becoming a REFERENCES edge.
    """
    code = "from numpy import NDArray\ndef use(x: NDArray) -> None:\n    pass\n"
    assert _references(code) == set()


def test_decorated_classmethod_return_annotation_becomes_references() -> None:
    """A decorated function must not lose its return-annotation edge.

    `_handle_decorated_definition` has its own call to `process_function_node`
    and must call `collect_return_annotation` too, exactly like the
    plain-function path in `_walk` does.
    """
    code = (
        "class Port:\n"
        "    pass\n"
        "class Factory:\n"
        "    @classmethod\n"
        "    def build(cls) -> Port:\n"
        "        pass\n"
    )
    assert ("pkg.mod.Factory.build", "pkg.mod.Port") in _references(code)


def test_splat_parameter_annotation_becomes_references() -> None:
    """Annotation-edge emission must not depend on `local_types` succeeding.

    `*args`/`**kw` have no plain identifier for `local_types` to key on
    (`get_identifier` returns "unknown" for a splat pattern), but the
    annotation still names a real type and must still produce an edge.
    """
    code = "class Port:\n    pass\ndef use(*args: Port) -> None:\n    pass\n"
    assert ("pkg.mod.use", "pkg.mod.Port") in _references(code)

    code_kw = "class Port:\n    pass\ndef use(**kw: Port) -> None:\n    pass\n"
    assert ("pkg.mod.use", "pkg.mod.Port") in _references(code_kw)


def test_method_self_reference_produces_no_edge() -> None:
    """A method annotating its own class is not a reference (spec D9)."""
    code = 'class Ring:\n    @classmethod\n    def make(cls) -> "Ring":\n        pass\n'
    assert _references(code) == set()


def test_nested_class_self_reference_produces_no_edge() -> None:
    """A nested class annotating its enclosing class is inside it, not a user of it."""
    code = (
        "class Outer:\n"
        "    class Inner:\n"
        '        def go(self, o: "Outer") -> None:\n'
        "            pass\n"
    )
    assert _references(code) == set()


def test_prefix_sharing_different_class_still_produces_an_edge() -> None:
    """The self-reference filter must be a dot-boundary check, not a string prefix.

    `Foo` and `FooBar` share a textual prefix but are unrelated classes; an
    annotation on `Foo` naming `FooBar` must still produce an edge.
    """
    code = "class FooBar:\n    pass\nclass Foo:\n    def go(self) -> FooBar:\n        pass\n"
    assert ("pkg.mod.Foo.go", "pkg.mod.FooBar") in _references(code)


def test_dunder_all_annotation_does_not_leak_its_value() -> None:
    """`collect_type_names` must only ever see a `type`/`return_type` node.

    `__all__: list[str] = ["ReExportedOnly"]` has a genuine annotation node
    (`list[str]`) sitting right beside a list literal full of class names. A
    rule that walked the whole assignment rather than only its `type` field
    would pick `ReExportedOnly` out of the *value* and emit a candidate for
    it. The control class, `ReExportedOnly`, is otherwise unreferenced except
    by a real annotation below, which proves the assertion isn't passing
    because annotation edges stopped firing altogether.
    """
    annotated = 'class ReExportedOnly:\n    pass\n__all__: list[str] = ["ReExportedOnly"]\n'
    targets = {t for _, t in _references(annotated)}
    assert "pkg.mod.ReExportedOnly" not in targets

    # Weaker control: no annotation node exists at all here, so this form
    # passes under any implementation, including a broken one that walks
    # values. Kept for completeness, not as the load-bearing assertion.
    bare = 'class ReExportedOnly:\n    pass\n__all__ = ["ReExportedOnly"]\n'
    targets_bare = {t for _, t in _references(bare)}
    assert "pkg.mod.ReExportedOnly" not in targets_bare

    # Control: the same class referenced from a real annotation position
    # must still produce an edge, or the assertions above would pass
    # vacuously if annotation edges were lost entirely.
    control = "class ReExportedOnly:\n    pass\ndef go(x: ReExportedOnly) -> None:\n    pass\n"
    assert ("pkg.mod.go", "pkg.mod.ReExportedOnly") in _references(control)
