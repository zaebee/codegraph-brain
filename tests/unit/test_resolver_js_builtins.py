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
        ("global.gc(a)", "js_builtins.global.gc"),
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


def test_windows_path_is_still_typescript() -> None:
    """The suffix check does not depend on the separator style."""
    code = "export function f() {\n  return Math.max(1, 2);\n}\n"
    edges, _ = _resolve_ts(code, "src\\app\\util.ts")
    assert "js_builtins.Math.max" in _call_targets(edges)


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


@pytest.mark.parametrize(
    ("code", "bare_target"),
    [
        (
            "import history from './history';\nexport function f() {\n  history.push('/');\n}\n",
            "history.push",
        ),
        (
            "import * as crypto from 'node:crypto';\n"
            "export function f() {\n  crypto.randomUUID();\n}\n",
            "crypto.randomUUID",
        ),
        ("import { fetch } from 'undici';\nexport function f() {\n  fetch('x');\n}\n", "fetch"),
        (
            "import { useC as confirm } from './c';\nexport function f() {\n  confirm();\n}\n",
            "confirm",
        ),
        (
            "export function f() {\n  const history = useHistory();\n  history.push('/');\n}\n",
            "history.push",
        ),
        (
            "export function f() {\n  const { location } = useRouter();\n  location.reload();\n}\n",
            "location.reload",
        ),
        ("export function f(process: Proc) {\n  process.run();\n}\n", "process.run"),
        ("export const g = (console: Log) => console.log('x');\n", "console.log"),
        # tree-sitter-typescript has no rest_parameter node: `...x` is a
        # required_parameter whose pattern is a rest_pattern.
        ("export function f(...console: any[]) {\n  console.log('x');\n}\n", "console.log"),
        ("export const g = (...process: P[]) => process.run();\n", "process.run"),
        (
            "import crypto = require('crypto');\n"
            "export function g() {\n  crypto.randomUUID();\n}\n",
            "crypto.randomUUID",
        ),
        (
            "namespace crypto {\n  export function x() {}\n}\n"
            "export function g() {\n  crypto.x();\n}\n",
            "crypto.x",
        ),
        (
            "export function f() {\n  try { x(); } catch (Event) { Event.stop(); }\n}\n",
            "Event.stop",
        ),
    ],
)
def test_name_bound_in_file_is_not_a_js_global(code: str, bare_target: str) -> None:
    """An imported, declared or parameter name that matches a global is the file's own binding.

    It stays an honest UNKNOWN rather than becoming a confident, wrong STDLIB edge.
    """
    edges, _ = _resolve_ts(code)
    targets = _call_targets(edges)
    assert bare_target in targets
    assert f"js_builtins.{bare_target}" not in targets


def test_import_alias_leaves_the_original_global_free() -> None:
    """`import { fetch as f }` binds `f`, so a bare `fetch()` is still the runtime's."""
    code = (
        "import { fetch as f } from 'undici';\nexport function g() {\n  f('a');\n  fetch('b');\n}\n"
    )
    edges, _ = _resolve_ts(code)
    assert "js_builtins.fetch" in _call_targets(edges)


def _shadowed(code: str) -> list[str]:
    """The shadowed_globals the TS extractor records on the FILE node."""
    nodes, _ = TypeScriptExtractor().parse(code, "src/app/m.ts")
    file_node = next(n for n in nodes if n.type == NodeType.FILE)
    shadowed: list[str] = file_node.metadata.get("shadowed_globals", [])
    return shadowed


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("const [Image, ...Blob] = xs;", ["Blob", "Image"]),
        ("const { a: { b: [URL] } } = x;", ["URL"]),
        ("for (const Map of xs) {}", ["Map"]),  # for-of parses as for_in_statement
        ("for (const [URL] of xs) {}", ["URL"]),
        ("class Worker {}\nfunction Request() {}", ["Request", "Worker"]),
        ("const h = async Response => 1;", ["Response"]),
        ("function f({ Headers = 1 }: T, URLSearchParams?: U) {}", ["Headers", "URLSearchParams"]),
        ("let history; var location;", ["history", "location"]),
        ("import crypto = require('crypto');", ["crypto"]),
        ("export import history = X.Y;", ["history"]),
        ("namespace crypto { }\nmodule history { }", ["crypto", "history"]),
        ("declare namespace location { }\nnamespace process.v2 { }", ["location", "process"]),
    ],
)
def test_shadowed_globals_binding_positions(code: str, expected: list[str]) -> None:
    """Every binding form records the global it rebinds, sorted."""
    assert _shadowed(code) == expected


@pytest.mark.parametrize(
    "code",
    [
        "const { fetch: f } = api;",  # the key is a property name, `f` is the binding
        "const { a = fetch } = api;",  # a default value is an expression
        "function g(a = console) {}",
        "import { fetch as f } from 'undici';",
        "const x = Math.max(1, 2);\nfetch('a');",  # plain use is not a binding
        "const notAGlobal = 1;",
        "declare module 'fetch' { }",  # a quoted module name binds nothing
    ],
)
def test_shadowed_globals_ignores_non_bindings(code: str) -> None:
    """A global that is only read, renamed away, or used as a key stays free."""
    assert _shadowed(code) == []
