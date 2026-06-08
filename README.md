# 🧠 CGIS: Code Graph Intelligence System
### *The Semantic Ground Truth for AI Agents*

[![Continuous Integration](https://github.com/zaebee/codegraph-brain/actions/workflows/ci.yml/badge.svg)](https://github.com/zaebee/codegraph-brain/actions/workflows/ci.yml)
[![Graph Integrity](https://img.shields.io/badge/graph_status-healthy-brightgreen)](https://github.com/zaebee/codegraph-brain)

**LLM coding agents (Claude, Cursor, GPT) are currently "guessing" your architecture based on flat text snippets. CGIS stops the guessing.**

CGIS transforms raw source code into a deterministic, multi-layered semantic graph. It provides AI agents with a high-fidelity architectural model, enabling them to understand not just what the code *says*, but how it *behaves* and *connects*.

---

## ⚡ The Problem: The "Context Gap"
Traditional RAG (Retrieval-Augmented Generation) feeds agents chunks of text. This leads to:
*   **Hallucinations:** Agents assume connections that don't exist.
*   **Context Bloat:** Passing entire files to explain a single function.
*   **Structural Blindness:** Agents cannot "see" transitive impacts (e.g., *"If I change this, what breaks 5 layers up?"*).

## ✨ The Solution: Semantic Intelligence
CGIS replaces "textual guessing" with **"structural calculation"**:
*   **Deterministic Resolution:** Full FQN (Fully Qualified Name) resolution via AST-based extraction.
*   **Multi-Layer Ontology:** Goes beyond calls. It understands `CONTAINS`, `DECLARES`, `IMPORTS`, and semantic domains.
*   **Agent-Native (MCP):** Exposes the entire graph as a set of high-performance tools for Claude, Cursor, and custom agents.
*   **Self-Documenting:** The documentation is a living artifact, automatically updated with live architecture diagrams.

---

## 🏗️ Architecture: The Pipeline

CGIS operates via a high-speed, three-stage pipeline:

1.  **EXTRACT:** Language-specific AST parsers (Tree-sitter) convert source code into raw nodes and edges.
2.  **RESOLVE:** The `ResolverEngine` disambiguates raw calls into absolute, deterministic FQNs.
3.  **STORE:** A high-performance SQLite backend enables complex graph traversals (BFS/DFS) in milliseconds.

```mermaid
graph LR
    A[Source Code] --> B[Extractors]
    B --> C[Resolver Engine]
    C --> D[(SQLite Graph DB)]
    D --> E[MCP Server]
    D --> F[Prompt Compiler]
    E --> G[AI Agents]
    F --> G
```

---

## 🚀 Quickstart

### 1. Installation
Using `uv` (recommended):
```bash
uv pip install -e .
```

### 2. Ingest a Repository
Turn any codebase into a semantic knowledge graph:
```bash
cgis ingest ./my-awesome-project --output graph.db
```

### 3. Query the Graph
Analyze impact or trace execution flow directly from your terminal:
```bash
# Trace the execution path of a function
cgis trace "my_module.MyClass.my_method" --depth 3 --format mermaid

# Analyze the blast radius of a change
cgis impact "my_module.core_function" --depth 5
```

---

## 🤖 Agent Integration (MCP)

CGIS is designed to be plugged into your AI workflow via the **Model Context Protocol (MCP)**. Once running, your agent gains "Superpowers":

*   `cgis_ingest`: Build the knowledge base.
*   `cgis_trace_flow`: Visualize execution paths.
*   `cgis_analyze_impact`: Predict regressions before they happen.
*   `cgis_get_structure`: Understand class/module hierarchy.

---

## 📊 Live System Architecture
*Kept in sync with the codebase — update this diagram when the core pipeline changes.*

<!-- START_CGIS_GRAPH -->
```mermaid
graph TD
    n_cli["cgis.cli.ingest (cli.py)"] -- CALLS --> n_pipe["cgis.pipeline.IngestionPipeline.run (pipeline.py)"]
    n_pipe -- CALLS --> n_ext["cgis.extractors.python_extractor.PythonExtractor.parse"]
    n_pipe -- CALLS --> n_res["cgis.resolver.engine.ResolverEngine.resolve"]
    n_pipe -- CALLS --> n_db["cgis.storage.sqlite_store.SQLiteStore.save_graph"]
```
<!-- END_CGIS_GRAPH -->

---

## 🛠️ Development

### Requirements
*   Python 3.12+
*   `uv` (for dependency management)

### Running Tests
```bash
make pytest
```

### Contributing
We are building the future of agentic engineering. Please see `CONTRIBUTING.md` for our standards on type safety (strict MyPy), linting, and ontology compliance.

---
*Built with ❤️ for the future of autonomous software engineering.*
