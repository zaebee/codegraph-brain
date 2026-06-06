"""Implements Pipeline to orcestrate code traversal."""

import logging
from collections.abc import Mapping
from pathlib import Path

import structlog
from rich.progress import Progress, SpinnerColumn, TextColumn

from cgis.core.models import Edge, Node
from cgis.extractors.base import BaseExtractor
from cgis.resolver.engine import ResolverEngine

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = structlog.getLogger(__name__)


class IngestionPipeline:
    def __init__(self, extractors: Mapping[str, BaseExtractor]) -> None:
        """
        Args:
            extractors: Map of file extensions to their respective extractors.
                        e.g., {".py": PythonExtractor()}
        """
        self._extractors = extractors

    def run(self, repo_path: str) -> tuple[list[Node], list[Edge]]:
        """
        The main pipeline execution: Walk -> Extract -> Resolve.
        """
        all_nodes: list[Node] = []
        all_edges: list[Edge] = []

        if not Path(repo_path).exists():
            msg = f"Path not found: {repo_path}"
            raise FileNotFoundError(msg)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            # Task 1: Extraction
            extract_task = progress.add_task(description="Extracting code entities...", total=None)

            for root, _, files in Path(repo_path).walk():
                for file in files:
                    extractor = self._get_extractor(file)
                    if not extractor:
                        continue

                    full_path = Path(root) / file
                    try:
                        with Path.open(full_path, encoding="utf-8") as f:
                            code = f.read()

                        nodes, edges = extractor.parse(code, str(full_path))
                        if nodes:
                            logger.info("Parsed nodes from file", nodes=len(nodes), file=file)
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
            logger.info("Resolution complete. Resolved edges.", edges=len(edges))

        return all_nodes, resolved_edges

    def _get_extractor(self, filename: str) -> BaseExtractor | None:
        for ext, extractor in self._extractors.items():
            if filename.endswith(ext):
                return extractor
        return None
