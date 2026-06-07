"""Implements Pipeline to orcestrate code traversal."""

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from cgis.core.models import Edge, Node
from cgis.extractors.base import BaseExtractor
from cgis.resolver.engine import _VIRTUAL_FILE_PATH, ResolverEngine

if TYPE_CHECKING:
    from cgis.storage.sqlite_store import SQLiteStore

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

    @staticmethod
    def _compute_hash(content: str) -> str:
        return hashlib.md5(content.encode("utf-8"), usedforsecurity=False).hexdigest()

    def run(
        self,
        repo_path: str,
        store: "SQLiteStore | None" = None,
    ) -> tuple[list[Node], list[Edge], list[Edge]]:
        """
        The main pipeline execution: Walk -> Extract -> Resolve.

        When `store` is provided the pipeline runs in incremental mode:
        unchanged files (same MD5) are skipped and their nodes are loaded
        from the store for the resolver. Only changed/new files are
        re-extracted and persisted. Stale files (removed from disk) are
        cleaned up automatically.
        """
        all_nodes: list[Node] = []
        all_edges: list[Edge] = []
        # file_path -> new hash, only for files that were re-extracted
        changed_files: dict[str, str] = {}
        found_file_paths: set[str] = set()

        path = Path(repo_path)
        if not path.exists():
            msg = f"Path not found: {repo_path}"
            raise FileNotFoundError(msg)
        if not path.is_dir():
            msg = f"Path is not a directory: {repo_path}"
            raise NotADirectoryError(msg)

        # Canonical workspace root: resolve symlinks + relative dots so that
        # both `cgis ingest ./src` and `cgis ingest /abs/path/src` produce
        # identical file_paths and FQNs in the database.
        workspace_root = path.resolve()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
            console=Console(stderr=True),
        ) as progress:
            # Task 1: Extraction
            extract_task = progress.add_task(description="Extracting code entities...", total=None)

            for root, dirs, files in workspace_root.walk():
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in self._excluded]
                for file in files:
                    extractor = self._get_extractor(file)
                    if not extractor:
                        continue

                    full_path = root / file
                    try:
                        # Resolve symlinks before relativising so that a link
                        # pointing outside the workspace is caught and skipped.
                        rel_path_str = full_path.resolve().relative_to(workspace_root).as_posix()
                    except ValueError:
                        logger.warning("File outside workspace root, skipping", file=str(full_path))
                        continue
                    found_file_paths.add(rel_path_str)

                    self._process_file(
                        full_path,
                        rel_path_str,
                        extractor,
                        store,
                        all_nodes,
                        all_edges,
                        changed_files,
                    )

                    progress.update(extract_task, advance=1)

            # Task 2: Resolution
            resolve_task = progress.add_task(description="Resolving semantic links...", total=None)
            logger.info("Starting resolution phase...")
            resolver = ResolverEngine(all_nodes, all_edges)
            resolved_edges, virtual_nodes = resolver.resolve()
            all_nodes.extend(virtual_nodes)
            progress.update(resolve_task, advance=1)
            logger.info(
                "Resolution complete.",
                edges=len(resolved_edges),
                virtual_nodes=len(virtual_nodes),
            )

        if store is not None:
            self._persist_incremental(
                store, all_nodes, resolved_edges, changed_files, found_file_paths, virtual_nodes
            )

        return all_nodes, all_edges, resolved_edges

    def _process_file(
        self,
        full_path: Path,
        full_path_str: str,
        extractor: BaseExtractor,
        store: "SQLiteStore | None",
        all_nodes: list[Node],
        all_edges: list[Edge],
        changed_files: dict[str, str],
    ) -> None:
        """Extract nodes/edges from one file, applying hash-based skip when store is provided."""
        try:
            with full_path.open(encoding="utf-8") as f:
                code = f.read()

            if store is not None:
                file_hash = self._compute_hash(code)
                if store.get_file_hash(full_path_str) == file_hash:
                    all_nodes.extend(store.get_nodes_by_file(full_path_str))
                    return
                changed_files[full_path_str] = file_hash

            nodes, edges = extractor.parse(code, full_path_str)
            if nodes:
                logger.info("Parsed nodes from file", nodes=len(nodes), full_path=full_path_str)
            all_nodes.extend(nodes)
            all_edges.extend(edges)
        except Exception as e:
            logger.exception("Failed to parse file", full_path=full_path, error=str(e))

    def _persist_incremental(
        self,
        store: "SQLiteStore",
        all_nodes: list[Node],
        resolved_edges: list[Edge],
        changed_files: dict[str, str],
        found_file_paths: set[str],
        virtual_nodes: list[Node] | None = None,
    ) -> None:
        """Persist only changed files and clean up stale ones in one transaction."""
        nodes_by_file: dict[str, list[Node]] = {}
        for node in all_nodes:
            if node.file_path in changed_files:
                nodes_by_file.setdefault(node.file_path, []).append(node)
        # Virtual nodes (EXTERNAL/STDLIB) are always re-persisted since they're derived;
        # mark the virtual path as changed so stale virtual nodes get purged first.
        if virtual_nodes:
            nodes_by_file.setdefault(_VIRTUAL_FILE_PATH, []).extend(virtual_nodes)
            changed_files[_VIRTUAL_FILE_PATH] = ""

        # Map source node → file so structural edges (file_path=None) can be assigned
        source_to_file: dict[str, str] = {
            node.id: node.file_path for node in all_nodes if node.file_path in changed_files
        }
        edges_by_file: dict[str, list[Edge]] = {}
        for edge in resolved_edges:
            file_path = edge.file_path or source_to_file.get(edge.source)
            if file_path and file_path in changed_files:
                edges_by_file.setdefault(file_path, []).append(edge)

        stale_files = store.get_all_tracked_files() - found_file_paths
        store.save_incremental_batch(nodes_by_file, edges_by_file, changed_files, stale_files)

        for file_path in changed_files:
            logger.info("Re-ingested changed file", file_path=file_path)
        for stale_path in stale_files:
            logger.info("Removed stale file from graph", file_path=stale_path)

    def _get_extractor(self, filename: str) -> BaseExtractor | None:
        for ext, extractor in self._extractors.items():
            if filename.endswith(ext):
                return extractor
        return None
