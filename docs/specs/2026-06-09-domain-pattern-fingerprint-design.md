# Domain Pattern Fingerprint & Drift Engine — Design Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Measure how far a real codebase's domain structure drifts from its declared ideal architectural patterns, producing a quantitative drift score per domain.

**Architecture:** `patterns.yaml` declares expected pattern templates per domain prefix → `FingerprintExtractor` computes actual fingerprint vectors from the graph → `DriftScorer` computes per-component normalized weighted deviation between ideal and actual → `cgis drift` CLI reports per-domain health.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML, existing `SQLiteStore` + `HealthScorer`, Typer CLI.

---

## Background & Motivation

The graph already has three semantic levels embedded in `core.yaml`:

- **Structural (L0/L1):** `CONTAINS`, `DECLARES`, `IMPORTS` — where things live
- **Behavioral (L1/L2):** `CALLS`, `REFERENCES` — how things flow
- **Semantic (L3):** `HANDLES`, `PERSISTS`, `AUTHORIZES` — why things exist

Each domain has a unique *pattern fingerprint* — a characteristic combination of structural patterns (hub, star, chain, dag, dispatcher) that reflects its architectural intent. Knowing the fingerprint, you can probabilistically infer the domain. Knowing the domain, you can prescribe the expected fingerprint and measure drift.

**The drift model:** `ideal.graph.json ↔ self-desc.graph.json`. Two artifacts in the same format; the distance between them is architectural debt made quantitative.

---

## Section 1 — Pattern Fingerprint Model

### `PatternFingerprint` dataclass

```python
@dataclass
class PatternFingerprint:
    domain: str               # e.g. "cgis.resolver"

    # Pattern counts (structural topology)
    hub_count: int            # nodes with fan_in > threshold AND fan_out == 0
    star_count: int           # nodes with fan_out > threshold AND fan_in <= 1
    chain_len: float          # avg length of CALLS chains within domain
    dag_depth: int            # max depth of IMPORTS DAG within domain
    router_count: int         # nodes with fan_out > 2 where targets share no IMPORTS
                              # (dispatcher / first-match-wins strategy)

    # Health aggregates
    cycle_ratio: float        # fraction of domain nodes involved in a cycle
    unresolved_ratio: float   # fraction of CALLS edges with raw_call: target
```

### Drift score

```
drift(ideal, actual) = sum(w_i * clip(|actual_i - ideal_i| / norm_i, 0, 1))
                     ∈ [0.0, 1.0]
```

Where:
- Sum runs over **constrained components only** (those with `min`/`max`/`exact` in the pattern template)
- `norm_i = max(ideal_i, 1)` by default — overridable per-component in `patterns.yaml`
- `w_i` are re-normalized to sum 1.0 after excluding unconstrained components
- Unconstrained components contribute 0 by construction (excluded, not zeroed out)

Rationale: cosine distance measures direction, not magnitude — `hub_count` ideal=1/actual=5 and ideal=1/actual=100 look the same to cosine. Per-component normalized deviation captures *how much* each structural constraint is violated independently.

- `0.0` — actual perfectly matches ideal
- `1.0` — complete divergence

Status thresholds:
- `< 0.20` → `clean` ✅
- `0.20–0.49` → `warning` ⚠️
- `≥ 0.50` → `critical` ❌

---

## Section 2 — `docs/ontology/patterns.yaml` Schema

New file alongside existing `domains.yaml`. Separated by concern: `domains.yaml` = semantic tagging, `patterns.yaml` = structural expectations.

```yaml
version: "1.0.0"

# Weights for drift score (must sum to 1.0)
drift_weights:
  hub_count:        0.15
  star_count:       0.15
  chain_len:        0.10
  dag_depth:        0.10
  router_count:     0.10
  cycle_ratio:      0.25   # most penalized — cycles always harmful
  unresolved_ratio: 0.15

# Named ideal pattern templates
patterns:
  pure_utility:
    description: "Shared library called by many, depends on nothing"
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
    description: "Coordinates N independent services, no leaf-to-leaf edges"
    star_count:       {min: 1}
    hub_count:        {max: 1}
    cycle_ratio:      {max: 0.0}

  layered_dag:
    description: "Clean layered architecture, no upward dependencies"
    dag_depth:        {min: 3}
    cycle_ratio:      {max: 0.0}

  dispatcher:
    # planned — requires ResolverEngine split (#115)
    description: "Routes to first matching strategy, mutually exclusive paths"
    router_count:     {min: 1}
    star_count:       {exact: 0}

# Project-specific domain expectations
project_domains:
  - name: "extraction"
    fqn_prefix: "cgis.extractors"
    expected_pattern: pure_utility
    drift_tolerance: 0.15

  - name: "resolution"
    fqn_prefix: "cgis.resolver"
    expected_pattern: pipeline_stage   # target state after #115
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

---

## Section 3 — Components

### File map

```
docs/ontology/patterns.yaml              ← new: pattern schema
src/cgis/query/fingerprint.py            ← new: PatternFingerprint + FingerprintExtractor
src/cgis/query/drift.py                  ← new: DriftScorer + DriftReport
src/cgis/cli.py                          ← modified: add `cgis drift` command
scripts/gen_ideal_graph.py               ← modified: add --from-ontology mode
tests/unit/test_fingerprint.py           ← new
tests/unit/test_drift.py                 ← new
```

### `FingerprintExtractor`

Reads graph from `SQLiteStore`, computes fingerprint vector for a given `fqn_prefix`.

```python
class FingerprintExtractor:
    def __init__(self, store: SQLiteStore) -> None: ...

    def extract(self, fqn_prefix: str) -> PatternFingerprint:
        # 1. Load nodes where _in_domain(node.id, fqn_prefix)
        #    _in_domain(fqn, prefix) = fqn == prefix or fqn.startswith(prefix + ".")
        #    (avoids false positives: "cgis.extractors_alt" ≠ "cgis.extractors")
        # 2. Load edges WHERE source OR target in domain node set
        # 3. Reuse HealthScorer.enrich() for fan_in, fan_out, in_cycle per node
        # 4. hub_count   = nodes with fan_in > 2 AND fan_out == 0
        # 5. star_count  = nodes with fan_out > 3 AND fan_in <= 1
        # 6. chain_len   = avg BFS depth along CALLS edges
        # 7. dag_depth   = max depth of IMPORTS subgraph (BFS from roots)
        # 8. router_count = nodes with fan_out > 2 where CALLS targets
        #                   share no IMPORTS edge with source
        # 9. cycle_ratio = len(in_cycle nodes) / len(domain nodes)
        # 10. unresolved_ratio = raw_call edges / total CALLS edges
```

Thresholds for hub/star detection are configurable, defaulting to:
- hub: `fan_in > 2`, `fan_out == 0`
- star: `fan_out > 3`, `fan_in <= 1`

### `DriftScorer`

Loads `patterns.yaml`, constructs ideal fingerprint from template constraints, computes per-component normalized weighted deviation (weighted MAE on constrained components only).

```python
class DriftScorer:
    def __init__(self, patterns_config: str) -> None: ...

    def score(
        self,
        actual: PatternFingerprint,
        domain: DomainConfig,
    ) -> DriftReport: ...


@dataclass
class DriftReport:
    domain: str
    fqn_prefix: str
    expected_pattern: str
    actual: PatternFingerprint
    ideal: PatternFingerprint        # reconstructed from template constraints
    drift_score: float               # 0.0–1.0
    violations: list[str]            # ["cycle_ratio 0.12 > max 0.0", ...]
    status: Literal["clean", "warning", "critical"]
    tolerance: float                 # from patterns.yaml domain config
```

**Ideal vector construction:** For each component, if constraint is `{min: X}` → ideal = X, `{max: X}` → ideal = 0, `{exact: X}` → ideal = X. Unconstrained components are **excluded from the sum entirely** — their weight is set to 0 and `w_i` values are re-normalized over constrained components only.

### `cgis drift` CLI command

```bash
cgis drift --db graph.db --patterns docs/ontology/patterns.yaml

# stdout (text):
Domain           Expected Pattern   Drift   Status
cgis.extractors  pure_utility       0.04    ✅ clean
cgis.resolver    pipeline_stage     0.71    ❌ critical
cgis.pipeline    orchestrator       0.18    ✅ clean
cgis.storage     pure_utility       0.09    ✅ clean
cgis.query       layered_dag        0.23    ⚠️  warning

# --format json → machine-readable DriftReport list
# exit code 1 if any domain status == "critical"
# --max-drift FLOAT → override critical threshold (default 0.50)
```

### `gen_ideal_graph.py --from-ontology` mode

New mode generates ideal graph with real FQN prefixes instead of toy names:

```bash
python scripts/gen_ideal_graph.py \
  --from-ontology docs/ontology/patterns.yaml \
  --output ideal.graph.json
```

For each `project_domain`, instantiates the `expected_pattern` template using the real `fqn_prefix` as namespace: generates minimal node/edge set satisfying all pattern constraints. Output format identical to `cgis ingest` JSON output — ready for UI overlay.

---

## Section 4 — CI Integration

```yaml
# .github/workflows/ci.yml
- name: Architecture drift check
  run: |
    uv run cgis ingest . --source-root src --output /tmp/graph.db
    uv run cgis drift \
      --db /tmp/graph.db \
      --patterns docs/ontology/patterns.yaml \
      --max-drift 0.5
```

---

## Out of Scope (v1)

These are explicitly deferred to future issues:

- **Probabilistic inference** (`fingerprint → probable domain`) — requires labeled training data
- **Cross-language `SERVES` edges** (`py ↔ ts` concept unification) — Level 4 ontological layer
- **UI overlay** (ideal vs actual graph side-by-side) — depends on this feature first
- **`dispatcher` pattern matching** — requires ResolverEngine refactor (#115) first
- **Fractal validation** (recursive pattern check at function/class/module/package scales) — v2

---

## Acceptance Criteria

- [ ] `docs/ontology/patterns.yaml` validates against a JSON Schema (tested)
- [ ] `FingerprintExtractor.extract()` returns correct fingerprint for known test graphs
- [ ] `DriftScorer.score()` returns `drift=0.0` when actual matches ideal exactly
- [ ] `DriftScorer.score()` returns `status=critical` for known God Object graph
- [ ] `cgis drift` exits 0 when all domains clean, exits 1 when any critical
- [ ] `gen_ideal_graph.py --from-ontology` produces valid graph.json
- [ ] mypy strict + ruff clean
- [ ] docstring coverage ≥ 90%
