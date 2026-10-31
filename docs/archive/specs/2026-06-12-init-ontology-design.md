# `cgis init-ontology`: Auto-Proposed patterns.yaml (#174)

**Date:** 2026-06-12
**Issue:** #174 — measure-then-label starter ontology
**Status:** Draft
**Roadmap:** #179 P0 (adoption blocker; pairs with the merged #178 trust guard)
**Lane:** A (drift/ontology) — new module in `query/`, append-only CLI/MCP additions

## 1. Problem

`cgis_drift` is the flagship feature with the steepest onboarding cost: it is a
no-op until someone hand-writes `patterns.yaml`. Hand-labeling also produced
real mistakes during dogfooding (`httpx._api`/`_decoders` mislabeled → false
criticals; `click.core` prefix mis-target → the #178 story). The engine
already computes everything steps 2-4 of the manual process need — it should
propose the file.

## 2. Design

### 2.1 New module: `src/cgis/query/ontology_init.py`

Module-level functions only (no class — nothing to feed the God-Object
detector):

```python
def discover_domains(nodes: list[Node], depth: int | None = None) -> list[str]:
    """Candidate domain prefixes from node FQNs.

    Auto-descend: starting at the FQN roots, walk down while a level has a
    single child; the first level with >= 2 children yields the candidates
    (src.click -> [src.click.core, src.click.parser, ...]). ``depth`` (segment
    count) overrides auto-descent when given. Sorted, deduplicated.
    """


def propose_ontology(
    db_path: str,
    margin: float = 0.03,
    min_nodes: int = 10,
    depth: int | None = None,
) -> str:
    """Return a ready-to-edit patterns.yaml as text (measure-then-label)."""
```

`propose_ontology` flow:

1. Validate `db_path` is an existing file FIRST (`FileNotFoundError` with the
   "run ingest first" wording, mirroring `analyze_drift`) — SQLite would
   otherwise silently create an empty db on open (gemini catch on the spec
   PR). Then open the store and run `discover_domains` over all node ids
   (exclude virtual nodes: `file_path == VIRTUAL_FILE_PATH`).
2. Per candidate, `FingerprintExtractor.extract(prefix)` (one shared
   extractor — its `_loaded()` cache makes N extractions cheap).
3. **Profile detection** per domain: majority file extension among the
   domain's nodes — `.py` → `python`, `.ts`/`.tsx`/`.js`/`.jsx`/`.vue` →
   `typescript`; tie/unknown → `python` with a `# profile guessed` comment.
4. **Fit** (approach A — DriftScorer IS the fitter): for each of the five
   bundled templates, score the fingerprint with a synthetic
   `DomainConfig(expected_pattern=t, profile=<detected>, drift_tolerance=1.0)`
   against a `DriftScorer` loaded from the bundled default ontology; fit =
   `report.drift_score`. Zero new fitting math — layer weights, empty-layer
   exclusion, and the unresolved-ratio discount are inherited from
   `_score_v2`, so proposed numbers are by construction comparable to later
   `cgis drift` runs.
5. **Binding decision** per domain:
   - `node_count < min_nodes` → hygiene-only entry,
     `# below min_nodes (<N> nodes) — census too small to label` comment.
   - `edge_count == 0` → hygiene-only,
     `# no intra-domain edges — nothing to fit` (the #178 `no_signal` shape).
   - best fit `> 0.5` (`_NO_FIT_THRESHOLD`) → hygiene-only,
     `# no template fits (best: <name> at <fit>)` — the honest branch
     (#177-aligned; avoids the httpx mislabel trap).
   - otherwise → `expected_pattern: <nearest>` with
     `drift_tolerance: <measured + margin>` (ceil to 2 decimals) and comment
     `# measured ≈ <fit> via init-ontology (runner-up: <second> at <fit2>) — ratchet down over time`.
   - Hygiene-only entries ALSO get `drift_tolerance: <hygiene-score + margin>`
     (their v1 hygiene measurement — the bundled hygiene block applies to
     every domain, so a domain with cycles must not break the round-trip
     guarantee).
6. Assemble the YAML: bundled header (profiles + patterns + hygiene blocks)
   + generated `project_domains:`. No `project_level:` section — quotient
   binding needs a human architectural decision; a commented-out skeleton
   with a pointer to the docs is emitted instead.

### 2.2 Bundled default ontology

A module-level string constant `_DEFAULT_ONTOLOGY_HEADER` in
`ontology_init.py`: the `version`, `profiles` (python + typescript),
`patterns` (5 templates with their `ideal` points) and `hygiene` blocks,
copied from `docs/ontology/patterns.yaml` WITHOUT the cgis-specific
`project_domains`/`project_level` sections. String constant, not package
data — no packaging changes, importable everywhere, parsed by the same
`DriftScorer` for fitting (written to a temp file or loaded via the scorer's
existing constructor contract — the implementation may add a
`DriftScorer.from_text` classmethod ONLY if the constructor strictly
requires a path; prefer reusing the path-based constructor with a
`tempfile`, keeping `DriftScorer`'s surface unchanged — it is in
`_KNOWN_GOD_OBJECTS` and need not grow).

Staleness note: the constant duplicates the template definitions. A unit
test pins equality of the `patterns:` block between the constant and
`docs/ontology/patterns.yaml` so the two cannot drift apart silently.

### 2.3 CLI: `cgis init-ontology` (append-only in cli.py)

```
cgis init-ontology --db graph.db --out patterns.yaml \
    --margin 0.03 --min-nodes 10 [--depth N] [--force]
```

- Refuses to overwrite an existing `--out` without `--force` (exit 1, red
  message) — never silently clobber a hand-tuned ontology.
- Missing db → exit 1 with the "run ingest first" message (mirror `drift`).
- On success: writes the file, prints a Rich summary table (domain, nodes,
  proposed pattern or hygiene-reason, tolerance) + next-step hint
  (`cgis drift --db ... --patterns <out>`).

### 2.4 MCP: `cgis_init_ontology` (append-only in mcp_server.py)

```python
def cgis_init_ontology(
    db_path: str = _DEFAULT_DB,
    margin: float = 0.03,
    min_nodes: int = 10,
    depth: int | None = None,
) -> str:
```

Returns the proposed YAML **text** — deliberately read-only: no file writes
from the MCP surface (avoids the S2083 path-write taint class entirely; the
agent decides where to save). Missing `db_path` → the `❌ Database not found
… Run cgis_ingest first.` error string, mirroring `cgis_drift` (the
`propose_ontology` FileNotFoundError is translated at this surface).
Docstring tells the agent to save it and run `cgis_drift` with it.

### 2.5 Determinism

Output is fully deterministic for a given graph: domains sorted, template
iteration in yaml declaration order, ties in fitting broken by template
name. Needed both for tests and for diff-able re-runs on CI.

## 3. Safety

- **Read-only over the graph**: no store writes, no census/ratchet
  implications, `docs/ontology/patterns.yaml` untouched.
- The generated file is INPUT for drift, never auto-applied to cgis's own
  ontology.
- Lane A: new module in `query/`, append-only blocks in `cli.py` /
  `mcp_server.py` (coordination contract with the parallel session).

## 4. Testing

1. **discover_domains unit tests**: auto-descent (single-root src-layout),
   multi-root, depth override, virtual-node exclusion, sorted/dedup.
2. **propose_ontology decision-matrix tests** (synthetic store fixtures):
   below-min_nodes → hygiene comment; edge_count==0 → hygiene; forced
   no-fit (a census far from all ideals) → hygiene + "no template fits";
   good fit → expected_pattern + tolerance == measured+margin (ceil 2dp);
   runner-up named in comment; profile detection per extension; determinism
   (two runs byte-identical).
3. **Round-trip acceptance (the killer test)**: build a fixture graph →
   `propose_ontology` → write yaml → `analyze_drift(db, yaml)` →
   assert NO `empty` anywhere, NO `no_signal` on LABELED domains (hygiene-only
   entries may legitimately be no_signal — the gate ignores it), NO domain
   above its proposed tolerance, `any_critical is False` **by construction**. Then the same round-trip on the real
   cgis self-graph in `tests/self_parsing/` (ingest src → propose → drift →
   all proposed domains within their proposed tolerances).
4. **Header-staleness pin**: `patterns:` block of the bundled constant ==
   the one in `docs/ontology/patterns.yaml` (parsed-yaml comparison, not
   string equality).
5. **CLI**: refuses overwrite without --force; --force overwrites; summary
   rendering; exit codes. **MCP**: returns parseable yaml containing
   `project_domains:`; no file side-effects.
6. Gates: `make format && make lint && make type-check && make pytest &&
   make doc-coverage`.

## 5. Acceptance (from #174, restated)

- [ ] `cgis init-ontology` on a fresh repo (db only) produces a
      `patterns.yaml` that `cgis drift` accepts and passes green.
- [ ] Nearest template + runner-up + measured fit reported per domain.
- [ ] `drift_tolerance = measured + margin`, hygiene-only below `min_nodes`.
- [ ] Honest no-fit branch (threshold 0.5) — no forced labels.
- [ ] MCP tool returns the yaml text read-only.
- [ ] mypy strict, ruff, interrogate ≥90%; ratchets untouched.

## 6. Out of scope

- `project_level` (quotient) auto-binding — human decision; commented
  skeleton only.
- New templates / template learning (#177's residual-driven "alphabet
  completeness" work).
- Structural-tier domain discovery (#186's tier ladder) — prefix depth is
  the natural unit for `DomainConfig` binding; revisit if #186 graduates.
- Auto-refresh / ratchet automation (#151), incremental ingest (#175).
- Multi-graph (python+typescript in one db) profile splitting beyond the
  per-domain majority-extension heuristic.

## Amendment 1 (2026-06-12): summary table columns

§2.3's summary table listed a "nodes" column; the proposed YAML deliberately
carries no node_count field (it is drift INPUT, not a report), so the CLI
table shows name / fqn_prefix / pattern-or-hygiene / tolerance. Node counts
remain visible in the per-domain comments (`# below min_nodes (N nodes)`)
and in any subsequent `cgis drift` run. Review finding M5, accepted as a
display simplification.
