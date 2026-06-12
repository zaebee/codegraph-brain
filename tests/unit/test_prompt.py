"""Unit tests for the pure GraphRAG context compiler (#19)."""

from cgis.core.models import Node, NodeType
from cgis.query.prompt import compile_context


def _node(
    node_id: str,
    node_type: NodeType = NodeType.METHOD,
    file_path: str = "cgis/query/engine.py",
    start: int = 74,
    end: int = 94,
) -> Node:
    return Node(
        id=node_id,
        type=node_type,
        name=node_id.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=start,
        end_line=end,
    )


FOCUS = _node("cgis.query.engine.QueryEngine.get_impact_graph")
CLASS = _node("cgis.query.engine.QueryEngine", NodeType.CLASS, start=64, end=200)
SIBLINGS = [
    _node("cgis.query.engine.QueryEngine.get_flow_graph", start=96, end=116),
    _node("cgis.query.engine.QueryEngine._bfs_traverse", start=128, end=171),
]
CALLERS = [
    _node("cgis.cli.impact", NodeType.FUNCTION, "cgis/cli.py", 460, 498),
    _node(
        "cgis.api.mcp_server.cgis_analyze_impact",
        NodeType.FUNCTION,
        "cgis/api/mcp_server.py",
        136,
        160,
    ),
]
CALLEES = [_node("cgis.query.engine.QueryEngine._bfs_traverse", start=128, end=171)]


def _compile(**overrides: object) -> str:
    kwargs: dict[str, object] = {
        "focus": FOCUS,
        "source": "def get_impact_graph(self):\n    return self._bfs_traverse()\n",
        "class_node": CLASS,
        "siblings": SIBLINGS,
        "callers": CALLERS,
        "callees": CALLEES,
        "unresolved_callees": [],
    }
    kwargs.update(overrides)
    return compile_context(**kwargs)  # type: ignore[arg-type]


def test_header_carries_focal_type_and_line_range() -> None:
    """The root <context> tag carries the focal FQN, node type and file:range."""
    out = _compile()
    assert (
        '<context focal="cgis.query.engine.QueryEngine.get_impact_graph"'
        ' type="METHOD" file="cgis/query/engine.py:74-94">' in out
    )
    assert out.rstrip().endswith("</context>")


def test_source_is_fenced_inside_source_tag() -> None:
    """The focal source code is wrapped in a python code fence inside <source>."""
    out = _compile()
    assert "<source>" in out
    assert "</source>" in out
    assert "```python\ndef get_impact_graph(self):" in out


def test_callers_section_lists_fqn_type_and_location() -> None:
    """Callers render as FQN + type + basename:line under a noted <callers> tag."""
    out = _compile()
    assert "<callers" in out
    assert "- cgis.cli.impact (FUNCTION, cli.py:460)" in out
    assert "- cgis.api.mcp_server.cgis_analyze_impact (FUNCTION, mcp_server.py:136)" in out


def test_callees_section_lists_dependencies() -> None:
    """Callees render under a <callees> tag with their location."""
    out = _compile()
    assert "<callees" in out
    assert "- cgis.query.engine.QueryEngine._bfs_traverse (METHOD, engine.py:128)" in out


def test_class_context_lists_siblings_by_name() -> None:
    """The enclosing class and its sibling members are listed compactly."""
    out = _compile()
    assert '<class name="cgis.query.engine.QueryEngine" file="cgis/query/engine.py:64">' in out
    assert "- get_flow_graph (METHOD, engine.py:96)" in out
    assert "- _bfs_traverse (METHOD, engine.py:128)" in out


def test_standalone_function_renders_class_none() -> None:
    """A module-level function (no enclosing class) still renders a <class> tag."""
    out = _compile(class_node=None, siblings=[])
    assert "<class>none — module-level function</class>" in out


def test_no_callers_renders_explicit_none() -> None:
    """An entry-point with no upstream callers renders an explicit 'none' (issue Test 3)."""
    out = _compile(callers=[])
    assert "<callers" in out
    assert "none — no upstream callers" in out


def test_unresolved_callees_are_marked() -> None:
    """Unresolved raw_call targets are listed and flagged so the agent knows."""
    out = _compile(unresolved_callees=["mystery_helper"])
    assert "- mystery_helper (unresolved)" in out


def test_source_fence_widens_to_escape_inner_backticks() -> None:
    """Source containing a triple-backtick run gets a longer fence (anti-injection)."""
    out = _compile(source="x = '''```not a fence```'''\n")
    # The opening fence must be longer than any backtick run inside the source.
    assert "````python" in out


def test_domain_block_emitted_when_focus_carries_l3_data() -> None:
    """A node tagged with a domain / ontology class emits a <domain> boundary block."""
    focus = Node(
        id="app.auth.verify_token",
        type=NodeType.FUNCTION,
        name="verify_token",
        file_path="app/auth.py",
        start_line=10,
        end_line=20,
        domains=["auth"],
        ontology_class="DomainConcept.Auth",
    )
    out = compile_context(
        focus=focus,
        source="",
        class_node=None,
        siblings=[],
        callers=[],
        callees=[],
        unresolved_callees=[],
    )
    assert '<domain ontology_class="DomainConcept.Auth" domains="auth">' in out


def test_domain_block_omitted_without_l3_data() -> None:
    """Nodes with no L3 tags stay lean — no empty <domain> clutter."""
    out = _compile()  # FOCUS has neither domains nor ontology_class
    assert "<domain" not in out


def test_domain_block_omitted_for_structural_ontology_only() -> None:
    """A structural ontology_class fallback (e.g. 'Function') with no domains is NOT a boundary."""
    focus = _node("app.mod.func", NodeType.FUNCTION)
    focus = focus.model_copy(update={"ontology_class": "Function"})
    out = compile_context(
        focus=focus,
        source="",
        class_node=None,
        siblings=[],
        callers=[],
        callees=[],
        unresolved_callees=[],
    )
    assert "<domain" not in out
