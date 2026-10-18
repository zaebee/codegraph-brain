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
2. Look up `name` in `_global_symbols`, among the caller's own language only; if ambiguous, prefer the same-scope candidate (a file, or a Go package) via `_file_global_symbols`
3. A resolved call scores `confidence=1.0` in every language (a Go builtin resolves to `builtin.<name>`), capped at 0.8 when its target is an UNKNOWN boundary node. If unresolved, the target becomes the bare `<name>` (or a pack fallback such as `js_builtins.<name>`), a virtual boundary node classified STDLIB, EXTERNAL or UNKNOWN, at `confidence=0.8`. A name equal to the id of a real node keeps the `raw_call:` prefix instead, so the edge cannot join that node (#661)

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
            namespace TEXT NOT NULL DEFAULT 'INTERNAL',
            is_test INTEGER NOT NULL DEFAULT 0,
            is_generated INTEGER NOT NULL DEFAULT 0
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
            line_number INTEGER,
            type_only INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS files_state (
            file_path TEXT PRIMARY KEY,
            hash TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ingest_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
        CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
        CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
        CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type);
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

---

## Live Pipeline Graph

The callees of `IngestionPipeline.run`, regenerated from CGIS's own source by the autodoc workflow on pushes to `main`, which opens a docs PR with the result.

<!-- START_CGIS_GRAPH -->
> *Auto-generated by CGIS parsing its own source — the tool documents itself.*

```mermaid
graph TD
classDef classNode fill:#e8f5e9,stroke:#2e7d32,stroke-width:1.5px,color:#1b5e20;
classDef funcNode fill:#e3f2fd,stroke:#1565c0,stroke-width:1.5px,color:#0d47a1;
classDef methodNode fill:#f3e5f5,stroke:#7b1fa2,stroke-width:1.5px,color:#4a148c;
classDef unresolvedNode fill:#fffde7,stroke:#fbc02d,stroke-width:1.5px,stroke-dasharray: 4 4,color:#f57f17;
classDef defaultNode fill:#fafafa,stroke:#9e9e9e,stroke-width:1.5px,color:#212121;
classDef stdlibNode fill:#eceff1,stroke:#607d8b,stroke-width:1px,color:#455a64;
classDef externalNode fill:#fff3e0,stroke:#e65100,stroke-width:1px,stroke-dasharray: 3 3,color:#bf360c;

    subgraph sg_models["models.py"]
        models_Edge["Edge (models.py:162)"]:::classNode
        models_Node["Node (models.py:105)"]:::classNode
    end
    subgraph sg_import_names["import_names.py"]
        import_names_ImportNameCollector["ImportNameCollector (import_names.py:147)"]:::classNode
        import_names_ImportNameCollector_collect["collect (import_names.py:168)"]:::methodNode
        import_names_ImportNameCollector_note["note (import_names.py:163)"]:::methodNode
    end
    subgraph sg_init["__init__.py"]
        pipeline_IngestionPipeline_cross_file_inputs_changed["_cross_file_inputs_changed (__init__.py:395)"]:::methodNode
        pipeline_IngestionPipeline_get_extractor["_get_extractor (__init__.py:539)"]:::methodNode
        pipeline_IngestionPipeline_is_noop_incremental["_is_noop_incremental (__init__.py:462)"]:::methodNode
        pipeline_IngestionPipeline_persist_incremental["_persist_incremental (__init__.py:486)"]:::methodNode
        pipeline_IngestionPipeline_process_file["_process_file (__init__.py:355)"]:::methodNode
        pipeline_IngestionPipeline_run["run (__init__.py:210)"]:::methodNode
        pipeline_IngestionPipeline_workspace_root["workspace_root (__init__.py:193)"]:::methodNode
        pipeline_needs_rebuild["_needs_rebuild (__init__.py:139)"]:::funcNode
        pipeline_observe["_observe (__init__.py:73)"]:::funcNode
        pipeline_refresh_dispatch["refresh_dispatch (__init__.py:36)"]:::funcNode
    end
    subgraph sg_uplift["uplift.py"]
        uplift_SemanticUpliftEngine["SemanticUpliftEngine (uplift.py:67)"]:::classNode
        uplift_SemanticUpliftEngine_execute_uplift["execute_uplift (uplift.py:92)"]:::methodNode
    end
    subgraph sg_engine["engine.py"]
        engine_ResolverEngine["ResolverEngine (engine.py:132)"]:::classNode
        engine_ResolverEngine_resolve["resolve (engine.py:178)"]:::methodNode
    end
    subgraph sg_sqlite_store["sqlite_store.py"]
        sqlite_store_SQLiteStore["SQLiteStore (sqlite_store.py:81)"]:::classNode
    end
    pipeline_IngestionPipeline_run -->|REFERENCES| models_Node
    pipeline_IngestionPipeline_run -->|REFERENCES| models_Edge
    pipeline_IngestionPipeline_run -->|REFERENCES| sqlite_store_SQLiteStore
    pipeline_IngestionPipeline_run -->|CALLS| pipeline_needs_rebuild
    pipeline_IngestionPipeline_run -->|CALLS| pipeline_IngestionPipeline_workspace_root
    pipeline_IngestionPipeline_run -->|CALLS| import_names_ImportNameCollector
    pipeline_IngestionPipeline_run -->|CALLS| pipeline_observe
    pipeline_IngestionPipeline_run -->|CALLS| import_names_ImportNameCollector_note
    pipeline_IngestionPipeline_run -->|CALLS| pipeline_IngestionPipeline_get_extractor
    pipeline_IngestionPipeline_run -->|CALLS| pipeline_IngestionPipeline_process_file
    pipeline_IngestionPipeline_run -->|CALLS| import_names_ImportNameCollector_collect
    pipeline_IngestionPipeline_run -->|CALLS| pipeline_IngestionPipeline_is_noop_incremental
    pipeline_IngestionPipeline_run -->|CALLS| engine_ResolverEngine
    pipeline_IngestionPipeline_run -->|CALLS| engine_ResolverEngine_resolve
    pipeline_IngestionPipeline_run -->|CALLS| pipeline_IngestionPipeline_run
    pipeline_IngestionPipeline_run -->|CALLS| pipeline_IngestionPipeline_cross_file_inputs_changed
    pipeline_IngestionPipeline_run -->|CALLS| pipeline_IngestionPipeline_persist_incremental
    pipeline_IngestionPipeline_run -->|CALLS| pipeline_refresh_dispatch
    pipeline_IngestionPipeline_run -->|CALLS| uplift_SemanticUpliftEngine_execute_uplift
    pipeline_IngestionPipeline_run -->|CALLS| uplift_SemanticUpliftEngine
```

| Symbol | Type | File |
|--------|------|------|
| `Node` | CLASS | [`models.py:105`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/core/models.py#L105) |
| `Edge` | CLASS | [`models.py:162`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/core/models.py#L162) |
| `ImportNameCollector` | CLASS | [`import_names.py:147`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/import_names.py#L147) |
| `note` | METHOD | [`import_names.py:163`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/import_names.py#L163) |
| `collect` | METHOD | [`import_names.py:168`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/import_names.py#L168) |
| `refresh_dispatch` | FUNCTION | [`__init__.py:36`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/pipeline/__init__.py#L36) |
| `_observe` | FUNCTION | [`__init__.py:73`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/pipeline/__init__.py#L73) |
| `_needs_rebuild` | FUNCTION | [`__init__.py:139`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/pipeline/__init__.py#L139) |
| `workspace_root` | METHOD | [`__init__.py:193`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/pipeline/__init__.py#L193) |
| `run` | METHOD | [`__init__.py:210`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/pipeline/__init__.py#L210) |
| `_process_file` | METHOD | [`__init__.py:355`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/pipeline/__init__.py#L355) |
| `_cross_file_inputs_changed` | METHOD | [`__init__.py:395`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/pipeline/__init__.py#L395) |
| `_is_noop_incremental` | METHOD | [`__init__.py:462`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/pipeline/__init__.py#L462) |
| `_persist_incremental` | METHOD | [`__init__.py:486`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/pipeline/__init__.py#L486) |
| `_get_extractor` | METHOD | [`__init__.py:539`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/pipeline/__init__.py#L539) |
| `SemanticUpliftEngine` | CLASS | [`uplift.py:67`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/pipeline/uplift.py#L67) |
| `execute_uplift` | METHOD | [`uplift.py:92`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/pipeline/uplift.py#L92) |
| `ResolverEngine` | CLASS | [`engine.py:132`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/resolver/engine.py#L132) |
| `resolve` | METHOD | [`engine.py:178`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/resolver/engine.py#L178) |
| `SQLiteStore` | CLASS | [`sqlite_store.py:81`](https://github.com/zaebee/codegraph-brain/blob/main/src/cgis/storage/sqlite_store.py#L81) |
<!-- END_CGIS_GRAPH -->
