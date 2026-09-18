"""`if TYPE_CHECKING:` imports are not a runtime dependency (#499).

The guard is the standard way to *break* an import cycle: the import runs for the
type checker and never at runtime. The extractor did not distinguish it, so
`cgis analyze` reported the pair as a circular dependency and advised a Mediator
— for a cycle the code had already broken that way. On owner-api the first
anomaly reported was exactly this: `models.address`, `models.finance` and
`models.owner`, every edge of it inside a TYPE_CHECKING block.

The edge stays in the graph. "What does this module depend on at type level" is a
real question, and `trace` must still answer it; only the cycle query skips it.
"""

from pathlib import Path

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.extractors.python_extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.query.analysis.analyzer import AnalyzerEngine
from cgis.storage.sqlite_store import SQLiteStore

_GUARDED = """\
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pkg.b import B


def use(b: "B") -> int:
    return 1
"""

_RUNTIME = """\
from pkg.a import use


class B:
    def go(self) -> int:
        return use(self)
"""


def _imports(code: str, file_path: str = "pkg/a.py") -> list:
    _nodes, edges = PythonExtractor().parse(code, file_path)
    return [edge for edge in edges if edge.type is EdgeType.IMPORTS]


def test_a_guarded_import_is_marked_type_only() -> None:
    """The edge is kept, and says it is not a runtime dependency."""
    edges = _imports(_GUARDED)

    guarded = next(edge for edge in edges if edge.target.endswith("pkg.b"))
    assert guarded.type_only is True


def test_an_ordinary_import_is_not() -> None:
    """The flag is about the guard, not about imports in general."""
    edges = _imports("import os\n\n\ndef go():\n    return os.getcwd()\n")

    assert all(edge.type_only is False for edge in edges)


def test_an_import_after_the_block_is_not_marked() -> None:
    """Only what is inside the block; the guard ends where the indentation does."""
    code = "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    import json\n\nimport os\n"
    edges = _imports(code)

    by_target = {edge.target: edge.type_only for edge in edges}
    assert by_target["json"] is True
    assert by_target["os"] is False


def test_an_import_under_another_condition_is_not_marked() -> None:
    """A conditional import is still a runtime one — only the TYPE_CHECKING guard counts.

    `if sys.version_info >= (3, 12): import tomllib` runs; treating every `if` as
    the guard would quietly drop real dependencies from cycle detection.
    """
    code = (
        "import sys\n\n"
        "if sys.version_info >= (3, 12):\n    import tomllib\n"
        "else:\n    import tomli as tomllib\n"
    )
    edges = _imports(code)

    assert {edge.target for edge in edges} >= {"tomllib", "tomli"}
    assert all(edge.type_only is False for edge in edges)


def test_a_negated_guard_is_not_the_guard() -> None:
    """`if not TYPE_CHECKING:` is the runtime branch, and reads as one."""
    code = "from typing import TYPE_CHECKING\n\nif not TYPE_CHECKING:\n    import json\n"
    edges = _imports(code)

    assert {edge.target: edge.type_only for edge in edges}["json"] is False


def test_a_type_only_cycle_is_not_reported(tmp_path: Path) -> None:
    """The whole point: the way to break a cycle must not read as one (#499)."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "a.py").write_text(_GUARDED, encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text(_RUNTIME, encoding="utf-8")
    db = str(tmp_path / "graph.db")

    with SQLiteStore(db) as store:
        IngestionPipeline({".py": PythonExtractor()}).run(str(tmp_path), store=store)
        anomalies = AnalyzerEngine(store).detect_cycles()
        edges = store.get_all_edges()

    assert anomalies == []
    # Still in the graph: `trace` and `impact` answer the type-level question.
    imports = [edge for edge in edges if edge.type is EdgeType.IMPORTS]
    assert any(edge.type_only and edge.target.endswith("pkg.b") for edge in imports)


def test_a_runtime_cycle_is_still_reported(tmp_path: Path) -> None:
    """Without the guard the same two modules are a real cycle."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "a.py").write_text(
        "from pkg.b import B\n\n\ndef use(b: B) -> int:\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "pkg" / "b.py").write_text(_RUNTIME, encoding="utf-8")
    db = str(tmp_path / "graph.db")

    with SQLiteStore(db) as store:
        IngestionPipeline({".py": PythonExtractor()}).run(str(tmp_path), store=store)
        anomalies = AnalyzerEngine(store).detect_cycles()

    assert len(anomalies) == 1
    assert set(anomalies[0].metrics["cycle_members"]) == {"pkg.a", "pkg.b"}


def test_the_flag_survives_a_round_trip(tmp_path: Path) -> None:
    """Stored and read back, or the cycle query would see it as runtime after re-open."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "a.py").write_text(_GUARDED, encoding="utf-8")
    db = str(tmp_path / "graph.db")

    with SQLiteStore(db) as store:
        IngestionPipeline({".py": PythonExtractor()}).run(str(tmp_path), store=store)

    with SQLiteStore(db) as store:
        edges = [e for e in store.get_all_edges() if e.type is EdgeType.IMPORTS]

    assert any(edge.type_only for edge in edges)


def test_an_old_database_opens_and_reads_its_imports_as_runtime(tmp_path: Path) -> None:
    """A graph written before the column exists migrates on open, and keeps its edges.

    Without the migration the very first query raises `no such column: type_only`,
    which is a worse outcome than the false cycle this fixes.
    """
    db = str(tmp_path / "old.db")
    with SQLiteStore(db) as store:
        store.save_graph(
            [_module("pkg.a"), _module("pkg.b")],
            [
                Edge(
                    id="e1",
                    source="pkg.a",
                    target="pkg.b",
                    type=EdgeType.IMPORTS,
                    confidence=1.0,
                )
            ],
        )
        conn = store._conn  # noqa: SLF001  # white-box: simulating a pre-#499 schema
        assert conn is not None
        conn.execute("ALTER TABLE edges DROP COLUMN type_only")
        conn.commit()

    with SQLiteStore(db) as store:
        edges = store.get_all_edges()

    assert [(edge.id, edge.type_only) for edge in edges] == [("e1", False)]


def _module(fqn: str) -> Node:
    return Node(
        id=fqn,
        type=NodeType.FILE,
        name=fqn,
        file_path=f"{fqn.replace('.', '/')}.py",
        start_line=1,
        end_line=1,
    )
