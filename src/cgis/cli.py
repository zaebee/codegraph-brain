""" "CLI to run pipeline."""

import json
from enum import StrEnum
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from cgis import __app_name__, __version__
from cgis.extractors.python_extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.query.engine import QueryEngine
from cgis.query.mermaid import MermaidCompiler
from cgis.storage.sqlite_store import RAW_CALL_PREFIX, SQLiteStore


class OutputFormat(StrEnum):
    TEXT = "text"
    MERMAID = "mermaid"


console = Console()
app = typer.Typer(help="CGIS: Code Graph Intelligence System CLI")


def _version_callback(value: bool) -> None:
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
    return


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
) -> None:
    """
    Scan a repository, extract code structure, and resolve semantic links.
    """
    extractors = {
        ".py": PythonExtractor(),
    }

    pipeline = IngestionPipeline(extractors)

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
            if output.endswith(".json"):
                graph_data = {
                    "metadata": {
                        "source_path": path,
                        "node_count": len(nodes),
                        "edge_count": len(resolved_edges),
                    },
                    "nodes": [n.model_dump() for n in nodes],
                    "edges": [e.model_dump() for e in resolved_edges],
                }

                output_path = Path(output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with output_path.open("w", encoding="utf-8") as f:
                    json.dump(graph_data, f, indent=2)
            else:
                with SQLiteStore(output) as store:
                    store.save_graph(nodes, resolved_edges, overwrite=True)

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


def build_trace_tree(
    store: SQLiteStore,
    current_id: str,
    current_tree: Tree,
    path_visited: set[str],
    max_depth: int,
    current_depth: int,
) -> None:
    """Cycle-safe recursive tree builder for downstream flow tracing."""
    if current_depth >= max_depth:
        return

    outgoing = store.get_outgoing_edges(current_id)
    nodes_map = {n.id: n for n in store.get_nodes([e.target for e in outgoing])}
    for edge in outgoing:
        target_id = edge.target
        target_node = nodes_map.get(target_id)

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
        build_trace_tree(store, target_id, branch, path_visited, max_depth, current_depth + 1)
        path_visited.remove(target_id)


@app.command()
def trace(
    start: str = typer.Argument(..., help="FQN of the starting node to trace flow from"),
    db: str = typer.Option("graph.db", "--db", "-d", help="Path to the SQLite database"),
    depth: int = typer.Option(5, "--depth", help="Maximum traversal depth"),
    output_format: OutputFormat = typer.Option(
        OutputFormat.TEXT, "--format", "-f", help="Output format: text or mermaid"
    ),
) -> None:
    """
    Trace execution flow starting from a specific code entity downwards.
    """
    path = Path(db)
    if not path.exists():
        console.print(f"[bold red]❌ Database not found:[/bold red] {db}. Run `ingest` first.")
        raise typer.Exit(code=1)

    with SQLiteStore(db) as store:
        start_node = store.get_node(start)
        if not start_node:
            console.print(f"[bold red]❌ Start entity not found in graph:[/bold red] {start}")
            raise typer.Exit(code=1)

        if output_format == OutputFormat.MERMAID:
            nodes, edges = QueryEngine(store).get_flow_graph(start, max_depth=depth)
            typer.echo(MermaidCompiler().compile(nodes, edges))
        else:
            console.print(
                f"[bold blue]🔍 Tracing execution flow starting from:[/bold blue] {start}\n"
            )
            root_label = (
                f"[bold cyan]{start_node.type.value}[/bold cyan] "
                f"[yellow]{start_node.id}[/yellow] "
                f"[dim]({start_node.file_path}:{start_node.start_line})[/dim]"
            )
            tree = Tree(root_label)
            build_trace_tree(store, start, tree, {start}, depth, 0)
            console.print(tree)


def build_impact_tree(
    store: SQLiteStore,
    current_id: str,
    current_tree: Tree,
    path_visited: set[str],
    max_depth: int,
    current_depth: int,
) -> None:
    """Cycle-safe recursive tree builder for upstream impact analysis."""
    if current_depth >= max_depth:
        return

    incoming = store.get_incoming_edges(current_id)
    nodes_map = {n.id: n for n in store.get_nodes([e.source for e in incoming])}
    for edge in incoming:
        source_id = edge.source
        source_node = nodes_map.get(source_id)

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
        build_impact_tree(store, source_id, branch, path_visited, max_depth, current_depth + 1)
        path_visited.remove(source_id)


@app.command()
def impact(
    target: str = typer.Argument(..., help="FQN of the target entity to analyze"),
    db: str = typer.Option("graph.db", "--db", "-d", help="Path to the SQLite database"),
    depth: int = typer.Option(5, "--depth", help="Maximum traversal depth"),
    output_format: OutputFormat = typer.Option(
        OutputFormat.TEXT, "--format", "-f", help="Output format: text or mermaid"
    ),
) -> None:
    """
    Analyze transitive upstream impact (callers) of changing a specific code entity.
    """
    path = Path(db)
    if not path.exists():
        console.print(f"[bold red]❌ Database not found:[/bold red] {db}. Run `ingest` first.")
        raise typer.Exit(code=1)

    with SQLiteStore(db) as store:
        target_node = store.get_node(target)
        if not target_node:
            console.print(f"[bold red]❌ Target entity not found in graph:[/bold red] {target}")
            raise typer.Exit(code=1)

        if output_format == OutputFormat.MERMAID:
            nodes, edges = QueryEngine(store).get_impact_graph(target, max_depth=depth)
            typer.echo(MermaidCompiler().compile(nodes, edges))
        else:
            console.print(
                f"[bold blue]🔍 Analyzing transitive upstream callers of:[/bold blue] {target}\n"
            )
            root_label = (
                f"[bold cyan]{target_node.type.value}[/bold cyan] "
                f"[yellow]{target_node.id}[/yellow] "
                f"[dim]({target_node.file_path}:{target_node.start_line})[/dim]"
            )
            tree = Tree(root_label)
            build_impact_tree(store, target, tree, {target}, depth, 0)
            console.print(tree)


@app.command()
def validate(
    db: str = typer.Option("graph.db", "--db", "-d", help="Path to the SQLite database"),
    threshold: float = typer.Option(
        0.30, "--threshold", "-t", min=0.0, max=1.0,
        help="Max allowed unresolved ratio (default 0.30 = 30%)",
    ),
) -> None:
    """
    Report graph integrity: resolved vs unresolved edge ratio.

    Exits with code 1 if the unresolved ratio exceeds the threshold.
    """
    path = Path(db)
    if not path.exists():
        console.print(f"[bold red]❌ Database not found:[/bold red] {db}. Run `ingest` first.")
        raise typer.Exit(code=1)

    try:
        with SQLiteStore(db) as store:
            stats = store.get_edge_stats()
    except Exception as e:
        console.print(f"[bold red]❌ Error reading database:[/bold red] {e}")
        raise typer.Exit(code=1) from e

    resolved_pct = (1.0 - stats.unresolved_ratio) * 100
    unresolved_pct = stats.unresolved_ratio * 100

    table = Table(title="Graph Integrity Report")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="magenta", justify="right")

    table.add_row("Total edges", str(stats.total))
    table.add_row("Resolved edges", f"{stats.resolved} ({resolved_pct:.1f}%)")
    table.add_row(
        "Unresolved edges",
        f"[yellow]{stats.unresolved} ({unresolved_pct:.1f}%)[/yellow]",
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

    console.print(
        f"\n[bold green]✅ Resolution ratio {resolved_pct:.1f}% "
        f"is above threshold {100 - threshold_pct:.1f}%[/bold green]"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
