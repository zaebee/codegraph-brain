"""Unit test cases for mermaid queries."""

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.query.mermaid import MermaidCompiler, _fqn_slug, _node_slug


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
    """Complex FQNs become readable slug IDs; labels preserve name and location."""
    ingest = _make_node_in_file("src.cgis.cli.ingest", "src/cgis/cli.py")
    resolver = _make_node_in_file(
        "src.cgis.resolver.engine.ResolverEngine", "src/cgis/resolver/engine.py"
    )
    edges = [_make_edge("src.cgis.cli.ingest", "src.cgis.resolver.engine.ResolverEngine")]

    mermaid_code = MermaidCompiler().compile([ingest, resolver], edges)

    # Edge endpoints use readable slug IDs, not raw FQNs and not md5 hashes
    assert "src.cgis.cli.ingest -->" not in mermaid_code
    assert "n_" not in mermaid_code
    assert "cli_ingest -->|" in mermaid_code
    assert "engine_ResolverEngine" in mermaid_code
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


def test_compile_unresolved_internal_virtual_node_styled_as_unresolved() -> None:
    """Virtual INTERNAL nodes (file_path=EXTERNAL, namespace=INTERNAL) render as unresolvedNode."""
    node = Node(
        id="self.unknown_method",
        type=NodeType.FUNCTION,
        name="unknown_method",
        file_path="EXTERNAL",
        start_line=0,
        end_line=0,
        namespace=NodeNamespace.INTERNAL,
    )
    mermaid_code = MermaidCompiler().compile([node], [])
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


def test_compile_phantom_target_without_raw_call_uses_default_style() -> None:
    """Phantom target without raw_call: prefix gets defaultNode, not unresolvedNode."""
    nodes = [_make_node("mod.py:caller")]
    edges = [_make_edge("mod.py:caller", "mod.py:deep_callee")]

    mermaid_code = MermaidCompiler().compile(nodes, edges)

    assert '"mod.py:deep_callee"' in mermaid_code
    # Must NOT be marked as unresolved — the node is known, just not in the subgraph
    node_lines = [
        line for line in mermaid_code.splitlines() if "deep_callee" in line and "-->" not in line
    ]
    assert node_lines
    assert ":::defaultNode" in node_lines[0]
    assert ":::unresolvedNode" not in node_lines[0]


def test_compile_empty_graph() -> None:
    """Compiling an empty graph returns a valid (but node-free) Mermaid header."""
    mermaid_code = MermaidCompiler().compile([], [])
    assert mermaid_code.startswith("graph TD")
    assert "classDef" in mermaid_code


def test_compile_escapes_angle_brackets_in_node_label() -> None:
    """Names like <lambda> or <listcomp> are escaped so Mermaid does not treat them as HTML tags."""
    nodes = [
        Node(
            id="mod.py:<lambda>",
            type=NodeType.FUNCTION,
            name="<lambda>",
            file_path="src/mod.py",
            start_line=1,
            end_line=1,
        )
    ]
    mermaid_code = MermaidCompiler().compile(nodes, [])

    assert "&lt;lambda&gt;" in mermaid_code
    assert "<lambda>" not in mermaid_code


def test_compile_escapes_angle_brackets_in_phantom_nodes() -> None:
    """Phantom source/target FQNs with < or > are escaped correctly."""
    nodes = [_make_node("mod.py:caller")]
    edges = [_make_edge("mod.py:<lambda>", "mod.py:caller")]

    mermaid_code = MermaidCompiler().compile(nodes, edges)

    assert "&lt;lambda&gt;" in mermaid_code
    assert "<lambda>" not in mermaid_code


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


# --- Subgraph grouping tests ---


def _make_node_in_file(
    node_id: str, file_path: str, node_type: NodeType = NodeType.FUNCTION
) -> Node:
    return Node(
        id=node_id,
        type=node_type,
        name=node_id.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=5,
        language="python",
    )


def test_compile_single_file_renders_flat() -> None:
    """Nodes from a single file produce no subgraph blocks."""
    nodes = [
        _make_node_in_file("cgis.pipeline.run", "src/cgis/pipeline.py"),
        _make_node_in_file("cgis.pipeline.ingest", "src/cgis/pipeline.py"),
    ]
    output = MermaidCompiler().compile(nodes, [])
    assert "subgraph" not in output


def test_compile_multiple_files_renders_subgraphs() -> None:
    """Nodes from different files are wrapped in subgraph blocks."""
    nodes = [
        _make_node_in_file("cgis.pipeline.run", "src/cgis/pipeline.py"),
        _make_node_in_file("cgis.cli.ingest", "src/cgis/cli.py"),
    ]
    output = MermaidCompiler().compile(nodes, [])
    assert "subgraph" in output
    assert "pipeline.py" in output
    assert "cli.py" in output


def test_compile_subgraph_contains_its_nodes() -> None:
    """Each subgraph block wraps only the nodes belonging to that file."""
    pipeline_node = _make_node_in_file("cgis.pipeline.run", "src/cgis/pipeline.py")
    cli_node = _make_node_in_file("cgis.cli.ingest", "src/cgis/cli.py")
    nodes = [pipeline_node, cli_node]
    output = MermaidCompiler().compile(nodes, [])

    pipeline_id = _node_slug(pipeline_node)
    cli_id = _node_slug(cli_node)
    lines = output.splitlines()

    # Each node must appear inside its subgraph block (between subgraph...end)
    def node_is_inside_subgraph(node_safe_id: str, subgraph_label: str) -> bool:
        inside = False
        for line in lines:
            if "subgraph" in line and subgraph_label in line:
                inside = True
            if inside and node_safe_id in line:
                return True
            if inside and line.strip() == "end":
                inside = False
        return False

    assert node_is_inside_subgraph(pipeline_id, "pipeline.py")
    assert node_is_inside_subgraph(cli_id, "cli.py")


def test_compile_edges_between_subgraphs_are_rendered() -> None:
    """Cross-file edges appear in the output regardless of subgraph grouping."""
    src = _make_node_in_file("cgis.cli.ingest", "src/cgis/cli.py")
    dst = _make_node_in_file("cgis.pipeline.run", "src/cgis/pipeline.py")
    edge = _make_edge("cgis.cli.ingest", "cgis.pipeline.run")
    output = MermaidCompiler().compile([src, dst], [edge])

    src_id = _node_slug(src)
    dst_id = _node_slug(dst)
    assert f"{src_id} -->|" in output
    assert dst_id in output


# --- Coverage gap tests: namespace node styles ---


def test_compile_stdlib_node_uses_stdlib_style() -> None:
    """STDLIB-namespace nodes get the stdlibNode CSS class."""
    node = Node(
        id="os.path.join",
        type=NodeType.FUNCTION,
        name="join",
        file_path="EXTERNAL",
        start_line=0,
        end_line=0,
        namespace=NodeNamespace.STDLIB,
    )
    caller = _make_node("mod.fn")
    edge = _make_edge("mod.fn", "os.path.join")
    output = MermaidCompiler().compile([caller, node], [edge])
    assert "stdlibNode" in output


def test_compile_external_node_uses_external_style() -> None:
    """EXTERNAL-namespace nodes get the externalNode CSS class."""
    node = Node(
        id="fastapi.FastAPI",
        type=NodeType.CLASS,
        name="FastAPI",
        file_path="EXTERNAL",
        start_line=0,
        end_line=0,
        namespace=NodeNamespace.EXTERNAL,
    )
    caller = _make_node("mod.fn")
    edge = _make_edge("mod.fn", "fastapi.FastAPI")
    output = MermaidCompiler().compile([caller, node], [edge])
    assert "externalNode" in output


def test_compile_unknown_namespace_uses_unresolved_style() -> None:
    """UNKNOWN-namespace nodes get the unresolvedNode CSS class."""
    node = Node(
        id="mystery.func",
        type=NodeType.FUNCTION,
        name="func",
        file_path="EXTERNAL",
        start_line=0,
        end_line=0,
        namespace=NodeNamespace.UNKNOWN,
    )
    caller = _make_node("mod.fn")
    edge = _make_edge("mod.fn", "mystery.func")
    output = MermaidCompiler().compile([caller, node], [edge])
    assert "unresolvedNode" in output


# --- #210: human/AI-readable node ids (full-path slug, not md5 hashes) ---


def test_node_slug_peels_module_prefix() -> None:
    """Internal node → '<file_stem>_<Class>_<method>' slug (#210)."""
    node = _make_node_in_file("src.cgis.pipeline.IngestionPipeline.run", "src/cgis/pipeline.py")
    assert _node_slug(node) == "pipeline_IngestionPipeline_run"


def test_node_slug_module_node_is_stem() -> None:
    """A module/file node (id == module fqn) slugs to its bare stem."""
    node = _make_node_in_file("src.cgis.pipeline", "src/cgis/pipeline.py", NodeType.MODULE)
    assert _node_slug(node) == "pipeline"


def test_node_slug_survives_source_root_mismatch() -> None:
    """Ids rooted differently from the file path still keep the file_stem prefix (#213 review).

    Ingesting ``./src`` yields ids like ``cgis.pipeline.X`` while the file path
    derives ``src.cgis.pipeline`` — the longest-suffix match must still prefix
    the stem, not fall back and drop it.
    """
    node = _make_node_in_file("cgis.pipeline.IngestionPipeline.run", "src/cgis/pipeline.py")
    assert _node_slug(node) == "pipeline_IngestionPipeline_run"


def test_compile_emits_readable_slug_not_hash() -> None:
    """compile() renders readable slug ids, never n_<md5> hashes (#210)."""
    node = _make_node_in_file("src.cgis.pipeline.IngestionPipeline.run", "src/cgis/pipeline.py")
    out = MermaidCompiler().compile([node], [])
    assert "pipeline_IngestionPipeline_run" in out
    assert "n_" not in out  # no md5 hash ids anywhere


def test_compile_disambiguates_colliding_slugs() -> None:
    """Two nodes with the same base slug → second gets a _2 suffix (#210)."""
    a = _make_node_in_file("src.a.helpers.run", "src/a/helpers.py")
    b = _make_node_in_file("src.b.helpers.run", "src/b/helpers.py")
    out = MermaidCompiler().compile([a, b], [])
    assert "helpers_run" in out
    assert "helpers_run_2" in out


def test_fqn_slug_guards_reserved_word_and_leading_digit() -> None:
    """Bare ids that collide with Mermaid keywords or start with a digit are prefixed."""
    assert _fqn_slug("raw_call:end") == "id_end"  # 'end' closes a subgraph — must not be bare
    assert _fqn_slug("9lives") == "id_9lives"  # leading digit is not a valid id start


def test_compile_phantom_raw_call_is_readable_and_unresolved() -> None:
    """Unresolved raw_call target renders a readable slug, still unresolved-styled (#210)."""
    src = _make_node_in_file("src.app.mod.caller", "src/app/mod.py")
    edge = _make_edge("src.app.mod.caller", "raw_call:mystery")
    out = MermaidCompiler().compile([src], [edge])
    assert "|CALLS| mystery" in out
    assert "unresolvedNode" in out
