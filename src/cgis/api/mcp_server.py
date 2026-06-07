"""MCP Server — exposes cgis graph operations as agentic tools.

STDIO transport: stdout is strictly reserved for JSON-RPC.
All logging goes to stderr via structlog.
"""

import sys

import structlog
from mcp.server.fastmcp import FastMCP

from cgis.extractors.python_extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.query.engine import QueryEngine
from cgis.query.mermaid import MermaidCompiler
from cgis.storage.sqlite_store import SQLiteStore

print("CGIS MCP Server starting…", file=sys.stderr)

logger = structlog.getLogger(__name__)

mcp: FastMCP = FastMCP("cgis-code-graph")

_EXTRACTORS = {".py": PythonExtractor()}


@mcp.tool()
def cgis_ingest(project_path: str, db_path: str = "graph.db") -> str:
    """Scan a local directory, extract all symbols, resolve links, and build the graph DB.

    Use this to initialise or refresh the code knowledge graph for a project.
    Paths are normalised relative to the workspace root so the database is
    portable across machines.
    """
    pipeline = IngestionPipeline(_EXTRACTORS)
    try:
        nodes, _raw, resolved = pipeline.run(project_path)
    except (FileNotFoundError, NotADirectoryError) as exc:
        return f"❌ {exc}"

    with SQLiteStore(db_path) as store:
        store.save_graph(nodes, resolved, overwrite=True)

    logger.info("MCP ingest complete", nodes=len(nodes), edges=len(resolved), db=db_path)
    return (
        f"✅ Ingested: {project_path}\n"
        f"Nodes: {len(nodes)}\n"
        f"Resolved edges: {len(resolved)}\n"
        f"Graph stored in: {db_path}"
    )


@mcp.tool()
def cgis_trace_flow(fqn: str, db_path: str = "graph.db", depth: int = 3) -> str:
    """Trace the execution call-graph starting from a specific FQN downwards.

    Returns a Mermaid.js diagram showing what the given function calls.
    Use ``cgis_ingest`` first if the database does not exist yet.
    """
    try:
        with SQLiteStore(db_path) as store:
            nodes, edges = QueryEngine(store).get_flow_graph(fqn, max_depth=depth)
    except RuntimeError as exc:
        return f"❌ {exc}"

    if not nodes:
        return f"❌ FQN not found in graph: {fqn}"

    diagram = MermaidCompiler().compile(nodes, edges)
    return f"### Execution flow for `{fqn}`:\n\n```mermaid\n{diagram}\n```"


@mcp.tool()
def cgis_analyze_impact(fqn: str, db_path: str = "graph.db", depth: int = 3) -> str:
    """Analyse transitive upstream callers of a specific FQN.

    Returns a Mermaid.js diagram showing what would be impacted if this
    function changed. Answers "what breaks if I change X?".
    """
    try:
        with SQLiteStore(db_path) as store:
            nodes, edges = QueryEngine(store).get_impact_graph(fqn, max_depth=depth)
    except RuntimeError as exc:
        return f"❌ {exc}"

    if not nodes:
        return f"❌ FQN not found in graph: {fqn}"

    diagram = MermaidCompiler().compile(nodes, edges)
    return f"### Impact analysis for `{fqn}`:\n\n```mermaid\n{diagram}\n```"


@mcp.tool()
def cgis_get_structure(fqn: str, db_path: str = "graph.db", depth: int = 2) -> str:
    """Show the class/module layout of a component by tracing outgoing edges.

    Returns a Mermaid.js diagram of the immediate call structure rooted at
    the given FQN. Useful for understanding how a class is organised.
    """
    try:
        with SQLiteStore(db_path) as store:
            nodes, edges = QueryEngine(store).get_flow_graph(fqn, max_depth=depth)
    except RuntimeError as exc:
        return f"❌ {exc}"

    if not nodes:
        return f"❌ FQN not found in graph: {fqn}"

    diagram = MermaidCompiler().compile(nodes, edges)
    return f"### Structure of `{fqn}`:\n\n```mermaid\n{diagram}\n```"
