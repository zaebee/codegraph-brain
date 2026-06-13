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
prefix → cohesion.build_file_graph (collapse node→file via Node.file_path, aggregate
IMPORTS [+CALLS], undirected weighted) → greedy_modularity → Q + communities →
partition_divergence vs current sub-directory layout → verdict → SuggestReport
(frozen dataclass) → CLI/MCP serialize`.

**Reuse:** `_in_domain` (prefix matching) from `fingerprint.py`; the node→unit collapse
idea from `quotient.py` (here collapsing to file granularity, not domain); `Node.file_path`
to derive the current-directory partition. The CNM modularity pass is net-new.

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

### Verdict — gated on BOTH signals

`Q` says "the structure is real"; `D` says "the layout disagrees with it". Both are
required to recommend a split.

| condition | verdict | meaning |
|---|---|---|
| `file_count < 2` or `edge_count == 0` | `no_signal` | nothing to measure |
| `Q < 0.25` | `leave` | no real community structure — flat is fine (e.g. `owner-api/utils`, Q=0) |
| `Q ≥ 0.25` and `D < 0.2` | `aligned` | structure exists and directories already express it |
| `Q ≥ 0.35` and `D ≥ 0.2` | `split` | real communities + layout diverges → split (`cgis.query`: Q=0.43, D=1.0) |
| `0.25 ≤ Q < 0.35` and `D ≥ 0.2` | `borderline` | inspect the bridge edges by hand |

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
Text output (Rich): verdict line (`✂️ SPLIT` / `✅ ALIGNED` / `· LEAVE` / `◌ no signal`),
`Q` + `divergence`, a community table (id → files), a bridges table (source, target,
weight). JSON output via the same enum pattern as `DriftOutputFormat`.

## MCP

`cgis_suggest_packages(db, prefix, with_calls=False)` returns the same JSON as
`--format json` — one service call, mirror of `cgis_drift`.

## JSON contract (shared by CLI `--format json` and MCP)

```json
{
  "package": "cgis.query", "layer": "imports", "file_count": 19, "edge_count": 19,
  "modularity_q": 0.434, "divergence": 1.0, "verdict": "split",
  "communities": [
    {"id": 0, "files": ["drift", "fingerprint", "triads", "quotient", "_scc"]}
  ],
  "bridges": [{"source": "engine", "target": "drift", "weight": 2}],
  "thresholds": {"split": 0.35, "leave": 0.25, "divergence": 0.2}
}
```

`layer` is `"imports"` or `"imports+calls"`. File names in `communities`/`bridges` are
the last FQN segment (the module name) for readability.

## Error / edge handling

- **Missing db** → error string (mirrors `cgis_drift`'s `FileNotFoundError` guard).
- **Prefix matches no files** → `no_signal` verdict with a note; no did-you-mean
  suggestion (YAGNI for slice 1).
- **Single file / no intra-package edges** → `no_signal` (`file_count < 2` or
  `edge_count == 0`).
- **One community detected** (whole graph is one blob) → `P_comm` is trivial; D vs a
  flat `P_dir` is 0 → not `split`.

## Testing

1. **`cohesion.py` units (synthetic graphs, known Q):** two disconnected cliques →
   Q ≈ 0.5, one community per clique; a single clique → Q ≈ 0, one community; CNM
   determinism (same graph, two runs → identical partition).
2. **`partition_divergence`:** flat `P_dir` vs multi-community → D = 1.0; aligned →
   D = 0.0; NMI symmetry; both-trivial → D = 0.
3. **`suggest_service` + MCP:** `SQLiteStore` fixture; verdict and JSON-shape assertions
   (in the spirit of `test_cgis_drift_*`).
4. **Self-parsing dogfood** (`tests/self_parsing/`): run `suggest-packages` on
   `cgis.query` in the self-parsed graph → assert `verdict == "split"`, `Q` in the band
   ~0.40–0.46, `divergence` high. The tool catching the exact smell it was built for is
   the canonical validation (the self-drift analogue for drift).

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
