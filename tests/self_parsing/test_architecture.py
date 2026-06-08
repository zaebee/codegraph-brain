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

_CGIS = "cgis"
_FORBIDDEN_FROM_CORE = frozenset({"extractors", "storage", "query", "api", "resolver", "pipeline"})
_FORBIDDEN_FROM_EXTRACTORS = frozenset({"storage", "query", "api", "pipeline"})

_CORE_NOT_FOUND = "ARCHITECTURAL VIOLATION — 'core.models' node not found in the parsed graph"
_CORE_FORBIDDEN = "ARCHITECTURAL VIOLATION — 'core.models' imports forbidden subpackage(s):\n"

_EXTRACTORS_FORBIDDEN = "ARCHITECTURAL VIOLATION — extractor(s) import forbidden layer(s):\n"
_API_STORAGE_FORBIDDEN = "ARCHITECTURAL VIOLATION — storage layer imports api layer:\n"


def _clean(value: str) -> str:
    """Remove prefix and take first dot splitted."""
    return value.removeprefix(f"{_CGIS}.").split(".")[0]


def _is_imports(target: str, constraint: str, target_type: EdgeType) -> bool:
    """Returns true if target type is IMPORTS."""
    return target == constraint and target_type == EdgeType.IMPORTS


def _is_forbidden(target: str, constraint: str, rules: set[str]) -> bool:
    """Returns true if target equal constraint or violates rules."""
    return target == constraint or _clean(target) in rules


def test_core_models_has_no_internal_dependencies(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """cgis.core.models must be a pure domain layer with zero cgis.* subpackage imports."""
    _, nodes, edges = graph_data
    violations = set()
    for e in edges:
        is_forbidden = _is_forbidden(e.target, _CGIS, _FORBIDDEN_FROM_CORE)
        is_imports = _is_imports(e.source.removeprefix(f"{_CGIS}."), "core.models", e.type)
        if is_imports and is_forbidden:
            violations.add(e.target)

    core_file = next((n for n in nodes if n.id.removeprefix(f"{_CGIS}.") == "core.models"), None)
    assert core_file is not None, _CORE_NOT_FOUND
    import_map = core_file.metadata.get("import_map") or {}
    for val in import_map.values():
        if _is_forbidden(val, _CGIS, _FORBIDDEN_FROM_CORE):
            violations.add(val)

    assert not violations, _CORE_FORBIDDEN + "\n".join(f"  -> {v}" for v in sorted(violations))


# ---------------------------------------------------------------------------
# Invariant 2 — Extractor Boundary
# ---------------------------------------------------------------------------


def test_extractors_are_database_blind(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Extractors must only do AST parsing — zero knowledge of storage, query, or api."""
    _, nodes, edges = graph_data
    violations = set()
    for e in edges:
        is_imports = _is_imports(_clean(e.source), "extractors", e.type)
        is_forbidden = _is_forbidden(e.target, _CGIS, _FORBIDDEN_FROM_EXTRACTORS)
        if is_imports and is_forbidden:
            violations.add(f"{e.source} -> {e.target}")

    for n in nodes:
        if _clean(n.id) == "extractors":
            import_map = n.metadata.get("import_map") or {}
            for val in import_map.values():
                if _is_forbidden(val, _CGIS, _FORBIDDEN_FROM_EXTRACTORS):
                    violations.add(f"{n.id} -> {val}")

    assert not violations, _EXTRACTORS_FORBIDDEN + "\n".join(f"  {v}" for v in sorted(violations))


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
        is_imports = _is_imports(_clean(e.source), "storage", e.type)
        is_forbidden = _is_forbidden(e.target, _CGIS, ["api"])
        if is_imports and is_forbidden:
            violations.add(f"{e.source} -> {e.target}")

    for n in nodes:
        if _clean(n.id) == "storage":
            import_map = n.metadata.get("import_map") or {}
            for val in import_map.values():
                if _is_forbidden(val, _CGIS, ["api"]):
                    violations.add(f"{n.id} -> {val}")

    assert not violations, _API_STORAGE_FORBIDDEN + "\n".join(f"  {v}" for v in sorted(violations))


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
