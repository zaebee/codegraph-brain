"""Architectural guardrails: hexagonal boundary invariants verified via the self-parsed graph.

Four invariants are enforced:
  1. Pure Domain Isolation  — cgis.core.models never imports other cgis.* subpackages.
  2. Extractor Boundary     — cgis.extractors.* never imports storage, query, or api layers.
  3. Storage Boundary       — cgis.storage.* never imports the api layer.
  4. Model Immutability     — Node and Edge declare frozen=True.
"""

from cgis.core.models import Edge, EdgeType, Node
from cgis.core.models import Edge as EdgeModel
from cgis.core.models import Node as NodeModel
from cgis.storage.sqlite_store import SQLiteStore

# ---------------------------------------------------------------------------
# Invariant 1 — Pure Domain Isolation
# ---------------------------------------------------------------------------

_FORBIDDEN_FROM_CORE = (
    "cgis.extractors",
    "cgis.storage",
    "cgis.query",
    "cgis.api",
    "cgis.resolver",
)


def test_core_models_has_no_internal_dependencies(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """cgis.core.models must be a pure domain layer with zero cgis.* subpackage imports."""
    _, _, edges = graph_data
    violations = [
        e
        for e in edges
        if e.source == "core.models"
        and e.type == EdgeType.IMPORTS
        and any(e.target.startswith(pkg) for pkg in _FORBIDDEN_FROM_CORE)
    ]
    assert not violations, (
        "ARCHITECTURAL VIOLATION — 'core.models' imports forbidden subpackage(s):\n"
        + "\n".join(f"  -> {e.target}" for e in violations)
    )


# ---------------------------------------------------------------------------
# Invariant 2 — Extractor Boundary
# ---------------------------------------------------------------------------

_FORBIDDEN_FROM_EXTRACTORS = ("cgis.storage", "cgis.query", "cgis.api")


def test_extractors_are_database_blind(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Extractors must only do AST parsing — zero knowledge of storage, query, or api."""
    _, _, edges = graph_data
    violations = [
        e
        for e in edges
        if e.source.startswith("extractors.")
        and e.type == EdgeType.IMPORTS
        and any(e.target.startswith(pkg) for pkg in _FORBIDDEN_FROM_EXTRACTORS)
    ]
    assert not violations, (
        "ARCHITECTURAL VIOLATION — extractor(s) import forbidden layer(s):\n"
        + "\n".join(f"  {e.source} -> {e.target}" for e in violations)
    )


# ---------------------------------------------------------------------------
# Invariant 3 — Storage / Persistence Boundary
# ---------------------------------------------------------------------------


def test_storage_does_not_import_api(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Storage layer must never import the api layer (no upward dependency)."""
    _, _, edges = graph_data
    violations = [
        e
        for e in edges
        if e.source.startswith("storage.")
        and e.type == EdgeType.IMPORTS
        and e.target.startswith("cgis.api")
    ]
    assert not violations, (
        "ARCHITECTURAL VIOLATION — storage layer imports api layer:\n"
        + "\n".join(f"  {e.source} -> {e.target}" for e in violations)
    )


# ---------------------------------------------------------------------------
# Invariant 4 — Model Immutability
# ---------------------------------------------------------------------------


def test_domain_models_are_frozen() -> None:
    """Node and Edge must declare frozen=True — graph nodes are immutable once created."""
    assert NodeModel.model_config.get("frozen") is True, (
        "ARCHITECTURAL VIOLATION — Node.model_config missing frozen=True"
    )
    assert EdgeModel.model_config.get("frozen") is True, (
        "ARCHITECTURAL VIOLATION — Edge.model_config missing frozen=True"
    )
