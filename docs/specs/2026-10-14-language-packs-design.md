# Language packs and a two-plane topology — design

Date: 2026-10-14. Status: proposed. Closes #146 on completion.

## Why

Two problems turned out to be one.

**The domain-level drift gate cannot be enforced (#146).** Since #144, the
quotient graph (each cgis domain collapsed to a node) has been scored
observe-only against `pipeline_stage`. It measured 0.700 in June and 0.644 on
2026-10-14 (b0d67f8), with a tolerance of 0.15. No template fits:
`orchestrator` scores 0.527 and `layered_dag` 0.492. The reason is the shape,
not the template. Cgis is two different structures over one core, and a
single triad census averages them into neither:

```
core            ← every package depends on it; it depends on nothing
storage         → core
extractors      → core
resolver        → core, storage (8 edges)
pipeline        → extractors, resolver, storage, import_names        build plane
query           → core, storage, extractors (3 edges)                read plane
guardian        → query, storage, extractors
cli, api        → query, storage, pipeline, extractors, guardian
import_names    → workspaces, tsconfig_paths, npm_/python_dependencies, storage
```

(Imports and calls between top-level packages, cgis at b0d67f8.)

**Adding a language means touching the whole system.** Knowledge about each
language is spread over five shared modules. `resolver/indices.py` has about
65 Python-specific and 65 TS-specific references; `symbols.py`, `engine.py`,
`pipeline.py` and `import_names.py` have dozens more. Four ecosystem readers
sit at the package root (`tsconfig_paths`, `workspaces`, `npm_dependencies`,
`python_dependencies`). `extractors/registry.py` already says "adding a
language should touch this file and nothing else", but that is true only for
parsing.

## Goals

1. A language is one package, `cgis/languages/<lang>/`, plus one line in a
   registry. Adding Go, Rust, PHP or Astro does not edit the core, the
   resolver or the pipeline.
2. Drift describes the real shape: one check per plane, plus forbidden edges.
   An enforced domain-level gate replaces the observe-only quotient.
3. The leaking edges `resolver → storage` and `query → extractors` are gone.

**Success:** the Go pilot (step 7) lands with a diff confined to
`languages/go/`, the registry line and tests. Both plane checks are enforced.
The forbidden-edge allowlist is empty.

## Non-goals

- Changing the graph format, FQN rules or MCP tool surface. The refactor is
  internal, and steps 1–5 must leave every graph with identical node and edge sets.
- Astro (#227), PHP and Rust. They follow the contract once Go has proven it.
- Revising the drift formula (#554).
- A second storage backend, or new surfaces (approach C, rejected as YAGNI).

## Architecture

Two planes over a shared core:

```
                 core (graph model, FQN helpers, Node/Edge)
                    ▲                                    ▲
   ── build ──      │                    ── read ──      │
   pipeline ─► languages/<py|ts|go> ─► resolver ─► storage ─► query ─► cli | api | guardian
              (parser + project      (language-free
               reader + rules)        engine)
```

- **Build plane.** `pipeline` orchestrates: it asks the registry for each
  file's pack, runs its extractor and project reader, hands nodes, edges and
  project facts to the resolver, and persists the result.
- **Read plane.** `storage → query → surfaces`, layered.
- **Core.** Model types and FQN utilities. It imports nothing from cgis.

### The `LanguagePack` contract

```python
class LanguagePack(Protocol):
    name: str                         # "python", "typescript", "go"
    extensions: tuple[str, ...]       # (".py",), (".ts", ".tsx"), (".go",)
    code_fence: str

    def extractor(self, source_roots: list[str]) -> BaseExtractor: ...
    def module_fqn(self, path: str, source_root: str | None) -> str: ...
    def project_reader(self, root: Path) -> ProjectReader: ...
    def classify(self, fqn: str, facts: ProjectFacts) -> NodeNamespace | None: ...
    def call_rules(self) -> Sequence[CallRule]: ...
```

| Part | Replaces | Notes |
|---|---|---|
| `extensions`, `extractor`, `module_fqn`, `code_fence` | `extractors/registry.py` `Language` | `module_fqn` must stay the function the extractor uses for FILE ids (existing round-trip test). |
| `project_reader` | `import_names.py`, `workspaces.py`, `tsconfig_paths.py`, `npm_dependencies.py`, `python_dependencies.py` | It notes manifests during the walk and configs above the root (#555), and yields `ProjectFacts`. The facts are serialisable and recorded in the store under today's keys, so `ImportNames.differ_from` and freshness keep working on existing graphs. |
| `classify` | `classify_fqn` branches in `resolver/indices.py`, `resolver/js_builtins.py` | It returns STDLIB/EXTERNAL for what the pack recognises, or None to defer to the generic rules. |
| `call_rules` | language branches in `resolver/symbols.py` (`self.x`, `Annotated` aliases, npm package calls, …) | A `CallRule` declares a hook point (`before_import_map`, `after_import_map`, `on_receiver`). The engine runs generic steps and pack rules in a fixed order; with no rule the generic chain is unchanged. |

**Registry.** `cgis/languages/__init__.py` lists the packs. It is the only
module outside `languages/` that names a language. `pipeline`, `query` and
`guardian` ask it "whose file is this?" and nothing else.

**Rules for packs.** A pack does not import another pack. Shared TS logic that
Astro will need moves to a shared module, or the Astro pack declares it
extends the TS pack through the registry. A pack never touches `storage`:
anything beyond nodes reaches the resolver as `ProjectFacts`.

## Drift gates

Domains after migration (`docs/ontology/patterns.yaml`):

| Domain | Template | Notes |
|---|---|---|
| `core` | `pure_utility` | new |
| `languages.python`, `languages.typescript`, `languages.go` | `layered_dag`, one shared tolerance | replaces `extraction` |
| `resolver` | `pipeline_stage` | unchanged (engine → symbols → indices) |
| `pipeline` | `orchestrator` | unchanged |
| `storage` | `pure_utility` | unchanged |
| `query` | `layered_dag` | unchanged |
| `cli`, `api`, `guardian` | hygiene / as today | unchanged |

Project-level checks replace the single `project_level` quotient binding:

1. **Build plane:** the quotient over `{pipeline, languages.*, resolver,
   storage}` against `orchestrator`.
2. **Read plane:** the quotient over `{storage, query, guardian, cli, api}`
   against `layered_dag`.
3. **Forbidden edges:** a new list in `patterns.yaml` of `from → to` prefix
   pairs that no IMPORTS/IMPORTS_SYMBOL/CALLS edge may cross:

   ```yaml
   forbidden_edges:
     - {from: "cgis.languages.*", to: "cgis.languages.*", unless: same_pack}
     - {from: "cgis.resolver",    to: "cgis.storage"}
     - {from: "cgis.query",       to: "cgis.languages"}
     - {from: "cgis.core",        to: "cgis"}            # core imports nothing
   ```

   A violation is reported as the edge, with file and line. An allowlist of
   today's violations (`resolver → storage` ×8, `query → extractors` ×3) can
   only shrink: a test fails if it grows, and also if it lists an edge that
   is no longer there.

**Why per plane.** A triad census averages everything in the quotient. The
build plane is a fan-out from `pipeline`; the read plane is layers. Each plane
is homogeneous, so a template can describe it. A single leaking edge is what
averaging hides, and check 3 catches it directly.

**Enforcement.** Check 3 is enforced from step 0. Checks 1 and 2 run
observe-only through the refactor, and become enforced at step 6 with a
tolerance set from the measured value, as domain tolerances are today. The
old project-level binding is deleted then, and #146 closes.

## Migration

**Safety net, written first.** The input must stay fixed while the code
changes. A test that ingests cgis's own `src/` would compare a refactored
cgis reading refactored source, which proves nothing.

- `tests/fixtures/graph_identity/` is a checked-in multi-language corpus:
  Python packages with DI aliases, ABC overrides and src-layout; a TS
  monorepo with `tsconfig` paths, workspaces, pnpm and npm dependencies; and
  configs above a sub-directory root.
- `tests/unit/test_graph_identity.py` ingests the corpus and compares the
  node and edge sets (ids, types, namespaces, targets, confidences) with a
  recorded snapshot. The snapshot is regenerated only by a step that is
  meant to change behaviour (step 7). Steps 1–5 must keep it unchanged.
- `scripts/graph_identity.py <corpus> <db-before> <db-after>` diffs two
  ingests. Every PR in steps 1–5 runs it in its description on cgis `src/`
  at a pinned sha (715cd9a, the A/B pin) and on owner-api @ 8de2245, with
  `main`'s cgis and the branch's cgis over the same input.

| Step | Change | Behaviour |
|---|---|---|
| 0. Measure and guard | Graph-identity corpus, snapshot test and diff script. Forbidden-edge check with the shrinking allowlist. Plane quotients measured on today's code and printed observe-only. | unchanged |
| 1. Skeleton | `languages/` with the protocol. `python` and `typescript` packs wrap today's extractors. `extractors/registry.py` becomes the pack registry. Language-specific extractor modules move under their pack. | unchanged |
| 2. Project readers | `python_dependencies` → `languages.python`; `npm_dependencies`, `workspaces`, `tsconfig_paths` → `languages.typescript`. `import_names.py` becomes a collector of per-pack `ProjectFacts`; store keys are kept. | unchanged |
| 3. Classification | stdlib/builtin/external knowledge → `classify` per pack; `indices.py` keeps the generic frame. | unchanged |
| 4. Call rules | Language steps in `symbols.py` → `call_rules`, with explicit hook points. | unchanged |
| 5. Leaking edges | `resolver → storage` and `query → extractors` removed; the allowlist is empty. | unchanged |
| 6. Gates | Plane checks enforced; old project-level binding deleted; #146 closed. | — |
| 7. Go pilot | `languages/go/`: tree-sitter-go extractor; `go.mod`/`go.work` reader (module path; required modules are EXTERNAL); `classify` (a standard-library import path has no dot in its first segment); a receiver rule for method calls. | new |

Each step is one PR with the full gate, the independent reviewer and the
identity test. Steps 1–5 are mergeable one at a time.

**Step 7 is the contract's exam.** Its acceptance is the diff: only
`languages/go/`, the registry line, the dependency on `tree-sitter-go` and
tests. If Go needs an edit anywhere else, the contract is missing something.
The contract gets fixed, and the change is made in the contract, not worked
around in Go.

## Risks and open questions

- **Template fit (#554).** It is unknown whether `orchestrator` and
  `layered_dag` describe the planes well enough to enforce. Step 0 measures
  them on today's code. If a plane is far from every template even after the
  leak fixes, a decision is needed before step 6: a new template, or keeping
  that plane observe-only and enforcing only forbidden edges.
- **Self-drift and god-object baselines move.** Moving modules changes domain
  membership. The `extraction` domain becomes `languages.*`, and
  `resolver/indices.py` shrinks. Each step re-measures; a tolerance may only
  go down, or be set for a new domain with the measurement in the commit
  message (the existing ratchet rule).
- **Hook points in `call_rules`** are the least certain part of the contract.
  Today's language branches are interleaved with generic steps inside
  `_resolve_local_type_call`, `resolve_global_call` and `resolve_self_call`.
  Step 4 extracts them one at a time, and the identity test decides whether
  the order is preserved.
- **Incremental ingest.** `ProjectFacts` must keep the `_CROSS_FILE_METADATA`
  and `ImportNames.differ_from` semantics. A facts change must still trigger
  the same rebuilds; the tests in `test_configs_above_root.py` and
  `test_annotated_alias_receiver.py` cover this.
- **Performance.** A registry lookup per file and a rule dispatch per call
  edge. Measure on the owner-api ingest at steps 1 and 4; the budget is no
  more than 5% slower.
