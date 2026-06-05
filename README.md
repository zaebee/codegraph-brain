# 🧠 CodeGraph Brain

### A Symbolic Graph-Based Code Intelligence System with FQN Resolution and Semantic Ontology Layer

---

## Abstract

Modern code understanding systems rely heavily on embedding-based retrieval, which fails to capture **structural correctness, namespace resolution, and execution semantics** of real-world software systems.

We introduce **CodeGraph Brain**, a graph-native code intelligence system that models repositories as a **multi-layer semantic graph**. The system combines:

* Fully Qualified Name (FQN) resolution
* AST-based extraction via tree-sitter
* Multi-pass symbol resolution (imports, scope, aliasing)
* Semantic ontology layer (GraphRAG-ready)
* Hybrid structural + semantic retrieval

Unlike embedding-only systems, CodeGraph Brain provides **deterministic reasoning over code structure and execution flow**.

---

## 1. Introduction

Code is not text. Code is a **structured, recursive system of symbolic references**.

Existing systems (e.g., embedding-based RAG tools) fail in:

* Namespace ambiguity (e.g., `save()` in multiple contexts)
* Aliasing (`import x as y`)
* Cross-file resolution
* Runtime flow reconstruction
* Domain-level reasoning

We propose a **symbolic graph-first model** for code intelligence.

---

## 2. Problem Statement

We define three fundamental problems:

### 2.1 Symbol Ambiguity

Multiple definitions of the same symbol exist across scopes.

### 2.2 Resolution Failure

Naive name matching fails under:

* imports
* aliasing
* class scope
* module boundaries

### 2.3 Semantic Gap

Embedding models cannot reliably infer:

* execution flow
* call chains
* system boundaries

---

## 3. System Overview

CodeGraph Brain is composed of 4 layers:

```text
┌──────────────────────────────┐
│  Semantic Ontology Layer     │
├──────────────────────────────┤
│  Execution Graph Layer       │
├──────────────────────────────┤
│  Structural Code Graph       │
├──────────────────────────────┤
│  AST (tree-sitter) Layer     │
└──────────────────────────────┘
```

---

## 4. Core Idea

We model code as:

> **A directed labeled multigraph over symbolic entities with fully qualified resolution.**

Each node is uniquely identified via:

```text
FQN = [module path] + [class scope] + [symbol name]
```

Example:

```text
src.auth.services.UserService.login
```

---

## 5. System Architecture

### 5.1 Pipeline

```text
Repository
   ↓
Tree-sitter Parser
   ↓
Symbol Extraction
   ↓
Phase 1: Global Indexing
   ↓
Phase 2: Namespace Resolution
   ↓
Phase 3: Edge Linking
   ↓
Graph Store (SQLite / DuckDB)
   ↓
Graph API (FastAPI)
```

---

## 6. Three-Pass Resolution Model

### Phase 1 — Declaration Indexing

Build global symbol table:

```text
FQN → Node metadata
```

Captures:

* functions
* classes
* methods

---

### Phase 2 — Namespace Mapping

Construct per-file local namespace:

* imports
* aliases
* scoped symbols
* class members

Example:

```python
from auth import login as auth_login
```

→

```text
auth_login → src.auth.login
```

---

### Phase 3 — Linking

Resolve calls:

* CALLS edges
* IMPORTS edges
* SCOPE edges

Fallback:

* unresolved calls are explicitly stored for debugging

---

## 7. Graph Model

### 7.1 Node Schema

```python
Node:
    id: str
    type: NodeType
    name: str
    file_path: str
    start_line: int
    end_line: int
    language: str

    ontology_class: Optional[str]
    domains: List[str]
    metadata: dict
```

---

### 7.2 Node Types

* FILE
* MODULE
* CLASS
* FUNCTION
* METHOD
* VARIABLE
* API_ENDPOINT
* DOMAIN_CONCEPT

---

### 7.3 Edge Schema

```python
Edge:
    source: str
    target: str
    type: EdgeType

    weight: float
    confidence: float
    context: Optional[str]
```

---

### 7.4 Edge Types

#### Structural

* DECLARES
* CONTAINS
* IMPORTS

#### Behavioral

* CALLS
* REFERENCES

#### Runtime

* ROUTES_TO
* TRIGGERS
* EMITS

#### Semantic

* HANDLES
* AUTHORIZES
* PERSISTS

---

## 8. Ontology Layer (OWL-Lite)

We define a lightweight ontology over the graph.

### 8.1 Core Classes

* CodeEntity
* CodeSymbol
* RuntimeBehavior
* DataEntity
* DomainConcept

---

### 8.2 Domain Model

Domains are first-class nodes:

* Auth
* Billing
* UserManagement
* Notifications

---

### 8.3 Semantic Relations

* belongsToDomain
* dependsOnDomain
* handles
* transforms
* persists

---

## 9. Resolver Model

We implement a deterministic resolution engine:

```text
resolve_call():
    1. Local Namespace (imports/alias)
    2. Class Scope (self.method)
    3. Global Symbol Table
    4. Mark unresolved if failed
```

This solves:

* shadowing
* aliasing
* namespace collisions

---

## 10. Query Model

Supported queries:

### 10.1 Usage Query

> Where is X used?

### 10.2 Dependency Query

> What depends on X?

### 10.3 Flow Query

> How does X work?

### 10.4 Impact Query

> What breaks if X changes?

---

## 11. Graph Context Output

All retrieval returns structured context:

```json
{
  "focus": "function_name",
  "nodes": [...],
  "edges": [...],
  "paths": [...],
  "summary": "...",
  "confidence": 0.0
}
```

---

## 12. Design Principles

### 12.1 Determinism over fuzziness

No embedding-based ambiguity in core resolution.

### 12.2 Explicit unresolved tracking

Unknown calls are first-class entities.

### 12.3 Multi-layer graph separation

Structural ≠ semantic ≠ runtime.

### 12.4 Fractal representation

Every node can be expanded into subgraphs.

---

## 13. Limitations (v0.1)

* dynamic languages (Python reflection) partially unresolved
* no runtime tracing yet
* no distributed indexing
* no full type inference system

---

## 14. Future Work

* Cross-repository graph linking
* Live runtime instrumentation
* IDE integration (VSCode plugin)
* GraphRAG prompt compiler
* Auto-domain inference (LLM-assisted ontology growth)
* Graph query language (Cypher-lite for agents)

---

## 15. Conclusion

CodeGraph Brain reframes code understanding as:

> a symbolic, resolvable, multi-layer graph system rather than semantic text retrieval.

This enables deterministic reasoning over software systems and forms a foundation for next-generation GraphRAG architectures.

---

## 🧠 Philosophy

> “If embeddings are intuition, graphs are cognition.”

---

## License

MIT
