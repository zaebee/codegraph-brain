# AGENTS.md — CodeGraph Brain

The guide for every AI agent in this repository. `CLAUDE.md` holds only `@AGENTS.md`,
so Claude Code and every other agent read the same text.

## What This Is

**CodeGraph Brain** (`cgis` — Code Graph Intelligence System) is a Python library and CLI that parses source code into a multi-layer semantic graph with fully qualified name (FQN) resolution. It uses tree-sitter for AST extraction and stores the resulting graph in SQLite.

The CLI entry point is `cgis ingest <path>`, which produces a `graph.json` output.

## Commands

```bash
# Run CLI
uv run cgis ingest <repo_path> --output graph.json

# Format
make format        # runs: uv run ruff format .

# Lint (with auto-fix)
make lint          # runs: uv run ruff check . --fix

# Type check (strict mypy — this is mandatory)
make type-check    # runs: uv run mypy src

# Tests
make pytest        # runs: uv run pytest

# Docstring coverage (minimum 90%)
make doc-coverage  # runs: uv run interrogate src

# Full verification (run before every commit/PR)
make format && make lint && make type-check && make pytest && make doc-coverage

# Single test
uv run pytest tests/unit/test_python_extractor.py::test_extract_simple_function -v

# Line coverage (same scope as CI — see the Makefile comment on why scripts/ counts)
make coverage        # runs: uv run pytest --cov=cgis --cov=scripts --cov-report=term-missing
make coverage-html   # same, writes htmlcov/
```

## Architecture

The pipeline runs in three phases: **Extract → Resolve → Store**.

```
IngestionPipeline (pipeline/__init__.py)
  ├── cgis.languages (registry)                ← lazy: a pack is imported when its first file is met
  │     ├── languages/python/                  ← PythonExtractor + PACK
  │     ├── languages/typescript/              ← TypeScriptExtractor (.ts/.tsx) + PACK
  │     └── languages/go/                      ← GoExtractor (.go) + go.mod reader + PACK (pilot)
  ├── ResolverEngine (resolver/engine.py)      ← raw_call: targets → resolved FQN edges
  ├── SQLiteStore (storage/sqlite_store.py)    ← persists graph; WAL mode
  └── post-write passes (pipeline/)            ← refresh_dispatch, SemanticUpliftEngine (pipeline/uplift.py)
        └── QueryEngine (query/engine.py)      ← BFS traversals (impact / flow)
```

**Node FQN format**: `module.ClassName.method_name` — fully dot-separated, derived from the file path (e.g., `src/cgis/pipeline.py` → `src.cgis.pipeline.IngestionPipeline.run`). Use `cgis.languages.scope_of(path, source_root)` to convert paths (it picks the file's own pack; the Python rule lives in `languages/python/extractor.py`). `__init__.py` strips the `/__init__` suffix.

**Raw call convention**: Extractors emit edges with `target = "raw_call:<name>"`. The `ResolverEngine` then resolves these to actual FQNs, or leaves them unresolved: the target becomes the bare raw name (or a pack fallback such as `js_builtins.<name>`), a virtual boundary node classified STDLIB, EXTERNAL or UNKNOWN (#661).

**`ResolverEngine` resolution order** for a call to `name`:
1. `self.method` → look up `method` on the enclosing class via `_class_methods` index
2. Unqualified call → `_global_symbols` by name, among the caller's own language only (#675); if ambiguous, prefer the same-scope candidate via `file_global_symbols` (a file's scope is its path, or the `metadata["scope"]` its FILE node declares — a Go package)

## Core Models (`src/cgis/core/models.py`)

- `Node` — immutable (frozen Pydantic). `id` is the FQN. Key fields: `type: NodeType`, `file_path`, `start_line`, `end_line`, `confidence_score`, `domains`, `ontology_class`.
- `Edge` — immutable (frozen Pydantic). `source`/`target` are FQNs. Key fields: `type: EdgeType`, `weight`, `confidence`. A call leaves the extractor as target `raw_call:<name>`; after resolution a resolved call scores 1.0 in every language (#674) unless its target is an UNKNOWN boundary node (capped at 0.8, #493), and an unresolved one scores 0.8. A DISPATCHES edge scores 0.5, and an unresolved class reference 0.5.
- `NodeType` — structural (`FILE`, `MODULE`, `CLASS`, `FUNCTION`, `METHOD`…), runtime (`API_ENDPOINT`, `DB_TABLE`…), semantic (`DOMAIN_CONCEPT`).
- `EdgeType` — structural (`CONTAINS`, `DECLARES`, `IMPORTS`), behavioral (`CALLS`, `REFERENCES`), semantic (`HANDLES`, `PERSISTS`, `AUTHORIZES`).

## Adding a Language Pack

1. Create `src/cgis/languages/<name>/` with an extractor subclassing `cgis.core.extractor.BaseExtractor`
   (`parse(code, file_path) -> tuple[list[Node], list[Edge]]`; emit `CALLS` edges with
   `target="raw_call:<name>"`, set `Node.language = "<name>"`).
2. In its `__init__.py`, define `PACK`, subclassing `cgis.core.extractor.LanguagePack` (mark methods `@override`)
   (`name`, `extensions`, `code_fence`, `is_test`, `scope_of`, `extractor`, `project_reader`,
   `index_facts`, `classify`, `resolution`). `resolution()` returns `ResolutionRules`
   (`cgis.core.resolution`): type normalizers, steps at a position (`after:import_map`), a
   fallback with its confidence, `reconcile_import`, and optionally `dispatch` (which methods a call through
   another type's may run; default: class hierarchy) — all reading the index as `ResolutionView`.
   Metadata other files resolve against goes in the manifest's `cross_file_metadata`. `classify` answers for references from the pack's own files and
   runs the whole chain; a source with no pack gets the generic tail in `SymbolIndex.classify_fqn`.
3. Add one `Manifest` to `MANIFESTS` in `src/cgis/languages/__init__.py`, with a `load` function that
   imports the pack statically inside it (lazy, yet visible to the review-fingerprint walk). Nothing
   else names the pack: CLI, MCP server, guardian and scripts all go through the registry, and the
   `surface-packs` forbidden-edge gate fails if a surface imports a pack directly.
4. A pack never imports another pack or `cgis.storage` (`cross-pack`, `pack-isolation` gates).
5. A pack's `__init__` imports no grammar: import the extractor inside `extractor()`. Config reading
   goes in a `ProjectReader` (`cgis.core.project`) whose facts are data keyed by `ingest_state`
   key — the collector (`cgis.import_names`), not the pack, writes them. A key other than the four
   built-ins (`_FACT_SHAPES`) travels as `ImportNames.extra`: recorded, compared, and merged into the
   `state` the pack's `index_facts` reads. Its value must read back equal from JSON (no tuples).
6. A FILE node's id comes from `file_node_id`, its definitions' namespace from `scope_of`; they differ
   only where several files share a scope (Go), which the FILE node declares as `metadata["scope"]`.

## Type Discipline

MyPy runs in strict mode (`strict = true`). All functions need full annotations including return types. The Pydantic plugin is active — use `model_copy(update={...})` for immutable model updates.

## Test Layout

```
tests/unit/           ← pure unit tests, no I/O
tests/integration/    ← (empty, planned)
tests/self_parsing/   ← (empty) intended to validate the engine by parsing itself
```

The self-parsing test (feeding `src/cgis/` to the ingestion pipeline and asserting the resulting graph matches actual structure) is the canonical correctness validation described in the blueprint.
