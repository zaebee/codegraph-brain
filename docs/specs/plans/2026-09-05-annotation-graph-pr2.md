# PR2 — #414 receiver resolution: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve `self.<attr>.<method>()` to the method it actually calls, so a dependency-injected layer stops reading as calling nothing.

**Architecture:** Three moves, no new machinery. `SymbolIndex` gains a `self_types` map built from the CLASS-node metadata PR1 already writes, exactly as `file_imports` is built from FILE-node metadata. `resolve_self_call` learns the dotted form: split `client.search`, look the receiver up in that map, then hand off to the inheritance walk that already exists. Finally `classify_fqn` stops calling `self.`-prefixed placeholders INTERNAL, so the ones that remain are counted honestly.

**Tech Stack:** Python 3.12+, Pydantic v2 frozen models, SQLite, pytest, mypy strict, ruff.

**Spec:** `docs/specs/2026-09-05-annotation-graph-design.md` — read it first. This plan implements decisions **D1** (consuming the map), **D6** (confidence), **D7** (reuse the hierarchy walk) and **D8** (two segments only). Its **Risks** section names two `self_types` shapes that are known-wrong; do not "fix" them here.

## Baseline, measured on merged main (0e7456e)

Re-ingested rather than remembered — the repo-root `graph.db` is untracked and stale:

```
uv run cgis ingest src --source-root src --output /tmp/pr2-base.db
  → 1 592 nodes / 5 688 edges
```

| Measurement | Value |
|---|---|
| `self.*` placeholder edges, total | 142 |
| …exactly two segments after `self` (in scope, D8) | **120** |
| …one, three or four segments (out of scope) | 22 |
| Two-segment sites whose receiver is in `self_types` | **107 (89%)** |
| …receiver genuinely unannotated | 13 |
| Distinct placeholder nodes | 65 |
| …shared by more than one owning class | **4** |
| `unresolved_ratio` | 0.1834 |
| `SymbolResolver` methods (God-Object gate fires at 10) | 9 |

The 13 that stay are real: `self.conn` (×8) from a `sqlite3.connect()` result, `self._index` (×3), and two others.

**The collision is not hypothetical.** Four placeholder nodes are shared today:

```
self._parser.parse        <- PythonExtractor, TypeScriptExtractor          (2 segments, in scope)
self._store.get_all_nodes <- FingerprintExtractor, SemanticUpliftEngine    (2 segments, in scope)
self._store.get_all_edges <- FingerprintExtractor, SemanticUpliftEngine    (2 segments, in scope)
self._pick_source_root    <- ClassHandler, FunctionHandler, TypeResolver   (1 segment — OUT of scope)
```

The fourth is `self.<attr>()` — a call to a callable attribute, not
`self.<attr>.<method>()`. Resolving it would need to know which function was passed
into the constructor, which is dataflow rather than annotation. **It stays shared
after this PR, by design**, and the calibration gate in Task 4 is scoped accordingly.

`self._parser` is a tree-sitter parser for a *different language* in each of those two classes, and the graph gives them one vertex.

## Decision the spec leaves open: confidence

#414 asks it directly — "an annotation is a claim, not a fact; `self.x` can be rebound;
resolving at 0.8 rather than 1.0 would keep the distinction the confidence column already
carries" — and the spec's D6 only settled that 0.8 is unusable, because
`_resolved_call_edge` already assigns 0.8 on *failure*.

**Ruling: a receiver-resolved call takes the same confidence as any other resolved call.
Do not add a separate value.**

The precedent is already in the file you are editing. `_resolve_local_type_call`
(`symbols.py:147`) resolves `var.method()` from a local variable's annotation and returns
through the ordinary path at ordinary confidence. Receiver resolution is the identical
claim — an annotation says what this name is — made about `self.attr` instead of `var`.
Giving one of them a discount and not the other would encode a distinction that does not
exist, and it would need `resolve_self_call` to return more than an FQN so the engine could
tell the two apart.

Cost if wrong: a consumer that wanted to exclude annotation-derived calls cannot, and would
need the distinction reintroduced. No consumer filters that way today; `audit_reachability`
is the only `min_confidence` user and it filters unresolved edges, which these are not.

## Global Constraints

- MyPy strict. Full annotations including return types. Models are frozen — `model_copy(update={...})`.
- Docstring coverage ≥ 90% (`make doc-coverage`); `tests/` is excluded.
- **Do not add a method to `SymbolResolver`.** It stands at 9 and `tests/self_parsing/test_architecture.py::test_god_object_baseline_not_exceeded` fires at 10 methods with 5 efferent couplings (`src/cgis/query/analysis/analyzer.py:18`). New helpers go in as module-level functions, which is the precedent `_resolve_self_type_*` set in `_python_functions.py`.
- Full verification before the PR: `make format && make lint && make type-check && make pytest && make doc-coverage`.
- **Never measure against the repo-root `graph.db`** — untracked, gitignored, months stale. Re-ingest to a scratch path.
- Baseline suite: **2114 passed**. `.github/test-count-baseline` is 2110; if the suite grows past 2260 the floor gate demands a re-baseline (drift ceiling 150).

## Setup

Create the worktree first, via the `superpowers:using-git-worktrees` skill. Branch `feat/receiver-resolution-pr2`, based on `main`.

## File structure

| File | Responsibility |
|---|---|
| `src/cgis/resolver/indices.py` (modify) | `SymbolIndex.self_types`, populated by `IndexBuilder.build` from CLASS-node metadata. Also the `classify_fqn` split. |
| `src/cgis/resolver/symbols.py` (modify) | `resolve_self_call` handles the dotted form; a module-level helper resolves a receiver's declared type to a CLASS FQN. |
| `tests/unit/test_resolver.py` (modify) | Resolution behaviour, end to end through `ResolverEngine`. |
| `tests/unit/test_indices.py` (modify, or create if absent) | Index construction and `classify_fqn`. |
| `tests/self_parsing/test_receiver_resolution.py` (**create**) | The calibration gate for this PR. |

---

### Task 1: `self_types` on the index

**Files:**
- Modify: `src/cgis/resolver/indices.py` — the `SymbolIndex` dataclass fields, and `IndexBuilder.build`
- Test: `tests/unit/test_indices.py`

**Interfaces:**
- Consumes: CLASS nodes carrying `metadata["self_types"] = {attr_name: type_fqn}`, written by PR1's extractor.
- Produces: `SymbolIndex.self_types: Mapping[str, dict[str, str]]`, keyed by class FQN. Task 2 reads it.

PR1 writes this map and nothing reads it. `file_imports` is built from FILE-node metadata in the same loop (`indices.py:137-139`); this is the identical move for CLASS nodes.

- [ ] **Step 1: Write the failing test**

If `tests/unit/test_indices.py` does not exist, create it with this content; if it does, append the test and reuse its existing imports.

```python
"""Tests for SymbolIndex construction."""

from cgis.core.models import Node, NodeType
from cgis.resolver.indices import IndexBuilder


def _class_node(fqn: str, self_types: dict[str, str] | None = None) -> Node:
    """A CLASS node, optionally carrying a self_types map."""
    return Node(
        id=fqn,
        type=NodeType.CLASS,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path="pkg/mod.py",
        start_line=1,
        end_line=10,
        language="python",
        metadata={"self_types": self_types} if self_types else {},
    )


def test_self_types_is_indexed_by_class_fqn() -> None:
    """A CLASS node's self_types map reaches the index under the class's own FQN."""
    index = IndexBuilder().build(
        [_class_node("pkg.mod.Adapter", {"client": "pkg.client.SearchClient"})]
    )
    assert index.self_types == {"pkg.mod.Adapter": {"client": "pkg.client.SearchClient"}}


def test_class_without_self_types_is_absent_rather_than_empty() -> None:
    """A class with no annotated attributes contributes no entry at all."""
    index = IndexBuilder().build([_class_node("pkg.mod.Plain")])
    assert "pkg.mod.Plain" not in index.self_types


def test_two_classes_keep_separate_maps() -> None:
    """Same attribute name, different classes, different types — no merging."""
    index = IndexBuilder().build(
        [
            _class_node("pkg.mod.A", {"parser": "pkg.a.PyParser"}),
            _class_node("pkg.mod.B", {"parser": "pkg.b.TsParser"}),
        ]
    )
    assert index.self_types["pkg.mod.A"]["parser"] == "pkg.a.PyParser"
    assert index.self_types["pkg.mod.B"]["parser"] == "pkg.b.TsParser"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_indices.py -v --no-header`
Expected: FAIL — `AttributeError: 'SymbolIndex' object has no attribute 'self_types'`

- [ ] **Step 3: Add the field**

In `src/cgis/resolver/indices.py`, add to the `SymbolIndex` dataclass, directly after the `class_methods` field so the two class-keyed maps sit together:

```python
    # class_fqn -> {attribute_name -> declared type FQN}, from CLASS node metadata.
    # Written by the extractor (spec D1), read by resolve_self_call (spec D7).
    self_types: Mapping[str, dict[str, str]]
```

- [ ] **Step 4: Populate it**

In `IndexBuilder.build`, beside the other accumulators:

```python
        self_types: dict[str, dict[str, str]] = {}
```

In the node loop, beside the `NodeType.FILE` branch that reads `import_map`:

```python
            if node.type == NodeType.CLASS:
                declared = node.metadata.get("self_types")
                if declared:
                    self_types[node.id] = declared
```

And in the `SymbolIndex(...)` construction, beside `class_methods`:

```python
            self_types=MappingProxyType(self_types),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_indices.py -v --no-header`
Expected: PASS, 3 tests.

Then the full suite — adding a required dataclass field breaks any other construction of `SymbolIndex`:
`uv run pytest -q --no-header` (baseline 2114 passed).

If something constructs `SymbolIndex` directly and now fails, add `self_types={}` there rather than giving the field a default: a default would let a future builder forget to populate it and resolve silently to nothing.

- [ ] **Step 6: Type-check and commit**

```bash
uv run mypy src
git add src/cgis/resolver/indices.py tests/unit/test_indices.py
git commit -m "feat(resolver): index self_types by class FQN"
```

---

### Task 2: `resolve_self_call` resolves through the receiver

**Files:**
- Modify: `src/cgis/resolver/symbols.py` — `resolve_self_call`, plus one module-level helper
- Test: `tests/unit/test_resolver.py`

**Interfaces:**
- Consumes: `SymbolIndex.self_types` (Task 1); the existing `_resolve_method_on_class_hierarchy(class_fqn, method_name, visited)` at `symbols.py:115`; `SymbolIndex.map_to_node_fqn`.
- Produces: `resolve_self_call` returning a resolved method FQN for a dotted name. Its one call site (`engine.py:95`) needs no change — it already passes the whole post-`self.` string.

Today `resolve_self_call(source_fqn, "client.search")` looks for a method literally named `client.search` on the owning class, finds nothing, and the edge keeps its `self.client.search` placeholder.

**What the current code already does, and must keep doing:** the plain form (`self.helper()` → `"helper"`) walks up the FQN segments so a nested function still finds its enclosing class, then goes through the inheritance walk. Do not disturb that path.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_resolver.py`, matching the file's existing style:

```python
def test_self_attribute_call_resolves_through_the_declared_type() -> None:
    """`self.client.search()` resolves to SearchClient.search via self_types."""
    code_client = "class SearchClient:\n    def search(self, index):\n        return {}\n"
    code_adapter = (
        "from pkg.client import SearchClient\n"
        "class Adapter:\n"
        "    def __init__(self, client: SearchClient) -> None:\n"
        "        self.client = client\n"
        "    def search(self, index):\n"
        "        return self.client.search(index)\n"
    )
    resolved = _resolve_two("pkg/client.py", code_client, "pkg/adapter.py", code_adapter)
    calls = {(e.source, e.target) for e in resolved if e.type == EdgeType.CALLS}
    assert ("pkg.adapter.Adapter.search", "pkg.client.SearchClient.search") in calls


def test_self_attribute_call_to_a_method_that_does_not_exist_is_not_invented() -> None:
    """A phantom method on an internal receiver must not resolve (spec D7)."""
    code_client = "class SearchClient:\n    def search(self, index):\n        return {}\n"
    code_adapter = (
        "from pkg.client import SearchClient\n"
        "class Adapter:\n"
        "    def __init__(self, client: SearchClient) -> None:\n"
        "        self.client = client\n"
        "    def broken(self):\n"
        "        return self.client.no_such_method()\n"
    )
    resolved = _resolve_two("pkg/client.py", code_client, "pkg/adapter.py", code_adapter)
    targets = {e.target for e in resolved if e.source == "pkg.adapter.Adapter.broken"}
    assert "pkg.client.SearchClient.no_such_method" not in targets


def test_self_attribute_call_finds_an_inherited_method() -> None:
    """Resolution walks EXTENDS, reusing the existing hierarchy search (spec D7)."""
    code_base = (
        "class Base:\n    def ping(self):\n        return 1\n"
        "class Child(Base):\n    pass\n"
    )
    code_user = (
        "from pkg.base import Child\n"
        "class User:\n"
        "    def __init__(self, dep: Child) -> None:\n"
        "        self.dep = dep\n"
        "    def go(self):\n"
        "        return self.dep.ping()\n"
    )
    resolved = _resolve_two("pkg/base.py", code_base, "pkg/user.py", code_user)
    calls = {(e.source, e.target) for e in resolved if e.type == EdgeType.CALLS}
    assert ("pkg.user.User.go", "pkg.base.Base.ping") in calls


def test_two_classes_with_a_same_named_attribute_do_not_share_a_target() -> None:
    """The placeholder collision: one attribute name, two types, two targets."""
    code_parsers = (
        "class PyParser:\n    def parse(self, s):\n        return s\n"
        "class TsParser:\n    def parse(self, s):\n        return s\n"
    )
    code_users = (
        "from pkg.parsers import PyParser, TsParser\n"
        "class PyHandler:\n"
        "    def __init__(self, parser: PyParser) -> None:\n"
        "        self.parser = parser\n"
        "    def go(self):\n"
        "        return self.parser.parse('x')\n"
        "class TsHandler:\n"
        "    def __init__(self, parser: TsParser) -> None:\n"
        "        self.parser = parser\n"
        "    def go(self):\n"
        "        return self.parser.parse('x')\n"
    )
    resolved = _resolve_two("pkg/parsers.py", code_parsers, "pkg/users.py", code_users)
    calls = {(e.source, e.target) for e in resolved if e.type == EdgeType.CALLS}
    assert ("pkg.users.PyHandler.go", "pkg.parsers.PyParser.parse") in calls
    assert ("pkg.users.TsHandler.go", "pkg.parsers.TsParser.parse") in calls


def test_unannotated_receiver_is_left_alone() -> None:
    """No annotation, no guess — the edge keeps its placeholder (spec D1)."""
    code = (
        "class Adapter:\n"
        "    def __init__(self, client) -> None:\n"
        "        self.client = client\n"
        "    def go(self):\n"
        "        return self.client.search()\n"
    )
    nodes, edges = PythonExtractor().parse(code, "pkg/adapter.py")
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    targets = {e.target for e in resolved if e.source == "pkg.adapter.Adapter.go"}
    assert "self.client.search" in targets


def test_deeper_chain_is_out_of_scope() -> None:
    """`self.a.b.c()` keeps its placeholder — two segments only (spec D8)."""
    code_client = "class SearchClient:\n    def search(self, i):\n        return {}\n"
    code_adapter = (
        "from pkg.client import SearchClient\n"
        "class Adapter:\n"
        "    def __init__(self, client: SearchClient) -> None:\n"
        "        self.client = client\n"
        "    def go(self):\n"
        "        return self.client.search('i').first()\n"
    )
    resolved = _resolve_two("pkg/client.py", code_client, "pkg/adapter.py", code_adapter)
    targets = {e.target for e in resolved if e.source == "pkg.adapter.Adapter.go"}
    assert any(t.startswith("self.client.search") for t in targets)
```

Add this helper near the top of the file if an equivalent is not already there:

```python
def _resolve_two(path_a: str, code_a: str, path_b: str, code_b: str) -> list[Edge]:
    """Parse two modules together and return the resolved edges."""
    ex = PythonExtractor()
    nodes_a, edges_a = ex.parse(code_a, path_a)
    nodes_b, edges_b = ex.parse(code_b, path_b)
    resolved, _ = ResolverEngine(nodes_a + nodes_b, edges_a + edges_b).resolve()
    return resolved
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_resolver.py -k "self_attribute or same_named_attribute or deeper_chain or unannotated_receiver" -v --no-header`

Expected: the first, third and fourth FAIL (nothing resolves through a receiver yet). `test_unannotated_receiver_is_left_alone` and `test_deeper_chain_is_out_of_scope` PASS already — they pin behaviour that must survive, not behaviour to add. Say so in your report; a test that passes before and after is a guard, and mislabelling it as a regression test is how a suite starts lying about what it proves.

- [ ] **Step 3: Add the receiver-type helper**

At module level in `src/cgis/resolver/symbols.py` — **not** as a method; `SymbolResolver` is at 9 and the God-Object gate fires at 10:

```python
def receiver_class_fqn(index: SymbolIndex, owner_fqn: str, attr: str) -> str | None:
    """Resolve `self.<attr>` on class `owner_fqn` to the CLASS node it was declared as.

    The declared type is a FQN the extractor built from the annotation and the
    file's import map, so it may carry a layout prefix the graph does not use
    (`cgis.x.Y` against a node id of `src.cgis.x.Y`); `map_to_node_fqn` is what
    reconciles those. A declared type with no CLASS node behind it — a builtin
    written as `pkg.mod.list`, or a third-party class — resolves to None, and
    the caller leaves the edge alone.
    """
    declared = index.self_types.get(owner_fqn, {}).get(attr)
    if declared is None:
        return None
    resolved = index.map_to_node_fqn(declared) or declared
    node = index.nodes.get(resolved)
    if node is None or node.type != NodeType.CLASS:
        return None
    return resolved
```

Add `SymbolIndex` to the existing `from cgis.resolver.indices import ...` line, and `NodeType` to the `from cgis.core.models import ...` line.

- [ ] **Step 4: Teach `resolve_self_call` the dotted form**

Replace the body of `resolve_self_call` in `src/cgis/resolver/symbols.py`:

```python
    def resolve_self_call(self, source_fqn: str, method_name: str) -> str | None:
        """Find the method a `self.…` call reaches, traversing inheritance.

        Two shapes. `self.helper()` arrives as "helper" and is looked up on the
        class that owns the source. `self.client.search()` arrives as
        "client.search": the receiver's declared type comes from `self_types`
        (spec D1), and the method is then searched on that class exactly as it
        would be on the owner — same walk, same inheritance rules (spec D7).

        Only one dot is handled. `self.a.b.c()` is left alone: resolving it
        would need the return type of `self.a.b`, which nothing records
        (spec D8).
        """
        owner = self._owning_class(source_fqn)
        if owner is None:
            return None
        attr, sep, method = method_name.partition(".")
        if not sep:
            return self._resolve_method_on_class_hierarchy(owner, method_name, set())
        if "." in method:
            return None
        receiver = receiver_class_fqn(self.index, owner, attr)
        if receiver is None:
            return None
        return self._resolve_method_on_class_hierarchy(receiver, method, set())

    def _owning_class(self, source_fqn: str) -> str | None:
        """The nearest enclosing class of source_fqn that the index knows methods for.

        Walks up the FQN segments so a nested function (`mod.Cls.method.inner`)
        still finds `mod.Cls`.
        """
        parts = source_fqn.split(".")
        for i in range(len(parts) - 1, 0, -1):
            candidate = ".".join(parts[:i])
            if candidate in self.index.class_methods:
                return candidate
        return None
```

**This adds one method to `SymbolResolver`, taking it from 9 to 10.** Run the God-Object gate immediately (Step 5) — if it fires, make `_owning_class` a module-level function taking `index` as its first parameter, like `receiver_class_fqn` above, and call it as `_owning_class(self.index, source_fqn)`.

- [ ] **Step 5: Run the tests and the gate**

```
uv run pytest tests/unit/test_resolver.py -v --no-header
uv run pytest tests/self_parsing/test_architecture.py -v --no-header
uv run pytest -q --no-header
```

Expected: the resolver tests pass; the architecture gate passes (or you applied the module-level fallback above); the full suite is at least 2114 plus what you added.

A pre-existing test may now resolve an edge it previously saw as a placeholder — that is the feature working. Update such a test to assert the resolved target; do **not** weaken it to accept either.

- [ ] **Step 6: Type-check and commit**

```bash
uv run mypy src
git add src/cgis/resolver/symbols.py tests/unit/test_resolver.py
git commit -m "feat(resolver): resolve self.<attr>.<method> through the declared receiver type (#414)"
```

---

### Task 3: stop calling a `self.` placeholder INTERNAL

**Files:**
- Modify: `src/cgis/resolver/indices.py` — `classify_fqn`, line 91
- Test: `tests/unit/test_indices.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: a changed `classify_fqn` verdict for `self.`-prefixed FQNs. Two call sites: `symbols.py:164` (the phantom-method policy in `_resolve_local_type_call`) and `engine.py:159` (`_ensure_virtual_node`, which sets a virtual node's namespace).

The condition today is:

```python
        if fqn.startswith((".", SELF_PREFIX)):
            return NodeNamespace.INTERNAL
```

A leading `.` is a relative import and is genuinely internal. A leading `self.` is an unresolved placeholder, and calling it INTERNAL makes `get_edge_stats` count it as **resolved** — so `unresolved_ratio` improves as a codebase adopts more DI. That is backwards, and it is why a green `validate` did not warn about the gap #414 reports.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_indices.py`:

```python
from cgis.core.models import NodeNamespace


def test_relative_import_prefix_is_internal() -> None:
    """A leading dot is a relative import — genuinely internal. Unchanged."""
    index = IndexBuilder().build([_class_node("pkg.mod.A")])
    assert index.classify_fqn(".sibling.thing") == NodeNamespace.INTERNAL


def test_self_placeholder_is_not_internal() -> None:
    """An unresolved self.<attr>.<method> placeholder must not count as resolved.

    Classifying it INTERNAL made get_edge_stats score it on the resolved side,
    so unresolved_ratio improved as a codebase adopted more dependency
    injection — the metric moving the wrong way for the exact reason #414 was
    filed.
    """
    index = IndexBuilder().build([_class_node("pkg.mod.A")])
    assert index.classify_fqn("self.client.search") != NodeNamespace.INTERNAL
```

- [ ] **Step 2: Run tests to verify one fails**

Run: `uv run pytest tests/unit/test_indices.py -k "prefix_is_internal or self_placeholder" -v --no-header`
Expected: `test_relative_import_prefix_is_internal` PASSES (it pins what must not change); `test_self_placeholder_is_not_internal` FAILS.

- [ ] **Step 3: Split the condition**

```python
        if fqn.startswith("."):
            return NodeNamespace.INTERNAL
        if fqn.startswith(SELF_PREFIX):
            # An unresolved receiver, not a symbol. UNKNOWN rather than INTERNAL
            # so get_edge_stats counts it as unresolved: classifying it INTERNAL
            # let unresolved_ratio *improve* as a codebase adopted more DI (#414).
            return NodeNamespace.UNKNOWN
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/unit/test_indices.py -v --no-header
uv run pytest -q --no-header
```

The second call site matters here: `_resolve_local_type_call` (`symbols.py:164`) keeps a phantom method only when its receiver classifies EXTERNAL or STDLIB. A `self.`-prefixed candidate never reaches that line — it is dispatched to `resolve_self_call` in `engine.py` before local-type resolution — but confirm no test regresses, and say in your report which tests you checked rather than asserting it in general.

- [ ] **Step 5: Type-check and commit**

```bash
uv run mypy src
git add src/cgis/resolver/indices.py tests/unit/test_indices.py
git commit -m "fix(resolver): an unresolved self. placeholder is not INTERNAL (#414)"
```

---

### Task 4: the calibration gate

**Files:**
- Create: `tests/self_parsing/test_receiver_resolution.py`

**Interfaces:**
- Consumes: everything above, through a real ingest.
- Produces: the gate that fails when receiver resolution silently stops working.

`tests/self_parsing/conftest.py` provides a session-scoped `root_graph_data` fixture yielding `(store, nodes, resolved_edges)` from a pipeline run over `src/` — the same root as the baseline command, so FQNs carry the `cgis.` prefix. Use it; do not add a second ingest.

- [ ] **Step 1: Re-derive the numbers**

Do not take the plan's numbers on faith — the tree has moved since they were measured.

```bash
uv run cgis ingest src --source-root src --output /tmp/pr2-after.db
sqlite3 /tmp/pr2-after.db "select count(*) from edges where target like 'self.%';"
sqlite3 /tmp/pr2-after.db "select round(1.0*sum(case when n.namespace is null or n.namespace='UNKNOWN' or e.target glob 'raw_call:*' then 1 else 0 end)/count(*),4) from edges e left join nodes n on e.target=n.id;"
```

Expected, from the baseline table: 142 placeholder edges before, **about 35 after** (13 unresolvable two-segment sites plus the 22 out of scope). Ratio about 0.19, well under the 0.30 gate.

Report both numbers. If the placeholder count is far from 35, something is not resolving that should — investigate before writing the constant. **Do not widen a tolerance to fit a number.**

- [ ] **Step 2: Write the gate**

```python
"""Self-parse calibration for receiver resolution (spec D7, D8).

Counts are measured against the tree at implementation time. They are a
ratchet, not a constant: a change that legitimately moves them updates the
numbers in the same commit, with the new measurement in the message.
"""

from cgis.core.models import EdgeType

# Measured via: uv run cgis ingest src --source-root src --output /tmp/pr2-after.db
# Before this PR: 142 placeholder edges, of which 120 were two-segment and 107
# of those had a receiver in self_types.
_EXPECTED_PLACEHOLDERS = 35
_TOLERANCE = 8


def test_self_placeholder_count_is_within_the_calibrated_band(root_graph_data) -> None:
    """Receiver resolution silently regressing shows up here as a rising count."""
    _store, _nodes, edges = root_graph_data
    placeholders = [e for e in edges if e.target.startswith("self.")]
    assert abs(len(placeholders) - _EXPECTED_PLACEHOLDERS) <= _TOLERANCE, (
        f"self.* placeholder edges = {len(placeholders)}, expected "
        f"{_EXPECTED_PLACEHOLDERS}±{_TOLERANCE}. Re-measure with a fresh ingest "
        "before changing this number; the repo-root graph.db is stale."
    )


def test_no_two_segment_placeholder_has_an_annotated_receiver(root_graph_data) -> None:
    """Every remaining two-segment placeholder is genuinely unannotated.

    This is the sharp one. The count test above passes if resolution breaks for
    one class and starts working for another; this fails the moment a receiver
    that *is* declared in self_types stops resolving.
    """
    _store, nodes, edges = root_graph_data
    self_types = {n.id: (n.metadata.get("self_types") or {}) for n in nodes}
    missed = []
    for edge in edges:
        if not edge.target.startswith("self."):
            continue
        parts = edge.target.split(".")
        if len(parts) != 3:
            continue
        owner = edge.source.rsplit(".", maxsplit=1)[0]
        if parts[1] in self_types.get(owner, {}):
            missed.append((edge.source, edge.target))
    assert not missed, f"declared receivers that did not resolve: {missed[:10]}"


def test_a_shared_two_segment_placeholder_no_longer_exists(root_graph_data) -> None:
    """The collision this PR removes, pinned on real code.

    Before: self._parser.parse was one vertex for PythonExtractor and
    TypeScriptExtractor, whose parsers are for different languages;
    self._store.get_all_nodes and .get_all_edges likewise merged
    FingerprintExtractor with SemanticUpliftEngine.

    Restricted to two-segment targets on purpose. A fourth collision at
    baseline, self._pick_source_root, has ONE segment: it is `self.<attr>()`,
    a call to a callable attribute, not `self.<attr>.<method>()`. Resolving it
    would mean knowing which function was passed into the constructor, which is
    dataflow, not annotation. D8 does not cover that shape and this PR must not
    be judged on it.
    """
    _store, _nodes, edges = root_graph_data
    owners: dict[str, set[str]] = {}
    for edge in edges:
        if edge.target.startswith("self.") and len(edge.target.split(".")) == 3:
            owners.setdefault(edge.target, set()).add(edge.source.rsplit(".", maxsplit=1)[0])
    shared = {t: sorted(o) for t, o in owners.items() if len(o) > 1}
    assert not shared, f"two-segment placeholders still shared by several classes: {shared}"


def test_resolved_receiver_calls_reach_real_methods(root_graph_data) -> None:
    """A resolved call must land on a node that exists — not a fabricated FQN."""
    _store, nodes, edges = root_graph_data
    known = {n.id for n in nodes}
    method_calls = [
        e
        for e in edges
        if e.type == EdgeType.CALLS and e.confidence >= 0.9 and e.target.startswith("cgis.")
    ]
    dangling = sorted({e.target for e in method_calls if e.target not in known})
    assert not dangling, f"CALLS edges pointing at non-existent nodes: {dangling[:10]}"
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/self_parsing/test_receiver_resolution.py -v --no-header`

If `test_a_shared_two_segment_placeholder_no_longer_exists` fails, read the failure: it names the targets still shared. Three two-segment collisions existed at baseline and all three have declared receivers, so all three should resolve. A survivor means its receiver's type is not in `self_types` — check whether the spec's Risks section already covers that shape before treating it as a bug in this PR.

- [ ] **Step 4: Full verification**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
```

All five must pass. Also run `uv run pytest tests/self_parsing/test_drift.py -v --no-header` — this PR changes edge targets, and the drift fingerprint counts CALLS; if any domain changed status, report it rather than adjusting a tolerance.

- [ ] **Step 5: Commit**

```bash
git add tests/self_parsing/test_receiver_resolution.py
git commit -m "test(self-parsing): calibrate receiver resolution (#414)"
```

---

## Opening the PR

Title: `feat(resolver): resolve self.<attr>.<method> through the declared receiver type (#414)`

State in the description, because a reviewer cannot see it from the diff:

1. **The measured before/after**: placeholder edges 142 → your number, `unresolved_ratio` 0.1834 → yours, with the ingest command.
2. **`unresolved_ratio` goes up, and that is the fix.** The remaining placeholders were being counted as resolved; they are now counted honestly. A reviewer seeing a metric worsen deserves to be told it is the point.
3. **The collision**: four placeholder nodes were shared by several classes at baseline — name them.
4. **What this does not do**: `self.a.b.c()` is untouched (D8); the two `self_types` shapes in the spec's Risks are untouched; #415 remains blocked on #416 and #417.
