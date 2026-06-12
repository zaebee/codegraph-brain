# 🧬 CGIS Ontology: Formal L1–L3 Schema

The CGIS ontology is a three-layer contract that maps raw AST constructs to semantic concepts. It governs what every `Node` and `Edge` in the graph means and is treated as a **breaking-change boundary** — modifying it requires a PR with explicit migration notes.

> For the *architectural* layer — the 13-triad census and the 5-pattern alphabet that drift scores against — see **[PATTERNS_AND_TRIADS.md](./PATTERNS_AND_TRIADS.md)**.

---

## L1 — Structural Layer (AST-derived)

Directly maps to syntax constructs extracted by tree-sitter. These are emitted by `BaseExtractor` subclasses.

### Node Types (L1)

| `NodeType` | `ontology_class` | Description |
| :--- | :--- | :--- |
| `FILE` | `"File"` | A source file on disk |
| `MODULE` | `"Module"` | A Python module (maps to `__init__.py` or a `.py` file as a namespace) |
| `CLASS` | `"Class"` | A class declaration |
| `FUNCTION` | `"Function"` | A module-level function |
| `METHOD` | `"Method"` | A method defined inside a class |
| `VARIABLE` | `"Variable"` | A module-level or class-level variable |
| `IMPORT` | `"Import"` | An import statement node |
| `EXPORT` | `"Export"` | An export declaration (JS/TS extractors) |

### Edge Types (L1)

| `EdgeType` | Direction | Description |
| :--- | :--- | :--- |
| `CONTAINS` | parent → child | File/module contains a class or function |
| `DECLARES` | class → member | Class declares a method or attribute |
| `IMPORTS` | file → symbol | File imports a symbol from another module |

---

## L2 — Behavioral Layer (Resolved)

Produced by `ResolverEngine` after FQN resolution. These edges represent runtime behavior.

### Node Types (L2)

| `NodeType` | `ontology_class` | Description |
| :--- | :--- | :--- |
| `API_ENDPOINT` | `"APIEndpoint"` | An HTTP route handler (e.g. FastAPI `@router.get`) |
| `ROUTE_HANDLER` | `"RouteHandler"` | Internal routing function |
| `DB_TABLE` | `"DBTable"` | A database model or table declaration |
| `DB_QUERY` | `"DBQuery"` | A query-constructing function |
| `EVENT` | `"Event"` | A domain event emitted or consumed |

### Edge Types (L2)

| `EdgeType` | Direction | Description |
| :--- | :--- | :--- |
| `CALLS` | caller → callee | A resolved function/method invocation |
| `REFERENCES` | node → node | A non-call symbolic reference |

**Unresolved calls** keep `target = "raw_call:<name>"` with `confidence = 0.1` and are stored explicitly so gaps in resolution are visible rather than hidden.

---

## L3 — Semantic Layer (Uplift)

Produced by `SemanticUpliftEngine`. These constructs exist above the code structure — they model intent and domain boundaries.

### Node Types (L3)

| `NodeType` | `ontology_class` | Description |
| :--- | :--- | :--- |
| `DOMAIN_CONCEPT` | `"DomainConcept"` | A named architectural domain (e.g. `domain:StorageLayer`) |

`DOMAIN_CONCEPT` nodes use `id = "domain:<name>"` and `file_path = VIRTUAL_FILE_PATH` — they are synthetic and not tied to any file.

### Edge Types (L3)

| `EdgeType` | Direction | Description |
| :--- | :--- | :--- |
| `DOMAIN_DEPENDS_ON` | domain → domain | Domain A calls into domain B (inferred from cross-domain `CALLS` edges) |

---

## Node Fields Reference

```
Node
├── id              str          — FQN (primary key, e.g. "cgis.pipeline.IngestionPipeline.run")
├── type            NodeType     — L1/L2/L3 classification
├── name            str          — short symbol name (last segment of FQN)
├── file_path       str          — absolute or repo-relative path; VIRTUAL_FILE_PATH for synthetic nodes
├── language        str          — source language (e.g. "python"); empty string for virtual nodes
├── start_line      int          — 1-based line number
├── end_line        int          — 1-based line number
├── namespace       NodeNamespace — INTERNAL (repo code) | EXTERNAL (stdlib/deps) | VIRTUAL
├── ontology_class  str | None   — set by SemanticUpliftEngine Phase 1
├── domains         list[str]    — set by SemanticUpliftEngine Phases 2–3
├── confidence_score float       — extractor confidence (default 1.0)
└── metadata        dict         — arbitrary key/value bag for extractor-specific data
```

---

## Edge Fields Reference

```
Edge
├── id          str       — unique edge identifier (typically "source->target:type")
├── source      str       — FQN of origin node
├── target      str       — FQN of target node (or "raw_call:<name>" if unresolved)
├── type        EdgeType  — L1/L2/L3 classification
├── weight      float     — traversal weight (default 1.0)
└── confidence  float     — resolution confidence; 0.1 for unresolved raw calls
```

---

## Domain Configuration (`docs/ontology/domains.yaml`)

Domain boundaries are declared in `docs/ontology/domains.yaml`. This file is the **authoritative source** for L3 semantic tagging. Patterns use `fnmatch` syntax and are matched case-sensitively.

```yaml
version: "0.1.0"
domains:
  StorageLayer:
    description: "Database storage, schemas, and persistence"
    heuristics:
      file_path_patterns:
        - "*storage/*"
      fqn_patterns:
        - "*.Store*"
        - "*storage.*"
```

**Ontology compliance rule:** Any PR that adds a new `NodeType`, `EdgeType`, or modifies `domains.yaml` must update this document and the `_ONTOLOGY_CLASS` mapping in `src/cgis/resolver/uplift.py`.
