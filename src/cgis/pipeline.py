"""Implements Pipeline to orcestrate code traversal."""

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from cgis.core.generated import is_generated_source
from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.core.paths import EXCLUDED_DIRS
from cgis.extractors.base import BaseExtractor
from cgis.resolver.engine import ResolverEngine
from cgis.resolver.uplift import SemanticUpliftEngine
from cgis.workspaces import PACKAGE_MANIFEST, WorkspacePackages

if TYPE_CHECKING:
    from cgis.storage.sqlite_store import SQLiteStore

logger = structlog.getLogger(__name__)

#: The node metadata another file's resolution reads, by node type. Everything
#: else a file carries (`local_types`, `shadowed_globals`, decorators) only
#: steers the resolution of that file's own edges, which are re-resolved anyway.
_CROSS_FILE_METADATA: dict[NodeType, tuple[str, ...]] = {
    NodeType.FILE: ("import_map", "reexports", "star_imports"),
    NodeType.CLASS: ("self_types",),
}


def _resolution_signature(nodes: list[Node], edges: list[Edge]) -> frozenset[str]:
    """What other files resolve against in one file's extraction (#38).

    The symbol index is built from node ids, types and names; the import map and
    re-exports of a FILE; the declared attribute types of a CLASS; and the
    inheritance tree from EXTENDS. If none of those changed, an unchanged file's
    stored edges are still what a fresh resolution would produce.
    """
    signature: set[str] = set()
    for node in nodes:
        keys = _CROSS_FILE_METADATA.get(node.type, ())
        meta = {key: node.metadata.get(key) for key in keys}
        signature.add(json.dumps([node.id, node.type.value, node.name, meta], sort_keys=True))
    signature.update(
        json.dumps(["EXTENDS", e.source, e.target]) for e in edges if e.type == EdgeType.EXTENDS
    )
    return frozenset(signature)


class IngestionPipeline:
    """Orchestrates the full Extract → Resolve → Store pipeline over a source tree."""

    _extractors: Mapping[str, BaseExtractor]
    _domains_config: str | None

    def __init__(
        self,
        extractors: Mapping[str, BaseExtractor],
        domains_config: str | None = None,
    ) -> None:
        """
        Args:
            extractors: Map of file extensions to their respective extractors.
                        e.g., {".py": PythonExtractor()}
            domains_config: Optional path to a domains.yaml file. When provided,
                            the SemanticUpliftEngine runs after resolution.
        """
        self._extractors = extractors
        self._domains_config = domains_config
        self._excluded = EXCLUDED_DIRS

    @staticmethod
    def _compute_hash(content: str) -> str:
        """Return the MD5 hex digest of the given source content string."""
        return hashlib.md5(content.encode("utf-8"), usedforsecurity=False).hexdigest()

    @staticmethod
    def workspace_root(repo_path: str) -> Path:
        """The canonical root to ingest, or FileNotFoundError / NotADirectoryError.

        Public so a caller about to open a database can refuse a bad path before
        creating or touching anything.
        """
        path = Path(repo_path)
        if not path.exists():
            msg = f"Path not found: {repo_path}"
            raise FileNotFoundError(msg)
        if not path.is_dir():
            msg = f"Path is not a directory: {repo_path}"
            raise NotADirectoryError(msg)
        # Resolve symlinks + relative dots so that both `cgis ingest ./src` and
        # `cgis ingest /abs/path/src` produce identical file_paths and FQNs.
        return path.resolve()

    def run(
        self,
        repo_path: str,
        store: "SQLiteStore | None" = None,
        *,
        rebuild: bool = False,
    ) -> tuple[list[Node], list[Edge], list[Edge]]:
        """
        The main pipeline execution: Walk -> Extract -> Resolve.

        When `store` is provided the pipeline runs in incremental mode:
        unchanged files (same MD5) are skipped and their nodes are loaded
        from the store for the resolver. Only changed/new files are
        re-extracted and persisted. Stale files (removed from disk) are
        cleaned up automatically.

        `rebuild=True` parses every file regardless of stored hashes and replaces
        the stored graph in the same transaction that writes the new one, so a bad
        path, an empty walk or a crash before that commit leaves the old graph
        intact.
        """
        all_nodes: list[Node] = []
        all_edges: list[Edge] = []
        # file_path -> new hash, only for files that were re-extracted
        changed_files: dict[str, str] = {}
        found_file_paths: set[str] = set()
        workspace_root = self.workspace_root(repo_path)
        packages = WorkspacePackages(workspace_root, self._extractors)

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
                    if file == PACKAGE_MANIFEST:
                        packages.note(root / file)
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
                        rebuild,
                    )

                    progress.update(extract_task, advance=1)

            # Incremental no-op short-circuit (#185): when nothing changed and
            # nothing went stale, the persisted graph is already correct.
            # Re-running the resolver + persistence + uplift would rebuild the
            # whole graph from the DB for zero benefit, so skip them entirely.
            workspace_packages = packages.unambiguous()
            # A renamed or moved package changes what unchanged files' imports mean,
            # and an incremental run re-resolves only changed files (#504).
            packages_changed = (
                store is not None
                and not rebuild
                and (store.get_workspace_packages() or {}) != workspace_packages
            )
            if not packages_changed and self._is_noop_incremental(
                store, changed_files, found_file_paths
            ):
                logger.info("No changes detected — skipping resolution and persistence.")
                return all_nodes, all_edges, []

            # Task 2: Resolution
            resolve_task = progress.add_task(description="Resolving semantic links...", total=None)
            logger.info("Starting resolution phase...")
            resolver = ResolverEngine(all_nodes, all_edges, workspace_packages=workspace_packages)
            resolved_edges, virtual_nodes = resolver.resolve()
            all_nodes.extend(virtual_nodes)
            progress.update(resolve_task, advance=1)
            logger.info(
                "Resolution complete.",
                edges=len(resolved_edges),
                virtual_nodes=len(virtual_nodes),
            )

        if store is None:
            return all_nodes, all_edges, resolved_edges
        if packages_changed:
            logger.info("Workspace packages changed — rebuilding the graph.")
            return self.run(repo_path, store=store, rebuild=True)
        if self._cross_file_inputs_changed(
            store, all_nodes, resolved_edges, changed_files, found_file_paths, rebuild
        ):
            # Unchanged files keep edges resolved against the old symbols, so the
            # only correct graph is a full one (#38). A rebuild never asks again.
            logger.info("Symbols other files resolve against changed — rebuilding the graph.")
            return self.run(repo_path, store=store, rebuild=True)

        self._persist_incremental(
            store,
            all_nodes,
            resolved_edges,
            changed_files,
            found_file_paths,
            virtual_nodes,
            rebuild,
        )
        store.record_workspace_packages(workspace_packages)
        logger.info("Running semantic uplift...")
        SemanticUpliftEngine(store, self._domains_config).execute_uplift()
        logger.info("Semantic uplift complete.")
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
        rebuild: bool = False,
    ) -> None:
        """Extract nodes/edges from one file, applying hash-based skip when store is provided."""
        try:
            with full_path.open(encoding="utf-8") as f:
                code = f.read()

            if store is not None:
                file_hash = self._compute_hash(code)
                if not rebuild and store.get_file_hash(full_path_str) == file_hash:
                    all_nodes.extend(store.get_nodes_by_file(full_path_str))
                    return
                changed_files[full_path_str] = file_hash

            nodes, edges = extractor.parse(code, full_path_str)
            # Stamped here rather than inside each extractor: the marker lives in
            # the source text the pipeline already holds, so every present and
            # future language extractor inherits this without knowing about it.
            if nodes and is_generated_source(code):
                nodes = [n.model_copy(update={"is_generated": True}) for n in nodes]
            if nodes:
                logger.info("Parsed nodes from file", nodes=len(nodes), full_path=full_path_str)
            all_nodes.extend(nodes)
            all_edges.extend(edges)
        except Exception as e:
            logger.exception("Failed to parse file", full_path=full_path, error=str(e))

    @staticmethod
    def _cross_file_inputs_changed(
        store: "SQLiteStore",
        all_nodes: list[Node],
        resolved_edges: list[Edge],
        changed_files: dict[str, str],
        found_file_paths: set[str],
        rebuild: bool = False,
    ) -> bool:
        """True when this run changes what *unchanged* files resolved against.

        Read before persistence, while the store still holds each changed file's
        previous nodes and edges. Three cases invalidate stored edges elsewhere:
        a file removed from disk, a file new to a non-empty graph (its names can
        make a global lookup ambiguous or satisfy one that missed — it shows up as
        a signature against no stored rows), and a changed file whose resolution
        signature differs. Tracked files are read from the
        nodes table, not `files_state`, which a JSON-less full `cgis ingest -o
        x.db` leaves empty.
        """
        if rebuild or not found_file_paths:
            # Nothing unchanged survives a rebuild. And an empty tree has no unchanged
            # files whose edges could be stale: the ordinary stale path removes the
            # deleted files, where a rebuild of an empty walk would keep them.
            return False
        tracked = store.get_tracked_source_files()
        if not tracked:
            return False
        if tracked - found_file_paths:
            return True

        # One pass each, not one per changed file: a lost files_state marks every
        # file changed, and per-file scans of all nodes and edges went quadratic.
        # Keyed by id with the last occurrence winning, which is what the store's
        # INSERT OR REPLACE keeps when a file declares one id twice.
        new_by_file: dict[str, dict[str, Node]] = {}
        for node in all_nodes:
            if node.file_path in changed_files:
                new_by_file.setdefault(node.file_path, {})[node.id] = node
        new_extends: dict[str, dict[str, Edge]] = {}
        for edge in resolved_edges:
            if edge.type == EdgeType.EXTENDS:
                new_extends.setdefault(edge.source, {})[edge.id] = edge

        for file_path in changed_files:
            # No early "no stored rows means a new file": a file whose every id is
            # held by another file's row (`gen/api.py` beside `gen/api/`) has none,
            # and is not new. A genuinely new file has ids the store lacks, so its
            # fresh signature is non-empty against an empty one below.
            old_nodes = store.get_nodes_by_file(file_path)
            old_ids = [n.id for n in old_nodes]
            old = _resolution_signature(old_nodes, store.get_outgoing_edges_batch(old_ids))
            fresh = new_by_file.get(file_path, {})
            # An id another file's row holds (`m.py` beside `m/__init__.py`) was
            # never this file's in the store, so it cannot be in `old` either.
            owner = {n.id: n.file_path for n in store.get_nodes(list(fresh))}
            new_nodes = [n for i, n in fresh.items() if owner.get(i, file_path) == file_path]
            new_edges = [e for n in new_nodes for e in new_extends.get(n.id, {}).values()]
            if _resolution_signature(new_nodes, new_edges) != old:
                return True
        return False

    def _is_noop_incremental(
        self,
        store: "SQLiteStore | None",
        changed_files: dict[str, str],
        found_file_paths: set[str],
    ) -> bool:
        """True when an incremental run can be skipped entirely.

        Never without a store: a plain run has nothing persisted to fall back on.

        Requires no re-extracted files and no stale files. A configured domains
        ontology also disables the skip: ``domains.yaml`` can change independently
        of the source tree, and the semantic uplift that applies it must re-run
        to pick those changes up (it is not tracked in ``changed_files``).

        ``changed_files`` / ``domains_config`` are checked before the
        ``get_all_tracked_files`` DB query so it is only issued when it can
        actually change the outcome.
        """
        if store is None or changed_files or self._domains_config is not None:
            return False
        stale_files = store.get_all_tracked_files() - found_file_paths
        return not stale_files

    def _persist_incremental(
        self,
        store: "SQLiteStore",
        all_nodes: list[Node],
        resolved_edges: list[Edge],
        changed_files: dict[str, str],
        found_file_paths: set[str],
        virtual_nodes: list[Node] | None = None,
        rebuild: bool = False,
    ) -> None:
        """Persist only changed files and clean up stale ones in one transaction.

        On a rebuild the whole stored graph is replaced in that transaction, virtual
        nodes included — unless nothing was extracted, when the old graph is kept.
        """
        nodes_by_file: dict[str, list[Node]] = {}
        for node in all_nodes:
            if node.file_path in changed_files:
                nodes_by_file.setdefault(node.file_path, []).append(node)

        # Map source node → file so structural edges (file_path=None) can be assigned
        source_to_file: dict[str, str] = {
            node.id: node.file_path for node in all_nodes if node.file_path in changed_files
        }
        edges_by_file: dict[str, list[Edge]] = {}
        for edge in resolved_edges:
            file_path = edge.file_path or source_to_file.get(edge.source)
            if file_path and file_path in changed_files:
                edges_by_file.setdefault(file_path, []).append(edge)

        if rebuild and not nodes_by_file:
            logger.warning("Rebuild extracted nothing — keeping the stored graph.")
            return
        stale_files = store.get_all_tracked_files() - found_file_paths
        store.save_incremental_batch(
            nodes_by_file,
            edges_by_file,
            changed_files,
            stale_files,
            replace_all=rebuild,
            virtual_nodes=virtual_nodes,
        )

        for file_path in changed_files:
            logger.info("Re-ingested changed file", file_path=file_path)
        for stale_path in stale_files:
            logger.info("Removed stale file from graph", file_path=stale_path)

    _TEST_FILE_PATTERN = re.compile(r"\.(test|spec)\.(py|ts|tsx|js|jsx)$", re.IGNORECASE)

    def _get_extractor(self, filename: str) -> BaseExtractor | None:
        """Return the registered extractor for the given filename, or None."""
        if self._TEST_FILE_PATTERN.search(filename):
            return None
        for ext, extractor in self._extractors.items():
            if filename.endswith(ext):
                return extractor
        return None
