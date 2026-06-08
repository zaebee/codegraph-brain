"""Generate docs/architecture/health_badge.json for the Shields.io endpoint badge.

Ingests src/cgis/ into a temporary graph, computes basic health metrics,
and writes a Shields.io-compatible JSON badge file.

Health scoring:
  - resolved_ratio >= 0.85 and node_count > 0  → healthy  (green)
  - resolved_ratio >= 0.70                      → degraded (yellow)
  - otherwise                                   → unhealthy (red)
"""

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cgis.extractors.python_extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.storage.sqlite_store import SQLiteStore

_SRC = Path("src/cgis")
_OUTPUT = Path("docs/architecture/health_badge.json")
_EXTRACTORS = {".py": PythonExtractor()}


@dataclass
class _HealthResult:
    message: str
    color: str
    node_count: int
    edge_count: int
    resolved: int
    unresolved: int


def _compute_health(db_path: str) -> _HealthResult:
    with SQLiteStore(db_path) as store:
        nodes = store.get_all_nodes()
        edges = store.get_all_edges()

    node_count = len(nodes)
    edge_count = len(edges)
    unresolved = sum(1 for e in edges if e.target.startswith("raw_call:"))
    resolved = edge_count - unresolved
    resolved_ratio = resolved / edge_count if edge_count else 1.0

    if node_count > 0 and resolved_ratio >= 0.85:
        message, color = "healthy", "brightgreen"
    elif resolved_ratio >= 0.70:
        message, color = "degraded", "yellow"
    else:
        message, color = "unhealthy", "red"

    return _HealthResult(
        message=message,
        color=color,
        node_count=node_count,
        edge_count=edge_count,
        resolved=resolved,
        unresolved=unresolved,
    )


def generate_health() -> None:
    """Ingest src/cgis, compute health metrics, write health_badge.json."""
    if not _SRC.exists():
        msg = f"Source directory not found: {_SRC}"
        raise FileNotFoundError(msg)

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        pipeline = IngestionPipeline(_EXTRACTORS)
        with SQLiteStore(db_path) as store:
            pipeline.run(str(_SRC), store=store)
        result = _compute_health(db_path)
    finally:
        Path(db_path).unlink(missing_ok=True)

    badge: dict[str, str | int] = {
        "schemaVersion": 1,
        "label": "graph status",
        "message": result.message,
        "color": result.color,
        "namedLogo": "graphql",
        "cacheSeconds": 3600,
    }
    _OUTPUT.write_text(json.dumps(badge, indent=2), encoding="utf-8")

    print(
        f"✅ Health: {result.message} | "
        f"nodes={result.node_count} "
        f"edges={result.edge_count} "
        f"unresolved={result.unresolved}"
    )
    print(f"   Written to {_OUTPUT}")


if __name__ == "__main__":
    generate_health()
