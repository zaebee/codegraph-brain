# Drift Gate Semantics v2: Intra-Domain Cycles, gate_failed, Tolerance Precedence (#176 + #170)

**Date:** 2026-06-12
**Issues:** #176 (cycle_ratio false criticals on flat repos) + #170 (status semantics) — one spec: both change what the gate means, and the cycle fix interacts with both #170 halves and with #174's round-trip guarantee.
**Status:** Draft
**Roadmap:** #179 P1 ("make the gate correct across topologies")
**Lane:** A (drift/ontology): `query/{fingerprint,drift,drift_service,health}.py`, `query/ontology_init.py`, `docs/ontology/patterns.yaml`, drift regions of `cli.py`/`mcp_server.py`.

## 1. Problem — and a corrected diagnosis

### 1.1 #176, what is actually happening

Issue #176 frames the httpx wall-of-criticals as "intra-module call cycles".
That diagnosis is wrong: **CALLS edges never enter the cycle computation.**
`HealthScorer._compute_cycles` (health.py) runs Tarjan SCC over IMPORTS edges
between FILE/MODULE nodes only, then `enrich()` marks EVERY descendant node
of a cycle-participating file with `in_cycle=True`, and
`FingerprintExtractor` computes `cycle_ratio = in_cycle nodes / domain
nodes`.

So the real mechanism on httpx: its modules genuinely import each other in
file-level cycles (plausibly TYPE_CHECKING-guarded — see §7); a single-file
domain whose one file touches ANY such cycle gets 100% of its nodes marked →
`cycle_ratio 1.00`. The metric measures the **blast radius of file-level
import cycles, denominated per domain** — a granularity artifact, not call
cycles. The issue's proposed `call_cycle_ratio` would gate a thing the
metric has never measured.

### 1.2 #170, two gate gaps

A. A hygiene breach (`cycle_ratio 0.07 > max 0.0`) lands in `violations` but
`status` stays `warning` because the TV score is low — an invariant breach
is dilutable by good topology.

B. `any_critical` and the CLI status label compare `drift_score >=
max_drift` (global), ignoring the per-domain `drift_tolerance` — so raising
a domain's tolerance above the global cap silently does nothing.

### 1.3 The #174 interaction (found during this design)

A naive "hygiene breach forces failure regardless of tolerance" breaks the
#174 round-trip guarantee: `init-ontology` on any repo with one intra-domain
cycle would produce a baseline that immediately fails. The resolution (§2.2)
turns hygiene gates into **acknowledgeable ratchets**.

## 2. Design

### 2.1 #176: cycle_ratio becomes intra-domain (fingerprint.py)

`FingerprintExtractor.extract(prefix)` computes its own cycle set instead of
consuming the global `in_cycle` metadata:

1. `domain_files` = FILE/MODULE nodes within the prefix (non-virtual).
2. Intra-domain import subgraph: IMPORTS edges whose source AND target are
   in `domain_files`.
3. Tarjan SCC (reuse the existing `tarjan_scc` helper health.py uses);
   cyclic files = members of SCCs with len > 1.
4. `cycle_count` = domain nodes whose `file_path` equals a cyclic file's
   `file_path`; `cycle_ratio = cycle_count / len(domain_nodes)` (unchanged
   denominator semantics — still blast radius, now of the domain's OWN
   cycles).

Consequences, pinned:

- Single-file domain → intra subgraph has ≤1 file → ratio 0 by construction.
  The httpx wall (8/10 false criticals) disappears with no configuration.
- Multi-file intra-package cycles (owner-api `app.services` 0.075) are
  caught exactly as before.
- **Cross-domain cycles are the quotient layer's job**: the k=1 quotient
  graph already renders them as 030C motifs and the project_level binding
  scores them. The spec pins this routing as the principle — no new
  per-domain field for "participates in someone else's cycle" (YAGNI).
- `HealthScorer`'s global `in_cycle` enrichment is UNTOUCHED (other
  consumers: health reporting, analyzer). Only the fingerprint's
  `cycle_ratio` source changes.

### 2.2 #170A: `gate_failed` status + acknowledgeable hygiene baselines

`DriftReport.status` gains `"gate_failed"` (alongside the #178 statuses):

```python
status: Literal["clean", "warning", "critical", "gate_failed", "empty", "no_signal"]
```

**Hygiene provenance**: `DriftScorer.score()` currently merges
`{**hygiene, **template}` into one constraint dict. It now tracks which
constraint KEYS came from the hygiene block; a violation of a
hygiene-provenance constraint forces `status="gate_failed"` regardless of
the TV score (the score is still computed and reported — only the status is
sticky). Template-constraint violations keep today's score-driven
classification.

**Acknowledgeable debt** — the #174-interaction fix. A domain binding may
declare:

```yaml
  - name: "services"
    fqn_prefix: "app.services"
    expected_pattern: layered_dag
    drift_tolerance: 0.30
    hygiene_baseline:
      cycle_ratio: 0.08   # acknowledged debt — values may only go DOWN
```

Semantics: the effective bound resolution is OPERATOR-AWARE (gemini catch
on the spec PR — a baseline must only ever RELAX the global bound):
`max`-constraints → `max(global, baseline)`; `min`-constraints →
`min(global, baseline)`; `exact`-constraints → the baseline overrides.
(Today's hygiene block carries only `max` constraints, but the resolution
rule must not bake that in.) Breach = measured violates the effective
bound → `gate_failed`. NEW debt beyond the
acknowledged level fails absolutely; staying at-or-under the acknowledged
level passes the gate (the violation list still notes
`cycle_ratio 0.07 acknowledged (baseline 0.08)` for visibility).
`hygiene_baseline` is a ratchet by convention: lowering it is the only
sanctioned direction (same social contract as `drift_tolerance`; #151's
future CI enforcement applies to both).

`DomainConfig` gains `hygiene_baseline: dict[str, float] =
field(default_factory=dict)`; the loader validates the block is a mapping
via the existing `_validate_mapping` helper (project parsing rule) before
parsing it, for project_domains and project_level alike.

**init-ontology integration** (`ontology_init.py`): when a measured domain
breaches a default hygiene bound (today: any intra-domain `cycle_ratio > 0`,
`unresolved_ratio` over the hygiene max), the proposal emits a
`hygiene_baseline` block with the measured value and the comment
`# acknowledged at baseline by init-ontology — ratchet down over time`.
This restores the #174 round-trip guarantee under gate_failed: a freshly
proposed ontology never fails its own graph, while any REGRESSION beyond
the baseline fails loudly.

**Gate**: `gate_failed` on an enforced binding → `any_critical = True`;
`enforce: false` stays observe-only (#178 consistency).

### 2.3 #170B: per-domain tolerance precedence

- `DomainConfig.drift_tolerance` becomes OPTIONAL (`float | None = None`);
  `_build_domain_config` parses it null-safely
  (`float(d["drift_tolerance"]) if d.get("drift_tolerance") is not None
  else None`) — the current unconditional `float(d[...])` would KeyError on
  omission.
- Effective tolerance per domain = `drift_tolerance if not None else
  max_drift` (the CLI/MCP `max_drift` argument is demoted to "default
  tolerance for domains that don't declare one").
- Status classification becomes RELATIVE to the effective tolerance:
  - `critical`: `score > tolerance_eff`
  - `warning`: `score > 0.75 * tolerance_eff`
  - `clean`: otherwise
  (replacing the absolute `_STATUS_WARNING`/`_STATUS_CRITICAL` constants;
  `gate_failed`/`empty`/`no_signal` take precedence over all three).
- `any_critical` = any ENFORCED binding with status in
  `{"critical", "gate_failed", "empty"}` — the score-vs-max_drift term
  disappears; per-domain ratchets now actually bind. (This also resolves
  the #178-era "score term is enforce-blind" deferral: the new term
  respects `enforce` uniformly.)
- ⚠️ BREAKING semantics, documented loudly: `--max-drift` no longer caps
  domains that declare their own tolerance; the CLI/MCP help text and
  `patterns.yaml` header comment are updated. The observed #170B repro
  (crud 0.527 < tolerance 0.55 flagged critical) becomes clean.

### 2.4 Surfaces

- CLI `_drift_status_label`: `gate_failed` → `[bold red]⛔ gate failed[/bold red]`;
  the score-based branches now receive the domain's effective tolerance
  instead of global `max_drift`. The quotient print line gains the same.
- MCP: no schema work (`dataclasses.asdict` serializes the new status and
  `hygiene_baseline` automatically).
- `_render_drift_table` unchanged otherwise; violations column/notes
  already surface the acknowledged-debt annotations.

### 2.5 Effects on cgis's own gates (measure, don't assume)

- cgis domains are believed acyclic intra-domain → `cycle_ratio` stays 0,
  fingerprints unchanged, ratchets untouched. A dedicated early plan task
  re-measures after §2.1 lands.
- The relative warning band changes which self-graph domains render
  "warning" vs "clean" in CLI output — cosmetic; the ratchet test compares
  score > tolerance directly and is unaffected.
- Existing `test_drift.py` status assertions that encode the OLD absolute
  `_classify` thresholds will be updated to the new relative semantics —
  this is the semantic change the spec owns, not test-weakening. The
  fixtures' `DomainConfig.drift_tolerance` values make expected statuses
  deterministic.

## 3. Testing

1. **Intra-domain cycles** (`test_fingerprint.py`): single-file domain in a
   global cross-domain cycle → ratio 0; two-file intra-domain cycle →
   ratio counts both files' nodes; mixed domain (one cyclic pair + clean
   files) → partial ratio; cross-domain-only cycle → 0 for both domains
   but visible in the quotient fixture (extend the existing quotient test
   with an assertion that the 030C signal survives there).
2. **gate_failed** (`test_drift.py`): hygiene breach + low TV → gate_failed
   (not warning); template-constraint violation alone → score-driven
   status; acknowledged baseline at/under → NOT gate_failed but violation
   note present; over baseline → gate_failed; baseline only relaxes
   (effective bound = max(global, baseline)).
3. **Precedence** (`test_drift.py` + `test_drift_service.py`): score under
   per-domain tolerance but over max_drift → clean/warning, gate green
   (the #170B repro); domain WITHOUT tolerance falls back to max_drift;
   relative warning band at 0.75×; enforced gate_failed trips
   `any_critical`, observe-only doesn't.
4. **init-ontology round-trip under gate_failed**
   (`test_ontology_init.py`): cyclic-domain fixture → proposal contains
   `hygiene_baseline` with measured value → `analyze_drift` on the proposal
   → no gate_failed, `any_critical is False`. The #174 self-graph
   round-trip test keeps passing unchanged.
5. **Self-parsing**: full suite + ratchets; re-measure drift table (expect
   byte-identical scores — cycle_ratio 0 everywhere today).
6. Gates: `make format && make lint && make type-check && make pytest &&
   make doc-coverage`.

## 4. Acceptance

- [ ] httpx-shaped fixture (single-file domains, cross-file cycles): zero
      cycle-driven criticals; the one genuine topological outlier still
      flagged by TV score.
- [ ] owner-api-shaped fixture (intra-package cycle): `gate_failed`, listed
      violation, `any_critical=True` — undilutable by low TV.
- [ ] #170B repro green: per-domain tolerance binds over max_drift.
- [ ] init-ontology proposal on a cyclic graph round-trips green with an
      acknowledged baseline.
- [ ] Ratchets untouched; mypy strict / ruff / interrogate ≥90%.

## 5. Out of scope

- TYPE_CHECKING-guarded imports creating phantom runtime-safe cycles —
  extractor-side (Lane B); a note is filed on the issue tracker rather than
  handled here.
- `call_cycle_ratio` — explicitly NOT built: the spec corrects #176's
  diagnosis; call cycles have never been part of the metric and gating them
  is unmotivated by any observed failure.
- #151 CI enforcement of ratchet directions (now covering
  `hygiene_baseline` too — noted there).
- #177 fit-quality reporting; quotient rebind (#146).
- Changing `HealthScorer`'s global cycle enrichment or its consumers.
