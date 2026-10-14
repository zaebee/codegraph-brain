# Language packs and an enforced topology — design

Date: 2026-10-14. Status: proposed (revision 2, after Gemini and an
independent design review). Closes #146 on completion.

## Why

Two problems turned out to be one.

**The domain-level drift gate cannot be enforced (#146).** Since #144, the
quotient graph (each cgis domain collapsed to a node) has been scored
observe-only against `pipeline_stage`. It measured 0.700 in June and 0.644 on
2026-10-14 (b0d67f8), with a tolerance of 0.15. Measured on the same commit,
no template fits, whether the whole quotient or the planes inside it:

| quotient | `pipeline_stage` | `orchestrator` | `layered_dag` |
|---|---|---|---|
| whole project | 0.644 | 0.527 | 0.492 |
| build plane `{pipeline, extractors, resolver, storage}` | — | 0.238 | — |
| read plane `{storage, query, guardian, cli, api}` | 0.677 | 0.677 | 0.676 |

The build plane comes close to `orchestrator` once its one leak goes (0.018).
But with one hub and a few leaves, `orchestrator` only says "no leaf-to-leaf
edge", which a forbidden-edge rule says directly. The read plane is not
layered: `cli`, `api` and `guardian` reach `storage` directly beside `query`
(25, 27 and 13 edges). Every such triad is a layer-skipping triangle, which
`layered_dag` excludes by design. Both planes also fail hygiene on
`unresolved_ratio` (0.34 and 0.25 against a maximum of 0.2). A triad census
cannot be the enforced gate for this topology.

The real dependencies (imports and calls between top-level packages):

```
core            ← every package depends on it; it depends on nothing
storage         → core
extractors      → core
resolver        → core, storage (8 edges, all from resolver/uplift.py)
pipeline        → extractors, resolver, storage, import_names        build plane
query           → core, storage, extractors (3: render/mermaid.py → language_for)
guardian        → query, storage, extractors
cli, api        → query, storage, pipeline, extractors, guardian
import_names    → workspaces, tsconfig_paths, npm_/python_dependencies, storage
```

**Adding a language means touching the whole system.** Knowledge about each
language is spread over five shared modules: `resolver/indices.py` alone
holds about 65 Python-specific and 65 TS-specific references. Four ecosystem
readers sit at the package root. `api/mcp_server.py` and
`scripts/generate_health.py` list extractors by hand, so a new language would
be invisible to MCP ingest. FQNs assume one file = one namespace unit, which
Go breaks: a Go package is a directory.

## Goals

1. A language is one package, `cgis/languages/<lang>/`, plus one registry
   entry. Adding Go does not edit the core, the resolver, the pipeline or a
   surface.
2. The topology is enforced by **forbidden edges**, each with an allowlist of
   today's violations that can only shrink. #146 closes on that gate.
3. Each language decides its namespace unit: file for Python and TS, package
   directory for Go.

**Success:**
- the Go pilot (step 7) lands with a diff confined to `languages/go/`, its
  registry entry, the `tree-sitter-go` dependency and tests;
- the forbidden-edge gate is enforced in CI;
- the `resolver → storage` and `query → languages.<pack>` allowlists are
  empty.

## Non-goals

- Changing the graph format, FQN rules or MCP tool surface for Python and TS.
  Steps 1–5 must leave every stored graph identical (see Migration).
- Making the read plane layered. "Surfaces reach storage only through query"
  is a rule with a shrinking allowlist (about 65 edges), paid down when those
  files are touched anyway, not in one refactor.
- Enforcing plane quotients. They stay observe-only reports; see Drift gates.
- Astro (#227), PHP, Rust; revising the drift formula (#554); a second storage
  backend.

## Architecture

Two planes over a shared core:

```
                 core (graph model, FQN helpers, Node/Edge)
                    ▲                                    ▲
   ── build ──      │                    ── read ──      │
   pipeline ─► languages/<py|ts|go> ─► resolver ─► storage ─► query ─► cli | api | guardian
              (parser, project       (language-free
               reader, rules)         engine)
```

- **Build plane.** `cgis/pipeline/` (today's `pipeline.py` becomes a package)
  orchestrates: it asks the registry for each file's pack, runs its extractor
  and project reader, hands nodes, edges and facts to the resolver, persists
  the result, then runs the post-write passes: semantic uplift and
  `refresh_dispatch`. Both move here from `resolver/` (step 5). They read and
  write the store, which is what the resolver must not do.
- **Read plane.** `storage → query → surfaces`.
- **Core.** Model types and FQN utilities. It imports nothing else from cgis.

  > **Implementation note (steps 1–2):** the pack contract — `BaseExtractor`,
  > `ModuleNamer`, `LanguagePack` (`cgis.core.extractor`), `ProjectReader` and
  > `ProjectFacts` (`cgis.core.project`) — lives in core too. A protocol inside the
  > registry formed a cycle with the packs that subclass it (#597).

### Registry

`cgis/languages/__init__.py` holds one lightweight **manifest** per pack: its
name, module path, extensions and `cross_file_metadata` keys. Everything the
pipeline needs before it meets a file is declared there, so nothing imports a
pack until a file of that language turns up. Loading every grammar eagerly
would undo #506 (Gemini on #593). It answers:

- `pack_for_path(path) -> LanguagePack | None`, by extension; this imports
  that one pack on first use;
- `pack_for_node(node) -> LanguagePack | None`, by `Node.language`, never by
  re-sniffing a suffix;
- `extractor_for(path, source_roots) -> BaseExtractor | None`, which
  `IngestionPipeline` calls per file instead of taking a ready mapping of
  every extractor; each pack's extractor is built once, when its first file
  is met;
- `cross_file_metadata()`, the union of the manifests' keys, without
  importing any pack.

The CLI, MCP server, health script and reviewer all use the registry; none
names an extractor class. Surfaces and `query` may import
`cgis.languages` (the registry) but not `cgis.languages.<pack>`.

> **Implementation note (steps 1–2):** manifests load their pack through a
> function holding a *static* import, never `importlib`, which the
> review-fingerprint walk cannot follow (#597). Pack modules are imported up
> front, because every project reader must see configs before the first file
> of its language; what stays lazy is the grammar, which a pack imports only
> inside `extractor()` (step 2, ruling S2).

### The `LanguagePack` contract

```python
class LanguagePack(Protocol):
    name: str                                   # "python", "typescript", "go"
    extensions: tuple[str, ...]
    code_fence: str

    # Files and namespaces
    def is_test(self, path: str) -> bool: ...
    def scope_of(self, path: str, source_root: str | None) -> str: ...
    def extractor(self, source_roots: list[str]) -> BaseExtractor: ...

    # Project facts
    def project_reader(self, root: Path) -> ProjectReader: ...

    # Index and classification
    def index_facts(self, nodes: Iterable[Node], facts: ProjectFacts) -> IndexFacts: ...
    def classify(self, fqn: str, ctx: ClassifyContext) -> NodeNamespace | None: ...

    # Resolution
    def resolution(self) -> ResolutionRules: ...

    # Incremental ingest
    cross_file_metadata: Mapping[NodeType, tuple[str, ...]]
```

**Files and namespaces**

- `is_test` replaces the Python-only `is_test_path` and the pipeline's
  `_TEST_FILE_PATTERN` (pipeline.py:447). For Go it matches `_test.go`.

  > **Implementation note (step 1):** those are two rules, not one — the walk's
  > *skip* (`foo.test.ts` is never read) became `Manifest.never_ingested` and the
  > registry's `skips()`; the node *flag* is the pack's `is_test`. Merging them
  > would have dropped every `tests/` directory from the graph (#597, ruling R1).
- `scope_of` returns the namespace unit a file's definitions live in:
  - Python and TS return today's module FQN for the file, so nothing changes;
  - Go returns the package directory, so `func (s *Server) Run()` in
    `server.go` and `type Server` in `types.go` both live under
    `<pkg>.Server`.

  The extractor builds every id from it. The generic code that today assumes
  file = unit uses the scope instead:
  - the global-symbol tiebreak, "same file" → "same scope" (symbols.py:305);
  - `_owning_module` (fingerprint.py:166);
  - `_public_names_under` (indices.py:663);
  - the guardian collector (collector.py:219).

  For Python and TS, scope and file coincide, so behaviour is unchanged.

**Project facts**

```python
class ProjectReader(Protocol):
    def note(self, path: Path) -> bool: ...          # a file the walk passed; True if taken
    def note_above(self, directories: list[Path], bounded: bool) -> None: ...  # #555
    def facts(self) -> ProjectFacts: ...

class ProjectFacts(Protocol):                        # frozen, JSON-serialisable
    def record(self, store: SQLiteStore) -> None: ...      # under today's store keys
    def differs_from(self, store: SQLiteStore) -> bool: ... # a change forces a rebuild
```

> **Implementation note (step 2):** `record`/`differs_from` contradict "a pack
> never touches storage" below, which the `pack-isolation` gate enforces. As
> built, `ProjectFacts.state()` returns the facts keyed by their `ingest_state`
> key, and the collector (`cgis.import_names.ImportNames`) records and compares
> them through the store — same keys, no pack-to-storage edge (ruling S1).

The Python pack's facts are today's dependency roots. The TS pack's are npm
dependencies, workspace packages (package.json and pnpm) and tsconfig path
aliases. `import_names.py` becomes the collector that asks each pack.

**Index and classification**

- `index_facts` holds what `IndexBuilder.build` derives from all nodes today
  for one language: Python's `layout_prefixes`, `external_roots`,
  `internal_roots` with src/lib stripping, `dependency_heads`, the
  `__init__` root and star-import expansion (indices.py:402, 642, 671,
  795–840). TS's include the per-file `bare_imports`/`bare_bindings`
  (indices.py:757).
- `classify` receives a `ClassifyContext`: the FQN's `source_file` (may be
  None), that file's FILE metadata, the project facts and the index facts.
  - **Preserved quirk:** today `classify_fqn(fqn)` called without a source
    file applies the Python reading (indices.py:287; called at symbols.py:188
    and 294, and indices.py:234). The registry keeps that: with no source, the
    Python pack classifies. Removing the quirk is a separate, measured change.

**Resolution.** The engine runs named steps in a fixed order. A pack
contributes to them; it does not get free hook points:

```python
class ResolutionRules(Protocol):
    type_normalizers: Sequence[TypeNormalizer]      # e.g. Annotated alias → T
    steps: Sequence[tuple[str, CallStep]]           # ("after:import_map", step)
    fallback: Fallback | None                       # (name, file) -> (target, confidence)
    reconcile_import: ImportReconciler | None       # resolve_import_target's language half
```

The generic chain, with today's positions of language logic:

1. `local_type`, with `type_normalizers` applied inside it, as
   `_through_annotated_alias` is today (symbols.py:285), and in
   `receiver_type_fqn` (symbols.py:63);
2. `import_map`;
3. pack steps positioned `after:import_map`, e.g. TS `_package_call`;
4. `global_symbols`, with the same-scope tiebreak;
5. `fallback`, e.g. JS builtins at confidence 0.8 (engine.py:172–176), not 1.0.

`reconcile_import` takes the language-split half of `resolve_import_target`
(indices.py:141–146). Step 4 of the migration extracts these one at a time.

**Incremental ingest.** `cross_file_metadata` lists, per node type, the
metadata keys other files resolve against. It is declared in the pack's
registry manifest, not read from the pack module, so collecting it imports
nothing. The pipeline's `_CROSS_FILE_METADATA` (pipeline.py:32) becomes the
union over the manifests:
`import_map`, `reexports`, `star_imports`, `self_types` and `annotated_type`
for Python, and so on per pack.

**Pack rules**

- A pack does not import another pack. Shared TS logic Astro needs moves to a
  shared module, or Astro declares in the registry that it extends TS.
- A pack never touches `storage`: everything beyond nodes reaches the
  resolver as `ProjectFacts` or `IndexFacts`.

## Drift gates

**The enforced gate is forbidden edges**, implemented by extending
`tests/self_parsing/test_architecture.py`, which already enforces three import
boundaries (lines 21–23), rather than adding a parallel list to
`patterns.yaml`. It runs over the cgis self-graph's IMPORTS, IMPORTS_SYMBOL and
CALLS edges between INTERNAL nodes.

| rule | today | after |
|---|---|---|
| `languages.<a>` → `languages.<b>`, a ≠ b | — | 0 |
| `languages.<pack>` → `storage`, `query`, `api`, `guardian`, `pipeline` | (extractors → storage etc. already 0) | 0 |
| `resolver` → `storage` | 8 (uplift.py) | 0 after step 5 |
| `query`, `guardian`, `cli`, `api` → `languages.<pack>` (registry allowed) | 3 (mermaid.py → `extractors.registry`) | 0 after step 1 |
| `cli`, `api`, `guardian` → `storage` (read through `query`) | about 65 | shrinking |
| `core` → any other cgis package | 0 (enforced today) | 0 |

**Matching.** A rule names a source prefix and a target prefix. `<pack>` is
one dot-segment after `cgis.languages.`, taken from the edge's own source and
target. So `languages.<a> → languages.<b>` fires when the two segments
differ, and an edge inside one pack never fires. An edge whose target is the
registry module `cgis.languages` itself matches no `<pack>` rule.

**Allowlist.** Each rule carries an allowlist of today's violations, keyed by
`(source module, target module)` with a count. The test fails when:

- an edge appears that no entry covers;
- a count grows;
- an entry no longer matches any edge, so the list cannot rot.

**Plane quotients** stay as observe-only reports in `test_drift.py`, printed
each run, for the record and for #554. The old `project_level` binding in
`patterns.yaml` is deleted at step 6, and #146 closes on the enforced
forbidden-edge gate.

**Domains** (`patterns.yaml`). `extraction` becomes one domain per pack
(`languages.python`, `languages.typescript`, `languages.go`), each
`layered_dag` with one shared tolerance, so a loose Go pack shows before
review. `resolution` is re-measured after uplift leaves it. `core` gets
`pure_utility`. The other domains are unchanged.

## Migration

**Safety net, written first.** The input must stay fixed while the code
changes. A test that ingests cgis's own `src/` would compare a refactored
cgis reading refactored source.

- `tests/fixtures/graph_identity/` is a checked-in corpus that covers:
  - Python: DI aliases, ABC overrides, star-import chains, `__init__`-rooted
    relative imports, a `src/` and a `lib/` layout, and a dependency root
    colliding with a package name (the alembic case);
  - TS: a monorepo with `tsconfig` paths, package.json and pnpm workspaces,
    npm dependencies, `.d.ts`, `node:` imports, shadowed globals;
  - configs above a sub-directory root.
- `tests/unit/test_graph_identity.py` ingests the corpus into a store and
  compares the **stored** graph, after uplift and `refresh_dispatch`, with a
  recorded snapshot:
  - nodes: id, type, namespace, file, lines, `language`, `is_test`,
    `is_generated`, and the full `metadata` (import_map, local_types,
    self_types, bare_*, annotated_type), which is exactly what steps 1–4
    relocate;
  - edges: id, source, target, type, confidence, weight, context, file,
    line;
  - the `ingest_state` keys.

  It runs twice, under two `PYTHONHASHSEED` values, so set-iteration order
  cannot pass by luck. It is an ordinary unit test, so CI runs it on every PR
  from step 0 on. **The Python/TS snapshot never changes, step 7 included.**
  Go gets a corpus and snapshot of its own, so the pilot cannot quietly move
  the existing languages.
- `scripts/graph_identity.py diff <db-a> <root-a> <db-b> <root-b>` diffs two
  ingests (`snapshot <corpus>` records one). Every PR in steps 1–5 reports it in its description, for cgis
  `src/` at 715cd9a and owner-api @ 8de2245, with `main`'s cgis and the
  branch's cgis over the same input.

| Step | Change | Behaviour |
|---|---|---|
| 0. Measure and guard | Identity corpus, snapshot test, diff script. Forbidden-edge rules in `test_architecture.py`, with today's allowlists. Plane quotients printed observe-only. | unchanged |
| 1. Skeleton and registry | `languages/` with the protocol; `python` and `typescript` packs wrap today's extractors and own `is_test` and `scope_of`. The lazy registry replaces `extractors/registry.py`. CLI, MCP server, health script, guardian and `mermaid.py` go through it. Moved modules are imported from their new paths, with test imports updated in the same PR (36 files) and no compatibility shims. The repo CLAUDE.md "adding an extractor" section is rewritten. The `extraction` domain in `patterns.yaml` is re-pointed at `cgis.languages`, with its tolerance re-measured. | unchanged |
| 2. Project readers | `ProjectReader`/`ProjectFacts` per pack; `import_names.py` collects. Store keys kept. | unchanged |
| 3. Index and classification | `index_facts` and `classify(fqn, ctx)` per pack; the no-source → Python rule kept in the registry. | unchanged |
| 4. Resolution steps | Named generic steps; type normalizers, positioned steps, fallback (with its 0.8) and import reconciliation per pack. `cross_file_metadata` per pack. | unchanged |
| 5. Build-plane passes | `pipeline.py` → `cgis/pipeline/`; `resolver/uplift.py` and `refresh_dispatch` move into it. `resolver → storage` allowlist empty; `resolution` domain re-measured. | unchanged |
| 6. Gates | Old `project_level` binding deleted; domains per pack; #146 closed. | — |
| 7. Go pilot | `languages/go/`, below. | new |

Each step is one PR with the full gate, the independent reviewer and the
identity report. Steps 1–5 are mergeable one at a time. Each step re-measures
the god-object and self-drift baselines. A tolerance may only go down, or be
set for a new domain with the measurement in the commit message. This is
already enforced: `docs/ontology/tolerances.lock` and
`test_tolerances_never_exceed_the_lock` (#151).

### Go pilot (step 7)

- **Parser:** `tree-sitter-go`.
- **Scope and tests:**
  - `scope_of` returns the package directory, relative to the module root;
  - `is_test` matches `_test.go`.
- **Project facts from `go.mod`/`go.work`:**
  - the module path;
  - required modules, which are EXTERNAL;
  - the replace directives that point inside the repository, which are
    internal.
- **classify:**
  1. check the own module path first (`module myapp` has no dot either);
  2. then the pseudo-package `C` (cgo);
  3. then "no dot in the first path segment" means standard library.
- **Ontology:** the Go extractor follows the same conventions as the others.
  Unresolved calls are emitted as `raw_call:<name>` for the resolver. Node
  and edge types come from the existing enums, with no new `NodeType`. The
  existing ontology-compliance tests run on the Go corpus.
- **Resolution:**
  - a receiver normalizer (`*Server` → `Server`);
  - package-qualified calls (`pkg.Run`) through the import map;
  - unqualified calls through the same-scope tiebreak;
  - struct embedding becomes EXTENDS, so the generic DISPATCHES pass covers
    promoted methods.

**Out of pilot scope**, recorded as follow-ups:

- **Implicit interface satisfaction.** It needs a method-set pass, which
  would add an optional `implementations()` contract element.
- **Generics beyond plain extraction.**
- **cgo beyond classification.**

**Acceptance is the diff:** only `languages/go/`, the registry entry, the
`tree-sitter-go` dependency and tests. If Go needs an edit anywhere else, the
contract is missing something. The contract gets fixed, and the change is
made there, not worked around in Go.

## Risks and open questions

- **Behaviour hidden in Python-only paths that Go also reaches.** For
  example, `resolve_layout`/`map_to_node_fqn` run for every language today,
  without a source-file gate (indices.py:229–236). Step 3 must gate them by
  pack. The identity test proves Python and TS unchanged; the Go pilot's own
  tests prove they no longer leak into Go.
- **Hook order.** Step 4 is the most delicate step. Extract one language
  branch per commit, with the identity test after each.
- **Baselines move.** Domain membership changes at steps 1 and 5, and
  `SymbolResolver` is at the god-object threshold. Steps follow the existing
  ratchet rule.
- **Imports of moved modules.** MCP server, CLI, scripts, autodoc and 36 test
  files. Step 1 updates them all; no shims.
- **Performance.** A lazy registry lookup per file and a step dispatch per
  call edge. Measure the owner-api ingest at steps 1 and 4; the budget is no
  more than 5% slower.
