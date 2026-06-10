# Motif-Basis Fingerprint v2 (Part B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace heuristic topological constraint scoring with a 13-triad census fingerprint and weighted total-variation distance to hand-authored ideal points, plus observe-only quotient (k=1) drift.

**Architecture:** New `triads.py` module computes the census (Batagelj–Mrvar tricode classification, no new dependencies). `PatternFingerprint` gains two 13-vectors (`t_imports`, `t_calls`). `DriftScorer.score()` gains a v2 path — `drift = renorm(w_imports·tv_imp + w_calls·disc·tv_calls + w_gates·gate_drift)` — selected per-domain by config presence (template has `ideal:` AND profile has `layers:`); everything else falls back to the untouched v1 path, so all existing fixtures stay green. A quotient builder collapses domains to single nodes and runs the SAME extractor+scorer against a `project_level` binding with `enforce: false`.

**Tech Stack:** Python 3.12, frozen dataclasses, PyYAML, pytest, mypy strict. No networkx — census is ~80 lines.

**Spec:** `docs/specs/2026-06-09-pattern-alphabet-motif-basis-design.md` §3 (all open questions resolved in PR #142 review).

---

## Design decisions locked by live measurement (2026-06-10, post-#143 main)

Measured with a throwaway networkx script (`nx.triadic_census`) on fresh ingests of `src/` and `ui/src`. These numbers are FACTS the plan asserts; if a task measures something wildly different (>±0.05), STOP — that is a census-implementation bug, not a ratchet renegotiation.

### Per-domain censuses (normalized over connected triples; zero classes omitted)

```
python src/ (688 nodes, 1777 edges)
cgis.extractors IMPORTS  triples=1    {021U 1.0}
cgis.extractors CALLS    triples=414  {021D .372, 021U .251, 021C .300, 111D .017, 111U .014, 030T .039, 120U .005, 120C .002}
cgis.resolver   IMPORTS  triples=0
cgis.resolver   CALLS    triples=126  {021D .429, 021U .198, 021C .349, 030T .024}
cgis.pipeline   IMPORTS  triples=0
cgis.pipeline   CALLS    triples=11   {021D .636, 021C .364}
cgis.storage    IMPORTS  triples=0
cgis.storage    CALLS    triples=39   {021D .205, 021U .692, 021C .103}
cgis.query      IMPORTS  triples=4    {021D .25, 021U .25, 021C .50}
cgis.query      CALLS    triples=199  {021D .523, 021U .085, 021C .357, 030T .035}

typescript ui/src (283 nodes, 493 edges)
components      IMPORTS  triples=27   {021D .778, 021C .222}
components      CALLS    triples=0
layout          CALLS    triples=4    {021D .75, 021C .25}
utils           CALLS    triples=4    {021D .5, 021U .5}
(all other domain/layer combinations: 0 connected triples)

quotient k=1 (python): IMPORTS triples=5 {021D .4, 021U .4, 030T .2}; CALLS triples=4 {021D .75, 021U .25}
quotient k=1 (ts):     IMPORTS triples=12 {021D .417, 021U .333, 021C .083, 030T .167}
```

All `unresolved_ratio` and `cycle_ratio` remain 0.00 on every domain (unchanged from Part A) ⇒ the CALLS discount and hygiene gates contribute zero today.

### Decisions (deviations from / refinements of the spec, justified)

1. **`layered_dag` ideal is `{021D: 0.5, 021C: 0.5}`, NOT `e_030T`** (spec §3.3 example). 030T is the transitive triangle A→B, B→C, A→C — i.e. a *layer-skipping shortcut*, which a clean layered DAG should NOT contain. Measurement agrees: `components` (the layered_dag domain) has 021D .778 / 021C .222 and **zero** 030T. Task 9 amends the spec example in the same PR.
2. **Template-declared non-triad constraints survive as gates.** `dag_depth {min: $min_depth}` stays on `layered_dag` (triads are local — they cannot see depth) and `unresolved_ratio {max: 0.1}` stays on `pure_utility`. "Everything topological moves to the distance term" (§3.3) applies to the *counting* components: `hub_count`, `star_count`, `chain_len`, `router_count` constraints are REMOVED from templates. The fields stay on `PatternFingerprint` (CLI/UI display, Part C).
3. **A layer with zero connected triples is EXCLUDED (weight 0 + renormalize), not scored against the ideal.** An empty census is "no data", not "maximally drifted" — same philosophy as the no-floor discount. Without this, `cgis.resolver` (IMPORTS empty) would carry tv=0.5 of pure phantom drift.
4. **Quotient (python) will measure tv=1.0 against `pipeline_stage`.** The measured quotient is a star out of `cgis.pipeline` + hub into `cgis.storage` — an *orchestrator* shape, not the chain the spec §3.4 example claims. This is exactly what `enforce: false` burn-in is for: the report surfaces it, nothing breaks, and rebinding to `orchestrator` is a Part B follow-up discussion, not a blocker.
5. **Quotient `unresolved_ratio` is 0 for the first milestone** (raw_call targets belong to no domain, so they never become quotient edges). The k=1 CALLS layer is therefore undiscounted; acceptable while observe-only, recorded in the test docstring.
6. **New tolerances are re-baselined.** The ratchet "only down" discipline applies *within* a metric; v2 is a new metric, so tolerances are set from the measured v2 drifts below (+small headroom), each with a `# measured` comment. Predicted values (hand-computed from the censuses above; Task 7 verifies live):

```
extraction  pure_utility   tv_cal=.749 → drift ≈ .262   tolerance 0.30
resolution  pipeline_stage tv_cal=.651 → drift ≈ .350   tolerance 0.40
pipeline    orchestrator   tv_cal=.364 → drift ≈ .196   tolerance 0.25
storage     pure_utility   tv_cal=.308 → drift ≈ .166   tolerance 0.20
query       layered_dag    tv_imp=.250, tv_cal=.143 → drift ≈ .138   tolerance 0.18
components  layered_dag    tv_imp=.278 → drift ≈ .167   tolerance 0.20
layout      pure_utility   tv_cal=1.0  → drift ≈ .455   tolerance 0.50  # 4 triples, small-N; revisit with TS resolver
hooks/store/providers/utils  (hygiene-only, v1 path)    drift 0.00     tolerance 0.15 (unchanged)
```

Python layer weights: imports .35 / calls .35 / gates .30. TypeScript: imports .45 / calls .25 / gates .30 (imports more reliable in TS — spec §3.6.2).

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `src/cgis/query/triads.py` | Create | TRIAD_ORDER, tricode classification, `triad_census`, `normalized_census`, `tv_distance` |
| `src/cgis/query/fingerprint.py` | Modify | `t_imports`/`t_calls` fields, census computation, `from_graph()` |
| `src/cgis/query/quotient.py` | Create | `build_quotient(nodes, edges, domains)` → quotient nodes/edges |
| `src/cgis/query/drift.py` | Modify | ideal/layers/triad_weights loading + v2 scoring path; `enforce` on DomainConfig; `load_project_level()` |
| `docs/ontology/patterns.yaml` | Modify | v2.1: ideals on 5 templates, layers+triad_weights per profile, slim templates, new tolerances, `project_level` |
| `src/cgis/cli.py` | Modify | TV columns in drift table; observe-only quotient section |
| `tests/unit/test_triads.py` | Create | 13 class tests + dedup/self-loop/empty + tv tests |
| `tests/unit/test_fingerprint.py` | Modify | census fields populated; from_graph |
| `tests/unit/test_drift.py` | Modify | v2 scoring arithmetic pins; v1 fallback pins |
| `tests/unit/test_quotient.py` | Create | aggregation, node ids, cross-domain only |
| `tests/unit/test_patterns_yaml.py` | Modify | ideal validation, layers sum, no counting constraints in templates |
| `tests/self_parsing/test_drift.py` | Modify | quotient observe-only report test |
| `docs/specs/2026-06-09-...-design.md` | Modify | §3.3 layered_dag example amendment |

---

### Task 1: Triad census module

**Files:**
- Create: `src/cgis/query/triads.py`
- Create: `tests/unit/test_triads.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the 13-class connected triad census (spec §3.1/§3.2)."""

import pytest

from cgis.core.models import Edge, EdgeType
from cgis.query.triads import TRIAD_ORDER, normalized_census, triad_census, tv_distance


def _e(source: str, target: str, etype: EdgeType = EdgeType.CALLS) -> Edge:
    return Edge(
        id=f"{source}:{etype.value}:{target}",
        source=source,
        target=target,
        type=etype,
        weight=1.0,
        confidence=1.0,
    )


def _census(edge_pairs: list[tuple[str, str]]) -> dict[str, int]:
    """Census over nodes {a, b, c} with the given directed CALLS edges."""
    return triad_census({"a", "b", "c"}, [_e(s, t) for s, t in edge_pairs], EdgeType.CALLS)


def _single(edge_pairs: list[tuple[str, str]], expected: str) -> None:
    counts = _census(edge_pairs)
    assert counts[expected] == 1, counts
    assert sum(counts.values()) == 1, counts


# ── one hand-built graph per connected triad class ────────────────────────────


def test_021d_out_fork() -> None:
    """A←B→C: one node points at the two others."""
    _single([("b", "a"), ("b", "c")], "021D")


def test_021u_in_join() -> None:
    """A→B←C: two nodes point at one."""
    _single([("a", "b"), ("c", "b")], "021U")


def test_021c_path() -> None:
    """A→B→C directed path."""
    _single([("a", "b"), ("b", "c")], "021C")


def test_111d() -> None:
    """Mutual dyad plus an incoming edge: A↔B, C→A."""
    _single([("a", "b"), ("b", "a"), ("c", "a")], "111D")


def test_111u() -> None:
    """Mutual dyad plus an outgoing edge: A↔B, A→C."""
    _single([("a", "b"), ("b", "a"), ("a", "c")], "111U")


def test_030t_feed_forward() -> None:
    """Transitive triangle: A→B, B→C, A→C."""
    _single([("a", "b"), ("b", "c"), ("a", "c")], "030T")


def test_030c_cycle() -> None:
    """3-cycle: A→B→C→A."""
    _single([("a", "b"), ("b", "c"), ("c", "a")], "030C")


def test_201() -> None:
    """Two mutual dyads sharing a node: A↔B, B↔C."""
    _single([("a", "b"), ("b", "a"), ("b", "c"), ("c", "b")], "201")


def test_120d() -> None:
    """Mutual dyad, third node points at both: A↔B, C→A, C→B."""
    _single([("a", "b"), ("b", "a"), ("c", "a"), ("c", "b")], "120D")


def test_120u() -> None:
    """Mutual dyad, both members point at the third: A↔B, A→C, B→C."""
    _single([("a", "b"), ("b", "a"), ("a", "c"), ("b", "c")], "120U")


def test_120c() -> None:
    """Mutual dyad on a directed path: A↔B, C→A, B→C."""
    _single([("a", "b"), ("b", "a"), ("c", "a"), ("b", "c")], "120C")


def test_210() -> None:
    """Two mutual dyads plus one single edge: A↔B, B↔C, A→C."""
    _single([("a", "b"), ("b", "a"), ("b", "c"), ("c", "b"), ("a", "c")], "210")


def test_300_complete() -> None:
    """All six directed edges present."""
    _single(
        [("a", "b"), ("b", "a"), ("b", "c"), ("c", "b"), ("a", "c"), ("c", "a")],
        "300",
    )


# ── mechanics ─────────────────────────────────────────────────────────────────


def test_triad_order_has_13_classes() -> None:
    """The canonical order lists exactly the 13 connected triad classes."""
    assert len(TRIAD_ORDER) == 13
    assert len(set(TRIAD_ORDER)) == 13
    assert TRIAD_ORDER[0] == "021D"


def test_each_triple_counted_once() -> None:
    """A 4-node star b→{a,c,d} has exactly C(3,2)=3 connected triples, all 021D."""
    nodes = {"a", "b", "c", "d"}
    edges = [_e("b", "a"), _e("b", "c"), _e("b", "d")]
    counts = triad_census(nodes, edges, EdgeType.CALLS)
    assert counts["021D"] == 3
    assert sum(counts.values()) == 3


def test_self_loops_and_duplicate_edges_ignored() -> None:
    """Self-loops are dropped; a duplicated edge does not change the class."""
    edges = [_e("a", "a"), _e("a", "b"), _e("a", "b"), _e("b", "c")]
    counts = triad_census({"a", "b", "c"}, edges, EdgeType.CALLS)
    assert counts["021C"] == 1
    assert sum(counts.values()) == 1


def test_edges_outside_node_set_and_wrong_type_ignored() -> None:
    """Only intra-set edges of the requested type participate."""
    edges = [
        _e("a", "b"),
        _e("b", "c", EdgeType.IMPORTS),  # wrong type
        _e("b", "x"),  # x outside the set
    ]
    counts = triad_census({"a", "b", "c"}, edges, EdgeType.CALLS)
    assert sum(counts.values()) == 0  # a→b alone is not a connected TRIPLE


def test_empty_graph_zero_census() -> None:
    """No edges → all-zero census; normalization stays all-zero (no NaN)."""
    counts = triad_census({"a", "b", "c"}, [], EdgeType.CALLS)
    assert sum(counts.values()) == 0
    assert normalized_census(counts) == (0.0,) * 13


def test_normalized_census_sums_to_one() -> None:
    """Normalized census of a non-empty graph sums to 1.0, ordered by TRIAD_ORDER."""
    counts = _census([("b", "a"), ("b", "c")])
    norm = normalized_census(counts)
    assert len(norm) == 13
    assert sum(norm) == pytest.approx(1.0)
    assert norm[TRIAD_ORDER.index("021D")] == pytest.approx(1.0)


# ── tv_distance ───────────────────────────────────────────────────────────────


def test_tv_identical_vectors_zero() -> None:
    """TV of a vector with itself is 0."""
    t = (1.0,) + (0.0,) * 12
    tv, contribs = tv_distance(t, t, (1.0,) * 13)
    assert tv == pytest.approx(0.0)
    assert all(c == 0.0 for _, c in contribs)


def test_tv_disjoint_unit_vectors_one() -> None:
    """TV between two disjoint distributions is 1 (the metric's upper bound)."""
    a = (1.0,) + (0.0,) * 12
    b = (0.0, 1.0) + (0.0,) * 11
    tv, _ = tv_distance(a, b, (1.0,) * 13)
    assert tv == pytest.approx(1.0)


def test_tv_decomposes_per_triad() -> None:
    """Each contribution is ½·w_i·|a_i−b_i| and they sum to the total."""
    a = (0.6, 0.4) + (0.0,) * 11
    b = (1.0,) + (0.0,) * 12
    weights = (1.0, 0.5) + (1.0,) * 11
    tv, contribs = tv_distance(a, b, weights)
    assert contribs[0] == (TRIAD_ORDER[0], pytest.approx(0.2))  # ½·1.0·0.4
    assert contribs[1] == (TRIAD_ORDER[1], pytest.approx(0.1))  # ½·0.5·0.4
    assert tv == pytest.approx(sum(c for _, c in contribs))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_triads.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cgis.query.triads'`

- [ ] **Step 3: Implement `src/cgis/query/triads.py`**

```python
"""13-class connected triad census and total-variation distance (spec §3.1–§3.3).

Classification uses the Batagelj–Mrvar tricode: the 6 possible directed edges
among an ordered triple form a 6-bit code; a 64-entry table maps each code to
one of the 16 MAN triad types. We count only the 13 *connected* types — the
unit tests pin one hand-built graph per class, which is the correctness anchor
for the table.
"""

from cgis.core.models import Edge, EdgeType

#: The 13 connected triad classes, canonical vector order for fingerprints.
TRIAD_ORDER: tuple[str, ...] = (
    "021D",
    "021U",
    "021C",
    "111D",
    "111U",
    "030T",
    "030C",
    "201",
    "120D",
    "120U",
    "120C",
    "210",
    "300",
)

#: All 16 MAN types in Batagelj–Mrvar order; the first three are disconnected.
_TRIAD_NAMES: tuple[str, ...] = (
    "003",
    "012",
    "102",
    "021D",
    "021U",
    "021C",
    "111D",
    "111U",
    "030T",
    "030C",
    "201",
    "120D",
    "120U",
    "120C",
    "210",
    "300",
)

#: Batagelj–Mrvar TRICODES: maps each 6-bit edge code to a 1-based MAN index.
_TRICODES: tuple[int, ...] = (
    1, 2, 2, 3, 2, 4, 6, 8, 2, 6, 5, 7, 3, 8, 7, 11,
    2, 6, 4, 8, 5, 9, 9, 13, 6, 10, 9, 14, 7, 14, 12, 15,
    2, 5, 6, 7, 6, 9, 10, 14, 4, 9, 9, 12, 8, 13, 14, 15,
    3, 7, 8, 11, 7, 12, 14, 15, 8, 14, 13, 15, 11, 15, 15, 16,
)

#: Zero vector, the default for fingerprints of empty domains.
ZERO_TRIADS: tuple[float, ...] = (0.0,) * len(TRIAD_ORDER)


def _classify(succ: dict[str, set[str]], v: str, u: str, w: str) -> str:
    """Return the MAN class name for the ordered triple (v, u, w)."""
    code = (
        (1 if u in succ[v] else 0)
        + (2 if v in succ[u] else 0)
        + (4 if w in succ[v] else 0)
        + (8 if v in succ[w] else 0)
        + (16 if w in succ[u] else 0)
        + (32 if u in succ[w] else 0)
    )
    return _TRIAD_NAMES[_TRICODES[code] - 1]


def triad_census(
    node_ids: set[str], edges: list[Edge], edge_type: EdgeType
) -> dict[str, int]:
    """Count connected triads over intra-set edges of edge_type.

    Self-loops and edges touching nodes outside node_ids are ignored.
    Each unordered triple is counted exactly once. O(Σ deg(v)²) — fine at
    our scale (spec §3.6.3).
    """
    succ: dict[str, set[str]] = {n: set() for n in node_ids}
    nbrs: dict[str, set[str]] = {n: set() for n in node_ids}
    for e in edges:
        if e.type != edge_type or e.source == e.target:
            continue
        if e.source in succ and e.target in succ:
            succ[e.source].add(e.target)
            nbrs[e.source].add(e.target)
            nbrs[e.target].add(e.source)

    counts: dict[str, int] = dict.fromkeys(TRIAD_ORDER, 0)
    seen: set[tuple[str, str, str]] = set()
    for v in node_ids:
        for u in nbrs[v]:
            for w in nbrs[v] | nbrs[u]:
                if w == v or w == u:
                    continue
                key_list = sorted((v, u, w))
                key = (key_list[0], key_list[1], key_list[2])
                if key in seen:
                    continue
                seen.add(key)
                # v–u adjacent and w adjacent to one of them ⇒ the triple is
                # connected, so the class is always one of the 13.
                counts[_classify(succ, key[0], key[1], key[2])] += 1
    return counts


def normalized_census(counts: dict[str, int]) -> tuple[float, ...]:
    """Return the census as a 13-tuple in TRIAD_ORDER, normalized to sum 1.

    An empty census normalizes to the all-zero vector ("no data", not NaN).
    """
    total = sum(counts.values())
    if total == 0:
        return ZERO_TRIADS
    return tuple(counts[name] / total for name in TRIAD_ORDER)


def tv_distance(
    t: tuple[float, ...],
    ideal: tuple[float, ...],
    weights: tuple[float, ...],
) -> tuple[float, list[tuple[str, float]]]:
    """Weighted total variation ½·Σ wᵢ·|tᵢ−idealᵢ| with exact per-triad decomposition.

    Returns (tv, contributions) where contributions pairs each TRIAD_ORDER
    name with its ½·wᵢ·|tᵢ−idealᵢ| term (spec §3.3: violations fall out of
    the metric instead of being approximated from it).
    """
    contribs = [
        (name, 0.5 * w * abs(a - b))
        for name, a, b, w in zip(TRIAD_ORDER, t, ideal, weights, strict=True)
    ]
    return sum(c for _, c in contribs), contribs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_triads.py -q`
Expected: 20 passed. If any of the 13 class tests fails, the TRICODES table or bit order is wrong — fix the table, do NOT bend the test (the hand-built graphs are ground truth; cross-check against `nx.triadic_census` with `uv run --with networkx` if in doubt).

- [ ] **Step 5: Commit**

```bash
git add src/cgis/query/triads.py tests/unit/test_triads.py
git commit -m "feat: 13-class connected triad census + weighted TV distance (spec §3.1-3.3)"
```

---

### Task 2: Fingerprint v2 — census vectors on PatternFingerprint

**Files:**
- Modify: `src/cgis/query/fingerprint.py`
- Modify: `tests/unit/test_fingerprint.py` (append tests)

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_fingerprint.py`; reuse the file's existing store-building helpers — read the file first to match its fixture style)

```python
# ── fingerprint v2: triad census vectors (spec §3.2) ─────────────────────────


def test_fingerprint_defaults_zero_census() -> None:
    """t_imports/t_calls default to the 13-dim zero vector (back-compat)."""
    fp = PatternFingerprint(
        domain="d",
        hub_count=0,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )
    assert fp.t_imports == (0.0,) * 13
    assert fp.t_calls == (0.0,) * 13


def test_extract_populates_calls_census(tmp_path: Path) -> None:
    """A domain with an internal A→B→C CALLS path measures t_calls ∝ e_021C."""
    nodes = [_node("m.a"), _node("m.b"), _node("m.c")]
    edges = [_calls_edge("m.a", "m.b"), _calls_edge("m.b", "m.c")]
    store = _store_with(tmp_path, nodes, edges)
    fp = FingerprintExtractor(store).extract("m")
    idx = TRIAD_ORDER.index("021C")
    assert fp.t_calls[idx] == pytest.approx(1.0)
    assert sum(fp.t_calls) == pytest.approx(1.0)
    assert fp.t_imports == (0.0,) * 13  # no IMPORTS edges


def test_from_graph_skips_store() -> None:
    """from_graph() builds an extractor over in-memory nodes/edges (quotient path)."""
    nodes = [_node("q.a"), _node("q.b"), _node("q.c")]
    edges = [_calls_edge("q.a", "q.b"), _calls_edge("q.a", "q.c")]
    fp = FingerprintExtractor.from_graph(nodes, edges).extract("q")
    assert fp.t_calls[TRIAD_ORDER.index("021D")] == pytest.approx(1.0)
```

`_node`, `_calls_edge`, `_store_with` refer to whatever helpers `test_fingerprint.py` already uses to build Nodes/Edges/stores — match the existing names exactly (the file has 16 tests; the helpers exist). Add `from cgis.query.triads import TRIAD_ORDER` to imports.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_fingerprint.py -q`
Expected: new tests FAIL — `PatternFingerprint` has no attribute `t_imports`; `from_graph` missing.

- [ ] **Step 3: Implement in `src/cgis/query/fingerprint.py`**

Add import: `from cgis.query.triads import ZERO_TRIADS, normalized_census, triad_census`.

Add fields to `PatternFingerprint` (after `unresolved_ratio`):

```python
    # Fingerprint v2 (spec §3.2): normalized 13-triad census per layer,
    # ordered by triads.TRIAD_ORDER. Zero vector = empty layer ("no data").
    t_imports: tuple[float, ...] = ZERO_TRIADS
    t_calls: tuple[float, ...] = ZERO_TRIADS
```

Change `__init__` signature to `store: SQLiteStore | None` and guard `_loaded`:

```python
    def __init__(self, store: SQLiteStore | None) -> None:
        """Accept an open SQLiteStore, or None when built via from_graph()."""
        self._store = store
        self._cache: tuple[list[Node], list[Edge]] | None = None

    @classmethod
    def from_graph(cls, nodes: list[Node], edges: list[Edge]) -> "FingerprintExtractor":
        """Build an extractor over an in-memory graph (used for the quotient graph)."""
        inst = cls(None)
        inst._cache = (HealthScorer(nodes, edges).enrich(), edges)
        return inst

    def _loaded(self) -> tuple[list[Node], list[Edge]]:
        """Return (enriched_nodes, all_edges), fetching from the store once and caching."""
        if self._cache is None:
            if self._store is None:
                msg = "FingerprintExtractor needs a store or a from_graph() preload."
                raise RuntimeError(msg)
            all_nodes = self._store.get_all_nodes()
            all_edges = self._store.get_all_edges()
            self._cache = (HealthScorer(all_nodes, all_edges).enrich(), all_edges)
        return self._cache
```

In `extract()`, after `internal_edges` is computed, add:

```python
        t_imports = normalized_census(triad_census(domain_ids, internal_edges, EdgeType.IMPORTS))
        t_calls = normalized_census(triad_census(domain_ids, internal_edges, EdgeType.CALLS))
```

and pass `t_imports=t_imports, t_calls=t_calls` in the final `PatternFingerprint(...)` constructor. (The empty-domain early return keeps the field defaults.)

- [ ] **Step 4: Run the full unit suite** (NOT just fingerprint tests — DriftScorer constructs fingerprints in many fixtures)

Run: `uv run pytest tests/unit -q`
Expected: all pass (defaults keep every existing constructor valid).

- [ ] **Step 5: Commit**

```bash
git add src/cgis/query/fingerprint.py tests/unit/test_fingerprint.py
git commit -m "feat: triad census vectors on PatternFingerprint + from_graph() (spec §3.2)"
```

---

### Task 3: DriftScorer — v2 config loading (ideal, layers, triad_weights, enforce)

**Files:**
- Modify: `src/cgis/query/drift.py`
- Modify: `tests/unit/test_drift.py` (append)

- [ ] **Step 1: Write the failing tests** (append a new section to `tests/unit/test_drift.py`)

```python
# ── §3.3 v2 config: ideal points, layers, triad weights ──────────────────────

_YAML_V2 = """\
version: "2.1.0"
profiles:
  python:
    drift_weights:
      hub_count:        0.15
      star_count:       0.15
      chain_len:        0.10
      dag_depth:        0.10
      router_count:     0.10
      cycle_ratio:      0.25
      unresolved_ratio: 0.15
    layers:
      imports: 0.35
      calls:   0.35
      gates:   0.30
    triad_weights:
      "030C": 0.5
hygiene:
  cycle_ratio:      {max: 0.0}
  unresolved_ratio: {max: 0.2}
patterns:
  pipeline_stage:
    description: "chain"
    ideal:
      imports: {"021C": 1.0}
      calls:   {"021C": 1.0}
project_domains:
  - name: "res"
    fqn_prefix: "res"
    expected_pattern: pipeline_stage
    profile: python
    drift_tolerance: 0.40
  - name: "proj"
    fqn_prefix: "quotient"
    expected_pattern: pipeline_stage
    profile: python
    drift_tolerance: 0.15
"""


@pytest.fixture
def v2_scorer(tmp_path: Path) -> DriftScorer:
    """Return a DriftScorer loaded from the v2 YAML fixture."""
    p = tmp_path / "patterns.yaml"
    p.write_text(_YAML_V2)
    return DriftScorer(str(p))


def test_ideal_point_loaded_as_13_tuple(v2_scorer: DriftScorer) -> None:
    """The template's ideal block parses into TRIAD_ORDER-aligned tuples."""
    imp, cal = v2_scorer.ideal_for("pipeline_stage")
    assert imp[TRIAD_ORDER.index("021C")] == pytest.approx(1.0)
    assert sum(imp) == pytest.approx(1.0)
    assert cal == imp


def test_ideal_unknown_triad_key_fails_loud(tmp_path: Path) -> None:
    """An ideal entry naming a non-existent triad class raises ValueError."""
    bad = _YAML_V2.replace('"021C": 1.0', '"999X": 1.0')
    assert bad != _YAML_V2
    p = tmp_path / "patterns.yaml"
    p.write_text(bad)
    with pytest.raises(ValueError, match="999X"):
        DriftScorer(str(p)).ideal_for("pipeline_stage")


def test_ideal_must_sum_to_one(tmp_path: Path) -> None:
    """An ideal layer whose values do not sum to 1.0 raises ValueError."""
    bad = _YAML_V2.replace('imports: {"021C": 1.0}', 'imports: {"021C": 0.7}')
    assert bad != _YAML_V2
    p = tmp_path / "patterns.yaml"
    p.write_text(bad)
    with pytest.raises(ValueError, match="sum"):
        DriftScorer(str(p)).ideal_for("pipeline_stage")


def test_layers_loaded_and_validated(v2_scorer: DriftScorer) -> None:
    """Profile layers parse; missing keys or bad sums raise."""
    layers = v2_scorer.layers_for("python")
    assert layers == {"imports": 0.35, "calls": 0.35, "gates": 0.30}


def test_layers_must_sum_to_one(tmp_path: Path) -> None:
    """Layer weights that do not sum to 1.0 raise ValueError."""
    bad = _YAML_V2.replace("gates:   0.30", "gates:   0.40")
    assert bad != _YAML_V2
    p = tmp_path / "patterns.yaml"
    p.write_text(bad)
    with pytest.raises(ValueError, match="sum"):
        DriftScorer(str(p)).layers_for("python")


def test_triad_weights_default_one(v2_scorer: DriftScorer) -> None:
    """Unlisted triads weigh 1.0; listed ones take the declared value."""
    w = v2_scorer.triad_weights_for("python")
    assert w[TRIAD_ORDER.index("030C")] == pytest.approx(0.5)
    assert w[TRIAD_ORDER.index("021C")] == pytest.approx(1.0)


def test_domain_config_enforce_defaults_true(v2_scorer: DriftScorer) -> None:
    """enforce defaults to True on every binding; explicit false is read."""
    domains = v2_scorer.load_project_domains()
    assert all(d.enforce for d in domains)
```

Add `from cgis.query.triads import TRIAD_ORDER` to the test file's imports.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_drift.py -q`
Expected: new tests FAIL — `ideal_for`/`layers_for`/`triad_weights_for`/`enforce` missing.

- [ ] **Step 3: Implement in `src/cgis/query/drift.py`**

Add import: `from cgis.query.triads import TRIAD_ORDER, ZERO_TRIADS`.

Add `enforce: bool = True` to `DomainConfig` (after `params`), and in `load_project_domains()` pass `enforce=bool(d.get("enforce", True))`.

Add methods to `DriftScorer`:

```python
    def ideal_for(self, pattern_name: str) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
        """Return (ideal_imports, ideal_calls) 13-tuples for a template, or None.

        None means the template declares no ideal block — the domain scores on
        the v1 path. Raises ValueError on unknown triad keys, layers other
        than imports/calls, or a layer that does not sum to 1.0.
        """
        template = self._patterns.get(pattern_name) or {}
        ideal = template.get("ideal")
        if ideal is None:
            return None
        if not isinstance(ideal, dict) or set(ideal) != {"imports", "calls"}:
            msg = f"Pattern '{pattern_name}' ideal must declare exactly imports and calls."
            raise ValueError(msg)
        return self._ideal_layer(pattern_name, ideal["imports"]), self._ideal_layer(
            pattern_name, ideal["calls"]
        )

    @staticmethod
    def _ideal_layer(pattern_name: str, layer: dict[str, Any]) -> tuple[float, ...]:
        """Convert one {triad: share} mapping into a TRIAD_ORDER-aligned tuple."""
        unknown = set(layer) - set(TRIAD_ORDER)
        if unknown:
            msg = f"Pattern '{pattern_name}' ideal names unknown triad(s) {sorted(unknown)}."
            raise ValueError(msg)
        values = tuple(float(layer.get(name, 0.0)) for name in TRIAD_ORDER)
        if abs(sum(values) - 1.0) > 1e-9:
            msg = f"Pattern '{pattern_name}' ideal layer must sum to 1.0, got {sum(values)}."
            raise ValueError(msg)
        return values

    def layers_for(self, profile_name: str) -> dict[str, float] | None:
        """Return validated layer weights for a profile, or None when undeclared."""
        profile = self._profiles.get(profile_name) or {}
        layers = profile.get("layers")
        if layers is None:
            return None
        if set(layers) != {"imports", "calls", "gates"}:
            msg = f"Profile '{profile_name}' layers must declare imports, calls, gates."
            raise ValueError(msg)
        result = {k: float(v) for k, v in layers.items()}
        if abs(sum(result.values()) - 1.0) > 1e-9:
            msg = f"Profile '{profile_name}' layers must sum to 1.0, got {sum(result.values())}."
            raise ValueError(msg)
        return result

    def triad_weights_for(self, profile_name: str) -> tuple[float, ...]:
        """Per-triad w_i for a profile; unlisted triads default to 1.0 (spec §3.3)."""
        profile = self._profiles.get(profile_name) or {}
        declared: dict[str, Any] = profile.get("triad_weights") or {}
        unknown = set(declared) - set(TRIAD_ORDER)
        if unknown:
            msg = f"Profile '{profile_name}' triad_weights names unknown triad(s) {sorted(unknown)}."
            raise ValueError(msg)
        return tuple(float(declared.get(name, 1.0)) for name in TRIAD_ORDER)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_drift.py tests/unit/test_patterns_yaml.py -q`
Expected: all pass (current `patterns.yaml` has no `ideal`/`layers` blocks yet — loaders return None, nothing else changes).

- [ ] **Step 5: Commit**

```bash
git add src/cgis/query/drift.py tests/unit/test_drift.py
git commit -m "feat: load ideal points, layer weights, triad weights, enforce flag (spec §3.3-3.4)"
```

---

### Task 4: v2 scoring path in `score()`

**Files:**
- Modify: `src/cgis/query/drift.py`
- Modify: `tests/unit/test_drift.py` (append)

The v2 path activates iff `ideal_for(domain.expected_pattern)` is not None AND `layers_for(domain.profile)` is not None. Otherwise `score()` behaves exactly as today (v1) — this keeps every legacy fixture and hygiene-only domain working unchanged.

v2 formula (decisions #2/#3 above):

```
gates       = hygiene constraints ∪ template non-ideal constraints (e.g. dag_depth)
gate_drift  = v1 weighted mechanism over `gates` only (discount per Part A) ∈ [0,1]
tv_imp      = tv(actual.t_imports, ideal_imports, triad_weights)   — skipped if t_imports == ZERO
tv_cal      = tv(actual.t_calls,   ideal_calls,   triad_weights)   — skipped if t_calls == ZERO
disc        = clip(1 − unresolved_ratio)
eff         = {imports: L_imp (0 if layer empty), calls: L_cal·disc (0 if empty), gates: L_gate (0 if no gates)}
drift_v2    = Σ eff_k · term_k / Σ eff_k        (0.0 if Σ eff == 0)
violations  = gate violations (v1 strings) + triad contributions ≥ 0.05:
              "T_calls[021D]=0.37 vs ideal 0.00 (+0.19)"
```

- [ ] **Step 1: Write the failing tests** (append; reuses `_YAML_V2` and `v2_scorer` from Task 3)

```python
# ── §3.3 v2 scoring: TV distance + gates + empty-layer exclusion ─────────────


def _v2_fp(
    t_imports: tuple[float, ...],
    t_calls: tuple[float, ...],
    unresolved: float = 0.0,
    cycle: float = 0.0,
) -> PatternFingerprint:
    """Fingerprint with explicit census vectors; v1 counting fields irrelevant in v2."""
    return PatternFingerprint(
        domain="res",
        hub_count=0,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=cycle,
        unresolved_ratio=unresolved,
        t_imports=t_imports,
        t_calls=t_calls,
    )


def _unit(name: str) -> tuple[float, ...]:
    """13-dim unit vector on the named triad class."""
    return tuple(1.0 if n == name else 0.0 for n in TRIAD_ORDER)


def test_v2_perfect_match_zero_drift(v2_scorer: DriftScorer) -> None:
    """Both layers exactly on the ideal point, clean gates → drift 0."""
    domain = v2_scorer.load_project_domains()[0]
    report = v2_scorer.score(_v2_fp(_unit("021C"), _unit("021C")), domain)
    assert report.drift_score == pytest.approx(0.0)
    assert report.tv_imports == pytest.approx(0.0)
    assert report.tv_calls == pytest.approx(0.0)


def test_v2_arithmetic_pinned(v2_scorer: DriftScorer) -> None:
    """drift = (L_imp·tv_imp + L_cal·tv_cal + L_gate·0) with all layers present.

    imports: measured 021D vs ideal 021C → tv 1.0
    calls:   60/40 split 021C/021D vs ideal 021C → tv 0.4
    gates clean → 0. drift = .35·1.0 + .35·0.4 + .30·0 = 0.49
    """
    domain = v2_scorer.load_project_domains()[0]
    t_calls = tuple(
        0.6 if n == "021C" else (0.4 if n == "021D" else 0.0) for n in TRIAD_ORDER
    )
    report = v2_scorer.score(_v2_fp(_unit("021D"), t_calls), domain)
    assert report.tv_imports == pytest.approx(1.0)
    assert report.tv_calls == pytest.approx(0.4)
    assert report.drift_score == pytest.approx(0.49)


def test_v2_empty_layer_excluded_not_drifted(v2_scorer: DriftScorer) -> None:
    """Zero-census imports layer drops out with renormalization (decision #3).

    Only calls carries signal: drift = (.35·0.4)/(.35+.30) ≈ 0.2154 — NOT
    .35·0.5 + ... which would treat 'no data' as half-drifted.
    """
    domain = v2_scorer.load_project_domains()[0]
    t_calls = tuple(
        0.6 if n == "021C" else (0.4 if n == "021D" else 0.0) for n in TRIAD_ORDER
    )
    report = v2_scorer.score(_v2_fp((0.0,) * 13, t_calls), domain)
    assert report.tv_imports is None  # excluded, not 0.5
    assert report.drift_score == pytest.approx(0.35 * 0.4 / 0.65)


def test_v2_discount_scales_calls_layer(v2_scorer: DriftScorer) -> None:
    """unresolved=0.5 halves the calls layer weight before renormalization.

    eff = (imports .35, calls .175, gates .30), total .825.
    Gate arithmetic (v1 mechanism): unresolved max 0.2, actual 0.5 →
    raw=.3, norm=max(.2, 1.0)=1.0 → component drift .3; gate weights
    cycle .25 / unresolved .15 (unresolved is NOT in _CALLS_LAYER, so no
    discount on its weight) → gate_drift = (.15/.40)·.3 = .1125.
    drift = (.35·1.0 + .175·0.4 + .30·.1125) / .825
    """
    domain = v2_scorer.load_project_domains()[0]
    t_calls = tuple(
        0.6 if n == "021C" else (0.4 if n == "021D" else 0.0) for n in TRIAD_ORDER
    )
    report = v2_scorer.score(_v2_fp(_unit("021D"), t_calls, unresolved=0.5), domain)
    expected = (0.35 * 1.0 + 0.175 * 0.4 + 0.30 * 0.1125) / (0.35 + 0.175 + 0.30)
    assert report.drift_score == pytest.approx(expected)
    assert any("unresolved_ratio" in v for v in report.violations)


def test_v2_triad_weight_damps_contribution(v2_scorer: DriftScorer) -> None:
    """030C carries w=0.5 in the fixture profile → its TV term is halved."""
    domain = v2_scorer.load_project_domains()[0]
    report = v2_scorer.score(_v2_fp(_unit("030C"), _unit("021C")), domain)
    # imports: |1−0|·0.5(w)·½ on 030C + |0−1|·1.0·½ on 021C = 0.25 + 0.5 = 0.75
    assert report.tv_imports == pytest.approx(0.75)


def test_v2_violations_name_top_triads(v2_scorer: DriftScorer) -> None:
    """Triad terms contributing ≥0.05 appear as human-readable violations."""
    domain = v2_scorer.load_project_domains()[0]
    report = v2_scorer.score(_v2_fp(_unit("021D"), _unit("021C")), domain)
    assert any("T_imports[021D]" in v for v in report.violations)
    assert any("T_imports[021C]" in v for v in report.violations)
    assert not any("T_calls" in v for v in report.violations)  # calls is on-ideal


def test_v1_path_untouched_without_ideal(scorer: DriftScorer, pure_util_domain: DomainConfig) -> None:
    """Legacy YAML (no ideal/layers) scores exactly as before; tv fields are None."""
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=1,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )
    report = scorer.score(fp, pure_util_domain)
    assert report.drift_score == pytest.approx(0.0)
    assert report.tv_imports is None
    assert report.tv_calls is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_drift.py -q`
Expected: new tests FAIL — `DriftReport` has no `tv_imports`; v2 arithmetic absent.

- [ ] **Step 3: Implement in `src/cgis/query/drift.py`**

Add to `DriftReport` (after `tolerance`):

```python
    tv_imports: float | None = None
    tv_calls: float | None = None
```

Add import: `from cgis.query.triads import TRIAD_ORDER, ZERO_TRIADS, tv_distance`.

Add module constant: `_TRIAD_VIOLATION_THRESHOLD = 0.05`.

Restructure `score()` — the existing body becomes `_score_v1(actual, domain, constraints, weights)` (the constraint loop, discount, ideal_fp construction stay byte-identical; it returns the DriftReport with tv fields defaulted to None). New `score()`:

```python
    def score(self, actual: PatternFingerprint, domain: DomainConfig) -> DriftReport:
        """Compute the drift score and return a DriftReport (v2 when configured)."""
        template, params = self._resolve_template(domain)
        hygiene = self._parse_constraints(self._hygiene, {})
        constraints = {**hygiene, **self._parse_constraints(template, params)}

        ideal = None if domain.expected_pattern is None else self.ideal_for(domain.expected_pattern)
        layers = None if domain.profile is None else self.layers_for(domain.profile)
        if ideal is None or layers is None:
            return self._score_v1(actual, domain, constraints, self._weights_for(domain))
        return self._score_v2(actual, domain, constraints, ideal, layers)
```

`_score_v2`:

```python
    def _score_v2(
        self,
        actual: PatternFingerprint,
        domain: DomainConfig,
        gates: dict[str, tuple[str, float]],
        ideal: tuple[tuple[float, ...], tuple[float, ...]],
        layers: dict[str, float],
    ) -> DriftReport:
        """Fingerprint v2 drift: layered TV distance + hard gates (spec §3.3)."""
        assert domain.profile is not None  # layers presence implies a profile
        triad_w = self.triad_weights_for(domain.profile)
        discount = max(0.0, min(1.0 - actual.unresolved_ratio, 1.0))
        violations: list[str] = []

        tv_imp: float | None = None
        if actual.t_imports != ZERO_TRIADS:
            tv_imp, contribs = tv_distance(actual.t_imports, ideal[0], triad_w)
            violations.extend(self._triad_violations("T_imports", actual.t_imports, ideal[0], contribs))
        tv_cal: float | None = None
        if actual.t_calls != ZERO_TRIADS:
            tv_cal, contribs = tv_distance(actual.t_calls, ideal[1], triad_w)
            violations.extend(self._triad_violations("T_calls", actual.t_calls, ideal[1], contribs))

        gate_drift, gate_violations = self._gate_drift(actual, domain, gates, discount)
        violations.extend(gate_violations)

        eff = {
            "imports": layers["imports"] if tv_imp is not None else 0.0,
            "calls": layers["calls"] * discount if tv_cal is not None else 0.0,
            "gates": layers["gates"] if gates else 0.0,
        }
        terms = {"imports": tv_imp or 0.0, "calls": tv_cal or 0.0, "gates": gate_drift}
        total = sum(eff.values())
        drift = sum(eff[k] * terms[k] for k in eff) / total if total > 0.0 else 0.0

        return DriftReport(
            domain=domain.name,
            fqn_prefix=domain.fqn_prefix,
            expected_pattern=domain.expected_pattern,
            actual=actual,
            ideal=self._ideal_fingerprint_v2(domain, ideal),
            drift_score=round(drift, 6),
            violations=violations,
            status=_classify(drift),
            tolerance=domain.drift_tolerance,
            tv_imports=tv_imp,
            tv_calls=tv_cal,
        )

    @staticmethod
    def _triad_violations(
        layer: str,
        actual: tuple[float, ...],
        ideal: tuple[float, ...],
        contribs: list[tuple[str, float]],
    ) -> list[str]:
        """Render triad terms contributing ≥ threshold as violation strings."""
        return [
            f"{layer}[{name}]={actual[i]:.2f} vs ideal {ideal[i]:.2f} (+{c:.2f})"
            for i, (name, c) in enumerate(contribs)
            if c >= _TRIAD_VIOLATION_THRESHOLD
        ]

    def _ideal_fingerprint_v2(
        self,
        domain: DomainConfig,
        ideal: tuple[tuple[float, ...], tuple[float, ...]],
    ) -> PatternFingerprint:
        """Ideal fingerprint carrying the template's triad points (v1 fields zero)."""
        return PatternFingerprint(
            domain=domain.fqn_prefix,
            hub_count=0,
            star_count=0,
            chain_len=0.0,
            dag_depth=0,
            router_count=0,
            cycle_ratio=0.0,
            unresolved_ratio=0.0,
            t_imports=ideal[0],
            t_calls=ideal[1],
        )
```

`_gate_drift` reuses the v1 per-constraint mechanism over the gate subset (extract the shared per-constraint loop from `_score_v1` so the arithmetic exists ONCE — `_score_constraint`, the weight renormalization with `_CALLS_LAYER` discount, and the violation strings are common; only the constraint set differs). Returns `(drift ∈ [0,1], violations)`; `(0.0, [])` when `gates` is empty.

- [ ] **Step 4: Run the full unit suite**

Run: `uv run pytest tests/unit -q`
Expected: all pass — v1 fixtures hit the v1 path (no `ideal`/`layers` in their YAML).

- [ ] **Step 5: Run lint + type-check** (the refactor splits score(); mypy strict must stay clean)

Run: `make lint && make type-check`
Expected: clean. If ruff flags the `assert domain.profile is not None`, replace with an explicit `if domain.profile is None: raise RuntimeError(...)` guard.

- [ ] **Step 6: Commit**

```bash
git add src/cgis/query/drift.py tests/unit/test_drift.py
git commit -m "feat: v2 scoring — layered TV distance, empty-layer exclusion, gate term (spec §3.3)"
```

---

### Task 5: Quotient graph builder

**Files:**
- Create: `src/cgis/query/quotient.py`
- Create: `tests/unit/test_quotient.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the domain-quotient graph builder (spec §3.4)."""

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.drift import DomainConfig
from cgis.query.quotient import QUOTIENT_PREFIX, build_quotient


def _node(fqn: str) -> Node:
    return Node(
        id=fqn,
        type=NodeType.FUNCTION,
        name=fqn.rsplit(".", 1)[-1],
        file_path="x.py",
        start_line=1,
        end_line=2,
    )


def _edge(source: str, target: str, etype: EdgeType) -> Edge:
    return Edge(
        id=f"{source}:{etype.value}:{target}",
        source=source,
        target=target,
        type=etype,
        weight=1.0,
        confidence=1.0,
    )


def _domain(name: str, prefix: str) -> DomainConfig:
    return DomainConfig(
        name=name, fqn_prefix=prefix, expected_pattern=None, drift_tolerance=0.5
    )


_DOMAINS = [_domain("ext", "p.ext"), _domain("res", "p.res"), _domain("sto", "p.sto")]

_NODES = [_node("p.ext.a"), _node("p.res.b"), _node("p.sto.c"), _node("other.z")]

_EDGES = [
    _edge("p.ext.a", "p.res.b", EdgeType.CALLS),     # cross-domain → kept
    _edge("p.ext.a", "p.res.b", EdgeType.IMPORTS),   # same pair, other layer → kept
    _edge("p.res.b", "p.sto.c", EdgeType.CALLS),     # cross-domain → kept
    _edge("p.ext.a", "p.ext.a", EdgeType.CALLS),     # intra-domain → dropped
    _edge("p.ext.a", "other.z", EdgeType.CALLS),     # target in no domain → dropped
    _edge("p.ext.a", "p.res.b", EdgeType.CALLS),     # duplicate pair → aggregated
]


def test_quotient_nodes_one_per_domain() -> None:
    """One MODULE node per domain, id = quotient.<name>."""
    qnodes, _ = build_quotient(_NODES, _EDGES, _DOMAINS)
    assert sorted(n.id for n in qnodes) == [
        f"{QUOTIENT_PREFIX}.ext",
        f"{QUOTIENT_PREFIX}.res",
        f"{QUOTIENT_PREFIX}.sto",
    ]
    assert all(n.type == NodeType.MODULE for n in qnodes)


def test_quotient_edges_aggregated_cross_domain_only() -> None:
    """Cross-domain edges aggregate per (src, dst, type) with weight = count."""
    _, qedges = build_quotient(_NODES, _EDGES, _DOMAINS)
    by_key = {(e.source, e.target, e.type): e for e in qedges}
    calls = by_key[(f"{QUOTIENT_PREFIX}.ext", f"{QUOTIENT_PREFIX}.res", EdgeType.CALLS)]
    assert calls.weight == 2.0  # the duplicate aggregated
    assert (f"{QUOTIENT_PREFIX}.ext", f"{QUOTIENT_PREFIX}.res", EdgeType.IMPORTS) in by_key
    assert len(qedges) == 3  # intra-domain and out-of-domain edges never appear


def test_quotient_chain_measures_021c() -> None:
    """ext→res→sto CALLS chain scores t_calls ∝ e_021C through the SAME extractor."""
    from cgis.query.fingerprint import FingerprintExtractor
    from cgis.query.triads import TRIAD_ORDER

    qnodes, qedges = build_quotient(_NODES, _EDGES, _DOMAINS)
    fp = FingerprintExtractor.from_graph(qnodes, qedges).extract(QUOTIENT_PREFIX)
    assert fp.t_calls[TRIAD_ORDER.index("021C")] == pytest.approx(1.0)
```

Add `import pytest` to imports.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_quotient.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `src/cgis/query/quotient.py`**

```python
"""Domain-quotient graph: collapse each domain to one node (spec §3.4).

The quotient is scored by the SAME FingerprintExtractor + DriftScorer as
module-level domains — that closure under coarsening is the point, not an
implementation convenience.
"""

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.drift import DomainConfig

#: FQN prefix of quotient nodes; the project_level binding matches it.
QUOTIENT_PREFIX = "quotient"

_QUOTIENT_EDGE_TYPES = frozenset({EdgeType.IMPORTS, EdgeType.CALLS})


def build_quotient(
    nodes: list[Node], edges: list[Edge], domains: list[DomainConfig]
) -> tuple[list[Node], list[Edge]]:
    """Return (quotient_nodes, quotient_edges) for the given domain bindings.

    One MODULE node per domain (id = quotient.<name>); cross-domain IMPORTS
    and CALLS edges aggregate per (source domain, target domain, type) with
    weight = aggregated edge count. Intra-domain edges and edges touching
    nodes outside every domain are dropped (raw_call targets land here, so
    the quotient's unresolved_ratio is 0 for the observe-only milestone —
    recorded in tests/self_parsing/test_drift.py).
    """
    domain_of: dict[str, str] = {}
    for n in nodes:
        for d in domains:
            if n.id == d.fqn_prefix or n.id.startswith(d.fqn_prefix + "."):
                domain_of[n.id] = d.name
                break

    qnodes = [
        Node(
            id=f"{QUOTIENT_PREFIX}.{d.name}",
            type=NodeType.MODULE,
            name=d.name,
            file_path=d.fqn_prefix,
            start_line=0,
            end_line=0,
        )
        for d in domains
    ]

    counts: dict[tuple[str, str, EdgeType], int] = {}
    for e in edges:
        if e.type not in _QUOTIENT_EDGE_TYPES:
            continue
        src = domain_of.get(e.source)
        dst = domain_of.get(e.target)
        if src is None or dst is None or src == dst:
            continue
        counts[(src, dst, e.type)] = counts.get((src, dst, e.type), 0) + 1

    qedges = [
        Edge(
            id=f"{QUOTIENT_PREFIX}.{src}:{etype.value}:{QUOTIENT_PREFIX}.{dst}",
            source=f"{QUOTIENT_PREFIX}.{src}",
            target=f"{QUOTIENT_PREFIX}.{dst}",
            type=etype,
            weight=float(count),
            confidence=1.0,
        )
        for (src, dst, etype), count in sorted(counts.items(), key=lambda kv: kv[0][:2])
    ]
    return qnodes, qedges
```

If `Node`/`Edge` require fields not shown here (check `core/models.py` field list before writing), supply neutral defaults the same way `gen_ideal_graph.py::_node` does.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_quotient.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/query/quotient.py tests/unit/test_quotient.py
git commit -m "feat: domain-quotient graph builder (spec §3.4)"
```

---

### Task 6: patterns.yaml v2.1 + structural tests

**Files:**
- Modify: `docs/ontology/patterns.yaml` (full rewrite below)
- Modify: `tests/unit/test_patterns_yaml.py`

- [ ] **Step 1: Update structural tests first** (they pin the new shape)

In `tests/unit/test_patterns_yaml.py`:

Add at module level:

```python
_TRIAD_ORDER = frozenset(
    {
        "021D", "021U", "021C", "111D", "111U", "030T", "030C",
        "201", "120D", "120U", "120C", "210", "300",
    }
)

_COUNTING_COMPONENTS = frozenset({"hub_count", "star_count", "chain_len", "router_count"})
```

Append tests:

```python
def test_every_template_has_ideal_with_both_layers() -> None:
    """Each of the 5 templates declares ideal.imports and ideal.calls summing to 1."""
    data = _load()
    for name, template in data["patterns"].items():
        ideal = template.get("ideal")
        assert isinstance(ideal, dict) and set(ideal) == {"imports", "calls"}, name
        for layer, point in ideal.items():
            assert set(point) <= _TRIAD_ORDER, f"{name}.{layer}"
            assert abs(sum(point.values()) - 1.0) < 1e-9, f"{name}.{layer}"


def test_templates_carry_no_counting_constraints() -> None:
    """Counting components moved to the distance term — gates only (spec §3.3)."""
    data = _load()
    for name, template in data["patterns"].items():
        for key, _ in _constraint_items(template):
            assert key not in _COUNTING_COMPONENTS, f"'{key}' in '{name}' must be an ideal, not a gate"


def test_profiles_declare_layers_summing_to_one() -> None:
    """Both profiles declare imports/calls/gates layer weights summing to 1."""
    data = _load()
    for name, profile in data["profiles"].items():
        layers = profile["layers"]
        assert set(layers) == {"imports", "calls", "gates"}, name
        assert abs(sum(layers.values()) - 1.0) < 1e-9, name


def test_project_level_binding_observe_only() -> None:
    """project_level exists, matches the quotient prefix, and is enforce: false."""
    data = _load()
    bindings = data["project_level"]
    assert len(bindings) == 1
    b = bindings[0]
    assert b["fqn_prefix"] == "quotient"
    assert b["enforce"] is False
    assert b["expected_pattern"] in data["patterns"]
```

Also update `_constraint_items` usage sites: the helper must now ALSO skip the `ideal` key — change its filter to `if key not in ("description", "params", "ideal") and isinstance(value, dict)`. (`test_all_pattern_constraints_use_known_components` gets the same `"ideal"` exclusion in its skip tuple.)

- [ ] **Step 2: Run to verify the new tests fail**

Run: `uv run pytest tests/unit/test_patterns_yaml.py -q`
Expected: 4 new tests FAIL against the current YAML.

- [ ] **Step 3: Rewrite `docs/ontology/patterns.yaml`**

```yaml
version: "2.1.0"

# ── Measurement profiles (spec §2.3, §3.3) ───────────────────────────────────
# drift_weights: v1 per-component weights — now used ONLY for the gate term
#   (hygiene + template-declared non-triad constraints).
# layers: fingerprint v2 layer weights (imports TV / calls TV / gates),
#   must sum to 1.0. The calls layer is discounted by (1 - unresolved_ratio);
#   a layer with zero connected triples is excluded and the rest renormalize.
# triad_weights: per-triad w_i for the TV metric; unlisted triads weigh 1.0.
profiles:
  python:
    drift_weights:
      hub_count:        0.15
      star_count:       0.15
      chain_len:        0.10
      dag_depth:        0.10
      router_count:     0.10
      cycle_ratio:      0.25
      unresolved_ratio: 0.15
    layers:
      imports: 0.35
      calls:   0.35
      gates:   0.30
    triad_weights: {}
  typescript:
    drift_weights:
      hub_count:        0.10
      star_count:       0.10
      chain_len:        0.10
      dag_depth:        0.15
      router_count:     0.10
      cycle_ratio:      0.30
      unresolved_ratio: 0.15
    layers:
      imports: 0.45   # IMPORTS is the reliable layer in TS (spec §3.6.2)
      calls:   0.25
      gates:   0.30
    triad_weights: {}

# ── Global hygiene invariants (spec §2.1) — hard gates, never distances ──────
hygiene:
  cycle_ratio:      {max: 0.0}
  unresolved_ratio: {max: 0.2}

# ── The closed alphabet: five templates (spec §2.1, §3.3) ────────────────────
# ideal: hand-authored points in 13-triad space, one per layer, each summing
# to 1.0. Counting constraints (hub/star/chain/router) are GONE — topology is
# measured as TV distance to the ideal. Non-triad structural gates (dag_depth)
# and template-specific hygiene remain as constraints.
patterns:
  pure_utility:
    description: "Shared library called by many; depends on nothing"
    ideal:
      imports: {"021U": 1.0}   # many modules import the one utility
      calls:   {"021U": 1.0}   # in-join: many callers, no fan-out
    unresolved_ratio: {max: 0.1}

  pipeline_stage:
    description: "Sequential transformer — one input domain, one output domain"
    ideal:
      imports: {"021C": 1.0}   # A→B→C chain
      calls:   {"021C": 1.0}

  orchestrator:
    description: "Coordinates N independent services; no leaf-to-leaf edges"
    ideal:
      imports: {"021D": 1.0}   # out-fork from the coordinator
      calls:   {"021D": 1.0}

  layered_dag:
    # Ideal deliberately EXCLUDES 030T: the transitive triangle is a
    # layer-skipping shortcut, not clean layering (spec §3.3 example amended
    # in this PR; measured: components IMPORTS = {021D .778, 021C .222}, 030T 0).
    description: "Clean layered architecture; no upward dependencies"
    params:
      min_depth: 3
    ideal:
      imports: {"021D": 0.5, "021C": 0.5}
      calls:   {"021D": 0.5, "021C": 0.5}
    dag_depth: {min: $min_depth}   # triads are local — depth needs its own gate

  dispatcher:
    # Planned — requires ResolverEngine split (#115).
    description: "Routes to the first matching strategy; mutually exclusive paths"
    ideal:
      imports: {"021D": 1.0}
      calls:   {"021D": 1.0}

# ── Project domain bindings ──────────────────────────────────────────────────
# drift_tolerance is a ratchet and may only go DOWN — but fingerprint v2 is a
# NEW metric, so every tolerance below is re-baselined from the 2026-06-10
# measurement (see plan: docs/specs/plans/2026-06-10-motif-basis-part-b.md).
project_domains:
  # Python graph (ingest root: src/ → FQNs carry the cgis. prefix)
  - name: "extraction"
    fqn_prefix: "cgis.extractors"
    expected_pattern: pure_utility
    profile: python
    drift_tolerance: 0.30 # v2 measured ≈0.26 (tv_calls .75: extractors call each other in forks/chains)

  - name: "resolution"
    fqn_prefix: "cgis.resolver"
    expected_pattern: pipeline_stage
    profile: python
    drift_tolerance: 0.40 # v2 measured ≈0.35 (tv_calls .65; IMPORTS layer empty → excluded)

  - name: "pipeline"
    fqn_prefix: "cgis.pipeline"
    expected_pattern: orchestrator
    profile: python
    drift_tolerance: 0.25 # v2 measured ≈0.20 (tv_calls .36)

  - name: "storage"
    fqn_prefix: "cgis.storage"
    expected_pattern: pure_utility
    profile: python
    drift_tolerance: 0.20 # v2 measured ≈0.17 (tv_calls .31 — closest fit in the codebase)

  - name: "query"
    fqn_prefix: "cgis.query"
    expected_pattern: layered_dag
    profile: python
    drift_tolerance: 0.18 # v2 measured ≈0.14 (tv_imp .25, tv_calls .14)

  # TypeScript graph (ingest root: ui/src → components/Foo.tsx = components.Foo)
  - name: "components"
    fqn_prefix: "components"
    expected_pattern: layered_dag
    profile: typescript
    params: {min_depth: 2} # JSX component trees are legitimately shallower
    drift_tolerance: 0.20 # v2 measured ≈0.17 (tv_imp .28; CALLS layer empty → excluded)

  - name: "layout"
    fqn_prefix: "layout"
    expected_pattern: pure_utility
    profile: typescript
    drift_tolerance: 0.50 # v2 measured ≈0.46 — 4 CALLS triples (small-N, spec §3.6.1); revisit with TS resolver

  # Hygiene-only domains (no expected_pattern → v1 gate path): acyclic + resolved.
  - name: "hooks"
    fqn_prefix: "hooks"
    profile: typescript
    drift_tolerance: 0.15 # measured 0.00

  - name: "store"
    fqn_prefix: "store"
    profile: typescript
    drift_tolerance: 0.15 # measured 0.00

  - name: "providers"
    fqn_prefix: "providers"
    profile: typescript
    drift_tolerance: 0.15 # measured 0.00

  - name: "utils"
    fqn_prefix: "utils"
    profile: typescript
    drift_tolerance: 0.15 # measured 0.00

# ── Project-level quotient binding (spec §3.4) — OBSERVE-ONLY burn-in ────────
# Measured quotient IMPORTS census {021D .4, 021U .4, 030T .2}: the domain
# graph is an orchestrator star out of `pipeline`, NOT the chain the spec
# example claimed. enforce stays false until the burn-in settles whether to
# rebind to orchestrator or restructure; flipping to true makes it a ratchet.
project_level:
  - name: "cgis-project"
    fqn_prefix: "quotient"
    expected_pattern: pipeline_stage
    profile: python
    drift_tolerance: 0.15
    enforce: false
```

- [ ] **Step 4: Add `load_project_level()` to DriftScorer** (`src/cgis/query/drift.py`)

In `__init__`, after `_project_domains`: `self._project_level: list[dict[str, Any]] = raw.get("project_level") or []`.

```python
    def load_project_level(self) -> list[DomainConfig]:
        """Return project-level quotient bindings (spec §3.4); enforce defaults False here."""
        return [
            DomainConfig(
                name=d["name"],
                fqn_prefix=d["fqn_prefix"],
                expected_pattern=d.get("expected_pattern"),
                drift_tolerance=float(d["drift_tolerance"]),
                profile=d.get("profile"),
                params=self._load_params(d),
                enforce=bool(d.get("enforce", False)),
            )
            for d in self._project_level
        ]
```

Append a loading test to `tests/unit/test_drift.py` (extend `_YAML_V2` with a `project_level:` block mirroring the real file):

```python
def test_load_project_level_observe_only(tmp_path: Path) -> None:
    """project_level bindings load with enforce defaulting to False."""
    yaml_pl = _YAML_V2 + (
        "project_level:\n"
        "  - name: \"proj\"\n"
        "    fqn_prefix: \"quotient\"\n"
        "    expected_pattern: pipeline_stage\n"
        "    profile: python\n"
        "    drift_tolerance: 0.15\n"
    )
    p = tmp_path / "patterns.yaml"
    p.write_text(yaml_pl)
    levels = DriftScorer(str(p)).load_project_level()
    assert len(levels) == 1
    assert levels[0].enforce is False
```

(Remove the duplicate `proj` entry from `_YAML_V2`'s `project_domains` if it shadows this — keep fixtures minimal.)

- [ ] **Step 5: Run unit suites**

Run: `uv run pytest tests/unit/test_patterns_yaml.py tests/unit/test_drift.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add docs/ontology/patterns.yaml tests/unit/test_patterns_yaml.py src/cgis/query/drift.py tests/unit/test_drift.py
git commit -m "feat: patterns.yaml v2.1 — ideal points, layers, re-baselined tolerances, project_level (spec §3.3-3.4)"
```

---

### Task 7: Self-parsing — live v2 ratchets + quotient observe-only

**Files:**
- Modify: `tests/self_parsing/test_drift.py`

- [ ] **Step 1: Add the quotient observe-only test** (append; existing tolerance tests stay untouched — they read the new YAML automatically)

```python
# ---------------------------------------------------------------------------
# Quotient (k=1) drift — OBSERVE-ONLY burn-in (spec §3.4)
# ---------------------------------------------------------------------------


def test_py_quotient_drift_observed_not_enforced(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Score the domain-quotient graph against project_level; report, never fail.

    enforce is false for the burn-in milestone: the assertion here is only
    that the machinery runs end-to-end and the binding stays observe-only.
    Known gap, recorded: quotient unresolved_ratio is 0 by construction
    (raw_call targets belong to no domain), so the k=1 CALLS layer is
    undiscounted until enforcement flips.
    """
    store, _, _ = root_graph_data
    scorer = DriftScorer(_PATTERNS)
    bindings = scorer.load_project_level()
    assert bindings, "project_level binding missing from patterns.yaml"

    domains = _selected_domains(scorer, graph="python")
    qnodes, qedges = build_quotient(store.get_all_nodes(), store.get_all_edges(), domains)
    extractor = FingerprintExtractor.from_graph(qnodes, qedges)

    for binding in bindings:
        assert binding.enforce is False, (
            "project_level flipped to enforce: true — this test must become a "
            "tolerance assertion like _assert_within_tolerance (see spec §3.4)"
        )
        report = scorer.score(extractor.extract(binding.fqn_prefix), binding)
        print(
            f"\n[quotient observe-only] {binding.name}: drift={report.drift_score:.3f} "
            f"(tolerance {binding.drift_tolerance:.2f}, tv_imports={report.tv_imports}, "
            f"tv_calls={report.tv_calls})\n  violations: {report.violations}"
        )
```

Add imports: `from cgis.query.quotient import build_quotient`.

- [ ] **Step 2: Run self-parsing for BOTH graphs**

Run: `uv run pytest tests/self_parsing -q -s`
Expected: ALL tests pass, including the existing per-domain tolerance tests against the NEW v2 tolerances. The quotient test prints drift ≈ 0.6–1.0 (the orchestrator-vs-pipeline_stage mismatch — expected, observe-only).

**STOP CONDITION:** if any per-domain drift exceeds its Task 6 tolerance by more than ~0.05 versus the predicted values in the header table, the census or scoring implementation diverges from the measured ground truth — debug the implementation (cross-check one domain against `nx.triadic_census`), do NOT raise the tolerance.

- [ ] **Step 3: Commit**

```bash
git add tests/self_parsing/test_drift.py
git commit -m "test: quotient drift observe-only burn-in on self-graph (spec §3.4)"
```

---

### Task 8: CLI — TV columns + quotient section

**Files:**
- Modify: `src/cgis/cli.py` (`_render_drift_table` ~L778, `drift` command ~L796)

- [ ] **Step 1: Extend the drift table and command**

In `_render_drift_table`, add two columns after "Drift":

```python
    table.add_column("TV imp", justify="right", style="dim")
    table.add_column("TV calls", justify="right", style="dim")
```

and in the row loop (after the drift cell):

```python
            f"{r.tv_imports:.2f}" if r.tv_imports is not None else "—",
            f"{r.tv_calls:.2f}" if r.tv_calls is not None else "—",
```

In the `drift` command, after the per-domain loop inside the `with SQLiteStore(db) as store:` block, add the observe-only quotient pass:

```python
            level_bindings = scorer.load_project_level()
            quotient_reports: list[DriftReport] = []
            if level_bindings:
                qnodes, qedges = build_quotient(store.get_all_nodes(), store.get_all_edges(), domains)
                q_extractor = FingerprintExtractor.from_graph(qnodes, qedges)
                quotient_reports = [
                    scorer.score(q_extractor.extract(b.fqn_prefix), b) for b in level_bindings
                ]
```

After `_render_drift_table(reports, max_drift)`:

```python
    for b, qr in zip(level_bindings, quotient_reports, strict=True):
        marker = "" if b.enforce else " [dim](observe-only)[/dim]"
        console.print(
            f"Quotient k=1 [{b.name}] vs {qr.expected_pattern}: "
            f"drift={qr.drift_score:.2f}{marker}"
        )
```

Observe-only quotient reports do NOT participate in `any_critical` (only enforced ones do — extend the `any_critical` expression with `or any(r.drift_score >= max_drift for b, r in zip(level_bindings, quotient_reports, strict=True) if b.enforce)`). In the JSON branch, append the quotient reports with an `"enforce"` key:

```python
        payload = [dataclasses.asdict(r) for r in reports]
        payload += [
            {**dataclasses.asdict(r), "enforce": b.enforce}
            for b, r in zip(level_bindings, quotient_reports, strict=True)
        ]
        typer.echo(_json.dumps(payload, indent=2))
```

Add import: `from cgis.query.quotient import build_quotient`. Move `level_bindings`/`quotient_reports` initialization above the `try` so the post-table loop sees them (`level_bindings: list[DomainConfig] = []` etc.).

- [ ] **Step 2: Smoke test**

```bash
uv run cgis ingest src --output /tmp/partb_smoke.db
uv run cgis drift --db /tmp/partb_smoke.db
```
Expected: table renders with TV columns (Python domains show numbers; hygiene-only "—"), then a `Quotient k=1 [cgis-project] vs pipeline_stage: drift=… (observe-only)` line. Exit code 0 — UI domains will show as warnings/zero on the python DB; that is the known smoke-test artifact (real gate = self-parsing), only verify rendering and exit code here.

- [ ] **Step 3: Run the full unit suite + lint + types**

Run: `uv run pytest -q && make lint && make type-check`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add src/cgis/cli.py
git commit -m "feat(cli): TV columns and observe-only quotient line in drift report"
```

---

### Task 9: Spec amendment + full gate

**Files:**
- Modify: `docs/specs/2026-06-09-pattern-alphabet-motif-basis-design.md` (§3.3 ideal-point example)

- [ ] **Step 1: Amend the spec example**

In §3.3, replace the line:

```
(hand-authored unit vectors, e.g. `pipeline_stage → T_imports ∝ e_021C`, `layered_dag → ∝ e_030T`).
```

with:

```
(hand-authored points, e.g. `pipeline_stage → T_imports ∝ e_021C`;
`layered_dag → {021D: 0.5, 021C: 0.5}` — NOT `e_030T` as an earlier draft said:
030T is the transitive layer-skipping triangle, which clean layering minimizes.
Confirmed by measurement during Part B implementation: the layered_dag-bound
`components` domain measures 021D .778 / 021C .222 with zero 030T).
```

- [ ] **Step 2: Run the complete verification gate**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
```
Expected: format/lint clean, mypy strict 0 issues, ALL tests pass (expect ~500+), doc coverage ≥ 90%.

- [ ] **Step 3: Commit**

```bash
git add docs/specs/2026-06-09-pattern-alphabet-motif-basis-design.md
git commit -m "docs(spec): amend layered_dag ideal example — 030T is a layer-skip, not layering"
```

---

## Out of Scope (do NOT implement)

- **Rebinding the quotient to `orchestrator`** — that is the burn-in's decision, made after observation, not pre-empted by this plan.
- **TypeScript quotient binding** — spec claims only `cgis ≅ pipeline_stage`; a ts project_level entry waits for the TS resolver work.
- **Quotient unresolved_ratio plumbing** — acceptable gap while observe-only; recorded in the test docstring.
- **Removing v1 counting fields from `PatternFingerprint`** — they still feed the CLI fingerprint display and Part C.
- **Part C (typed codons / ATCG)** — gated on #47.
- **Null-model z-scores, edge-by-edge ideal diffing** — explicit spec non-goals (§5).
