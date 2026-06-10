"""Architectural guardrails: hexagonal boundary invariants verified via the self-parsed graph.

Six invariants are enforced:
  1. Pure Domain Isolation  — cgis.core.models never imports other cgis.* subpackages.
  2. Extractor Boundary     — cgis.extractors.* never imports storage, query, or api layers.
  3. Storage Boundary       — cgis.storage.* never imports the api layer.
  4. Model Immutability     — Node and Edge declare frozen=True.
  5. No Circular Dependencies — AnalyzerEngine finds zero cycles in cgis itself.
  6. God Object Baseline    — number of God Objects does not grow beyond known set.
"""

from cgis.core.models import Edge, EdgeType, Node
from cgis.query.analyzer import AnalyzerEngine
from cgis.storage.sqlite_store import SQLiteStore

# ---------------------------------------------------------------------------
# Invariant 1 — Pure Domain Isolation
# ---------------------------------------------------------------------------

_CGIS = "cgis"
_FORBIDDEN_API = frozenset({"api"})
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


def _is_forbidden(target: str, constraint: str, rules: frozenset[str]) -> bool:
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
        is_forbidden = _is_forbidden(e.target, _CGIS, _FORBIDDEN_API)
        if is_imports and is_forbidden:
            violations.add(f"{e.source} -> {e.target}")

    for n in nodes:
        if _clean(n.id) == "storage":
            import_map = n.metadata.get("import_map") or {}
            for val in import_map.values():
                if _is_forbidden(val, _CGIS, _FORBIDDEN_API):
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


# ---------------------------------------------------------------------------
# Invariant 5 — No Circular Dependencies
# ---------------------------------------------------------------------------

_CYCLES_VIOLATION = "ARCHITECTURAL VIOLATION — circular dependencies detected in cgis core:\n"


def test_cgis_has_no_circular_dependencies(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """AnalyzerEngine must find zero cycles in cgis itself.

    Cycles make modules impossible to test in isolation and are the primary
    cause of spaghetti architecture. This test enforces the layered DAG.
    """
    store, _, _ = graph_data
    engine = AnalyzerEngine(store)
    cycles = engine.detect_cycles()
    assert not cycles, _CYCLES_VIOLATION + "\n".join(
        f"  {a.focal_fqn}: {a.metrics.get('cycle_members')}" for a in cycles
    )


def test_analyzer_report_is_deterministic(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Running AnalyzerEngine twice on the same graph must produce identical results."""
    store, _, _ = graph_data
    report_a = AnalyzerEngine(store).run()
    report_b = AnalyzerEngine(store).run()
    assert report_a.total_anomalies == report_b.total_anomalies
    ids_a = {a.id for a in report_a.anomalies}
    ids_b = {a.id for a in report_b.anomalies}
    assert ids_a == ids_b, f"Non-deterministic analyzer output: {ids_a.symmetric_difference(ids_b)}"


# ---------------------------------------------------------------------------
# Invariant 6 — God Object Baseline
# ---------------------------------------------------------------------------

# Known large classes whose size is accepted and documented.
# Adding a new entry here requires a conscious decision.
_KNOWN_GOD_OBJECTS = frozenset(
    {
        "storage.sqlite_store.SQLiteStore",  # data-access layer — many CRUD methods by design
        "extractors.python_extractor.PythonExtractor",  # complex AST visitor
        "extractors.typescript_extractor.TypeScriptExtractor",  # complex AST visitor
        "resolver.engine.ResolverEngine",  # multi-pass symbol resolution
        "query.drift.DriftScorer",  # drift scoring — multiple constraint/weight/param operations
    }
)

_GOD_OBJECT_VIOLATION = (
    "ARCHITECTURAL VIOLATION — new God Object(s) detected beyond the known baseline:\n"
)


def test_god_object_baseline_not_exceeded(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """No new God Objects must be introduced beyond the documented baseline.

    If a class grows beyond the thresholds, either refactor it or explicitly
    add it to _KNOWN_GOD_OBJECTS with a justification comment.
    """
    store, _, _ = graph_data
    engine = AnalyzerEngine(store)
    god_objects = engine.detect_god_objects()
    found_fqns = {a.focal_fqn for a in god_objects}
    new_violations = found_fqns - _KNOWN_GOD_OBJECTS
    assert not new_violations, _GOD_OBJECT_VIOLATION + "\n".join(
        f"  {fqn}" for fqn in sorted(new_violations)
    )
