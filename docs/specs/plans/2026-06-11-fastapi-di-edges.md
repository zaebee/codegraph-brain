# FastAPI DI Edges Implementation Plan (#161 slice 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Model FastAPI dependency-injection wiring as `DEPENDS_ON` edges and DI-alias `VARIABLE` nodes so `analyze_impact`/`trace_flow` surface `Depends()` consumers.

**Architecture:** Three extractor emissions (direct DI hook in `_process_call_node`, module-level alias nodes, speculative `raw_dep:` candidates per typed param) + one resolver branch (`raw_dep:` resolves to VARIABLE nodes or drops). Spec: `docs/specs/2026-06-11-fastapi-di-edges-design.md` — read §3 before starting.

**Tech Stack:** Python 3.12, tree-sitter, Pydantic v2 (frozen models), pytest, mypy strict.

**File map:**
- `src/cgis/core/models.py` — add `EdgeType.DEPENDS_ON`
- `src/cgis/extractors/python_extractor.py` — `_DI_CALL_NAMES`, `_di_provider_name`, DI hook in `_process_call_node`, `_find_di_calls`, `_process_module_assignment`, `raw_dep:` emission in `_collect_param_type`
- `src/cgis/resolver/engine.py` — `RAW_DEP_PREFIX`, `_variable_symbols`/`_file_variable_symbols`, `_resolve_dep_candidate`, drop branch in `resolve()`
- Tests: `tests/unit/test_python_extractor.py`, `tests/unit/test_resolver.py`, `tests/unit/test_di_acceptance.py` (new)

**Conventions:** run tests with `uv run pytest <path>::<name> -v`. Commit after every green task. MyPy strict — full annotations everywhere. Docstrings on every new method (interrogate ≥90%).

---

### Task 1: `EdgeType.DEPENDS_ON` + direct DI hook in `_process_call_node`

Covers spec §3.1 and §3.2b. `_walk` already recurses into parameter subtrees with `current_func_node` set, so every `Depends(...)`/`Security(...)` call — in a default, inside `Annotated[...]`, or under wrappers — reaches `_process_call_node`. One hook there is the whole feature.

**Files:**
- Modify: `src/cgis/core/models.py` (EdgeType enum, after `CONSUMES = "CONSUMES"`)
- Modify: `src/cgis/extractors/python_extractor.py` (`_process_call_node`, ~line 611)
- Test: `tests/unit/test_python_extractor.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_python_extractor.py`:

```python
def test_depends_in_param_default_emits_depends_on(extractor: PythonExtractor) -> None:
    code = """
from fastapi import Depends

def get_db():
    pass

def handler(db = Depends(get_db)):
    pass
"""
    _nodes, edges = extractor.parse(code, "api.py")

    dep_edges = [e for e in edges if e.type == EdgeType.DEPENDS_ON]
    assert len(dep_edges) == 1
    assert dep_edges[0].source == "api.handler"
    assert dep_edges[0].target == "raw_call:get_db"
    # The plain CALLS edge to Depends itself is unchanged (regression guard)
    assert any(e.type == EdgeType.CALLS and e.target == "raw_call:Depends" for e in edges)


def test_depends_inside_annotated_emits_depends_on(extractor: PythonExtractor) -> None:
    code = """
from typing import Annotated
from fastapi import Depends

def resolve_owner():
    pass

def handler(owner: Annotated[object, Depends(resolve_owner)]):
    pass
"""
    _nodes, edges = extractor.parse(code, "api.py")

    dep_edges = [e for e in edges if e.type == EdgeType.DEPENDS_ON]
    assert any(
        e.source == "api.handler" and e.target == "raw_call:resolve_owner" for e in dep_edges
    )


def test_depends_under_union_wrapper_emits_depends_on(extractor: PythonExtractor) -> None:
    code = """
from typing import Annotated
from fastapi import Depends

def resolve_owner():
    pass

def handler(owner: Annotated[object, Depends(resolve_owner)] | None = None):
    pass
"""
    _nodes, edges = extractor.parse(code, "api.py")

    assert any(
        e.type == EdgeType.DEPENDS_ON and e.target == "raw_call:resolve_owner" for e in edges
    )


def test_security_call_emits_depends_on(extractor: PythonExtractor) -> None:
    code = """
from fastapi import Security

def get_scopes():
    pass

def handler(scopes = Security(get_scopes)):
    pass
"""
    _nodes, edges = extractor.parse(code, "api.py")

    assert any(
        e.type == EdgeType.DEPENDS_ON and e.target == "raw_call:get_scopes" for e in edges
    )


def test_argless_depends_emits_no_depends_on(extractor: PythonExtractor) -> None:
    code = """
from fastapi import Depends

def handler(db = Depends()):
    pass
"""
    _nodes, edges = extractor.parse(code, "api.py")

    assert not any(e.type == EdgeType.DEPENDS_ON for e in edges)


def test_depends_with_lambda_arg_emits_no_depends_on(extractor: PythonExtractor) -> None:
    code = """
from fastapi import Depends

def handler(db = Depends(lambda: None)):
    pass
"""
    _nodes, edges = extractor.parse(code, "api.py")

    assert not any(e.type == EdgeType.DEPENDS_ON for e in edges)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_python_extractor.py -k depends_on -v`
Expected: FAIL — `AttributeError: DEPENDS_ON` (enum member does not exist yet).

- [ ] **Step 3: Add the enum member**

In `src/cgis/core/models.py`, Behavioral/Execution group of `EdgeType` (after `CONSUMES = "CONSUMES"`):

```python
    DEPENDS_ON = "DEPENDS_ON"
```

Note: distinct from the semantic `DOMAIN_DEPENDS_ON` (L3, quotient-level between domain nodes) — `DEPENDS_ON` is code-level DI wiring. No consumer matches edge types by name pattern (spec §3.1).

- [ ] **Step 4: Implement the hook**

In `src/cgis/extractors/python_extractor.py`. First, class-level constant (near `_GENERIC_WRAPPERS`, ~line 717):

```python
    _DI_CALL_NAMES: frozenset[str] = frozenset({"Depends", "Security"})
```

New helper (place right after `_process_call_node`):

```python
    def _di_provider_name(self, call_node: BaseNode, code_bytes: bytes) -> str | None:
        """Return the first positional argument's identifier/dotted name, or None.

        None for argless calls, keyword-only calls, and non-name arguments
        (lambdas, calls, subscripts) — those emit no DEPENDS_ON edge (spec §3.2a/b).
        """
        args = call_node.child_by_field_name("arguments")
        if not args:
            return None
        for child in args.named_children:
            if child.type == "keyword_argument":
                continue
            if child.type in ("identifier", "attribute"):
                name = self._get_identifier(child, code_bytes)
                return name if name != "unknown" else None
            return None
        return None
```

Extend `_process_call_node` — append after the existing `edges.append(...)` for the CALLS edge, inside the `if child:` block:

```python
            if call_name in self._DI_CALL_NAMES:
                provider = self._di_provider_name(node, code_bytes)
                if provider:
                    edges.append(
                        Edge(
                            id=f"{file_path}:dep_{node.start_byte}_{node.end_byte}",
                            type=EdgeType.DEPENDS_ON,
                            source=source_id,
                            target=f"raw_call:{provider}",
                            confidence=0.5,
                            context=f"DI dependency on {provider}",
                            file_path=file_path,
                            line_number=node.start_point.row + 1,
                        )
                    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_python_extractor.py -v`
Expected: all PASS (new 6 + existing suite untouched).

- [ ] **Step 6: Commit**

```bash
git add src/cgis/core/models.py src/cgis/extractors/python_extractor.py tests/unit/test_python_extractor.py
git commit -m "feat(extractor): DEPENDS_ON edges for Depends/Security in function params (#161)"
```

---

### Task 2: Module-level DI alias nodes

Covers spec §3.2a. Module-level assignments whose RHS subtree contains a DI call become `VARIABLE` nodes plus `alias —DEPENDS_ON→ raw_call:<provider>` edges. Class-body and tuple-target assignments are skipped. FQN is `f"{module_fqn}.{name}"` — NOT `_get_id` (assignments have no `name` field).

**Files:**
- Modify: `src/cgis/extractors/python_extractor.py` (`_walk` ~line 185, new methods)
- Test: `tests/unit/test_python_extractor.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_python_extractor.py`:

```python
def test_module_level_di_alias_annotated_form(extractor: PythonExtractor) -> None:
    code = """
from typing import Annotated
from fastapi import Depends

def resolve_owner():
    pass

ResolvedOwnerDep = Annotated[object, Depends(resolve_owner)]
"""
    nodes, edges = extractor.parse(code, "deps.py")

    alias = next(n for n in nodes if n.name == "ResolvedOwnerDep")
    assert alias.type == NodeType.VARIABLE
    assert alias.id == "deps.ResolvedOwnerDep"

    dep_edges = [e for e in edges if e.type == EdgeType.DEPENDS_ON]
    assert len(dep_edges) == 1
    assert dep_edges[0].source == "deps.ResolvedOwnerDep"
    assert dep_edges[0].target == "raw_call:resolve_owner"


def test_module_level_di_alias_direct_form(extractor: PythonExtractor) -> None:
    code = """
from fastapi import Depends

def get_db():
    pass

DbDep = Depends(get_db)
"""
    nodes, edges = extractor.parse(code, "deps.py")

    alias = next(n for n in nodes if n.name == "DbDep")
    assert alias.type == NodeType.VARIABLE
    assert any(
        e.type == EdgeType.DEPENDS_ON
        and e.source == "deps.DbDep"
        and e.target == "raw_call:get_db"
        for e in edges
    )


def test_plain_module_constants_emit_no_nodes(extractor: PythonExtractor) -> None:
    code = """
LIMIT = 10
NAMES = ["a", "b"]
ALIAS = SomeType
"""
    nodes, edges = extractor.parse(code, "consts.py")

    assert not any(n.type == NodeType.VARIABLE for n in nodes)
    assert not any(e.type == EdgeType.DEPENDS_ON for e in edges)


def test_argless_depends_alias_node_without_edge(extractor: PythonExtractor) -> None:
    code = """
from typing import Annotated
from fastapi import Depends

AutoDep = Annotated[object, Depends()]
"""
    nodes, edges = extractor.parse(code, "deps.py")

    assert any(n.type == NodeType.VARIABLE and n.name == "AutoDep" for n in nodes)
    assert not any(e.type == EdgeType.DEPENDS_ON for e in edges)


def test_tuple_target_di_assignment_skipped(extractor: PythonExtractor) -> None:
    code = """
from fastapi import Depends

def get_db():
    pass

a, b = Depends(get_db), None
"""
    nodes, edges = extractor.parse(code, "deps.py")

    assert not any(n.type == NodeType.VARIABLE for n in nodes)
    assert not any(e.type == EdgeType.DEPENDS_ON for e in edges)


def test_class_body_di_assignment_skipped(extractor: PythonExtractor) -> None:
    code = """
from fastapi import Depends

def get_db():
    pass

class Config:
    db = Depends(get_db)
"""
    nodes, edges = extractor.parse(code, "deps.py")

    assert not any(n.type == NodeType.VARIABLE for n in nodes)
    assert not any(e.type == EdgeType.DEPENDS_ON for e in edges)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_python_extractor.py -k "alias or constants or tuple_target or class_body" -v`
Expected: FAIL — `StopIteration` (no VARIABLE node found) / assertion errors.

- [ ] **Step 3: Implement**

In `src/cgis/extractors/python_extractor.py`. New methods (place after `_di_provider_name`):

```python
    def _find_di_calls(self, node: BaseNode, code_bytes: bytes) -> list[BaseNode]:
        """Return all call nodes in the subtree whose callee is a DI name (spec §3.2a)."""
        found: list[BaseNode] = []
        stack = [node]
        while stack:
            curr = stack.pop()
            if curr.type == "call":
                fn = curr.child_by_field_name("function")
                if fn and self._get_identifier(fn, code_bytes) in self._DI_CALL_NAMES:
                    found.append(curr)
            stack.extend(curr.children)
        return found

    def _process_module_assignment(
        self,
        node: BaseNode,
        code_bytes: bytes,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
        module_fqn: str,
    ) -> None:
        """Emit a VARIABLE alias node + DEPENDS_ON edges for module-level DI assignments.

        Only fires for `Name = <RHS containing Depends/Security>` with a plain
        identifier LHS at true module level (class bodies excluded by the
        caller). Plain constants never reach the node list (spec §3.2a).
        """
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if not left or not right or left.type != "identifier":
            return
        di_calls = self._find_di_calls(right, code_bytes)
        if not di_calls:
            return
        name = self._get_identifier(left, code_bytes)
        if name == "unknown":
            return
        alias_id = f"{module_fqn}.{name}" if module_fqn else name
        nodes.append(
            Node(
                id=alias_id,
                type=NodeType.VARIABLE,
                name=name,
                file_path=file_path,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                language=self.LANG,
            )
        )
        for call in di_calls:
            provider = self._di_provider_name(call, code_bytes)
            if not provider:
                continue
            edges.append(
                Edge(
                    id=f"{file_path}:dep_{call.start_byte}_{call.end_byte}",
                    type=EdgeType.DEPENDS_ON,
                    source=alias_id,
                    target=f"raw_call:{provider}",
                    confidence=0.5,
                    context=f"DI alias for {provider}",
                    file_path=file_path,
                    line_number=node.start_point.row + 1,
                )
            )
```

Wire into `_walk` — extend the existing assignment branch (~line 185):

```python
        elif node.type == "assignment" and current_func_node and local_types_acc is not None:
            self._collect_assignment_type(
                node, code_bytes, import_map, current_func_node, local_types_acc
            )
        elif (
            node.type == "assignment"
            and current_func_node is None
            and self._get_fqn_prefix(node, code_bytes) is None
        ):
            # True module level: not in a function (current_func_node) and not
            # in a class body (_get_fqn_prefix). Class-body DI aliases are out
            # of scope (spec §6).
            self._process_module_assignment(
                node, code_bytes, file_path, nodes, edges, module_fqn or ""
            )
```

(The first `elif` is the existing code, unchanged — shown for placement.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_python_extractor.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/extractors/python_extractor.py tests/unit/test_python_extractor.py
git commit -m "feat(extractor): module-level DI alias VARIABLE nodes (#161)"
```

---

### Task 3: `raw_dep:` candidates from typed parameters

Covers spec §3.2c. Every typed parameter whose cleaned annotation is a plain/dotted name additionally emits a speculative `func —DEPENDS_ON→ raw_dep:<TypeName>` edge at confidence 0.1. No extractor-side filtering — the resolver drops non-aliases (Task 4). Requires threading `edges` into `_collect_param_type` (explicit signature change, spec §3.2c).

**Files:**
- Modify: `src/cgis/extractors/python_extractor.py` (`_collect_param_type` ~line 688, its call site in `_walk` ~line 194)
- Test: `tests/unit/test_python_extractor.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_python_extractor.py`:

```python
def test_typed_param_emits_raw_dep_candidate(extractor: PythonExtractor) -> None:
    code = """
def handler(owner: PublishedOwnerDep):
    pass
"""
    _nodes, edges = extractor.parse(code, "routes.py")

    cands = [e for e in edges if e.target == "raw_dep:PublishedOwnerDep"]
    assert len(cands) == 1
    assert cands[0].type == EdgeType.DEPENDS_ON
    assert cands[0].source == "routes.handler"
    assert cands[0].confidence == pytest.approx(0.1)


def test_untyped_param_emits_no_raw_dep_candidate(extractor: PythonExtractor) -> None:
    code = """
def handler(owner):
    pass
"""
    _nodes, edges = extractor.parse(code, "routes.py")

    assert not any(e.target.startswith("raw_dep:") for e in edges)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_python_extractor.py -k raw_dep -v`
Expected: first test FAILS (no `raw_dep:` edge emitted yet), second passes vacuously.

- [ ] **Step 3: Implement**

In `src/cgis/extractors/python_extractor.py`, change `_collect_param_type`'s signature and add the emission at the end:

```python
    def _collect_param_type(
        self,
        node: BaseNode,
        code_bytes: bytes,
        import_map: dict[str, str] | None,
        func_node: Node,
        acc: dict[str, dict[str, str]],
        edges: list[Edge],
    ) -> None:
        """Populate acc with param→FQN for typed parameter annotations.

        Also emits a speculative `raw_dep:` DEPENDS_ON candidate per typed
        parameter; the resolver keeps it only when it resolves to a DI alias
        (VARIABLE node) and drops it otherwise (spec §3.2c).
        """
        if not node.named_children:
            return
        name_node = node.named_children[0]
        type_node = node.child_by_field_name("type")
        if not type_node:
            return
        var_name = self._get_identifier(name_node, code_bytes)
        if var_name == "unknown":
            return
        # Slice raw bytes to capture union/generic types like `A | None` or `list[X]`
        raw_type = (
            code_bytes[type_node.start_byte : type_node.end_byte].decode("utf-8").strip("\"'")
        )
        clean_type = self._clean_python_type_string(raw_type)
        if not clean_type:
            return
        acc.setdefault(func_node.id, {})[var_name] = self._resolve_type_fqn(
            clean_type, import_map, func_node.file_path
        )
        edges.append(
            Edge(
                id=f"{func_node.file_path}:rawdep_{node.start_byte}_{node.end_byte}",
                type=EdgeType.DEPENDS_ON,
                source=func_node.id,
                target=f"raw_dep:{clean_type}",
                confidence=0.1,
                context=f"Annotation candidate {clean_type}",
                file_path=func_node.file_path,
                line_number=node.start_point.row + 1,
            )
        )
```

Update the call site in `_walk` (~line 194):

```python
            self._collect_param_type(
                node, code_bytes, import_map, current_func_node, local_types_acc, edges
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_python_extractor.py tests/unit/test_pipeline.py -v`
Expected: all PASS. If a pre-existing extractor/pipeline test asserts an exact edge count and now fails, update that count — the new `raw_dep:` candidates are intentional (note the change in the commit message).

- [ ] **Step 5: Commit**

```bash
git add src/cgis/extractors/python_extractor.py tests/unit/test_python_extractor.py
git commit -m "feat(extractor): speculative raw_dep: candidates per typed param (#161)"
```

---

### Task 4: Resolver — `raw_dep:` resolution with drop policy

Covers spec §3.3. New VARIABLE-only indices, a `raw_dep:` branch in `resolve()` that keeps the edge only when it lands on a VARIABLE node and otherwise drops it with an explicit `continue`. The literal `raw_dep:` must never leak into output. `_global_symbols` is untouched — call resolution behavior must not change.

**Files:**
- Modify: `src/cgis/resolver/engine.py`
- Test: `tests/unit/test_resolver.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_resolver.py` (the `_file_node` helper at the top of the file already exists — reuse it):

```python
def _alias_node(fqn: str, file_path: str) -> Node:
    """Helper: build a VARIABLE node representing a DI alias."""
    return Node(
        id=fqn,
        type=NodeType.VARIABLE,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=5,
        end_line=5,
    )


def _func_node(fqn: str, file_path: str) -> Node:
    """Helper: build a FUNCTION node."""
    return Node(
        id=fqn,
        type=NodeType.FUNCTION,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=3,
    )


def _raw_dep_edge(source: str, name: str, file_path: str) -> Edge:
    """Helper: build a speculative raw_dep: candidate edge."""
    return Edge(
        id=f"{file_path}:rawdep_1",
        source=source,
        target=f"raw_dep:{name}",
        type=EdgeType.DEPENDS_ON,
        confidence=0.1,
        file_path=file_path,
    )


def test_raw_dep_resolves_to_same_file_variable() -> None:
    """A raw_dep: candidate matching a VARIABLE node in the graph is kept."""
    nodes = [
        _func_node("deps.resolve_published_owner", "deps.py"),
        _alias_node("deps.ResolvedOwnerDep", "deps.py"),
    ]
    edges = [_raw_dep_edge("deps.resolve_published_owner", "ResolvedOwnerDep", "deps.py")]

    resolved, _ = ResolverEngine(nodes, edges).resolve()

    assert len(resolved) == 1
    assert resolved[0].target == "deps.ResolvedOwnerDep"
    assert resolved[0].confidence == 1.0


def test_raw_dep_resolves_cross_file_via_import_map() -> None:
    """A raw_dep: candidate resolves through the consuming file's import map."""
    nodes = [
        _file_node("routes.py", {"PublishedOwnerDep": "deps.PublishedOwnerDep"}),
        _func_node("routes.get_vehicle", "routes.py"),
        _alias_node("deps.PublishedOwnerDep", "deps.py"),
    ]
    edges = [_raw_dep_edge("routes.get_vehicle", "PublishedOwnerDep", "routes.py")]

    resolved, _ = ResolverEngine(nodes, edges).resolve()

    assert len(resolved) == 1
    assert resolved[0].target == "deps.PublishedOwnerDep"


def test_raw_dep_to_class_is_dropped() -> None:
    """A raw_dep: candidate resolving to a non-VARIABLE node is dropped."""
    nodes = [
        _func_node("routes.get_vehicle", "routes.py"),
        Node(
            id="models.User",
            type=NodeType.CLASS,
            name="User",
            file_path="models.py",
            start_line=1,
            end_line=5,
        ),
    ]
    edges = [_raw_dep_edge("routes.get_vehicle", "User", "routes.py")]

    resolved, _ = ResolverEngine(nodes, edges).resolve()

    assert resolved == []


def test_raw_dep_unresolved_is_dropped_and_never_leaks() -> None:
    """An unresolved raw_dep: target never appears in the resolved edge set."""
    nodes = [_func_node("routes.get_vehicle", "routes.py")]
    edges = [
        _raw_dep_edge("routes.get_vehicle", "TotallyUnknown", "routes.py"),
        Edge(
            id="edge_call",
            source="routes.get_vehicle",
            target="raw_call:unknown_fn",
            type=EdgeType.CALLS,
            confidence=0.5,
        ),
    ]

    resolved, _ = ResolverEngine(nodes, edges).resolve()

    assert not any(e.target.startswith("raw_dep:") for e in resolved)
    # raw_call: keeps unresolved targets (regression guard for the asymmetry)
    assert any(e.target == "unknown_fn" for e in resolved)


def test_variable_nodes_do_not_pollute_call_resolution() -> None:
    """VARIABLE nodes are not in _global_symbols: raw_call: never lands on them."""
    nodes = [
        _func_node("app.caller", "app.py"),
        _alias_node("deps.OwnerDep", "deps.py"),
    ]
    edges = [
        Edge(
            id="edge_call",
            source="app.caller",
            target="raw_call:OwnerDep",
            type=EdgeType.CALLS,
            confidence=0.5,
        )
    ]

    resolved, _ = ResolverEngine(nodes, edges).resolve()

    # Unresolved (kept as bare name), NOT resolved to deps.OwnerDep
    assert resolved[0].target == "OwnerDep"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_resolver.py -k raw_dep -v`
Expected: FAIL — `raw_dep:` targets pass through the `raw_call:` fallthrough (`if not edge.target.startswith("raw_call:")` keeps them verbatim), so targets still carry the prefix.

- [ ] **Step 3: Implement**

In `src/cgis/resolver/engine.py`. Module-level constant (next to `_RAW_CLASS_PREFIX`):

```python
RAW_DEP_PREFIX = "raw_dep:"
```

(Public name — the spec designates the resolver as the owner of this contract.)

In `__init__`, after the `self._class_methods` declaration:

```python
        # DI-alias (VARIABLE) indices for raw_dep: resolution; kept separate
        # from _global_symbols so call resolution behavior does not change.
        self._variable_symbols: dict[str, list[str]] = {}
        self._file_variable_symbols: dict[tuple[str, str], list[str]] = {}
```

In `_build_indices`, inside the node loop (after the METHOD block):

```python
            # Index DI aliases for raw_dep: candidate resolution
            if node.type == NodeType.VARIABLE:
                self._variable_symbols.setdefault(node.name, []).append(node.id)
                self._file_variable_symbols.setdefault(
                    (os.path.normpath(node.file_path), node.name), []
                ).append(node.id)
```

In `resolve()`, add a branch **before** the `raw_call:` fallthrough check (i.e. right after the `_RAW_CLASS_PREFIX` block):

```python
            if edge.target.startswith(RAW_DEP_PREFIX):
                dep_name = edge.target.removeprefix(RAW_DEP_PREFIX)
                dep_target = self._resolve_dep_candidate(dep_name, edge.source, edge.file_path)
                if dep_target is None:
                    # Speculative candidate that is not a DI alias: drop the
                    # edge entirely — raw_dep: must never leak into output
                    # (spec §3.3).
                    continue
                resolved_edges.append(
                    edge.model_copy(update={"target": dep_target, "confidence": 1.0})
                )
                continue
```

New methods (place after `_resolve_global_call`):

```python
    def _resolve_dep_candidate(
        self, name: str, source_fqn: str, edge_file_path: str | None
    ) -> str | None:
        """Resolve a raw_dep: candidate to a VARIABLE (DI alias) node, or None.

        Order: the consuming file's import map first, then the VARIABLE-only
        symbol index with same-file preference. Returns None for anything that
        is not an existing VARIABLE node — the caller drops the edge.
        """
        file_path = self._get_normalized_file_path(source_fqn, edge_file_path)
        if file_path:
            via_import = self._resolve_via_import_map(name, file_path)
            if via_import and self._is_variable_node(via_import):
                return via_import
        candidates = self._variable_symbols.get(name, [])
        if len(candidates) == 1:
            return candidates[0]
        if candidates and file_path:
            same_file = self._file_variable_symbols.get((file_path, name), [])
            if len(same_file) == 1:
                return same_file[0]
        return None

    def _is_variable_node(self, fqn: str) -> bool:
        """Return True when fqn names an existing VARIABLE node in the graph."""
        node = self.nodes.get(fqn)
        return node is not None and node.type == NodeType.VARIABLE
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_resolver.py -v`
Expected: all PASS (new 5 + full existing resolver suite — the regression guard).

- [ ] **Step 5: Commit**

```bash
git add src/cgis/resolver/engine.py tests/unit/test_resolver.py
git commit -m "feat(resolver): raw_dep: resolution to DI aliases with drop policy (#161)"
```

---

### Task 5: End-to-end acceptance fixture (the #161 case)

Covers spec §5 acceptance: the exact `owner.py` + `routes.py` shape from the issue, through `IngestionPipeline` → `SQLiteStore` → `QueryEngine`. `impact(resolve_published_owner)` must surface the alias and all 4 endpoints; `flow(resolve_published_owner)` must include the sub-dependency chain.

**Files:**
- Create: `tests/unit/test_di_acceptance.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_di_acceptance.py`:

```python
"""End-to-end acceptance for FastAPI DI modeling (#161 slice 1).

Replicates the exact reproducible case from issue #161: two providers, two
Annotated DI aliases, four consuming endpoints. Asserts via QueryEngine that
impact and flow traversals surface the DI coupling layer.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from cgis.extractors.python_extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.query.engine import QueryEngine
from cgis.storage.sqlite_store import SQLiteStore

_DEPS_PY = '''
from typing import Annotated
from fastapi import Depends, Path


class User:
    pass


def get_owner_service():
    pass


OwnerServiceDep = Annotated[object, Depends(get_owner_service)]


async def resolve_owner(owner_service: OwnerServiceDep, owner_id: str = Path(...)) -> User:
    pass


ResolvedOwnerDep = Annotated[User, Depends(resolve_owner)]


async def resolve_published_owner(owner: ResolvedOwnerDep) -> User:
    pass


PublishedOwnerDep = Annotated[User, Depends(resolve_published_owner)]
'''

_ROUTES_PY = '''
from deps import PublishedOwnerDep


async def get_owner_public_profile(owner: PublishedOwnerDep):
    pass


async def search_vehicles(owner: PublishedOwnerDep):
    pass


async def get_vehicle(owner: PublishedOwnerDep):
    pass


async def calculate_pricing(owner: PublishedOwnerDep):
    pass
'''

_ENDPOINTS = (
    "get_owner_public_profile",
    "search_vehicles",
    "get_vehicle",
    "calculate_pricing",
)


@pytest.fixture
def di_graph_store(tmp_path: Path) -> Iterator[SQLiteStore]:
    """Ingest the #161 fixture into a real SQLiteStore and yield it (open)."""
    (tmp_path / "deps.py").write_text(_DEPS_PY, encoding="utf-8")
    (tmp_path / "routes.py").write_text(_ROUTES_PY, encoding="utf-8")

    pipeline = IngestionPipeline({".py": PythonExtractor()})
    nodes, _raw, resolved = pipeline.run(str(tmp_path))

    with SQLiteStore(str(tmp_path / "graph.db")) as store:
        store.save_graph(nodes, resolved, overwrite=True)
        yield store


def _find_id(store: SQLiteStore, suffix: str) -> str:
    """Find the unique node id ending with `.suffix` (dot boundary)."""
    matches = store.find_nodes_by_suffix(suffix, limit=2)
    assert len(matches) == 1, f"expected unique node for {suffix}, got {matches}"
    return matches[0].id


def test_impact_surfaces_alias_and_all_endpoints(di_graph_store: SQLiteStore) -> None:
    engine = QueryEngine(di_graph_store)
    rpo = _find_id(di_graph_store, "resolve_published_owner")

    imp_nodes, _ = engine.get_impact_graph(rpo, max_depth=4)
    ids = {n.id for n in imp_nodes}

    assert any(i.endswith(".PublishedOwnerDep") for i in ids)
    for endpoint in _ENDPOINTS:
        assert any(i.endswith(f".{endpoint}") for i in ids), f"missing endpoint {endpoint}"


def test_flow_includes_sub_dependency_chain(di_graph_store: SQLiteStore) -> None:
    engine = QueryEngine(di_graph_store)
    rpo = _find_id(di_graph_store, "resolve_published_owner")

    flow_nodes, _ = engine.get_flow_graph(rpo, max_depth=3)
    ids = {n.id for n in flow_nodes}

    assert any(i.endswith(".ResolvedOwnerDep") for i in ids)
    assert any(i.endswith(".resolve_owner") for i in ids)
```

Note: `SQLiteStore` is used as a context manager (`__enter__`/`__exit__`, no public `close()`) — hence the generator fixture. `find_nodes_by_suffix` exists since #145 (`src/cgis/storage/sqlite_store.py`).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_di_acceptance.py -v`
Expected: with Tasks 1–4 done, this should PASS already — it is the integration check. If it FAILS, the failure pinpoints the broken layer: missing alias node (Task 2), missing direct edge (Task 1), candidate not resolved (Task 4). Debug there, not here.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_di_acceptance.py
git commit -m "test: end-to-end acceptance fixture for FastAPI DI modeling (#161)"
```

---

### Task 6: Full gates + drift-safety verification

Covers spec §4: self-parsing must stay green with **unchanged ratchets** — cgis has no FastAPI, so zero new DEPENDS_ON edges and zero VARIABLE nodes may appear in the self-graph. If `tests/self_parsing/test_drift.py` moves, that is a design violation to investigate (per #145 lesson), NOT a tolerance to bump.

- [ ] **Step 1: Run the full verification chain**

Run: `make format && make lint && make type-check && make pytest && make doc-coverage`
Expected: all green; interrogate ≥90%.

- [ ] **Step 2: Explicitly verify self-parsing + ratchets**

Run: `uv run pytest tests/self_parsing/ -v`
Expected: all PASS with no ratchet changes. Also sanity-check the self-graph is DI-free:

Run: `uv run cgis ingest src --output /tmp/self.db 2>/dev/null && uv run python -c "
from cgis.storage.sqlite_store import SQLiteStore
with SQLiteStore('/tmp/self.db') as s:
    edges = s.get_all_edges()
    dep = [e for e in edges if e.type == 'DEPENDS_ON']
    print('DEPENDS_ON edges in self-graph:', len(dep))
    assert not dep
"`
Expected: `DEPENDS_ON edges in self-graph: 0`. (If the ingest/CLI invocation differs, adapt — the assertion is what matters.)

- [ ] **Step 3: Commit any formatting fallout**

```bash
git add -u
git commit -m "chore: formatting/lint fallout for DI edges (#161)" || echo "nothing to commit"
```

---

## Out of scope (do NOT implement)

Symbol-level import edges (slice 2), class-body DI aliases, argless `Depends()` semantics, `Security` scopes, configurable DI-name lists, indexing non-DI module assignments. See spec §6.
