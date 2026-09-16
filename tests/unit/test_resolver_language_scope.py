"""Python's stdlib and builtin names classify STDLIB only for Python sources (#454)."""

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace
from cgis.extractors.python_extractor import PythonExtractor
from cgis.extractors.typescript_extractor import TypeScriptExtractor
from cgis.resolver.engine import ResolverEngine


def _namespaces(nodes: list[Node], edges: list[Edge]) -> dict[str, NodeNamespace]:
    """Resolve, then map every CALLS target to the namespace of its node."""
    resolved, virtual = ResolverEngine(nodes, edges).resolve()
    by_id = {n.id: n for n in [*nodes, *virtual]}
    return {
        e.target: by_id[e.target].namespace
        for e in resolved
        if e.type == EdgeType.CALLS and e.target in by_id
    }


@pytest.mark.parametrize(
    ("call", "target"),
    [
        ("this.nodes.map(x)", "this.nodes.map"),  # `this` is a Python stdlib module
        ("list.push(x)", "list.push"),  # `list` is a Python builtin
        ("queue.shift()", "queue.shift"),  # `queue` is a Python stdlib module
        ("random.pick(x)", "random.pick"),
        ("types.includes(x)", "types.includes"),
        ("map.get(x)", "map.get"),
    ],
)
def test_ts_call_on_python_name_is_unknown(call: str, target: str) -> None:
    """A TypeScript receiver that happens to share a Python stdlib/builtin name is not STDLIB."""
    code = f"export function f(x: any) {{\n  {call};\n}}\n"
    nodes, edges = TypeScriptExtractor().parse(code, "src/app/util.ts")
    assert _namespaces(nodes, edges)[target] == NodeNamespace.UNKNOWN


def test_ts_js_builtins_still_stdlib() -> None:
    """Scoping the Python check leaves the JS runtime namespace (#111) untouched."""
    code = "export function f() {\n  Math.max(1, 2);\n}\n"
    nodes, edges = TypeScriptExtractor().parse(code, "src/app/util.ts")
    assert _namespaces(nodes, edges)["js_builtins.Math.max"] == NodeNamespace.STDLIB


@pytest.mark.parametrize("call", ["len(x)", "json.dumps(x)", "os.path.join(x)", "list(x)"])
def test_python_call_on_stdlib_name_is_still_stdlib(call: str) -> None:
    """Python sources keep classifying their own stdlib and builtins as before."""
    code = f"import json\nimport os\n\ndef f(x):\n    return {call}\n"
    nodes, edges = PythonExtractor().parse(code, "app/mod.py")
    namespaces = _namespaces(nodes, edges)
    target = call.split("(", maxsplit=1)[0]
    assert namespaces[target] == NodeNamespace.STDLIB
