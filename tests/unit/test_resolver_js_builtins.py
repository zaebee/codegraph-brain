"""JS/TS built-in globals resolve to a synthetic `js_builtins.*` STDLIB namespace (#111)."""

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.extractors.python_extractor import PythonExtractor
from cgis.extractors.typescript_extractor import TypeScriptExtractor
from cgis.resolver.engine import ResolverEngine


def _resolve_ts(code: str, file_path: str = "src/app/util.ts") -> tuple[list[Edge], list[Node]]:
    """Extract and resolve one TypeScript file."""
    nodes, edges = TypeScriptExtractor(tsx=file_path.endswith(".tsx")).parse(code, file_path)
    resolved, virtual = ResolverEngine(nodes, edges).resolve()
    return resolved, nodes + virtual


def _call_targets(edges: list[Edge]) -> set[str]:
    """The targets of every CALLS edge."""
    return {e.target for e in edges if e.type == EdgeType.CALLS}


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        ("Math.max(a, b)", "js_builtins.Math.max"),
        ("Object.keys(a)", "js_builtins.Object.keys"),
        ("Promise.resolve(a)", "js_builtins.Promise.resolve"),
        ("Array.from(a)", "js_builtins.Array.from"),
        ("JSON.parse(a)", "js_builtins.JSON.parse"),
        ("console.error(a)", "js_builtins.console.error"),
        ("document.querySelector(a)", "js_builtins.document.querySelector"),
        ("setTimeout(a, 1)", "js_builtins.setTimeout"),
        ("fetch(a)", "js_builtins.fetch"),
        ("String(a)", "js_builtins.String"),
    ],
)
def test_ts_global_call_resolves_to_js_builtins(call: str, expected: str) -> None:
    """A call on a JS global becomes a js_builtins.* edge instead of an UNKNOWN bare name."""
    code = f"export function f(a: any, b: any) {{\n  return {call};\n}}\n"
    edges, nodes = _resolve_ts(code)
    assert expected in _call_targets(edges)
    node = next(n for n in nodes if n.id == expected)
    assert node.namespace == NodeNamespace.STDLIB


def test_tsx_file_gets_js_builtins_too() -> None:
    """The .tsx extension is JavaScript as well."""
    code = "export function C() {\n  return Math.min(1, 2);\n}\n"
    edges, _ = _resolve_ts(code, "src/app/View.tsx")
    assert "js_builtins.Math.min" in _call_targets(edges)


def test_local_symbol_shadows_js_global() -> None:
    """A project function named like a global is the project's, not the runtime's."""
    code = (
        "function fetch(u: string) {\n  return u;\n}\n"
        "export function g() {\n  return fetch('x');\n}\n"
    )
    edges, _ = _resolve_ts(code)
    targets = _call_targets(edges)
    assert "js_builtins.fetch" not in targets
    assert any(t.endswith(".fetch") and not t.startswith("js_builtins.") for t in targets)


def test_non_global_receiver_stays_unresolved() -> None:
    """A method on a local value is not a built-in: `arr.map` has no receiver type to go on."""
    code = "export function f(arr: number[]) {\n  return arr.map((x) => x);\n}\n"
    edges, _ = _resolve_ts(code)
    assert not any(t.startswith("js_builtins.") for t in _call_targets(edges))


def test_python_file_never_gets_js_builtins() -> None:
    """The namespace is language-scoped: a Python call to `console.log` is not a JS global."""
    code = "def f(console):\n    console.log('x')\n    setTimeout(1)\n"
    nodes, edges = PythonExtractor().parse(code, "app/mod.py")
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    assert not any(t.startswith("js_builtins.") for t in _call_targets(resolved))


def test_edge_without_file_path_uses_source_node_file() -> None:
    """TS CALLS edges carry no file_path; the source node's file decides the language."""
    source = Node(
        id="src.app.util.f",
        type=NodeType.FUNCTION,
        name="f",
        file_path="src/app/util.ts",
        start_line=1,
        end_line=3,
    )
    edge = Edge(
        id="e1",
        source=source.id,
        target="raw_call:Math.max",
        type=EdgeType.CALLS,
        confidence=0.3,
    )
    resolved, _ = ResolverEngine([source], [edge]).resolve()
    assert resolved[0].target == "js_builtins.Math.max"
