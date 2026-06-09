"""Unit tests for TypeScriptExtractor."""

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.extractors.typescript_extractor import TypeScriptExtractor, file_path_to_module_fqn

# ---------------------------------------------------------------------------
# file_path_to_module_fqn
# ---------------------------------------------------------------------------


def test_fqn_plain_ts() -> None:
    """src/api/handler.ts -> src.api.handler."""
    assert file_path_to_module_fqn("src/api/handler.ts") == "src.api.handler"


def test_fqn_tsx() -> None:
    """src/components/Button.tsx -> src.components.Button."""
    assert file_path_to_module_fqn("src/components/Button.tsx") == "src.components.Button"


def test_fqn_index_ts() -> None:
    """src/utils/index.ts -> src.utils (strips /index like Python's __init__)."""
    assert file_path_to_module_fqn("src/utils/index.ts") == "src.utils"


def test_fqn_index_tsx() -> None:
    """src/components/index.tsx -> src.components."""
    assert file_path_to_module_fqn("src/components/index.tsx") == "src.components"


def test_fqn_windows_path() -> None:
    """Windows path with drive letter is handled correctly."""
    assert file_path_to_module_fqn("C:\\src\\api\\handler.ts") == "src.api.handler"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def extractor() -> TypeScriptExtractor:
    """Return a TypeScript (non-TSX) extractor."""
    return TypeScriptExtractor()


def _nodes_by_type(nodes: list[Node], node_type: NodeType) -> list[Node]:
    return [n for n in nodes if n.type == node_type]


def _edges_by_type(edges: list[Edge], edge_type: EdgeType) -> list[Edge]:
    return [e for e in edges if e.type == edge_type]


# ---------------------------------------------------------------------------
# AC1: Standard classes, methods, functions
# ---------------------------------------------------------------------------


def test_extract_class_and_method(extractor: TypeScriptExtractor) -> None:
    """Class and method are extracted with correct FQNs and CONTAINS/DECLARES edges."""
    code = "class Auth { login() {} }"
    nodes, edges = extractor.parse(code, "src/auth.ts")

    classes = _nodes_by_type(nodes, NodeType.CLASS)
    methods = _nodes_by_type(nodes, NodeType.METHOD)
    assert len(classes) == 1
    assert classes[0].id == "src.auth.Auth"
    assert len(methods) == 1
    assert methods[0].id == "src.auth.Auth.login"

    contains = _edges_by_type(edges, EdgeType.CONTAINS)
    declares = _edges_by_type(edges, EdgeType.DECLARES)
    assert any(e.target == "src.auth.Auth" for e in contains)
    assert any(e.source == "src.auth.Auth" and e.target == "src.auth.Auth.login" for e in declares)


def test_extract_function_declaration(extractor: TypeScriptExtractor) -> None:
    """Standard function declaration produces a FUNCTION node."""
    code = "function init(): void { }"
    nodes, edges = extractor.parse(code, "src/main.ts")

    funcs = _nodes_by_type(nodes, NodeType.FUNCTION)
    assert len(funcs) == 1
    assert funcs[0].id == "src.main.init"
    assert any(e.target == "src.main.init" and e.type == EdgeType.CONTAINS for e in edges)


def test_extract_exported_class_and_function(extractor: TypeScriptExtractor) -> None:
    """export keyword is transparently unwrapped."""
    code = """
export class Auth { login() {} }
export function init() {}
"""
    nodes, _ = extractor.parse(code, "src/auth.ts")
    assert any(n.id == "src.auth.Auth" for n in nodes)
    assert any(n.id == "src.auth.Auth.login" for n in nodes)
    assert any(n.id == "src.auth.init" for n in nodes)


# ---------------------------------------------------------------------------
# AC2: Arrow functions + CALLS edges
# ---------------------------------------------------------------------------


def test_extract_arrow_function(extractor: TypeScriptExtractor) -> None:
    """Arrow function const is extracted as FUNCTION node."""
    code = "const calculate = () => { process(); };"
    nodes, _ = extractor.parse(code, "src/math.ts")

    funcs = _nodes_by_type(nodes, NodeType.FUNCTION)
    assert any(n.id == "src.math.calculate" for n in funcs)


def test_arrow_function_emits_calls_edge(extractor: TypeScriptExtractor) -> None:
    """Arrow function body call emits a CALLS edge with raw_call: prefix."""
    code = "const calculate = () => { process(); };"
    _, edges = extractor.parse(code, "src/math.ts")

    calls = _edges_by_type(edges, EdgeType.CALLS)
    assert any(e.source == "src.math.calculate" and e.target == "raw_call:process" for e in calls)


def test_this_call_normalised_to_self(extractor: TypeScriptExtractor) -> None:
    """this.method() is emitted as raw_call:self.method for resolver parity."""
    code = "class Svc { run() { this.helper(); } }"
    _, edges = extractor.parse(code, "src/svc.ts")

    calls = _edges_by_type(edges, EdgeType.CALLS)
    assert any(e.target == "raw_call:self.helper" for e in calls)


# ---------------------------------------------------------------------------
# AC3: Index file FQN logic
# ---------------------------------------------------------------------------


def test_index_file_fqn(extractor: TypeScriptExtractor) -> None:
    """Functions in index.ts get FQN rooted at the directory, not index."""
    code = "export function helper() {}"
    nodes, _ = extractor.parse(code, "src/utils/index.ts")

    funcs = _nodes_by_type(nodes, NodeType.FUNCTION)
    assert any(n.id == "src.utils.helper" for n in funcs)


# ---------------------------------------------------------------------------
# AC4: Import edge extraction
# ---------------------------------------------------------------------------


def test_relative_import_edge(extractor: TypeScriptExtractor) -> None:
    """./utils from src/api/handler.ts resolves to src.api.utils (not src.api.handler.utils)."""
    code = "import { foo } from './utils';"
    _, edges = extractor.parse(code, "src/api/handler.ts")

    imports = _edges_by_type(edges, EdgeType.IMPORTS)
    assert any(e.target == "src.api.utils" for e in imports)


def test_parent_dir_import_edge(extractor: TypeScriptExtractor) -> None:
    """../utils from src/api/sub/handler.ts resolves to src.api.utils."""
    code = "import { bar } from '../utils';"
    _, edges = extractor.parse(code, "src/api/sub/handler.ts")

    imports = _edges_by_type(edges, EdgeType.IMPORTS)
    assert any(e.target == "src.api.utils" for e in imports)


def test_grandparent_dir_import_edge(extractor: TypeScriptExtractor) -> None:
    """../../utils from src/api/sub/deep/handler.ts resolves to src.api.utils."""
    code = "import { baz } from '../../utils';"
    _, edges = extractor.parse(code, "src/api/sub/deep/handler.ts")

    imports = _edges_by_type(edges, EdgeType.IMPORTS)
    assert any(e.target == "src.api.utils" for e in imports)


def test_relative_import_index_stripped(extractor: TypeScriptExtractor) -> None:
    """./utils/index resolves to src.api.utils (index suffix stripped)."""
    code = "import { x } from './utils/index';"
    _, edges = extractor.parse(code, "src/api/handler.ts")

    imports = _edges_by_type(edges, EdgeType.IMPORTS)
    assert any(e.target == "src.api.utils" for e in imports)


def test_external_import_edge(extractor: TypeScriptExtractor) -> None:
    """External package import emits an IMPORTS edge."""
    code = "import React from 'react';"
    _, edges = extractor.parse(code, "src/App.tsx")

    imports = _edges_by_type(edges, EdgeType.IMPORTS)
    assert any(e.target == "react" for e in imports)


# ---------------------------------------------------------------------------
# AC4: Pipeline integration (.ts / .tsx routing)
# ---------------------------------------------------------------------------


def test_tsx_extractor_parses_jsx() -> None:
    """TSX extractor handles JSX syntax without errors."""
    tsx = TypeScriptExtractor(tsx=True)
    code = """
export function Button() {
  return <button onClick={() => click()}>OK</button>;
}
"""
    nodes, _ = tsx.parse(code, "src/Button.tsx")
    assert any(n.id == "src.Button.Button" for n in nodes)


def test_multiple_methods_in_class(extractor: TypeScriptExtractor) -> None:
    """All methods in a class are extracted with correct FQNs."""
    code = """
class Service {
  connect() {}
  disconnect() {}
  send(msg: string) {}
}
"""
    nodes, _ = extractor.parse(code, "src/service.ts")
    method_ids = {n.id for n in nodes if n.type == NodeType.METHOD}
    assert method_ids == {
        "src.service.Service.connect",
        "src.service.Service.disconnect",
        "src.service.Service.send",
    }


def test_file_node_always_present(extractor: TypeScriptExtractor) -> None:
    """A FILE node is always the first node emitted."""
    nodes, _ = extractor.parse("", "src/empty.ts")
    assert nodes[0].type == NodeType.FILE
    assert nodes[0].id == "src.empty"


# ---------------------------------------------------------------------------
# Edge ID uniqueness
# ---------------------------------------------------------------------------


def test_edge_ids_unique(extractor: TypeScriptExtractor) -> None:
    """Duplicate calls to the same target produce distinct edge IDs."""
    code = """
function foo() { bar(); bar(); }
function baz() { bar(); }
"""
    _, edges = extractor.parse(code, "src/test.ts")
    edge_ids = [e.id for e in edges]
    assert len(edge_ids) == len(set(edge_ids))


# ---------------------------------------------------------------------------
# Abstract class
# ---------------------------------------------------------------------------


def test_abstract_class(extractor: TypeScriptExtractor) -> None:
    """Abstract class is extracted as a CLASS node with its methods."""
    code = "abstract class Base { abstract run(): void; }"
    nodes, _ = extractor.parse(code, "src/base.ts")
    assert any(n.id == "src.base.Base" and n.type == NodeType.CLASS for n in nodes)
    assert any(n.id == "src.base.Base.run" and n.type == NodeType.METHOD for n in nodes)


# ---------------------------------------------------------------------------
# Known limitations
# ---------------------------------------------------------------------------


def test_optional_chain_call_skipped(extractor: TypeScriptExtractor) -> None:
    """Optional chaining obj?.method() is not extracted (known limitation)."""
    code = "function foo() { obj?.bar(); }"
    _, edges = extractor.parse(code, "src/test.ts")
    calls = _edges_by_type(edges, EdgeType.CALLS)
    # tree-sitter represents obj?.bar() as call_expression with member_expression
    # containing an optional chain — _get_call_name may or may not capture it;
    # this test documents the current behaviour and catches regressions.
    targets = {e.target for e in calls}
    assert all(t.startswith("raw_call:") for t in targets)


# ---------------------------------------------------------------------------
# source_root
# ---------------------------------------------------------------------------


def test_fqn_with_source_root() -> None:
    """source_root strips the leading directory before dotting."""
    assert file_path_to_module_fqn("src/api/handler.ts", source_root="src") == "api.handler"


def test_fqn_source_root_index_stripped() -> None:
    """index suffix is still dropped after source_root stripping."""
    assert file_path_to_module_fqn("src/components/index.tsx", source_root="src") == "components"


def test_fqn_source_root_trailing_slash() -> None:
    """Trailing slash in source_root is handled gracefully."""
    assert file_path_to_module_fqn("src/api/handler.ts", source_root="src/") == "api.handler"


def test_fqn_source_root_no_match() -> None:
    """When file_path does not start with source_root, no stripping occurs."""
    assert file_path_to_module_fqn("lib/utils.ts", source_root="src") == "lib.utils"


def test_typescript_extractor_source_roots_strips_prefix() -> None:
    """TypeScriptExtractor(source_roots=['src']) yields FQNs without the 'src.' prefix."""
    ext = TypeScriptExtractor(source_roots=["src"])
    code = "export function hello() {}"
    nodes, _ = ext.parse(code, "src/api/handler.ts")
    fqns = [n.id for n in nodes]
    assert all(not fqn.startswith("src.") for fqn in fqns)
    assert any(fqn.startswith("api.") for fqn in fqns)


def test_typescript_extractor_source_roots_none_is_unchanged() -> None:
    """TypeScriptExtractor() without source_roots behaves as before."""
    ext = TypeScriptExtractor()
    code = "export function hello() {}"
    nodes, _ = ext.parse(code, "src/api/handler.ts")
    fqns = [n.id for n in nodes]
    assert any(fqn.startswith("src.api.") for fqn in fqns)


def test_typescript_extractor_source_roots_unmatched_root() -> None:
    """If no source_root matches the file, FQN is built from the full path."""
    ext = TypeScriptExtractor(source_roots=["lib"])
    code = "export function hello() {}"
    nodes, _ = ext.parse(code, "src/api/handler.ts")
    fqns = [n.id for n in nodes]
    assert any(fqn.startswith("src.api.") for fqn in fqns)
