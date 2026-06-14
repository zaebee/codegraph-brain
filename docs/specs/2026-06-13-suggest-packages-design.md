# `cgis suggest-packages` — design (RFC #242, slice 1)

**Status:** approved (2026-06-13)
**Issue:** #242 (package-cohesion analysis — graph-driven sub-package suggestions)
**Lane:** A (drift / ontology / query)
**Slice:** 1 of 3 (the analysis as a feature; slice 0 = go/no-go decided "build feature first"; slice 2 = `query/` restructure)

## Goal

A new analysis — `cgis suggest-packages <prefix>` — that, for any package in the
ingested graph, detects its dependency communities, measures how far the current
directory layout diverges from those communities, and emits a data-backed verdict
(`split` / `borderline` / `aligned` / `leave` / `no_signal`). Report-only: it
**describes and scores**, it does not move files (`--apply` is out of scope).

## Motivation

`src/cgis/query/` grew to 19 flat files spanning ≥4 subsystems. A throwaway spike
(AST-import graph + hand-rolled Clauset-Newman-Moore modularity) measured
**modularity Q = 0.434** — strong community structure flattened into one directory.
Cross-validation across 9 packages / 5 codebases (cgis, owner-api×6, rich, requests,
django, fastapi) showed Q discriminates **structure, not size**: `owner-api/utils`
(11 files) scores Q=0.000 while `django/utils` (42 files) scores Q=0.456 — same name,
opposite verdict. Only `cgis.query` (0.434) and `django/utils` (0.456) crossed Q≳0.35.

The smell is real, measurable, and the dependency graph itself proposes the fix.
This RFC generalizes the spike into a product capability: the principle is *directory
structure should be a projection of the dependency graph*; this tool quantifies the
divergence. It is the same machinery as the domain quotient (`quotient.py`), one scale
down — community detection over the intra-package import graph instead of the
cross-domain quotient graph.

## Architecture

Follows the `drift` split: pure logic separate from db-reading orchestration.

```
src/cgis/query/cohesion.py          pure logic, no I/O
  build_file_graph(nodes, edges, prefix, with_calls) -> FileGraph
  greedy_modularity(graph)          -> (communities, Q)   # hand-rolled CNM, no NetworkX
  partition_divergence(p_comm, p_dir) -> float            # 1 - NMI, in [0, 1]

src/cgis/query/suggest_service.py   orchestration (mirror of drift_service.py)
  suggest_packages(db, prefix, with_calls) -> SuggestReport

src/cgis/cli.py                     @app.command() suggest-packages (Rich + --format json)
src/cgis/api/mcp_server.py          cgis_suggest_packages (same service -> JSON)
```

**Data flow:** `graph.db → suggest_service reads FILE/MODULE nodes + edges under
prefix → cohesion.build_file_graph (collapse node→file, reconcile cross-rooted targets
by FQN suffix, aggregate IMPORTS [+CALLS], undirected weighted) → greedy_modularity → Q +
communities → partition_divergence vs current sub-directory layout → verdict →
SuggestReport (frozen dataclass) → CLI/MCP serialize`.

**Root-agnostic edge resolution (load-bearing).** The intra-package import graph must
not depend on how the user ingested. Verified empirically: ingesting `src/` gives file
nodes `cgis.query.*` and 19 internal IMPORTS edges that resolve file-to-file; ingesting
`src/cgis/` gives file nodes `query.*` while the **same** import edges target the literal
written path `cgis.query.*` (separate EXTERNAL placeholder nodes) — so a naive
`node→file via Node.file_path` collapse finds **zero** internal edges and silently reports
`no_signal` on the exact package the tool exists for. `build_file_graph` therefore
reconciles each import target to a file under `prefix` by **FQN suffix** (reusing #145's
`resolve_fqn` / `SQLiteStore.find_nodes_by_suffix`): exact-first, then a unique
dot-boundary suffix match; an ambiguous suffix is skipped (never guessed). With this, the
two ingest roots produce the same graph and the same verdict.

**Reuse:** `_in_domain` (prefix matching) from `fingerprint.py`; the node→unit collapse
idea from `quotient.py` (here collapsing to file granularity, not domain); `resolve_fqn` /
`find_nodes_by_suffix` (#145) for root-agnostic target reconciliation; `Node.file_path` /
FQN segmentation to derive the current-directory partition. The CNM modularity pass is
net-new.

## The metric

### Modularity Q

Standard Newman modularity on the **undirected, weighted** file graph:

```
Q = (1 / 2m) · Σ_ij [ A_ij − k_i·k_j / 2m ] · δ(c_i, c_j)
```

where `A` is the symmetric file-adjacency (edge weight = aggregated import [+call]
count between two files), `k_i` the weighted degree of file `i`, `m` the total edge
weight, and `c_i` file `i`'s community.

**Greedy modularity (Clauset-Newman-Moore):** start each file in its own community,
repeatedly merge the pair of communities yielding the maximum positive `ΔQ`, stop when
no merge improves `Q`. Tie-break on sorted file-id pairs so the result is deterministic.
Pure Python, no NetworkX (consistent with the tricode census, which only uses NetworkX
in cross-validation tests).

### Divergence D = 1 − NMI(P_comm, P_dir)

Two partitions of the same file set:
- `P_comm` — the communities detected from the graph.
- `P_dir` — files grouped by their **containing sub-directory relative to the package
  root**. Concretely: strip `prefix` from a file's FQN and split the remainder by `.`;
  a single remaining segment (e.g. `cgis.query.drift` under prefix `cgis.query`) means the
  file lives directly in the package root → the shared `"<root>"` group, while two or more
  remaining segments (e.g. `cgis.query.drift.fingerprint`) means the file lives in
  sub-directory `drift`. A flat package therefore yields one `"<root>"` group for every
  file.

`D = 1 − NMI(P_comm, P_dir)`, in `[0, 1]`, where NMI is normalized mutual information.

**Why NMI, not a "misplaced-files fraction":** for a flat package `P_dir` is a single
group, so a misplacement count would be 0 (every file is "in" the one directory) — a
false green. NMI against the trivial 1-cluster partition is 0, so **D = 1.0**: "the
graph wants structure the directory does not express" — exactly the smell. A
perfectly-aligned layout gives NMI = 1 → D = 0.

NMI normalization: `NMI = I(X;Y) / mean(H(X), H(Y))`, with `NMI = 1.0` by definition
when both partitions have zero entropy (single cluster on both sides → nothing to split,
D = 0).

**Isolated files.** A file with no intra-package import (a pure leaf — e.g. `fqn`,
`graph_json`, `mermaid`, `metrics` in `cgis.query`) has weighted degree 0. It is a
**singleton community** in `P_comm`, is counted in `file_count`, and is a normal member of
`P_dir`. Isolated nodes contribute 0 to `Q` (their `k_i·k_j` null-model term is 0), so they
neither inflate nor depress modularity; they do enter the NMI/`D` computation as singletons.

**Flat-package degeneracy (important).** For a flat package `P_dir` is the single
`"<root>"` group, so `D = 1.0` for *any* non-trivial `P_comm` — `D` carries no
discriminating signal there and the verdict is driven by `Q` alone. The `divergence`
threshold only does work for **already-nested** packages, where it separates `aligned`
(dirs match communities) from `split`/`borderline` (nested but mis-grouped).

**Threshold calibration.** `Q`'s thresholds (`0.35`/`0.25`) are cross-validated across 9
packages / 5 codebases. The `divergence=0.2` threshold is **provisional**: every high-`Q`
case measured so far is flat (`D=1.0` by construction), so it is not calibrated on a real
nested package. We checked the in-repo nested candidate `cgis.guardian` (over its
`providers/` split) and it does **not** exercise the boundary: measured `Q≈0.33, D≈0.76 →
borderline` — it lands non-split because `Q < 0.35`, NOT because of low divergence. cgis
has no genuinely well-aligned nested package in-repo, so the `aligned` (low-`D`) side of
the `0.2` boundary is covered **end-to-end by a synthetic test** (a nested-and-aligned
package → `D≈0 → aligned`) rather than by a real one. The threshold therefore remains
**provisional on real-world data** until a low-`D` nested package (e.g. in owner-api) is
measured — the same "measure before fixing the threshold" honesty applied to the
`tangle_ratio` gate. The self-parsing guardian test pins the real `Q/D/verdict` numbers
and states this gap explicitly rather than implying a calibration it doesn't perform.

### Verdict — gated on BOTH signals, with direction

`Q` says "the structure is real"; `D` says "the layout disagrees with it". Both are
required to recommend a change. `D = 1 − NMI` is **symmetric**, so a high `D` alone does
not say *which way* the layout is wrong: a package can be **under-split** (the graph wants
more grouping than the directories express — `cgis.query`) or **over-split** (directories
are finer than the communities — files that should merge). `split` must not be emitted for
the over-split case. We disambiguate cheaply by comparing partition sizes:

```
direction = under_split  if |P_dir| < |P_comm|
            over_split   if |P_dir| > |P_comm|
            matched      otherwise
```

For a flat package `|P_dir| = 1 ≤ |P_comm|`, so `split` is always the right direction.

| condition | verdict | meaning |
|---|---|---|
| `file_count < 2` or `edge_count == 0` | `no_signal` | nothing to measure |
| `Q < 0.25` | `leave` | no real community structure — flat is fine (e.g. `owner-api/utils`, Q=0) |
| `Q ≥ 0.25` and `D < 0.2` | `aligned` | structure exists and directories already express it |
| `Q ≥ 0.35`, `D ≥ 0.2`, `direction = under_split` | `split` | real communities + flatter layout → split (`cgis.query`: Q=0.43, D=1.0) |
| `Q ≥ 0.35`, `D ≥ 0.2`, `direction = over_split` | `consolidate` | finer dirs than communities → merge (rare; bridges show what to fold) |
| `Q ≥ 0.35`, `D ≥ 0.2`, `direction = matched` | `borderline` | same group count, different membership → re-group; inspect bridges |
| `0.25 ≤ Q < 0.35` and `D ≥ 0.2` | `borderline` | weak structure — inspect the bridge edges by hand |

`direction` is also surfaced in the JSON contract so an agent can act on it without
re-deriving partition sizes.

Thresholds default from the cross-validation (`split=0.35`, `leave=0.25`, `divergence=0.2`).
`--min-q` overrides the split threshold.

### Bridges

Edges between distinct communities — the cost of splitting: `[(source, target, weight)]`
sorted by weight descending. For `cgis.query` these are `engine → {drift, analysis}` —
shared core that becomes a cross-package import after a split.

## Graph layer

Default **IMPORTS only** (always resolved via the import map; the validated spike signal).
`--with-calls` switches to the combined graph (IMPORTS ∪ CALLS, both collapsed to file
level). The cgis-native dogfood found imports ≈ calls ≈ combined for `query/`
(Q=0.453/0.441/0.447), so imports is the reliable default; `--with-calls` is the escape
hatch for packages where import-coupling and call-coupling diverge. A calls-only mode is
deliberately omitted (calls are sparse and partly unresolved — low-value).

## CLI surface

```
cgis suggest-packages <fqn_prefix> \
    [--db graph.db] [--with-calls] [--format text|json] [--min-q 0.35]
```

Operates on the ingested db (consistent with `trace` / `impact` / `structure` / `drift`).
Text output (Rich): verdict line (`✂️ SPLIT` / `🔗 CONSOLIDATE` / `✅ ALIGNED` / `· LEAVE`
/ `◌ no signal`), `Q` + `divergence` + `direction`, a community table (id → files), a
bridges table (source, target, weight). JSON output via the same enum pattern as
`DriftOutputFormat`.

## MCP

`cgis_suggest_packages(db, prefix, with_calls=False)` returns the same JSON as
`--format json` — one service call, mirror of `cgis_drift`.

## JSON contract (shared by CLI `--format json` and MCP)

```json
{
  "package": "cgis.query", "layer": "imports", "file_count": 19, "edge_count": 19,
  "modularity_q": 0.434, "divergence": 1.0, "direction": "under_split", "verdict": "split",
  "communities": [
    {"id": 0, "files": ["drift", "fingerprint", "triads", "quotient", "_scc"]}
  ],
  "bridges": [{"source": "engine", "target": "drift", "weight": 2}],
  "thresholds": {"split": 0.35, "leave": 0.25, "divergence": 0.2}
}
```

`layer` is `"imports"` or `"imports+calls"`. `verdict` is one of `split` / `consolidate` /
`aligned` / `leave` / `no_signal`; `direction` is `under_split` / `over_split` / `matched`.
File names in `communities`/`bridges` are the last FQN segment (the module name) for
readability.

## Error / edge handling

- **Missing db** → error string (mirrors `cgis_drift`'s `FileNotFoundError` guard).
- **Prefix matches no files** → `no_signal` verdict with a note; no did-you-mean
  suggestion (YAGNI for slice 1).
- **Files exist but zero internal edges — distinguish two causes (no silent failure,
  per #182):**
  - if some import edges from under-`prefix` files exist but *none* reconciled to a file
    under `prefix` → emit `no_signal` with the diagnostic note *"N import edges found but
    none resolve inside the package — the graph looks mis-rooted or imports are
    unresolved; try ingesting the package's parent directory"*. This is the empirically
    observed `src/cgis`-root case, and must never read as a clean "nothing to split".
  - if there are genuinely no intra-package import edges (a real flat leaf bag) →
    `no_signal` plain.
- **Single file** (`file_count < 2`) → `no_signal`.
- **One community detected** (whole graph is one blob) → `P_comm` is trivial; D vs a
  flat `P_dir` is 0 → not `split` (`leave`, since Q ≈ 0).

## Testing

1. **`cohesion.py` units (synthetic graphs, known Q):** two disconnected cliques →
   Q ≈ 0.5, one community per clique; a single clique → Q ≈ 0, one community; isolated
   node → singleton community, Q unchanged; CNM determinism (same graph, two runs →
   identical partition).
2. **`partition_divergence`:** flat `P_dir` vs multi-community → D = 1.0; aligned →
   D = 0.0; NMI symmetry; both-trivial → D = 0.
3. **`direction`:** `|P_dir| < |P_comm|` → `under_split`; `>` → `over_split`; `=` →
   `matched`; over-split + high-D → verdict `consolidate`, not `split`.
4. **Root-agnostic reconciliation (the 🔴 regression guard):** ingest the same fixture
   under both `src/` and `src/cgis/` roots → `suggest-packages` yields the **same verdict
   and same Q** (suffix reconciliation closes the gap); a deliberately mis-rooted db with
   unresolvable targets → `no_signal` with the mis-rooted diagnostic note, never a clean
   verdict.
5. **`suggest_service` + MCP:** `SQLiteStore` fixture; verdict and JSON-shape assertions
   (in the spirit of `test_cgis_drift_*`), including the `direction` field.
6. **Divergence-boundary (`aligned`, low-`D`) coverage:** a synthetic nested-and-aligned
   package — two cliques whose directories already match the communities → `D≈0`,
   `direction=matched` → `aligned`. This exercises the `0.2` boundary end-to-end (the real
   nested in-repo case, `cgis.guardian`, does NOT: it reads `borderline` on low `Q≈0.33`,
   with `D≈0.76` still high — pinned in the self-parsing test with an honesty note).
7. **Self-parsing dogfood** (`tests/self_parsing/`): run `suggest-packages` on
   `cgis.query` in the self-parsed graph → assert `verdict == "split"`, `Q` in a wide band
   (~0.38–0.52; measured `0.475` and creeping up as `query/` grows — slice 2 resets this
   domain), `divergence == 1.0`. The tool catching the exact smell it was built for is the
   canonical validation (the self-drift analogue for drift).

## Known cost (RFC risk #2 — recorded explicitly)

Adding `cohesion.py` + `suggest_service.py` to `query/` grows the `cgis.query` census
domain, which already sits at the edge (drift 0.18). The plan includes a self-drift run;
if the new files push `cgis.query` past tolerance, **consciously re-baseline**
`patterns.yaml` (precedents #174 / #161 / #199) rather than bending the metric. The
restructure that resolves this (slice 2) re-baselines the domains anyway.

## Out of scope (slice 1)

- `--apply` scaffolding / `git mv` automation (slice 2 does the restructure by hand,
  with an honest import-site update — no re-export shims, per #182 laundering risk).
- A suggested sub-package **naming** map (`drift/`, `analysis/`…) — the RFC keeps naming
  a human judgment ("the algorithm suggests boundaries, it doesn't dictate them"); slice 1
  reports communities by id + member files only.
- Louvain or calls-only layers, did-you-mean on bad prefixes, recursive multi-package
  sweeps.

**Known approximation:** greedy CNM with the "stop when no merge improves Q" rule finds a
**local** modularity optimum, not the globally optimal dendrogram cut. This is an accepted
slice-1 heuristic (it reproduced the spike's answer on `cgis.query` via an independent run),
documented here so it is a stated assumption, not a silent one; Louvain / multi-resolution
refinement is deferred.
