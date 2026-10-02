"""Refresh a stale graph before a read tool answers from it (#175).

Opt-in: set `CGIS_AUTO_REFRESH=1` in the MCP server's environment. Off by
default, because it turns every graph-reading tool into one that may write
the database and take as long as a full rebuild.

A refresh is an incremental ingest from the recorded root with the recorded
options, so it is the same graph `cgis_ingest` would leave, not a guess at it.
It never runs on a missing database (there is no root to ingest from), on an
`UNKNOWN` graph, or on one whose ingest options were never recorded. Whatever
it declines or fails to do, the tool still answers and its freshness note
still says the graph is stale.
"""

import os
import threading
import time
from collections.abc import Callable
from functools import wraps
from inspect import signature
from pathlib import Path

import structlog

from cgis.core.freshness import FreshnessState
from cgis.extractors.registry import build_extractors
from cgis.pipeline import IngestionPipeline
from cgis.storage.sqlite_store import SQLiteStore

logger = structlog.getLogger(__name__)

#: The environment variable that turns refreshing on.
AUTO_REFRESH_ENV = "CGIS_AUTO_REFRESH"
_TRUTHY = frozenset({"1", "true", "yes", "on"})

#: One refresh at a time in this process. A second caller waits, then finds the
#: graph fresh and returns, rather than racing the first into "database is
#: locked" once a rebuild outlasts SQLite's busy timeout.
_LOCK = threading.Lock()


def auto_refresh_enabled() -> bool:
    """True when the server's environment opts in to refreshing stale graphs."""
    return os.environ.get(AUTO_REFRESH_ENV, "").strip().lower() in _TRUTHY


def refresh_if_stale(db_path: str) -> bool:
    """Bring `db_path` up to date if it is stale and can be refreshed; True if it ran.

    Never raises: a refresh is a courtesy on top of the query the caller asked
    for, and a failed one leaves the stored graph as it was.
    """
    if not auto_refresh_enabled() or not Path(db_path).is_file():
        return False
    with _LOCK:
        try:
            return _refresh(db_path)
        except Exception as exc:
            logger.warning("Auto-refresh failed; answering from the stored graph", error=str(exc))
            return False


def _refresh(db_path: str) -> bool:
    """The refresh itself, run under the lock: freshness is re-read here, not before."""
    with SQLiteStore(db_path) as store:
        if store.freshness().state is not FreshnessState.STALE:
            return False
        recorded = store.get_ingest_state()
        options = store.get_ingest_options()
        if recorded is None or options is None:
            logger.info("Graph is stale but its ingest options were never recorded; not refreshing")
            return False
        root = recorded[0]
        source_roots, domains = options
        if domains is not None and not Path(domains).is_file():
            logger.info(
                "Graph is stale but its domains file is gone; not refreshing", domains=domains
            )
            return False
        started = time.perf_counter()
        pipeline = IngestionPipeline(build_extractors(source_roots), domains_config=domains)
        nodes, _raw, _resolved = pipeline.run(root, store=store)
        if nodes:
            store.record_ingest(root, pipeline.observed_mtimes)
        logger.info(
            "Auto-refreshed stale graph",
            db=db_path,
            root=root,
            seconds=round(time.perf_counter() - started, 3),
        )
        return True


def refreshes_graph[**P, R](tool: Callable[P, R]) -> Callable[P, R]:
    """Refresh the tool's `db_path` graph, if opted in and stale, before the tool runs.

    `wraps` keeps the signature the MCP SDK builds the tool's schema from.
    """
    params = signature(tool)

    @wraps(tool)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        bound = params.bind(*args, **kwargs)
        bound.apply_defaults()
        db_path = bound.arguments.get("db_path")
        if isinstance(db_path, str):
            refresh_if_stale(db_path)
        return tool(*args, **kwargs)

    return wrapper
