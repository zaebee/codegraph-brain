"""MCP Server — exposes cgis graph operations as agentic tools.

STDIO transport: stdout is strictly reserved for JSON-RPC.
All logging goes to stderr via structlog.
"""

import dataclasses
import json
import sys
from pathlib import Path
from typing import Annotated, Any

import structlog
from mcp.server.mcpserver import MCPServer
from pydantic import Field

from cgis import __version__
from cgis.core.coverage import TraversalCoverage
from cgis.core.freshness import Freshness, FreshnessState
from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.extractors.python_extractor import PythonExtractor
from cgis.extractors.typescript_extractor import TypeScriptExtractor
from cgis.pipeline import IngestionPipeline
from cgis.query.analysis.overview import DEFAULT_DEPTH, DEFAULT_LIMIT, build_overview
from cgis.query.analysis.suggest_service import report_to_dict, suggest_packages
from cgis.query.context.audit import audit_reachability
from cgis.query.context.context_service import build_context
from cgis.query.context.orphans import find_orphan_classes
from cgis.query.drift.drift_service import analyze_drift
from cgis.query.drift.fractal import analyze_fractal_db
from cgis.query.drift.ontology_init import propose_ontology
from cgis.query.engine import BEHAVIORAL_EDGE_TYPES, QueryEngine
from cgis.query.fqn import resolve_fqn
from cgis.query.render.graph_json import graph_to_json
from cgis.query.render.mermaid import MermaidCompiler
from cgis.query.render.metrics import DuckDBAnalyzer
from cgis.storage.sqlite_store import RAW_CALL_PREFIX, SQLiteStore

print("CGIS MCP Server starting…", file=sys.stderr)

logger = structlog.getLogger(__name__)

mcp: MCPServer = MCPServer("cgis-code-graph", version=__version__)

_EXTRACTORS = {
    ".py": PythonExtractor(),
    ".ts": TypeScriptExtractor(),
    ".tsx": TypeScriptExtractor(tsx=True),
}
_DEFAULT_DB = "graph.db"

# Agents choose arguments from the JSON schema, and the SDK does not copy
# docstring prose into it — each parameter's meaning has to be declared here.
DbPath = Annotated[
    str,
    Field(
        description="SQLite graph built by cgis_ingest. A relative path resolves against the "
        "MCP server's working directory, not the agent's — prefer an absolute path."
    ),
]
Fqn = Annotated[
    str,
    Field(
        description="Fully qualified name, e.g. pkg.module.Class.method. A unique dot-boundary "
        "suffix also resolves; an ambiguous one returns candidates. Use cgis_find_symbol "
        "to look a name up."
    ),
]
IncludeStructure = Annotated[
    bool,
    Field(
        description="Also follow containment (CONTAINS/DECLARES): a module's or class's own "
        "members, and the class or file enclosing a symbol. Off by default, as in the CLI; "
        "cgis_get_structure is the tool for members alone."
    ),
]
IncludeExternal = Annotated[
    bool,
    Field(
        description="Also return stdlib, third-party and unresolved call targets — calls on "
        "values whose type is decided at runtime. Off by default, as in the CLI, because they "
        "dominate the payload; in json, coverage/top_unresolved still counts what was dropped."
    ),
]


def _traversal_filters(
    include_structure: bool, include_external: bool
) -> tuple[frozenset[EdgeType] | None, bool]:
    """Edge-type allowlist and external flag for trace/impact — the CLI's defaults (#481)."""
    return (None if include_structure else BEHAVIORAL_EDGE_TYPES), include_external


OutputFormat = Annotated[
    str,
    Field(
        description='"mermaid" for a diagram, or "json" for a payload with real FQNs '
        "(case-insensitive). Any other value returns an error."
    ),
]


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


def _wants_json(output_format: str) -> bool:
    """True when a traversal will be rendered as JSON — the only shape that carries coverage."""
    return output_format.strip().lower() == "json"


def _render_subgraph_with_freshness(
    db_path: str,
    output_format: str,
    root: str,
    note: str,
    title: str,
    nodes: list[Node],
    edges: list[Edge],
    coverage: TraversalCoverage | None = None,
) -> str:
    """`_render_subgraph`, with this database's freshness measured for it (#175)."""
    return _render_subgraph(
        output_format, root, note, title, nodes, edges, _graph_freshness(db_path), coverage
    )


def _render_subgraph(
    output_format: str,
    root: str,
    note: str,
    title: str,
    nodes: list[Node],
    edges: list[Edge],
    freshness: "Freshness | None" = None,
    coverage: TraversalCoverage | None = None,
) -> str:
    """Render a traversal result as a Mermaid diagram or joinable JSON (#171).

    ``json`` returns the raw ``{root, nodes, edges}`` payload with real FQNs —
    no markdown wrapper — so an agent can parse and combine it across calls.
    ``mermaid`` (default) returns the human-readable diagram. Any other value
    is an explicit error rather than a silent fallback.

    ``freshness`` is placed differently in each: a key inside the JSON object,
    a prose note above the diagram. Prefixing the JSON would make it unparseable
    exactly when the graph goes stale, and this renderer is the only place that
    knows which shape it is about to produce (#175). ``coverage`` (#201) rides
    in the JSON only.
    """
    fmt = output_format.strip().lower()
    if fmt == "json":
        payload = graph_to_json(root, nodes, edges, coverage)
        if freshness is not None and freshness.state is not FreshnessState.FRESH:
            payload = {**payload, "freshness": freshness.model_dump()}
        return json.dumps(payload, indent=2)
    if fmt == "mermaid":
        diagram = MermaidCompiler().compile(nodes, edges)
        return (
            f"{_freshness_text(freshness)}{note}### {title} `{root}`:\n\n```mermaid\n{diagram}\n```"
        )
    return f"❌ Unknown format '{output_format}'. Use 'mermaid' or 'json'."


#: Names cgis will create a database under. Everything else is refused, so an
#: agent cannot be talked into materialising `~/.ssh/authorized_keys` (#312).
_DB_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})

#: First 16 bytes of any SQLite file.
_SQLITE_MAGIC = b"SQLite format 3\x00"


def _reject_db_path(db_path: str) -> str | None:
    """Return a refusal message for an unusable ``db_path``, or None if it is fine.

    ``cgis_ingest`` is the only MCP tool that creates its database — the other
    twelve refuse a path that does not already exist, which is guard enough for
    them. It needs a different one: it reads untrusted repository content and is
    then told by the same agent where to write, so ``db_path`` is attacker-
    reachable in a way the read-only tools' paths are not.

    Refuses an unexpected suffix, a missing parent directory (creating a tree is
    never wanted), a directory target, and an existing file that is not a
    database. The last is belt-and-braces — SQLite already declines to open a
    non-database — but it fails with a message that says why.

    Everything is judged on the **resolved** path. A dangling symlink is the
    bypass this guard exists to stop: ``attack.db`` pointing at a target that
    does not exist yet makes ``is_file()`` return False, every check passes, and
    SQLite creates the target. Resolving first means the suffix rule applies to
    where the write actually lands. (A symlink to an *existing* non-database was
    already refused by the magic-byte check.)

    The filesystem probes are wrapped: this runs *before* ``cgis_ingest``'s own
    try/except, and ``Path.resolve()``, ``Path.is_dir()`` and friends propagate
    ``OSError`` (a ``PermissionError`` on the parent), which would escape the
    tool as a crash instead of a message the agent can act on.

    ``RuntimeError`` too, and this docstring used to claim ``OSError`` covered
    it: on a symlink loop CPython's ``resolve()`` catches the ELOOP ``OSError``
    and re-raises it as ``RuntimeError("Symlink loop from ...")``
    (pathlib.py:1237). Corrected in #347, where the same guard was being
    modelled for the metrics log and the case was actually tested.
    """
    try:
        path = Path(db_path).resolve()
    except (OSError, RuntimeError) as exc:
        return f"❌ Refusing db_path '{db_path}': path is inaccessible ({exc})."
    # Case-insensitive: `.DB` carries the same intent, and on a case-insensitive
    # filesystem it is literally the same file.
    if path.suffix.lower() not in _DB_SUFFIXES:
        allowed = ", ".join(sorted(_DB_SUFFIXES))
        return f"❌ Refusing db_path '{db_path}': name must end in one of {allowed}."
    try:
        if path.is_dir():
            return f"❌ Refusing db_path '{db_path}': it is a directory."
        if not path.parent.is_dir():
            return (
                f"❌ Refusing db_path '{db_path}': parent directory does not exist. "
                "cgis will not create one."
            )
        if path.is_file() and path.stat().st_size > 0:
            with path.open("rb") as fh:
                if fh.read(len(_SQLITE_MAGIC)) != _SQLITE_MAGIC:
                    return (
                        f"❌ Refusing db_path '{db_path}': existing file is not a SQLite database."
                    )
    except OSError as exc:
        return f"❌ Refusing db_path '{db_path}': path is inaccessible ({exc})."
    return None


def _graph_freshness(db_path: str) -> Freshness | None:
    """The freshness of `db_path`, or None when the probe itself could not run.

    Never raises: a freshness check is a courtesy on top of the query the caller
    actually asked for, and must not be able to take it down.
    """
    # Existence first: `SQLiteStore` creates the file it is pointed at, and a
    # probe must not materialise a database as a side effect of asking about one.
    if not Path(db_path).is_file():
        return None
    try:
        with SQLiteStore(db_path) as store:
            return store.freshness()
    except Exception:
        return None


def _freshness_text(result: "Freshness | None") -> str:
    """Format a freshness result as a prose note, or "" when there is nothing to say."""
    if result is None or result.state is FreshnessState.FRESH:
        return ""
    if result.state is FreshnessState.STALE:
        return (
            f"> \u26a0 Graph is stale: {result.changed} changed, {result.missing} missing "
            "since ingest. Re-run cgis_ingest for a current answer.\n\n"
        )
    return f"> Freshness unknowable: {result.reason}\n\n"


def _freshness_note(db_path: str) -> str:
    """A one-line staleness prefix for a *text* answer, or "" when fresh (#175).

    The idiom `cgis_find_symbol` already uses (`return note + payload`). Only for
    tools returning prose — a JSON tool gets `_with_freshness` instead, because a
    prefix would break `json.loads` for every consumer exactly when the graph
    goes stale.
    """
    return _freshness_text(_graph_freshness(db_path))


def _with_freshness(db_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Add a `freshness` key to a JSON payload, but only when there is one (#175).

    Inside the object rather than prefixed to the string: a text prefix would
    make the payload unparseable at the moment the caller most needs a parseable
    answer, and would make the tool's output shape depend on its freshness. A
    fresh graph adds no key, so existing consumers see an unchanged shape.
    """
    result = _graph_freshness(db_path)
    if result is None or result.state is FreshnessState.FRESH:
        return payload
    return {**payload, "freshness": result.model_dump()}


@mcp.tool()
def cgis_ingest(
    project_path: Annotated[
        str,
        Field(
            description="Root directory of the project to scan. A relative path resolves "
            "against the MCP server's working directory."
        ),
    ],
    db_path: Annotated[
        str,
        Field(
            description="Where to write the graph: must end in .db, .sqlite or .sqlite3, in a "
            "directory that already exists, and must not be an existing non-SQLite file. "
            "A relative path resolves against the server's working directory."
        ),
    ] = _DEFAULT_DB,
    full_rebuild: Annotated[
        bool,
        Field(description="Re-scan every file from scratch instead of the incremental default."),
    ] = False,
) -> str:
    """Scan a local directory, extract all symbols, resolve links, and build the graph DB.

    Use this to initialise or refresh the code knowledge graph for a project.
    Node FQNs are normalised relative to the workspace root so the graph is
    portable across machines.

    ``db_path`` must name a database — it has to end in ``.db``, ``.sqlite`` or
    ``.sqlite3``, live in a directory that already exists, and not point at an
    existing file that is not a SQLite database. cgis will not create parent
    directories.

    By default the ingest is **incremental**: only changed/new files are
    re-scanned, and the summary reports both what changed this run and the
    whole-graph total. When a change alters what other files resolve against — a
    renamed, removed or added symbol, a deleted or new file, a changed base class
    or re-export — the incremental run rebuilds the whole graph itself, so edges
    in unchanged files never point at symbols that no longer exist. Set
    ``full_rebuild=True`` to force a re-scan of every file from scratch.
    """
    refusal = _reject_db_path(db_path)
    if refusal is not None:
        logger.warning("MCP ingest refused db_path", db=db_path)
        return refusal

    pipeline = IngestionPipeline(_EXTRACTORS)
    try:
        with SQLiteStore(db_path) as store:
            # A rebuild replaces the stored graph in the transaction that writes the
            # new one: deleted-file nodes and stale hashes go, uplift runs inside
            # the pipeline, and a failure part-way leaves the old graph intact.
            nodes, _raw, resolved = pipeline.run(project_path, store=store, rebuild=full_rebuild)
            if nodes:
                store.record_ingest(project_path)
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
    if not nodes:
        # Nothing extracted: a rebuild kept the stored graph, and neither mode
        # records this path as the graph's fresh root.
        kept = "the stored graph was kept" if full_rebuild else "the graph now holds none"
        lines = [f"⚠️ No source files extracted from {project_path} (mode: {mode}); {kept}."]
    else:
        lines = [f"✅ Ingested: {project_path} (mode: {mode})"]
    if nodes and not full_rebuild and not resolved:
        # Incremental no-op: an empty resolved set means no files changed. Say so
        # explicitly so the stable total below doesn't read as a shrunken graph (#192).
        lines.append("No files changed since the last ingest.")
    lines.append(f"Graph total: {total_nodes} nodes / {total_edges} edges")
    lines.append(f"Graph stored in: {db_path}")
    return "\n".join(lines)


@mcp.tool()
def cgis_trace_flow(
    fqn: Fqn,
    db_path: DbPath = _DEFAULT_DB,
    depth: Annotated[
        int,
        Field(
            description="Maximum edge hops downstream, over calls, imports, inheritance, DI "
            "dependencies and references (plus containment with include_structure)."
        ),
    ] = 3,
    output_format: OutputFormat = "mermaid",
    include_structure: IncludeStructure = False,
    include_external: IncludeExternal = False,
) -> str:
    """Downstream subgraph of one FQN: everything it reaches within ``depth`` hops.

    Follows every edge except containment — in practice calls, imports,
    inheritance, DI dependencies and references — between internal code, so this
    answers "what does X depend on?". Containment and
    stdlib/third-party nodes are left out unless ``include_structure`` /
    ``include_external`` ask for them (external covers stdlib, third-party and
    unresolved call targets) — the same view as the CLI's ``trace``.
    For what depends on X use
    ``cgis_analyze_impact``; for only the members of a module or class,
    ``cgis_get_structure``; for a source-included brief to read before editing
    one symbol, ``cgis_context``.

    ``output_format="mermaid"`` (default) returns a human-readable diagram;
    ``"json"`` returns a joinable ``{root, nodes, edges, coverage}`` payload
    with real FQNs (not display hashes) for agent/CI use. ``coverage`` counts
    the calls the traversed functions make that resolved to nothing, and
    ``top_unresolved`` names the most frequent. Read the names, not only the
    ratio: in Python most are methods on untyped locals (``logger.info``,
    ``items.append``), which cut nothing short. Use ``cgis_ingest`` first if
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
            allowed, show_external = _traversal_filters(include_structure, include_external)
            result = QueryEngine(store).get_flow_result(
                res.resolved,
                max_depth=depth,
                allowed_edge_types=allowed,
                show_external=show_external,
                with_coverage=_wants_json(output_format),
            )
    except Exception as exc:
        return f"❌ {exc}"

    note = f"> Resolved '{fqn}' → '{res.resolved}'\n\n" if res.via_suffix else ""
    return _render_subgraph_with_freshness(
        db_path,
        output_format,
        res.resolved,
        note,
        "Execution flow for",
        result.nodes,
        result.edges,
        result.coverage,
    )


@mcp.tool()
def cgis_analyze_impact(
    fqn: Fqn,
    db_path: DbPath = _DEFAULT_DB,
    depth: Annotated[
        int,
        Field(
            description="Maximum edge hops upstream, over callers, importers, subclasses, "
            "type references and DI dependents (plus the enclosing class or file with "
            "include_structure)."
        ),
    ] = 3,
    output_format: OutputFormat = "mermaid",
    include_structure: IncludeStructure = False,
    include_external: IncludeExternal = False,
) -> str:
    """Upstream subgraph of one FQN: everything that reaches it within ``depth`` hops.

    Follows every edge except containment — in practice callers, importers,
    subclasses, type references and DI dependents — within internal code, so this
    answers "what breaks if I change X?". The
    enclosing class or file and stdlib/third-party nodes are left out unless
    ``include_structure`` / ``include_external`` ask for them — the same view as
    the CLI's ``impact``. For what X depends on use
    ``cgis_trace_flow``; for only the members of a module or class,
    ``cgis_get_structure``; for a source-included brief to read before editing
    one symbol, ``cgis_context``.

    ``output_format="mermaid"`` (default)
    returns a diagram; ``"json"`` returns a joinable ``{root, nodes, edges,
    coverage}`` payload with real FQNs — letting an agent compute set
    differences (e.g. "which route handlers never reach ``verify_ownership``?")
    directly. ``coverage`` counts unresolved calls whose name matches a
    traversed function, method or class: callers that may be missing, named in
    ``top_unresolved``. It is an upper bound — a common name matches calls on
    unrelated objects, which the names make visible.
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
            allowed, show_external = _traversal_filters(include_structure, include_external)
            result = QueryEngine(store).get_impact_result(
                res.resolved,
                max_depth=depth,
                allowed_edge_types=allowed,
                show_external=show_external,
                with_coverage=_wants_json(output_format),
            )
    except Exception as exc:
        return f"❌ {exc}"

    note = f"> Resolved '{fqn}' → '{res.resolved}'\n\n" if res.via_suffix else ""
    return _render_subgraph_with_freshness(
        db_path,
        output_format,
        res.resolved,
        note,
        "Impact analysis for",
        result.nodes,
        result.edges,
        result.coverage,
    )


@mcp.tool()
def cgis_get_structure(
    fqn: Fqn,
    db_path: DbPath = _DEFAULT_DB,
    depth: Annotated[
        int, Field(description="Maximum containment levels to descend (module → class → method).")
    ] = 2,
    output_format: OutputFormat = "mermaid",
) -> str:
    """Members of a module or class: the classes, functions and methods it contains.

    Follows containment (CONTAINS/DECLARES) only, so no call or import appears.
    For how the code connects use ``cgis_trace_flow`` (what it depends on) or
    ``cgis_analyze_impact`` (what depends on it).

    Matches the CLI ``structure`` command. ``output_format="mermaid"`` (default) returns a
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
    return _render_subgraph_with_freshness(
        db_path, output_format, res.resolved, note, "Structure of", nodes, edges
    )


@mcp.tool()
def cgis_drift(
    db_path: DbPath = _DEFAULT_DB,
    patterns_path: Annotated[
        str,
        Field(
            description="patterns.yaml (.yaml or .yml) declaring each domain's expected "
            "pattern and tolerance, relative to the server's working directory. "
            "cgis_init_ontology proposes one."
        ),
    ] = "docs/ontology/patterns.yaml",
    max_drift: Annotated[
        float,
        Field(
            description="Drift tolerance for domains that declare no drift_tolerance of their own."
        ),
    ] = 0.50,
    profile: Annotated[
        str | None,
        Field(
            description="Score only domains with this profile, plus profile-less ones — e.g. one "
            "language when patterns.yaml mixes several."
        ),
    ] = None,
    max_residual: Annotated[
        float,
        Field(
            description="Distance to the nearest template beyond which a domain's fit band is "
            '"none" (no template fits).'
        ),
    ] = 0.45,
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
        return json.dumps(_with_freshness(db_path, payload), indent=2)
    except Exception as exc:
        return f"❌ {exc}"


@mcp.tool()
def cgis_suggest_packages(
    db_path: DbPath = _DEFAULT_DB,
    prefix: Annotated[
        str | None,
        Field(
            description="FQN prefix of the package to analyse, e.g. cgis.query, matched on whole "
            "dot-segments. Needed in practice: without it the verdict is no_signal."
        ),
    ] = None,
    with_calls: Annotated[
        bool, Field(description="Use the combined import + call graph instead of imports only.")
    ] = False,
    min_q: Annotated[
        float,
        Field(
            description="Modularity threshold: at or above it, a package whose layout "
            "disagrees with its communities is flagged split (or consolidate, if over-split)."
        ),
    ] = 0.35,
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
    return json.dumps(_with_freshness(db_path, report_to_dict(report)), indent=2)


@mcp.tool()
def cgis_validate(
    db_path: DbPath = _DEFAULT_DB,
    threshold: Annotated[
        float, Field(description="Highest unresolved-edge ratio (0-1) still reported as healthy.")
    ] = 0.30,
) -> str:
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
        return json.dumps(_with_freshness(db_path, payload), indent=2)
    except Exception as exc:
        return f"❌ {exc}"


@mcp.tool()
def cgis_overview(
    db_path: DbPath = _DEFAULT_DB,
    depth: Annotated[
        int,
        Field(description="FQN segments per package prefix; 1 is the top level."),
    ] = DEFAULT_DEPTH,
    limit: Annotated[
        int, Field(description="Maximum packages listed per section.")
    ] = DEFAULT_LIMIT,
) -> str:
    """Where to start in a graph you know nothing about: sizes and a package map.

    Call this first in an unfamiliar repository — every other tool needs a name,
    and this is the one that hands you some. Returns JSON: symbol counts by type,
    file and edge totals, the unresolved-edge ratio, and the largest packages with
    production and tests listed separately. Each ``prefix`` is a real FQN prefix,
    so it can go straight into ``cgis_get_structure``, ``cgis_find_symbol``
    (``fqn_prefix``) or ``cgis_metrics`` (``scope``).

    Listings are capped; ``packages_omitted`` appears when rows were cut. Entry
    points are deliberately not reported — "nothing calls it" is not one on a
    framework codebase, where most handlers have no incoming call edge.
    """
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        with SQLiteStore(db_path) as store:
            report = build_overview(store, depth=depth, limit=limit)
    except Exception as exc:
        return f"❌ {exc}"
    return json.dumps(_with_freshness(db_path, report), indent=2)


@mcp.tool()
def cgis_find_symbol(
    query: Annotated[
        str,
        Field(
            description="Leaf symbol name to search for, without dots (e.g. get_flow_result) — "
            "not an FQN. Case-insensitive substring match, ranked exact > prefix > substring."
        ),
    ],
    db_path: DbPath = _DEFAULT_DB,
    kind: Annotated[
        str | None,
        Field(
            description="Only return this node type, e.g. FUNCTION, METHOD or CLASS (any case). "
            "An unknown type matches nothing rather than raising an error."
        ),
    ] = None,
    fqn_prefix: Annotated[
        str | None,
        Field(
            description="Only return symbols at or under this FQN prefix, matched on whole "
            "dot-segments: app.svc does not match app.svc_alt, and a partial segment "
            "matches nothing."
        ),
    ] = None,
    limit: Annotated[int, Field(description="Maximum number of candidates to return.")] = 20,
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
    # No freshness here, deliberately. This tool's documented return is a JSON
    # *list*, so there is no key to put the signal in, and prefixing prose would
    # break `json.loads` for every caller the moment the tree is edited — the
    # exact hazard the split-by-return-type rule exists to prevent. An earlier
    # revision claimed this tool "already prefixes a note for suffix resolution";
    # it does not — that note belongs to the five traversal tools (#443 review).
    # Giving it a signal means changing the documented shape, which is its own
    # decision rather than a side effect of this one.
    return json.dumps(payload, indent=2)


@mcp.tool()
def cgis_init_ontology(
    db_path: DbPath = _DEFAULT_DB,
    margin: Annotated[
        float,
        Field(description="Headroom added to each measured score to form the proposed tolerance."),
    ] = 0.03,
    min_nodes: Annotated[
        int,
        Field(description="Domains with fewer nodes stay hygiene-only instead of getting a label."),
    ] = 10,
    depth: Annotated[
        int | None,
        Field(
            description="Fixed FQN segment depth for domain discovery (positive); omit to pick it "
            "automatically."
        ),
    ] = None,
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
        # It proposes drift tolerances from whatever the graph holds, so it owes
        # the caller the same signal every other reading tool gives (#443 review).
        # As a YAML *comment*: this output is a document meant to be saved, and a
        # prose prefix breaks the parse exactly the way it would break JSON.
        note = _freshness_text(_graph_freshness(db_path)).strip().lstrip("> ")
        prefix = f"# {note}\n" if note else ""
        return prefix + propose_ontology(db_path, margin=margin, min_nodes=min_nodes, depth=depth)
    except Exception as e:  # translate errors to the ❌-message medium
        return f"❌ Error proposing ontology: {e}"


@mcp.tool()
def cgis_context(
    fqn: Fqn,
    db_path: DbPath = _DEFAULT_DB,
    depth: Annotated[
        int,
        Field(description="Call hops around the focal node; 1 means direct callers and callees."),
    ] = 1,
    source_root: Annotated[
        str,
        Field(
            description="Directory the graph's stored file paths are relative to — normally the "
            "project_path given to cgis_ingest; prefer an absolute path. Empty means the "
            "server's working directory, so source shows as unavailable when the server runs "
            "elsewhere."
        ),
    ] = "",
) -> str:
    """Prompt-ready brief on one FQN: its source, class, direct callers and callees.

    Call this before editing a symbol, instead of reading its files. It follows
    calls only, one hop by default. Source is included when the file is found
    (see ``source_root``), and the domain when the graph was tagged with one.
    For a multi-hop subgraph
    over calls, imports, inheritance and references without source, use
    ``cgis_trace_flow`` (downstream) or
    ``cgis_analyze_impact`` (upstream).

    Returns an XML-tagged prompt — the focal node's source, its enclosing class,
    its architectural domain boundary, direct callers (upstream ripple) and
    callees (downstream dependencies) — meant to be injected into your context
    window in place of raw file dumps. Far more token-efficient than reading
    whole files, and structured so boundaries stay unambiguous.

    Use ``cgis_ingest`` first if the database does not exist. ``source_root``
    locates source files on disk when the graph was ingested from a
    sub-directory (e.g. ``"src"`` after ``cgis ingest ./src``); it is safe to
    pass even when the stored paths already start with that segment (#228).
    When no candidate exists the ``<source>`` block degrades gracefully to
    "unavailable".
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
    return _freshness_note(db_path) + note + payload


@mcp.tool()
def cgis_metrics(
    db_path: DbPath = _DEFAULT_DB,
    limit: Annotated[int, Field(description="Top-N rows returned per section.")] = 10,
    exclude: Annotated[
        list[str] | None,
        Field(
            description='Drop nodes whose FQN contains any of these dot-segments, e.g. ["tests"]; '
            "they are removed from PageRank propagation too."
        ),
    ] = None,
    scope: Annotated[
        list[str] | None,
        Field(
            description="Keep only nodes under any of these dot-prefixes, e.g. "
            '["domains.billing"]; rank still propagates over the whole graph.'
        ),
    ] = None,
) -> str:
    """Whole-graph architectural metrics — coupling bottlenecks, God classes, PageRank.

    Returns JSON ``{bottlenecks, god_classes, critical}`` computed with vectorized
    DuckDB aggregations over the whole graph (fan-in/fan-out coupling,
    declared-member counts, PageRank) — the global "what are the hotspots?" view
    that complements the node-local trace/impact/context tools. Requires the
    optional ``duckdb`` extra; an unavailable dependency is reported as a normal
    ❌ message.

    ``exclude`` drops any node whose FQN contains one of the given dot-segments
    (e.g. ``["tests"]`` removes both ``tests.*`` and ``domains.*.tests.*``) so
    test/vendor scaffolding stays out of the rankings.

    ``scope`` is its complement: it keeps only nodes under one of the given
    dot-prefixes, anchored and cut on a dot boundary, so
    ``["domains.reservation"]`` is that subtree and not
    ``domains.reservation_archive``. Use it for a per-domain review. The two
    compose, and they differ where it matters for PageRank — ``exclude`` removes
    nodes from the propagation graph, ``scope`` filters the rows and lets rank
    propagate over the whole graph, so a scoped run reports how central the
    subtree is *globally*. Coupling in-degree likewise keeps counting callers
    from outside the scope, which is the ripple a domain review is after (#239).
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
                scope=scope or [],
            )
    except Exception as exc:
        return f"❌ {exc}"

    return json.dumps(_with_freshness(db_path, report.model_dump()), indent=2)


@mcp.tool()
def cgis_find_orphans(
    db_path: DbPath = _DEFAULT_DB,
    prefix: Annotated[
        str | None,
        Field(description="Only consider classes under this FQN prefix, cut on a dot boundary."),
    ] = None,
    include_tests: Annotated[
        bool,
        Field(
            description="Count test code as a user, so the report means "
            '"unreachable from anywhere".'
        ),
    ] = False,
    include_generated: Annotated[
        bool, Field(description="Include machine-generated classes, which are hidden by default.")
    ] = False,
) -> str:
    """Classes nothing in production builds, extends or names — dead-code candidates.

    Finds classes that no test, type checker or linter flags, because each is
    still imported somewhere: a package re-export keeps a class importable long
    after its last real caller is gone. On one mid-sized backend this reported
    43 of 1 789 classes, and the hand-written equivalent's findings were all
    real and all deleted.

    Two filters decide the answer. **Tests are not users** — a class built only
    by its own test is exactly the shape being hunted. **A re-export is not a
    use** — ``IMPORTS_SYMBOL`` does not count, or nothing is ever reported. What
    counts is construction (``CALLS``), inheritance (``EXTENDS``) and being named
    (``REFERENCES`` — an annotation, or a class handed to a framework); the last
    keeps abstract ports and Protocols off the list.

    ``prefix`` narrows to one package on a dot boundary. ``include_tests`` counts
    test code as a user, turning the report into "unreachable from anywhere".

    Machine-generated classes are **hidden by default**, and ``include_generated``
    puts them back. The query is right about them — nothing constructs a
    betterproto stub — but nobody hand-deletes one either, so they are noise
    rather than a finding. Measured on owner-api at b7d02fe6, five of six
    reported orphans were generated entities and the sixth a nested pydantic
    ``Config``: the unfiltered report had no actionable row in it (#432).

    Returns JSON ``{orphans, considered, test_sources, generated_excluded}``;
    each orphan carries ``fqn``/``file``/``line``. **A listing is a candidate for
    deletion, not a proof** — a class named only inside a decorator (#429) or
    arriving through a star import is invisible here, so the sweep errs towards
    reporting a live class rather than hiding a dead one. ``test_sources: 0`` in a
    repository that has tests means the graph predates the ``is_test`` column:
    re-ingest. ``generated_excluded`` counts every generated class left out of
    the population under the same ``prefix``, referenced or not — so ``0`` on a
    repository with generated code means the same for ``is_generated``, which has
    no backfill: the marker is in the file header, not in the database.
    """
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        with SQLiteStore(db_path) as store:
            report = find_orphan_classes(
                store,
                prefix=(prefix or "").strip() or None,
                include_tests=include_tests,
                include_generated=include_generated,
            )
    except Exception as exc:
        return f"❌ {exc}"
    return json.dumps(_with_freshness(db_path, dataclasses.asdict(report)), indent=2)


@mcp.tool()
def cgis_audit_reachability(
    target: Annotated[
        str,
        Field(
            description="FQN of the checkpoint every source must reach, e.g. an ownership check. "
            "A unique dot-boundary suffix also resolves."
        ),
    ],
    db_path: DbPath = _DEFAULT_DB,
    from_type: Annotated[
        str | None,
        Field(
            description="NodeType of the sources to audit, e.g. ROUTE_HANDLER, API_ENDPOINT or "
            "FUNCTION (any case). Give this, from_prefix, or both."
        ),
    ] = None,
    from_prefix: Annotated[
        str | None,
        Field(
            description="Only audit sources at or under this FQN prefix, matched on whole "
            "dot-segments. A selection matching no source is an error naming the "
            "whole-segment prefixes it may have meant. Combined with from_type when both "
            "are given."
        ),
    ] = None,
    depth: Annotated[
        int,
        Field(description="Maximum reachability depth; a longer path is reported as a gap."),
    ] = 5,
) -> str:
    """Reachability/authorization audit — which sources never reach a checkpoint.

    The headline use is **IDOR/authz coverage**: list every route handler that does
    NOT transitively reach an ownership check. Reachability follows behavioral edges
    (CALLS *and* FastAPI ``Depends()`` DEPENDS_ON), so a guard wired via DI counts.

    Select sources with ``from_type`` (a NodeType like ``ROUTE_HANDLER`` /
    ``API_ENDPOINT`` / ``FUNCTION``) and/or ``from_prefix`` (FQN prefix) — at least
    one is required. Returns JSON ``{target, covered, gaps}`` where each gap carries
    ``fqn``/``file``/``line``. Generalizes to validators, event tracking, or
    service-layer-boundary rules by pointing ``target`` at the required node.

    A selection that matches no source returns a ❌ message, not an empty
    ``{covered: [], gaps: []}`` that would read as a passing audit (#467).
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
    return note + json.dumps(_with_freshness(db_path, dataclasses.asdict(result)), indent=2)


@mcp.tool()
def cgis_fractal(db_path: DbPath = _DEFAULT_DB) -> str:
    """Report the motif census across the repository's structural tiers.

    Coarsens the graph along its own structure — symbol, class, module, then
    directory levels trimmed from the leaf end — and measures the 13-triad
    census at every rung. Returns JSON: one entry per layer (IMPORTS, CALLS)
    with the full per-rung curve (groups, triads, entropy in bits, dominant
    motif, tangle ratio) and the fit.

    ``verdict`` is the sign of ``slope`` (entropy bits per halving of the group
    count) outside a ``2 * std_error`` dead-band: ``hierarchical`` means
    coarsening ADDS motif diversity, ``flat`` means it destroys it,
    ``scale_invariant`` means the mix is the same at every scale, and
    ``no_signal`` means fewer than three rungs carried enough triads to fit.

    Read the curve, not just the verdict — the fit is a lossy summary of a
    non-linear curve. Observe-only: this tool enforces nothing and no gate
    reads it. Call after ``cgis_ingest``.
    """
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        reports = analyze_fractal_db(db_path)
        payload = {"layers": [dataclasses.asdict(r) for r in reports]}
        return json.dumps(_with_freshness(db_path, payload), indent=2)
    except Exception as exc:
        return f"❌ {exc}"
