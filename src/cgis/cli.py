""" "CLI to run pipeline."""

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from cgis import __app_name__, __version__
from cgis.extractors.python_extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline

app = typer.Typer(help="CGIS: Code Graph Intelligence System CLI")
console = Console()


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
        "graph.json", "--output", "-o", help="Path to save the graph (JSON)"
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

    try:
        nodes, raw_edges, resolved_edges = pipeline.run(path)

        if not nodes:
            console.print(
                "[bold yellow]⚠️  Warning: No nodes were extracted. "
                "Check your path or file extensions.[/bold yellow]"
            )
            return

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

        table = Table(title="Ingestion Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="magenta")

        table.add_row("Nodes extracted", str(len(nodes)))
        table.add_row("Edges extracted (raw)", str(len(raw_edges)))
        table.add_row("Edges resolved (clean)", str(len(resolved_edges)))

        console.print(table)
        console.print("[bold green]✅ Success![/bold green] Graph data ready.")

    except Exception as e:
        console.print(f"[bold red]❌ Error during ingestion:[/bold red] {e}")
        raise typer.Exit(code=1) from e


if __name__ == "__main__":
    app()
