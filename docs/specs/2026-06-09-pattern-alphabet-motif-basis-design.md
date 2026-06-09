# Unified Pattern Alphabet & Motif-Basis Fingerprint

**Status:** Draft — for review
**Date:** 2026-06-09
**Depends on:** PR #141 (intra-domain fan metrics, self-drift guardrails)
**Supersedes (partially):** the pattern vocabulary split introduced by `docs/ontology/patterns_ui.yaml`

---

## 1. Context and Motivation

PR #141 added self-drift guardrails for both the Python graph (`src/`) and the
TypeScript graph (`ui/src`). To make the UI domains scoreable, it introduced a
*second* pattern ontology — `patterns_ui.yaml` — with two new templates:
`leaf_module` and `shallow_dag`.

This was a mistake worth naming precisely: **ontological drift**. The pattern
vocabulary forked because the *measurement instrument* (the TS extractor
resolves far fewer CALLS edges than the Python one) is noisier — and that noise
leaked into the ontology:

- `leaf_module` is not a pattern. It asserts nothing about topology; it is
  "hygiene only" (acyclic, mostly resolved). The *absence* of a structural
  claim is not a structural claim.
- `shallow_dag` is `layered_dag` with a different parameter (`min_depth: 2`
  instead of `3`). If every new threshold spawns a new pattern, the alphabet is
  not closed and grows linearly with the number of domains.

### The core thesis

> The pattern alphabet is **single, closed, and small**. Domains differ in
> their **fingerprint** — the combination, counts, and wiring of patterns —
> not in vocabulary. Knowing a fingerprint, one can probabilistically infer
> the domain archetype; knowing a domain's ontological description, one can
> derive its ideal fingerprint.

Differences between the Python and TypeScript graphs are differences of
**measurement profile** (component weights, confidence discounts), never of
ontology.

This spec defines:

- **Part A** — unify the alphabet: parameterized templates + per-graph
  measurement profiles. Kills the `patterns_ui.yaml` fork. Small refactor, no
  new math.
- **Part B** — ground the fingerprint in a formal motif basis (directed triad
  census), move drift to a distance metric, and add quotient-level (fractal)
  drift.
- **Part C** — roadmap only: typed motifs (codons) on top of semantic uplift.

Part A is implementable without Part B. Part C is explicitly out of scope for
implementation and exists so reviewers see the full trajectory.

---

## 2. Part A — Unified Alphabet

### 2.1 The closed alphabet

One `patterns.yaml`, one `patterns:` section, five templates:

| Template | Claim | Triad signature (see Part B) |
|---|---|---|
| `pure_utility` | many depend on it, it depends on nothing | hub-dominant (021U) |
| `pipeline_stage` | sequential transformer | chain-dominant (021C) |
| `orchestrator` | one coordinator fans out to independents | star-dominant (021D) |
| `layered_dag` | acyclic layered imports | feed-forward-dominant (030T) |
| `dispatcher` | routes to first matching strategy | star without back-import |

`leaf_module` and `shallow_dag` are removed. Hygiene constraints
(`cycle_ratio`, `unresolved_ratio`) are **global invariants applied to every
domain**, not a template — a domain that wants "hygiene only" simply declares
no `expected_pattern` (topology components then contribute zero drift, exactly
like today's unconstrained components).

### 2.2 Parameterized templates

Parameters live in the **domain binding**, not in the alphabet:

```yaml
patterns:
  layered_dag:
    description: "Clean layered architecture; no upward dependencies"
    params:
      min_depth: 3          # default
    dag_depth:        {min: $min_depth}
    cycle_ratio:      {max: 0.0}

project_domains:
  - name: "components"
    fqn_prefix: "components"
    expected_pattern: layered_dag
    params: {min_depth: 2}   # JSX component trees are legitimately shallower
    drift_tolerance: 0.15
```

Resolution rule: `domain.params` overrides `template.params` defaults;
`$name` placeholders in constraint values are substituted before scoring.
A domain may only override parameters declared in the template's `params:`
block — unknown keys are a config error (fail loud, consistent with the
`DriftScorer` ValueError/TypeError discipline from PR #140).

### 2.3 Per-graph measurement profiles

```yaml
profiles:
  python:
    drift_weights:
      hub_count: 0.15, star_count: 0.15, chain_len: 0.10, dag_depth: 0.10,
      router_count: 0.10, cycle_ratio: 0.25, unresolved_ratio: 0.15
  typescript:
    drift_weights:
      hub_count: 0.10, star_count: 0.10, chain_len: 0.10, dag_depth: 0.15,
      router_count: 0.10, cycle_ratio: 0.30, unresolved_ratio: 0.15

project_domains:
  - name: "components"
    profile: typescript
    ...
```

(Weights shown inline for brevity; actual YAML uses nested mapping form.)

**Confidence discount.** `unresolved_ratio` stops being merely a scored
component and becomes a *discount on the confidence of CALLS-derived
components* (`hub_count`, `star_count`, `chain_len`, `router_count`):

```
effective_weight(c) = weight(c) * (1 - unresolved_ratio)   for c in CALLS-layer
```

Rationale: when 60% of calls are unresolved, the measured star/hub counts
describe the extractor, not the code. Today we compensate by hand-tuning UI
weights; the discount derives the same effect from measured data and removes
the temptation to fork ontologies. IMPORTS-derived components (`dag_depth`,
`cycle_ratio`) are not discounted — the import graph is reliable in both
extractors. Remaining weights are renormalized after the discount (same
renormalization rule `score()` already applies to unconstrained components).

**No discount floor.** At `unresolved_ratio = 1.0` the CALLS layer zeroes out
entirely: no data → no structural claim. A floor would inject a phantom
signal that masks extractor failures. The pathological case (a domain whose
drift looks artificially clean because its CALLS layer vanished) is already
caught by the `unresolved_ratio {max: X}` hard gate — the domain fails
hygiene regardless of the soft term.

### 2.4 File layout after A

- `docs/ontology/patterns.yaml` — alphabet (5 templates), profiles, and *all*
  project domains (both `cgis.*` and ui prefixes, each binding naming its
  profile).
- `docs/ontology/patterns_ui.yaml` — **deleted**.
- `tests/self_parsing/test_drift.py` — both graphs score against the single
  file; the `_PATTERNS_UI` constant disappears.

Migration note: UI tolerances re-measure after unification (expected to stay
0.00–0.15); Python tolerances keep their PR #141 ratchet values.

---

## 3. Part B — Motif Basis (fingerprint v2)

### 3.1 Theoretical grounding

For directed graphs there are exactly **13 connected three-node motifs**
(triads). Triad census — counting each class over all connected node triples —
is the standard structural fingerprint in network science; Milo et al. (2004)
showed that networks of similar *function* share near-identical Triad
Significance Profiles and cluster into "superfamilies". This is precisely the
fingerprint→domain inference direction of the core thesis.

Our current heuristic components are projections of five of those triads:

| Component | Triad | Shape |
|---|---|---|
| `star_count` | 021D | A←B→C (out-fork) |
| `hub_count` | 021U | A→B←C (in-join) |
| `chain_len` | 021C | A→B→C (path) |
| `dag_depth` | 030T | feed-forward triangle |
| `cycle_ratio` | 030C | 3-cycle (anti-pattern) |

The remaining 8 triads (mutual-edge and dense forms) are mostly smells in a
code graph; the census counts them for free and they sharpen the signature.

The hub/star pair illustrates why the alphabet is smaller than it looks: they
are **duals under edge reversal** (021U ↔ 021D). Chain is self-dual. The basis
has real structure; our v1 components were groping toward it.

### 3.2 Fingerprint v2 definition

```
fingerprint_v2(domain) = (T_imports, T_calls, hygiene)

T_imports ∈ R^13   normalized triad counts over intra-domain IMPORTS edges
T_calls   ∈ R^13   normalized triad counts over intra-domain CALLS edges
hygiene   = (cycle_ratio, unresolved_ratio)
```

Layering is mandatory, not cosmetic: IMPORTS is the *static* space (how the
structure is built), CALLS is the *behavioral* space (how control flows). The
two carry independent signals — and CALLS carries the Part C typed-codon
future (see §4.2). `T_calls` is discounted by `(1 - unresolved_ratio)` exactly
as in §2.3.

Normalization: counts divided by the number of connected triples in the
domain's subgraph (raw ratios). **No null-model z-scores**: our domains are
tens of nodes; randomized-baseline statistics at that size are noise. This is
an honest deviation from Milo's TSP, recorded as a known limitation.

### 3.3 Drift as distance

Each template names an **ideal point** in motif space (hand-authored unit
vectors, e.g. `pipeline_stage → T_imports ∝ e_021C`, `layered_dag → ∝ e_030T`).

The distance is **total variation (weighted L1) on the normalized triad
distributions**, not cosine:

```
tv(T, ideal)  = ½ · Σ_i  w_i · |T[i] − ideal[i]|        (i over 13 triads)

drift_v2 = w_imports · tv(T_imports, ideal_imports)
         + w_calls   · tv(T_calls,   ideal_calls) · (1 - unresolved_ratio)
         + hard-constraint violations (v1 mechanism, kept)
```

Why TV and not cosine or Jensen–Shannon (resolves former Open Question 2):

- **Cosine masks small components.** It is dominated by the large coordinates
  of the vector; a rare-but-critical triad (e.g. a few percent of 030C
  cycles) barely moves the angle. An angular metric also does not decompose
  per-component, which contradicts the violation-reporting requirement below.
- **JSD needs smoothing on sparse vectors.** Our 13-triad vectors are mostly
  zeros (small domains express 2–4 triad types); the log terms in JSD are
  undefined at 0 and force an arbitrary epsilon. TV handles zeros exactly.
- **TV is bounded [0, 1] and decomposes exactly**: each triad's contribution
  is `½·w_i·|T[i] − ideal[i]|`, so the violation strings fall directly out of
  the metric instead of being approximated from it.

Per-triad weights `w_i` default to 1 and live in the measurement profile
(§2.3) — e.g. the TypeScript profile may down-weight CALLS-only triads.
The known residual risk of TV is noise sensitivity in the long tail of the
13-triad distribution; if minor fluctuations in non-dominant triads produce
false positives, the fix is lowering `w_i` for those triads in the profile —
a tuning knob — not clipping the metric itself, which stays clean.

The v1 weighted-violation score survives only for **hard invariants**
(`cycle_ratio {max: 0}`, `unresolved_ratio {max: X}`) — these are gates, not
distances. Everything topological moves to the distance term. Violations
reporting stays: the per-triad terms with the largest contribution to the
TV sum become the human-readable violation strings.

### 3.4 Fractal / quotient drift

**Claim: the alphabet is minimal iff it is closed under coarsening.** Collapse
each domain to a single node (quotient graph: nodes = domains, edges =
aggregated cross-domain IMPORTS/CALLS, weight = edge count). If the alphabet
is right, the quotient graph is described in the *same* vocabulary — no new
patterns at scale k+1:

```
k=0  modules inside a domain      → chain / star / hub / ffwd / cycle
k=1  domains inside the project   → cgis ≅ pipeline_stage
                                    (extraction → resolution → storage)
```

Mechanism: run the **same** `FingerprintExtractor` + `DriftScorer` on the
quotient graph against a `project_level` binding in `patterns.yaml`:

```yaml
project_level:
  expected_pattern: pipeline_stage
  drift_tolerance: 0.15
  enforce: false   # observe-only for the first milestone
```

**Observe-only first** (resolves former Open Question 3): the quotient drift
is computed and reported in test output but not asserted for one milestone —
a burn-in period to calibrate `drift_tolerance` against real noise before it
can break CI. Once a baseline exists, `enforce: true` flips it into the same
ratchet discipline as per-domain tolerances (and the value may then only
decrease).

The result is a **fractal drift profile** — a drift score per scale. It
catches the failure mode invisible to per-domain drift: clean modules, chaotic
inter-domain wiring ("well-organized mess"). At k=1 this formalizes and
eventually replaces the hand-maintained edge assertions in
`tests/self_parsing/test_architecture.py`.

Honest terminology: this is a **self-similar hierarchy** (3–4 scales:
function → module → domain → service), not a Mandelbrot fractal. The testable
statement is *invariance of motif statistics under the quotient operation*,
validated empirically on the self-graph — not assumed.

### 3.5 `ideal.graph.json ↔ self-desc.graph.json`

The ideal graph generated from the ontology (`scripts/gen_ideal_graph.py`) and
the measured self-graph are compared **by per-domain fingerprints and the
quotient fingerprint — never edge-by-edge**. Edge-level diffing is brittle
(every rename breaks it) and answers the wrong question; fingerprint distance
answers "is the *shape* still the declared shape".

### 3.6 Known limitations (recorded, not hidden)

1. **Small-N statistics.** Domains of 20–60 nodes ⇒ raw normalized counts
   only; no significance testing. Tolerances absorb the variance (ratchet
   discipline from PR #141 continues to apply).
2. **TS resolver quality.** Until TS CALLS resolution improves, `T_calls` for
   UI domains is mostly discounted away. That is correct behavior: the system
   says "I cannot see", instead of hallucinating topology. Improving the TS
   extractor is the highest-leverage prerequisite for Part B on UI.
3. **Triad census cost.** O(Σ deg(v)²) per domain — negligible at our scale
   (<10⁴ nodes), noted for future large-repo use.

---

## 4. Part C — Roadmap: Typed Motifs (Codons)

*No implementation in this spec. Recorded so the trajectory is reviewable.*

Primary sources (read in full):
[The Tale of the Enchanted Apiary](https://zaebee.github.io/agents/article.html)
and the Hive
[Sacred Codon Pattern Library](https://github.com/zaebee/hive/blob/main/docs/appendix_b_sacred_codon_pattern_library.md).

### 4.1 The key structural fact

The five Sacred Codons over the ATCG role alphabet (A=Aggregate,
T=Transformation, C=Connector, G=Genesis Event):

| Codon | Shape | Typing |
|---|---|---|
| C→A→G (handle command) | chain-3 | Connector→Aggregate→Event |
| C→T→C (query) | chain-3 | Connector→Transform→Connector |
| G→C→A→G (react to event) | chain-4 | Event→Connector→Aggregate→Event |
| G→C→A→C (immune response) | chain-4 | Event→Connector→Aggregate→Connector |
| Choreography | composition | sequence of codons + compensation |

**All codons are topologically near-identical (short chains). The entire
signal is in node typing.** An untyped L2 fingerprint cannot distinguish them
even in principle. Hence the phasing A+B → C is forced, not preferential:
typed detection requires `ontology_class` on nodes, i.e. semantic uplift
(issue #47).

### 4.2 What C adds when uplift lands

- **Typed motif** = L2 shape × `ontology_class` of participants. Codon
  detection = typed chain matching in the CALLS layer (the codons are
  behavioral; this is why §3.2 keeps the CALLS layer separate).
- **Chemical bond rules as edge constraints.** Hive Appendix B already
  specifies `validate_atcg_bonds` (A–G bonds, C–A bonds, T-purity, no toxic
  combinations) — these are typed edge predicates. cgis with uplift becomes
  the missing Genesis Engine validator. Library anti-patterns are
  graph-detectable: "C→A without G" = a chain terminating at an Aggregate
  with no event edge; "side effect in C→T→C" = a write/PERSISTS edge on a
  T-node.
- **Ideal local subgraphs from declarative definitions.** The article's
  WorkerBee YAML (`listens_to` / `produces` / `connectors`) *is* a generator
  of an ideal typed subgraph per component — the per-component refinement of
  `ideal.graph.json`.
- **Probabilistic domain classification**: nearest-centroid over fingerprint
  vectors (the fingerprint→domain direction of the core thesis).
- The metamorphosis lifecycle (egg→larva→pupa→adult) maps onto the ratchet:
  a domain matures as its `drift_tolerance` ratchets down.

---

## 5. Phasing & Non-Goals

| Phase | Content | Gate |
|---|---|---|
| A | unified alphabet, params, profiles, confidence discount | this spec approved |
| B | triad census, distance drift, quotient drift | A merged; self-drift stays green |
| C | typed codons, bond rules, classification | semantic uplift (#47) merged |

Non-goals (explicitly rejected for now):

- Role discovery / blockmodeling (RolX etc.) — automatic decomposition is
  research territory; the closed alphabet + declared bindings is our
  decomposition.
- Null-model significance testing (§3.6.1).
- Edge-by-edge ideal-graph diffing (§3.5).
- Any new pattern template motivated by extractor noise — that is the
  ontological drift this spec exists to prevent.

## 6. Open Questions — all resolved in review (PR #142)

1. ~~§2.3: confidence discount floor vs. full zero-out~~ — **resolved: no
   floor, zero out**. A floor injects a phantom signal masking extractor
   failures; the pathological fully-unresolved domain is caught by the
   `unresolved_ratio` hard gate anyway. See §2.3.
2. ~~§3.3: cosine vs. Jensen–Shannon for the distance~~ — **resolved:
   weighted total variation (L1)** on the normalized triad distributions.
   Cosine masks small components and does not decompose per-triad; JSD needs
   epsilon-smoothing on our sparse vectors. Long-tail noise is tuned via
   per-triad `w_i`, not by clipping the metric. See §3.3.
3. ~~§3.4: quotient drift hard gate vs. observe-only~~ — **resolved:
   observe-only for one milestone** (`enforce: false`), burn-in to calibrate
   tolerance against noise, then flips into the ratchet. See §3.4.
4. ~~§2.4: single `patterns.yaml` vs. split binding files~~ — **resolved:
   single file**. A "thin" secondary file recreates the drift surface this
   spec exists to kill; atomicity of the ontology is a feature. See §2.4.
