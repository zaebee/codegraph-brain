"""Unit tests for the structural tier ladder and its entropy slope (#186)."""

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.drift.fractal import build_ladder


def _node(fqn: str, ntype: NodeType, path: str) -> Node:
    """A graph node for ladder tests."""
    return Node(
        id=fqn,
        type=ntype,
        name=fqn.rsplit(".", 1)[-1],
        file_path=path,
        start_line=0,
        end_line=0,
    )


def _edge(src: str, tgt: str, etype: EdgeType) -> Edge:
    """A graph edge for ladder tests."""
    return Edge(
        id=f"{src}:{etype.value}:{tgt}",
        source=src,
        target=tgt,
        type=etype,
        weight=1.0,
        confidence=1.0,
    )


def test_t1_class_folds_method_into_declaring_class() -> None:
    nodes = [
        _node("pkg.mod", NodeType.FILE, "pkg/mod.py"),
        _node("pkg.mod.Thing", NodeType.CLASS, "pkg/mod.py"),
        _node("pkg.mod.Thing.run", NodeType.METHOD, "pkg/mod.py"),
    ]
    edges = [
        _edge("pkg.mod", "pkg.mod.Thing", EdgeType.CONTAINS),
        _edge("pkg.mod.Thing", "pkg.mod.Thing.run", EdgeType.DECLARES),
    ]

    ladder = dict(build_ladder(nodes, edges))

    assert ladder["T1_class"]["pkg.mod.Thing.run"] == "pkg.mod.Thing"
    assert ladder["T2_module"]["pkg.mod.Thing.run"] == "pkg.mod"


def test_t1_class_leaves_module_level_function_on_its_file() -> None:
    nodes = [
        _node("pkg.mod", NodeType.FILE, "pkg/mod.py"),
        _node("pkg.mod.helper", NodeType.FUNCTION, "pkg/mod.py"),
    ]
    edges = [_edge("pkg.mod", "pkg.mod.helper", EdgeType.CONTAINS)]

    ladder = dict(build_ladder(nodes, edges))

    assert ladder["T1_class"]["pkg.mod.helper"] == "pkg.mod"


def test_nested_function_folds_through_its_enclosing_function() -> None:
    nodes = [
        _node("pkg.mod", NodeType.FILE, "pkg/mod.py"),
        _node("pkg.mod.outer", NodeType.FUNCTION, "pkg/mod.py"),
        _node("pkg.mod.outer.inner", NodeType.FUNCTION, "pkg/mod.py"),
    ]
    edges = [
        _edge("pkg.mod", "pkg.mod.outer", EdgeType.CONTAINS),
        _edge("pkg.mod.outer", "pkg.mod.outer.inner", EdgeType.CONTAINS),
    ]

    ladder = dict(build_ladder(nodes, edges))

    assert ladder["T1_class"]["pkg.mod.outer.inner"] == "pkg.mod"
    assert ladder["T2_module"]["pkg.mod.outer.inner"] == "pkg.mod"


def test_directory_rungs_trim_from_the_leaf_so_every_file_moves() -> None:
    """The regression test for the root-truncation artifact (#186).

    Trimming from the root end leaves shallow files stationary and produces
    near-duplicate rungs. Trimming from the leaf end moves every file at every
    rung until it bottoms out at <root>.
    """
    nodes = [
        _node("a.b.c.deep", NodeType.FILE, "a/b/c/deep.py"),
        _node("a.shallow", NodeType.FILE, "a/shallow.py"),
    ]

    ladder = dict(build_ladder(nodes, []))

    assert ladder["T3_up1"]["a.b.c.deep"] == "a/b/c"
    assert ladder["T3_up1"]["a.shallow"] == "a"
    assert ladder["T4_up2"]["a.b.c.deep"] == "a/b"
    assert ladder["T4_up2"]["a.shallow"] == "<root>"
    assert ladder["T5_up3"]["a.b.c.deep"] == "a"


def test_ladder_starts_with_the_identity_rung() -> None:
    nodes = [_node("pkg.mod", NodeType.FILE, "pkg/mod.py")]

    rungs = build_ladder(nodes, [])

    assert rungs[0][0] == "T0_symbol"
    assert rungs[0][1] == {"pkg.mod": "pkg.mod"}


def test_containment_cycle_does_not_hang() -> None:
    """A malformed graph must not spin the parent-walk forever."""
    nodes = [
        _node("pkg.a", NodeType.FUNCTION, "pkg/x.py"),
        _node("pkg.b", NodeType.FUNCTION, "pkg/x.py"),
    ]
    edges = [
        _edge("pkg.a", "pkg.b", EdgeType.CONTAINS),
        _edge("pkg.b", "pkg.a", EdgeType.CONTAINS),
    ]

    ladder = dict(build_ladder(nodes, edges))

    assert ladder["T1_class"]["pkg.a"] == "pkg.a"
    assert ladder["T1_class"]["pkg.b"] == "pkg.b"


def test_empty_graph_yields_no_rungs_beyond_identity() -> None:
    rungs = build_ladder([], [])

    assert rungs[0][0] == "T0_symbol"
    assert rungs[0][1] == {}
