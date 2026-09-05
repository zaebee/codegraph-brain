# PR1 — Annotation extractor foundation: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the graph record what a type annotation says — an `attr → type` map on every class, and a `REFERENCES` edge for every internal class named in an annotation.

**Architecture:** Extraction only. A new pure module collects every type name inside an annotation (tree-sitter subtree → set of names); the existing `raw_dep:` speculative-edge road carries those names to the resolver, which gains one branch turning an internal-CLASS hit into `REFERENCES`. A `self_types` map is accumulated during the walk and attached to CLASS node metadata, mirroring how `local_types` is attached to functions. No resolution behavior changes in this PR — #414 consumes `self_types` in PR2.

**Tech Stack:** Python 3.12+, tree-sitter (`tree_sitter_python`), Pydantic v2 frozen models, SQLite, pytest, mypy strict, ruff.

**Spec:** `docs/specs/2026-09-05-annotation-graph-design.md` — read it before starting. This plan implements decisions D1, D3, D4 and D9 only.

## Global Constraints

- MyPy runs in **strict mode**. Every function needs full annotations including return types. Models are frozen — update with `model_copy(update={...})`.
- Docstring coverage minimum is **90%** (`make doc-coverage`, `interrogate`). Every new function and class needs a docstring.
- Full verification before the PR: `make format && make lint && make type-check && make pytest && make doc-coverage`.
- **Never measure against the repo-root `graph.db`.** It is untracked, gitignored, and 113 commits stale. Re-ingest to a scratch path: `uv run cgis ingest src --source-root src --output /tmp/pr1-check.db`. There is no `--db-path` flag; `--output` takes the `.db` path.
- Do **not** modify `clean_python_type_string` in `src/cgis/extractors/_python_types.py`. It answers "what type is this variable" for `local_types` and is correct for that. D9 exists because it is the wrong question for a reference edge.
- One `pytest.raises` block per throwing call (S5778) — a repo pre-commit hook enforces this.

## Setup

Create the worktree first, via the `superpowers:using-git-worktrees` skill. Branch name: `feat/annotation-extractor-pr1`, based on `main`.

## File structure

| File | Responsibility |
|---|---|
| `src/cgis/extractors/_python_annotations.py` (**create**) | D9: given an annotation tree-sitter node, return every type name mentioned. Pure, no I/O, no graph knowledge. |
| `src/cgis/extractors/_python_ast.py` (modify) | Add `enclosing_class_fqn` — nearest enclosing `class_definition`, as an FQN. |
| `src/cgis/extractors/_python_functions.py` (modify) | Emit one `raw_dep:` per name from D9; add return-annotation collection; collect `self_types` from assignments. |
| `src/cgis/extractors/python_extractor.py` (modify) | Thread `self_types_acc` through the walk; attach it to CLASS nodes; route return and class-body annotations. |
| `src/cgis/resolver/symbols.py` (modify) | Add `resolve_internal_class` — a type name to an existing internal CLASS node. |
| `src/cgis/resolver/engine.py` (modify) | `_resolved_dep_edge` gains the internal-CLASS → `REFERENCES` branch. |
| `tests/unit/test_python_annotations.py` (**create**) | D9 collector unit tests. |
| `tests/unit/test_annotation_edges.py` (**create**) | End-to-end extractor+resolver tests over source strings. |
| `tests/unit/test_self_types.py` (**create**) | The four `self_types` sources. |
| `tests/self_parsing/test_annotation_calibration.py` (**create**) | Self-parse counts that fail if any source regresses. |

---

### Task 1: D9 type-name collector

**Files:**
- Create: `src/cgis/extractors/_python_annotations.py`
- Test: `tests/unit/test_python_annotations.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `collect_type_names(annotation_node: BaseNode, code_bytes: bytes) -> list[str]` — every type name in the annotation, in source order, deduplicated. Tasks 4 and 5 depend on this exact name and signature.

Why this exists: `clean_python_type_string` reduces `list[Node]` to `list`. Measured on HEAD, that drops 330 of 577 internal-class annotation references and leaves 11 classes with no edge at all. This function answers the other question — *which* types does this annotation name.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the annotation type-name collector (D9)."""

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

from cgis.extractors._python_annotations import collect_type_names

_PARSER = Parser(Language(tspython.language()))


def _annotation(source: str):
    """Parse `x: <source>` and return the annotation node plus the code bytes."""
    code = f"x: {source}".encode()
    root = _PARSER.parse(code).root_node
    assignment = root.named_children[0].named_children[0]
    type_node = assignment.child_by_field_name("type")
    assert type_node is not None
    return type_node, code


def test_bare_name():
    node, code = _annotation("SearchClient")
    assert collect_type_names(node, code) == ["SearchClient"]


def test_generic_keeps_both_container_and_argument():
    node, code = _annotation("list[Node]")
    assert collect_type_names(node, code) == ["list", "Node"]


def test_nested_generic_reaches_every_argument():
    node, code = _annotation("dict[str, list[Edge]]")
    assert collect_type_names(node, code) == ["dict", "str", "list", "Edge"]


def test_union_keeps_every_member():
    node, code = _annotation("Node | Edge | None")
    assert collect_type_names(node, code) == ["Node", "Edge", "None"]


def test_dotted_name_yields_last_segment():
    node, code = _annotation("models.BaseModel")
    assert collect_type_names(node, code) == ["models.BaseModel"]


def test_string_annotation_is_parsed():
    node, code = _annotation('"list[Node]"')
    assert collect_type_names(node, code) == ["list", "Node"]


def test_duplicates_collapse_preserving_first_position():
    node, code = _annotation("dict[Node, Node]")
    assert collect_type_names(node, code) == ["dict", "Node"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_python_annotations.py -v --no-header`
Expected: FAIL — `ModuleNotFoundError: No module named 'cgis.extractors._python_annotations'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Type-name collection from annotation subtrees (spec D9).

`clean_python_type_string` in `_python_types.py` answers "what type is this
variable" — it reduces `list[Node]` to `list`, which is correct for
`local_types` (the receiver of `x.append()` really is a list). A reference edge
asks the other question: which types does this annotation *name*. Measured on
HEAD, the difference is 330 of 577 internal-class references.
"""

import tree_sitter_python as tspython
from tree_sitter import Language, Parser
from tree_sitter import Node as BaseNode

_STRING_PARSER = Parser(Language(tspython.language()))


def collect_type_names(annotation_node: BaseNode, code_bytes: bytes) -> list[str]:
    """Return every type name mentioned in an annotation, deduplicated, in source order.

    Descends through generics, unions and nested subscripts, so `dict[str,
    list[Edge]]` yields the container and every argument. A dotted reference is
    kept whole (`models.BaseModel`) so the resolver can use its import map. A
    string annotation is re-parsed and its contents collected.
    """
    names: list[str] = []
    _collect(annotation_node, code_bytes, names)
    seen: set[str] = set()
    return [n for n in names if not (n in seen or seen.add(n))]


def _collect(node: BaseNode, code_bytes: bytes, acc: list[str]) -> None:
    """Walk an annotation subtree, appending each type name found to acc."""
    if node.type == "string":
        _collect_from_string(node, code_bytes, acc)
        return
    if node.type in ("identifier", "none", "attribute"):
        acc.append(code_bytes[node.start_byte : node.end_byte].decode("utf-8"))
        if node.type == "attribute":
            return  # a dotted name is one reference, not two
    for child in node.named_children:
        _collect(child, code_bytes, acc)


def _collect_from_string(node: BaseNode, code_bytes: bytes, acc: list[str]) -> None:
    """Re-parse a string annotation (PEP 563 style) and collect the names inside it."""
    raw = code_bytes[node.start_byte : node.end_byte].decode("utf-8").strip("\"'")
    if not raw:
        return
    inner_bytes = f"x: {raw}".encode()
    root = _STRING_PARSER.parse(inner_bytes).root_node
    if not root.named_children:
        return
    assignment = root.named_children[0].named_children[0]
    type_node = assignment.child_by_field_name("type")
    if type_node is not None:
        _collect(type_node, inner_bytes, acc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_python_annotations.py -v --no-header`
Expected: PASS, 7 tests.

If `test_union_keeps_every_member` fails on `None`, check the tree-sitter node type for `None` in a type position — it is `none`, not `identifier`. The implementation above handles both.

- [ ] **Step 5: Type-check and commit**

```bash
uv run mypy src
git add src/cgis/extractors/_python_annotations.py tests/unit/test_python_annotations.py
git commit -m "feat(extractors): collect every type name in an annotation (D9)"
```

---

### Task 2: `enclosing_class_fqn` helper

**Files:**
- Modify: `src/cgis/extractors/_python_ast.py` (add after `get_fqn_prefix`, around line 136)
- Test: `tests/unit/test_python_annotations.py` (append)

**Interfaces:**
- Consumes: `file_path_to_module_fqn`, `extract_node_name` — both already in this module.
- Produces: `enclosing_class_fqn(node: BaseNode, code_bytes: bytes, file_path: str, source_root: str | None) -> str | None`. Task 3 uses it to key `self_types`.

`get_fqn_prefix` already walks upward but collects class *and* function names, so inside `A.__init__` it returns `"A.__init__"`. Task 3 needs the class alone.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_python_annotations.py`:

```python
from cgis.extractors._python_ast import enclosing_class_fqn


def _first_assignment_in(code: str):
    """Parse a module and return its first `assignment` node, depth-first."""
    code_bytes = code.encode()
    stack = [_PARSER.parse(code_bytes).root_node]
    while stack:
        node = stack.pop(0)
        if node.type == "assignment":
            return node, code_bytes
        stack.extend(node.named_children)
    raise AssertionError("no assignment node in source")


def test_enclosing_class_fqn_inside_a_method():
    node, code = _first_assignment_in("class A:\n    def __init__(self):\n        self.x = 1\n")
    assert enclosing_class_fqn(node, code, "pkg/mod.py", None) == "pkg.mod.A"


def test_enclosing_class_fqn_in_a_class_body():
    node, code = _first_assignment_in("class A:\n    x: int\n")
    assert enclosing_class_fqn(node, code, "pkg/mod.py", None) == "pkg.mod.A"


def test_enclosing_class_fqn_is_none_at_module_level():
    node, code = _first_assignment_in("x = 1\n")
    assert enclosing_class_fqn(node, code, "pkg/mod.py", None) is None


def test_enclosing_class_fqn_picks_the_nearest_class():
    node, code = _first_assignment_in("class Outer:\n    class Inner:\n        x: int\n")
    assert enclosing_class_fqn(node, code, "pkg/mod.py", None) == "pkg.mod.Outer.Inner"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_python_annotations.py -k enclosing -v --no-header`
Expected: FAIL — `ImportError: cannot import name 'enclosing_class_fqn'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/cgis/extractors/_python_ast.py`:

```python
def enclosing_class_fqn(
    node: BaseNode, code_bytes: bytes, file_path: str, source_root: str | None
) -> str | None:
    """Return the FQN of the nearest enclosing class, or None outside any class.

    `get_fqn_prefix` collects class *and* function names, so inside `A.__init__`
    it yields "A.__init__". Attribute types are keyed by class, so this walks up
    to the nearest `class_definition` and builds the full nesting path from the
    classes above it, skipping any function in between.
    """
    curr = node.parent
    parts: list[str] = []
    found = False
    while curr:
        if curr.type == "class_definition":
            found = True
            parts.append(extract_node_name(curr.child_by_field_name("name"), code_bytes))
        curr = curr.parent
    if not found:
        return None
    module = file_path_to_module_fqn(file_path, source_root)
    path = ".".join(reversed(parts))
    return f"{module}.{path}" if module else path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_python_annotations.py -v --no-header`
Expected: PASS, 11 tests.

- [ ] **Step 5: Type-check and commit**

```bash
uv run mypy src
git add src/cgis/extractors/_python_ast.py tests/unit/test_python_annotations.py
git commit -m "feat(extractors): add enclosing_class_fqn helper"
```

---

### Task 3: `self_types` on CLASS node metadata

**Files:**
- Modify: `src/cgis/extractors/_python_functions.py` (add `collect_self_type`, after `collect_assignment_type` which ends around line 264)
- Modify: `src/cgis/extractors/python_extractor.py` (`parse` around lines 121-142, `_handle_assignment` around lines 158-194, `_walk` signature around lines 195-206)
- Test: `tests/unit/test_self_types.py`

**Interfaces:**
- Consumes: `enclosing_class_fqn` (Task 2); `TypeResolver.resolve_type_fqn` and `clean_python_type_string` (existing).
- Produces: CLASS nodes carry `metadata["self_types"] = {attr_name: type_fqn}`. PR2's `resolve_self_call` reads this; nothing in PR1 consumes it.

The four sources, all `assignment` nodes distinguished by their fields (verified against tree-sitter):

| Source | `left` | `type` | `right` |
|---|---|---|---|
| class-body annotation `x: T` | `identifier` | present | — |
| `self.x: T = ...` | `attribute` | present | any |
| `self.x = x` (from `__init__` param) | `attribute` | — | `identifier` |
| `self.x = Builder()` | `attribute` | — | `call` |

- [ ] **Step 1: Write the failing test**

```python
"""The four sources that populate a class's self_types map (spec D1)."""

from cgis.extractors.python_extractor import PythonExtractor


def _class_self_types(code: str, class_fqn: str) -> dict[str, str]:
    """Parse source and return the self_types metadata of one class node."""
    nodes, _ = PythonExtractor().parse(code, "pkg/mod.py")
    for node in nodes:
        if node.id == class_fqn:
            types: dict[str, str] = node.metadata.get("self_types") or {}
            return types
    raise AssertionError(f"no node {class_fqn} in {[n.id for n in nodes]}")


def test_init_parameter_assigned_to_attribute():
    code = (
        "from pkg.client import SearchClient\n"
        "class A:\n"
        "    def __init__(self, client: SearchClient) -> None:\n"
        "        self.client = client\n"
    )
    assert _class_self_types(code, "pkg.mod.A") == {"client": "pkg.client.SearchClient"}


def test_class_body_annotation():
    code = "from pkg.client import SearchClient\nclass A:\n    client: SearchClient\n"
    assert _class_self_types(code, "pkg.mod.A") == {"client": "pkg.client.SearchClient"}


def test_annotated_attribute_assignment():
    code = (
        "from pkg.client import SearchClient\n"
        "class A:\n"
        "    def __init__(self) -> None:\n"
        "        self.client: SearchClient = build()\n"
    )
    assert _class_self_types(code, "pkg.mod.A") == {"client": "pkg.client.SearchClient"}


def test_constructor_assignment():
    code = (
        "from pkg.client import SearchClient\n"
        "class A:\n"
        "    def __init__(self) -> None:\n"
        "        self.client = SearchClient()\n"
    )
    assert _class_self_types(code, "pkg.mod.A") == {"client": "pkg.client.SearchClient"}


def test_unannotated_attribute_is_not_guessed():
    code = "class A:\n    def __init__(self, client) -> None:\n        self.client = client\n"
    assert _class_self_types(code, "pkg.mod.A") == {}


def test_generic_annotation_records_the_container_type():
    code = (
        "from pkg.models import Node\n"
        "class A:\n"
        "    items: list[Node]\n"
    )
    assert _class_self_types(code, "pkg.mod.A") == {"items": "pkg.mod.list"}


def test_two_classes_keep_separate_maps():
    code = (
        "from pkg.client import SearchClient\n"
        "from pkg.other import Other\n"
        "class A:\n"
        "    client: SearchClient\n"
        "class B:\n"
        "    client: Other\n"
    )
    assert _class_self_types(code, "pkg.mod.A") == {"client": "pkg.client.SearchClient"}
    assert _class_self_types(code, "pkg.mod.B") == {"client": "pkg.other.Other"}
```

`test_generic_annotation_records_the_container_type` is deliberate: `self_types`
answers "what is the receiver of `self.items.append()`", so `list` is the right
answer here and `clean_python_type_string` is the right function. D9's collector
is for reference edges (Task 4), not for this map. The unresolved `pkg.mod.list`
FQN is what `resolve_type_fqn` returns for an unimported name; PR2 tolerates it.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_self_types.py -v --no-header`
Expected: FAIL — all seven assert `{}` because nothing writes `self_types` yet.

- [ ] **Step 3: Write the collector**

Add to `src/cgis/extractors/_python_functions.py`:

```python
    def collect_self_type(
        self,
        node: BaseNode,
        code_bytes: bytes,
        import_map: dict[str, str] | None,
        class_fqn: str,
        file_path: str,
        init_param_types: dict[str, str],
        acc: dict[str, dict[str, str]],
    ) -> None:
        """Populate acc with attr→FQN for one assignment inside a class (spec D1).

        Handles the four annotated shapes: a class-body annotation, an annotated
        attribute assignment, an attribute assigned from a typed `__init__`
        parameter, and an attribute assigned a constructor call. An attribute
        with no annotation anywhere is left out rather than guessed at.
        """
        left = node.child_by_field_name("left")
        if left is None:
            return
        attr_name = self._assigned_attr_name(left, code_bytes)
        if attr_name is None:
            return

        type_node = node.child_by_field_name("type")
        if type_node is not None:
            raw = code_bytes[type_node.start_byte : type_node.end_byte].decode("utf-8")
            clean = self._types.clean_python_type_string(raw.strip("\"'"))
            if clean:
                acc.setdefault(class_fqn, {})[attr_name] = self._types.resolve_type_fqn(
                    clean, import_map, file_path
                )
            return

        right = node.child_by_field_name("right")
        if right is None:
            return
        if right.type == "identifier":
            param_name = get_identifier(right, code_bytes)
            param_fqn = init_param_types.get(param_name)
            if param_fqn:
                acc.setdefault(class_fqn, {})[attr_name] = param_fqn
            return
        if right.type == "call":
            func_node = right.child_by_field_name("function")
            if func_node is None:
                return
            class_name = get_identifier(func_node, code_bytes)
            if class_name == "unknown" or not class_name[:1].isupper():
                return
            acc.setdefault(class_fqn, {})[attr_name] = self._types.resolve_type_fqn(
                class_name, import_map, file_path
            )

    @staticmethod
    def _assigned_attr_name(left: BaseNode, code_bytes: bytes) -> str | None:
        """Return the attribute name being assigned: `self.x` → "x", `x: T` → "x".

        Returns None for anything else — a subscript target, a tuple unpack, or
        an attribute on a receiver other than `self`.
        """
        if left.type == "identifier":
            return code_bytes[left.start_byte : left.end_byte].decode("utf-8")
        if left.type != "attribute":
            return None
        obj = left.child_by_field_name("object")
        attr = left.child_by_field_name("attribute")
        if obj is None or attr is None:
            return None
        if code_bytes[obj.start_byte : obj.end_byte].decode("utf-8") != "self":
            return None
        return code_bytes[attr.start_byte : attr.end_byte].decode("utf-8")
```

- [ ] **Step 4: Wire it into the walk**

In `src/cgis/extractors/python_extractor.py`:

1. Add `self_types_acc: dict[str, dict[str, str]] | None = None` as a keyword parameter to `_walk` (after `local_types_acc`) and to `_handle_assignment` and `_handle_decorated_definition`, forwarding it at every recursive `self._walk(...)` call and every handler call — there are recursion sites around lines 263-273, 300-310 and 322-332.

2. In `parse`, create the accumulator and attach it:

```python
        self_types_acc: dict[str, dict[str, str]] = {}

        self._walk(
            root_node,
            code_bytes,
            file_path,
            nodes,
            edges,
            import_map=import_map,
            module_fqn=module_fqn,
            local_types_acc=local_types_acc,
            self_types_acc=self_types_acc,
        )

        # Apply accumulated local types from assignments and param annotations
        nodes_by_id = {n.id: i for i, n in enumerate(nodes)}
        for func_id, lt in local_types_acc.items():
            if func_id in nodes_by_id:
                i = nodes_by_id[func_id]
                nodes[i] = nodes[i].model_copy(
                    update={"metadata": {**nodes[i].metadata, "local_types": lt}}
                )
        # Apply accumulated attribute types to their class nodes (spec D1)
        for class_id, st in self_types_acc.items():
            if class_id in nodes_by_id:
                i = nodes_by_id[class_id]
                nodes[i] = nodes[i].model_copy(
                    update={"metadata": {**nodes[i].metadata, "self_types": st}}
                )
```

3. In `_handle_assignment`, before the existing `if current_func_node:` branch:

```python
        class_fqn = enclosing_class_fqn(
            node, code_bytes, file_path, self._pick_source_root(file_path)
        )
        if class_fqn is not None and self_types_acc is not None:
            init_param_types = (
                local_types_acc.get(f"{class_fqn}.__init__", {})
                if local_types_acc is not None
                else {}
            )
            self._functions.collect_self_type(
                node, code_bytes, import_map, class_fqn, file_path,
                init_param_types, self_types_acc,
            )
```

Import `enclosing_class_fqn` from `cgis.extractors._python_ast` at the top of the file.

**Ordering note:** `local_types_acc` for `__init__` is populated by `collect_param_type` when `_walk` reaches the `typed_parameter` nodes, which are children of the `parameters` node and therefore visited *before* the `block` containing `self.x = x`. The lookup above is safe. If a test shows otherwise, do not reorder the walk — read `init_param_types` lazily by moving the `collect_self_type` call to a second pass over collected assignments.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_self_types.py -v --no-header`
Expected: PASS, 7 tests.

Then confirm nothing regressed: `uv run pytest tests/unit -q --no-header`

- [ ] **Step 6: Type-check and commit**

```bash
uv run mypy src
git add src/cgis/extractors/ tests/unit/test_self_types.py
git commit -m "feat(extractors): record attribute types as self_types on class nodes (D1)"
```

---

### Task 4: Annotation edges for every position and every name

**Files:**
- Modify: `src/cgis/extractors/_python_functions.py` (`collect_param_type`, lines 267-312; add `collect_return_annotation` and `emit_annotation_edges`)
- Modify: `src/cgis/extractors/python_extractor.py` (`_walk`, after `process_function_node`; `_handle_assignment`)
- Test: `tests/unit/test_annotation_edges.py`

**Interfaces:**
- Consumes: `collect_type_names` (Task 1), `enclosing_class_fqn` (Task 2).
- Produces: one `DEPENDS_ON` edge with `target=f"raw_dep:{name}"` per distinct type name per annotation position, from parameter, return and `AnnAssign` positions. Task 5 converts the internal-class ones to `REFERENCES`.

Currently `collect_param_type` emits exactly one `raw_dep:` edge, using `clean_python_type_string` — so `list[Node]` emits `raw_dep:list` and `Node` is lost. This task emits one per name from D9's collector, and adds the two missing positions.

Edge ids must stay unique: the current id is `f"{file_path}:rawdep_{start_byte}_{end_byte}"`, which now repeats per name. Append the name.

- [ ] **Step 1: Write the failing test**

```python
"""Annotation positions produce one raw_dep edge per named type (spec D4, D9)."""

from cgis.core.models import EdgeType
from cgis.extractors.python_extractor import PythonExtractor

_RAW_DEP = "raw_dep:"


def _dep_targets(code: str) -> set[str]:
    """Parse source and return every raw_dep: target emitted, without the prefix."""
    _, edges = PythonExtractor().parse(code, "pkg/mod.py")
    return {
        e.target.removeprefix(_RAW_DEP)
        for e in edges
        if e.type == EdgeType.DEPENDS_ON and e.target.startswith(_RAW_DEP)
    }


def test_parameter_annotation_emits_its_type():
    assert "SearchClient" in _dep_targets("def f(c: SearchClient) -> None: pass\n")


def test_generic_parameter_emits_container_and_argument():
    targets = _dep_targets("def f(items: list[Node]) -> None: pass\n")
    assert {"list", "Node"} <= targets


def test_return_annotation_emits_its_type():
    assert "Report" in _dep_targets("def f() -> Report: pass\n")


def test_generic_return_emits_every_argument():
    targets = _dep_targets("def f() -> dict[str, Edge]: pass\n")
    assert {"dict", "str", "Edge"} <= targets


def test_class_body_annotation_emits_its_type():
    assert "SearchClient" in _dep_targets("class A:\n    client: SearchClient\n")


def test_annotated_local_assignment_emits_its_type():
    code = "def f() -> None:\n    x: Report = build()\n"
    assert "Report" in _dep_targets(code)


def test_bare_none_return_emits_nothing():
    assert _dep_targets("def f() -> None: pass\n") == set()


def test_edge_ids_are_unique_across_names_of_one_annotation():
    _, edges = PythonExtractor().parse("def f(x: dict[str, Edge]) -> None: pass\n", "pkg/mod.py")
    dep_ids = [e.id for e in edges if e.target.startswith(_RAW_DEP)]
    assert len(dep_ids) == len(set(dep_ids))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_annotation_edges.py -v --no-header`
Expected: FAIL on `test_generic_parameter_emits_container_and_argument` (only `list` present), `test_return_annotation_emits_its_type`, `test_generic_return_emits_every_argument`, `test_class_body_annotation_emits_its_type`, `test_annotated_local_assignment_emits_its_type`.

- [ ] **Step 3: Write the shared emitter**

Add to `src/cgis/extractors/_python_functions.py`:

```python
    def emit_annotation_edges(
        self,
        type_node: BaseNode,
        code_bytes: bytes,
        source_fqn: str,
        file_path: str,
        edges: list[Edge],
    ) -> None:
        """Emit one speculative `raw_dep:` edge per type named in an annotation.

        The resolver keeps a name that resolves to a DI alias (as DEPENDS_ON) or
        to an internal class (as REFERENCES) and drops the rest, so emitting
        stdlib and third-party names here costs nothing downstream (spec D3/D4).
        """
        for name in collect_type_names(type_node, code_bytes):
            if name == "None":
                continue
            edges.append(
                Edge(
                    id=f"{file_path}:rawdep_{type_node.start_byte}_{type_node.end_byte}_{name}",
                    type=EdgeType.DEPENDS_ON,
                    source=source_fqn,
                    target=f"raw_dep:{name}",
                    confidence=0.1,
                    context=f"Annotation candidate {name}",
                    file_path=file_path,
                    line_number=type_node.start_point.row + 1,
                )
            )

    def collect_return_annotation(
        self,
        node: BaseNode,
        code_bytes: bytes,
        func_node: Node,
        file_path: str,
        edges: list[Edge],
    ) -> None:
        """Emit annotation edges for a function's return type, if it has one."""
        type_node = node.child_by_field_name("return_type")
        if type_node is None:
            return
        self.emit_annotation_edges(type_node, code_bytes, func_node.id, file_path, edges)
```

Import `collect_type_names` from `cgis.extractors._python_annotations` at the top of `_python_functions.py`.

In `collect_param_type`, replace the single-edge `edges.append(...)` block at the end (lines 300-312) with:

```python
        self.emit_annotation_edges(type_node, code_bytes, func_node.id, func_node.file_path, edges)
```

Leave the `acc.setdefault(...)` line above it untouched — `local_types` keeps using `clean_python_type_string`.

- [ ] **Step 4: Wire the two new positions**

In `python_extractor.py` `_walk`, in the `function_definition` branch, after `next_func_node = self._functions.process_function_node(...)`:

```python
            self._functions.collect_return_annotation(
                node, code_bytes, next_func_node, file_path, edges
            )
```

In `_handle_assignment`, after the `collect_self_type` call added in Task 3:

```python
        type_node = node.child_by_field_name("type")
        if type_node is not None:
            source_fqn = current_func_node.id if current_func_node else class_fqn
            if source_fqn:
                self._functions.emit_annotation_edges(
                    type_node, code_bytes, source_fqn, file_path, edges
                )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_annotation_edges.py -v --no-header`
Expected: PASS, 8 tests.

Then the full unit suite: `uv run pytest tests/unit -q --no-header`

Existing tests in `tests/unit/test_di_acceptance.py` assert DI behavior over `raw_dep:` edges. If one fails because a generic annotation now emits extra candidates, that is expected — the extra candidates resolve to nothing and are dropped in Task 5. Fix the assertion to check the target it cares about is present, not that it is the only one.

- [ ] **Step 6: Type-check and commit**

```bash
uv run mypy src
git add src/cgis/extractors/ tests/unit/test_annotation_edges.py
git commit -m "feat(extractors): emit annotation edges for every named type and position (D4, D9)"
```

---

### Task 5: Resolver turns an internal-class candidate into REFERENCES

**Files:**
- Modify: `src/cgis/resolver/symbols.py` (add `resolve_internal_class` after `resolve_dep_candidate`, which ends around line 113)
- Modify: `src/cgis/resolver/engine.py` (`_resolved_dep_edge`, lines 103-111)
- Test: `tests/unit/test_annotation_edges.py` (append)

**Interfaces:**
- Consumes: the `raw_dep:` edges from Task 4.
- Produces: `REFERENCES` edges whose target is an internal CLASS node FQN, confidence 1.0. PR3's orphan query counts incoming `CALLS` and `EXTENDS`; these `REFERENCES` edges are what keep an annotation-only port from reading as dead.

Current behavior (`engine.py:103`): a `raw_dep:` candidate that is not a VARIABLE returns None and the edge is dropped. This adds one branch before the drop.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_annotation_edges.py`:

```python
from cgis.resolver.engine import ResolverEngine


def _resolve(code: str, file_path: str = "pkg/mod.py"):
    """Parse and resolve one source string, returning the final edges."""
    nodes, edges = PythonExtractor().parse(code, file_path)
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    return resolved


def _references(code: str) -> set[tuple[str, str]]:
    """Return (source, target) for every REFERENCES edge after resolution."""
    return {(e.source, e.target) for e in _resolve(code) if e.type == EdgeType.REFERENCES}


def test_parameter_annotation_of_a_local_class_becomes_references():
    code = (
        "class Port:\n    pass\n"
        "def use(p: Port) -> None:\n    pass\n"
    )
    assert ("pkg.mod.use", "pkg.mod.Port") in _references(code)


def test_class_referenced_only_inside_a_generic_still_gets_an_edge():
    code = (
        "class Item:\n    pass\n"
        "def use(items: list[Item]) -> None:\n    pass\n"
    )
    assert ("pkg.mod.use", "pkg.mod.Item") in _references(code)


def test_return_annotation_of_a_local_class_becomes_references():
    code = (
        "class Report:\n    pass\n"
        "def build() -> Report:\n    pass\n"
    )
    assert ("pkg.mod.build", "pkg.mod.Report") in _references(code)


def test_stdlib_annotation_produces_no_reference_edge():
    targets = {t for _, t in _references("def f(x: str) -> None:\n    pass\n")}
    assert targets == set()


def test_unresolvable_annotation_produces_no_reference_edge():
    targets = {t for _, t in _references("def f(x: Nowhere) -> None:\n    pass\n")}
    assert targets == set()


def test_no_raw_dep_target_survives_resolution():
    code = (
        "class Port:\n    pass\n"
        "def use(p: Port, items: list[int]) -> None:\n    pass\n"
    )
    assert not [e for e in _resolve(code) if e.target.startswith(_RAW_DEP)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_annotation_edges.py -k references -v --no-header`
Expected: FAIL — the three positive tests find no `REFERENCES` edges; the candidates are dropped.

- [ ] **Step 3: Add the resolver method**

Add to `src/cgis/resolver/symbols.py`:

```python
    def resolve_internal_class(
        self, name: str, source_fqn: str, edge_file_path: str | None
    ) -> str | None:
        """Resolve a type name to an existing internal CLASS node, or None.

        Reuses `resolve_class_ref` (import map first, then the global symbol
        index) and then verifies the result is a class that actually exists in
        the graph. `resolve_class_ref` can return an import-map FQN for a symbol
        with no node — a third-party type — so the membership check is what
        keeps external names out (spec D3).
        """
        resolved = self.resolve_class_ref(name, source_fqn, edge_file_path)
        if resolved is None:
            return None
        node = self.index.nodes.get(resolved)
        if node is None or node.type != NodeType.CLASS:
            return None
        return resolved
```

Add `NodeType` to the existing `from cgis.core.models import ...` line in that file.

- [ ] **Step 4: Add the engine branch**

Replace `_resolved_dep_edge` in `src/cgis/resolver/engine.py`:

```python
    def _resolved_dep_edge(self, edge: Edge) -> Edge | None:
        """Resolve a raw_dep: candidate edge, or None when it must be dropped (spec §3.3).

        A candidate resolves in one of two ways: to a DI alias (a VARIABLE node),
        which keeps it a DEPENDS_ON wiring edge, or to an internal class, which
        makes it a REFERENCES annotation edge (spec D4). Anything else is a
        speculative candidate that must not leak into the output.
        """
        dep_name = edge.target.removeprefix(RAW_DEP_PREFIX)
        dep_target = self._resolver.resolve_dep_candidate(dep_name, edge.source, edge.file_path)
        if dep_target is not None:
            return edge.model_copy(update={"target": dep_target, "confidence": 1.0})
        class_target = self._resolver.resolve_internal_class(
            dep_name, edge.source, edge.file_path
        )
        if class_target is not None:
            return edge.model_copy(
                update={
                    "target": class_target,
                    "type": EdgeType.REFERENCES,
                    "confidence": 1.0,
                }
            )
        return None
```

Add `EdgeType` to the `from cgis.core.models import ...` block at the top of `engine.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_annotation_edges.py -v --no-header`
Expected: PASS, 14 tests.

Then the full suite: `uv run pytest -q --no-header`

- [ ] **Step 6: Type-check and commit**

```bash
uv run mypy src
git add src/cgis/resolver/ tests/unit/test_annotation_edges.py
git commit -m "feat(resolver): resolve an annotation candidate to a REFERENCES edge (D4)"
```

---

### Task 6: Self-parse calibration and the drift check

**Files:**
- Create: `tests/self_parsing/test_annotation_calibration.py`
- Test: itself

**Interfaces:**
- Consumes: everything above, through a real ingest of `src/`.
- Produces: nothing consumed by later tasks. This is the gate that fails when a source stops being collected.

The spec's measured targets, re-derived at implementation time. A count that merely asserts "> 0" passes whether or not the four sources work — these assert the measured value with a tolerance band, so losing one source fails.

- [ ] **Step 1: Write the calibration test**

`tests/self_parsing/conftest.py` already provides a session-scoped
`root_graph_data` fixture yielding `(store, nodes, resolved_edges)` from a
pipeline run over `src/` — the same root as the measurement command, so FQNs
carry the `cgis.` prefix. Use it; do not add a second ingest.

```python
"""Self-parse calibration for annotation extraction (spec D3, D9).

Counts are measured against HEAD at the time PR1 was written. They are a
ratchet, not a constant: a change that legitimately moves them should update
the numbers in the same commit, with the new measurement in the message.
"""

from cgis.core.models import EdgeType, NodeType

# Measured on HEAD, 2026-09-05, via:
#   uv run cgis ingest src --source-root src --output /tmp/pr1-check.db
_EXPECTED_REFERENCES = 596
_TOLERANCE = 60  # ~10%: absorbs ordinary code churn, not a lost source


def test_reference_edge_count_is_within_the_calibrated_band(root_graph_data):
    """A lost annotation position or a regression to the cleaned head fails here."""
    _store, _nodes, edges = root_graph_data
    refs = [e for e in edges if e.type == EdgeType.REFERENCES]
    assert abs(len(refs) - _EXPECTED_REFERENCES) <= _TOLERANCE, (
        f"REFERENCES edges = {len(refs)}, expected {_EXPECTED_REFERENCES}±{_TOLERANCE}. "
        "Re-measure with a fresh ingest before changing this number; the repo-root "
        "graph.db is stale and must not be used."
    )


def test_every_reference_target_is_an_internal_class(root_graph_data):
    """D3: stdlib, third-party and unresolved annotations must not produce edges."""
    _store, nodes, edges = root_graph_data
    classes = {n.id for n in nodes if n.type == NodeType.CLASS}
    bad = sorted({e.target for e in edges if e.type == EdgeType.REFERENCES} - classes)
    assert not bad, f"REFERENCES edges pointing at non-class nodes: {bad[:10]}"


def test_classes_referenced_only_inside_generics_have_edges(root_graph_data):
    """D9's reason for existing: these 11 get zero edges under the cleaned-head rule."""
    only_in_generics = {
        "AmbiguousEntry", "ArchitecturalAnomaly", "Bridge", "Community",
        "GoldenComment", "NodeMetric", "PrPlan", "SliceCounts", "UnionRun",
    }
    _store, _nodes, edges = root_graph_data
    targets = {
        e.target.rsplit(".", maxsplit=1)[-1]
        for e in edges
        if e.type == EdgeType.REFERENCES
    }
    missing = sorted(only_in_generics - targets)
    assert not missing, f"classes referenced only inside generics have no edge: {missing}"


def test_classes_carry_self_types(root_graph_data):
    """D1: at least the classes measured to have annotated attributes have the map."""
    _store, nodes, _edges = root_graph_data
    with_map = [
        n for n in nodes if n.type == NodeType.CLASS and n.metadata.get("self_types")
    ]
    assert len(with_map) >= 30, f"only {len(with_map)} classes carry self_types"
```

`DuckDBAnalyzer` and `_IdAllocator` are omitted from the generics list: both were
measured as referenced only inside generics, but they are also constructed
directly, so they would pass this test for the wrong reason.

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/self_parsing/test_annotation_calibration.py -v --no-header`

If the count differs from 596, **do not adjust the tolerance to fit.** Re-derive it and find out which position is missing:

```bash
uv run cgis ingest src --source-root src --output /tmp/pr1-check.db
sqlite3 /tmp/pr1-check.db "select count(*) from edges where type='REFERENCES';"
sqlite3 /tmp/pr1-check.db "select source, target from edges where type='REFERENCES' limit 20;"
```

Update `_EXPECTED_REFERENCES` to the measured value only once you can explain the difference.

- [ ] **Step 3: Check the drift status column (spec D2)**

The spec flags one exposure: `edge_count = len(internal_edges)` in
`query/drift/fingerprint.py:346` counts every edge type, and `drift.py:365`
returns `status="no_signal"` when it is zero — so a `REFERENCES` edge could flip
a domain from `no_signal` to scored.

Run: `uv run pytest tests/self_parsing/test_drift.py -v --no-header`

Expected: PASS unchanged. If any domain changed status, report it in the PR
description rather than adjusting a tolerance — the spec predicts no realistic
exposure, and a domain that moved is evidence the prediction was wrong.

- [ ] **Step 4: Full verification**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
```

All five must pass. `doc-coverage` fails below 90% — the new module and every new
method need docstrings.

- [ ] **Step 5: Commit**

```bash
git add tests/self_parsing/test_annotation_calibration.py
git commit -m "test(self-parsing): calibrate annotation edge extraction (D3, D9)"
```

---

## Opening the PR

Title: `feat(extractors): record what annotations say — self_types and REFERENCES edges (#414, #415)`

The description must state, because reviewers cannot see it from the diff:

1. **Traversal density.** `impact` and `trace_flow` apply no edge-type filter by default, so +596 `REFERENCES` edges widen their results. This is intended — an annotation is a real dependency — but it changes existing output.
2. **The measured count**, with the ingest command that produced it, and the note that the repo-root `graph.db` is stale and was not used.
3. **The drift status result** from Task 6 Step 3.
4. **What this PR does not do:** no resolution behavior changes. `self_types` is written but nothing reads it until PR2.

Link both issues; the PR closes neither.
