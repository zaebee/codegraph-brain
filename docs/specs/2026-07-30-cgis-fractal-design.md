# `cgis_fractal` — structural tier ladder (#186)

**Status:** approved (2026-07-30)
**Issue:** #186 (recursive-quotient / pattern-algebra macro track), deliverable 3 of 3
**Lane:** A (drift / ontology / query)

## Goal

Report how a repository's 13-triad motif distribution flows as the graph is
coarsened along its **own structural tiers** — symbol → class → module →
directory levels — and summarize that flow as a single rung-count-invariant
number: the slope of Shannon entropy against log group count.

This replaces FQN-depth truncation, which #186 has now shown three separate times
to produce grain-dependent artifacts.

## Motivation, and what this spec retracts

Two prior #186 results do not survive a structurally-defined ladder. Both were
measured during the 2026-07-29/30 dig and are retracted here.

**1. The 2D verdict `(H-range × collapse-ratio)` inverts.** Measured on the tier
ladder (CALLS layer):

| repo | rungs (live) | H-range | collapse |
|------|--------------|---------|----------|
| django | 8 (6) | 1.88 | ×0.27 |
| cgis | 6 (4) | 1.13 | ×0.28 |
| flask | 4 (3) | 0.86 | ×0.15 |

flask collapses *deeper* than django, so the published rule ("deep collapse +
low H-range ⇒ FRACTAL") now labels flask fractal and django layered — the exact
opposite of the recorded verdicts. Cause: both axes are proxies for directory
depth, `collapse` as a per-step geometric mean is dominated by the single largest
jump (flask 558→3), and `H-range` grows mechanically with the number of rungs
sampled, which varies by repo. The verdict is retired; see "The summary" below
for what replaces it.

**2. The entropy valley / "module tier = ground state" does not reproduce.**
Under tier grouping, CALLS entropy rises monotonically from the symbol rung:
django `0.32 → 1.11 → 1.22 → 1.17 → 2.20 → 2.14`, owner-api
`0.33 → 1.21 → 1.29 → 1.27 → 1.73 → 2.23`. The minimum sits at the symbol rung,
not the module rung. The earlier V-shape came from a different grouping whose
"module" tier corresponds to this ladder's first *directory* rung.

Not retracted, and confirmed a third time: the `021U → 021D` mirror flip does not
occur. Dominant motif stays `021U` at every rung for django, owner-api and
sqlalchemy.

## The ladder

Rungs are structural. Their **number is determined by the repository**, never by
a swept parameter — this is the property whose absence killed deliverable 2
(`closure-gap`, retired in #186).

| rung | grouping |
|------|----------|
| `T0_symbol` | identity — every node is its own group |
| `T1_class` | `FUNCTION` / `METHOD` / `VARIABLE` walk up `DECLARES` / `CONTAINS` to the nearest `CLASS`, falling back to their `FILE`; `CLASS` and `FILE` nodes map to themselves |
| `T2_module` | every node walks to its `FILE` / `MODULE` ancestor |
| `T3_up1` … `Tn_upk` | the file's directory path with `k` components trimmed **from the leaf end**, `k = 1 … max_depth`; an emptied path becomes `<root>` |

Directory rungs trim from the leaf, not from the root. Root-side truncation was
measured and rejected: it leaves shallow files stationary, producing near-duplicate
rungs (django `199 → 193 → 183` across three rungs) — the same truncation artifact
in a new coordinate.

Parent links come from `CONTAINS` and `DECLARES`, the only structural edge types
in the graph (`FILE→CONTAINS→{FUNCTION,CLASS}`, `CLASS→DECLARES→METHOD`,
`FUNCTION→CONTAINS→FUNCTION` for nested functions). The walk is cycle-guarded; a
node with no ancestor of the target type is its own group.

At each rung, per layer, the quotient graph is the set of distinct cross-group
edge pairs (self-loops dropped), and the census is `triad_census` over it —
the same function the drift layer already uses.

### Rung hygiene

Two filters, both required, both applied **per layer** (so the effective ladder
differs between IMPORTS and CALLS — this is expected and must be reported):

- **Identical-census dedup.** Consecutive rungs whose census vectors are equal
  collapse to one. This is not an optimization: `IMPORTS` edges connect only
  `FILE` nodes, so `T0`/`T1`/`T2` are literally the same import quotient
  (django: 107 863 triads at all three). Reporting them as three rungs would
  triple-count one observation in the fit.
- **`no_signal` rungs.** A rung with fewer than `_MIN_RUNG_TRIADS = 10` triads,
  or with a single group, is reported but excluded from the fit. Top rungs
  routinely degenerate this way (flask's first directory rung is 3 groups and
  1 triad). This constant is a data-sufficiency floor, not a verdict threshold:
  it decides whether a rung is *observed*, never what the answer is. Moving it
  changes which rungs are fitted, never which side of zero the slope lands on.

## Per-rung report

Per rung, per layer: group count, the normalized 13-triad census, Shannon
entropy `H` in bits (max `log₂ 13 ≈ 3.70`), the dominant motif and its share,
`tangle_ratio` (free — `tangle_mass` already exists), and the `no_signal` flag.

## The summary

Least-squares fit of `H` against `x = −log₂(groups)` over live, deduplicated
rungs, so `x` increases as the graph coarsens:

```
slope = dH / d log₂(groups⁻¹)      [bits per halving of group count]
```

The slope is **rung-count invariant by construction**: it normalizes by actual
collapse, not by rung index. Two repos with the same coarsening behaviour but
different directory depths get the same slope.

Reported alongside: `r_squared`, the number of live rungs, and the full per-rung
curve. The fit is a lossy summary of a non-linear curve (R² 0.71–0.86 on the
hierarchical cluster) — the curve is the evidence, the slope is the headline, and
the report must carry both.

### Verdict

Three-way, from the sign of the slope with a significance dead-band:

| condition | verdict | reading |
|-----------|---------|---------|
| `slope > 2·SE` | `hierarchical` | coarsening adds motif diversity — coupling, composition and cycles appear above the module |
| `\|slope\| ≤ 2·SE` | `scale_invariant` | the motif mix is the same at every scale |
| `slope < −2·SE` | `flat` | coarsening destroys diversity — nothing above the module, remnants degenerate to one motif |
| `< 3` live rungs | `no_signal` | not enough ladder to fit |

`SE` is the standard error of the fitted slope. **The sign split is measured; the
dead-band is proposed** — see acceptance criterion 3 for what happens if it
swallows the hierarchical cluster. Using the fit's own error rather
than an invented constant is deliberate: every thresholded verdict in this issue's
history has been falsified by re-measurement, and a dead-band derived from the
residuals cannot be tuned into a desired answer.

No band ever reaches `patterns.yaml`, no gate is touched, nothing is enforced.
This is an observe-only diagnostic.

## Measured baseline

CALLS layer, tier ladder, six repositories (2026-07-30):

| repo | slope (bits/halving) | R² | live rungs | verdict |
|------|---------------------|-----|------------|---------|
| sqlalchemy 2.x | +0.171 | 0.86 | 4 | hierarchical |
| cgis | +0.146 | 0.71 | 4 | hierarchical |
| django 6.x | +0.133 | 0.75 | 6 | hierarchical |
| owner-api | +0.123 | 0.73 | 6 | hierarchical |
| httpx 0.28 | −0.676 | 0.89 | 3 | flat |
| flask 3.1 | −1.020 | 0.97 | 3 | flat |

The split is by **sign**, and it separates repositories with a real package
hierarchy from flat single-package libraries — the same population split as the
original FLAT verdict, now expressed as a number with a natural zero.

IMPORTS slopes are all within ±0.03 with erratic R²: after dedup the import
ladder has too few distinct rungs to fit. Expect `no_signal` or
`scale_invariant` on IMPORTS for most repositories; that is a true statement
about the layer, not a defect.

## Where it lives

New module `src/cgis/query/drift/fractal.py` — it consumes `triads.py`
(`triad_census`, `normalized_census`, `tangle_mass`) and belongs to the same
lane. Nothing in `drift.py`, `fingerprint.py` or `quotient.py` changes; the
existing quotient path is untouched.

Public surface:

- `build_ladder(nodes, edges) -> list[Rung]` — grouping only, no censuses.
- `analyze_fractal(nodes, edges, layer) -> FractalReport` — ladder + per-rung
  metrics + fit + verdict.
- `FractalReport` / `RungReport` — frozen dataclasses, mirroring the
  `DriftReport` / `FitQuality` convention.

Surfaces, both append-only:

- CLI `cgis fractal <db>` — a new `@app.command()`, table per layer plus the
  summary line. Does not touch the `drift` command region.
- MCP `cgis_fractal(db_path, layer)` in `src/cgis/api/mcp_server.py`, appended
  after the existing tools, following the `cgis_drift` payload conventions.
  `MCP_REFERENCE` is regenerated by autodoc, never hand-edited.

## Error and edge handling

- Empty graph, or fewer than 3 nodes: `no_signal`, empty ladder, no exception.
- A node whose containment walk hits a cycle: the guard returns the node itself
  as its group.
- A file with no directory component: `<root>`.
- All rungs identical after dedup (a single flat package): one live rung →
  `no_signal`.
- `H` of an empty census is `None`, never `NaN` — matching `normalized_census`,
  which returns the zero vector for "no data".
- Degenerate fit (`Σ(x − x̄)² = 0`, all rungs the same size): `no_signal`.

## Testing

Unit (`tests/unit/`):

- Ladder construction on a hand-built graph: nested function → enclosing
  function → file; method → class → file; module-level function → file.
- Leaf-trim directory rungs on mixed-depth paths — every file moves at every
  rung (the regression test for the root-truncation artifact).
- Identical-census dedup: an IMPORTS-only graph yields one rung for T0/T1/T2.
- `no_signal` paths: empty graph, single group, fewer than 3 live rungs,
  degenerate fit.
- Slope sign on synthetic ladders: a constructed rising-diversity ladder gives
  `hierarchical`, a collapsing one gives `flat`, a constant one gives
  `scale_invariant`.

Self-parsing (`tests/self_parsing/`): cgis on itself reports `hierarchical` on
CALLS with slope in `[0.10, 0.20]` — a loose band, asserting the sign and order
of magnitude rather than pinning a value that ordinary refactors would break.

## Acceptance criteria

1. The six-repo sign split above reproduces.
2. **Rung-count invariance is demonstrated, not assumed:** truncating the ladder
   (dropping the top rung, dropping the bottom rung) must not flip any verdict.
   This is the test that deliverable 2 failed and the reason this one exists.
3. The three-way dead-band is validated: `SE` is computed for all six repos and
   the `hierarchical` / `flat` assignments above survive it. **The sign split is
   measured; the dead-band is proposed.** If `2·SE` swallows the +0.12…+0.17
   cluster, the verdict degrades to two-way (`sign` vs `no_signal`) and the
   `scale_invariant` band is dropped rather than re-tuned.

## Out of scope

- `closure-gap` (deliverable 2) — retired in #186 as unmeasurable without a
  canonical grain. Whether this ladder supplies that grain is a question for a
  future issue, not a promise made here.
- Any gate, threshold in `patterns.yaml`, or `hygiene_baseline` wiring.
- Cross-language comparison — the ladder is defined over `CONTAINS` / `DECLARES`,
  which any extractor emits, but only Python is validated here.
