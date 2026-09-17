# 💻 CLI Usage Guide

## Installation

```bash
uv pip install -e .
```

Verify:

```bash
cgis --version
```

---

## Commands

### `cgis ingest`

Scan a repository, extract code structure, and build the semantic graph.

```bash
cgis ingest <path> [OPTIONS]
```

| Option | Default | Description |
| :--- | :--- | :--- |
| `--output`, `-o` | `graph.json` | Output path (`.db` for SQLite, `.json` for raw dump) |
| `--incremental`, `-i` | `False` | Only re-ingest changed files (requires `.db` output) |
| `--domains`, `-d` | `None` | Path to `domains.yaml` for semantic uplift |

**Examples:**

```bash
# Full ingest with semantic uplift
cgis ingest ./src --output graph.db --domains docs/ontology/domains.yaml

# Incremental update after code changes
cgis ingest ./src --output graph.db --incremental

# Raw JSON dump for inspection
cgis ingest ./src --output graph.json
```

---

### `cgis trace`

Everything a FQN reaches **downstream**: every edge except containment, which in practice means calls, imports, inheritance, DI dependencies and references between internal code. Containment stays out unless `--show-structure` adds it, and so do stdlib, third-party and unresolved call targets unless `--show-external` does. The MCP tool `cgis_trace_flow` uses the same defaults (`include_structure`, `include_external`).

```bash
cgis trace <fqn> [OPTIONS]
```

| Option | Default | Description |
| :--- | :--- | :--- |
| `--db`, `-d` | `graph.db` | Path to the graph database |
| `--depth` | `5` | Maximum traversal depth |
| `--format`, `-f` | `text` | `text`, `mermaid`, or `json` (joinable `{root, nodes, edges, coverage}`) |
| `--show-structure` | off | Also follow containment (CONTAINS/DECLARES) |
| `--show-external` | off | Also show stdlib, third-party and unresolved call targets |
| `--internal-only` | off | Drop those nodes again from `mermaid`/`json` output; a no-op unless `--show-external` is on, and not valid with `text` |
| `--min-confidence` | none | Hide edges below this confidence. Resolved calls score 1.0 and inferred ones 0.8, so a threshold above 0.8 is what filters anything |

**Examples:**

```bash
# Text tree of what IngestionPipeline.run depends on
cgis trace "cgis.pipeline.IngestionPipeline.run" --depth 3

# Mermaid diagram for pasting into docs
cgis trace "cgis.pipeline.IngestionPipeline.run" --format mermaid

# Machine-readable, with the module's own members included
cgis trace "cgis.query.engine" --format json --show-structure
```

---

### `cgis impact`

Everything that reaches a FQN **upstream**: callers, importers, subclasses, type references and DI dependents. The enclosing class or file stays out unless `--show-structure` adds it, and so do stdlib, third-party and unresolved call targets unless `--show-external` does. The MCP tool `cgis_analyze_impact` uses the same defaults.

```bash
cgis impact <fqn> [OPTIONS]
```

| Option | Default | Description |
| :--- | :--- | :--- |
| `--db`, `-d` | `graph.db` | Path to the graph database |
| `--depth` | `5` | Maximum traversal depth |
| `--format`, `-f` | `text` | `text`, `mermaid`, or `json` (joinable `{root, nodes, edges, coverage}`) |
| `--show-structure` | off | Also follow containment (CONTAINS/DECLARES) |
| `--show-external` | off | Also show stdlib, third-party and unresolved call targets |
| `--internal-only` | off | Drop those nodes again from `mermaid`/`json` output; a no-op unless `--show-external` is on, and not valid with `text` |
| `--min-confidence` | none | Hide edges below this confidence. Resolved calls score 1.0 and inferred ones 0.8, so a threshold above 0.8 is what filters anything |

**Examples:**

```bash
# What depends on SQLiteStore.save_graph?
cgis impact "cgis.storage.sqlite_store.SQLiteStore.save_graph"

# Blast radius of changing the Node model
cgis impact "cgis.core.models.Node" --depth 4 --format mermaid
```

---

### `cgis structure`

Show the internal layout of a module or class.

```bash
cgis structure <fqn> [OPTIONS]
```

| Option | Default | Description |
| :--- | :--- | :--- |
| `--db`, `-d` | `graph.db` | Path to the graph database |
| `--depth` | `2` | Traversal depth |
| `--show-external` | `False` | Include external nodes |

**Example:**

```bash
cgis structure "cgis.storage.sqlite_store.SQLiteStore"
```

---

### `cgis validate`

Validate the graph database for integrity and schema compliance.

```bash
cgis validate [OPTIONS]
```

| Option | Default | Description |
| :--- | :--- | :--- |
| `--db`, `-d` | `graph.db` | Path to the graph database |

---

## FQN format

All commands accept FQNs in dot-separated form derived from file paths:

```
src/cgis/pipeline.py                →  cgis.pipeline
src/cgis/pipeline.py::IngestionPipeline  →  cgis.pipeline.IngestionPipeline
src/cgis/pipeline.py::IngestionPipeline.run  →  cgis.pipeline.IngestionPipeline.run
```

Use `cgis structure` on a module to discover available FQNs before running `trace` or `impact`.
