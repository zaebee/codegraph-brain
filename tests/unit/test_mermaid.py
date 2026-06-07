"""Unit test cases for mermaid queries."""

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.mermaid import MermaidCompiler


def _make_node(node_id: str, node_type: NodeType = NodeType.FUNCTION) -> Node:
    return Node(
        id=node_id,
        type=node_type,
        name=node_id.rsplit(":", maxsplit=1)[-1],
        file_path="src/mod.py",
        start_line=1,
        end_line=5,
        language="python",
    )


def _make_edge(source: str, target: str) -> Edge:
    return Edge(id=f"{source}->{target}", source=source, target=target, type=EdgeType.CALLS)


def test_compile_id_normalization_and_func_styling() -> None:
    """Complex FQNs are hashed to safe IDs; labels preserve name and location."""
    nodes = [
        _make_node("src/cgis/cli.py:ingest"),
        _make_node("src/cgis/resolver/engine.py:ResolverEngine"),
    ]
    edges = [_make_edge("src/cgis/cli.py:ingest", "src/cgis/resolver/engine.py:ResolverEngine")]

    mermaid_code = MermaidCompiler().compile(nodes, edges)

    # Raw FQNs must not appear as edge endpoints — only safe hashed IDs
    assert "src/cgis/cli.py:ingest -->" not in mermaid_code
    # Original names preserved as labels
    assert '"ingest (mod.py:1)"' in mermaid_code
    # Class definitions injected
    assert "classDef funcNode" in mermaid_code
    assert ":::funcNode" in mermaid_code


def test_compile_class_node_styling() -> None:
    """CLASS nodes receive the classNode style."""
    nodes = [_make_node("mod.py:MyClass", NodeType.CLASS)]
    mermaid_code = MermaidCompiler().compile(nodes, [])
    assert ":::classNode" in mermaid_code


def test_compile_method_node_styling() -> None:
    """METHOD nodes receive the methodNode style."""
    nodes = [_make_node("mod.py:MyClass.do_thing", NodeType.METHOD)]
    mermaid_code = MermaidCompiler().compile(nodes, [])
    assert ":::methodNode" in mermaid_code


def test_compile_default_node_styling() -> None:
    """Node types without a dedicated style fall back to defaultNode."""
    nodes = [_make_node("mod.py", NodeType.FILE)]
    mermaid_code = MermaidCompiler().compile(nodes, [])
    assert ":::defaultNode" in mermaid_code


def test_compile_raw_call_node_styled_as_unresolved() -> None:
    """Nodes with a raw_call: ID are styled as unresolvedNode even when in the nodes list."""
    nodes = [_make_node("raw_call:print")]
    mermaid_code = MermaidCompiler().compile(nodes, [])
    assert ":::unresolvedNode" in mermaid_code


def test_compile_phantom_source_node() -> None:
    """An edge whose source is not in the nodes list renders the source as a defaultNode inline."""
    nodes = [_make_node("mod.py:callee")]
    edges = [_make_edge("mod.py:ghost_caller", "mod.py:callee")]

    mermaid_code = MermaidCompiler().compile(nodes, edges)

    # The ghost caller must appear with the fallback defaultNode style
    assert "mod.py:ghost_caller" in mermaid_code
    assert ":::defaultNode" in mermaid_code


def test_compile_phantom_target_with_raw_call_prefix() -> None:
    """An unresolved raw_call: target renders as unresolvedNode with the prefix stripped."""
    nodes = [_make_node("mod.py:caller")]
    edges = [_make_edge("mod.py:caller", "raw_call:print")]

    mermaid_code = MermaidCompiler().compile(nodes, edges)

    # Prefix must be stripped from the visible label
    assert '"print"' in mermaid_code
    assert "raw_call:print" not in mermaid_code.split("\n")[0]  # not as a bare ID
    assert ":::unresolvedNode" in mermaid_code


def test_compile_empty_graph() -> None:
    """Compiling an empty graph returns a valid (but node-free) Mermaid header."""
    mermaid_code = MermaidCompiler().compile([], [])
    assert mermaid_code.startswith("graph TD")
    assert "classDef" in mermaid_code


def test_compile_output_is_deterministic() -> None:
    """Compiling the same graph twice produces identical output (stable ID hashing)."""
    nodes = [_make_node("src/cgis/cli.py:some_function")]
    edges = [_make_edge("src/cgis/cli.py:some_function", "src/cgis/other.py:helper")]
    compiler = MermaidCompiler()
    assert compiler.compile(nodes, edges) == compiler.compile(nodes, edges)
    # Hashed IDs must be alphanumeric — no raw slashes or colons in node declarations
    output = compiler.compile(nodes, edges)
    for line in output.splitlines():
        if "-->|" in line:
            parts = line.strip().split(" -->|")
            assert "/" not in parts[0], f"Raw path in edge source ID: {parts[0]}"
