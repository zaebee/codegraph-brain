# `tangle_ratio` hygiene gate — design (#186)

**Status:** approved (2026-06-13)
**Issue:** #186 (recursive-quotient / pattern-algebra macro track), deliverable 1 of 3
**Lane:** A (drift / ontology / query)

## Goal

Add a `tangle_ratio` hygiene invariant — the normalized mass of transpose-fixed
(mutual) triad motifs — as the symmetric partner of the existing `cycle_ratio`
gate, closing the **antisymmetry** half of the health predicate
`health = acyclicity + antisymmetry`.

## Motivation

The drift subsystem enforces **acyclicity** via the `cycle_ratio` gate (catches
`030C` back-edge loops). It does **not** enforce **antisymmetry**: a domain made
entirely of mutual coupling (`M ≥ 1` triads — `111*`, `120*`, `201`, `210`, `300`)
passes every gate today. The #186 9-repo census found `rider-web` is ~100% tangle
and sails through clean. That is an unnamed product gap.

Pattern algebra (validated on #186): the 13-triad space partitions under the
transpose operator `ᵀ`. **Patterns** are the antisymmetric part (`ᵀ` flips
`021D↔021U`, fixes acyclic `021C`/`030T`). **Anti-patterns** are the
transpose-*fixed* mutual mass — a dyad `A↔B` is its own transpose; `300` is the
maximal fixed point. Scoring that fixed mass `Σ M·triad` is the direct
operationalization of the antisymmetry half of the predicate.

## The metric

Per layer, from the normalized 13-triad census vector `t` (already carried on
`PatternFingerprint` as `t_imports` / `t_calls`):

```
tangle_mass(t) = ( Σ_i  M_i · t_i ) / 3
```

- `M_i` = the MAN first digit (mutual-dyad count) of triad `i`, aligned to
  `TRIAD_ORDER`:

  | M | triads |
  |---|--------|
  | 0 | `021D`, `021U`, `021C`, `030T`, `030C` |
  | 1 | `111D`, `111U`, `120D`, `120U`, `120C` |
  | 2 | `201`, `210` |
  | 3 | `300` |

- `÷3` normalizes to `[0, 1]` so thresholds read on the same scale as
  `cycle_ratio`.

Reference points: pure DAG → `0`; a single mutual dyad (`201`) → `≈0.67`;
pure mesh (`300`) → `1.0`. Healthy `030T` (transitive triangle, `M = 0`) does
**not** count — only mutual coupling does.

**Both layers** combine via the worst layer (a hard gate takes the worst case,
not a weighted distance):

```
tangle_ratio = max( tangle_mass(t_imports), tangle_mass(t_calls) )
```

## Where it lives (all append-only)

| File | Change |
|------|--------|
| `src/cgis/query/triads.py` | new pure `tangle_mass(census: tuple[float, ...]) -> float` + module-level `_TANGLE_WEIGHTS` aligned to `TRIAD_ORDER` |
| `src/cgis/query/fingerprint.py` | new field `tangle_ratio: float` on `PatternFingerprint`, computed at build time from `t_imports` / `t_calls` via `max(tangle_mass(...), tangle_mass(...))` |
| `src/cgis/query/drift.py` | add `"tangle_ratio"` to the hygiene-key list (≈ line 20); `_hygiene_check` reads `actual.tangle_ratio` automatically — no gate-logic change |
| `docs/ontology/patterns.yaml` | add `tangle_ratio: {max: <default>}` to the `hygiene:` block |
| `src/cgis/query/ontology_init.py` | mirror the same line in `_DEFAULT_ONTOLOGY_HEADER` (staleness pin-test keeps the two parse-identical) |
| `src/cgis/cli.py` | append-only `tangle` column in the drift table render |
| `src/cgis/api/mcp_server.py` | append-only `tangle_ratio` in the `cgis_drift` payload |

The `hygiene_baseline` ratchet (#151) works for free: `tangle_ratio` is a valid
hygiene key like any other, so acknowledged debt is declared per-domain via
`hygiene_baseline: {tangle_ratio: <relaxed>}`.

## Default threshold — empirical, not invented

The default `max` is chosen at plan time by **measuring** `tangle_ratio` on three
anchors, not picked a priori:

- **rider-web** (~1.0 mesh) — the gate MUST fire here.
- **cgis-self** (clean → micro) — the self-drift gate MUST stay green (the
  drift-rebaseline lesson: a refactor/feature that reddens the self-gate means
  re-negotiate the metric, not ship a red default).
- **owner-api** (~0.21 macro mass, moderately coupled) — the borderline that
  calibrates where "coupled-but-shipping" sits relative to the threshold.

Expected landing zone `0.15–0.25`, but the committed number is whatever the
measurement supports while keeping cgis-self green. If no single default
separates rider-web from cgis-self, fall back to a conservative default that
only fires on near-mesh and document the gap.

## Error / edge handling

- **Empty census** (`ZERO_TRIADS`, no data): `tangle_mass` returns `0.0` by
  construction (`Σ 0·M = 0`). A no-data domain is not tangled.
- **Hygiene-only domains** (no `expected_pattern`): already run the gate path;
  `tangle_ratio` applies to them too — desirable (mesh with no declared template
  should still fail).
- **Quotient level**: scored by the same `PatternFingerprint` + gate, so the
  project-level binding gets `tangle_ratio` automatically (cross-domain mutual
  coupling is exactly the macro-tangle the research flagged on owner-api).

## Testing

1. **`tangle_mass` unit table** (`tests/unit/test_triads.py` or new
   `test_tangle.py`): DAG-only census → `0.0`; pure `300` → `1.0`; pure `201`
   → `≈0.667`; pure `030T` → `0.0`; empty → `0.0`. Weight-alignment assertion:
   `_TANGLE_WEIGHTS` length == 13 and matches `TRIAD_ORDER` first-digit mapping.
2. **`PatternFingerprint.tangle_ratio`**: a synthetic mesh domain (mutual edges)
   yields high `tangle_ratio`; a synthetic DAG domain yields `0.0`; `max` picks
   the worse of imports/calls.
3. **Gate integration** (`tests/unit/test_drift*.py`): a mesh domain with
   `tangle_ratio` over the default → status `gate_failed`; a clean domain →
   `clean`; `hygiene_baseline: {tangle_ratio: ...}` relaxes a known breach back
   to `clean` (ratchet path).
4. **Staleness pin**: the existing patterns.yaml ↔ ontology_init header
   parse-identity test must still pass after adding the line to both.
5. **Self-drift safety**: cgis-self drift run stays green with the chosen
   default (asserted as part of threshold selection, re-checked in CI's
   self-parsing path if present).

## Out of scope (other #186 deliverables, separate PRs)

- `closure-gap` coupling metric (reachability saturation on the
  `[micro ↔ closure(micro)]` spectrum).
- `cgis_fractal` tool (entropy + collapse-ratio per structural tier, 2D verdict).
