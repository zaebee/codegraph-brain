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

Trace the execution call-graph **downstream** from a starting FQN.

```bash
cgis trace <fqn> [OPTIONS]
```

| Option | Default | Description |
| :--- | :--- | :--- |
| `--db`, `-d` | `graph.db` | Path to the graph database |
| `--depth` | `5` | Maximum traversal depth |
| `--format`, `-f` | `text` | Output format: `text` or `mermaid` |
| `--internal-only` | `False` | Exclude external library nodes |

**Examples:**

```bash
# Text tree of what IngestionPipeline.run calls
cgis trace "cgis.pipeline.IngestionPipeline.run" --depth 3

# Mermaid diagram for pasting into docs
cgis trace "cgis.pipeline.IngestionPipeline.run" --format mermaid
```

---

### `cgis impact`

Trace **upstream** callers — who depends on this FQN?

```bash
cgis impact <fqn> [OPTIONS]
```

| Option | Default | Description |
| :--- | :--- | :--- |
| `--db`, `-d` | `graph.db` | Path to the graph database |
| `--depth` | `5` | Maximum traversal depth |
| `--format`, `-f` | `text` | Output format: `text` or `mermaid` |
| `--internal-only` | `False` | Exclude external library nodes |

**Examples:**

```bash
# Who calls SQLiteStore.save_graph?
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
