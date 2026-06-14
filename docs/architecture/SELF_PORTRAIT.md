# cgis Self-Portrait

cgis ingests its own `src/` and runs every analysis on the resulting graph — the
engine measuring itself. This page is the canonical dogfood snapshot: the
**commands are the contract**; the numbers are a point-in-time reading (post
RFC #242 slice 2, the `query/` subpackage split) and will drift as the code does.

## Reproduce

```bash
uv run cgis ingest src --output self.db          # ~1.2k nodes, ~3.8k edges

uv run cgis metrics        --db self.db --limit 12          # bottlenecks · god classes · PageRank
uv run cgis drift          --db self.db                     # per-domain architectural drift
uv run cgis suggest-packages cgis.query --db self.db        # cohesion / sub-package boundaries
uv run cgis audit cgis.storage.sqlite_store.SQLiteStore \
    --from-prefix cgis.cli --from-type FUNCTION --db self.db # reachability to the store
```

Ingest the **repo root's `src/`** (not `src/cgis/`) so FQNs carry the `cgis.`
prefix — the domain bindings in `docs/ontology/patterns.yaml` expect it.

## What the engine sees in itself

### 🏛️ God classes (declared members)

| Class | Members | Note |
|---|---:|---|
| `cgis.storage.sqlite_store.SQLiteStore` | 37 | data-access layer — many CRUD methods by design (sanctioned baseline) |
| `cgis.query.drift.drift.DriftScorer` | 30 | drift scoring — constraint/weight/param operations (sanctioned baseline) |
| `cgis.guardian.collector.ContextCollector` | 11 | review context assembly |
| `cgis.extractors.typescript_extractor.TypeScriptExtractor` | 10 | AST visitor |
| `cgis.query.render.metrics.DuckDBAnalyzer` | 9 | analytical layer |
| `cgis.resolver.symbols.SymbolResolver` | 9 | symbol indexing |

The two large classes are the documented `_KNOWN_GOD_OBJECTS` baseline
(`tests/self_parsing/test_architecture.py`) — the slice-2 restructure introduced
no new ones.

### ⭐ Critical nodes (PageRank — transitive importance)

| Node | PageRank | In / Out | Reading |
|---|---:|---|---|
| `cgis.extractors._python_ast.get_identifier` | 0.0455 | 17 / 2 | the real critical path — both the most-coupled and the most-important node; if it breaks, extraction breaks |
| `cgis.core.models.Edge` | 0.0128 | 18 / 0 | foundation — a stable sink everything depends on |
| `cgis.core.models.Node` | 0.0105 | 10 / 0 | foundation |
| `cgis.storage.sqlite_store.SQLiteStore` | 0.0077 | 24 / 0 | foundation |

The high-in / zero-out nodes (`Edge`, `Node`, `SQLiteStore`) are stable
abstractions on the right side of the main sequence — the base the rest builds on.

### 🔌 Coupling

- **Hubs (fan-in):** `get_identifier` (17), `resolve_fqn` (8 — the #145 suffix
  resolver, reused for slice-1's root-agnostic edges and widely depended on).
- **Orchestrators (fan-out):** `cgis.guardian.chunked.run_chunked_review` (18),
  `cgis.query.analysis.suggest_service.suggest_packages` (16),
  `cgis.query.drift.drift_service.analyze_drift` (15),
  `cgis.query.drift.drift.DriftScorer.score` (12) — top-level coordinators, high
  out-degree by nature.

### 📐 Shape — architectural drift

`cgis.query` scores **drift = 0.1863** against its declared pattern (tolerance
0.20) — a `warning` band, within tolerance. The slice-2 subpackage split did not
push it over; no re-baseline was needed. (`tests/self_parsing/test_drift.py` is
the live ratchet.)

### 🧩 Cohesion — `suggest-packages` on its own graph

The package-cohesion tool dogfooded on the package it was built for:

| Package | Q | divergence | verdict |
|---|---:|---:|---|
| `cgis.query` (post-split) | 0.475 | **0.31** | split |
| `cgis.query.drift` (a new subpackage) | 0.099 | — | **leave** |

Before slice 2, `cgis.query` was flat and read `divergence = 1.0`. The
restructure dropped it to **0.31** — the directory layout now largely projects
the dependency graph. The residual is deliberate: the chosen by-responsibility
grouping (`drift`/`analysis`/`context`/`render`) is coarser than the tool's
finer by-connectivity communities (RFC §4: the algorithm suggests, the human
groups). Each new subpackage is internally cohesive — `cgis.query.drift` reads
`leave` (Q = 0.099), confirming a sound boundary rather than an arbitrary cut.

### 🔒 Reachability — `audit`

`audit` finds sources that never reach a checkpoint (built for authz/IDOR gaps,
#172). cgis has no authz, so as an architectural probe: *do CLI commands reach
the storage layer?*

**35 `cgis.cli.*` sources → `SQLiteStore`: 13 covered, 22 gaps.** Most gaps are
expected — pure renderers (`_render_*`, `build_*_tree`), entry points (`main`,
`app`, `_version_callback`), and the guardian-stats commands that read JSONL, not
the graph. One gap is a genuine architectural fact rather than noise:
**`cgis.cli.metrics` does not reach `SQLiteStore`** — the `metrics` command opens
the SQLite file directly through `DuckDBAnalyzer`, bypassing the store
abstraction. That is exactly the kind of checkpoint-bypassing path the audit
primitive exists to surface.

## Synthesis

cgis reads as a cleanly layered engine: a stable foundation
(`models.Edge/Node`, `SQLiteStore`) → primitives → orchestrators
(`suggest_packages`, `analyze_drift`). Two sanctioned god-objects, one true
critical node worth guarding (`get_identifier`), one honest store-bypass
(`metrics` → DuckDB), and a `query/` package whose directory structure — after
slice 2 — now projects its own dependency graph. Every lens reflects the
restructure consistently, with no new smells introduced.
