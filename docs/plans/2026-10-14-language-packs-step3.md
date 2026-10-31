# Language Packs — Step 3: Index Facts and Classification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each language pack computes the index facts it classifies with and classifies FQNs itself. The resolver's `classify_fqn` stops containing Python or TypeScript rules, and every stored graph stays byte-identical.

**Architecture:**
- `cgis.core.index` defines `IndexFacts` (a protocol) and `ClassifyContext` (a frozen dataclass).
- `LanguagePack` gains two methods:
  - `index_facts(nodes, state) -> IndexFacts`;
  - `classify(fqn, ctx) -> NodeNamespace`.
- The registry adds two functions:
  - `index_facts(nodes, state) -> dict[str, IndexFacts]`;
  - `classifying_pack(source_file)`, which returns the Python pack when there is no source. That is the preserved quirk.
- `IndexBuilder` keeps the language-free indices and asks every pack for its facts.
- `SymbolIndex.classify_fqn` keeps the two language-free rules (relative `.` and `self.`), then hands off to the source file's pack. A source no pack reads gets the generic tail.
- The resolution-only facts stay on `SymbolIndex` until step 4 moves their consumers: `layout_prefixes`, `workspace_packages`, `path_aliases`, `file_bare_bindings` and the star-import expansion.

**Tech Stack:** Python 3.12, tree-sitter, SQLite, pytest, ruff, mypy strict, interrogate.

**Spec:** `docs/specs/2026-10-14-language-packs-design.md` (rev 3), "Index and classification" and Migration row 3. Outline: `docs/plans/2026-10-14-language-packs.md` "Step 3". Previous steps: #597, #599 (their plans' Rulings and the spec's implementation notes).

## Global Constraints

- `make format && make lint && make type-check && make pytest && make doc-coverage` passes before every commit. Gate with `&&`, never with `;`, a newline after a heredoc, or a pipe on the gating command.
- mypy strict on `src/`, docstring coverage ≥ 90%, Sonar cognitive complexity ≤ 15 per function.
- **Behaviour unchanged:** `tests/fixtures/graph_identity/snapshot.json` is not modified, and the main-vs-branch `graph_identity diff` reads `identical` on all three trees: cgis `src` @715cd9a, owner-api @8de2245, and this repo's `ui/`.
- The existing classification tests stay as they are, assertions included:
  - `tests/unit/test_resolver_indices.py`
  - `tests/unit/test_resolver_language_scope.py`
  - `tests/unit/test_resolver.py`

  They may only be re-pointed where they read a `SymbolIndex` field that moves: `external_roots` and `dependency_roots`.
- Ingest time: median of three owner-api ingests, branch vs main, within +5%. That is the spec's step-4 budget, applied here because `classify_fqn` is on the hot path. Over budget: **stop and report**.
- Drift tolerances only go down. If `extraction` drift exceeds 0.40 or unresolved exceeds 0.37, or `resolution` drift exceeds 0.40 or unresolved exceeds 0.45: **stop and report**. Main measures `resolution` at 0.385 / 0.433, close to its lock.
- Test-count floor: raise `.github/test-count-baseline` (now 3095) if the suite passes 3245.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Worktree `/home/zaebee/projects/ownima/cgb-step3`, branch `feat/language-packs-step3`.

## Rulings (where this plan departs from the spec's or outline's text)

- **C1: `classify` returns `NodeNamespace`, not `NodeNamespace | None`.**
  - Today's order interleaves the languages: TS bare imports, then internal roots (with Python's declared-dependency rule), then the JS root, then Python stdlib, then Python external roots.
  - A pack that answered only "its" part could not preserve that order. For example, a project package named `json` must stay INTERNAL, not STDLIB.
  - So each pack's `classify` runs its whole chain, and the shared rules (internal root, JS root) appear in both packs.
  - *Cost if wrong:* the duplication is two `if`s per pack.
- **C2: `index_facts(nodes, state)` takes the merged project state, keyed by `ingest_state` key, not the pack's own `ProjectFacts` object.**
  - The resolver receives project facts as values (`ResolverEngine(..., dependency_roots=..., npm_dependencies=...)`), not as pack objects.
  - Rebuilding pack objects there would make the engine name pack classes.
  - *Cost if wrong:* a signature change when step 5 rebuilds the pipeline.
- **C3: facts that only resolution consumes stay on `SymbolIndex` until step 4.**
  - These are `layout_prefixes`, star-import expansion (`reexports`), `workspace_packages`, `path_aliases` and `file_bare_bindings`.
  - Step 4 is where resolution becomes per-pack (`reconcile_import`, positioned steps). Moving the data now would leave the resolver reaching into a pack's facts for each one.
  - The Python facts still compute the corroborated prefixes, because that is the same pass as `external_roots`. `IndexFacts.layout_prefixes` hands them back.
- **C4: the outline's "`resolve_layout`/`map_to_node_fqn` gated by pack" is not done.**
  - Both run for every language today. Gating them would change TypeScript resolution, and this step promises no change.
  - It stays a separate, measured change.
- **C5: one transitional engine-to-pack edge.**
  - `symbols._package_call`, a TypeScript rule inside the resolver, needs the TS pack's `package_namespace`.
  - It imports it directly, recorded under a new forbidden-edge rule `engine-packs` (`cgis.resolver` → `cgis.languages.<pack>`). Step 4 empties that allowlist when `_package_call` becomes the TS pack's positioned step.
- **C6: the JS constants move to core.** `JS_BUILTINS_ROOT`, `NODE_SCHEME` and `NODE_BUILTIN_MODULES` move from `cgis.resolver.js_builtins` to `cgis.core.js_globals`, so the TS pack does not import the engine.

## Review Focus

- **A project package that shares a stdlib or third-party name** (`app/json/`, `app/alembic/` plus a declared `alembic`) keeps today's namespace. Covered by the existing `test_resolver_indices.py` cases and the owner-api identity diff.
- **`classify_fqn(fqn)` with no source** still applies the Python reading. It is called that way from `resolve_layout` and from two sites in `symbols.py` (Task 3, `test_no_source_classifies_as_python`).
- **A source path no pack reads** (`.js`, `.pyi`, `Foo.PY`) gets the old generic tail: internal root → INTERNAL, JS root → STDLIB, anything else → UNKNOWN (Task 3, `test_a_source_no_pack_reads_gets_the_generic_tail`).
- **Hot-path cost.** The per-call pack lookup is cached, with no `PurePosixPath` on every call. Measured in Task 4.
- **TS bare imports in a file with no bindings, and a Python file with none.** Classification must not depend on which pack's facts happen to hold an entry for the path (Task 2, `test_only_typescript_files_carry_bare_imports`).

---

### Task 1: The Python pack's index facts and classification

**Files:**
- Create: `src/cgis/core/index.py`, `src/cgis/languages/python/index.py`, `tests/unit/test_python_index.py`
- Modify: `src/cgis/core/js_globals.py`, `src/cgis/resolver/js_builtins.py`, `src/cgis/resolver/indices.py` (C6: import the moved constants)

**Interfaces:**
- Produces:
  - **`cgis.core.index.IndexFacts`** (Protocol). Two read-only properties:
    - `internal_roots: frozenset[str]`: roots the pack adds to the language-free set;
    - `layout_prefixes: frozenset[str]`: corroborated prefixes before internal roots are subtracted.
  - **`cgis.core.index.ClassifyContext`** (frozen dataclass): `source_file: str | None`, `internal_roots: frozenset[str]`, `nodes: Mapping[str, Node]`, `facts: IndexFacts`.
  - **`cgis.languages.python.index`:**
    - `PythonIndexFacts`, with fields `internal_roots`, `layout_prefixes`, `external_roots`, `dependency_roots`, `dependency_heads`, all `frozenset[str]`;
    - `index_facts(nodes: Mapping[str, Node], state: Mapping[str, object]) -> PythonIndexFacts`;
    - `classify(fqn: str, ctx: ClassifyContext) -> NodeNamespace`.
  - **`cgis.core.js_globals`:** `JS_BUILTINS_ROOT`, `NODE_SCHEME`, `NODE_BUILTIN_MODULES`.

- [ ] **Step 1: Move the JS constants to core (C6).**
  1. Cut these from `src/cgis/resolver/js_builtins.py` and paste them, verbatim with their comments, at the end of `src/cgis/core/js_globals.py`:
     - `JS_BUILTINS_ROOT = "js_builtins"`
     - `NODE_SCHEME = "node:"`
     - the whole `NODE_BUILTIN_MODULES` frozenset
  2. In `js_builtins.py`, add `from cgis.core.js_globals import JS_BUILTINS_ROOT, NODE_BUILTIN_MODULES, NODE_SCHEME`. Drop names it no longer uses: ruff tells you which.
  3. In `indices.py`, import the three constants from `cgis.core.js_globals`.
  4. Run `git grep -n "js_builtins import" -- src tests scripts`. Re-point every import of a moved name to `cgis.core.js_globals`.
  5. Run `uv run pytest tests/unit -q -k "js or builtin or language_scope"`. Expected: all pass.

- [ ] **Step 2: Write the failing tests.** Create `tests/unit/test_python_index.py`:

```python
"""The Python pack's index facts and classification (language-packs step 3).

Values are those the pre-step-3 `IndexBuilder`/`classify_fqn` produced for the
same nodes; the equivalence over real graphs is the identity snapshot's job.
"""

from types import MappingProxyType

from cgis.core.index import ClassifyContext
from cgis.core.models import Node, NodeNamespace, NodeType
from cgis.languages.python.index import PythonIndexFacts, classify, index_facts


def _node(fqn: str, node_type: NodeType = NodeType.FUNCTION, **kw: object) -> Node:
    return Node(
        id=fqn, type=node_type, name=fqn.rsplit(".", 1)[-1],
        file_path=kw.pop("file_path", f"{fqn.replace('.', '/')}.py"),  # type: ignore[arg-type]
        start_line=1, end_line=1, **kw,  # type: ignore[arg-type]
    )  # fmt: skip


def _facts(nodes: list[Node], deps: list[str] | None = None) -> PythonIndexFacts:
    by_id = {n.id: n for n in nodes}
    return index_facts(MappingProxyType(by_id), {"python_dependencies": deps or []})


def _ctx(facts: PythonIndexFacts, nodes: list[Node], roots: set[str], source: str | None) -> ClassifyContext:
    return ClassifyContext(
        source_file=source,
        internal_roots=frozenset(roots) | facts.internal_roots,
        nodes=MappingProxyType({n.id: n for n in nodes}),
        facts=facts,
    )


def test_import_roots_split_into_external_and_first_party() -> None:
    """#424: `app` reaches our nodes once stripped (twice: corroborated); `pydantic` never does."""
    file_node = _node(
        "domains.views", NodeType.FILE, file_path="domains/views.py",
        metadata={"import_map": {
            "A": "app.domains.models.A", "B": "app.domains.models.B", "C": "pydantic.BaseModel",
        }},
    )  # fmt: skip
    nodes = [file_node, _node("domains.models.A"), _node("domains.models.B")]
    facts = _facts(nodes)
    assert facts.external_roots == frozenset({"pydantic"})
    assert "app" in facts.internal_roots
    assert facts.layout_prefixes == frozenset({"app"})


def test_src_and_lib_layouts_add_their_second_segment() -> None:
    facts = _facts([_node("src.cgis.pipeline.run"), _node("lib.util.x")])
    assert {"cgis", "util"} <= facts.internal_roots


def test_only_colliding_dependencies_are_kept() -> None:
    """#495: a declared dependency matters only where an internal root shares its name."""
    nodes = [_node("alembic.env.run_migrations"), _node("app.main")]
    facts = _facts(nodes, deps=["alembic", "fastapi"])
    assert facts.dependency_roots == frozenset({"alembic"})
    assert facts.dependency_heads == frozenset({"alembic.env"})


def test_classification_chain() -> None:
    nodes = [_node("alembic.env.run_migrations"), _node("json.helpers.f")]
    facts = _facts(nodes, deps=["alembic"])
    ctx = _ctx(facts, nodes, {"alembic", "json"}, "app/x.py")
    assert classify("alembic.env.run_migrations", ctx) is NodeNamespace.INTERNAL
    assert classify("alembic.op.add_column", ctx) is NodeNamespace.EXTERNAL  # declared, not ours
    assert classify("json.helpers.f", ctx) is NodeNamespace.INTERNAL  # our package beats stdlib
    assert classify("os.path.join", ctx) is NodeNamespace.STDLIB
    assert classify("len", ctx) is NodeNamespace.STDLIB
    assert classify("js_builtins.Math", ctx) is NodeNamespace.STDLIB
    assert classify("nothing.known", ctx) is NodeNamespace.UNKNOWN
```

- [ ] **Step 3: Run them and watch them fail.**
  - Run: `uv run pytest tests/unit/test_python_index.py -q`
  - Expected: collection error, `ModuleNotFoundError: cgis.core.index`.

- [ ] **Step 4: The contract.** Create `src/cgis/core/index.py`:

```python
"""What a language pack contributes to the symbol index and to classification.

Spec: Index and classification. The resolver keeps the language-free indices;
each pack derives what only its language's nodes and configs can say, and
classifies an FQN referenced from one of its files (language-packs step 3).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from cgis.core.models import Node


class IndexFacts(Protocol):
    """One pack's facts over the whole node set; frozen."""

    @property
    def internal_roots(self) -> frozenset[str]:
        """Roots this pack adds to the language-free set (first segment of every node id)."""
        ...

    @property
    def layout_prefixes(self) -> frozenset[str]:
        """Prefixes sources write that node ids do not carry, before internal roots are removed."""
        ...


@dataclass(frozen=True)
class ClassifyContext:
    """What a pack's `classify` may read about one reference."""

    #: The file the reference comes from; None applies the Python reading (spec quirk).
    source_file: str | None
    #: Every internal root, all packs' contributions included.
    internal_roots: frozenset[str]
    nodes: Mapping[str, Node]
    #: This pack's own facts.
    facts: IndexFacts
```

- [ ] **Step 5: The Python pack's facts.** Create `src/cgis/languages/python/index.py`.
  - **Move these verbatim out of `src/cgis/resolver/indices.py`, with their docstrings and comments:**
    - `_LAYOUT_PREFIX_EVIDENCE`
    - `_strips_to_a_node`
    - `_heads_under`
    - `_is_declared_dependency`
    - the body of `IndexBuilder._build_external_roots`, as a module-level `_split_import_roots`
    - `_BUILTINS`
  - Rewrite `_is_declared_dependency` to take `(fqn, root, facts, nodes)`. It is only ever called for a Python source or for none, so the source-file check goes.
  - Do not delete the originals from `indices.py` yet. Task 3 does that once nothing calls them.

```python
"""The Python pack's index facts and classification (spec: Index and classification).

Moved from `cgis.resolver.indices` in language-packs step 3; the history of each
rule is in its docstring and issue number.
"""

import builtins
import os
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import cast

from cgis.core.index import ClassifyContext
from cgis.core.js_globals import JS_BUILTINS_ROOT
from cgis.core.models import Node, NodeNamespace, NodeType

_BUILTINS: frozenset[str] = frozenset(dir(builtins))
_LAYOUT_DIRS = frozenset({"src", "lib"})
# ... _LAYOUT_PREFIX_EVIDENCE, _strips_to_a_node, _heads_under moved verbatim ...


@dataclass(frozen=True)
class PythonIndexFacts:
    """What Python import maps and manifests say about the whole node set."""

    #: Second segments of `src`/`lib` layouts and first-party import prefixes (#424).
    internal_roots: frozenset[str]
    #: Import roots with enough evidence to strip off a target (#498 review).
    layout_prefixes: frozenset[str]
    #: Import roots no node of ours sits under.
    external_roots: frozenset[str]
    #: Declared dependencies that collide with an internal root (#495).
    dependency_roots: frozenset[str]
    #: `root.second` heads our own nodes have under those roots.
    dependency_heads: frozenset[str]


def index_facts(nodes: Mapping[str, Node], state: Mapping[str, object]) -> PythonIndexFacts:
    """Derive the facts from every node and the declared `python_dependencies`."""
    node_ids = set(nodes)
    local_roots: set[str] = set()
    layout_roots: set[str] = set()
    file_imports: dict[str, dict[str, str]] = {}
    for node in nodes.values():
        parts = node.id.split(".")
        local_roots.add(parts[0])
        if len(parts) > 1 and parts[0] in _LAYOUT_DIRS:
            layout_roots.add(parts[1])
        if node.type == NodeType.FILE:
            file_imports[os.path.normpath(node.file_path)] = node.metadata.get("import_map") or {}
    external, first_party, corroborated = _split_import_roots(file_imports, node_ids)
    declared = set(cast("list[str]", state.get("python_dependencies", [])))
    colliding = (local_roots | layout_roots) & declared
    return PythonIndexFacts(
        internal_roots=frozenset(layout_roots | first_party),
        layout_prefixes=frozenset(corroborated),
        external_roots=frozenset(external),
        dependency_roots=frozenset(colliding),
        dependency_heads=_heads_under(colliding, node_ids),
    )


def classify(fqn: str, ctx: ClassifyContext) -> NodeNamespace:
    """The Python reading of a root: ours, a declared dependency, stdlib, or a known import."""
    facts = cast("PythonIndexFacts", ctx.facts)
    root = fqn.split(".", maxsplit=1)[0]
    if root in ctx.internal_roots:
        if _is_declared_dependency(fqn, root, facts, ctx.nodes):
            return NodeNamespace.EXTERNAL
        return NodeNamespace.INTERNAL
    if root == JS_BUILTINS_ROOT:
        return NodeNamespace.STDLIB
    if root in sys.stdlib_module_names or root in _BUILTINS:
        return NodeNamespace.STDLIB
    if root in facts.external_roots:
        return NodeNamespace.EXTERNAL
    return NodeNamespace.UNKNOWN
```

  - `colliding` is `local_roots | layout_roots` because the old builder intersected the declared roots with `internal_roots` *before* adding first-party prefixes. At that point it held the first segments plus the `src`/`lib` second segments. Keep that exact set.
  - If ruff flags `Iterable` as unused after the move, drop it.

- [ ] **Step 6: Run the tests.**
  - Run: `uv run pytest tests/unit/test_python_index.py tests/unit/test_resolver_indices.py -q`
  - Expected: all pass. The old index is still the one the resolver uses.

- [ ] **Step 7: Gate and commit.** Message: `feat(languages): the Python pack's index facts and classification (not wired yet)`, with a body naming C6 and the Co-Authored-By line. Use the same gate line as step 2's plan, with scratchpad dir `step3`.

---

### Task 2: The TypeScript pack's index facts and classification

**Files:**
- Create: `src/cgis/languages/typescript/index.py`, `tests/unit/test_typescript_index.py`

**Interfaces:**
- Consumes: `cgis.core.index.ClassifyContext` and `IndexFacts` (Task 1); `JS_BUILTINS_ROOT`, `NODE_SCHEME` and `NODE_BUILTIN_MODULES` from `cgis.core.js_globals`.
- Produces, in `cgis.languages.typescript.index`:
  - `TypeScriptIndexFacts`, with fields `internal_roots`, `layout_prefixes` (both always empty), `npm_dependencies: frozenset[str]` and `file_bare_imports: Mapping[str, frozenset[str]]`;
  - `index_facts(nodes, state) -> TypeScriptIndexFacts`;
  - `package_namespace(fqn: str, source_file: str | None, facts: TypeScriptIndexFacts) -> NodeNamespace | None`;
  - `classify(fqn, ctx) -> NodeNamespace`.

- [ ] **Step 1: Write the failing tests.** Create `tests/unit/test_typescript_index.py`:

```python
"""The TypeScript pack's index facts and classification (language-packs step 3)."""

from types import MappingProxyType

from cgis.core.index import ClassifyContext
from cgis.core.models import Node, NodeNamespace, NodeType
from cgis.languages.typescript.index import (
    TypeScriptIndexFacts,
    classify,
    index_facts,
    package_namespace,
)


def _file(fqn: str, path: str, **metadata: object) -> Node:
    return Node(
        id=fqn, type=NodeType.FILE, name=path.rsplit("/", 1)[-1], file_path=path,
        start_line=1, end_line=1, language="typescript", metadata=metadata,
    )  # fmt: skip


def _facts(nodes: list[Node], npm: list[str]) -> TypeScriptIndexFacts:
    return index_facts(MappingProxyType({n.id: n for n in nodes}), {"npm_dependencies": npm})


def test_only_typescript_files_carry_bare_imports() -> None:
    nodes = [
        _file("app.h", "app/h.ts", bare_imports=["react", "node:fs"]),
        _file("app.plain", "app/plain.ts"),
    ]
    facts = _facts(nodes, ["react"])
    assert facts.file_bare_imports == {"app/h.ts": frozenset({"react", "node:fs"})}
    assert facts.internal_roots == frozenset() == facts.layout_prefixes


def test_a_package_the_file_imports_and_the_manifest_declares_is_external() -> None:
    facts = _facts([_file("app.h", "app/h.ts", bare_imports=["react", "fs", "lodash"])], ["react"])
    assert package_namespace("react.useState", "app/h.ts", facts) is NodeNamespace.EXTERNAL
    assert package_namespace("fs.readFile", "app/h.ts", facts) is NodeNamespace.STDLIB
    assert package_namespace("lodash.map", "app/h.ts", facts) is None  # undeclared: a lost edge
    assert package_namespace("react.useState", "app/other.ts", facts) is None  # not imported there


def test_classification_chain() -> None:
    facts = _facts([_file("app.h", "app/h.ts", bare_imports=["react"])], ["react"])
    ctx = ClassifyContext(
        source_file="app/h.ts", internal_roots=frozenset({"app", "json"}),
        nodes=MappingProxyType({}), facts=facts,
    )  # fmt: skip
    assert classify("react.useState", ctx) is NodeNamespace.EXTERNAL
    assert classify("app.util", ctx) is NodeNamespace.INTERNAL
    assert classify("js_builtins.Math", ctx) is NodeNamespace.STDLIB
    assert classify("json.parse", ctx) is NodeNamespace.INTERNAL  # an internal root, not Python's json
    assert classify("os.path", ctx) is NodeNamespace.UNKNOWN  # #454: Python names mean nothing here
```

- [ ] **Step 2: Run them and watch them fail.**
  - Run: `uv run pytest tests/unit/test_typescript_index.py -q`
  - Expected: `ModuleNotFoundError: cgis.languages.typescript.index`.

- [ ] **Step 3: Write the module.**
  - Move `bare_import_namespace`'s docstring and body from `indices.py` into `package_namespace`. Read `facts.file_bare_imports` and `facts.npm_dependencies` instead of the index's.
  - Leave the original in place until Task 3.

```python
"""The TypeScript pack's index facts and classification (spec: Index and classification)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from cgis.core.index import ClassifyContext
from cgis.core.js_globals import JS_BUILTINS_ROOT, NODE_BUILTIN_MODULES, NODE_SCHEME, specifier_fqn
from cgis.core.models import Node, NodeNamespace, NodeType


@dataclass(frozen=True)
class TypeScriptIndexFacts:
    """Declared npm packages and each file's bare imports (#556)."""

    internal_roots: frozenset[str]
    layout_prefixes: frozenset[str]
    #: npm packages the manifests declare, as written, less our workspace packages.
    npm_dependencies: frozenset[str]
    #: normalized file_path -> the bare specifiers that file imports, as written.
    file_bare_imports: Mapping[str, frozenset[str]]


def index_facts(nodes: Mapping[str, Node], state: Mapping[str, object]) -> TypeScriptIndexFacts:
    """Read the bare imports the extractor recorded and the declared npm packages."""
    bare = {
        os.path.normpath(n.file_path): frozenset(n.metadata["bare_imports"])
        for n in nodes.values()
        if n.type == NodeType.FILE and n.metadata.get("bare_imports")
    }
    return TypeScriptIndexFacts(
        internal_roots=frozenset(),
        layout_prefixes=frozenset(),
        npm_dependencies=frozenset(cast("list[str]", state.get("npm_dependencies", []))),
        file_bare_imports=MappingProxyType(bare),
    )


def package_namespace(
    fqn: str, source_file: str | None, facts: TypeScriptIndexFacts
) -> NodeNamespace | None:
    """<the bare_import_namespace docstring, moved verbatim>"""
    # <the body, with `index.` replaced by `facts.`>


def classify(fqn: str, ctx: ClassifyContext) -> NodeNamespace:
    """A package the file imports, then our own roots, then the JS builtins; else UNKNOWN (#454)."""
    package = package_namespace(fqn, ctx.source_file, cast("TypeScriptIndexFacts", ctx.facts))
    if package is not None:
        return package
    root = fqn.split(".", maxsplit=1)[0]
    if root in ctx.internal_roots:
        return NodeNamespace.INTERNAL
    if root == JS_BUILTINS_ROOT:
        return NodeNamespace.STDLIB
    return NodeNamespace.UNKNOWN
```

  - Check that the dict comprehension matches the old builder. `_index_metadata` keyed bare imports by `os.path.normpath(node.file_path)` and recorded them only when `specifiers` was truthy, which is the same condition used here.

- [ ] **Step 4: Run the tests.**
  - Run: `uv run pytest tests/unit/test_typescript_index.py tests/unit/test_resolver_language_scope.py -q`
  - Expected: all pass.

- [ ] **Step 5: Gate and commit.** Message: `feat(languages): the TypeScript pack's index facts and classification (not wired yet)`.

---

### Task 3: Wire the packs in; the resolver's classification becomes language-free

**Files:**
- Modify:
  - `src/cgis/core/extractor.py` (two `LanguagePack` methods)
  - both pack `__init__.py`
  - `src/cgis/languages/__init__.py` (`index_facts`, `classifying_pack`)
  - `src/cgis/resolver/indices.py`
  - `src/cgis/resolver/symbols.py`
  - `tests/self_parsing/test_architecture.py` (rule `engine-packs`)
  - `tests/self_parsing/forbidden_edges_allowlist.json`
  - `tests/unit/test_resolver_indices.py:64,213,454,459` (re-point moved fields only)
- Test: `tests/unit/test_languages.py` (append)

**Interfaces:**
- Consumes: Task 1 and Task 2 modules.
- Produces:
  - `LanguagePack.index_facts(nodes: Mapping[str, Node], state: Mapping[str, object]) -> IndexFacts`
  - `LanguagePack.classify(fqn: str, ctx: ClassifyContext) -> NodeNamespace`
  - `cgis.languages.index_facts(nodes, state) -> dict[str, IndexFacts]`
  - `cgis.languages.classifying_pack(source_file: str | None) -> LanguagePack | None`
  - `SymbolIndex.facts: Mapping[str, IndexFacts]`

  `SymbolIndex` loses `external_roots`, `dependency_roots`, `dependency_heads`, `npm_dependencies` and `file_bare_imports`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/unit/test_languages.py`:

```python
from cgis.core.models import NodeNamespace
from cgis.languages import classifying_pack
from cgis.resolver.indices import IndexBuilder


def test_no_source_classifies_as_python() -> None:
    """Spec quirk, kept in the registry: classify_fqn(fqn) without a source reads it as Python."""
    pack = classifying_pack(None)
    assert pack is not None
    assert pack.name == "python"
    index = IndexBuilder().build([])
    assert index.classify_fqn("os.path") is NodeNamespace.STDLIB
    assert index.classify_fqn("os.path", "app/x.ts") is NodeNamespace.UNKNOWN


@pytest.mark.parametrize("source", ["web/a.js", "types/x.pyi", "Foo.PY"])
def test_a_source_no_pack_reads_gets_the_generic_tail(source: str) -> None:
    """Internal root → INTERNAL, the JS root → STDLIB, anything else UNKNOWN — as before."""
    assert classifying_pack(source) is None
    index = IndexBuilder().build(
        [Node(id="app.f", type=NodeType.FUNCTION, name="f", file_path="app/f.py",
              start_line=1, end_line=1)]
    )  # fmt: skip
    assert index.classify_fqn("app.g", source) is NodeNamespace.INTERNAL
    assert index.classify_fqn("js_builtins.Math", source) is NodeNamespace.STDLIB
    assert index.classify_fqn("os.path", source) is NodeNamespace.UNKNOWN
```

Move the imports to the file's import block (`Node` and `NodeType` are already imported there, so check), and add `import pytest` if it is missing.

- [ ] **Step 2: Run them and watch them fail.**
  - Run: `uv run pytest tests/unit/test_languages.py -q -k "no_source or generic_tail"`
  - Expected: `ImportError: cannot import name 'classifying_pack'`.

- [ ] **Step 3: The contract and the packs.**
  1. In `src/cgis/core/extractor.py`, import `IndexFacts` and `ClassifyContext` from `cgis.core.index`, plus `NodeNamespace`. Add to `LanguagePack`:

```python
    def index_facts(self, nodes: Mapping[str, Node], state: Mapping[str, object]) -> IndexFacts:
        """This pack's facts over every node, given the merged project state (step 3, C2)."""
        ...

    def classify(self, fqn: str, ctx: ClassifyContext) -> NodeNamespace:
        """The namespace of `fqn` referenced from one of this pack's files — the whole chain (C1)."""
        ...
```

  2. In each pack's `__init__.py`, implement both with `@override`, delegating to the pack's `index` module:

```python
    @override
    def index_facts(self, nodes: Mapping[str, Node], state: Mapping[str, object]) -> IndexFacts:
        """See `cgis.languages.python.index.index_facts`."""
        return index.index_facts(nodes, state)

    @override
    def classify(self, fqn: str, ctx: ClassifyContext) -> NodeNamespace:
        """See `cgis.languages.python.index.classify`."""
        return index.classify(fqn, ctx)
```

     Use `from cgis.languages.python import index` (and the same for typescript). The modules import no grammar.

- [ ] **Step 4: The registry.** In `src/cgis/languages/__init__.py`, add `from functools import cache, lru_cache`, import `IndexFacts` from `cgis.core.index` and `Node` from core models, then:

```python
def index_facts(nodes: Mapping[str, Node], state: Mapping[str, object]) -> dict[str, IndexFacts]:
    """Every pack's index facts, by pack name."""
    return {m.name: _load(m.name).index_facts(nodes, state) for m in MANIFESTS}


@lru_cache(maxsize=4096)
def classifying_pack(source_file: str | None) -> LanguagePack | None:
    """The pack that classifies a reference from `source_file`; None when no pack reads it.

    No source means the Python pack: `classify_fqn(fqn)` has always applied the
    Python reading, and removing that is a separate, measured change (spec,
    Index and classification). Cached: classification is on the resolver's hot
    path, and a path's suffix never changes.
    """
    return _load("python") if source_file is None else pack_for_path(source_file)
```

- [ ] **Step 5: The resolver.** In `src/cgis/resolver/indices.py`:
  1. `SymbolIndex`:
     - delete the fields `external_roots`, `dependency_roots`, `dependency_heads`, `npm_dependencies` and `file_bare_imports`, with their comments;
     - add `facts: Mapping[str, IndexFacts] = MappingProxyType({})`, commented `# pack name -> that pack's index facts (language-packs step 3)`;
     - keep `internal_roots` and `layout_prefixes`.
  2. Replace `classify_fqn`'s body after the two generic rules:

```python
        pack = classifying_pack(source_file)
        if pack is None:
            # A source no pack reads: the rules every language shared before step 3.
            root = fqn.split(".", maxsplit=1)[0]
            if root in self.internal_roots:
                return NodeNamespace.INTERNAL
            return NodeNamespace.STDLIB if root == JS_BUILTINS_ROOT else NodeNamespace.UNKNOWN
        context = ClassifyContext(
            source_file=source_file,
            internal_roots=self.internal_roots,
            nodes=self.nodes,
            facts=self.facts[pack.name],
        )
        return pack.classify(fqn, context)
```

     Keep its docstring. Add: `The language rules are the source's pack's (language-packs step 3).`
  3. `IndexBuilder.build`:
     - keep the language-free loop;
     - change `_add_node_to_suffix_map` so it adds only `parts[0]` to `internal_roots` (the `src`/`lib` second segment is now Python's);
     - drop the `file_bare_imports` accumulation from `_index_metadata`, but **keep** `bare_bindings`, populated under the same `if specifiers:` condition;
     - replace the external-roots and dependency block with:

```python
        state = {
            "python_dependencies": list(dependency_roots),
            "npm_dependencies": list(npm_dependencies),
        }
        facts = pack_index_facts(MappingProxyType(nodes_by_id), state)
        roots = frozenset(internal_roots.union(*(f.internal_roots for f in facts.values())))
        corroborated = frozenset().union(*(f.layout_prefixes for f in facts.values()))
        self._expand_star_imports(star_imports, reexports, set(nodes_by_id))
```

     then build `SymbolIndex(..., internal_roots=roots, layout_prefixes=corroborated - roots, facts=MappingProxyType(facts), ...)`, without the deleted fields. Import `from cgis.languages import classifying_pack, index_facts as pack_index_facts` and `from cgis.core.index import ClassifyContext, IndexFacts`.
  4. Delete what Tasks 1 and 2 moved and nothing now calls: `bare_import_namespace`, `_is_declared_dependency`, `_strips_to_a_node`, `_heads_under`, `_LAYOUT_PREFIX_EVIDENCE`, `_build_external_roots`, `_BUILTINS`, and the `builtins`/`sys` imports if unused. Check with `git grep -n "<name>" -- src tests` before each deletion.

- [ ] **Step 6: `symbols._package_call` (C5).**
  - Import `from cgis.languages.typescript.index import TypeScriptIndexFacts, package_namespace` and drop `bare_import_namespace` from the `indices` import.
  - Replace the check with:

```python
    facts = index.facts.get("typescript")
    if not isinstance(facts, TypeScriptIndexFacts):
        return None
    if package_namespace(candidate, file_path, facts) is None:
        return None
```

  - Add one comment line above it: `# A TypeScript rule inside the engine until step 4 makes it the TS pack's step (C5).`

- [ ] **Step 7: Record the transitional edge in the gate.**
  1. In `tests/self_parsing/test_architecture.py` `RULES`, add, after `resolver-storage`:

```python
    _Rule("engine-packs", ("cgis.resolver",), ("cgis.languages",),
          exempt_modules=frozenset({"cgis.languages"})),
```

  2. Add `"engine-packs": {}` to `forbidden_edges_allowlist.json`.
  3. Run `uv run pytest tests/self_parsing/test_architecture.py -q -k allowlist`. It fails and prints the actual entries.
  4. Expected: exactly one key, `"cgis.resolver.symbols -> cgis.languages.typescript.index"`, with a small count (an import plus a call or two). Copy that entry into the JSON.
  5. Any other `engine-packs` entry means the resolver reaches a pack somewhere else: fix the code, not the allowlist.
  6. If `RULES[0]` is referenced by index in a test (`grep -n "RULES\[" tests/self_parsing/test_architecture.py`), check that inserting the rule after `resolver-storage` leaves index 0 unchanged.

- [ ] **Step 8: Re-point the moved-field reads in tests.** In `tests/unit/test_resolver_indices.py`:
  - `index.external_roots` → `index.facts["python"].external_roots`, at :64 and :213;
  - `.dependency_roots` → `.facts["python"].dependency_roots`, at :454 and :459.

  Change nothing else: no assertion and no fixture. Then run `git grep -nE "\.(external_roots|dependency_roots|dependency_heads|npm_dependencies|file_bare_imports)\b" -- tests src`. Expected: only uses on pack facts objects, and `ImportNames.npm_dependencies`.

- [ ] **Step 9: Run the tests.**
  - Run: `uv run pytest tests/unit/test_languages.py tests/unit/test_resolver_indices.py tests/unit/test_resolver_language_scope.py tests/unit/test_resolver.py tests/unit/test_python_index.py tests/unit/test_typescript_index.py tests/unit/test_graph_identity.py tests/self_parsing -q`
  - Expected: all pass.
  - A failure in the resolver tests is a behaviour change. Find it with `superpowers:systematic-debugging`; never edit the assertion.

- [ ] **Step 10: Gate and commit.** Message: `refactor(resolver): classification and its index facts come from the language packs`, with a body naming C1–C5 and the Co-Authored-By line.

---

### Task 4: Measure, record, document

**Files:**
- `tests/unit/review_path_inventory.txt` (regenerate if the closure changed)
- `docs/ontology/tolerances.lock` and `patterns.yaml`, only if a tolerance may go down
- `docs/specs/2026-10-14-language-packs-design.md` (implementation note)
- `CLAUDE.md` ("Adding a Language Pack": `index_facts`, `classify`)

- [ ] **Step 1: Drift.** Ingest `src` with the branch's cgis and run `cgis drift --profile python --format json`, as in step 2's plan. Read `extraction`, `resolution` and `cgis-project`. Stop if any exceeds the bounds in Global Constraints. Record the numbers in the ledger.

- [ ] **Step 2: Hot-path timing.**

```bash
S=/tmp/claude-1001/-home-zaebee-projects-ownima-codegraph-brain/97c475b5-6bc7-4231-a643-ff81f3f651ec/scratchpad/step3/perf && /bin/rm -rf $S && mkdir -p $S/oa && git -C /home/zaebee/projects/ownima/owner-api archive 8de2245 ownima-backend | tar -x -C $S/oa && for side in main branch; do P=$([ $side = main ] && echo /home/zaebee/projects/ownima/codegraph-brain || echo /home/zaebee/projects/ownima/cgb-step3); for i in 1 2 3; do /usr/bin/time -f "$side %e" -a -o $S/times.txt uv run --project $P cgis ingest $S/oa/ownima-backend --output $S/$side-$i.db > /dev/null 2>&1 || exit 1; done; done && sort $S/times.txt
```

  - Expected: the branch median is within +5% of main's. Report both medians.
  - If it is over budget, profile `classify_fqn` before anything else (`python -m cProfile -s cumtime`).

- [ ] **Step 3: Inventory.**
  - Regenerate it as in step 2's plan, Task 2 Step 3.
  - Expected: nothing gone, and new entries for the two `index.py` modules and `core/index.py`.

- [ ] **Step 4: Docs.**
  1. In the spec's "Index and classification" section, add an `> **Implementation note (step 3):**` covering four points:
     - `classify` returns the whole chain, not `None` (C1);
     - `index_facts` takes the merged project state (C2);
     - resolution-only facts and star expansion stay on `SymbolIndex` until step 4 (C3);
     - the outline's `resolve_layout` gating was dropped as a behaviour change (C4).
  2. In CLAUDE.md "Adding a Language Pack" step 2, add `index_facts` and `classify` to the list of members. Add one line: `classify answers for references from the pack's own files and runs the whole chain; a source with no pack gets the generic tail in SymbolIndex.classify_fqn.`

- [ ] **Step 5: Gate and commit.** Message: `docs: step 3 implementation notes; inventory`, with the measurements in the body.

---

### Task 5: Identity report, review, PR

- [ ] **Step 1: Identity.** Run the same commands as step 2's plan, Task 6, Steps 1–2, with `step3` paths and `BR=/home/zaebee/projects/ownima/cgb-step3`.
  - Expected: `identical` three times.
  - Before ingesting, check that main has not moved under the branch: `git -C <main> diff --stat <merge-base> main -- src` should be empty.

- [ ] **Step 2: Independent review.** Dispatch a reviewer with execution tools on the most capable model. Give it the branch diff, this plan, the spec, the ledger rulings and the Review Focus. Fix Critical/Important findings test-first, then rerun the gate and the identity step.

- [ ] **Step 3: PR.** Push and open the PR, titled `refactor(languages): step 3 — per-pack index facts and classification`. The body carries:
  - the module map;
  - rulings C1–C6;
  - three identity results;
  - drift and timing numbers;
  - the `engine-packs` allowlist entry and that step 4 empties it;
  - the reviewer's deferred minors.

  Then check the Gemini and Guardian comments and Sonar, and reply in every thread.

<!-- START_CGIS_RELATED -->
**Related:** [language-packs-step4](2026-10-14-language-packs-step4.md) · [language-packs-step5](2026-10-14-language-packs-step5.md) · [language-packs-step6](2026-10-14-language-packs-step6.md) · [language-packs-step7](2026-10-14-language-packs-step7.md) · [language-packs](2026-10-14-language-packs.md)

**Code:** [`src/cgis/core/`](../../src/cgis/core/) · [`src/cgis/languages/`](../../src/cgis/languages/) · [`src/cgis/resolver/`](../../src/cgis/resolver/) · [`tests/fixtures/graph_identity/snapshot.json`](../../tests/fixtures/graph_identity/snapshot.json) · [`tests/unit/test_resolver_indices.py`](../../tests/unit/test_resolver_indices.py)

<!-- END_CGIS_RELATED -->
