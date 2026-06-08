# ⚙️ How CGIS Works: The 3-Pass Compiler Pipeline

## Overview

CGIS processes a source tree in three sequential phases — **Extract → Resolve → Store** — producing a deterministic, queryable semantic graph. Each phase is stateless relative to the previous: the extractor knows nothing about resolution, and the resolver knows nothing about storage.

```
Source Files
    │
    ▼
[Phase 1: Extract]  ─── tree-sitter AST ──▶  raw Nodes + raw Edges (raw_call:name)
    │
    ▼
[Phase 2: Resolve]  ─── FQN disambiguation ▶  resolved Edges (module.Class.method)
    │
    ▼
[Phase 3: Store]    ─── SQLite WAL mode  ──▶  graph.db  (queryable via BFS)
    │
    ▼
[Phase 4: Uplift]   ─── semantic tagging ──▶  ontology_class + domain[] fields
```

---

## Phase 1 — AST Extraction

**Entry point:** `BaseExtractor.parse(code, file_path) → (nodes, edges)`

Each language extractor walks the tree-sitter AST and emits:

- **`Node`** for every structural symbol: `FILE`, `MODULE`, `CLASS`, `FUNCTION`, `METHOD`, `VARIABLE`, `IMPORT`
- **`CONTAINS` / `DECLARES` edges** for structural containment (file→class→method)
- **`CALLS` edges** with `target = "raw_call:<name>"` for every call site

The FQN of each node is derived deterministically from its file path:

```
src/cgis/pipeline.py  →  src.cgis.pipeline
src/cgis/__init__.py  →  src.cgis          (strips __init__ suffix)
```

A class `IngestionPipeline` in that file gets the FQN `src.cgis.pipeline.IngestionPipeline`, and its method `run` becomes `src.cgis.pipeline.IngestionPipeline.run`.

---

## Phase 2 — Symbol Resolution

**Entry point:** `ResolverEngine.resolve() → (resolved_edges, virtual_nodes)`

The resolver operates on the complete set of raw edges emitted by Phase 1. It builds two indexes:

| Index | Purpose |
| :--- | :--- |
| `_global_symbols` | `name → [FQN, ...]` — all symbols across the whole repo |
| `_class_methods` | `class_fqn.method → FQN` — fast class-scope lookup |
| `_file_global_symbols` | `file → name → FQN` — tie-breaking by same-file preference |

**Resolution order** for a `raw_call:<name>` edge from source FQN `A`:

1. If `name` starts with `self.`, look up the method on the enclosing class via `_class_methods`
2. Look up `name` in `_global_symbols`; if ambiguous, prefer same-file candidate via `_file_global_symbols`
3. If unresolved: keep target as `raw_call:<name>`, set `confidence=0.1`

Unresolved calls are **first-class citizens** — they appear in the graph explicitly rather than being silently discarded.

---

## Phase 3 — Storage

**Entry point:** `SQLiteStore.save_graph(nodes, edges, overwrite=False)`

The SQLite store runs in **WAL mode** for concurrent read safety. The schema is auto-generated from `SQLiteStore._create_schema()` — do not edit below manually:

<!-- START_CGIS_SCHEMA -->
```sql
CREATE TABLE IF NOT EXISTS nodes (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            language TEXT NOT NULL,
            ontology_class TEXT,
            domains TEXT,
            confidence_score REAL NOT NULL,
            metadata TEXT,
            namespace TEXT NOT NULL DEFAULT 'INTERNAL'
        );

        CREATE TABLE IF NOT EXISTS edges (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            type TEXT NOT NULL,
            weight REAL NOT NULL,
            confidence REAL NOT NULL,
            context TEXT,
            file_path TEXT,
            line_number INTEGER
        );

        CREATE TABLE IF NOT EXISTS files_state (
            file_path TEXT PRIMARY KEY,
            hash TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
        CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
        CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
        CREATE INDEX IF NOT EXISTS idx_nodes_file_path ON nodes(file_path);
        CREATE INDEX IF NOT EXISTS idx_edges_file_path ON edges(file_path);
```
<!-- END_CGIS_SCHEMA -->

Graph traversals use iterative BFS with batch edge fetches (O(depth) round-trips, not O(nodes)):

```python
# Per BFS level: one query for the entire frontier
edges = store.get_outgoing_edges_batch(current_frontier)
```

---

## Phase 4 — Semantic Uplift

**Entry point:** `SemanticUpliftEngine.execute_uplift()`

Optional post-processing pass that enriches nodes with semantic metadata. Requires a `domains.yaml` config for phases 2–3.

| Sub-phase | Action |
| :--- | :--- |
| 1. Ontology mapping | Assigns `ontology_class` string from `NodeType` (e.g. `CLASS → "Class"`) |
| 2. Heuristic tagging | Matches `file_path` and FQN against `fnmatch` patterns from `domains.yaml` |
| 3. Structural propagation | Flows domain tags downward via `CONTAINS`/`DECLARES` edges (BFS) |
| 4. Dependency inference | Emits `DOMAIN_DEPENDS_ON` edges for cross-domain `CALLS` relationships |

Domain tags are always reset before phases 2–3 to ensure deterministic results across re-runs.

---

## Incremental Mode

`cgis ingest --incremental` skips files whose SHA-256 content hash has not changed since the last run. Changed files are re-extracted and their stale nodes/edges are replaced atomically before the full uplift pass runs.

---

## Query Model

Two BFS traversals are exposed via `QueryEngine`:

| Method | Direction | Question answered |
| :--- | :--- | :--- |
| `get_flow_graph(fqn)` | Downstream (outgoing) | "What does X call?" |
| `get_impact_graph(fqn)` | Upstream (incoming) | "What breaks if X changes?" |

Both return `(nodes, edges)` suitable for rendering as a Mermaid diagram.
