"""Architectural guardrails: hexagonal boundary invariants verified via the self-parsed graph.

Four invariants are enforced:
  1. Pure Domain Isolation  — cgis.core.models never imports other cgis.* subpackages.
  2. Extractor Boundary     — cgis.extractors.* never imports storage, query, or api layers.
  3. Storage Boundary       — cgis.storage.* never imports the api layer.
  4. Model Immutability     — Node and Edge declare frozen=True.
"""

from cgis.core.models import Edge, EdgeType, Node
from cgis.storage.sqlite_store import SQLiteStore

# ---------------------------------------------------------------------------
# Invariant 1 — Pure Domain Isolation
# ---------------------------------------------------------------------------

_FORBIDDEN_FROM_CORE = frozenset({"extractors", "storage", "query", "api", "resolver", "pipeline"})


def test_core_models_has_no_internal_dependencies(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """cgis.core.models must be a pure domain layer with zero cgis.* subpackage imports."""
    _, nodes, edges = graph_data
    violations = set()
    for e in edges:
        if e.source == "core.models" and e.type == EdgeType.IMPORTS:
            target = e.target
            if (
                target == "cgis"
                or target.removeprefix("cgis.").split(".")[0] in _FORBIDDEN_FROM_CORE
            ):
                violations.add(target)

    core_file = next((n for n in nodes if n.id == "core.models"), None)
    if core_file:
        import_map = core_file.metadata.get("import_map") or {}
        for val in import_map.values():
            if val == "cgis" or val.removeprefix("cgis.").split(".")[0] in _FORBIDDEN_FROM_CORE:
                violations.add(val)

    assert not violations, (
        "ARCHITECTURAL VIOLATION — 'core.models' imports forbidden subpackage(s):\n"
        + "\n".join(f"  -> {v}" for v in sorted(violations))
    )


# ---------------------------------------------------------------------------
# Invariant 2 — Extractor Boundary
# ---------------------------------------------------------------------------

_FORBIDDEN_FROM_EXTRACTORS = frozenset({"storage", "query", "api", "pipeline"})


def test_extractors_are_database_blind(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Extractors must only do AST parsing — zero knowledge of storage, query, or api."""
    _, nodes, edges = graph_data
    violations = set()
    for e in edges:
        if e.source.split(".")[0] == "extractors" and e.type == EdgeType.IMPORTS:
            target = e.target
            if (
                target == "cgis"
                or target.removeprefix("cgis.").split(".")[0] in _FORBIDDEN_FROM_EXTRACTORS
            ):
                violations.add(f"{e.source} -> {target}")

    for n in nodes:
        if n.id.split(".")[0] == "extractors" and "import_map" in n.metadata:
            import_map = n.metadata.get("import_map") or {}
            for val in import_map.values():
                if (
                    val == "cgis"
                    or val.removeprefix("cgis.").split(".")[0] in _FORBIDDEN_FROM_EXTRACTORS
                ):
                    violations.add(f"{n.id} -> {val}")

    assert not violations, (
        "ARCHITECTURAL VIOLATION — extractor(s) import forbidden layer(s):\n"
        + "\n".join(f"  {v}" for v in sorted(violations))
    )


# ---------------------------------------------------------------------------
# Invariant 3 — Storage / Persistence Boundary
# ---------------------------------------------------------------------------


def test_storage_does_not_import_api(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Storage layer must never import the api layer (no upward dependency)."""
    _, nodes, edges = graph_data
    violations = set()
    for e in edges:
        if e.source.split(".")[0] == "storage" and e.type == EdgeType.IMPORTS:
            target = e.target
            if target == "cgis" or target.removeprefix("cgis.").split(".")[0] == "api":
                violations.add(f"{e.source} -> {target}")

    for n in nodes:
        if n.id.split(".")[0] == "storage" and "import_map" in n.metadata:
            import_map = n.metadata.get("import_map") or {}
            for val in import_map.values():
                if val == "cgis" or val.removeprefix("cgis.").split(".")[0] == "api":
                    violations.add(f"{n.id} -> {val}")

    assert not violations, (
        "ARCHITECTURAL VIOLATION — storage layer imports api layer:\n"
        + "\n".join(f"  {v}" for v in sorted(violations))
    )


# ---------------------------------------------------------------------------
# Invariant 4 — Model Immutability
# ---------------------------------------------------------------------------


def test_domain_models_are_frozen() -> None:
    """Node and Edge must declare frozen=True — graph nodes are immutable once created."""
    assert Node.model_config.get("frozen") is True, (
        "ARCHITECTURAL VIOLATION — Node.model_config missing frozen=True"
    )
    assert Edge.model_config.get("frozen") is True, (
        "ARCHITECTURAL VIOLATION — Edge.model_config missing frozen=True"
    )
