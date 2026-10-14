"""`output_format="text"`, and trace with impact's node budget and test screen (#569, #580).

JSON repeated every FQN in nodes and edges: `cgis_trace_flow(cgis.cli.ingest,
depth=3)` was 29k characters, and the A/B agent that got it named all 70 nodes.
The tree form is a fifth of that and reads in call order.
"""

from pathlib import Path

import pytest

from cgis.api.mcp_server import _coverage_text, cgis_analyze_impact, cgis_ingest, cgis_trace_flow
from cgis.core.coverage import TraversalCoverage
from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.render.text import graph_to_text


def _node(fqn: str, line: int = 1) -> Node:
    return Node(
        id=fqn,
        type=NodeType.FUNCTION,
        name=fqn.rsplit(".", 1)[-1],
        file_path=f"{fqn.split('.', maxsplit=1)[0]}.py",
        start_line=line,
        end_line=line + 1,
    )


def _edge(src: str, dst: str, line: int, edge_type: EdgeType = EdgeType.CALLS) -> Edge:
    return Edge(
        id=f"{src}->{dst}@{line}",
        source=src,
        target=dst,
        type=edge_type,
        line_number=line,
    )


_NODES = [_node("m.root"), _node("m.first", 10), _node("m.second", 20), _node("m.shared", 30)]
_NODES.append(_node("m.Model", 40))
_EDGES = [
    # Declared out of call order: the tree must follow the call-site lines.
    _edge("m.root", "m.second", 7),
    _edge("m.root", "m.first", 3),
    _edge("m.first", "m.shared", 11),
    _edge("m.second", "m.shared", 21),
    _edge("m.first", "m.Model", 12, EdgeType.REFERENCES),
    _edge("m.second", "m.Model", 22, EdgeType.REFERENCES),
]


def test_children_come_in_call_site_order_and_indentation_is_depth() -> None:
    lines = graph_to_text("m.root", _NODES, _EDGES, downstream=True).splitlines()

    assert lines[0] == "m.root  m.py:1"
    assert lines[1] == "  m.first  m.py:10"
    assert lines[2].startswith("    m.shared")
    assert lines.index("  m.second  m.py:20") > lines.index("  m.first  m.py:10")


def test_a_call_reached_twice_is_expanded_once_and_pointed_back_to() -> None:
    text = graph_to_text("m.root", _NODES, _EDGES, downstream=True)

    assert text.count("m.shared  m.py:30") == 1
    assert "    ↪ m.shared" in text


def test_a_repeated_reference_is_dropped_and_a_first_one_is_tagged() -> None:
    """`Node` named in every signature says nothing about what runs."""
    text = graph_to_text("m.root", _NODES, _EDGES, downstream=True)

    assert "m.Model  m.py:40 [REFERENCES]" in text
    assert "↪ m.Model" not in text


def test_a_call_site_expands_a_node_an_earlier_annotation_only_names() -> None:
    """`root` annotates with `m.Model` before `m.run` calls it (Gemini on #581).

    Expanded at the first sighting, the annotation took `m.Model`'s subtree and
    the call that runs it got a bare `↪`.
    """
    nodes = [_node("m.root"), _node("m.run", 5), _node("m.Model", 9), _node("m.build", 12)]
    edges = [
        _edge("m.root", "m.Model", 1, EdgeType.REFERENCES),
        _edge("m.root", "m.run", 2),
        _edge("m.run", "m.Model", 6),
        _edge("m.Model", "m.build", 10),
    ]
    lines = graph_to_text("m.root", nodes, edges, downstream=True).splitlines()

    assert lines == [
        "m.root  m.py:1",
        "  m.run  m.py:5",
        "    m.Model  m.py:9",
        "      m.build  m.py:12",
    ]


def test_a_node_only_named_is_still_listed_where_it_is_named() -> None:
    """No call reaches it, so the mention is the only place it can appear."""
    text = graph_to_text("m.root", _NODES, _EDGES, downstream=True)

    assert "m.Model  m.py:40 [REFERENCES]" in text


def test_a_callee_called_on_several_lines_is_listed_once() -> None:
    edges = [_edge("m.root", "m.first", 3), _edge("m.root", "m.first", 4)]
    text = graph_to_text("m.root", _NODES[:2], edges, downstream=True)

    assert text.splitlines() == ["m.root  m.py:1", "  m.first  m.py:10"]


def test_a_node_the_root_does_not_reach_is_listed_at_the_end() -> None:
    """The heading counts it; the tree must not drop it without a word."""
    text = graph_to_text("m.root", _NODES[:2], [], downstream=True)

    assert text.splitlines()[-2:] == ["(not reached from the root:)", "  m.first"]


def test_containment_is_not_tagged() -> None:
    edges = [_edge("m.root", "m.first", 0, EdgeType.CONTAINS)]
    assert graph_to_text("m.root", _NODES[:2], edges, downstream=True).endswith("m.first  m.py:10")


def test_upstream_puts_callers_under_the_callee() -> None:
    text = graph_to_text("m.shared", _NODES, _EDGES, downstream=False)

    assert text.splitlines()[:2] == ["m.shared  m.py:30", "  m.first  m.py:10"]


_REPO = {
    "app.py": (
        "from helpers import util\n"
        "from tests.fixtures import fake\n\n"
        "def main():\n"
        "    print('x')\n"
        "    len('x')\n"
        "    step_a()\n"
        "    step_b()\n"
        "    fake()\n\n"
        "def step_a():\n    util()\n\n"
        "def step_b():\n    util()\n"
    ),
    "helpers.py": "def util():\n    pass\n",
    "tests/fixtures.py": "def fake():\n    pass\n",
}


@pytest.fixture
def db(tmp_path: Path) -> str:
    """A main that calls two steps, stdlib, and a function in test code."""
    for rel, source in _REPO.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    db_path = str(tmp_path / "graph.db")
    cgis_ingest(str(tmp_path), db_path)
    return db_path


def test_trace_as_text_has_the_tree_and_the_coverage_line(db: str) -> None:
    text = cgis_trace_flow("app.main", db, depth=3, output_format="text")

    assert "\nExecution flow for app.main (" in text, "the notes come first, then the tree"
    assert "\n  app.step_a  app.py:" in text
    assert "\n    helpers.util  helpers.py:" in text
    assert "\ncoverage: " in text


def test_trace_leaves_out_test_code_and_says_so(db: str) -> None:
    text = cgis_trace_flow("app.main", db, depth=3, output_format="text")

    assert "tests.fixtures.fake" not in text
    assert "1 test node(s) left out" in text
    assert "tests.fixtures.fake" in cgis_trace_flow(
        "app.main", db, depth=3, output_format="text", include_tests=True
    )


def test_stdlib_targets_do_not_spend_the_trace_budget(db: str) -> None:
    """`print` and `len` are pruned anyway; counted, they would crowd out `step_b`."""
    text = cgis_trace_flow("app.main", db, depth=1, output_format="text", max_nodes=3)

    assert "app.step_a" in text
    assert "app.step_b" in text
    assert "Truncated" not in text


def test_the_trace_budget_cuts_and_says_where(db: str) -> None:
    text = cgis_trace_flow("app.main", db, depth=3, output_format="text", max_nodes=2)

    assert "Truncated at depth 1" in text
    assert "one of the nodes shown" in text


def test_the_default_diagram_carries_the_trace_notes(db: str) -> None:
    """Mermaid takes the walk's no-coverage path; its notes must still arrive (#581 review)."""
    text = cgis_trace_flow("app.main", db, depth=3, max_nodes=2)

    assert "Truncated at depth 1" in text
    assert "test node(s) left out" in text


def test_externals_shown_are_cut_before_internal_code(tmp_path: Path) -> None:
    """With include_external, `print` and `len` took slots from the steps (#581 review).

    The module is `zz` so its ids sort after `print` and `len`: by id alone, the
    externals would win the two free slots.
    """
    (tmp_path / "zz.py").write_text(
        "def main():\n    print(1)\n    len('')\n    zz_a()\n    zz_b()\n\n"
        "def zz_a():\n    pass\n\ndef zz_b():\n    pass\n",
        encoding="utf-8",
    )
    db = str(tmp_path / "graph.db")
    cgis_ingest(str(tmp_path), db)

    text = cgis_trace_flow(
        "zz.main", db, depth=1, output_format="text", include_external=True, max_nodes=3
    )

    assert "zz.zz_a" in text
    assert "zz.zz_b" in text
    assert "Truncated at depth 1" in text


def test_the_heading_counts_the_nodes(db: str) -> None:
    text = cgis_trace_flow("app.main", db, depth=3, output_format="text")

    assert "Execution flow for app.main (4 nodes):" in text


def test_impact_as_text_lists_callers_under_the_callee(db: str) -> None:
    text = cgis_analyze_impact("helpers.util", db, depth=2, output_format="text")

    assert text.startswith("Impact analysis for helpers.util (")
    assert "\n  app.step_a  app.py:" in text
    assert "\n    app.main  app.py:" in text


def test_an_unknown_format_names_text_among_the_choices(db: str) -> None:
    assert "'text'" in cgis_trace_flow("app.main", db, output_format="yaml")


def test_the_coverage_line_names_the_commonest_unresolved_calls() -> None:
    line = _coverage_text(
        TraversalCoverage(
            basis="unresolved_calls_made",
            calls_examined=10,
            calls_unresolved=3,
            top_unresolved=[("logger.info", 2), ("x.y", 1)],
        )
    )

    assert line.startswith("coverage: 3 of 10 calls unresolved")
    assert line.endswith("most often logger.info (2), x.y (1)")
