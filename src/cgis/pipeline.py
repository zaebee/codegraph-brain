"""Implements Pipeline to orcestrate code traversal."""

from collections.abc import Mapping
from pathlib import Path

import structlog
from rich.progress import Progress, SpinnerColumn, TextColumn

from cgis.core.models import Edge, Node
from cgis.extractors.base import BaseExtractor
from cgis.resolver.engine import ResolverEngine

logger = structlog.getLogger(__name__)


class IngestionPipeline:
    def __init__(self, extractors: Mapping[str, BaseExtractor]) -> None:
        """
        Args:
            extractors: Map of file extensions to their respective extractors.
                        e.g., {".py": PythonExtractor()}
        """
        self._extractors = extractors
        self._excluded = {"venv", ".venv", "__pycache__", "node_modules", "build", "dist"}

    def run(self, repo_path: str) -> tuple[list[Node], list[Edge], list[Edge]]:
        """
        The main pipeline execution: Walk -> Extract -> Resolve.
        """
        all_nodes: list[Node] = []
        all_edges: list[Edge] = []

        path = Path(repo_path)
        if not path.exists():
            msg = f"Path not found: {repo_path}"
            raise FileNotFoundError(msg)
        if not path.is_dir():
            msg = f"Path is not a directory: {repo_path}"
            raise NotADirectoryError(msg)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            # Task 1: Extraction
            extract_task = progress.add_task(description="Extracting code entities...", total=None)

            for root, dirs, files in Path(repo_path).walk():
                # Skip hidden directories and common dependency/cache folders
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in self._excluded]
                for file in files:
                    extractor = self._get_extractor(file)
                    if not extractor:
                        continue

                    full_path = Path(root) / file
                    try:
                        with full_path.open(encoding="utf-8") as f:
                            code = f.read()

                        nodes, edges = extractor.parse(code, str(full_path))
                        if nodes:
                            logger.info(
                                "Parsed nodes from file", nodes=len(nodes), full_path=str(full_path)
                            )
                        all_nodes.extend(nodes)
                        all_edges.extend(edges)
                    except Exception as e:
                        logger.exception("Failed to parse file", full_path=full_path, error=str(e))

                    progress.update(extract_task, advance=1)

            # Task 2: Resolution
            resolve_task = progress.add_task(description="Resolving semantic links...", total=None)
            logger.info("Starting resolution phase...")
            resolver = ResolverEngine(all_nodes, all_edges)
            resolved_edges = resolver.resolve()
            progress.update(resolve_task, advance=1)
            logger.info("Resolution complete. Resolved edges.", edges=len(resolved_edges))

        return all_nodes, all_edges, resolved_edges

    def _get_extractor(self, filename: str) -> BaseExtractor | None:
        for ext, extractor in self._extractors.items():
            if filename.endswith(ext):
                return extractor
        return None
