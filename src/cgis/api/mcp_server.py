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

from cgis.core.models import Edge, Node, NodeType
from cgis.extractors.python_extractor import PythonExtractor
from cgis.extractors.typescript_extractor import TypeScriptExtractor
from cgis.pipeline import IngestionPipeline
from cgis.query.audit import audit_reachability
from cgis.query.context_service import build_context
from cgis.query.drift_service import analyze_drift
from cgis.query.engine import QueryEngine
from cgis.query.fqn import resolve_fqn
from cgis.query.graph_json import graph_to_json
from cgis.query.mermaid import MermaidCompiler
from cgis.query.metrics import DuckDBAnalyzer
from cgis.query.ontology_init import propose_ontology
from cgis.query.suggest_service import report_to_dict, suggest_packages
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


def _resolution_error(fqn: str, candidates: list[str], truncated: bool = False) -> str:
    """Render a not-found / ambiguous FQN error for tool output."""
    if candidates:
        listing = "\n".join(f"- {c}" for c in candidates)
        msg = f"❌ Ambiguous FQN '{fqn}'. Candidates:\n{listing}"
        if truncated:
            msg += "\n… (more matches exist; refine the name)"
        return msg
    return f"❌ FQN not found in graph: {fqn}"


def _blank_fqn_error(fqn: str) -> str | None:
    """Reject empty/whitespace FQN before touching the store (mirrors the #173 search guard)."""
    if not fqn.strip():
        return "❌ FQN cannot be empty or whitespace-only."
    return None


def _render_subgraph(
    output_format: str,
    root: str,
    note: str,
    title: str,
    nodes: list[Node],
    edges: list[Edge],
) -> str:
    """Render a traversal result as a Mermaid diagram or joinable JSON (#171).

    ``json`` returns the raw ``{root, nodes, edges}`` payload with real FQNs —
    no markdown wrapper — so an agent can parse and combine it across calls.
    ``mermaid`` (default) returns the human-readable diagram. Any other value
    is an explicit error rather than a silent fallback.
    """
    fmt = output_format.strip().lower()
    if fmt == "json":
        return json.dumps(graph_to_json(root, nodes, edges), indent=2)
    if fmt == "mermaid":
        diagram = MermaidCompiler().compile(nodes, edges)
        return f"{note}### {title} `{root}`:\n\n```mermaid\n{diagram}\n```"
    return f"❌ Unknown format '{output_format}'. Use 'mermaid' or 'json'."


@mcp.tool()
def cgis_ingest(project_path: str, db_path: str = _DEFAULT_DB, full_rebuild: bool = False) -> str:
    """Scan a local directory, extract all symbols, resolve links, and build the graph DB.

    Use this to initialise or refresh the code knowledge graph for a project.
    Paths are normalised relative to the workspace root so the database is
    portable across machines.

    By default the ingest is **incremental**: only changed/new files are
    re-scanned, and the summary reports both what changed this run and the
    whole-graph total. Set ``full_rebuild=True`` to re-scan every file and
    overwrite the database from scratch — use this to drop nodes for files that
    were deleted or renamed, which an incremental run leaves behind.
    """
    pipeline = IngestionPipeline(_EXTRACTORS)
    try:
        with SQLiteStore(db_path) as store:
            if full_rebuild:
                # Clear first, then run incrementally over an empty DB: this drops
                # deleted-file nodes, repopulates files_state correctly, and runs
                # uplift inside the pipeline (store provided) — all in one path.
                store.clear()
            _nodes, _raw, resolved = pipeline.run(project_path, store=store)
            total_nodes = store.get_node_count()
            total_edges = store.get_edge_count()
    except Exception as exc:
        return f"❌ {exc}"

    mode = "full rebuild" if full_rebuild else "incremental"
    logger.info(
        "MCP ingest complete",
        mode=mode,
        total_nodes=total_nodes,
        total_edges=total_edges,
        db=db_path,
    )
    lines = [f"✅ Ingested: {project_path} (mode: {mode})"]
    if not full_rebuild and not resolved:
        # Incremental no-op: an empty resolved set means no files changed. Say so
        # explicitly so the stable total below doesn't read as a shrunken graph (#192).
        lines.append("No files changed since the last ingest.")
    lines.append(f"Graph total: {total_nodes} nodes / {total_edges} edges")
    lines.append(f"Graph stored in: {db_path}")
    return "\n".join(lines)


@mcp.tool()
def cgis_trace_flow(
    fqn: str, db_path: str = _DEFAULT_DB, depth: int = 3, output_format: str = "mermaid"
) -> str:
    """Trace the execution call-graph starting from a specific FQN downwards.

    ``output_format="mermaid"`` (default) returns a human-readable diagram;
    ``"json"`` returns a joinable ``{root, nodes, edges}`` payload with real
    FQNs (not display hashes) for agent/CI use. Use ``cgis_ingest`` first if
    the database does not exist yet.
    """
    if blank := _blank_fqn_error(fqn):
        return blank
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        with SQLiteStore(db_path) as store:
            res = resolve_fqn(store, fqn)
            if res.resolved is None:
                return _resolution_error(fqn, res.candidates, res.truncated)
            nodes, edges = QueryEngine(store).get_flow_graph(res.resolved, max_depth=depth)
    except Exception as exc:
        return f"❌ {exc}"

    note = f"> Resolved '{fqn}' → '{res.resolved}'\n\n" if res.via_suffix else ""
    return _render_subgraph(output_format, res.resolved, note, "Execution flow for", nodes, edges)


@mcp.tool()
def cgis_analyze_impact(
    fqn: str, db_path: str = _DEFAULT_DB, depth: int = 3, output_format: str = "mermaid"
) -> str:
    """Analyse transitive upstream callers of a specific FQN.

    Answers "what breaks if I change X?". ``output_format="mermaid"`` (default)
    returns a diagram; ``"json"`` returns a joinable ``{root, nodes, edges}``
    payload with real FQNs — letting an agent compute set differences (e.g.
    "which route handlers never reach ``verify_ownership``?") directly.
    """
    if blank := _blank_fqn_error(fqn):
        return blank
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        with SQLiteStore(db_path) as store:
            res = resolve_fqn(store, fqn)
            if res.resolved is None:
                return _resolution_error(fqn, res.candidates, res.truncated)
            nodes, edges = QueryEngine(store).get_impact_graph(res.resolved, max_depth=depth)
    except Exception as exc:
        return f"❌ {exc}"

    note = f"> Resolved '{fqn}' → '{res.resolved}'\n\n" if res.via_suffix else ""
    return _render_subgraph(output_format, res.resolved, note, "Impact analysis for", nodes, edges)


@mcp.tool()
def cgis_get_structure(
    fqn: str, db_path: str = _DEFAULT_DB, depth: int = 2, output_format: str = "mermaid"
) -> str:
    """Show the structural layout (CONTAINS/DECLARES) of a module or class.

    Traverses only containment edges — no call-graph noise — matching the CLI
    ``structure`` command. ``output_format="mermaid"`` (default) returns a
    diagram of the hierarchy rooted at the given FQN; ``"json"`` returns the
    joinable ``{root, nodes, edges}`` payload with real FQNs.
    """
    if blank := _blank_fqn_error(fqn):
        return blank
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        with SQLiteStore(db_path) as store:
            res = resolve_fqn(store, fqn)
            if res.resolved is None:
                return _resolution_error(fqn, res.candidates, res.truncated)
            nodes, edges = QueryEngine(store).get_structural_graph(res.resolved, max_depth=depth)
    except Exception as exc:
        return f"❌ {exc}"

    note = f"> Resolved '{fqn}' → '{res.resolved}'\n\n" if res.via_suffix else ""
    return _render_subgraph(output_format, res.resolved, note, "Structure of", nodes, edges)


@mcp.tool()
def cgis_drift(
    db_path: str = _DEFAULT_DB,
    patterns_path: str = "docs/ontology/patterns.yaml",
    max_drift: float = 0.50,
    profile: str | None = None,
    max_residual: float = 0.45,
) -> str:
    """Report per-domain architectural drift against declared ideal patterns.

    Returns JSON: ``any_critical`` verdict, per-domain reports (each carrying a
    ``fit`` block — nearest alphabet template + residual + good/weak/none band),
    the observe-only quotient layer, and ``coverage`` (graph prefixes bound by no
    domain). Call after ``cgis_ingest`` to learn whether your edits pushed a
    domain past its drift tolerance.

    ``max_drift`` is now the default tolerance only for domains that omit
    ``drift_tolerance`` — it no longer caps domains that declare their own
    (see #170).

    ``profile``: when set, score only domains with this profile (plus
    profile-less ones). Use when your patterns.yaml mixes languages but the
    graph holds one language — avoids false EMPTY reports for other-language
    domains that would otherwise fail the gate.

    ``max_residual``: a domain whose nearest template is farther than this gets
    ``fit.band = "none"`` ("no template fits") — a grab-bag module or an
    alphabet gap, independent of drift tolerance (#177).
    """
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    if not Path(patterns_path).exists():
        return f"❌ Patterns file not found: {patterns_path}"
    try:
        analysis = analyze_drift(
            db_path,
            patterns_path,
            max_drift=max_drift,
            profile=profile,
            max_residual=max_residual,
        )
        payload = {
            "any_critical": analysis.any_critical,
            "max_drift": max_drift,
            "domains": [
                {**dataclasses.asdict(r), "tangle_ratio": round(r.actual.tangle_ratio, 4)}
                for r in analysis.reports
            ],
            "quotient": [
                {
                    **dataclasses.asdict(r),
                    "enforce": b.enforce,
                    "tangle_ratio": round(r.actual.tangle_ratio, 4),
                }
                for b, r in analysis.quotient
            ],
            "coverage": analysis.coverage,
        }
        return json.dumps(payload, indent=2)
    except Exception as exc:
        return f"❌ {exc}"


@mcp.tool()
def cgis_suggest_packages(
    db_path: str = _DEFAULT_DB,
    prefix: str = "",
    with_calls: bool = False,
    min_q: float = 0.35,
) -> str:
    """Suggest sub-package boundaries for a package from its dependency communities.

    Returns JSON: modularity_q, divergence, direction (under/over/matched),
    verdict (split/consolidate/aligned/leave/borderline/no_signal), the detected
    communities (id + member files), the cross-community bridge edges (cost of
    splitting), and the thresholds used. Default layer is IMPORTS; set
    ``with_calls`` for the combined import+call graph. Run ``cgis_ingest`` first.

    A mis-rooted graph (import targets resolve to no internal file) returns
    ``no_signal`` with a diagnostic note rather than a silent clean verdict.
    """
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        report = suggest_packages(db_path, prefix, with_calls=with_calls, min_q=min_q)
    except Exception as exc:
        return f"❌ Error during suggest-packages: {exc}"
    return json.dumps(report_to_dict(report), indent=2)


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
        payload = {
            "total": stats.total,
            "resolved": stats.resolved,
            "stdlib": stats.stdlib,
            "external": stats.external,
            "unresolved": stats.unresolved,
            "unresolved_ratio": stats.unresolved_ratio,
            "top_unresolved": [
                [t.removeprefix(RAW_CALL_PREFIX), c] for t, c in stats.top_unresolved
            ],
            "threshold": threshold,
            "healthy": stats.unresolved_ratio <= threshold,
        }
        return json.dumps(payload, indent=2)
    except Exception as exc:
        return f"❌ {exc}"


@mcp.tool()
def cgis_find_symbol(
    query: str,
    db_path: str = _DEFAULT_DB,
    kind: str | None = None,
    fqn_prefix: str | None = None,
    limit: int = 20,
) -> str:
    """Resolve a partial symbol name to candidate FQNs (substring match, ranked).

    Call this BEFORE ``cgis_trace_flow`` / ``cgis_analyze_impact`` /
    ``cgis_get_structure`` when you know a short name (e.g.
    ``get_reservation_prices``) but not its full FQN — it removes the
    read-the-file-first guesswork. Returns JSON ``[{fqn, name, type, file,
    line}]`` ranked exact > prefix > substring. ``kind`` filters by node type
    (FUNCTION / METHOD / CLASS / …); ``fqn_prefix`` scopes the search.
    """
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        # Blank/whitespace kind means "no type filter", not "match nothing".
        kinds = (kind.strip().upper(),) if kind and kind.strip() else ()
        with SQLiteStore(db_path) as store:
            matches = store.search_nodes(query, kinds=kinds, fqn_prefix=fqn_prefix, limit=limit)
    except Exception as exc:
        return f"❌ {exc}"
    payload = [
        {
            "fqn": n.id,
            "name": n.name,
            "type": n.type.value,
            "file": n.file_path,
            "line": n.start_line,
        }
        for n in matches
    ]
    return json.dumps(payload, indent=2)


@mcp.tool()
def cgis_init_ontology(
    db_path: str = _DEFAULT_DB,
    margin: float = 0.03,
    min_nodes: int = 10,
    depth: int | None = None,
) -> str:
    """Propose a starter patterns.yaml from the measured graph (read-only).

    Returns the YAML text — save it yourself (e.g. to patterns.yaml), review
    the proposed labels, then run ``cgis_drift`` with it. Tolerances are the
    measured scores plus ``margin``: a baseline to ratchet down, not a verdict.

    No files are written; the caller decides where to persist the output.
    """
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        return propose_ontology(db_path, margin=margin, min_nodes=min_nodes, depth=depth)
    except Exception as e:  # translate errors to the ❌-message medium
        return f"❌ Error proposing ontology: {e}"


@mcp.tool()
def cgis_context(
    fqn: str, db_path: str = _DEFAULT_DB, depth: int = 1, source_root: str = ""
) -> str:
    """Compile an agent-facing GraphRAG context package for a focal FQN (#19).

    Returns an XML-tagged prompt — the focal node's source, its enclosing class,
    its architectural domain boundary, direct callers (upstream ripple) and
    callees (downstream dependencies) — meant to be injected into your context
    window in place of raw file dumps. Far more token-efficient than reading
    whole files, and structured so boundaries stay unambiguous.

    Use ``cgis_ingest`` first if the database does not exist. ``source_root``
    locates source files on disk when the graph was ingested from a
    sub-directory (e.g. ``"src"`` after ``cgis ingest ./src``); without it the
    ``<source>`` block degrades gracefully to "unavailable".
    """
    if blank := _blank_fqn_error(fqn):
        return blank
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        with SQLiteStore(db_path) as store:
            res = resolve_fqn(store, fqn)
            if res.resolved is None:
                return _resolution_error(fqn, res.candidates, res.truncated)
            payload = build_context(store, res.resolved, depth=depth, source_root=source_root)
    except Exception as exc:
        return f"❌ {exc}"

    note = f"> Resolved '{fqn}' → '{res.resolved}'\n\n" if res.via_suffix else ""
    return note + payload


@mcp.tool()
def cgis_metrics(
    db_path: str = _DEFAULT_DB, limit: int = 10, exclude: list[str] | None = None
) -> str:
    """Whole-graph architectural metrics — coupling bottlenecks + God classes (#16).

    Returns JSON ``{bottlenecks, god_classes, critical}`` computed with vectorized
    DuckDB aggregations over the whole graph (fan-in/fan-out coupling,
    declared-member counts, PageRank) — the global "what are the hotspots?" view
    that complements the node-local trace/impact/context tools. Requires the
    optional ``duckdb`` extra; an unavailable dependency is reported as a normal
    ❌ message.

    ``exclude`` drops any node whose FQN contains one of the given dot-segments
    (e.g. ``["tests"]`` removes both ``tests.*`` and ``domains.*.tests.*``) so
    test/vendor scaffolding stays out of the rankings.
    """
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        with DuckDBAnalyzer(db_path) as analyzer:
            report = analyzer.architecture_report(
                bottleneck_limit=limit,
                god_limit=limit,
                critical_limit=limit,
                exclude=exclude or [],
            )
    except Exception as exc:
        return f"❌ {exc}"

    return json.dumps(report.model_dump(), indent=2)


@mcp.tool()
def cgis_audit_reachability(
    target: str,
    db_path: str = _DEFAULT_DB,
    from_type: str | None = None,
    from_prefix: str | None = None,
    depth: int = 5,
) -> str:
    """Reachability/authorization audit — which sources never reach a checkpoint (#172).

    The headline use is **IDOR/authz coverage**: list every route handler that does
    NOT transitively reach an ownership check. Reachability follows behavioral edges
    (CALLS *and* FastAPI ``Depends()`` DEPENDS_ON), so a guard wired via DI counts.

    Select sources with ``from_type`` (a NodeType like ``ROUTE_HANDLER`` /
    ``API_ENDPOINT`` / ``FUNCTION``) and/or ``from_prefix`` (FQN prefix) — at least
    one is required. Returns JSON ``{target, covered, gaps}`` where each gap carries
    ``fqn``/``file``/``line``. Generalizes to validators, event tracking, or
    service-layer-boundary rules by pointing ``target`` at the required node.
    """
    if blank := _blank_fqn_error(target):
        return blank
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    # Defensive against agents passing JSON null for omitted optional params.
    node_type: NodeType | None = None
    if from_type and from_type.strip():
        try:
            node_type = NodeType(from_type.strip().upper())
        except ValueError:
            valid = ", ".join(t.value for t in NodeType)
            return f"❌ Unknown node type '{from_type}'. Valid: {valid}"
    prefix = (from_prefix or "").strip() or None
    if node_type is None and prefix is None:
        return "❌ Provide from_type or from_prefix to select audited sources."
    try:
        with SQLiteStore(db_path) as store:
            res = resolve_fqn(store, target)
            if res.resolved is None:
                return _resolution_error(target, res.candidates, res.truncated)
            result = audit_reachability(
                store,
                target_fqn=res.resolved,
                from_type=node_type,
                from_prefix=prefix,
                max_depth=depth,
            )
    except Exception as exc:
        return f"❌ {exc}"

    note = f"> Resolved '{target}' → '{res.resolved}'\n\n" if res.via_suffix else ""
    return note + json.dumps(dataclasses.asdict(result), indent=2)
