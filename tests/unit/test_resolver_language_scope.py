"""Python's stdlib and builtin names classify STDLIB only for Python sources (#454)."""

from pathlib import Path

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace
from cgis.extractors.python_extractor import PythonExtractor
from cgis.extractors.registry import build_extractors
from cgis.extractors.typescript_extractor import TypeScriptExtractor
from cgis.pipeline import IngestionPipeline
from cgis.resolver.engine import ResolverEngine
from cgis.resolver.indices import IndexBuilder
from cgis.storage.sqlite_store import SQLiteStore

_PY_JSON = "import json\n\ndef f(x):\n    return json.dumps(x)\n"
_TS_JSON = "export function g(json: any) {\n  json.dumps(1);\n}\n"


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


@pytest.mark.parametrize("source_file", ["cmd/main.go", "src/App.java", "lib/mod.rb"])
def test_non_python_source_does_not_inherit_python_names(source_file: str) -> None:
    """The Python reading is opted into by `.py`, not the default for anything that is not TS."""
    nodes, _ = PythonExtractor().parse(_PY_JSON, "pp/mod.py")
    index = IndexBuilder().build(nodes)
    assert index.classify_fqn("json.dumps", source_file) == NodeNamespace.UNKNOWN
    assert index.classify_fqn("json.dumps", "pp/mod.py") == NodeNamespace.STDLIB
    assert index.classify_fqn("json.dumps") == NodeNamespace.STDLIB


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


def _mixed(ts_first: bool) -> dict[str, NodeNamespace]:
    """Resolve a Python and a TypeScript file that both call `json.dumps`, in a given order."""
    py = PythonExtractor().parse(_PY_JSON, "pp/mod.py")
    ts = TypeScriptExtractor().parse(_TS_JSON, "tt/util.ts")
    first, second = (ts, py) if ts_first else (py, ts)
    return _namespaces([*first[0], *second[0]], [*first[1], *second[1]])


@pytest.mark.parametrize("ts_first", [False, True])
def test_mixed_repo_classification_does_not_depend_on_file_order(ts_first: bool) -> None:
    """A target reached from both languages keeps the Python classification, whichever comes first.

    The node id is shared, so one namespace must win. Python's is a real claim about
    the symbol; the TS reading is only "not known". Walk order (`Path.walk`, unsorted)
    must not decide it.
    """
    assert _mixed(ts_first)["json.dumps"] == NodeNamespace.STDLIB


def test_ts_source_ignores_python_import_roots() -> None:
    """A Python `import json` makes `json` an external root; a TS `json` receiver stays UNKNOWN."""
    py = PythonExtractor().parse(_PY_JSON, "pp/mod.py")
    ts = TypeScriptExtractor().parse(
        "export function g(json: any) {\n  json.walk(1);\n}\n", "tt/util.ts"
    )
    namespaces = _namespaces([*py[0], *ts[0]], [*py[1], *ts[1]])
    assert namespaces["json.walk"] == NodeNamespace.UNKNOWN


def test_incremental_ts_change_does_not_downgrade_python_stdlib(tmp_path: Path) -> None:
    """Re-ingesting only the TS file must not overwrite the shared node with UNKNOWN."""
    (tmp_path / "pp").mkdir()
    (tmp_path / "tt").mkdir()
    (tmp_path / "pp" / "mod.py").write_text(_PY_JSON, encoding="utf-8")
    (tmp_path / "tt" / "util.ts").write_text(_TS_JSON, encoding="utf-8")
    pipeline = IngestionPipeline(build_extractors([]))
    db = str(tmp_path / "g.db")

    def namespace() -> NodeNamespace:
        with SQLiteStore(db) as store:
            node = store.get_node("json.dumps")
        assert node is not None
        return node.namespace

    with SQLiteStore(db) as store:
        pipeline.run(str(tmp_path), store=store)
    assert namespace() == NodeNamespace.STDLIB

    for step in range(3):
        (tmp_path / "tt" / "util.ts").write_text(_TS_JSON + f"// edit {step}\n", encoding="utf-8")
        with SQLiteStore(db) as store:
            pipeline.run(str(tmp_path), store=store)
        assert namespace() == NodeNamespace.STDLIB, f"downgraded after TS-only edit {step}"
