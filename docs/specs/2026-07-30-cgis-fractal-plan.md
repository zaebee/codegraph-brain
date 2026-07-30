# cgis_fractal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an observe-only `cgis fractal` CLI command and `cgis_fractal` MCP tool that report how a repository's 13-triad motif distribution flows across its own structural tiers, summarized by the slope of Shannon entropy against log group count.

**Architecture:** One new module `src/cgis/query/drift/fractal.py` holds everything: ladder construction (parent-walk over `CONTAINS`/`DECLARES`, then directory folds), per-rung measurement (census → entropy → tangle), and the least-squares fit that yields the verdict. The CLI and MCP surfaces are thin renderers over one entry point, `analyze_fractal_db()`. Nothing in `drift.py`, `fingerprint.py` or `quotient.py` is touched — the existing quotient path and every gate stay exactly as they are.

**Tech Stack:** Python 3.12+, frozen dataclasses, typer + rich (CLI), FastMCP (`mcp>=2`), pytest. Reuses `cgis.query.drift.triads` (`triad_census`, `normalized_census`, `tangle_mass`, `TRIAD_ORDER`).

**Spec:** `docs/specs/2026-07-30-cgis-fractal-design.md`
**Issue:** #186, deliverable 3 of 3
**Reference implementation:** `scripts/probe_tier_ladder.py` — already committed, already reproduces the spec's baseline table. When in doubt about a formula, read it there. Do **not** import from `scripts/`; the probe is evidence, the module is the product.

## Global Constraints

- **MyPy strict** (`make type-check` runs `mypy src`). Every function needs full annotations including return types. No `Any` unless unavoidable.
- **Ruff** with the repo's full rule set (`E,W,F,UP,B,SIM,I,C90,N,C4,ANN,A,EM,ISC,G,PIE,PT,Q,RSE,RET,SLF,TCH,PTH,PLC,PLE,PLW,TRY,PERF,RUF`), line length **100**, double quotes.
- **Docstring coverage ≥ 90%** (`uv run interrogate src`). Every public function, class and module needs a docstring.
- **Observe-only.** No entry in `patterns.yaml`, no `_COMPONENT_NAMES` registration, no `hygiene_baseline` wiring, no change to `any_critical` or any gate. If a task tempts you to touch a gate, you have misread the spec.
- **Append-only** in `src/cgis/cli.py` and `src/cgis/api/mcp_server.py` — add new definitions at the end of the file; do not edit the `drift` command or the `cgis_drift` tool.
- **`docs/how-to/MCP_REFERENCE.md` is generated** by `scripts/generate_mcp_ref.py`. Never hand-edit it; regenerate and commit the result.
- **Frozen dataclasses** for all report types, matching `DriftReport` / `FitQuality` convention.
- **Full verification before every commit:** `make format && make lint && make type-check && make pytest && make doc-coverage`.
- Branch `feat/186-cgis-fractal`, worktree `.claude/worktrees/fractal`. Work there; the shared main checkout stays on clean `main`.

## File Structure

| File | Responsibility |
|------|----------------|
| `src/cgis/query/drift/fractal.py` (create) | The whole feature: ladder, per-rung measurement, fit, verdict, DB entry point |
| `tests/unit/test_fractal.py` (create) | Ladder shape, dedup, fit math, verdict bands, rung-truncation invariance |
| `tests/self_parsing/test_fractal.py` (create) | cgis measured on itself — acceptance criterion 1 |
| `src/cgis/cli.py` (modify, append) | `cgis fractal` command + `FractalOutputFormat` enum + `_render_fractal` |
| `src/cgis/api/mcp_server.py` (modify, append) | `cgis_fractal` MCP tool |
| `tests/unit/test_cli.py` (modify, append) | CLI smoke tests for the new command |
| `docs/how-to/MCP_REFERENCE.md` (regenerate) | Autodoc output, committed |

Task order is dependency order: 1 → 2 → 3 build the module bottom-up, 4 and 5 are independent surfaces over task 3, 6 is the acceptance measurement.

---

### Task 1: Ladder construction

**Files:**
- Create: `src/cgis/query/drift/fractal.py`
- Test: `tests/unit/test_fractal.py`

**Interfaces:**
- Consumes: `cgis.core.models` (`Node`, `Edge`, `EdgeType`, `NodeType`)
- Produces:
  - `build_ladder(nodes: list[Node], edges: list[Edge]) -> list[tuple[str, dict[str, str]]]` — ordered `(rung_name, node_id -> group_id)` pairs, finest first
  - Module constants `LADDER_LAYERS`, `MIN_RUNG_TRIADS`, `MIN_LIVE_RUNGS`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_fractal.py`:

```python
"""Unit tests for the structural tier ladder and its entropy slope (#186)."""

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.drift.fractal import build_ladder


def _node(fqn: str, ntype: NodeType, path: str) -> Node:
    """A graph node for ladder tests."""
    return Node(
        id=fqn,
        type=ntype,
        name=fqn.rsplit(".", 1)[-1],
        file_path=path,
        start_line=0,
        end_line=0,
    )


def _edge(src: str, tgt: str, etype: EdgeType) -> Edge:
    """A graph edge for ladder tests."""
    return Edge(
        id=f"{src}:{etype.value}:{tgt}",
        source=src,
        target=tgt,
        type=etype,
        weight=1.0,
        confidence=1.0,
    )


def test_t1_class_folds_method_into_declaring_class() -> None:
    nodes = [
        _node("pkg.mod", NodeType.FILE, "pkg/mod.py"),
        _node("pkg.mod.Thing", NodeType.CLASS, "pkg/mod.py"),
        _node("pkg.mod.Thing.run", NodeType.METHOD, "pkg/mod.py"),
    ]
    edges = [
        _edge("pkg.mod", "pkg.mod.Thing", EdgeType.CONTAINS),
        _edge("pkg.mod.Thing", "pkg.mod.Thing.run", EdgeType.DECLARES),
    ]

    ladder = dict(build_ladder(nodes, edges))

    assert ladder["T1_class"]["pkg.mod.Thing.run"] == "pkg.mod.Thing"
    assert ladder["T2_module"]["pkg.mod.Thing.run"] == "pkg.mod"


def test_t1_class_leaves_module_level_function_on_its_file() -> None:
    nodes = [
        _node("pkg.mod", NodeType.FILE, "pkg/mod.py"),
        _node("pkg.mod.helper", NodeType.FUNCTION, "pkg/mod.py"),
    ]
    edges = [_edge("pkg.mod", "pkg.mod.helper", EdgeType.CONTAINS)]

    ladder = dict(build_ladder(nodes, edges))

    assert ladder["T1_class"]["pkg.mod.helper"] == "pkg.mod"


def test_nested_function_folds_through_its_enclosing_function() -> None:
    nodes = [
        _node("pkg.mod", NodeType.FILE, "pkg/mod.py"),
        _node("pkg.mod.outer", NodeType.FUNCTION, "pkg/mod.py"),
        _node("pkg.mod.outer.inner", NodeType.FUNCTION, "pkg/mod.py"),
    ]
    edges = [
        _edge("pkg.mod", "pkg.mod.outer", EdgeType.CONTAINS),
        _edge("pkg.mod.outer", "pkg.mod.outer.inner", EdgeType.CONTAINS),
    ]

    ladder = dict(build_ladder(nodes, edges))

    assert ladder["T1_class"]["pkg.mod.outer.inner"] == "pkg.mod"
    assert ladder["T2_module"]["pkg.mod.outer.inner"] == "pkg.mod"


def test_directory_rungs_trim_from_the_leaf_so_every_file_moves() -> None:
    """The regression test for the root-truncation artifact (#186).

    Trimming from the root end leaves shallow files stationary and produces
    near-duplicate rungs. Trimming from the leaf end moves every file at every
    rung until it bottoms out at <root>.
    """
    nodes = [
        _node("a.b.c.deep", NodeType.FILE, "a/b/c/deep.py"),
        _node("a.shallow", NodeType.FILE, "a/shallow.py"),
    ]

    ladder = dict(build_ladder(nodes, []))

    assert ladder["T3_up1"]["a.b.c.deep"] == "a/b/c"
    assert ladder["T3_up1"]["a.shallow"] == "a"
    assert ladder["T4_up2"]["a.b.c.deep"] == "a/b"
    assert ladder["T4_up2"]["a.shallow"] == "<root>"
    assert ladder["T5_up3"]["a.b.c.deep"] == "a"


def test_ladder_starts_with_the_identity_rung() -> None:
    nodes = [_node("pkg.mod", NodeType.FILE, "pkg/mod.py")]

    rungs = build_ladder(nodes, [])

    assert rungs[0][0] == "T0_symbol"
    assert rungs[0][1] == {"pkg.mod": "pkg.mod"}


def test_containment_cycle_does_not_hang() -> None:
    """A malformed graph must not spin the parent-walk forever."""
    nodes = [
        _node("pkg.a", NodeType.FUNCTION, "pkg/x.py"),
        _node("pkg.b", NodeType.FUNCTION, "pkg/x.py"),
    ]
    edges = [
        _edge("pkg.a", "pkg.b", EdgeType.CONTAINS),
        _edge("pkg.b", "pkg.a", EdgeType.CONTAINS),
    ]

    ladder = dict(build_ladder(nodes, edges))

    assert ladder["T1_class"]["pkg.a"] == "pkg.a"
    assert ladder["T1_class"]["pkg.b"] == "pkg.b"


def test_empty_graph_yields_no_rungs_beyond_identity() -> None:
    rungs = build_ladder([], [])

    assert rungs[0][0] == "T0_symbol"
    assert rungs[0][1] == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_fractal.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cgis.query.drift.fractal'`

- [ ] **Step 3: Write the module**

Create `src/cgis/query/drift/fractal.py`:

```python
"""Structural tier ladder and its entropy slope (spec 2026-07-30, #186).

Coarsens the graph along its OWN structure — symbol, class, module, then
directory levels — and measures the 13-triad census at every rung. The number of
rungs is set by the repository, never by a swept parameter: grain-dependence is
what retired the closure-gap metric on the same issue.
"""

import math
from dataclasses import dataclass

from cgis.core.models import Edge, EdgeType, Node, NodeType

#: Layers the ladder is measured on, in report order.
LADDER_LAYERS: tuple[EdgeType, ...] = (EdgeType.IMPORTS, EdgeType.CALLS)

#: The only structural edge types in the graph; a parent-walk follows these.
_STRUCT_EDGES = frozenset({EdgeType.CONTAINS, EdgeType.DECLARES})
_SYMBOL_TYPES = frozenset({NodeType.FUNCTION, NodeType.METHOD, NodeType.VARIABLE})
_FILE_TYPES = frozenset({NodeType.FILE, NodeType.MODULE})

#: Data-sufficiency floor: a rung below this many triads is reported but not
#: fitted. It decides whether a rung is OBSERVED, never what the verdict is.
MIN_RUNG_TRIADS = 10

#: Fewer live rungs than this and there is no curve to fit.
MIN_LIVE_RUNGS = 3

#: Group id for a file that has been folded above its top directory.
ROOT_GROUP = "<root>"

Grouping = dict[str, str]


def _parent_map(nodes: list[Node], edges: list[Edge]) -> dict[str, str]:
    """Child id -> parent id, from CONTAINS / DECLARES edges."""
    ids = {n.id for n in nodes}
    return {e.target: e.source for e in edges if e.type in _STRUCT_EDGES and e.source in ids}


def _walk_to(
    node_id: str,
    parents: dict[str, str],
    types: dict[str, NodeType],
    stop: frozenset[NodeType],
) -> str:
    """Walk up the containment chain to the nearest ancestor of a stop type.

    Cycle-guarded: a malformed graph returns the node itself rather than
    looping. A node with no ancestor of a stop type is its own group.
    """
    seen: set[str] = set()
    current: str | None = node_id
    while current is not None and current not in seen:
        if types.get(current) in stop:
            return current
        seen.add(current)
        current = parents.get(current)
    return node_id


def _directory_parts(nodes: list[Node], file_of: Grouping) -> dict[str, list[str]]:
    """Node id -> the directory components of its file's path."""
    by_id = {n.id: n for n in nodes}
    parts: dict[str, list[str]] = {}
    for node_id, file_id in file_of.items():
        node = by_id.get(file_id)
        parts[node_id] = node.file_path.split("/")[:-1] if node and node.file_path else []
    return parts


def build_ladder(nodes: list[Node], edges: list[Edge]) -> list[tuple[str, Grouping]]:
    """Return the repository's structural rungs, finest first.

    ``T0_symbol`` is the identity grouping, ``T1_class`` folds symbols into their
    declaring class, ``T2_module`` folds everything into its file, and each
    ``Tn_upk`` folds files into a directory with ``k - 1`` components trimmed
    **from the leaf end** — so every file moves at every rung until it bottoms
    out at ``<root>``.
    """
    parents = _parent_map(nodes, edges)
    types = {n.id: n.type for n in nodes}

    file_of = {n.id: _walk_to(n.id, parents, types, _FILE_TYPES) for n in nodes}
    class_of = {
        n.id: (
            _walk_to(n.id, parents, types, _FILE_TYPES | {NodeType.CLASS})
            if n.type in _SYMBOL_TYPES
            else n.id
        )
        for n in nodes
    }
    parts = _directory_parts(nodes, file_of)

    rungs: list[tuple[str, Grouping]] = [
        ("T0_symbol", {n.id: n.id for n in nodes}),
        ("T1_class", class_of),
        ("T2_module", file_of),
    ]
    depth = max((len(p) for p in parts.values()), default=0)
    for k in range(1, depth + 1):
        rungs.append(
            (
                f"T{len(rungs)}_up{k}",
                {
                    node_id: "/".join(p[: max(len(p) - k + 1, 0)]) or ROOT_GROUP
                    for node_id, p in parts.items()
                },
            )
        )
    return rungs
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_fractal.py -v`
Expected: PASS — 7 passed

- [ ] **Step 5: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/query/drift/fractal.py tests/unit/test_fractal.py
git commit -m "feat(fractal): structural tier ladder (#186)"
```

---

### Task 2: Per-rung measurement

**Files:**
- Modify: `src/cgis/query/drift/fractal.py` (append)
- Test: `tests/unit/test_fractal.py` (append)

**Interfaces:**
- Consumes: `build_ladder`, `MIN_RUNG_TRIADS`, `LADDER_LAYERS` from Task 1; `cgis.query.drift.triads` (`TRIAD_ORDER`, `normalized_census`, `tangle_mass`, `triad_census`)
- Produces:
  - `RungReport` — frozen dataclass with fields `name: str`, `groups: int`, `triads: int`, `census: tuple[float, ...]`, `entropy: float | None`, `dominant: str`, `dominant_share: float`, `tangle_ratio: float`, `live: bool`
  - `measure_layer(nodes: list[Node], edges: list[Edge], layer: EdgeType) -> list[RungReport]`
  - `entropy_bits(census: tuple[float, ...]) -> float`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_fractal.py` (and extend the import line at the top to
`from cgis.query.drift.fractal import RungReport, build_ladder, entropy_bits, measure_layer`):

```python
def _files(*paths: str) -> list[Node]:
    """FILE nodes named after their paths, dots for slashes."""
    return [_node(p.removesuffix(".py").replace("/", "."), NodeType.FILE, p) for p in paths]


def test_entropy_of_a_single_motif_is_zero() -> None:
    census = tuple(1.0 if name == "021C" else 0.0 for name in TRIAD_ORDER)

    assert entropy_bits(census) == 0.0


def test_entropy_of_two_equal_motifs_is_one_bit() -> None:
    census = tuple(0.5 if name in ("021C", "021D") else 0.0 for name in TRIAD_ORDER)

    assert entropy_bits(census) == 1.0


def test_identical_censuses_collapse_to_one_rung() -> None:
    """IMPORTS edges connect only FILE nodes, so T0/T1/T2 are the same quotient."""
    nodes = _files("p/f1.py", "p/f2.py", "p/f3.py")
    edges = [
        _edge("p.f1", "p.f2", EdgeType.IMPORTS),
        _edge("p.f2", "p.f3", EdgeType.IMPORTS),
    ]

    rungs = measure_layer(nodes, edges, EdgeType.IMPORTS)

    assert [r.name for r in rungs] == ["T0_symbol", "T3_up1"]


def test_thin_rungs_are_reported_but_not_live() -> None:
    nodes = _files("p/f1.py", "p/f2.py", "p/f3.py")
    edges = [
        _edge("p.f1", "p.f2", EdgeType.IMPORTS),
        _edge("p.f2", "p.f3", EdgeType.IMPORTS),
    ]

    rungs = measure_layer(nodes, edges, EdgeType.IMPORTS)

    assert rungs[0].triads == 1
    assert rungs[0].live is False
    assert rungs[0].dominant == "021C"


def test_a_rung_with_no_triads_has_no_entropy() -> None:
    nodes = _files("p/f1.py", "p/f2.py")

    rungs = measure_layer(nodes, [], EdgeType.CALLS)

    assert rungs[0].triads == 0
    assert rungs[0].entropy is None
    assert rungs[0].live is False
```

Add `from cgis.query.drift.triads import TRIAD_ORDER` to the test module's imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_fractal.py -v -k "entropy or rung"`
Expected: FAIL — `ImportError: cannot import name 'measure_layer'`

- [ ] **Step 3: Implement the measurement**

Append to `src/cgis/query/drift/fractal.py`, and add to its imports:

```python
from cgis.query.drift.triads import TRIAD_ORDER, normalized_census, tangle_mass, triad_census
```

```python
@dataclass(frozen=True)
class RungReport:
    """One rung of the ladder measured on one layer.

    ``live`` marks a rung with enough triads to enter the fit; thin and
    single-group rungs are still reported so the curve stays readable.
    """

    name: str
    groups: int
    triads: int
    census: tuple[float, ...]
    entropy: float | None
    dominant: str
    dominant_share: float
    tangle_ratio: float
    live: bool


def entropy_bits(census: tuple[float, ...]) -> float:
    """Shannon entropy of a normalized census, in bits (max log2(13) ~ 3.70)."""
    return -sum(p * math.log2(p) for p in census if p > 0)


def _quotient_edges(grouping: Grouping, edges: list[Edge], layer: EdgeType) -> list[Edge]:
    """Distinct cross-group edges of one layer; self-loops dropped."""
    pairs = {
        (grouping[e.source], grouping[e.target])
        for e in edges
        if e.type is layer
        and e.source in grouping
        and e.target in grouping
        and grouping[e.source] != grouping[e.target]
    }
    return [
        Edge(
            id=f"{u}:{layer.value}:{v}",
            source=u,
            target=v,
            type=layer,
            weight=1.0,
            confidence=1.0,
        )
        for u, v in sorted(pairs)
    ]


def _rung_report(name: str, groups: int, counts: dict[str, int]) -> RungReport:
    """Build one RungReport from a raw triad census."""
    triads = sum(counts.values())
    census = normalized_census(counts)
    dominant, share = (
        max(zip(TRIAD_ORDER, census, strict=True), key=lambda kv: kv[1]) if triads else ("-", 0.0)
    )
    return RungReport(
        name=name,
        groups=groups,
        triads=triads,
        census=census,
        entropy=entropy_bits(census) if triads else None,
        dominant=dominant,
        dominant_share=share,
        tangle_ratio=tangle_mass(census),
        live=triads >= MIN_RUNG_TRIADS and groups > 1,
    )


def measure_layer(nodes: list[Node], edges: list[Edge], layer: EdgeType) -> list[RungReport]:
    """Measure every rung on one layer, collapsing rungs with identical censuses.

    The dedup is not an optimization. IMPORTS edges connect only FILE nodes, so
    ``T0``/``T1``/``T2`` are literally the same import quotient — counting them
    three times would triple-weight one observation in the fit.
    """
    rows: list[RungReport] = []
    for name, grouping in build_ladder(nodes, edges):
        counts = triad_census(set(grouping.values()), _quotient_edges(grouping, edges, layer), layer)
        row = _rung_report(name, len(set(grouping.values())), counts)
        if rows and rows[-1].census == row.census and rows[-1].triads == row.triads:
            continue
        rows.append(row)
    return rows
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_fractal.py -v`
Expected: PASS — 12 passed

- [ ] **Step 5: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/query/drift/fractal.py tests/unit/test_fractal.py
git commit -m "feat(fractal): per-rung census, entropy and census dedup (#186)"
```

---

### Task 3: Fit, verdict and the DB entry point

**Files:**
- Modify: `src/cgis/query/drift/fractal.py` (append)
- Test: `tests/unit/test_fractal.py` (append)

**Interfaces:**
- Consumes: `RungReport`, `measure_layer`, `MIN_LIVE_RUNGS`, `LADDER_LAYERS` from Tasks 1–2; `cgis.storage.sqlite_store.SQLiteStore`
- Produces:
  - `FractalFit` — frozen dataclass with `slope: float`, `r_squared: float`, `std_error: float`, `live_rungs: int`
  - `FractalReport` — frozen dataclass with `layer: str`, `rungs: list[RungReport]`, `fit: FractalFit | None`, `verdict: str`
  - `fit_ladder(rungs: list[RungReport]) -> FractalFit | None`
  - `verdict_of(fit: FractalFit | None) -> str` — one of `"hierarchical"`, `"flat"`, `"scale_invariant"`, `"no_signal"`
  - `analyze_fractal(nodes: list[Node], edges: list[Edge], layer: EdgeType) -> FractalReport`
  - `analyze_fractal_db(db_path: str) -> list[FractalReport]` — one report per entry in `LADDER_LAYERS`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_fractal.py`:

```python
def _rung(name: str, groups: int, entropy: float) -> RungReport:
    """A live RungReport carrying only the two fields the fit reads."""
    return RungReport(
        name=name,
        groups=groups,
        triads=100,
        census=ZERO_TRIADS,
        entropy=entropy,
        dominant="021U",
        dominant_share=1.0,
        tangle_ratio=0.0,
        live=True,
    )


def test_rising_diversity_under_coarsening_is_hierarchical() -> None:
    rungs = [_rung("a", 800, 0.9), _rung("b", 400, 1.1), _rung("c", 200, 1.3), _rung("d", 100, 1.5)]

    fit = fit_ladder(rungs)

    assert fit is not None
    assert fit.slope > 0
    assert verdict_of(fit) == "hierarchical"


def test_collapsing_diversity_under_coarsening_is_flat() -> None:
    rungs = [_rung("a", 800, 1.5), _rung("b", 400, 1.1), _rung("c", 200, 0.7), _rung("d", 100, 0.3)]

    fit = fit_ladder(rungs)

    assert fit is not None
    assert fit.slope < 0
    assert verdict_of(fit) == "flat"


def test_constant_entropy_is_scale_invariant() -> None:
    rungs = [_rung("a", 800, 1.2), _rung("b", 400, 1.2), _rung("c", 200, 1.2)]

    assert verdict_of(fit_ladder(rungs)) == "scale_invariant"


def test_slope_inside_the_dead_band_is_scale_invariant() -> None:
    """A noisy near-zero trend must not be reported as a direction."""
    rungs = [_rung("a", 800, 1.0), _rung("b", 400, 1.4), _rung("c", 200, 1.0), _rung("d", 100, 1.4)]

    fit = fit_ladder(rungs)

    assert fit is not None
    assert abs(fit.slope) <= 2.0 * fit.std_error
    assert verdict_of(fit) == "scale_invariant"


def test_too_few_live_rungs_is_no_signal() -> None:
    rungs = [_rung("a", 800, 0.9), _rung("b", 400, 1.4)]

    assert fit_ladder(rungs) is None
    assert verdict_of(None) == "no_signal"


def test_dead_rungs_are_excluded_from_the_fit() -> None:
    live = [_rung("a", 800, 0.9), _rung("b", 400, 1.1), _rung("c", 200, 1.3)]
    dead = RungReport(
        name="top",
        groups=2,
        triads=1,
        census=ZERO_TRIADS,
        entropy=0.0,
        dominant="300",
        dominant_share=1.0,
        tangle_ratio=1.0,
        live=False,
    )

    fit = fit_ladder([*live, dead])

    assert fit is not None
    assert fit.live_rungs == 3


def test_verdict_survives_truncating_the_ladder() -> None:
    """Acceptance criterion 2: the verdict must not depend on rung count.

    This is the property whose absence retired the closure-gap metric — its
    ranking reshuffled completely when the grain was swept.
    """
    rungs = [
        _rung("a", 1600, 0.8),
        _rung("b", 800, 1.0),
        _rung("c", 400, 1.2),
        _rung("d", 200, 1.4),
        _rung("e", 100, 1.6),
    ]

    full = verdict_of(fit_ladder(rungs))

    assert full == "hierarchical"
    assert verdict_of(fit_ladder(rungs[1:])) == full
    assert verdict_of(fit_ladder(rungs[:-1])) == full
    assert verdict_of(fit_ladder(rungs[1:-1])) == full


def test_analyze_fractal_reports_both_the_curve_and_the_verdict() -> None:
    nodes = _files("p/f1.py", "p/f2.py", "p/f3.py")
    edges = [
        _edge("p.f1", "p.f2", EdgeType.IMPORTS),
        _edge("p.f2", "p.f3", EdgeType.IMPORTS),
    ]

    report = analyze_fractal(nodes, edges, EdgeType.IMPORTS)

    assert report.layer == "IMPORTS"
    assert report.verdict == "no_signal"
    assert [r.name for r in report.rungs] == ["T0_symbol", "T3_up1"]
```

Extend the test module's imports to:

```python
from cgis.query.drift.fractal import (
    RungReport,
    analyze_fractal,
    build_ladder,
    entropy_bits,
    fit_ladder,
    measure_layer,
    verdict_of,
)
from cgis.query.drift.triads import TRIAD_ORDER, ZERO_TRIADS
```

Import only what the tests use — ruff's `F401` fails the build on an unused
import, so do not add `FractalReport` here.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_fractal.py -v -k "verdict or fit or analyze"`
Expected: FAIL — `ImportError: cannot import name 'fit_ladder'`

- [ ] **Step 3: Implement the fit**

Append to `src/cgis/query/drift/fractal.py`, adding `from cgis.storage.sqlite_store import SQLiteStore` and `from pathlib import Path` to its imports:

```python
#: Numeric floor below which a sum of squares counts as zero.
_EPS = 1e-12


@dataclass(frozen=True)
class FractalFit:
    """Least-squares fit of entropy against log group count."""

    slope: float
    r_squared: float
    std_error: float
    live_rungs: int


@dataclass(frozen=True)
class FractalReport:
    """One layer's ladder, its fit and the resulting verdict."""

    layer: str
    rungs: list[RungReport]
    fit: FractalFit | None
    verdict: str


def fit_ladder(rungs: list[RungReport]) -> FractalFit | None:
    """Fit entropy against ``-log2(groups)`` over the live rungs.

    ``x`` increases as the graph coarsens, so a positive slope means coarsening
    ADDS motif diversity. The fit is rung-count invariant by construction: it
    normalizes by actual collapse, not by rung index. Returns None when there is
    no curve to fit.
    """
    points = [(-math.log2(r.groups), r.entropy) for r in rungs if r.live and r.entropy is not None]
    if len(points) < MIN_LIVE_RUNGS:
        return None
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx < _EPS:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / sxx
    ss_res = sum(
        (y - (mean_y + slope * (x - mean_x))) ** 2 for x, y in zip(xs, ys, strict=True)
    )
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    return FractalFit(
        slope=slope,
        # A perfectly flat curve has no variance to explain; report 0.0 rather
        # than NaN so the value stays JSON-serializable.
        r_squared=1.0 - ss_res / ss_tot if ss_tot > _EPS else 0.0,
        std_error=math.sqrt(ss_res / (len(points) - 2) / sxx),
        live_rungs=len(points),
    )


def verdict_of(fit: FractalFit | None) -> str:
    """Three-way verdict from the sign of the slope with a 2*SE dead-band.

    The dead-band comes from the fit's own residuals rather than a tuned
    constant: every thresholded verdict in #186's history was falsified by
    re-measurement.
    """
    if fit is None:
        return "no_signal"
    band = 2.0 * fit.std_error
    if fit.slope > band:
        return "hierarchical"
    if fit.slope < -band:
        return "flat"
    return "scale_invariant"


def analyze_fractal(nodes: list[Node], edges: list[Edge], layer: EdgeType) -> FractalReport:
    """Measure one layer's ladder and band it."""
    rungs = measure_layer(nodes, edges, layer)
    fit = fit_ladder(rungs)
    return FractalReport(layer=layer.value, rungs=rungs, fit=fit, verdict=verdict_of(fit))


def analyze_fractal_db(db_path: str) -> list[FractalReport]:
    """Measure every ladder layer from a graph database.

    Raises:
        FileNotFoundError: If ``db_path`` does not point to an existing file.
            Use ``cgis ingest`` to create the graph database first.
    """
    if not Path(db_path).is_file():
        msg = f"Graph database not found: {db_path}"
        raise FileNotFoundError(msg)
    with SQLiteStore(db_path) as store:
        nodes, edges = store.get_all_nodes(), store.get_all_edges()
    return [analyze_fractal(nodes, edges, layer) for layer in LADDER_LAYERS]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_fractal.py -v`
Expected: PASS — 20 passed

- [ ] **Step 5: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/query/drift/fractal.py tests/unit/test_fractal.py
git commit -m "feat(fractal): entropy slope fit, dead-band verdict, db entry point (#186)"
```

---

### Task 4: CLI command

**Files:**
- Modify: `src/cgis/cli.py` (append at end, before the `if __name__ == "__main__":` block)
- Test: `tests/unit/test_cli.py` (append)

**Interfaces:**
- Consumes: `analyze_fractal_db`, `FractalReport` from Task 3
- Produces: the `cgis fractal` command; no Python API other tasks depend on

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_cli.py`. It already imports everything these tests
need (`json`, `Path`, `make_file_node` and `make_import_edge` from `conftest`,
`SQLiteStore`, `app`), already defines the module-level `runner = CliRunner()`,
and already has a `_plain()` helper that strips ANSI escapes — Rich colorizes
output, so assert against `_plain(result.stdout)`, never the raw string.

The store's write API is `save_graph(nodes, edges)`. There is no `save_nodes` or
`save_edges`.

```python
def test_fractal_reports_a_verdict_per_layer(tmp_path: Path) -> None:
    db_path = str(tmp_path / "graph.db")
    nodes = [
        make_file_node("p.f1", "p/f1.py"),
        make_file_node("p.f2", "p/f2.py"),
        make_file_node("p.f3", "p/f3.py"),
    ]
    edges = [make_import_edge("p.f1", "p.f2"), make_import_edge("p.f2", "p.f3")]
    with SQLiteStore(db_path) as store:
        store.save_graph(nodes, edges)

    result = runner.invoke(app, ["fractal", "--db", db_path])

    assert result.exit_code == 0
    assert "IMPORTS" in _plain(result.stdout)
    assert "CALLS" in _plain(result.stdout)


def test_fractal_json_format_is_machine_readable(tmp_path: Path) -> None:
    db_path = str(tmp_path / "graph.db")
    with SQLiteStore(db_path) as store:
        store.save_graph([make_file_node("p.f1", "p/f1.py")], [])

    result = runner.invoke(app, ["fractal", "--db", db_path, "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [layer["layer"] for layer in payload["layers"]] == ["IMPORTS", "CALLS"]
    assert payload["layers"][0]["verdict"] == "no_signal"


def test_fractal_missing_db_exits_nonzero(tmp_path: Path) -> None:
    result = runner.invoke(app, ["fractal", "--db", str(tmp_path / "nope.db")])

    assert result.exit_code == 1
    assert "not found" in _plain(result.stdout).lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_cli.py -v -k fractal`
Expected: FAIL — exit code 2, "No such command 'fractal'"

- [ ] **Step 3: Implement the command**

Add the enum next to the other format enums near the top of `src/cgis/cli.py`
(after `class SuggestOutputFormat`):

```python
class FractalOutputFormat(StrEnum):
    """Supported output formats for the fractal command."""

    TEXT = "text"
    JSON = "json"
```

Add the import alongside the other drift imports:

```python
from cgis.query.drift.fractal import FractalReport, analyze_fractal_db
```

Append at the end of the file, before `if __name__ == "__main__":`:

```python
def _render_fractal(reports: list[FractalReport]) -> None:
    """Render one table per layer: the ladder, then the fit and verdict."""
    for report in reports:
        fit = report.fit
        summary = (
            f"slope={fit.slope:+.3f} R²={fit.r_squared:.2f} "
            f"band=±{2 * fit.std_error:.3f} live={fit.live_rungs}"
            if fit is not None
            else "no fit"
        )
        console.print(
            f"\n[bold]{report.layer}[/bold]  "
            f"[cyan]{report.verdict}[/cyan]  [dim]{summary}[/dim]"
        )
        table = Table()
        table.add_column("Rung", style="white")
        table.add_column("Groups", justify="right")
        table.add_column("Triads", justify="right")
        table.add_column("H (bits)", justify="right")
        table.add_column("Dominant", style="cyan")
        table.add_column("Tangle", justify="right")
        for rung in report.rungs:
            entropy = "—" if rung.entropy is None else f"{rung.entropy:.2f}"
            name = rung.name if rung.live else f"{rung.name} [dim](no_signal)[/dim]"
            table.add_row(
                name,
                str(rung.groups),
                str(rung.triads),
                entropy,
                f"{rung.dominant}:{rung.dominant_share:.2f}",
                f"{rung.tangle_ratio:.3f}",
            )
        console.print(table)


@app.command()
def fractal(
    db: str = typer.Option(_DEFAULT_DB, "--db", "-d", help=_DEFAULT_DB_HELP),
    output_format: FractalOutputFormat = typer.Option(
        FractalOutputFormat.TEXT, "--format", "-f", help=_TEXT_JSON_FORMAT_HELP
    ),
) -> None:
    """Report the motif census across the repository's structural tiers.

    Coarsens the graph along its own structure — symbol, class, module, then
    directory levels — and fits Shannon entropy against log group count. A
    positive slope means coarsening adds motif diversity (`hierarchical`); a
    negative one means it destroys it (`flat`). Observe-only: always exits 0 on
    success. Run `ingest` first.
    """
    try:
        reports = analyze_fractal_db(db)
    except FileNotFoundError as e:
        console.print(f"[bold red]❌ Database not found:[/bold red] {db}. Run `ingest` first.")
        raise typer.Exit(code=1) from e
    except Exception as e:
        console.print(f"[bold red]❌ Error during fractal analysis:[/bold red] {e}")
        raise typer.Exit(code=1) from e

    if output_format == FractalOutputFormat.JSON:
        typer.echo(
            _json.dumps({"layers": [dataclasses.asdict(r) for r in reports]}, indent=2)
        )
        return
    _render_fractal(reports)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_cli.py -v -k fractal`
Expected: PASS — 3 passed

- [ ] **Step 5: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): cgis fractal command (#186)"
```

---

### Task 5: MCP tool

**Files:**
- Modify: `src/cgis/api/mcp_server.py` (append after the last `@mcp.tool()`)
- Regenerate: `docs/how-to/MCP_REFERENCE.md`

**Interfaces:**
- Consumes: `analyze_fractal_db` from Task 3
- Produces: the `cgis_fractal` MCP tool returning a JSON string

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_fractal.py`:

```python
def test_mcp_fractal_returns_json_layers(tmp_path: Path) -> None:
    from cgis.api.mcp_server import cgis_fractal

    db_path = str(tmp_path / "graph.db")
    with SQLiteStore(db_path) as store:
        store.save_graph(
            _files("p/f1.py", "p/f2.py", "p/f3.py"),
            [
                _edge("p.f1", "p.f2", EdgeType.IMPORTS),
                _edge("p.f2", "p.f3", EdgeType.IMPORTS),
            ],
        )

    payload = json.loads(cgis_fractal(db_path=db_path))

    assert [layer["layer"] for layer in payload["layers"]] == ["IMPORTS", "CALLS"]
    assert payload["layers"][0]["verdict"] == "no_signal"


def test_mcp_fractal_reports_a_missing_database(tmp_path: Path) -> None:
    from cgis.api.mcp_server import cgis_fractal

    assert "❌" in cgis_fractal(db_path=str(tmp_path / "nope.db"))
```

Add `import json` and `from pathlib import Path` and
`from cgis.storage.sqlite_store import SQLiteStore` to the test module's imports.

Note: the MCP tools in this repo are plain functions decorated with `@mcp.tool()`
and remain directly callable — see `tests/unit/test_generate_mcp_ref.py` for the
existing precedent.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_fractal.py -v -k mcp`
Expected: FAIL — `ImportError: cannot import name 'cgis_fractal'`

- [ ] **Step 3: Implement the tool**

Add the import alongside the other drift imports in `src/cgis/api/mcp_server.py`:

```python
from cgis.query.drift.fractal import analyze_fractal_db
```

Append after the last `@mcp.tool()` definition:

```python
@mcp.tool()
def cgis_fractal(db_path: str = _DEFAULT_DB) -> str:
    """Report the motif census across the repository's structural tiers.

    Coarsens the graph along its own structure — symbol, class, module, then
    directory levels trimmed from the leaf end — and measures the 13-triad
    census at every rung. Returns JSON: one entry per layer (IMPORTS, CALLS)
    with the full per-rung curve (groups, triads, entropy in bits, dominant
    motif, tangle ratio) and the fit.

    ``verdict`` is the sign of ``slope`` (entropy bits per halving of the group
    count) outside a ``2 × std_error`` dead-band: ``hierarchical`` means
    coarsening ADDS motif diversity, ``flat`` means it destroys it,
    ``scale_invariant`` means the mix is the same at every scale, and
    ``no_signal`` means fewer than three rungs carried enough triads to fit.

    Read the curve, not just the verdict — the fit is a lossy summary of a
    non-linear curve. Observe-only: this tool enforces nothing and no gate
    reads it. Call after ``cgis_ingest``.
    """
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        reports = analyze_fractal_db(db_path)
        payload = {"layers": [dataclasses.asdict(r) for r in reports]}
        return json.dumps(payload, indent=2)
    except Exception as exc:
        return f"❌ {exc}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_fractal.py -v -k mcp`
Expected: PASS — 2 passed

- [ ] **Step 5: Regenerate the MCP reference**

Run: `uv run python scripts/generate_mcp_ref.py`
Then: `uv run pytest tests/unit/test_generate_mcp_ref.py -v`
Expected: PASS, and `git diff docs/how-to/MCP_REFERENCE.md` shows the new
`cgis_fractal` row. Never hand-edit that file — if the diff looks wrong, fix the
docstring and regenerate.

- [ ] **Step 6: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/api/mcp_server.py docs/how-to/MCP_REFERENCE.md tests/unit/test_fractal.py
git commit -m "feat(mcp): cgis_fractal tool (#186)"
```

---

### Task 6: Self-parsing acceptance measurement

**Files:**
- Create: `tests/self_parsing/test_fractal.py`

**Interfaces:**
- Consumes: `analyze_fractal`, `verdict_of` from Task 3; the session-scoped
  `graph_data` fixture from `tests/self_parsing/conftest.py`, which yields
  `(store, nodes, resolved_edges)` for `src/cgis/`

- [ ] **Step 1: Write the failing test**

Create `tests/self_parsing/test_fractal.py`:

```python
"""cgis measured on itself — acceptance criterion 1 of the #186 fractal spec."""

from cgis.core.models import Edge, EdgeType, Node
from cgis.query.drift.fractal import analyze_fractal
from cgis.storage.sqlite_store import SQLiteStore


def test_cgis_calls_ladder_is_hierarchical(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Coarsening cgis's own call graph must ADD motif diversity.

    The band is deliberately loose: it asserts the sign and the order of
    magnitude, not a value that ordinary refactors would break. The spec
    measured +0.146 on a src/ ingest; this fixture ingests src/cgis/, one
    directory level shallower, so the value shifts slightly.
    """
    _store, nodes, edges = graph_data

    report = analyze_fractal(nodes, edges, EdgeType.CALLS)

    assert report.verdict == "hierarchical"
    assert report.fit is not None
    assert 0.05 <= report.fit.slope <= 0.30


def test_cgis_ladder_reports_a_real_curve(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """The report must carry the evidence, not only the headline."""
    _store, nodes, edges = graph_data

    report = analyze_fractal(nodes, edges, EdgeType.CALLS)

    assert len(report.rungs) >= 4
    assert report.rungs[0].name == "T0_symbol"
    assert sum(1 for r in report.rungs if r.live) >= 3
    assert all(r.entropy is None or r.entropy >= 0.0 for r in report.rungs)
```

- [ ] **Step 2: Run the test to verify it fails or passes for the right reason**

Run: `uv run pytest tests/self_parsing/test_fractal.py -v -s`
Expected: PASS. If `test_cgis_calls_ladder_is_hierarchical` fails on the band
rather than the verdict, print the measured slope and widen the band **only**
after confirming the verdict is still `hierarchical` — the sign is the contract,
the band is a sanity rail.

- [ ] **Step 3: Cross-check against the committed probe**

Run:
```bash
uv run cgis ingest src --source-root src -o /tmp/cgis-check.db
uv run python scripts/probe_tier_ladder.py cgis=/tmp/cgis-check.db
uv run cgis fractal --db /tmp/cgis-check.db
```
Expected: the probe and the new command report the same slope, R², dead-band and
verdict for both layers. A mismatch means the module diverged from the reference
implementation — fix the module, not the probe.

- [ ] **Step 4: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add tests/self_parsing/test_fractal.py
git commit -m "test(fractal): self-parsing acceptance measurement (#186)"
```

- [ ] **Step 5: Record the measured value in the spec**

If the self-parsing slope differs from the spec's `+0.146`, append one line to
the "Measured baseline" section of `docs/specs/2026-07-30-cgis-fractal-design.md`
noting the fixture's value and why it differs (ingest root). Do not edit the
six-repo table — those numbers were measured on `src/` ingests and stay as the
published baseline.

```bash
git add docs/specs/2026-07-30-cgis-fractal-design.md
git commit -m "docs(specs): record the self-parsing fractal slope (#186)"
```

---

## Definition of done

- `uv run cgis fractal --db graph.db` prints a ladder table and a verdict per layer.
- `cgis_fractal` appears in `docs/how-to/MCP_REFERENCE.md` (generated, not hand-written).
- `make format && make lint && make type-check && make pytest && make doc-coverage` all pass.
- The new command reproduces `scripts/probe_tier_ladder.py` on cgis itself.
- No gate, no `patterns.yaml` entry, no change to `any_critical`.
- Spec acceptance criteria 1 and 2 are covered by tests
  (`tests/self_parsing/test_fractal.py`, `test_verdict_survives_truncating_the_ladder`);
  criterion 3 was satisfied before implementation and is recorded in the spec.
