# 🔺 Patterns & Triads — the architecture alphabet

How CGIS turns a dependency graph into an *architectural verdict*. This is the
human- and AI-readable companion to two terse sources:

- `src/cgis/query/triads.py` — the 13-triad census (academic MAN notation)
- `docs/ontology/patterns.yaml` — the 5-pattern alphabet (one-line + ideal vectors)

Read this first; reach for those when you need the exact table or weights.

> **The one-sentence model.** Every domain is fingerprinted as a *distribution
> over 13 local connection shapes (triads)*; an architectural **pattern** is an
> ideal distribution; **drift** is the distance between the two. Topology is
> measured, never asserted.

Throughout, an edge **`A → B` means "A depends on B"** (A imports or calls B).

---

## Part 1 — The 13-triad census

A **triad** is any 3 nodes and the directed edges among them. The 3 dyads (node
pairs) are each **N**ull, **A**symmetric (one arc), or **M**utual (both arcs),
which collapses the possibilities to 16 isomorphism classes — the **MAN**
census. Three classes are disconnected (`003`, `012`, `102`) and dropped; CGIS
counts the **13 connected** classes, in this canonical order:

```
021D  021U  021C  111D  111U  030T  030C  201  120D  120U  120C  210  300
```

The 3-digit prefix = (#Mutual, #Asymmetric, #Null) dyads. The letter
disambiguates the shape: **D**own (fan-out), **U**p (fan-in), **C**yclic/Chain,
**T**ransitive.

### The shapes that matter most (2- and 3-edge)

| Triad | Picture (`→` = depends on) | What it means in code | Smell? |
| :--- | :--- | :--- | :--- |
| **021D** | `A ← B → C` | **Fan-out / divergence.** One node depends on two independent others — a *coordinator* pulling in collaborators. | ✅ healthy |
| **021U** | `A → B ← C` | **Fan-in / convergence.** Two nodes depend on one shared other — a *shared utility / sink*. | ✅ healthy |
| **021C** | `A → B → C` | **Chain / hand-off.** Linear pipeline; A flows through B to C, no shortcut. | ✅ healthy |
| **030T** | `A → B → C`, `A → C` | **Transitive triangle.** Layering *with a shortcut* — an upper node reaches a lower one both directly and through the middle. The real signature of layered DAGs. | ✅ (depth) |
| **030C** | `A → B → C → A` | **Cycle.** A circular dependency among three nodes. | 🔴 **real smell** |

These five carry most of the signal in real graphs. `021D/U/C` are the building
blocks of the 5 patterns; `030T` dominates deep/layered macro-graphs; `030C` is
the cyclic-dependency flag.

### The denser tail (mutual dyads → coupling)

A **mutual** dyad is `A ↔ B` (A and B depend on *each other*). The remaining 8
triads all contain at least one mutual dyad, so they only appear where code is
**reciprocally coupled**. They're grouped here by density rather than drawn
individually — their *presence and total mass* is the signal ("how tangled is
this?"), not the exact variant.

| Family | Triads | Reading |
| :--- | :--- | :--- |
| One mutual + one arc | `111D`, `111U` | A reciprocal pair with one outsider attached. |
| Two mutual dyads | `201` | Two reciprocal pairs sharing a node. |
| One mutual + two arcs | `120D`, `120U`, `120C` | A reciprocal pair plus a third node wired in (down/up/through). |
| Two mutual + one arc | `210` | Almost a clique — one arc short of total. |
| Complete | `300` | **Fully tangled** — all three depend on each other. |

A macro-graph whose census mass sits in this tail (e.g. a frontend where
`store ↔ components ↔ hooks`) is genuinely **meshed**, not layered — a signal no
fan-out/fan-in/chain template can represent (see Part 3 and issue #186).

### How it's computed

`triad_census(node_ids, edges, edge_type)` walks every connected triple, encodes
its 6 possible edges as a 6-bit **Batagelj–Mrvar tricode**, and looks up the MAN
class in a 64-entry table (`_TRICODES`). `normalized_census(...)` turns the
counts into a 13-vector summing to 1 — the domain's **fingerprint** for one edge
layer. The census is computed *per edge type*: separately over `IMPORTS` and
over `CALLS`.

---

## Part 2 — The 5-pattern alphabet

A **pattern** is a *closed alphabet* of ideal fingerprints — hand-authored
points in 13-triad space, one distribution per edge layer. A domain is labeled
by whichever pattern its measured census is closest to (TV distance, Part 3).
Counting thresholds are gone: shape is a *distance*, not a rule.

| Pattern | Ideal motif | Plain English | Example domain |
| :--- | :--- | :--- | :--- |
| **`pure_utility`** | `021U` = 1.0 | *Everyone depends on it; it depends on nothing.* A shared library, all fan-in. | `storage` |
| **`pipeline_stage`** | `021C` = 1.0 | *Sequential transformer* — one input, one output, linear hand-off. | `resolver` (engine → symbols → indices) |
| **`orchestrator`** | `021D` = 1.0 | *Coordinates N independent services*; fan-out from one coordinator, no leaf-to-leaf edges. | `pipeline` (fans out to extractors/resolver/storage) |
| **`dispatcher`** | `021D` = 1.0 | *Routes to the first matching strategy*; mutually exclusive paths. Topologically identical to `orchestrator` — distinguished by **intent**, not shape. | `cli` |
| **`layered_dag`** | `021D` = 0.5, `021C` = 0.5 + `dag_depth ≥ 3` | *Clean layered architecture*; fan-out **and** chains, no upward edges. Triads are local, so depth is enforced by a separate `dag_depth` gate. | `query`, `extractors` |
| **`funnel`** | `021U` = 0.5, `021C` = 0.5 | *Convergent aggregation* — fan-in **and** chains, the edge-transpose of `layered_dag`. Many inputs funnel through staged sinks. | `crud`, `http clients`, `composables` |

Three honest notes baked into the alphabet:

- **`funnel` = transpose(`layered_dag`)** (`021D`⇄`021U`, `021C` self-dual). Added
  per #186 research: across 9 repos it is the single *most common* intra-domain
  archetype (best-fit tally `funnel 39 > layered_dag 28 > pure_utility 27`) — the
  one the hand-authored alphabet missed. Its presence keeps the closed alphabet
  **transpose-closed** and stops fit-quality (#177) from flagging convergence
  layers as "no template fits".
- **`orchestrator` ≡ `dispatcher` topologically** (both `021D`=1.0). The census
  can't tell a coordinator from a router — only a human's `expected_pattern`
  choice can. That's a feature: the label records *intent*; drift checks the
  *shape* is consistent with it.
- **`layered_dag` is specified on `021C` (chains)** but real layered code emits
  heavy `030T` (transitive triangles — see Part 1). This mismatch is a known
  open question for the macro scale (#186).

Each pattern may also carry **non-triad gates** that triads can't express:
`layered_dag` requires `dag_depth ≥ min_depth`; `pure_utility` tightens
`unresolved_ratio ≤ 0.1`. These are hard constraints, evaluated alongside the
distance.

---

## Part 3 — How drift reads (census → verdict)

A domain's **drift score** combines, per the active profile's weights
(`profiles:` in `patterns.yaml`):

1. **`imports` layer** — TV distance between the measured IMPORTS census and the
   pattern's `imports` ideal.
2. **`calls` layer** — same over CALLS, discounted by `(1 − unresolved_ratio)`
   (a domain whose calls are mostly unresolved gets its calls-evidence faded).
3. **`gates` layer** — hygiene invariants (`cycle_ratio`, `unresolved_ratio`)
   and template constraints (`dag_depth`, …).

**Total-variation (TV) distance** between two 13-vectors `p`, `q` is
`½ · Σ wᵢ · |pᵢ − qᵢ|` — where `wᵢ` are per-triad weights from the profile's
`triad_weights` (default `1.0`, i.e. plain TV ∈ [0, 1]; raising a weight makes a
particular motif count for more). 0 = identical shape, 1 = no overlap. Layers
with zero connected triples are excluded and the rest renormalize.

```
measured census  ──TV──►  nearest pattern ideal  ──►  drift_score ∈ [0,1]
                                                         │
                        clean ── <0.20 ── warning ── <0.50 ── critical
```

The score is bucketed into a **status** (`clean`/`warning`/`critical` — today
the bands are the absolute `_STATUS_WARNING=0.20` / `_STATUS_CRITICAL=0.50`
constants in `query/drift.py`); the per-domain **`drift_tolerance`** is the
separate *gate* threshold that decides whether an enforced domain fails the run.
(Making status banding tolerance-relative and the gate uniformly
enforce-aware is the subject of #170 / #216.) **Hygiene breaches** — e.g. a
`030C` cycle surfacing as `cycle_ratio > 0` — gate independently of the TV
score. See `docs/ontology/patterns.yaml` for the live bindings.

### The recursive view (quotient)

The same census + scorer runs one scale up: collapse each domain to a single
node, aggregate cross-domain IMPORTS/CALLS, and fingerprint the **k=1 quotient
graph** against a `project_level` pattern. Closure under coarsening is the point
— a project whose macro-shape is `021D`-heavy is an *orchestrator of domains*;
one heavy in the mutual tail is a *tangle*. This is the lens behind the
fractality experiment (**#186**), where the finding is that the macro scale is
dominated by `030T` + the mutual tail — motifs the current 5-pattern alphabet,
built on `021*`, cannot yet name.

---

## See also

- `src/cgis/query/triads.py` — census implementation + the tricode table
- `docs/ontology/patterns.yaml` — the live pattern alphabet and domain bindings
- `docs/specs/2026-06-09-pattern-alphabet-motif-basis-design.md` — why a motif basis
- `docs/architecture/ONTOLOGY.md` — the L1–L3 node/edge type schema
- issue **#186** — recursive-quotient / fractality; macro-scale alphabet gaps
