""" "CLI to run pipeline."""

import json as _json
from enum import StrEnum
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from cgis import __app_name__, __version__
from cgis.core.models import VIRTUAL_FILE_PATH, Edge, EdgeType, Node, NodeNamespace
from cgis.extractors.python_extractor import PythonExtractor, file_path_to_module_fqn
from cgis.extractors.typescript_extractor import TypeScriptExtractor
from cgis.pipeline import IngestionPipeline
from cgis.query.analyzer import AnalyzerEngine
from cgis.query.anomaly import AnomalyType, ArchitecturalAnomaly
from cgis.query.engine import BEHAVIORAL_EDGE_TYPES, QueryEngine
from cgis.query.mermaid import MermaidCompiler
from cgis.resolver.uplift import SemanticUpliftEngine
from cgis.storage.sqlite_store import RAW_CALL_PREFIX, SQLiteStore

_DEFAULT_DB = "graph.db"
_DEFAULT_DB_HELP = "Path to the SQLite database"
_DEPTH_HELP = "Maximum traversal depth"
_FORMAT_HELP = "Output format: text or mermaid"

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


class OutputFormat(StrEnum):
    """Supported output formats for trace, impact, and structure commands."""

    TEXT = "text"
    MERMAID = "mermaid"


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
        graph_data = {
            "metadata": {
                "source_path": source_path,
                "node_count": len(nodes),
                "edge_count": len(resolved_edges),
            },
            "nodes": [n.model_dump() for n in nodes],
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
) -> None:
    """
    Scan a repository, extract code structure, and resolve semantic links.
    """
    extractors = {
        ".py": PythonExtractor(),
        ".ts": TypeScriptExtractor(),
        ".tsx": TypeScriptExtractor(tsx=True),
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


def build_trace_tree(
    store: SQLiteStore,
    current_id: str,
    current_tree: Tree,
    path_visited: set[str],
    max_depth: int,
    current_depth: int,
    allowed_edge_types: frozenset[EdgeType] | None = None,
    show_external: bool = True,
) -> None:
    """Cycle-safe recursive tree builder for downstream flow tracing."""
    if current_depth >= max_depth:
        return

    outgoing = store.get_outgoing_edges(current_id)
    if allowed_edge_types is not None:
        outgoing = [e for e in outgoing if e.type in allowed_edge_types]
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
        start_node = store.get_node(start)
        if not start_node:
            console.print(f"[bold red]❌ Start entity not found in graph:[/bold red] {start}")
            raise typer.Exit(code=1)

        if output_format == OutputFormat.MERMAID:
            nodes, edges = QueryEngine(store).get_flow_graph(
                start, max_depth=depth, allowed_edge_types=allowed, show_external=show_external
            )
            if internal_only:
                nodes, edges = _filter_internal(nodes, edges)
            typer.echo(MermaidCompiler().compile(nodes, edges))
        else:
            if internal_only:
                msg = "--internal-only is only supported with '--format mermaid'"
                raise typer.BadParameter(msg)
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
) -> None:
    """Cycle-safe recursive tree builder for upstream impact analysis."""
    if current_depth >= max_depth:
        return

    incoming = store.get_incoming_edges(current_id)
    if allowed_edge_types is not None:
        incoming = [e for e in incoming if e.type in allowed_edge_types]
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
        target_node = store.get_node(target)
        if not target_node:
            console.print(f"[bold red]❌ Target entity not found in graph:[/bold red] {target}")
            raise typer.Exit(code=1)

        if output_format == OutputFormat.MERMAID:
            nodes, edges = QueryEngine(store).get_impact_graph(
                target, max_depth=depth, allowed_edge_types=allowed, show_external=show_external
            )
            if internal_only:
                nodes, edges = _filter_internal(nodes, edges)
            typer.echo(MermaidCompiler().compile(nodes, edges))
        else:
            if internal_only:
                msg = "--internal-only is only supported with '--format mermaid'"
                raise typer.BadParameter(msg)
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
        target_node = store.get_node(target)
        if not target_node:
            console.print(f"[bold red]❌ Node not found in graph:[/bold red] {target}")
            raise typer.Exit(code=1)

        nodes, edges = QueryEngine(store).get_structural_graph(target, max_depth=depth)

    if output_format == OutputFormat.MERMAID:
        typer.echo(MermaidCompiler().compile(nodes, edges))
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
        OutputFormat.TEXT, "--format", "-f", help="Output format: text or json"
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

    if output_format.value == "json":
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


if __name__ == "__main__":  # pragma: no cover
    app()
