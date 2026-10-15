# Language Packs — Step 7: The Go Pilot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Go as a third language pack (`cgis/languages/go/`) with its own corpus and snapshot, without moving the Python/TypeScript graphs. First make the three contract fixes that Go needs and Python/TS never exercised.

**Architecture:** Two PRs.

- **7a: contract fixes.** Python/TS graphs stay byte-identical.
  1. `LanguagePack.file_node_id(path, root)` separates "the FILE node of this file" from `scope_of`, "the namespace its definitions live in". The registry's consumers (guardian collector, guardian martian, mermaid, CLI) use the former.
  2. The resolver's same-file tiebreak becomes **same-scope**. The scope of a file is read from its FILE node (`metadata["scope"]`, defaulting to the node id), so Python/TS behave as before.
  3. Project facts a pack owns beyond the four built-in keys flow to its `index_facts` and are recorded and compared in `ingest_state`. Today `ImportNames._FACT_SHAPES` refuses them.
- **7b: the Go pack.**
  - `languages/go/` contains:
    - `naming` (scope and FILE id);
    - `extractor` (tree-sitter-go);
    - `project` (the `go.mod` reader);
    - `index` (facts, classify);
    - `resolution` (import reconciliation, receiver normalizer).
  - It also adds one manifest, the `tree-sitter-go` dependency, a Go corpus with its own snapshot, the `languages.go` drift domain at the shared 0.42, and the `_PLANES` build entry.
  - The 7b diff touches only those files. Anything else means the contract is still missing something: fix the contract in 7a, not in Go.

**Tech Stack:** Python 3.12, tree-sitter 0.25.2, tree-sitter-go 0.25.0 (verified: 336 KB wheel, parses with the current runtime), SQLite, pytest, ruff, mypy strict.

**Spec:** `docs/specs/2026-10-14-language-packs-design.md` (rev 3), "Go pilot (step 7)" and Migration row 7, plus every implementation note from steps 1–6. Outline: `docs/plans/2026-10-14-language-packs.md` "Step 7".

## Global Constraints

- **Gate:** `make format && make lint && make type-check && make pytest && make doc-coverage` passes before every commit. Join commands with `&&`; never use `;`, a bare newline after a heredoc, or a pipe on the gating command.
- **Static checks:** mypy strict on `src/`; docstring coverage ≥ 90%; Sonar cognitive complexity ≤ 15 per function.
- **7a:** `tests/fixtures/graph_identity/snapshot.json` is not modified, and main-vs-branch `graph_identity diff` reads `identical` on cgis `src` @715cd9a, owner-api @8de2245 and `ui/`.
- **7b:**
  - The Python/TS snapshot is not modified.
  - `git diff --stat main` lists only:
    - `src/cgis/languages/go/**`
    - `src/cgis/languages/__init__.py` (one manifest)
    - `pyproject.toml`, `uv.lock`
    - `tests/**go**`, `tests/fixtures/graph_identity_go/**`
    - `docs/ontology/patterns.yaml`, `docs/ontology/tolerances.lock`
    - `tests/self_parsing/test_drift.py` (`_PLANES`), `tests/unit/review_path_inventory.txt`
    - docs
- **Go output:** every node and edge type comes from the existing enums (no new `NodeType`), and unresolved calls are `raw_call:<name>`.
- **Drift:** the `languages.go` domain gets drift_tolerance **0.42**, the shared bar that `test_every_pack_has_a_domain_at_the_shared_tolerance` enforces. **Stop and report** if Go measures above it. Never raise the shared bar.
- **Disk:** the root disk is ~98% full. Keep scratch under the scratchpad and delete databases after use.
- **Test-count floor:** raise `.github/test-count-baseline` (now 3095) if the suite passes 3245. 7b likely will.
- **Attribution:** commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; PR bodies end with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- **Worktree:** `/home/zaebee/projects/ownima/cgb-step7`. Branch `feat/language-packs-step7a`, then `feat/language-packs-step7b` from main once 7a merges.

## Rulings

- **H1 — split the contract (user decision at plan time).**
  - The spec says `scope_of` returns a Go package directory. Its four consumers, though, use it to find a file's FILE node, and for Python and TypeScript those are the same thing.
  - `file_node_id(path, root)` is added. For Python/TS it returns `scope_of`; for Go it returns `<package scope>.<stem>_go`, so two files of one package keep distinct FILE nodes while their definitions share `<package scope>.<Name>`.
- **H2 — scope comes from the FILE node.**
  - The resolver tiebreak needs a file's scope, but the resolver has no source roots to recompute it.
  - The extractor writes `metadata["scope"]` on a FILE node only when the scope differs from the node id, which happens only for Go. The index reads `metadata.get("scope", node.id)`.
  - Python/TS: the scope is the FILE id, so the same-scope key is one-to-one with the old same-file key.
- **H3 — pack-owned project state.**
  - `ImportNames` keeps its four typed fields and gains `extra: dict[str, object]` for every other key a pack's facts declare. `_FACT_SHAPES` stays as the guard for the four, and an unknown key is now accepted into `extra`.
  - Comparison and recording go through two new public store methods, `get_state(key)` and `record_state(key, value)`, which are the existing private `_state_json`/`_record_state_json`.
  - `ResolverEngine`/`SymbolResolver`/`IndexBuilder.build` take an optional `project_state` mapping, merged into the `state` handed to `index_facts`.
  - This also begins #604's "language-named state threading" without removing it.
- **H4 — Go import paths reach node ids through `layout_prefixes`.**
  - The Go extractor records each import as its dotted path (`example.com/app/internal/server` → `example.com.app.internal.server`).
  - The Go index facts contribute the module path's dotted segments (`example`, `com`, `app`) as `layout_prefixes`. The generic `strip_layout_prefixes` then maps `example.com.app.internal.server.Run` to `internal.server.Run`.
  - *Cost:* `layout_prefixes` is one global set. A repository holding Python and Go whose Go module segments match Python import roots could see a Python import stripped. This is recorded as a follow-up, not handled in the pilot.
- **H5 — an internal package import edge points at a representative FILE node.**
  - A Go package has no node of its own (no new `NodeType`, and one MODULE node per package would be emitted by every file).
  - Go's `reconcile_import` maps an internal package import to the lexicographically first FILE node of that scope. This is deterministic and keeps package-level import topology.
  - It is approximate, and is documented as such.
- **H6 — receiver calls.** `r.m()`, where `r` is the method's receiver name, is emitted as `raw_call:self.m`. The generic `resolve_self_call` then walks `<scope>.T`'s methods and EXTENDS (embedding). The receiver normalizer (`*T` → `T`) is the Go pack's `type_normalizers` entry, applied to local variable types.
- **Pilot out of scope** (spec), each recorded as a follow-up issue in Task B6:
  - implicit interface satisfaction;
  - generics beyond plain extraction;
  - cgo beyond classification;
  - `go.work` multi-module;
  - H4's shared prefix set.

## Review Focus

- **7a: a consumer that still uses `scope_of` to find a FILE node.** After 7a, every one uses `file_node_id`. Task A1 greps for this. A Go file must slug, be reviewed and be targeted by its FILE node.
- **7a: the tiebreak on Python/TS.** Identical: the identity diff on three trees, plus Task A2's `test_same_scope_is_same_file_for_python`.
- **7b: two files of one package defining methods on a type declared in the other file.** The methods hang under one `<scope>.T` (Task B3 corpus, `test_methods_in_another_file_share_the_type`).
- **7b: a `main.go` at the module root.** It gets a unique FILE id, and its definitions take the package clause's name (Task B3, `test_root_package_takes_its_clause_name`).
- **7b: a Python-only or TS-only repository.** It never imports `tree_sitter_go`: the Go pack's `__init__` loads no grammar, as step 2's S2 requires, and the existing `_cli_ingest_loads` tests get a Go-free assertion (Task B4).

---

## Phase A — contract fixes (PR 7a; graph-identical)

### Task A1: `file_node_id` separates a file's node from its scope

**Files:**
- Modify:
  - `src/cgis/core/extractor.py` (`LanguagePack.file_node_id`)
  - both pack `__init__.py`
  - `src/cgis/languages/__init__.py` (registry `file_node_id`)
  - `src/cgis/cli.py:842`
  - `src/cgis/guardian/collector.py:219`
  - `scripts/guardian_martian.py:304`
  - `src/cgis/query/render/mermaid.py:82`
- Test: `tests/unit/test_languages.py`

- [ ] **Step 1: Failing tests.** Append:

```python
from cgis.languages import file_node_id


def test_file_node_id_is_the_scope_for_python_and_typescript() -> None:
    """Step 7 ruling H1: they coincide for py/ts, so nothing changes for them."""
    for path in ("pkg/mod.py", "app/handler.ts", "components/index.tsx", "src/cgis/pipeline"):
        assert file_node_id(path, None) == scope_of(path, None)
```

- [ ] **Step 2: RED.**
  - Run: `uv run pytest tests/unit/test_languages.py -q -k file_node_id`
  - Expected: `ImportError: cannot import name 'file_node_id'`.

- [ ] **Step 3: Implement.**
  1. Add to `LanguagePack`:

```python
    def file_node_id(self, path: str, source_root: str | None) -> str:
        """The id of this file's FILE node. Equal to `scope_of` where a file is its own scope."""
        ...
```

  2. In both packs, add `@override` returning `self.scope_of(path, source_root)`.
  3. In the registry, mirror `scope_of`:

```python
def file_node_id(path: str, source_root: str | None) -> str:
    """The id of a path's FILE node, by its own pack; a path no pack reads gets the Python rule."""
    pack = pack_for_path(path) or _load("python")
    return pack.file_node_id(path, source_root)
```

  4. Re-point the four consumers from `scope_of` to `file_node_id`: the registry function for the CLI, `pack.file_node_id` for the others.
  5. Check with `git grep -n "scope_of(" -- src scripts`. Expected: only the definitions and the packs' own `scope_of`.

- [ ] **Step 4: GREEN + gate + commit.**
  - Message: `refactor(languages): file_node_id — a file's node, apart from its scope (step 7a)`.

### Task A2: the tiebreak prefers the same scope

**Files:**
- Modify: `src/cgis/resolver/indices.py` (`IndexBuilder` keys `file_global_symbols` / `file_variable_symbols` by scope; new `file_scopes: Mapping[str, str]`, normalized path → scope)
- Modify: `src/cgis/resolver/symbols.py:236,303` (look up by the source file's scope)
- Test: `tests/unit/test_resolver_indices.py`

- [ ] **Step 1: Failing tests.**

```python
def test_scope_defaults_to_the_file_node_id() -> None:
    """Python/TS: a file's scope is its FILE node, so same-scope is same-file (step 7 H2)."""
    file_node = _node("pkg.mod", node_type=NodeType.FILE, file_path="pkg/mod.py")
    index = IndexBuilder().build([file_node, _node("pkg.mod.f", file_path="pkg/mod.py")])
    assert index.file_scopes["pkg/mod.py"] == "pkg.mod"
    assert index.file_global_symbols[("pkg.mod", "f")] == ["pkg.mod.f"]


def test_a_file_can_declare_a_wider_scope() -> None:
    """Go: two files of one package share a scope through FILE metadata."""
    a = _node("srv.a_go", node_type=NodeType.FILE, file_path="srv/a.go", metadata={"scope": "srv"})
    b = _node("srv.b_go", node_type=NodeType.FILE, file_path="srv/b.go", metadata={"scope": "srv"})
    index = IndexBuilder().build(
        [a, b, _node("srv.Run", file_path="srv/a.go"), _node("other.Run", file_path="other/x.go")]
    )
    assert index.file_scopes["srv/b.go"] == "srv"
    assert index.file_global_symbols[("srv", "Run")] == ["srv.Run"]
```

  Adapt the `_node` helper call if its signature differs; read it first.

- [ ] **Step 2: RED.** Expected: `AttributeError: file_scopes`.

- [ ] **Step 3: Implement.**
  1. In `build`, make a first pass over FILE nodes to compute `file_scopes[normpath(file_path)] = node.metadata.get("scope") or node.id`.
  2. In `_index_by_name`, key by `file_scopes.get(normpath(file_path), normpath(file_path))`. The fallback keeps a node whose file has no FILE node keyed as before.
  3. In `symbols.py`, both tiebreaks look up `(index.file_scopes.get(file_path, file_path), name)`.
  4. Add the field to `SymbolIndex` with a comment. It is required, like `facts`.

- [ ] **Step 4: GREEN.**
  - Run: `uv run pytest tests/unit/test_resolver_indices.py tests/unit/test_resolver.py tests/unit/test_graph_identity.py tests/self_parsing -q`
  - Expected: all pass.
- [ ] **Step 5: Gate + commit.**
  - Message: `refactor(resolver): the global-symbol tiebreak prefers the same scope (step 7a)`.

### Task A3: pack-owned project state

**Files:**
- Modify:
  - `src/cgis/import_names.py` (`ImportNames.extra`; `from_facts` routes unknown keys there, still type-checking the four)
  - `src/cgis/storage/sqlite_store.py` (public `record_state`/`get_state`, delegating to the private pair)
  - `src/cgis/pipeline/__init__.py` (pass `project_state=import_names.extra` to `ResolverEngine`)
  - `src/cgis/resolver/engine.py`, `symbols.py`, `indices.py` (optional `project_state`, merged into `state`)
- Test: `tests/unit/test_project_facts.py`

- [ ] **Step 1: Failing tests.** Replace `test_a_key_the_collector_cannot_store_fails`, written in step 2. Its premise changes by design (H3), so the replacement must say so in its docstring.

```python
def test_a_pack_owned_key_is_carried_recorded_and_compared(tmp_path: Path) -> None:
    """Step 7 H3: a fact under a key the four built-ins do not cover travels as `extra`."""
    names = ImportNames.from_facts([_Facts({"go_module": {"path": "example.com/app"}})])
    assert names.extra == {"go_module": {"path": "example.com/app"}}
    with SQLiteStore(str(tmp_path / "g.db")) as store:
        names.record(store)
        assert names.differ_from(store) is False
        changed = ImportNames.from_facts([_Facts({"go_module": {"path": "example.com/other"}})])
        assert changed.differ_from(store) is True


def test_a_built_in_key_of_the_wrong_shape_still_fails() -> None:
    facts = [_Facts({"npm_dependencies": ("react",)})]
    with pytest.raises(TypeError, match="npm_dependencies"):
        ImportNames.from_facts(facts)


def test_pack_state_reaches_index_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    """IndexBuilder merges a pack's extra state into what index_facts receives (H3)."""
    import cgis.resolver.indices as indices

    seen: dict[str, object] = {}
    real = indices.pack_index_facts

    def recording(nodes: object, state: dict[str, object]) -> object:
        seen.update(state)
        return real(nodes, state)

    monkeypatch.setattr(indices, "pack_index_facts", recording)
    indices.IndexBuilder().build([], project_state={"go_module": {"path": "example.com/app"}})
    assert seen["go_module"] == {"path": "example.com/app"}
    assert "python_dependencies" in seen  # the built-ins are still there


def test_a_pack_key_that_shadows_a_built_in_fails() -> None:
    from cgis.resolver.indices import IndexBuilder

    with pytest.raises(ValueError, match="npm_dependencies"):
        IndexBuilder().build([], project_state={"npm_dependencies": []})
```

Check the exact built-in state key names in `IndexBuilder.build` (around `indices.py:300-359`) before running RED. If they differ from `python_dependencies` / `npm_dependencies`, use the real names. Ledger that as a ruling.

- [ ] **Step 2: RED.** Expected: `AttributeError: extra`, and so on.
- [ ] **Step 3: Implement as H3 describes.**
  - `ImportNames.extra` defaults to an empty dict.
  - `differ_from` adds `any((store.get_state(k) or None) != v for k, v in self.extra.items())`.
  - `record` writes each extra key.
  - The engine/resolver/builder `project_state` defaults to `None` and is merged into `state` after the four built-ins. A clash with a built-in key is a `ValueError`.
- [ ] **Step 4: GREEN + identity.** Python/TS packs declare no extra keys, so `extra` is empty and nothing changes. Run the identity snapshot test and the full gate.
- [ ] **Step 5: Commit.**
  - Message: `feat(languages): project state a pack owns reaches its index facts and ingest_state (step 7a)`.

### Task A4: identity, review, PR 7a

- [ ] Identity diff on three trees (as in step 5's plan, Task 3). Expected: `identical` three times.
- [ ] Spec note under "Go pilot": H1–H3.
- [ ] Independent reviewer with execution tools, given the disk warning. Fix Critical/Important findings test-first.
- [ ] PR `refactor(languages): step 7a — contract fixes for a third language`. Merge on the user's approval. Then branch 7b from main.

---

## Phase B — the Go pack (PR 7b)

### Task B1: dependency, naming, manifest, empty pack

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (`uv add "tree-sitter-go>=0.25.0"`)
- Create: `src/cgis/languages/go/__init__.py`, `src/cgis/languages/go/naming.py`
- Modify: `src/cgis/languages/__init__.py` (manifest)
- Test: `tests/unit/test_go_naming.py`

- [ ] **Step 1: Failing tests.**

```python
from cgis.languages import is_supported, pack_for_path
from cgis.languages.go.naming import GO_LANG, file_node_id, scope_of


def test_scope_is_the_package_directory() -> None:
    assert scope_of("internal/server/server.go", None) == "internal.server"
    assert scope_of("src/internal/server/server.go", "src") == "internal.server"


def test_root_files_have_an_empty_directory_scope() -> None:
    """The extractor names root definitions by the package clause; scope_of cannot see it."""
    assert scope_of("main.go", None) == ""


def test_file_node_id_is_unique_per_file() -> None:
    assert file_node_id("internal/server/server.go", None) == "internal.server.server_go"
    assert file_node_id("internal/server/types.go", None) == "internal.server.types_go"
    assert file_node_id("main.go", None) == "main_go"


def test_go_is_registered() -> None:
    assert is_supported("cmd/app/main.go")
    pack = pack_for_path("cmd/app/main.go")
    assert pack is not None and pack.name == GO_LANG == "go"
    assert pack.is_test("server_test.go") and not pack.is_test("server.go")
```

- [ ] **Step 2: RED.** Expected: `ModuleNotFoundError: cgis.languages.go`.
- [ ] **Step 3: Implement.**
  - `naming.py` is pure (no tree-sitter):
    - `GO_LANG = "go"`;
    - `scope_of` (source root stripped as `file_path_to_module_fqn` does, then the directory dotted);
    - `file_node_id` (`f"{scope}.{stem}_go"` or `f"{stem}_go"` at the root);
    - `is_test` (`name.endswith("_test.go")`).
  - `go/__init__.py`: a `GoPack(LanguagePack)` with `@override` methods. `extractor` imports `.extractor` lazily (S2). `code_fence` returns `"go"`. `project_reader`, `index_facts`, `classify` and `resolution` come from the modules of Tasks B2–B5. Until those exist, start with minimal stubs:
    - a reader with empty facts;
    - facts with empty sets;
    - classify returning UNKNOWN;
    - `ResolutionRules()`.

    Each stub is replaced by its task.
  - Manifest:

```python
    Manifest(
        name="go",
        load=_go,
        extensions=(".go",),
        never_ingested=re.compile(r"(?!)"),  # Go tests are ingested and flagged, never skipped
        cross_file_metadata={NodeType.FILE: ("import_map",)},
    ),
```

    with `def _go() -> LanguagePack:` importing `cgis.languages.go.PACK` like the others.
  - `cross_file_metadata` unions to the existing FILE keys, so the table is unchanged.
- [ ] **Step 4: GREEN.** The existing `test_every_pack_has_a_domain_at_the_shared_tolerance` now FAILS, naming `go`. This is expected: add the domain in Task B5 or now (see B5, Step 3). Do it now, so the suite is green per commit:
  1. Add `languages.go` at 0.42 to `patterns.yaml` with hygiene baseline 0.50 (provisional, re-measured in B5).
  2. Add it to the lock.
  3. Add it to `_PLANES["build"]`.
- [ ] **Step 5: Gate + commit.** Message: `feat(languages): the Go pack — manifest, naming, tree-sitter-go (step 7b)`.

### Task B2: `go.mod` project reader

**Files:**
- Create: `src/cgis/languages/go/project.py`
- Test: `tests/unit/test_go_project.py`

- [ ] **Tests:**
  - A `go.mod` with `module example.com/app`, a `require (github.com/x/y v1.2.3)` block and `replace example.com/lib => ./lib` gives:

```python
state == {"go_module": {"path": "example.com/app", "requires": ["github.com/x/y"], "replaces": {"example.com/lib": "lib"}}}
```

  - A replace pointing outside the repository (`=> ../elsewhere` or a module version) is dropped from `replaces`.
  - No `go.mod` gives `{"go_module": {}}`.
  - `note()` takes `go.mod` only at the ingest root. `note_above` takes the nearest `go.mod` above the root when the root has none, bounded like the TS reader.
- [ ] **Implement:**
  - A line parser: `module`, `require` single-line and block, `replace` single-line and block.
  - Ignore comments; no external dependency.
  - Facts are a frozen dataclass with `state()`.
- [ ] **Gate + commit.**

### Task B3: the extractor

**Files:**
- Create: `src/cgis/languages/go/extractor.py`
- Test: `tests/unit/test_go_extractor.py`

- [ ] **Tests.** Each is one source string parsed with `GoExtractor().parse(code, path)`. Assert node ids/types and edges:
  1. **Package and FILE.** For `package server` in `internal/server/server.go`:
     - the FILE node is `internal.server.server_go`;
     - it has `metadata["scope"] == "internal.server"`;
     - its `language` is `"go"`.
  2. **Functions and types.**
     - `func Run()` → FUNCTION `internal.server.Run`, with FILE → CONTAINS.
     - `type Server struct{}` → CLASS `internal.server.Server`.
     - `type Handler interface{ Serve() }` → CLASS.
  3. **Methods.** `func (s *Server) Start()` → METHOD `internal.server.Server.Start`, with `Server` → DECLARES. The receiver type is the same for a value receiver.
  4. **Methods in another file share the type.** In `types.go`, `type Server struct{}`; in `server.go`, `func (s *Server) Start()`. Both files' outputs use `internal.server.Server`.
  5. **Root package takes its clause name.** `main.go` at the root with `package main`, `func main()` → FUNCTION `main.main`, FILE `main_go`, `metadata["scope"] == "main"`.
  6. **Imports.**
     - `import ("fmt"; srv "example.com/app/internal/server")` → `import_map == {"fmt": "fmt", "srv": "example.com.app.internal.server"}`.
     - IMPORTS edges go from the FILE to `fmt` and to `example.com.app.internal.server`.
     - The default alias is the last path segment.
     - Blank (`_`) and dot imports are recorded as IMPORTS but not mapped.
  7. **Calls.**
     - `helper()` → CALLS `raw_call:helper`.
     - `srv.Run()` → `raw_call:srv.Run`.
     - `s.helper()` inside a method whose receiver is `s` → `raw_call:self.helper` (H6).
     - `x.Do()` on a local `x := NewThing()` → `raw_call:x.Do`.
  8. **Embedding.** `type Server struct{ Base; *pkg.Other }` → EXTENDS edges `internal.server.Server` → `raw_class:Base` and `raw_class:pkg.Other`.
  9. **Local types.** `var x *Thing` or `x := &Thing{}` in a function gives `local_types` metadata on the function node: `{"x": "*Thing"}` resolved through the import map like Python's. The normalizer strips `*` (Task B5).
  10. **Tests are flagged.** `server_test.go` → every node `is_test is True`. That comes through `is_test_path`? It doesn't match `_test.go`. So the extractor passes `is_test=True` explicitly on every node of a `_test.go` file (the `Node` validator honours an explicit value; step-1 ruling R1).
- [ ] **Implement:**
  - Subclass `BaseExtractor` and walk the tree-sitter-go AST. The node kinds were verified in planning:
    - `package_clause/package_identifier`;
    - `import_declaration/import_spec(name, path: interpreted_string_literal)`;
    - `type_declaration/type_spec(name, type: struct_type/interface_type)`;
    - `field_declaration` with no name, meaning embedded;
    - `function_declaration(name)`;
    - `method_declaration(receiver: parameter_list/parameter_declaration(name, type: pointer_type|type_identifier), name: field_identifier)`;
    - `call_expression(function: identifier|selector_expression(operand, field))`.
  - Keep each function ≤ 15 cognitive complexity: one handler per declaration kind, as `languages/python/_classes.py` does.
- [ ] **Gate + commit** after the tests pass. Mutation-check one test per behaviour group.

### Task B4: index facts, classify, resolution

**Files:**
- Create: `src/cgis/languages/go/index.py`, `src/cgis/languages/go/resolution.py`
- Test: `tests/unit/test_go_index.py`, `tests/unit/test_go_resolution.py`

- [ ] **Tests:**
  - **Facts:**
    - `layout_prefixes == {"example", "com", "app"}` for module `example.com/app` (H4);
    - `internal_roots` adds nothing beyond the generic first segments;
    - an `imports` table maps each dotted import path to INTERNAL / STDLIB / EXTERNAL / cgo.
  - **classify, in spec order:**
    1. a scope of ours → INTERNAL;
    2. `C.malloc` → STDLIB (cgo pseudo-package);
    3. `fmt.Println`, `net.http.Get` (from import path `net/http`) → STDLIB;
    4. `github.com.x.y.Do` with `github.com/x/y` required → EXTERNAL;
    5. an unknown dotted name → UNKNOWN.

    Classification is by longest prefix in the imports table, then internal roots.
  - **Resolution:**
    - `srv.Run` from a file importing `example.com/app/internal/server` resolves to `internal.server.Run`, through the import map plus generic `layout_prefixes`;
    - an unqualified `helper` defined in another file of the same package resolves through the same-scope tiebreak (Task A2), even when another package also has a `helper`;
    - `s.m()` resolves to the method on the receiver's type, or on an embedded type through EXTENDS;
    - the package import IMPORTS edge reconciles to the lexicographically first FILE node of that scope (H5);
    - the normalizer turns `*T` into `T`.
- [ ] **Implement:**
  - `index_facts` reads FILE `import_map` values and `state["go_module"]`.
  - `classify` follows the table above.
  - `reconcile_import` and the normalizer are as described.
  - `RULES = ResolutionRules(type_normalizers=(pointer_receiver,), reconcile_import=reconcile_import)`.
- [ ] **Gate + commit.**

### Task B5: Go corpus, snapshot, drift domain

**Files:**
- Create:
  - `tests/fixtures/graph_identity_go/corpus/`: a small module with `go.mod` (`module example.com/shop`, one `require`, one internal `replace`), `main.go` at the root, `internal/server/{server.go,types.go,server_test.go}`, `internal/store/store.go` with an embedded struct, and one cgo file
  - `tests/fixtures/graph_identity_go/snapshot.json`
  - `tests/unit/test_go_graph_identity.py`
- Modify: `docs/ontology/patterns.yaml` (`languages.go` hygiene re-measured), `tests/unit/review_path_inventory.txt`

- [ ] **Step 1: The corpus test.**
  - Copy `test_graph_identity.py`'s snapshot test: two hash seeds, a subprocess, `--out`.
  - Point it at the Go corpus with a `SNAPSHOT_ROOTS`-like `{"go": "."}`. Reuse `scripts/graph_identity.py`, giving `snapshot` a `--roots` option if needed. That counts as a script change, so record it as a ruling.
  - Record the snapshot with `uv run python scripts/graph_identity.py snapshot tests/fixtures/graph_identity_go/corpus --out tests/fixtures/graph_identity_go/snapshot.json`.
  - **Review the snapshot by hand** before committing:
    - every node and edge type is a known enum value;
    - unresolved targets carry `raw_call:`;
    - the cross-file methods hang under one type;
    - `C.*` is STDLIB;
    - the required module is EXTERNAL.
- [ ] **Step 2: Ontology compliance on Go.** Add `test_go_corpus_uses_only_the_ontology`:
  - ingest the corpus;
  - assert that every node type is in `core.yaml`'s `node_types`, that every edge type is in its `edge_types`, and that every CALLS target without a node starts with `raw_call:` or is a classified virtual node.
- [ ] **Step 3: Drift.**
  - Measure `languages.go` on a fresh ingest of `src/`.
  - Expected: drift ≤ 0.42 against `layered_dag`. **Stop and report otherwise.**
  - Set its hygiene baseline to measured + headroom.
- [ ] **Step 4: Python/TS untouched.**
  - Run: `git diff --exit-code main -- tests/fixtures/graph_identity/snapshot.json`
  - Run the main-vs-branch identity diff on the three trees. Expected: identical.
- [ ] **Step 5: Gate + commit.**

### Task B6: acceptance, docs, review, PR 7b

- [ ] **Acceptance diff.** Run `git diff --stat main`. Expected: only the files listed in Global Constraints. Anything else is a finding.
- [ ] **Python-only repositories load no Go grammar.** Extend `_cli_ingest_loads` assertions: the Python-only and TS-only cases also exclude `tree_sitter_go`.
- [ ] **Docs.**
  - CLAUDE.md: Go in the Architecture block.
  - Spec note: H4–H6 and the follow-ups.
- [ ] **Follow-up issues:**
  - implicit interface satisfaction;
  - generics;
  - cgo beyond classification;
  - `go.work`;
  - H4's global prefix set.
- [ ] **Independent review.** Execution tools, disk warning. Ask for an end-to-end ingest of a real Go repository from the module cache, if one is present under `~/go/pkg/mod`.
- [ ] **PR** `feat(languages): step 7b — the Go pilot`. Merge on approval.
