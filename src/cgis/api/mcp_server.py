"""MCP Server — exposes cgis graph operations as agentic tools.

STDIO transport: stdout is strictly reserved for JSON-RPC.
All logging goes to stderr via structlog.
"""

import dataclasses
import json
import sys
from pathlib import Path

import structlog
from mcp.server.fastmcp import FastMCP

from cgis.extractors.python_extractor import PythonExtractor
from cgis.extractors.typescript_extractor import TypeScriptExtractor
from cgis.pipeline import IngestionPipeline
from cgis.query.drift_service import analyze_drift
from cgis.query.engine import QueryEngine
from cgis.query.fqn import resolve_fqn
from cgis.query.mermaid import MermaidCompiler
from cgis.storage.sqlite_store import RAW_CALL_PREFIX, SQLiteStore

print("CGIS MCP Server starting…", file=sys.stderr)

logger = structlog.getLogger(__name__)

mcp: FastMCP = FastMCP("cgis-code-graph")

_EXTRACTORS = {
    ".py": PythonExtractor(),
    ".ts": TypeScriptExtractor(),
    ".tsx": TypeScriptExtractor(tsx=True),
}
_DEFAULT_DB = "graph.db"


def _resolution_error(fqn: str, candidates: list[str]) -> str:
    """Render a not-found / ambiguous FQN error for tool output."""
    if candidates:
        listing = "\n".join(f"- {c}" for c in candidates)
        return f"❌ Ambiguous FQN '{fqn}'. Candidates:\n{listing}"
    return f"❌ FQN not found in graph: {fqn}"


@mcp.tool()
def cgis_ingest(project_path: str, db_path: str = _DEFAULT_DB) -> str:
    """Scan a local directory, extract all symbols, resolve links, and build the graph DB.

    Use this to initialise or refresh the code knowledge graph for a project.
    Paths are normalised relative to the workspace root so the database is
    portable across machines.
    """
    pipeline = IngestionPipeline(_EXTRACTORS)
    try:
        with SQLiteStore(db_path) as store:
            nodes, _raw, resolved = pipeline.run(project_path, store=store)
    except Exception as exc:
        return f"❌ {exc}"

    logger.info("MCP ingest complete", nodes=len(nodes), edges=len(resolved), db=db_path)
    return (
        f"✅ Ingested: {project_path}\n"
        f"Nodes: {len(nodes)}\n"
        f"Resolved edges: {len(resolved)}\n"
        f"Graph stored in: {db_path}"
    )


@mcp.tool()
def cgis_trace_flow(fqn: str, db_path: str = _DEFAULT_DB, depth: int = 3) -> str:
    """Trace the execution call-graph starting from a specific FQN downwards.

    Returns a Mermaid.js diagram showing what the given function calls.
    Use ``cgis_ingest`` first if the database does not exist yet.
    """
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        with SQLiteStore(db_path) as store:
            res = resolve_fqn(store, fqn)
            if res.resolved is None:
                return _resolution_error(fqn, res.candidates)
            nodes, edges = QueryEngine(store).get_flow_graph(res.resolved, max_depth=depth)
    except Exception as exc:
        return f"❌ {exc}"

    if not nodes:
        return f"❌ FQN not found in graph: {fqn}"

    note = f"> Resolved '{fqn}' → '{res.resolved}'\n\n" if res.via_suffix else ""
    diagram = MermaidCompiler().compile(nodes, edges)
    return f"{note}### Execution flow for `{res.resolved}`:\n\n```mermaid\n{diagram}\n```"


@mcp.tool()
def cgis_analyze_impact(fqn: str, db_path: str = _DEFAULT_DB, depth: int = 3) -> str:
    """Analyse transitive upstream callers of a specific FQN.

    Returns a Mermaid.js diagram showing what would be impacted if this
    function changed. Answers "what breaks if I change X?".
    """
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        with SQLiteStore(db_path) as store:
            res = resolve_fqn(store, fqn)
            if res.resolved is None:
                return _resolution_error(fqn, res.candidates)
            nodes, edges = QueryEngine(store).get_impact_graph(res.resolved, max_depth=depth)
    except Exception as exc:
        return f"❌ {exc}"

    if not nodes:
        return f"❌ FQN not found in graph: {fqn}"

    note = f"> Resolved '{fqn}' → '{res.resolved}'\n\n" if res.via_suffix else ""
    diagram = MermaidCompiler().compile(nodes, edges)
    return f"{note}### Impact analysis for `{res.resolved}`:\n\n```mermaid\n{diagram}\n```"


@mcp.tool()
def cgis_get_structure(fqn: str, db_path: str = _DEFAULT_DB, depth: int = 2) -> str:
    """Show the class/module layout of a component by tracing outgoing edges.

    Returns a Mermaid.js diagram of the immediate call structure rooted at
    the given FQN. Useful for understanding how a class is organised.
    """
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        with SQLiteStore(db_path) as store:
            res = resolve_fqn(store, fqn)
            if res.resolved is None:
                return _resolution_error(fqn, res.candidates)
            nodes, edges = QueryEngine(store).get_flow_graph(res.resolved, max_depth=depth)
    except Exception as exc:
        return f"❌ {exc}"

    if not nodes:
        return f"❌ FQN not found in graph: {fqn}"

    note = f"> Resolved '{fqn}' → '{res.resolved}'\n\n" if res.via_suffix else ""
    diagram = MermaidCompiler().compile(nodes, edges)
    return f"{note}### Structure of `{res.resolved}`:\n\n```mermaid\n{diagram}\n```"


@mcp.tool()
def cgis_drift(
    db_path: str = _DEFAULT_DB,
    patterns_path: str = "docs/ontology/patterns.yaml",
    max_drift: float = 0.50,
) -> str:
    """Report per-domain architectural drift against declared ideal patterns.

    Returns JSON: ``any_critical`` verdict, per-domain reports and the
    observe-only quotient layer. Call after ``cgis_ingest`` to learn whether
    your edits pushed a domain past its drift tolerance.
    """
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    if not Path(patterns_path).exists():
        return f"❌ Patterns file not found: {patterns_path}"
    try:
        analysis = analyze_drift(db_path, patterns_path, max_drift=max_drift)
    except Exception as exc:
        return f"❌ {exc}"
    payload = {
        "any_critical": analysis.any_critical,
        "max_drift": max_drift,
        "domains": [dataclasses.asdict(r) for r in analysis.reports],
        "quotient": [{**dataclasses.asdict(r), "enforce": b.enforce} for b, r in analysis.quotient],
    }
    return json.dumps(payload, indent=2)


@mcp.tool()
def cgis_validate(db_path: str = _DEFAULT_DB, threshold: float = 0.30) -> str:
    """Report graph integrity as JSON: edge resolution stats + health verdict.

    Check this before trusting ``cgis_analyze_impact`` output — a high
    unresolved ratio means callers are missing from the graph.
    """
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        with SQLiteStore(db_path) as store:
            stats = store.get_edge_stats()
    except Exception as exc:
        return f"❌ {exc}"
    payload = {
        "total": stats.total,
        "resolved": stats.resolved,
        "stdlib": stats.stdlib,
        "external": stats.external,
        "unresolved": stats.unresolved,
        "unresolved_ratio": stats.unresolved_ratio,
        "top_unresolved": [[t.removeprefix(RAW_CALL_PREFIX), c] for t, c in stats.top_unresolved],
        "threshold": threshold,
        "healthy": stats.unresolved_ratio <= threshold,
    }
    return json.dumps(payload, indent=2)
