# Domain Pattern Fingerprint & Drift Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Domain Pattern Fingerprint & Drift Engine so that `cgis drift` can measure how far each domain's actual graph topology deviates from its declared ideal architectural pattern.

**Architecture:** Three layers — `patterns.yaml` declares expectations; `FingerprintExtractor` computes a 7-component vector per domain from a `SQLiteStore`; `DriftScorer` scores the vector against the ideal using per-component normalized weighted deviation (weighted MAE). The `cgis drift` CLI ties them together and exits 1 if any domain is critical.

**Tech Stack:** Python 3.12, Pydantic v2 (models in `core/`), PyYAML (already in runtime deps), Typer + Rich (existing CLI), `dataclasses.dataclass(frozen=True)`, existing `SQLiteStore.get_all_nodes()` / `get_all_edges()` and `HealthScorer`.

---

## File Structure

```
docs/ontology/patterns.yaml              ← NEW: pattern templates + project domains
src/cgis/query/fingerprint.py            ← NEW: PatternFingerprint + FingerprintExtractor
src/cgis/query/drift.py                  ← NEW: DomainConfig + DriftReport + DriftScorer
src/cgis/cli.py                          ← MODIFIED: add `cgis drift` command
scripts/gen_ideal_graph.py               ← MODIFIED: add --from-ontology mode
tests/unit/test_patterns_yaml.py         ← NEW: structural validation of patterns.yaml
tests/unit/test_fingerprint.py           ← NEW: FingerprintExtractor unit tests
tests/unit/test_drift.py                 ← NEW: DriftScorer unit tests
```

---

## Task 1: `docs/ontology/patterns.yaml` + structural test

**Files:**
- Create: `docs/ontology/patterns.yaml`
- Create: `tests/unit/test_patterns_yaml.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_patterns_yaml.py
"""Structural validation for docs/ontology/patterns.yaml."""

from pathlib import Path

import yaml

PATTERNS_PATH = Path(__file__).parent.parent.parent / "docs" / "ontology" / "patterns.yaml"

_COMPONENT_NAMES = frozenset(
    {
        "hub_count",
        "star_count",
        "chain_len",
        "dag_depth",
        "router_count",
        "cycle_ratio",
        "unresolved_ratio",
    }
)


def _load() -> dict:  # type: ignore[type-arg]
    return yaml.safe_load(PATTERNS_PATH.read_text())


def test_patterns_yaml_exists() -> None:
    """The patterns.yaml file must exist at docs/ontology/patterns.yaml."""
    assert PATTERNS_PATH.exists()


def test_required_top_level_keys() -> None:
    """Top-level keys: version, drift_weights, patterns, project_domains."""
    data = _load()
    assert "version" in data
    assert "drift_weights" in data
    assert "patterns" in data
    assert "project_domains" in data


def test_drift_weights_cover_exactly_all_components() -> None:
    """drift_weights must have exactly the 7 component names."""
    data = _load()
    assert set(data["drift_weights"].keys()) == _COMPONENT_NAMES


def test_drift_weights_sum_to_one() -> None:
    """Weights must sum to 1.0 (within floating-point tolerance)."""
    data = _load()
    total = sum(data["drift_weights"].values())
    assert abs(total - 1.0) < 1e-9


def test_all_pattern_constraints_use_known_components() -> None:
    """No pattern template may reference an unknown component name."""
    data = _load()
    for pattern_name, template in data["patterns"].items():
        for key in template:
            if key == "description":
                continue
            assert key in _COMPONENT_NAMES, (
                f"Unknown component '{key}' in pattern '{pattern_name}'"
            )


def test_each_constraint_has_exactly_one_operator() -> None:
    """Each component constraint must have exactly one of: min, max, exact."""
    data = _load()
    for pattern_name, template in data["patterns"].items():
        for key, value in template.items():
            if key == "description" or not isinstance(value, dict):
                continue
            ops = set(value.keys()) & {"min", "max", "exact"}
            assert len(ops) == 1, (
                f"Constraint '{key}' in '{pattern_name}' must have exactly one operator, got {ops}"
            )


def test_project_domains_have_required_fields() -> None:
    """Each project domain must have name, fqn_prefix, expected_pattern, drift_tolerance."""
    data = _load()
    for domain in data["project_domains"]:
        assert "name" in domain
        assert "fqn_prefix" in domain
        assert "expected_pattern" in domain
        assert "drift_tolerance" in domain


def test_project_domains_reference_known_patterns() -> None:
    """expected_pattern in each domain must refer to a defined pattern."""
    data = _load()
    known = set(data["patterns"].keys())
    for domain in data["project_domains"]:
        assert domain["expected_pattern"] in known, (
            f"Domain '{domain['name']}' references unknown pattern '{domain['expected_pattern']}'"
        )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_patterns_yaml.py -v
```
Expected: `FAILED` — `AssertionError` on `test_patterns_yaml_exists` (file not there yet).

- [ ] **Step 3: Create `docs/ontology/patterns.yaml`**

```yaml
version: "1.0.0"

# Weights for the drift score (must sum exactly to 1.0).
# cycle_ratio is weighted highest — import cycles are always harmful.
drift_weights:
  hub_count:        0.15
  star_count:       0.15
  chain_len:        0.10
  dag_depth:        0.10
  router_count:     0.10
  cycle_ratio:      0.25
  unresolved_ratio: 0.15

# Named ideal pattern templates.
# Each component key accepts exactly one of: {min: X}, {max: X}, {exact: X}.
# Omitted components are unconstrained — they contribute zero drift.
patterns:
  pure_utility:
    description: "Shared library called by many; depends on nothing"
    hub_count:        {min: 1}
    star_count:       {exact: 0}
    cycle_ratio:      {max: 0.0}
    unresolved_ratio: {max: 0.1}

  pipeline_stage:
    description: "Sequential transformer — one input domain, one output domain"
    chain_len:        {min: 2.0}
    star_count:       {max: 1}
    cycle_ratio:      {max: 0.0}

  orchestrator:
    description: "Coordinates N independent services; no leaf-to-leaf edges"
    star_count:       {min: 1}
    hub_count:        {max: 1}
    cycle_ratio:      {max: 0.0}

  layered_dag:
    description: "Clean layered architecture; no upward dependencies"
    dag_depth:        {min: 3}
    cycle_ratio:      {max: 0.0}

  dispatcher:
    # Planned — requires ResolverEngine split (#115).
    description: "Routes to the first matching strategy; mutually exclusive paths"
    router_count:     {min: 1}
    star_count:       {exact: 0}

# Project-specific domain expectations.
project_domains:
  - name: "extraction"
    fqn_prefix: "cgis.extractors"
    expected_pattern: pure_utility
    drift_tolerance: 0.15

  - name: "resolution"
    fqn_prefix: "cgis.resolver"
    expected_pattern: pipeline_stage
    drift_tolerance: 0.20

  - name: "pipeline"
    fqn_prefix: "cgis.pipeline"
    expected_pattern: orchestrator
    drift_tolerance: 0.25

  - name: "storage"
    fqn_prefix: "cgis.storage"
    expected_pattern: pure_utility
    drift_tolerance: 0.15

  - name: "query"
    fqn_prefix: "cgis.query"
    expected_pattern: layered_dag
    drift_tolerance: 0.20
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_patterns_yaml.py -v
```
Expected: 8 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git checkout -b feat/issue-139-fingerprint-drift
git add docs/ontology/patterns.yaml tests/unit/test_patterns_yaml.py
git commit -m "feat(#139): add patterns.yaml and structural validation test"
```

---

## Task 2: `PatternFingerprint` dataclass + `FingerprintExtractor`

**Files:**
- Create: `src/cgis/query/fingerprint.py`
- Create: `tests/unit/test_fingerprint.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_fingerprint.py
"""Unit tests for PatternFingerprint and FingerprintExtractor."""

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.query.fingerprint import FingerprintExtractor, PatternFingerprint
from cgis.storage.sqlite_store import SQLiteStore


# ── helpers ──────────────────────────────────────────────────────────────────


def _node(fqn: str, ntype: NodeType = NodeType.FUNCTION, fp: str = "a.py") -> Node:
    return Node(
        id=fqn,
        type=ntype,
        name=fqn.split(".")[-1],
        file_path=fp,
        start_line=1,
        end_line=10,
        namespace=NodeNamespace.INTERNAL,
    )


def _edge(src: str, tgt: str, etype: EdgeType = EdgeType.CALLS) -> Edge:
    return Edge(id=f"{src}:{etype.value}:{tgt}", source=src, target=tgt, type=etype)


def _store(nodes: list[Node], edges: list[Edge]) -> SQLiteStore:
    s = SQLiteStore(":memory:")
    s.connect()
    s.save_graph(nodes, edges, overwrite=True)
    return s


# ── _in_domain boundary tests ────────────────────────────────────────────────


def test_prefix_boundary_no_false_positive() -> None:
    """'dom_extra' must not be included when extracting domain 'dom'."""
    n1 = _node("dom.fn")
    n2 = _node("dom_extra.fn")
    with _store([n1, n2], []) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.hub_count == 0
    assert fp.unresolved_ratio == 0.0


def test_exact_prefix_match_is_included() -> None:
    """A node whose id equals the fqn_prefix exactly must be included."""
    n = _node("dom", NodeType.FILE, fp="dom.py")
    with _store([n], []) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.cycle_ratio == 0.0  # node exists, no cycles


# ── empty domain ──────────────────────────────────────────────────────────────


def test_empty_domain_returns_zero_fingerprint() -> None:
    """No matching nodes → all-zero PatternFingerprint."""
    with _store([], []) as store:
        fp = FingerprintExtractor(store).extract("nonexistent")
    assert fp == PatternFingerprint(
        domain="nonexistent",
        hub_count=0,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )


# ── hub_count ─────────────────────────────────────────────────────────────────


def test_hub_count_detects_high_fan_in_zero_fan_out() -> None:
    """Node with fan_in > 2 and fan_out == 0 is a hub."""
    nodes = [
        _node("dom.hub"),
        _node("dom.c1"),
        _node("dom.c2"),
        _node("dom.c3"),
    ]
    edges = [
        _edge("dom.c1", "dom.hub"),
        _edge("dom.c2", "dom.hub"),
        _edge("dom.c3", "dom.hub"),
    ]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.hub_count == 1


def test_hub_count_zero_when_hub_has_outgoing() -> None:
    """A node with fan_in > 2 but fan_out > 0 is NOT a hub."""
    nodes = [_node("dom.almost"), _node("dom.c1"), _node("dom.c2"), _node("dom.c3"), _node("dom.x")]
    edges = [
        _edge("dom.c1", "dom.almost"),
        _edge("dom.c2", "dom.almost"),
        _edge("dom.c3", "dom.almost"),
        _edge("dom.almost", "dom.x"),  # has outgoing — disqualified
    ]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.hub_count == 0


# ── star_count ────────────────────────────────────────────────────────────────


def test_star_count_detects_high_fan_out_low_fan_in() -> None:
    """Node with fan_out > 3 and fan_in <= 1 is a star."""
    nodes = [_node("dom.star"), _node("dom.l1"), _node("dom.l2"), _node("dom.l3"), _node("dom.l4")]
    edges = [
        _edge("dom.star", "dom.l1"),
        _edge("dom.star", "dom.l2"),
        _edge("dom.star", "dom.l3"),
        _edge("dom.star", "dom.l4"),
    ]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.star_count == 1


def test_star_count_zero_when_fan_out_is_three() -> None:
    """fan_out == 3 does not qualify as a star (threshold is > 3)."""
    nodes = [_node("dom.n"), _node("dom.l1"), _node("dom.l2"), _node("dom.l3")]
    edges = [_edge("dom.n", "dom.l1"), _edge("dom.n", "dom.l2"), _edge("dom.n", "dom.l3")]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.star_count == 0


# ── cycle_ratio ───────────────────────────────────────────────────────────────


def test_cycle_ratio_is_one_for_fully_cyclic_domain() -> None:
    """Two FILE nodes that import each other → cycle_ratio = 1.0."""
    f1 = _node("dom.modA", NodeType.FILE, fp="dom/modA.py")
    f2 = _node("dom.modB", NodeType.FILE, fp="dom/modB.py")
    edges = [
        _edge("dom.modA", "dom.modB", EdgeType.IMPORTS),
        _edge("dom.modB", "dom.modA", EdgeType.IMPORTS),
    ]
    with _store([f1, f2], edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.cycle_ratio == 1.0


def test_cycle_ratio_zero_when_no_cycles() -> None:
    """DAG imports → cycle_ratio = 0.0."""
    f1 = _node("dom.a", NodeType.FILE, fp="dom/a.py")
    f2 = _node("dom.b", NodeType.FILE, fp="dom/b.py")
    edges = [_edge("dom.a", "dom.b", EdgeType.IMPORTS)]
    with _store([f1, f2], edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.cycle_ratio == 0.0


# ── unresolved_ratio ──────────────────────────────────────────────────────────


def test_unresolved_ratio_half_when_one_raw_call() -> None:
    """1 raw_call + 1 resolved out of 2 CALLS → unresolved_ratio = 0.5."""
    caller = _node("dom.fn")
    target = _node("dom.other")
    edges = [
        _edge("dom.fn", "raw_call:something", EdgeType.CALLS),
        _edge("dom.fn", "dom.other", EdgeType.CALLS),
    ]
    with _store([caller, target], edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.unresolved_ratio == 0.5


def test_unresolved_ratio_zero_when_no_calls() -> None:
    """Domain with no CALLS edges → unresolved_ratio = 0.0 (not a division-by-zero)."""
    n = _node("dom.fn")
    with _store([n], []) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.unresolved_ratio == 0.0


# ── chain_len ──────────────────────────────────────────────────────────────────


def test_chain_len_linear_three_node_chain() -> None:
    """A → B → C within domain: chain_len = 2.0."""
    nodes = [_node("dom.a"), _node("dom.b"), _node("dom.c")]
    edges = [_edge("dom.a", "dom.b"), _edge("dom.b", "dom.c")]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.chain_len == 2.0


def test_chain_len_zero_for_single_isolated_node() -> None:
    """A domain with a single node and no CALLS edges → chain_len = 0.0."""
    with _store([_node("dom.x")], []) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.chain_len == 0.0


# ── dag_depth ─────────────────────────────────────────────────────────────────


def test_dag_depth_three_level_import_chain() -> None:
    """A imports B imports C (within domain) → dag_depth = 2."""
    nodes = [
        _node("dom.a", NodeType.FILE, fp="dom/a.py"),
        _node("dom.b", NodeType.FILE, fp="dom/b.py"),
        _node("dom.c", NodeType.FILE, fp="dom/c.py"),
    ]
    edges = [
        _edge("dom.a", "dom.b", EdgeType.IMPORTS),
        _edge("dom.b", "dom.c", EdgeType.IMPORTS),
    ]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.dag_depth == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_fingerprint.py -v
```
Expected: `ImportError` — `cannot import name 'FingerprintExtractor'`.

- [ ] **Step 3: Implement `src/cgis/query/fingerprint.py`**

```python
"""PatternFingerprint dataclass and FingerprintExtractor."""

from collections import deque
from dataclasses import dataclass

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query._scc import build_adjacency, tarjan_scc
from cgis.query.health import HealthScorer
from cgis.storage.sqlite_store import SQLiteStore

RAW_CALL_PREFIX = "raw_call:"

_HUB_FAN_IN_THRESHOLD = 2   # fan_in > 2
_STAR_FAN_OUT_THRESHOLD = 3  # fan_out > 3
_ROUTER_FAN_OUT_THRESHOLD = 2  # fan_out > 2


@dataclass(frozen=True)
class PatternFingerprint:
    """Seven-component structural fingerprint for a domain."""

    domain: str

    # Pattern counts (structural topology)
    hub_count: int
    star_count: int
    chain_len: float
    dag_depth: int
    router_count: int

    # Health aggregates
    cycle_ratio: float
    unresolved_ratio: float


def _in_domain(fqn: str, prefix: str) -> bool:
    """Return True iff fqn is the prefix itself or a child of it (segment-boundary aware)."""
    return fqn == prefix or fqn.startswith(prefix + ".")


def _avg_chain_length(domain_ids: set[str], internal_calls: list[Edge]) -> float:
    """Average max-BFS-depth from each source node along CALLS edges within the domain."""
    adj: dict[str, list[str]] = {}
    has_incoming: set[str] = set()
    for e in internal_calls:
        if e.type == EdgeType.CALLS:
            adj.setdefault(e.source, []).append(e.target)
            has_incoming.add(e.target)

    sources = [n for n in domain_ids if n not in has_incoming and n in adj]
    if not sources:
        return 0.0

    depths: list[int] = []
    for src in sources:
        visited: set[str] = {src}
        queue: deque[tuple[str, int]] = deque([(src, 0)])
        max_depth = 0
        while queue:
            node_id, depth = queue.popleft()
            max_depth = max(max_depth, depth)
            for child in adj.get(node_id, []):
                if child not in visited:
                    visited.add(child)
                    queue.append((child, depth + 1))
        depths.append(max_depth)

    return sum(depths) / len(depths)


def _max_dag_depth(domain_ids: set[str], internal_edges: list[Edge]) -> int:
    """Max BFS depth along IMPORTS edges within the domain (from import-root nodes)."""
    adj: dict[str, list[str]] = {}
    has_incoming: set[str] = set()
    for e in internal_edges:
        if e.type == EdgeType.IMPORTS:
            adj.setdefault(e.source, []).append(e.target)
            has_incoming.add(e.target)

    roots = [n for n in domain_ids if n not in has_incoming and n in adj]
    if not roots:
        return 0

    max_depth = 0
    for root in roots:
        visited: set[str] = {root}
        queue: deque[tuple[str, int]] = deque([(root, 0)])
        while queue:
            node_id, depth = queue.popleft()
            max_depth = max(max_depth, depth)
            for child in adj.get(node_id, []):
                if child not in visited:
                    visited.add(child)
                    queue.append((child, depth + 1))

    return max_depth


def _count_routers(domain_nodes: list[Node], all_edges: list[Edge]) -> int:
    """Count domain nodes with fan_out > 2 whose CALLS targets don't import them."""
    # Build: for each node, who imports it
    imported_by: dict[str, set[str]] = {}
    for e in all_edges:
        if e.type == EdgeType.IMPORTS:
            imported_by.setdefault(e.target, set()).add(e.source)

    router_count = 0
    for node in domain_nodes:
        fan_out = node.metadata.get("fan_out", 0)
        if fan_out <= _ROUTER_FAN_OUT_THRESHOLD:
            continue
        calls_targets = [
            e.target
            for e in all_edges
            if e.source == node.id and e.type == EdgeType.CALLS
        ]
        if len(calls_targets) <= _ROUTER_FAN_OUT_THRESHOLD:
            continue
        # Router: none of its CALLS targets imports it
        node_importers = imported_by.get(node.id, set())
        if not (set(calls_targets) & node_importers):
            router_count += 1

    return router_count


class FingerprintExtractor:
    """Compute a PatternFingerprint for a given FQN domain prefix from a SQLiteStore."""

    def __init__(self, store: SQLiteStore) -> None:
        """Accept an open SQLiteStore (must already be connected)."""
        self._store = store

    def extract(self, fqn_prefix: str) -> PatternFingerprint:
        """Return the structural fingerprint for all nodes under fqn_prefix."""
        all_nodes = self._store.get_all_nodes()
        all_edges = self._store.get_all_edges()

        # Enrich with fan_in, fan_out, in_cycle from the global graph
        enriched_all = HealthScorer(all_nodes, all_edges).enrich()

        domain_nodes = [n for n in enriched_all if _in_domain(n.id, fqn_prefix)]
        if not domain_nodes:
            return PatternFingerprint(
                domain=fqn_prefix,
                hub_count=0,
                star_count=0,
                chain_len=0.0,
                dag_depth=0,
                router_count=0,
                cycle_ratio=0.0,
                unresolved_ratio=0.0,
            )

        domain_ids = {n.id for n in domain_nodes}

        # Internal edges: both endpoints within the domain
        internal_edges = [
            e for e in all_edges if e.source in domain_ids and e.target in domain_ids
        ]
        # Outgoing edges from domain nodes (source in domain, any target)
        domain_outgoing = [e for e in all_edges if e.source in domain_ids]

        hub_count = sum(
            1
            for n in domain_nodes
            if n.metadata.get("fan_in", 0) > _HUB_FAN_IN_THRESHOLD
            and n.metadata.get("fan_out", 0) == 0
        )
        star_count = sum(
            1
            for n in domain_nodes
            if n.metadata.get("fan_out", 0) > _STAR_FAN_OUT_THRESHOLD
            and n.metadata.get("fan_in", 0) <= 1
        )
        chain_len = _avg_chain_length(domain_ids, internal_edges)
        dag_depth = _max_dag_depth(domain_ids, internal_edges)
        router_count = _count_routers(domain_nodes, all_edges)

        cycle_count = sum(1 for n in domain_nodes if n.metadata.get("in_cycle", False))
        cycle_ratio = cycle_count / len(domain_nodes)

        calls_edges = [e for e in domain_outgoing if e.type == EdgeType.CALLS]
        raw_calls = [e for e in calls_edges if e.target.startswith(RAW_CALL_PREFIX)]
        unresolved_ratio = len(raw_calls) / len(calls_edges) if calls_edges else 0.0

        return PatternFingerprint(
            domain=fqn_prefix,
            hub_count=hub_count,
            star_count=star_count,
            chain_len=chain_len,
            dag_depth=dag_depth,
            router_count=router_count,
            cycle_ratio=cycle_ratio,
            unresolved_ratio=unresolved_ratio,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_fingerprint.py -v
```
Expected: all 14 tests PASSED.

- [ ] **Step 5: Run full verification**

```bash
make type-check && make lint
```
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/cgis/query/fingerprint.py tests/unit/test_fingerprint.py
git commit -m "feat(#139): add PatternFingerprint dataclass and FingerprintExtractor"
```

---

## Task 3: `DomainConfig` + `DriftReport` + `DriftScorer`

**Files:**
- Create: `src/cgis/query/drift.py`
- Create: `tests/unit/test_drift.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_drift.py
"""Unit tests for DriftScorer and DriftReport."""

import pytest
import yaml

from cgis.query.drift import DomainConfig, DriftReport, DriftScorer
from cgis.query.fingerprint import PatternFingerprint

# ── minimal YAML for tests ────────────────────────────────────────────────────

_YAML = """\
version: "1.0.0"
drift_weights:
  hub_count:        0.15
  star_count:       0.15
  chain_len:        0.10
  dag_depth:        0.10
  router_count:     0.10
  cycle_ratio:      0.25
  unresolved_ratio: 0.15
patterns:
  pure_utility:
    description: "Hub pattern"
    hub_count:        {min: 1}
    star_count:       {exact: 0}
    cycle_ratio:      {max: 0.0}
    unresolved_ratio: {max: 0.1}
  pipeline_stage:
    description: "Chain pattern"
    chain_len:        {min: 2.0}
    cycle_ratio:      {max: 0.0}
project_domains:
  - name: "extraction"
    fqn_prefix: "cgis.extractors"
    expected_pattern: pure_utility
    drift_tolerance: 0.15
  - name: "resolution"
    fqn_prefix: "cgis.resolver"
    expected_pattern: pipeline_stage
    drift_tolerance: 0.20
"""


@pytest.fixture
def scorer(tmp_path: pytest.TempPathFactory) -> DriftScorer:
    """Return a DriftScorer loaded from the minimal YAML fixture."""
    p = tmp_path / "patterns.yaml"  # type: ignore[operator]
    p.write_text(_YAML)
    return DriftScorer(str(p))


@pytest.fixture
def pure_util_domain() -> DomainConfig:
    return DomainConfig(
        name="extraction",
        fqn_prefix="cgis.extractors",
        expected_pattern="pure_utility",
        drift_tolerance=0.15,
    )


# ── load_project_domains ──────────────────────────────────────────────────────


def test_load_project_domains_count(scorer: DriftScorer) -> None:
    """load_project_domains returns one DomainConfig per entry in project_domains."""
    domains = scorer.load_project_domains()
    assert len(domains) == 2


def test_load_project_domains_fields(scorer: DriftScorer) -> None:
    """First domain has the expected field values."""
    domains = scorer.load_project_domains()
    d = domains[0]
    assert d.name == "extraction"
    assert d.fqn_prefix == "cgis.extractors"
    assert d.expected_pattern == "pure_utility"
    assert d.drift_tolerance == 0.15


# ── drift = 0 when perfectly matching ────────────────────────────────────────


def test_drift_zero_when_actual_matches_ideal(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """hub_count=1, star_count=0, cycle_ratio=0.0, unresolved_ratio=0.0 → drift=0, status=clean."""
    perfect = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=1,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )
    report = scorer.score(perfect, pure_util_domain)
    assert report.drift_score == 0.0
    assert report.status == "clean"
    assert report.violations == []


# ── satisfying min constraint doesn't create drift ───────────────────────────


def test_exceeding_min_does_not_penalise(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """hub_count=5 satisfies min:1 — drift contribution from hub_count must be 0."""
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=5,  # well above min=1, no violation
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )
    report = scorer.score(fp, pure_util_domain)
    assert report.drift_score == 0.0


# ── violations list ───────────────────────────────────────────────────────────


def test_violations_list_when_below_min(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """hub_count=0 violates min:1 → violation string includes 'hub_count'."""
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=0,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )
    report = scorer.score(fp, pure_util_domain)
    assert any("hub_count" in v for v in report.violations)


def test_violations_list_when_above_max(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """cycle_ratio=0.5 violates max:0.0 → violation string includes 'cycle_ratio'."""
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=1,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.5,
        unresolved_ratio=0.0,
    )
    report = scorer.score(fp, pure_util_domain)
    assert any("cycle_ratio" in v for v in report.violations)


def test_violations_list_when_exact_mismatch(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """star_count=2 violates exact:0 → violation string includes 'star_count'."""
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=1,
        star_count=2,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )
    report = scorer.score(fp, pure_util_domain)
    assert any("star_count" in v for v in report.violations)


# ── status thresholds ─────────────────────────────────────────────────────────


def test_status_critical_for_god_object(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """All constrained components violated → status=critical."""
    god_object = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=0,       # violates min:1
        star_count=5,      # violates exact:0
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.8,   # violates max:0.0
        unresolved_ratio=0.9,  # violates max:0.1
    )
    report = scorer.score(god_object, pure_util_domain)
    assert report.status == "critical"
    assert report.drift_score >= 0.50


def test_status_clean_below_threshold(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """A slightly imperfect fingerprint below 0.20 → status=clean."""
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=1,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.05,  # below max:0.1, no violation, no drift
    )
    report = scorer.score(fp, pure_util_domain)
    assert report.status == "clean"


# ── report fields ──────────────────────────────────────────────────────────────


def test_report_contains_domain_metadata(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """DriftReport carries domain name, fqn_prefix, expected_pattern, tolerance."""
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=1, star_count=0, chain_len=0.0, dag_depth=0,
        router_count=0, cycle_ratio=0.0, unresolved_ratio=0.0,
    )
    report = scorer.score(fp, pure_util_domain)
    assert report.domain == "extraction"
    assert report.fqn_prefix == "cgis.extractors"
    assert report.expected_pattern == "pure_utility"
    assert report.tolerance == 0.15


# ── unconstrained components ──────────────────────────────────────────────────


def test_unconstrained_components_add_no_drift(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """chain_len and dag_depth are unconstrained in pure_utility — any value → zero drift."""
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=1,
        star_count=0,
        chain_len=100.0,   # unconstrained — must not add drift
        dag_depth=50,      # unconstrained — must not add drift
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )
    report = scorer.score(fp, pure_util_domain)
    assert report.drift_score == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_drift.py -v
```
Expected: `ImportError` — `cannot import name 'DriftScorer'`.

- [ ] **Step 3: Implement `src/cgis/query/drift.py`**

```python
"""DomainConfig, DriftReport, and DriftScorer for architectural drift measurement."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from cgis.query.fingerprint import PatternFingerprint

_COMPONENT_NAMES = (
    "hub_count",
    "star_count",
    "chain_len",
    "dag_depth",
    "router_count",
    "cycle_ratio",
    "unresolved_ratio",
)

_STATUS_WARNING = 0.20
_STATUS_CRITICAL = 0.50


@dataclass(frozen=True)
class DomainConfig:
    """Project-level domain expectation loaded from patterns.yaml."""

    name: str
    fqn_prefix: str
    expected_pattern: str
    drift_tolerance: float


@dataclass(frozen=True)
class DriftReport:
    """Per-domain drift analysis result."""

    domain: str
    fqn_prefix: str
    expected_pattern: str
    actual: PatternFingerprint
    ideal: PatternFingerprint
    drift_score: float
    violations: list[str]
    status: Literal["clean", "warning", "critical"]
    tolerance: float


def _classify(score: float) -> Literal["clean", "warning", "critical"]:
    if score < _STATUS_WARNING:
        return "clean"
    if score < _STATUS_CRITICAL:
        return "warning"
    return "critical"


class DriftScorer:
    """Load patterns.yaml and score actual PatternFingerprints against ideal templates."""

    def __init__(self, patterns_config: str) -> None:
        """Load and parse the patterns YAML file at patterns_config path."""
        raw: dict[str, Any] = yaml.safe_load(Path(patterns_config).read_text())
        self._weights: dict[str, float] = raw["drift_weights"]
        self._patterns: dict[str, dict[str, Any]] = raw["patterns"]
        self._project_domains: list[dict[str, Any]] = raw["project_domains"]

    def load_project_domains(self) -> list[DomainConfig]:
        """Return all project domains declared in patterns.yaml."""
        return [
            DomainConfig(
                name=d["name"],
                fqn_prefix=d["fqn_prefix"],
                expected_pattern=d["expected_pattern"],
                drift_tolerance=float(d["drift_tolerance"]),
            )
            for d in self._project_domains
        ]

    def score(self, actual: PatternFingerprint, domain: DomainConfig) -> DriftReport:
        """Compute the drift score and return a DriftReport."""
        template = self._patterns[domain.expected_pattern]
        constraints = self._parse_constraints(template)

        if not constraints:
            ideal_fp = PatternFingerprint(
                domain=domain.fqn_prefix,
                hub_count=0, star_count=0, chain_len=0.0,
                dag_depth=0, router_count=0, cycle_ratio=0.0, unresolved_ratio=0.0,
            )
            return DriftReport(
                domain=domain.name,
                fqn_prefix=domain.fqn_prefix,
                expected_pattern=domain.expected_pattern,
                actual=actual,
                ideal=ideal_fp,
                drift_score=0.0,
                violations=[],
                status="clean",
                tolerance=domain.drift_tolerance,
            )

        total_weight = sum(self._weights[name] for name in constraints)
        violations: list[str] = []
        drift_sum = 0.0
        ideal_overrides: dict[str, float] = {}

        for name, (op, value) in constraints.items():
            actual_val = float(getattr(actual, name))
            fvalue = float(value)

            if op == "min":
                ideal_val = fvalue
                norm = max(ideal_val, 1.0)
                raw = max(0.0, ideal_val - actual_val)
                if actual_val < fvalue:
                    violations.append(f"{name} {actual_val} < min {fvalue}")
            elif op == "max":
                ideal_val = 0.0
                norm = max(fvalue, 1.0)
                raw = max(0.0, actual_val - fvalue)
                if actual_val > fvalue:
                    violations.append(f"{name} {actual_val} > max {fvalue}")
            else:  # exact
                ideal_val = fvalue
                norm = max(ideal_val, 1.0)
                raw = abs(actual_val - ideal_val)
                if actual_val != fvalue:
                    violations.append(f"{name} {actual_val} != exact {fvalue}")

            ideal_overrides[name] = ideal_val
            component_drift = min(raw / norm, 1.0)
            weight = self._weights[name] / total_weight
            drift_sum += weight * component_drift

        ideal_fp = PatternFingerprint(
            domain=domain.fqn_prefix,
            hub_count=int(ideal_overrides.get("hub_count", 0)),
            star_count=int(ideal_overrides.get("star_count", 0)),
            chain_len=float(ideal_overrides.get("chain_len", 0.0)),
            dag_depth=int(ideal_overrides.get("dag_depth", 0)),
            router_count=int(ideal_overrides.get("router_count", 0)),
            cycle_ratio=float(ideal_overrides.get("cycle_ratio", 0.0)),
            unresolved_ratio=float(ideal_overrides.get("unresolved_ratio", 0.0)),
        )

        return DriftReport(
            domain=domain.name,
            fqn_prefix=domain.fqn_prefix,
            expected_pattern=domain.expected_pattern,
            actual=actual,
            ideal=ideal_fp,
            drift_score=round(drift_sum, 6),
            violations=violations,
            status=_classify(drift_sum),
            tolerance=domain.drift_tolerance,
        )

    def _parse_constraints(
        self, template: dict[str, Any]
    ) -> dict[str, tuple[str, float]]:
        """Extract (operator, value) pairs for each constrained component in a template."""
        result: dict[str, tuple[str, float]] = {}
        for name in _COMPONENT_NAMES:
            constraint = template.get(name)
            if constraint is None or not isinstance(constraint, dict):
                continue
            for op in ("min", "max", "exact"):
                if op in constraint:
                    result[name] = (op, float(constraint[op]))
                    break
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_drift.py -v
```
Expected: all 13 tests PASSED.

- [ ] **Step 5: Run full verification**

```bash
make type-check && make lint
```
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/cgis/query/drift.py tests/unit/test_drift.py
git commit -m "feat(#139): add DriftScorer and DriftReport"
```

---

## Task 4: `cgis drift` CLI command

**Files:**
- Modify: `src/cgis/cli.py`
- Test: `tests/unit/test_cli.py` (existing file — append new tests)

- [ ] **Step 1: Write the failing tests**

Read `tests/unit/test_cli.py` first, then append:

```python
# Append to tests/unit/test_cli.py

def test_drift_exits_0_when_all_clean(tmp_path: pytest.TempPathFactory) -> None:
    """cgis drift exits 0 when all domains are clean."""
    # Build a minimal DB
    db_path = str(tmp_path / "g.db")  # type: ignore[operator]
    with SQLiteStore(db_path) as store:
        store.save_graph([], [], overwrite=True)

    # Build a patterns.yaml where no domains exist (trivially all clean)
    patterns_path = str(tmp_path / "patterns.yaml")  # type: ignore[operator]
    Path(patterns_path).write_text(
        "version: '1.0.0'\n"
        "drift_weights:\n"
        "  hub_count: 0.15\n  star_count: 0.15\n  chain_len: 0.10\n"
        "  dag_depth: 0.10\n  router_count: 0.10\n  cycle_ratio: 0.25\n"
        "  unresolved_ratio: 0.15\n"
        "patterns:\n"
        "  pure_utility:\n    description: x\n    cycle_ratio: {max: 0.0}\n"
        "project_domains: []\n"
    )

    result = runner.invoke(app, ["drift", "--db", db_path, "--patterns", patterns_path])
    assert result.exit_code == 0


def test_drift_exits_1_when_any_critical(tmp_path: pytest.TempPathFactory) -> None:
    """cgis drift exits 1 when at least one domain is critical."""
    db_path = str(tmp_path / "g.db")  # type: ignore[operator]
    with SQLiteStore(db_path) as store:
        store.save_graph([], [], overwrite=True)

    patterns_path = str(tmp_path / "patterns.yaml")  # type: ignore[operator]
    # Domain with cycle_ratio max:0.0 and high penalty weight — empty graph
    # gives cycle_ratio=0.0 which is fine, but hub_count min:10 on an empty
    # domain will drive drift to 1.0 → critical.
    Path(patterns_path).write_text(
        "version: '1.0.0'\n"
        "drift_weights:\n"
        "  hub_count: 1.0\n  star_count: 0.0\n  chain_len: 0.0\n"
        "  dag_depth: 0.0\n  router_count: 0.0\n  cycle_ratio: 0.0\n"
        "  unresolved_ratio: 0.0\n"
        "patterns:\n"
        "  needs_hub:\n    description: x\n    hub_count: {min: 10}\n"
        "project_domains:\n"
        "  - name: test\n    fqn_prefix: nonexistent\n"
        "    expected_pattern: needs_hub\n    drift_tolerance: 0.10\n"
    )

    result = runner.invoke(app, ["drift", "--db", db_path, "--patterns", patterns_path])
    assert result.exit_code == 1


def test_drift_missing_db_exits_1(tmp_path: pytest.TempPathFactory) -> None:
    """cgis drift exits 1 when --db path does not exist."""
    patterns_path = str(tmp_path / "patterns.yaml")  # type: ignore[operator]
    Path(patterns_path).write_text("version: '1.0.0'\ndrift_weights: {}\npatterns: {}\nproject_domains: []\n")
    result = runner.invoke(app, ["drift", "--db", "no_such.db", "--patterns", patterns_path])
    assert result.exit_code == 1


def test_drift_missing_patterns_exits_1(tmp_path: pytest.TempPathFactory) -> None:
    """cgis drift exits 1 when --patterns path does not exist."""
    db_path = str(tmp_path / "g.db")  # type: ignore[operator]
    with SQLiteStore(db_path) as store:
        store.save_graph([], [], overwrite=True)
    result = runner.invoke(app, ["drift", "--db", db_path, "--patterns", "no_such.yaml"])
    assert result.exit_code == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_cli.py -k "drift" -v
```
Expected: `FAILED` — no `drift` subcommand registered yet.

- [ ] **Step 3: Add `drift` command to `src/cgis/cli.py`**

Add at the top with the other imports:
```python
from cgis.query.drift import DriftScorer
from cgis.query.fingerprint import FingerprintExtractor
```

Add the command before `if __name__ == "__main__":`:

```python
@app.command()
def drift(
    db: str = typer.Option(_DEFAULT_DB, "--db", "-d", help=_DEFAULT_DB_HELP),
    patterns: str = typer.Option(
        "docs/ontology/patterns.yaml",
        "--patterns",
        "-p",
        help="Path to a patterns.yaml file with domain expectations.",
    ),
    output_format: OutputFormat = typer.Option(
        OutputFormat.TEXT, "--format", "-f", help="Output format: text or json"
    ),
    max_drift: float = typer.Option(
        0.50,
        "--max-drift",
        min=0.0,
        max=1.0,
        help="Override critical threshold (default 0.50).",
    ),
) -> None:
    """
    Report per-domain architectural drift against declared ideal patterns.

    Exits with code 1 if any domain drift score meets or exceeds the critical threshold.
    """
    if not Path(db).is_file():
        console.print(f"[bold red]❌ Database not found:[/bold red] {db}. Run `ingest` first.")
        raise typer.Exit(code=1)

    if not Path(patterns).is_file():
        console.print(f"[bold red]❌ Patterns file not found:[/bold red] {patterns}")
        raise typer.Exit(code=1)

    scorer = DriftScorer(patterns)
    domains = scorer.load_project_domains()

    reports = []
    with SQLiteStore(db) as store:
        extractor = FingerprintExtractor(store)
        for domain in domains:
            fp = extractor.extract(domain.fqn_prefix)
            reports.append(scorer.score(fp, domain))

    any_critical = any(r.drift_score >= max_drift for r in reports)

    if output_format.value == "json":
        import dataclasses

        typer.echo(_json.dumps([dataclasses.asdict(r) for r in reports], indent=2))
        if any_critical:
            raise typer.Exit(code=1)
        return

    def _status_label(score: float) -> str:
        if score >= max_drift:
            return "[bold red]❌ critical[/bold red]"
        if score >= 0.20:
            return "[yellow]⚠️  warning[/yellow]"
        return "[green]✅ clean[/green]"

    table = Table(title="Architectural Drift Report")
    table.add_column("Domain", style="cyan")
    table.add_column("Expected Pattern", style="dim")
    table.add_column("Drift", justify="right")
    table.add_column("Status", justify="center")

    for r in reports:
        table.add_row(r.fqn_prefix, r.expected_pattern, f"{r.drift_score:.2f}", _status_label(r.drift_score))

    console.print(table)

    if any_critical:
        console.print("[bold red]❌ One or more domains exceed the drift threshold.[/bold red]")
        raise typer.Exit(code=1)

    console.print("[bold green]✅ All domains within tolerance.[/bold green]")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_cli.py -k "drift" -v
```
Expected: 4 drift tests PASSED.

- [ ] **Step 5: Run full suite + type check**

```bash
make type-check && make lint && uv run pytest tests/unit/test_cli.py -v
```
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/cgis/cli.py tests/unit/test_cli.py
git commit -m "feat(#139): add cgis drift CLI command"
```

---

## Task 5: `gen_ideal_graph.py --from-ontology` mode

**Files:**
- Modify: `scripts/gen_ideal_graph.py`
- Test: `tests/unit/test_gen_ideal_graph.py` (existing file — append new tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_gen_ideal_graph.py`:

```python
# Append to tests/unit/test_gen_ideal_graph.py

import tempfile
from pathlib import Path as _Path

_MINIMAL_PATTERNS = """\
version: "1.0.0"
drift_weights:
  hub_count: 0.15
  star_count: 0.15
  chain_len: 0.10
  dag_depth: 0.10
  router_count: 0.10
  cycle_ratio: 0.25
  unresolved_ratio: 0.15
patterns:
  pure_utility:
    description: "Hub"
    hub_count: {min: 1}
    cycle_ratio: {max: 0.0}
  layered_dag:
    description: "DAG"
    dag_depth: {min: 3}
    cycle_ratio: {max: 0.0}
project_domains:
  - name: extraction
    fqn_prefix: cgis.extractors
    expected_pattern: pure_utility
    drift_tolerance: 0.15
  - name: query
    fqn_prefix: cgis.query
    expected_pattern: layered_dag
    drift_tolerance: 0.20
"""


def test_from_ontology_returns_valid_schema() -> None:
    """generate_from_ontology returns a graph with metadata, nodes, edges."""
    # Must import here after sys.path.insert in module setup
    from gen_ideal_graph import generate_from_ontology

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(_MINIMAL_PATTERNS)
        patterns_path = f.name

    graph = generate_from_ontology(patterns_path)
    assert set(graph.keys()) == {"metadata", "nodes", "edges"}
    assert graph["metadata"]["node_count"] == len(graph["nodes"])
    assert graph["metadata"]["edge_count"] == len(graph["edges"])


def test_from_ontology_no_duplicate_node_ids() -> None:
    """Nodes generated for multiple domains must have unique IDs."""
    from gen_ideal_graph import generate_from_ontology

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(_MINIMAL_PATTERNS)
        patterns_path = f.name

    graph = generate_from_ontology(patterns_path)
    ids = [n["id"] for n in graph["nodes"]]
    assert len(ids) == len(set(ids)), "Duplicate node IDs"


def test_from_ontology_node_ids_use_domain_prefix() -> None:
    """All node IDs must start with the domain fqn_prefix."""
    from gen_ideal_graph import generate_from_ontology

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(_MINIMAL_PATTERNS)
        patterns_path = f.name

    graph = generate_from_ontology(patterns_path)
    known_prefixes = {"cgis.extractors", "cgis.query"}
    for node in graph["nodes"]:
        nid = node["id"]
        assert any(nid == p or nid.startswith(p + ".") for p in known_prefixes), (
            f"Node ID '{nid}' does not match any domain prefix"
        )


def test_from_ontology_no_dangling_edges() -> None:
    """All edge source/target must refer to existing node IDs."""
    from gen_ideal_graph import generate_from_ontology

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(_MINIMAL_PATTERNS)
        patterns_path = f.name

    graph = generate_from_ontology(patterns_path)
    node_ids = {n["id"] for n in graph["nodes"]}
    for edge in graph["edges"]:
        assert edge["source"] in node_ids
        assert edge["target"] in node_ids
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_gen_ideal_graph.py -k "ontology" -v
```
Expected: `ImportError` — `cannot import name 'generate_from_ontology'`.

- [ ] **Step 3: Add `generate_from_ontology` and `--from-ontology` to `scripts/gen_ideal_graph.py`**

Add `import yaml` after the existing imports at the top of `gen_ideal_graph.py`, then add the new function before `main()`:

```python
import yaml  # add with existing stdlib imports
```

Add the new function before `main()`:

```python
# Pattern name → existing builder function.
# Patterns not listed here fall back to build_hub as a generic "has-nodes" scaffold.
_PATTERN_TO_BUILDER: dict[str, Any] = {
    "pure_utility": build_hub,
    "pipeline_stage": build_chain,
    "orchestrator": build_star,
    "layered_dag": build_dag,
    "dispatcher": build_star,  # closest structural match available in v1
}


def generate_from_ontology(patterns_path: str) -> dict[str, Any]:
    """Generate an ideal graph from a patterns.yaml file.

    For each project_domain, instantiates the expected_pattern template
    using the real fqn_prefix as the node namespace.
    """
    raw: dict[str, Any] = yaml.safe_load(Path(patterns_path).read_text())
    domains: list[dict[str, Any]] = raw.get("project_domains", [])

    all_nodes: list[GraphDict] = []
    all_edges: list[GraphDict] = []

    for domain in domains:
        fqn_prefix: str = domain["fqn_prefix"]
        pattern_name: str = domain["expected_pattern"]
        builder = _PATTERN_TO_BUILDER.get(pattern_name, build_hub)
        pat_nodes, pat_edges = builder()
        ns_nodes, ns_edges = _prefix(pat_nodes, pat_edges, fqn_prefix)
        all_nodes.extend(ns_nodes)
        all_edges.extend(ns_edges)

    return {
        "metadata": {
            "source_path": patterns_path,
            "node_count": len(all_nodes),
            "edge_count": len(all_edges),
        },
        "nodes": all_nodes,
        "edges": all_edges,
    }
```

Extend `main()` to add `--from-ontology` argument:

```python
def main() -> None:
    """Entry point for the ideal graph generator script."""
    parser = argparse.ArgumentParser(
        description="Generate a synthetic ideal architecture graph.json."
    )
    parser.add_argument(
        "--pattern",
        "-p",
        choices=[*PATTERNS, "all"],
        default=None,
        help="Graph pattern to generate.",
    )
    parser.add_argument(
        "--from-ontology",
        metavar="PATTERNS_YAML",
        default=None,
        help="Generate ideal graph from a patterns.yaml file (project_domains).",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="-",
        help="Output file path (default: stdout)",
    )
    args = parser.parse_args()

    if args.from_ontology:
        graph = generate_from_ontology(args.from_ontology)
    elif args.pattern:
        graph = generate(args.pattern)
    else:
        graph = generate("all")

    if args.output == "-":
        json.dump(graph, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        Path(args.output).write_text(json.dumps(graph, indent=2) + "\n")
        node_count = graph["metadata"]["node_count"]
        edge_count = graph["metadata"]["edge_count"]
        print(f"Written {node_count} nodes, {edge_count} edges → {args.output}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_gen_ideal_graph.py -v
```
Expected: all existing + 4 new tests PASSED.

- [ ] **Step 5: Run full verification**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
```
Expected: all checks pass. Fix any mypy or docstring issues before committing.

- [ ] **Step 6: Commit**

```bash
git add scripts/gen_ideal_graph.py tests/unit/test_gen_ideal_graph.py
git commit -m "feat(#139): add gen_ideal_graph --from-ontology mode"
```

---

## Task 6: Push and open PR

- [ ] **Step 1: Final full check**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
```
Expected: all green.

- [ ] **Step 2: Push branch and open PR**

```bash
git push -u origin feat/issue-139-fingerprint-drift
gh pr create \
  --title "feat(#139): domain pattern fingerprint & drift engine" \
  --body "Closes #139

## Summary
- Add \`docs/ontology/patterns.yaml\` with 5 pattern templates and 5 project domains
- Add \`src/cgis/query/fingerprint.py\`: \`PatternFingerprint\` + \`FingerprintExtractor\`
- Add \`src/cgis/query/drift.py\`: \`DriftScorer\` + \`DriftReport\`
- Add \`cgis drift\` CLI command (exits 1 if any domain critical)
- Add \`gen_ideal_graph.py --from-ontology\` mode

## Test plan
- [ ] \`uv run pytest tests/unit/test_patterns_yaml.py\`
- [ ] \`uv run pytest tests/unit/test_fingerprint.py\`
- [ ] \`uv run pytest tests/unit/test_drift.py\`
- [ ] \`uv run pytest tests/unit/test_cli.py -k drift\`
- [ ] \`uv run pytest tests/unit/test_gen_ideal_graph.py\`
- [ ] \`make type-check && make lint && make doc-coverage\`
- [ ] \`uv run cgis ingest . --source-root src --output /tmp/g.db && uv run cgis drift --db /tmp/g.db --patterns docs/ontology/patterns.yaml\`
" \
  --base main
```
