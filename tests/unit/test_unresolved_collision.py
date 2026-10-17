"""#661: an unresolved call never joins a real node whose id equals its raw name.

An unresolved CALLS edge targets its bare raw name, minted as a virtual boundary
node. When that text equals a real node's id (a Go local `q` shadowing package `q`,
a Python parameter named like a module), the edge used to join the real node and
read as a resolved call to it.
"""

import json
import sqlite3
from pathlib import Path

from cgis.api.mcp_server import cgis_analyze_impact, cgis_ingest
from cgis.core.models import VIRTUAL_FILE_PATH, Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.resolver.engine import ResolverEngine
from cgis.storage.sqlite_store import SQLiteStore


def _ingest(root: Path) -> Path:
    db = root / "graph.db"
    cgis_ingest(str(root), str(db))
    return db


def _calls_from(db: Path, source: str) -> list[Edge]:
    with SQLiteStore(str(db)) as store:
        return [e for e in store.get_outgoing_edges(source) if e.type is EdgeType.CALLS]


def _go_module(root: Path, files: dict[str, str]) -> None:
    (root / "go.mod").write_text("module example.com/m\n\ngo 1.22\n", encoding="utf-8")
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def test_go_a_local_shadowing_a_package_does_not_call_into_it(tmp_path: Path) -> None:
    _go_module(
        tmp_path,
        {
            "q/q.go": "package q\n\nfunc Get() int { return 1 }\n",
            "p/p.go": "package p\n\nfunc viaAny(q interface{ Get() int }) int { return q.Get() }\n",
        },
    )
    db = _ingest(tmp_path)
    (call,) = _calls_from(db, "p.viaAny")
    assert call.target != "q.Get"
    answer = json.loads(cgis_analyze_impact("q.Get", str(db), output_format="json"))
    assert "p.viaAny" not in {n["fqn"] for n in answer["nodes"]}
    # Still named as a possible caller, by its bare name: the gap is a place to read.
    assert [name for name, _ in answer["coverage"]["top_unresolved"]] == ["q.Get"]


def test_go_a_local_closure_named_len_is_not_the_builtin(tmp_path: Path) -> None:
    _go_module(
        tmp_path,
        {
            "p/p.go": "package p\n\nfunc f(xs []int) int {\n\tlen := func(int) int { return 0 }\n"
            "\treturn len(1)\n}\n\nfunc g(ys []int) int { return len(ys) }\n",
        },
    )
    db = _ingest(tmp_path)
    (closure,) = _calls_from(db, "p.f")
    (builtin,) = _calls_from(db, "p.g")
    assert builtin.target == "builtin.len"
    assert builtin.confidence == 1.0
    assert closure.target != builtin.target


def test_python_a_parameter_named_like_a_module_does_not_call_the_module(tmp_path: Path) -> None:
    # The owner-api shape: `vocabulary(1)` on a parameter landed on the FILE `vocabulary`.
    (tmp_path / "vocabulary.py").write_text("WORDS = []\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "def f(vocabulary):\n    return vocabulary(1)\n", encoding="utf-8"
    )
    db = _ingest(tmp_path)
    (call,) = _calls_from(db, "main.f")
    assert call.target != "vocabulary"
    with SQLiteStore(str(db)) as store:
        node = store.get_node(call.target)
    assert node is not None
    assert node.namespace is NodeNamespace.UNKNOWN
    assert node.name == "vocabulary"


def test_an_unresolved_class_reference_does_not_join_a_real_node() -> None:
    nodes = [
        Node(
            id="Widget",
            type=NodeType.FILE,
            name="Widget.py",
            file_path="Widget.py",
            start_line=1,
            end_line=1,
            language="python",
        ),
        Node(
            id="pkg.Frame",
            type=NodeType.CLASS,
            name="Frame",
            file_path="pkg/frame.x",
            start_line=1,
            end_line=2,
            language="go",
        ),
    ]
    edges = [
        Edge(
            id="pkg/frame.x:extends:Widget",
            source="pkg.Frame",
            target="raw_class:Widget",
            type=EdgeType.EXTENDS,
            confidence=1.0,
        ),
    ]
    resolved, virtual = ResolverEngine(nodes, edges).resolve()
    (extends,) = [e for e in resolved if e.type is EdgeType.EXTENDS]
    assert extends.target != "Widget"
    assert extends.target in {v.id for v in virtual}


def test_no_unresolved_edge_joins_a_real_node(tmp_path: Path) -> None:
    # The store-level invariant, over every shape above at once.
    _go_module(
        tmp_path,
        {
            "q/q.go": "package q\n\nfunc Get() int { return 1 }\n",
            "p/p.go": "package p\n\nfunc viaAny(q interface{ Get() int }) int { return q.Get() }\n",
        },
    )
    (tmp_path / "vocabulary.py").write_text("WORDS = []\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "def f(vocabulary):\n    return vocabulary(1)\n", encoding="utf-8"
    )
    db = _ingest(tmp_path)
    with sqlite3.connect(db) as conn:
        joined = conn.execute(
            "SELECT e.source, e.target FROM edges e JOIN nodes n ON e.target = n.id"
            " WHERE e.type IN ('CALLS', 'REFERENCES', 'EXTENDS') AND e.confidence < 1.0"
            " AND n.file_path != ?",
            (VIRTUAL_FILE_PATH,),
        ).fetchall()
    assert joined == []
