""" "CLI to run pipeline."""

import dataclasses
import json as _json
from enum import StrEnum
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.tree import Tree

from cgis import __app_name__, __version__
from cgis.core.models import VIRTUAL_FILE_PATH, Edge, EdgeType, Node, NodeNamespace
from cgis.extractors.python_extractor import PythonExtractor, file_path_to_module_fqn
from cgis.extractors.typescript_extractor import TypeScriptExtractor
from cgis.guardian.metrics import load_reviews, rate_review
from cgis.pipeline import IngestionPipeline
from cgis.query.analyzer import AnalyzerEngine
from cgis.query.anomaly import AnomalyType, ArchitecturalAnomaly
from cgis.query.context_service import build_context
from cgis.query.drift import DriftReport
from cgis.query.drift_service import analyze_drift
from cgis.query.engine import BEHAVIORAL_EDGE_TYPES, QueryEngine
from cgis.query.fqn import resolve_fqn
from cgis.query.graph_json import graph_to_json
from cgis.query.health import HealthScorer
from cgis.query.mermaid import MermaidCompiler
from cgis.query.metrics import ArchitectureReport, DuckDBAnalyzer
from cgis.query.ontology_init import propose_ontology
from cgis.resolver.uplift import SemanticUpliftEngine
from cgis.storage.sqlite_store import RAW_CALL_PREFIX, SQLiteStore

_DEFAULT_DB = "graph.db"
_DEFAULT_DB_HELP = "Path to the SQLite database"
_DEPTH_HELP = "Maximum traversal depth"
_FORMAT_HELP = "Output format: text, mermaid, or json"
_TEXT_JSON_FORMAT_HELP = "Output format: text or json"
_INTERNAL_ONLY_TEXT_ERR = (
    "--internal-only is only supported with '--format mermaid' or '--format json'"
)

_OPT_SHOW_STRUCTURE: bool = typer.Option(
    False,
    "--show-structure/--no-show-structure",
    help="Include structural edges (CONTAINS, DECLARES) in output",
)
_OPT_SHOW_EXTERNAL: bool = typer.Option(
    False,
    "--show-external/--no-show-external",
    help="Include stdlib and external nodes in output",
)
_OPT_MIN_CONFIDENCE: float | None = typer.Option(
    None,
    "--min-confidence",
    min=0.0,
    max=1.0,
    help="Hide edges below this confidence (e.g. 0.5 drops unresolved raw_call edges)",
)


class OutputFormat(StrEnum):
    """Supported output formats for trace, impact, and structure commands."""

    TEXT = "text"
    MERMAID = "mermaid"
    JSON = "json"


class DriftOutputFormat(StrEnum):
    """Supported output formats for the drift command."""

    TEXT = "text"
    JSON = "json"


console = Console()
app = typer.Typer(help="CGIS: Code Graph Intelligence System CLI")


def _version_callback(value: bool) -> None:
    """Print version string and exit when --version flag is passed."""
    if value:
        typer.echo(f"{__app_name__} v{__version__}")
        raise typer.Exit


@app.callback()
def main(
    _version: bool | None = typer.Option(
        None,
        "--version",
        "-v",
        help="Show the application's version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """CGIS — Code Graph Intelligence System CLI."""


def _write_graph_output(
    output: str,
    source_path: str,
    nodes: list[Node],
    resolved_edges: list[Edge],
    domains: str | None,
) -> None:
    """Persist the ingestion result to the given output path (.db or .json)."""
    if output.endswith(".json"):
        enriched_nodes = HealthScorer(nodes, resolved_edges).enrich()
        graph_data = {
            "metadata": {
                "source_path": source_path,
                "node_count": len(enriched_nodes),
                "edge_count": len(resolved_edges),
            },
            "nodes": [n.model_dump() for n in enriched_nodes],
            "edges": [e.model_dump() for e in resolved_edges],
        }
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            _json.dump(graph_data, f, indent=2)
    else:
        with SQLiteStore(output) as store:
            store.save_graph(nodes, resolved_edges, overwrite=True)
            SemanticUpliftEngine(store, domains).execute_uplift()


@app.command()
def ingest(
    path: str = typer.Argument(..., help="Path to the repository to ingest"),
    output: str = typer.Option(
        "graph.json", "--output", "-o", help="Path to save the graph (JSON or .db)"
    ),
    incremental: bool = typer.Option(
        False,
        "--incremental",
        "-i",
        help="Only re-ingest files whose content has changed (requires .db output).",
    ),
    domains: str | None = typer.Option(
        None,
        "--domains",
        "-d",
        help="Path to a domains.yaml file for semantic uplift (requires .db output).",
    ),
    source_roots: list[str] = typer.Option(
        [],
        "--source-root",
        "-s",
        help=(
            "Strip this directory prefix when building FQNs "
            "(e.g. --source-root src makes src/cgis/foo.py → cgis.foo). "
            "May be repeated for multiple roots."
        ),
    ),
) -> None:
    """
    Scan a repository, extract code structure, and resolve semantic links.
    """
    roots = source_roots or []
    extractors = {
        ".py": PythonExtractor(source_roots=roots),
        ".ts": TypeScriptExtractor(source_roots=roots),
        ".tsx": TypeScriptExtractor(tsx=True, source_roots=roots),
    }

    pipeline = IngestionPipeline(extractors, domains_config=domains)

    if domains and not Path(domains).is_file():
        console.print(f"[bold red]❌ Domains config file not found:[/bold red] {domains}")
        raise typer.Exit(code=1)

    console.print(f"[bold blue]🚀 Starting ingestion for:[/bold blue] {path}")

    if incremental and output.endswith(".json"):
        console.print(
            "[bold yellow]⚠️  --incremental requires a .db output file. "
            "Falling back to full ingest.[/bold yellow]"
        )
        incremental = False

    try:
        if incremental:
            with SQLiteStore(output) as store:
                nodes, raw_edges, resolved_edges = pipeline.run(path, store=store)
        else:
            nodes, raw_edges, resolved_edges = pipeline.run(path)

        if not nodes:
            console.print(
                "[bold yellow]⚠️  Warning: No nodes were extracted. "
                "Check your path or file extensions.[/bold yellow]"
            )
            return

        if not incremental:
            _write_graph_output(output, path, nodes, resolved_edges, domains)

        table = Table(title="Ingestion Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="magenta")

        table.add_row("Nodes extracted", str(len(nodes)))
        table.add_row("Edges extracted (raw)", str(len(raw_edges)))
        table.add_row("Edges resolved (clean)", str(len(resolved_edges)))
        if incremental:
            table.add_row("Mode", "incremental")

        console.print(table)
        console.print("[bold green]✅ Success![/bold green] Graph data ready.")

    except Exception as e:
        console.print(f"[bold red]❌ Error during ingestion:[/bold red] {e}")
        raise typer.Exit(code=1) from e


def _filter_internal(
    nodes: list[Node],
    edges: list[Edge],
) -> tuple[list[Node], list[Edge]]:
    """Keep only INTERNAL nodes backed by a real source file (exclude virtual placeholders)."""
    filtered_nodes = [
        n
        for n in nodes
        if n.namespace == NodeNamespace.INTERNAL and n.file_path != VIRTUAL_FILE_PATH
    ]
    internal_ids = {n.id for n in filtered_nodes}
    filtered_edges = [e for e in edges if e.source in internal_ids and e.target in internal_ids]
    return filtered_nodes, filtered_edges


def _render_graph(
    output_format: OutputFormat,
    root: str,
    nodes: list[Node],
    edges: list[Edge],
    internal_only: bool = False,
) -> str:
    """Render a subgraph for a non-text format (Mermaid for eyes, JSON for agents)."""
    if internal_only:
        nodes, edges = _filter_internal(nodes, edges)
    if output_format == OutputFormat.JSON:
        return _json.dumps(graph_to_json(root, nodes, edges), indent=2)
    return MermaidCompiler().compile(nodes, edges)


def _resolve_cli_fqn(store: SQLiteStore, target: str, kind: str) -> str:
    """Resolve a possibly-partial FQN for a CLI command or exit with code 1."""
    resolution = resolve_fqn(store, target)
    if resolution.resolved is None:
        if resolution.candidates:
            console.print(f"[bold red]❌ Ambiguous FQN:[/bold red] {target}")
            for candidate in resolution.candidates:
                console.print(f"  [dim]- {candidate}[/dim]")
            if resolution.truncated:
                console.print("  [dim]… (more matches exist; refine the name)[/dim]")
        else:
            console.print(f"[bold red]❌ {kind} not found in graph:[/bold red] {target}")
        raise typer.Exit(code=1)
    if resolution.via_suffix:
        console.print(f"[dim]Resolved '{target}' → '{resolution.resolved}'[/dim]")
    return resolution.resolved


def build_trace_tree(
    store: SQLiteStore,
    current_id: str,
    current_tree: Tree,
    path_visited: set[str],
    max_depth: int,
    current_depth: int,
    allowed_edge_types: frozenset[EdgeType] | None = None,
    show_external: bool = True,
    min_confidence: float | None = None,
) -> None:
    """Cycle-safe recursive tree builder for downstream flow tracing."""
    if current_depth >= max_depth:
        return

    outgoing = store.get_outgoing_edges(current_id)
    if allowed_edge_types is not None:
        outgoing = [e for e in outgoing if e.type in allowed_edge_types]
    if min_confidence is not None:
        outgoing = [e for e in outgoing if e.confidence >= min_confidence]
    nodes_map = {n.id: n for n in store.get_nodes([e.target for e in outgoing])}
    for edge in outgoing:
        target_id = edge.target
        target_node = nodes_map.get(target_id)

        if not show_external and (
            target_node is None or target_node.namespace != NodeNamespace.INTERNAL
        ):
            continue

        if target_node:
            label = (
                f"[bold green]{target_node.type.value}[/bold green] "
                f"[yellow]{target_node.id}[/yellow] "
                f"[dim]({target_node.file_path}:{target_node.start_line})[/dim]"
            )
        else:
            label = f"[bold red]Unresolved[/bold red] [dim]{target_id}[/dim]"

        branch = current_tree.add(label)

        if not target_node:
            continue

        if target_id in path_visited:
            branch.add("[bold red]↻ Cycle detected[/bold red]")
            continue

        path_visited.add(target_id)
        build_trace_tree(
            store,
            target_id,
            branch,
            path_visited,
            max_depth,
            current_depth + 1,
            allowed_edge_types=allowed_edge_types,
            show_external=show_external,
            min_confidence=min_confidence,
        )
        path_visited.remove(target_id)


@app.command()
def trace(
    start: str = typer.Argument(..., help="FQN of the starting node to trace flow from"),
    db: str = typer.Option(_DEFAULT_DB, "--db", "-d", help=_DEFAULT_DB_HELP),
    depth: int = typer.Option(5, "--depth", help=_DEPTH_HELP),
    output_format: OutputFormat = typer.Option(
        OutputFormat.TEXT, "--format", "-f", help=_FORMAT_HELP
    ),
    internal_only: bool = typer.Option(
        False, "--internal-only", help="Exclude stdlib and external nodes from output"
    ),
    show_structure: bool = _OPT_SHOW_STRUCTURE,
    show_external: bool = _OPT_SHOW_EXTERNAL,
    min_confidence: float | None = _OPT_MIN_CONFIDENCE,
) -> None:
    """
    Trace execution flow starting from a specific code entity downwards.
    """
    path = Path(db)
    if not path.is_file():
        console.print(f"[bold red]❌ Database not found:[/bold red] {db}. Run `ingest` first.")
        raise typer.Exit(code=1)

    allowed: frozenset[EdgeType] | None = None if show_structure else BEHAVIORAL_EDGE_TYPES

    with SQLiteStore(db) as store:
        start = _resolve_cli_fqn(store, start, "Start entity")
        start_node = store.get_node(start)
        if not start_node:  # pragma: no cover — resolved FQNs always exist
            raise typer.Exit(code=1)

        if output_format != OutputFormat.TEXT:
            nodes, edges = QueryEngine(store).get_flow_graph(
                start,
                max_depth=depth,
                allowed_edge_types=allowed,
                show_external=show_external,
                min_confidence=min_confidence,
            )
            typer.echo(_render_graph(output_format, start, nodes, edges, internal_only))
        else:
            if internal_only:
                raise typer.BadParameter(_INTERNAL_ONLY_TEXT_ERR)
            console.print(
                f"[bold blue]🔍 Tracing execution flow starting from:[/bold blue] {start}\n"
            )
            root_label = (
                f"[bold cyan]{start_node.type.value}[/bold cyan] "
                f"[yellow]{start_node.id}[/yellow] "
                f"[dim]({start_node.file_path}:{start_node.start_line})[/dim]"
            )
            tree = Tree(root_label)
            build_trace_tree(
                store,
                start,
                tree,
                {start},
                depth,
                0,
                allowed_edge_types=allowed,
                show_external=show_external,
                min_confidence=min_confidence,
            )
            console.print(tree)


def build_impact_tree(
    store: SQLiteStore,
    current_id: str,
    current_tree: Tree,
    path_visited: set[str],
    max_depth: int,
    current_depth: int,
    allowed_edge_types: frozenset[EdgeType] | None = None,
    show_external: bool = True,
    min_confidence: float | None = None,
) -> None:
    """Cycle-safe recursive tree builder for upstream impact analysis."""
    if current_depth >= max_depth:
        return

    incoming = store.get_incoming_edges(current_id)
    if allowed_edge_types is not None:
        incoming = [e for e in incoming if e.type in allowed_edge_types]
    if min_confidence is not None:
        incoming = [e for e in incoming if e.confidence >= min_confidence]
    nodes_map = {n.id: n for n in store.get_nodes([e.source for e in incoming])}
    for edge in incoming:
        source_id = edge.source
        source_node = nodes_map.get(source_id)

        if not show_external and (
            source_node is None or source_node.namespace != NodeNamespace.INTERNAL
        ):
            continue

        if source_node:
            label = (
                f"[bold magenta]{source_node.type.value}[/bold magenta] "
                f"[yellow]{source_node.id}[/yellow] "
                f"[dim]({source_node.file_path}:{source_node.start_line})[/dim]"
            )
        else:
            label = f"[bold red]Unknown Caller[/bold red] [dim]{source_id}[/dim]"

        branch = current_tree.add(label)

        if not source_node:
            continue

        if source_id in path_visited:
            branch.add("[bold red]↻ Cycle detected[/bold red]")
            continue

        path_visited.add(source_id)
        build_impact_tree(
            store,
            source_id,
            branch,
            path_visited,
            max_depth,
            current_depth + 1,
            allowed_edge_types=allowed_edge_types,
            show_external=show_external,
            min_confidence=min_confidence,
        )
        path_visited.remove(source_id)


@app.command()
def impact(
    target: str = typer.Argument(..., help="FQN of the target entity to analyze"),
    db: str = typer.Option(_DEFAULT_DB, "--db", "-d", help=_DEFAULT_DB_HELP),
    depth: int = typer.Option(5, "--depth", help=_DEPTH_HELP),
    output_format: OutputFormat = typer.Option(
        OutputFormat.TEXT, "--format", "-f", help=_FORMAT_HELP
    ),
    internal_only: bool = typer.Option(
        False, "--internal-only", help="Exclude stdlib and external nodes from output"
    ),
    show_structure: bool = _OPT_SHOW_STRUCTURE,
    show_external: bool = _OPT_SHOW_EXTERNAL,
    min_confidence: float | None = _OPT_MIN_CONFIDENCE,
) -> None:
    """
    Analyze transitive upstream impact (callers) of changing a specific code entity.
    """
    path = Path(db)
    if not path.is_file():
        console.print(f"[bold red]❌ Database not found:[/bold red] {db}. Run `ingest` first.")
        raise typer.Exit(code=1)

    allowed: frozenset[EdgeType] | None = None if show_structure else BEHAVIORAL_EDGE_TYPES

    with SQLiteStore(db) as store:
        target = _resolve_cli_fqn(store, target, "Target entity")
        target_node = store.get_node(target)
        if not target_node:  # pragma: no cover — resolved FQNs always exist
            raise typer.Exit(code=1)

        if output_format != OutputFormat.TEXT:
            nodes, edges = QueryEngine(store).get_impact_graph(
                target,
                max_depth=depth,
                allowed_edge_types=allowed,
                show_external=show_external,
                min_confidence=min_confidence,
            )
            typer.echo(_render_graph(output_format, target, nodes, edges, internal_only))
        else:
            if internal_only:
                raise typer.BadParameter(_INTERNAL_ONLY_TEXT_ERR)
            console.print(
                f"[bold blue]🔍 Analyzing transitive upstream callers of:[/bold blue] {target}\n"
            )
            root_label = (
                f"[bold cyan]{target_node.type.value}[/bold cyan] "
                f"[yellow]{target_node.id}[/yellow] "
                f"[dim]({target_node.file_path}:{target_node.start_line})[/dim]"
            )
            tree = Tree(root_label)
            build_impact_tree(
                store,
                target,
                tree,
                {target},
                depth,
                0,
                allowed_edge_types=allowed,
                show_external=show_external,
                min_confidence=min_confidence,
            )
            console.print(tree)


@app.command()
def validate(
    db: str = typer.Option(_DEFAULT_DB, "--db", "-d", help=_DEFAULT_DB_HELP),
    threshold: float = typer.Option(
        0.30,
        "--threshold",
        "-t",
        min=0.0,
        max=1.0,
        help="Max allowed unresolved ratio (default 0.30 = 30%)",
    ),
) -> None:
    """
    Report graph integrity: resolved vs unresolved edge ratio.

    Exits with code 1 if the unresolved ratio exceeds the threshold.
    """
    path = Path(db)
    if not path.is_file():
        console.print(f"[bold red]❌ Database not found:[/bold red] {db}. Run `ingest` first.")
        raise typer.Exit(code=1)

    try:
        with SQLiteStore(db) as store:
            stats = store.get_edge_stats()
    except Exception as e:
        console.print(f"[bold red]❌ Error reading database:[/bold red] {e}")
        raise typer.Exit(code=1) from e

    def _pct(n: int) -> str:
        """Format an edge count as a percentage of total edges."""
        return f"{n / stats.total * 100:.1f}%" if stats.total else "0.0%"

    unresolved_pct = stats.unresolved_ratio * 100

    table = Table(title="Graph Integrity Report")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="magenta", justify="right")

    table.add_row("Total edges", str(stats.total))
    table.add_row("Internal (resolved)", f"{stats.resolved} ({_pct(stats.resolved)})")
    table.add_row("Stdlib calls", f"{stats.stdlib} ({_pct(stats.stdlib)})")
    table.add_row("External calls", f"{stats.external} ({_pct(stats.external)})")
    if stats.unresolved:
        table.add_row(
            "Unresolved (raw)",
            f"[bold red]{stats.unresolved} ({_pct(stats.unresolved)})[/bold red]",
        )
    console.print(table)

    if stats.top_unresolved:
        console.print("\n[bold]Top unresolved targets:[/bold]")
        top_table = Table(show_header=False, box=None, padding=(0, 2))
        top_table.add_column("target", style="dim")
        top_table.add_column("count", justify="right", style="yellow")
        for target, count in stats.top_unresolved:
            name = target.removeprefix(RAW_CALL_PREFIX)
            top_table.add_row(name, str(count))
        console.print(top_table)

    threshold_pct = threshold * 100
    if stats.unresolved_ratio > threshold:
        console.print(
            f"\n[bold red]❌ Unresolved ratio {unresolved_pct:.1f}% "
            f"exceeds threshold {threshold_pct:.1f}%[/bold red]"
        )
        raise typer.Exit(code=1)

    internal_pct = stats.resolved / stats.total * 100 if stats.total else 0.0
    console.print(
        f"\n[bold green]✅ Internal {internal_pct:.1f}% — "
        f"unresolved {unresolved_pct:.1f}% below threshold {threshold_pct:.1f}%[/bold green]"
    )


@app.command()
def find(
    query: str = typer.Argument(..., help="Partial symbol name to search for"),
    db: str = typer.Option(_DEFAULT_DB, "--db", "-d", help=_DEFAULT_DB_HELP),
    kind: str | None = typer.Option(
        None, "--kind", "-k", help="Filter by node type (FUNCTION/METHOD/CLASS/...)"
    ),
    prefix: str | None = typer.Option(
        None, "--prefix", "-p", help="Scope results to FQNs under this prefix"
    ),
    limit: int = typer.Option(20, "--limit", "-n", min=1, help="Max results"),
) -> None:
    """
    Find symbols by partial name → candidate FQNs (ranked exact > prefix > substring).

    Resolves the "I know the name but not the FQN" gap before trace/impact/structure.
    """
    path = Path(db)
    if not path.is_file():
        console.print(f"[bold red]❌ Database not found:[/bold red] {db}. Run `ingest` first.")
        raise typer.Exit(code=1)

    kinds = (kind.strip().upper(),) if kind and kind.strip() else ()
    with SQLiteStore(db) as store:
        matches = store.search_nodes(query, kinds=kinds, fqn_prefix=prefix, limit=limit)

    if not matches:
        console.print(f"[yellow]No symbols matching '{escape(query)}'[/yellow]")
        return

    table = Table(title=f"Symbols matching '{escape(query)}'")
    table.add_column("Name", style="cyan")
    table.add_column("Type", style="dim")
    table.add_column("FQN", style="yellow")
    table.add_column("Location", style="dim")
    for node in matches:
        table.add_row(node.name, node.type.value, node.id, f"{node.file_path}:{node.start_line}")
    console.print(table)


@app.command()
def structure(
    target: str = typer.Argument(..., help="FQN or file path of the module/class to inspect"),
    db: str = typer.Option(_DEFAULT_DB, "--db", "-d", help=_DEFAULT_DB_HELP),
    depth: int = typer.Option(3, "--depth", help=_DEPTH_HELP),
    output_format: OutputFormat = typer.Option(
        OutputFormat.TEXT, "--format", "-f", help=_FORMAT_HELP
    ),
) -> None:
    """
    Show the structural hierarchy (UML) of a module or class.

    Traverses only CONTAINS and DECLARES edges — no call-graph noise.
    Accepts an FQN (e.g. src.cgis.pipeline) or a file path (src/cgis/pipeline.py).
    """
    path = Path(db)
    if not path.is_file():
        console.print(f"[bold red]❌ Database not found:[/bold red] {db}. Run `ingest` first.")
        raise typer.Exit(code=1)

    # Normalize file path → FQN
    if "/" in target or "\\" in target or target.endswith(".py"):
        normalized = target.replace("\\", "/").removeprefix("./")
        target = file_path_to_module_fqn(normalized)
        console.print(f"[dim]→ FQN: {target}[/dim]")

    with SQLiteStore(db) as store:
        target = _resolve_cli_fqn(store, target, "Node")
        target_node = store.get_node(target)
        if not target_node:  # pragma: no cover — resolved FQNs always exist
            raise typer.Exit(code=1)

        nodes, edges = QueryEngine(store).get_structural_graph(target, max_depth=depth)

    if output_format != OutputFormat.TEXT:
        typer.echo(_render_graph(output_format, target, nodes, edges))
        return

    console.print(f"[bold blue]📦 Structure of:[/bold blue] {target}\n")
    root_label = (
        f"[bold cyan]{target_node.type.value}[/bold cyan] [yellow]{target_node.id}[/yellow]"
    )
    tree = Tree(root_label)
    nodes_map = {n.id: n for n in nodes}
    children_map: dict[str, list[str]] = {}
    for edge in edges:
        children_map.setdefault(edge.source, []).append(edge.target)
    _build_structure_tree(target_node.id, tree, nodes_map, children_map, depth, 0)
    console.print(tree)


def _build_structure_tree(
    node_id: str,
    branch: Tree,
    nodes_map: dict[str, Node],
    children_map: dict[str, list[str]],
    max_depth: int,
    current_depth: int,
) -> None:
    """Recursively add children to a rich Tree branch using preloaded nodes/edges."""
    if current_depth >= max_depth:
        return
    for child_id in sorted(
        children_map.get(node_id, []),
        key=lambda cid: nodes_map[cid].start_line if cid in nodes_map else 0,
    ):
        child_node = nodes_map.get(child_id)
        if not child_node:
            continue
        label = f"[bold cyan]{child_node.type.value}[/bold cyan] [yellow]{child_node.name}[/yellow]"
        child_branch = branch.add(label)
        _build_structure_tree(
            child_id, child_branch, nodes_map, children_map, max_depth, current_depth + 1
        )


_SEVERITY_COLOUR = {
    AnomalyType.CIRCULAR_DEPENDENCY: "red",
    AnomalyType.ZONE_OF_PAIN: "yellow",
    AnomalyType.GOD_OBJECT: "magenta",
}


@app.command()
def analyze(
    db: str = typer.Option(_DEFAULT_DB, "--db", "-d", help=_DEFAULT_DB_HELP),
    min_severity: float = typer.Option(
        0.0,
        "--min-severity",
        "-s",
        min=0.0,
        max=1.0,
        help="Only show anomalies at or above this severity score (0.0-1.0)",
    ),
    output_format: OutputFormat = typer.Option(
        OutputFormat.TEXT, "--format", "-f", help=_TEXT_JSON_FORMAT_HELP
    ),
) -> None:
    """
    Detect architectural anti-patterns in the ingested code graph.

    Runs three detectors: circular dependencies (Tarjan SCC), Zone of Pain
    (Uncle Bob's instability / abstractness metrics), and God Objects.
    Requires a .db graph file produced by the `ingest` command.
    """
    path = Path(db)
    if not path.is_file():
        console.print(f"[bold red]❌ Database not found:[/bold red] {db}. Run `ingest` first.")
        raise typer.Exit(code=1)

    with SQLiteStore(db) as store:
        report = AnalyzerEngine(store).run()

    visible = [a for a in report.anomalies if a.severity_score >= min_severity]

    if output_format == OutputFormat.MERMAID:
        console.print("[bold red]❌ JSON/text only for analyze — mermaid not supported.[/bold red]")
        raise typer.Exit(code=1)

    if output_format == OutputFormat.JSON:
        typer.echo(_json.dumps([a.model_dump() for a in visible], indent=2))
        return

    console.print(
        f"\n[bold blue]🔍 Architectural Health Report[/bold blue]  "
        f"[dim]{db}[/dim]\n"
        f"  Nodes analysed : [cyan]{report.total_nodes_analyzed}[/cyan]\n"
        f"  Anomalies found: [{'red' if report.total_anomalies else 'green'}]"
        f"{report.total_anomalies}[/{'red' if report.total_anomalies else 'green'}]\n"
    )

    if not visible:
        console.print("[bold green]✅ No anomalies above the severity threshold.[/bold green]")
        return

    by_type: dict[AnomalyType, list[ArchitecturalAnomaly]] = {}
    for a in visible:
        by_type.setdefault(a.type, []).append(a)

    for anomaly_type, items in by_type.items():
        colour = _SEVERITY_COLOUR.get(anomaly_type, "white")
        console.print(f"[bold {colour}]{'━' * 60}[/bold {colour}]")
        console.print(
            f"[bold {colour}]{anomaly_type.value}[/bold {colour}]  ({len(items)} found)\n"
        )
        for a in sorted(items, key=lambda x: x.severity_score, reverse=True):
            console.print(
                f"  [yellow]{a.focal_fqn}[/yellow]  "
                f"severity=[bold {colour}]{a.severity_score:.2f}[/bold {colour}]"
            )
            for key, val in a.metrics.items():
                console.print(f"    [dim]{key}:[/dim] {val}")
            console.print(f"  [italic]💡 {a.refactoring_hint}[/italic]\n")


_DEFAULT_METRICS = "guardian_metrics.jsonl"


@app.command()
def guardian_rate(
    pr: int = typer.Argument(..., help="GitHub PR number to rate."),
    applied: int = typer.Argument(..., help="Number of findings actually applied."),
    metrics: str = typer.Option(_DEFAULT_METRICS, "--metrics", "-m", help="Path to metrics file."),
) -> None:
    """Record how many Guardian findings were applied for a given PR."""
    updated = rate_review(pr=pr, applied=applied, metrics_path=Path(metrics))
    if updated:
        console.print(f"[green]✅ PR #{pr}: recorded {applied} applied findings.[/green]")
    else:
        console.print(f"[red]❌ No unrated entry found for PR #{pr} in {metrics}.[/red]")
        raise typer.Exit(code=1)


@app.command()
def guardian_stats(
    metrics: str = typer.Option(_DEFAULT_METRICS, "--metrics", "-m", help="Path to metrics file."),
    last: int = typer.Option(20, "--last", "-n", help="Show only the last N reviews."),
) -> None:
    """Show Guardian review quality metrics trend."""
    reviews = load_reviews(Path(metrics))
    if not reviews:
        console.print(f"[yellow]No metrics found in {metrics}.[/yellow]")
        raise typer.Exit

    reviews = reviews[-last:]

    table = Table(title=f"Guardian Review Metrics (last {len(reviews)})")
    table.add_column("PR", style="cyan", justify="right")
    table.add_column("Model", style="dim")
    table.add_column("Tokens", justify="right")
    table.add_column("Findings", justify="right")
    table.add_column("Applied", justify="right")
    table.add_column("Precision", justify="right")
    table.add_column("LGTM")

    total_tokens = 0
    rated = [r for r in reviews if r.get("findings_applied") is not None]

    for r in reviews:
        pr_str = f"#{r['pr']}" if r.get("pr") else "—"
        tokens = int(r.get("total_tokens", 0))
        total_tokens += tokens
        findings = int(r.get("findings_total", 0))
        applied = r.get("findings_applied")
        if applied is not None:
            applied_str = str(applied)
            precision = f"{int(applied) / findings * 100:.0f}%" if findings else "—"
        else:
            applied_str = "[dim]?[/dim]"
            precision = "[dim]?[/dim]"
        lgtm = "✅" if r.get("lgtm") else ""
        table.add_row(
            pr_str,
            str(r.get("model", "")),
            f"{tokens:,}",
            str(findings),
            applied_str,
            precision,
            lgtm,
        )

    console.print(table)

    if rated:
        total_findings = sum(int(r["findings_total"]) for r in rated)
        total_applied = sum(int(r["findings_applied"]) for r in rated)
        avg_precision = f"{total_applied / total_findings * 100:.0f}%" if total_findings else "—"
        console.print(f"\n  Avg tokens/review : [cyan]{total_tokens // len(reviews):,}[/cyan]")
        rated_label = f"rated {len(rated)}/{len(reviews)} reviews"
        console.print(f"  Overall precision : [cyan]{avg_precision}[/cyan]  ({rated_label})")


def _drift_status_label(status: str = "clean") -> str:
    """Return a Rich-formatted status label for a drift report entry.

    ``gate_failed`` renders first and distinctly (spec §2.4, #170A).
    ``empty`` and ``no_signal`` are status-driven and precede score-driven ones (#178).
    Label derivation is fully status-driven.
    """
    if status == "gate_failed":
        return "[bold red]⛔ gate failed[/bold red]"
    if status == "empty":
        return "[bold red]⛔ EMPTY[/bold red]"
    if status == "no_signal":
        return "[yellow]◌ no signal[/yellow]"
    if status == "critical":
        return "[bold red]❌ critical[/bold red]"
    if status == "warning":
        return "[yellow]⚠️  warning[/yellow]"
    return "[green]✅ clean[/green]"


def _render_drift_table(reports: list[DriftReport]) -> None:
    """Print an Architectural Drift Report table to the console.

    Each report carries its own effective tolerance (``r.tolerance``), so no
    global ``max_drift`` parameter is needed here (#170B, spec §2.4).
    """
    table = Table(title="Architectural Drift Report")
    table.add_column("Domain", style="cyan")
    table.add_column("Expected Pattern", style="dim")
    table.add_column("Drift", justify="right")
    table.add_column("TV imp", justify="right", style="dim")
    table.add_column("TV calls", justify="right", style="dim")
    table.add_column("Status", justify="center")
    for r in reports:
        table.add_row(
            r.fqn_prefix,
            r.expected_pattern or "(hygiene)",
            f"{r.drift_score:.2f}",
            f"{r.tv_imports:.2f}" if r.tv_imports is not None else "—",
            f"{r.tv_calls:.2f}" if r.tv_calls is not None else "—",
            _drift_status_label(r.status),
        )
        if r.note:
            table.add_row(f"[dim]{escape(r.note)}[/dim]", "", "", "", "", "")
    console.print(table)


@app.command()
def drift(
    db: str = typer.Option(_DEFAULT_DB, "--db", "-d", help=_DEFAULT_DB_HELP),
    patterns: str = typer.Option(
        "docs/ontology/patterns.yaml",
        "--patterns",
        "-p",
        help="Path to a patterns.yaml file with domain expectations.",
    ),
    output_format: DriftOutputFormat = typer.Option(
        DriftOutputFormat.TEXT, "--format", "-f", help=_TEXT_JSON_FORMAT_HELP
    ),
    max_drift: float = typer.Option(
        0.50,
        "--max-drift",
        min=0.0,
        max=1.0,
        help=(
            "Default tolerance for domains that do not declare drift_tolerance "
            "(no longer caps domains that do — see #170)."
        ),
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        "-P",
        help=(
            "Score only domains with this profile (plus profile-less ones). "
            "Without it, zero-match domains of OTHER profiles report EMPTY "
            "and fail the gate — use --profile when your patterns.yaml mixes "
            "languages but the db holds one graph."
        ),
    ),
) -> None:
    """
    Report per-domain architectural drift against declared ideal patterns.

    Exits with code 1 if any domain drift score meets or exceeds the critical threshold.
    """
    if not Path(db).is_file():
        console.print(f"[bold red]❌ Database not found:[/bold red] {db}. Run `ingest` first.")
        raise typer.Exit(code=1)

    if not Path(patterns).is_file():
        console.print(f"[bold red]❌ Patterns file not found:[/bold red] {patterns}")
        raise typer.Exit(code=1)

    try:
        analysis = analyze_drift(db, patterns, max_drift=max_drift, profile=profile)
    except Exception as e:
        console.print(f"[bold red]❌ Error during drift analysis:[/bold red] {e}")
        raise typer.Exit(code=1) from e

    if output_format == DriftOutputFormat.JSON:
        payload = [dataclasses.asdict(r) for r in analysis.reports]
        payload += [{**dataclasses.asdict(r), "enforce": b.enforce} for b, r in analysis.quotient]
        typer.echo(_json.dumps(payload, indent=2))
        if analysis.any_critical:
            raise typer.Exit(code=1)
        return

    _render_drift_table(analysis.reports)

    for b, qr in analysis.quotient:
        marker = "" if b.enforce else " [dim](observe-only)[/dim]"
        status_label = _drift_status_label(qr.status)
        console.print(
            f"Quotient k=1 \\[{b.name}] vs {qr.expected_pattern}: "
            f"drift={qr.drift_score:.2f} {status_label}{marker}"
        )
        if qr.note:
            console.print(f"  [dim]{escape(qr.note)}[/dim]")

    if analysis.any_critical:
        console.print("[bold red]❌ One or more domains exceed the drift threshold.[/bold red]")
        raise typer.Exit(code=1)

    console.print("[bold green]✅ All domains within tolerance.[/bold green]")


def _render_init_summary(text: str) -> None:
    """Print a compact Rich summary table for the proposed ontology.

    Parses ``yaml.safe_load(text)["project_domains"]`` and renders one row per
    domain with columns: name, fqn_prefix, pattern (or "(hygiene)"), tolerance.
    Mirrors the style of ``_render_drift_table``.
    """
    try:
        data = yaml.safe_load(text)
        domains = data.get("project_domains") or []
    except Exception:  # pragma: no cover — malformed yaml is not expected here
        return

    table = Table(title="Proposed Ontology Summary")
    table.add_column("Name", style="cyan")
    table.add_column("FQN Prefix", style="yellow")
    table.add_column("Pattern", style="dim")
    table.add_column("Tolerance", justify="right", style="magenta")

    for d in domains:
        table.add_row(
            escape(str(d.get("name", ""))),
            escape(str(d.get("fqn_prefix", ""))),
            escape(str(d.get("expected_pattern", "(hygiene)"))),
            f"{d.get('drift_tolerance', ''):.2f}"
            if isinstance(d.get("drift_tolerance"), (int, float))
            else escape(str(d.get("drift_tolerance", ""))),
        )
    console.print(table)


@app.command(name="init-ontology")
def init_ontology(
    db: str = typer.Option(_DEFAULT_DB, "--db", "-d", help=_DEFAULT_DB_HELP),
    out: str = typer.Option(
        "patterns.yaml", "--out", "-o", help="Where to write the proposed ontology."
    ),
    margin: float = typer.Option(
        0.03, "--margin", min=0.0, max=0.5, help="Tolerance headroom above the measured score."
    ),
    min_nodes: int = typer.Option(
        10, "--min-nodes", min=1, help="Domains smaller than this stay hygiene-only."
    ),
    depth: int | None = typer.Option(
        None, "--depth", min=1, help="Fixed FQN segment depth for domain discovery (default: auto)."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing --out file."),
) -> None:
    """Propose a starter patterns.yaml from the measured graph (measure-then-label)."""
    if Path(out).exists() and not force:
        console.print(f"[bold red]❌ {out} already exists[/bold red] — use --force to overwrite.")
        raise typer.Exit(code=1)
    try:
        text = propose_ontology(db, margin=margin, min_nodes=min_nodes, depth=depth)
    except FileNotFoundError as e:
        console.print(f"[bold red]❌ {e}[/bold red]")
        raise typer.Exit(code=1) from e
    Path(out).write_text(text)
    _render_init_summary(text)
    console.print(f"[bold green]✅ Proposed ontology written to {out}[/bold green]")
    console.print(f"Next: [cyan]cgis drift --db {db} --patterns {out}[/cyan]")


@app.command()
def context(
    fqn: str = typer.Argument(..., help="FQN of the focal node to compile context for"),
    db: str = typer.Option(_DEFAULT_DB, "--db", "-d", help=_DEFAULT_DB_HELP),
    depth: int = typer.Option(
        1, "--depth", min=1, help="CALLS traversal depth (1=direct neighbours)."
    ),
    source_root: str = typer.Option(
        "",
        "--source-root",
        "-s",
        help="Directory prepended to stored file paths to locate source for the snippet "
        "(e.g. 'src' if you ran `cgis ingest ./src`).",
    ),
) -> None:
    """Compile an agent-facing GraphRAG context package for a focal FQN.

    Emits an XML-tagged prompt (focal source, enclosing class, domain boundary,
    callers, callees) to stdout — pipe it straight into an LLM:

        cgis context "pkg.mod.func" | llm "refactor this safely"

    Resolution notes go to stderr so stdout stays a clean payload.
    """
    path = Path(db)
    if not path.is_file():
        console.print(f"[bold red]❌ Database not found:[/bold red] {db}. Run `ingest` first.")
        raise typer.Exit(code=1)

    err_console = Console(stderr=True)
    with SQLiteStore(db) as store:
        resolution = resolve_fqn(store, fqn)
        if resolution.resolved is None:
            if resolution.candidates:
                err_console.print(f"[bold red]❌ Ambiguous FQN:[/bold red] {fqn}")
                for candidate in resolution.candidates:
                    err_console.print(f"  [dim]- {candidate}[/dim]")
                if resolution.truncated:
                    err_console.print("  [dim]… (more matches exist; refine the name)[/dim]")
            else:
                err_console.print(f"[bold red]❌ Node not found in graph:[/bold red] {fqn}")
            raise typer.Exit(code=1)
        if resolution.via_suffix:
            err_console.print(f"[dim]Resolved '{fqn}' → '{resolution.resolved}'[/dim]")
        payload = build_context(store, resolution.resolved, depth=depth, source_root=source_root)
    typer.echo(payload)


def _render_metrics(report: ArchitectureReport) -> None:
    """Print the architecture report as two Rich tables (bottlenecks + God classes)."""
    bottlenecks = Table(title="🔌 Coupling bottlenecks (top by fan-in + fan-out)")
    bottlenecks.add_column("Node", style="cyan")
    bottlenecks.add_column("Type", style="magenta")
    bottlenecks.add_column("In", justify="right", style="green")
    bottlenecks.add_column("Out", justify="right", style="yellow")
    for m in report.bottlenecks:
        bottlenecks.add_row(m.node_id, m.node_type, str(m.in_degree), str(m.out_degree))
    console.print(bottlenecks)

    gods = Table(title="🏛️  God classes (top by declared members)")
    gods.add_column("Class", style="cyan")
    gods.add_column("Members", justify="right", style="red")
    for m in report.god_classes:
        gods.add_row(m.node_id, str(m.out_degree))
    console.print(gods)

    critical = Table(title="⭐ Critical nodes (top by PageRank — transitive importance)")
    critical.add_column("Node", style="cyan")
    critical.add_column("Type", style="magenta")
    critical.add_column("PageRank", justify="right", style="green")
    for m in report.critical:
        critical.add_row(m.node_id, m.node_type, f"{m.page_rank:.4f}")
    console.print(critical)


@app.command()
def metrics(
    db: str = typer.Option(_DEFAULT_DB, "--db", "-d", help=_DEFAULT_DB_HELP),
    limit: int = typer.Option(10, "--limit", min=1, help="Top-N rows per section."),
    output_format: OutputFormat = typer.Option(
        OutputFormat.TEXT, "--format", "-f", help=_TEXT_JSON_FORMAT_HELP
    ),
) -> None:
    """Whole-graph architectural metrics — bottlenecks, God classes, PageRank (DuckDB).

    Runs vectorized aggregations over the graph via an optional DuckDB layer.
    Install it with `pip install 'codegraph-brain[analytics]'` if missing.
    """
    if output_format == OutputFormat.MERMAID:
        console.print("[bold red]❌ metrics supports --format text or json only.[/bold red]")
        raise typer.Exit(code=2)
    if not Path(db).is_file():
        console.print(f"[bold red]❌ Database not found:[/bold red] {db}. Run `ingest` first.")
        raise typer.Exit(code=1)
    try:
        with DuckDBAnalyzer(db) as analyzer:
            report = analyzer.architecture_report(
                bottleneck_limit=limit, god_limit=limit, critical_limit=limit
            )
    except Exception as e:  # duckdb missing, extension fetch, or a non-SQLite file
        console.print(f"[bold red]❌ {e}[/bold red]")
        raise typer.Exit(code=1) from e

    if output_format == OutputFormat.JSON:
        typer.echo(_json.dumps(report.model_dump(), indent=2))
        return
    _render_metrics(report)


if __name__ == "__main__":  # pragma: no cover
    app()
