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


def test_the_runtime_branch_of_the_guard_is_not_marked() -> None:
    """`if TYPE_CHECKING: … else: <shim>` — the fallback runs (#501 review).

    `else_clause` is a child of the same `if_statement`, so testing the condition
    alone marked the shim type-only and dropped a real dependency.
    """
    code = (
        "from typing import TYPE_CHECKING\n\n"
        "if TYPE_CHECKING:\n    from pkg.types import T\n"
        "else:\n    from pkg.runtime import T\n"
    )
    by_target = {edge.target: edge.type_only for edge in _imports(code)}

    assert by_target["pkg.types"] is True
    assert by_target["pkg.runtime"] is False


def test_an_elif_branch_is_not_marked() -> None:
    """Same statement, another branch that runs."""
    code = (
        "import sys\nfrom typing import TYPE_CHECKING\n\n"
        "if TYPE_CHECKING:\n    import tomllib\n"
        "elif sys.version_info >= (3, 12):\n    import tomli\n"
    )
    by_target = {edge.target: edge.type_only for edge in _imports(code)}

    assert by_target["tomllib"] is True
    assert by_target["tomli"] is False


def test_a_module_imported_both_ways_stays_runtime(tmp_path: Path) -> None:
    """The edge id carries no branch, so marking the guarded copy hid a real cycle.

    `from pkg.b import helper` at the top and `from pkg.b import B` under the guard
    share `pkg.a:imports:pkg.b`; `INSERT OR REPLACE` keeps the last one written, so
    the graph lost the runtime dependency and `analyze` reported no cycle at all —
    a false negative in the query this feature exists to correct (#501 review).
    """
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "a.py").write_text(
        "from typing import TYPE_CHECKING\n"
        "from pkg.b import helper\n\n"
        "if TYPE_CHECKING:\n    from pkg.b import B\n\n\n"
        "def use() -> int:\n    return helper()\n",
        encoding="utf-8",
    )
    (tmp_path / "pkg" / "b.py").write_text(
        "from pkg.a import use\n\n\ndef helper() -> int:\n    return 1\n\n\nclass B:\n    pass\n",
        encoding="utf-8",
    )
    db = str(tmp_path / "graph.db")

    with SQLiteStore(db) as store:
        IngestionPipeline({".py": PythonExtractor()}).run(str(tmp_path), store=store)
        anomalies = AnalyzerEngine(store).detect_cycles()
        stored = {edge.id: edge.type_only for edge in store.get_all_edges()}

    assert stored["pkg.a:imports:pkg.b"] is False
    assert len(anomalies) == 1


def test_a_parenthesised_guard_still_counts() -> None:
    """`if (TYPE_CHECKING):` is the same guard."""
    code = "from typing import TYPE_CHECKING\n\nif (TYPE_CHECKING):\n    import json\n"

    assert {edge.target: edge.type_only for edge in _imports(code)}["json"] is True


def test_symbol_imports_inside_the_guard_are_marked_too() -> None:
    """The flag describes the statement, so every edge it emits carries it."""
    _nodes, edges = PythonExtractor().parse(_GUARDED, "pkg/a.py")
    by_id = {edge.id: edge.type_only for edge in edges if edge.type is EdgeType.IMPORTS_SYMBOL}

    assert by_id["pkg.a:imports_symbol:pkg.b.B"] is True
    # The guard's own import is an ordinary one.
    assert by_id["pkg.a:imports_symbol:typing.TYPE_CHECKING"] is False


def test_incremental_ingest_over_an_upgraded_graph_refreshes_the_flags(tmp_path: Path) -> None:
    """The migration blanks the file hashes, or nobody gets this fix (#501 review).

    `_process_file` skips a file whose hash still matches, so an incremental run
    over a graph written before the column would re-parse nothing and keep
    reporting the false cycle.
    """
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "a.py").write_text(_GUARDED, encoding="utf-8")
    db = str(tmp_path / "graph.db")
    pipeline = IngestionPipeline({".py": PythonExtractor()})

    with SQLiteStore(db) as store:
        pipeline.run(str(tmp_path), store=store)
        conn = store._conn  # noqa: SLF001  # white-box: simulating a pre-#499 graph
        assert conn is not None
        conn.execute("ALTER TABLE edges DROP COLUMN type_only")
        conn.commit()

    with SQLiteStore(db) as store:  # migration runs on open
        pipeline.run(str(tmp_path), store=store)
        edges = [e for e in store.get_all_edges() if e.type is EdgeType.IMPORTS]

    assert any(edge.type_only for edge in edges), "the re-ingest must re-read the guard"


def test_the_health_scorer_agrees_with_analyze(tmp_path: Path) -> None:
    """One predicate, three cycle computations — or the graph contradicts itself.

    `HealthScorer` writes `in_cycle` into node metadata; before #501 it still
    counted type-only imports, so `analyze` reported no cycle while the nodes
    carried `in_cycle: true`.
    """
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "a.py").write_text(_GUARDED, encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text(_RUNTIME, encoding="utf-8")
    db = str(tmp_path / "graph.db")

    with SQLiteStore(db) as store:
        IngestionPipeline({".py": PythonExtractor()}).run(str(tmp_path), store=store)
        nodes = store.get_all_nodes()

    assert not [node.id for node in nodes if node.metadata.get("in_cycle")]
