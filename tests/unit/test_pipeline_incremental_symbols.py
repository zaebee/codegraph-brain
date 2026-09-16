"""An incremental ingest produces the graph a full ingest would (#38).

Unchanged files keep their stored edges, which were resolved against the symbols
that existed when they were written. When a changed file alters what other files
resolve against — renames, deletes, adds a symbol, re-bases a class — those edges
go stale, so the run falls back to a full rebuild. A body-only edit stays
incremental.
"""

import sqlite3
from pathlib import Path

import pytest

from cgis.core.models import VIRTUAL_FILE_PATH, Edge, Node
from cgis.extractors.python_extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.storage.sqlite_store import SQLiteStore

Files = dict[str, str | None]


def _write(root: Path, files: Files) -> None:
    """Write (or, for None, delete) each file under root."""
    for rel, content in files.items():
        path = root / rel
        if content is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _graph(db: str) -> tuple[set[tuple[str, str, str]], set[str]]:
    """Edges as (source, target, type) and the ids of real (non-virtual) nodes."""
    with SQLiteStore(db) as store:
        edges = {(e.source, e.target, str(e.type)) for e in store.get_all_edges()}
        real = {n.id for n in store.get_all_nodes() if n.file_path != VIRTUAL_FILE_PATH}
    return edges, real


def _ingest(root: Path, db: str, pipeline: IngestionPipeline) -> None:
    """One pipeline run against the database at db."""
    with SQLiteStore(db) as store:
        pipeline.run(str(root), store=store)


_B_IMPORTS_FOO = "from pkg.a import foo\n\n\ndef bar():\n    return foo()\n"

SCENARIOS: dict[str, tuple[Files, Files]] = {
    "rename a symbol another file calls": (
        {"pkg/a.py": "def foo():\n    return 1\n", "pkg/b.py": _B_IMPORTS_FOO},
        {"pkg/a.py": "def foo2():\n    return 1\n"},
    ),
    "delete the file another file calls into": (
        {"pkg/a.py": "def foo():\n    return 1\n", "pkg/b.py": _B_IMPORTS_FOO},
        {"pkg/a.py": None},
    ),
    "add a file that makes a global name ambiguous": (
        {"pkg/a.py": "def foo():\n    return 1\n", "pkg/b.py": "def bar():\n    return foo()\n"},
        {"pkg/c.py": "def foo():\n    return 2\n"},
    ),
    "re-base a class a subclass inherits a method through": (
        {
            "pkg/base.py": (
                "class One:\n    def run(self):\n        return 1\n\n\n"
                "class Two:\n    def run(self):\n        return 2\n"
            ),
            "pkg/mid.py": "from pkg.base import One\n\n\nclass Mid(One):\n    pass\n",
            "pkg/leaf.py": (
                "from pkg.mid import Mid\n\n\n"
                "class Leaf(Mid):\n    def go(self):\n        return self.run()\n"
            ),
        },
        {"pkg/mid.py": "from pkg.base import Two\n\n\nclass Mid(Two):\n    pass\n"},
    ),
    "re-point a re-export another file imports through": (
        {
            "pkg/impl.py": "def foo():\n    return 1\n",
            "pkg/impl2.py": "def foo():\n    return 2\n",
            "pkg/api.py": "from pkg.impl import foo\n",
            "pkg/use.py": "from pkg.api import foo\n\n\ndef bar():\n    return foo()\n",
        },
        {"pkg/api.py": "from pkg.impl2 import foo\n"},
    ),
    # Both names stay in use, so the import map and re-exports are identical and
    # only the EXTENDS edge (or, below, the declared attribute type) differs.
    "re-base a class with both bases already imported": (
        {
            "pkg/base.py": (
                "class One:\n    def run(self):\n        return 1\n\n\n"
                "class Two:\n    def run(self):\n        return 2\n"
            ),
            "pkg/mid.py": (
                "from pkg.base import One, Two\n\nBASES = (One, Two)\n\n\n"
                "class Mid(One):\n    pass\n"
            ),
            "pkg/leaf.py": (
                "from pkg.mid import Mid\n\n\n"
                "class Leaf(Mid):\n    def go(self):\n        return self.run()\n"
            ),
        },
        {
            "pkg/mid.py": (
                "from pkg.base import One, Two\n\nBASES = (One, Two)\n\n\n"
                "class Mid(Two):\n    pass\n"
            )
        },
    ),
    "re-type an attribute a subclass calls through": (
        {
            "pkg/clients.py": (
                "class Fast:\n    def get(self):\n        return 1\n\n\n"
                "class Slow:\n    def get(self):\n        return 2\n"
            ),
            "pkg/core.py": (
                "from pkg.clients import Fast, Slow\n\nCLIENTS = (Fast, Slow)\n\n\n"
                "class Core:\n    def __init__(self, client: Fast) -> None:\n"
                "        self.client = client\n"
            ),
            "pkg/use.py": (
                "from pkg.core import Core\n\n\n"
                "class Use(Core):\n    def go(self):\n        return self.client.get()\n"
            ),
        },
        {
            "pkg/core.py": (
                "from pkg.clients import Fast, Slow\n\nCLIENTS = (Fast, Slow)\n\n\n"
                "class Core:\n    def __init__(self, client: Slow) -> None:\n"
                "        self.client = client\n"
            )
        },
    ),
    "edit only a function body": (
        {"pkg/a.py": "def foo():\n    return 1\n", "pkg/b.py": _B_IMPORTS_FOO},
        {"pkg/a.py": "def foo():\n    x = 2\n    return x\n"},
    ),
}


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_incremental_graph_equals_full_ingest(scenario: str, tmp_path: Path) -> None:
    """After the edit, the incremental database holds exactly the full-ingest edges and nodes."""
    initial, edit = SCENARIOS[scenario]
    pipeline = IngestionPipeline({".py": PythonExtractor()})

    work = tmp_path / "work"
    _write(work, initial)
    incremental_db = str(tmp_path / "incremental.db")
    _ingest(work, incremental_db, pipeline)
    _write(work, edit)
    _ingest(work, incremental_db, pipeline)

    full_db = str(tmp_path / "full.db")
    _ingest(work, full_db, pipeline)

    inc_edges, inc_nodes = _graph(incremental_db)
    full_edges, full_nodes = _graph(full_db)
    assert inc_nodes == full_nodes
    assert inc_edges - full_edges == set(), "stale edges survived the incremental run"
    assert full_edges - inc_edges == set(), "edges a full ingest has are missing"


class _CountingExtractor(PythonExtractor):
    """A PythonExtractor that records which files it parsed."""

    def __init__(self, parsed: list[str]) -> None:
        """Share the list the test reads."""
        super().__init__()
        self._parsed = parsed

    def parse(self, code: str, file_path: str) -> tuple[list[Node], list[Edge]]:
        """Record, then delegate."""
        self._parsed.append(file_path)
        return super().parse(code, file_path)


def _parsed_on_second_run(tmp_path: Path, edit: Files) -> list[str]:
    """Ingest the two-file fixture, apply edit, and return the files the second run parsed."""
    parsed: list[str] = []
    pipeline = IngestionPipeline({".py": _CountingExtractor(parsed)})
    work = tmp_path / "work"
    _write(work, {"pkg/a.py": "def foo():\n    return 1\n", "pkg/b.py": _B_IMPORTS_FOO})
    db = str(tmp_path / "g.db")
    _ingest(work, db, pipeline)
    parsed.clear()
    _write(work, edit)
    _ingest(work, db, pipeline)
    return sorted(parsed)


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        ({"pkg/a.py": "def foo():\n    x = 2\n    return x\n"}, ["pkg/a.py"]),
        ({"pkg/a.py": "def foo2():\n    return 1\n"}, ["pkg/a.py", "pkg/a.py", "pkg/b.py"]),
    ],
    ids=["body edit stays incremental", "rename rebuilds every file"],
)
def test_rebuild_only_when_symbols_change(tmp_path: Path, edit: Files, expected: list[str]) -> None:
    """A body edit re-parses one file; a symbol change re-parses the tree.

    The rename case parses `a.py` twice: once to learn its symbols changed, once
    in the rebuild. That repeat is the whole cost of the check.
    """
    assert _parsed_on_second_run(tmp_path, edit) == expected


@pytest.mark.parametrize(
    ("initial", "edit"),
    [
        (
            {"m.py": "def a():\n    return 1\n", "m/__init__.py": "def b():\n    return 1\n"},
            {"m.py": "def a():\n    return 5\n"},
        ),
        (
            {
                "c.py": (
                    "try:\n    import fast\n\n    class C:\n"
                    "        def __init__(self, x: int) -> None:\n            self.x = x\n"
                    "except ImportError:\n\n    class C:\n"
                    "        def __init__(self, x: str) -> None:\n            self.x = x\n"
                ),
            },
            {
                "c.py": (
                    "try:\n    import fast\n\n    class C:\n"
                    "        def __init__(self, x: int) -> None:\n            self.x = x + 0\n"
                    "except ImportError:\n\n    class C:\n"
                    "        def __init__(self, x: str) -> None:\n            self.x = x\n"
                ),
            },
        ),
    ],
    ids=["module file beside its package", "class defined in try and except"],
)
def test_body_edit_with_colliding_ids_stays_incremental(
    tmp_path: Path, initial: Files, edit: Files
) -> None:
    """Two nodes sharing an id are stored as one row; that must not read as a symbol change.

    `m.py` and `m/__init__.py` both map to module `m`, and a class defined in both
    branches of a `try` is one id twice. The store keeps one row per id, so the
    stored signature can never equal a naive fresh one — every edit would rebuild.
    """
    parsed: list[str] = []
    pipeline = IngestionPipeline({".py": _CountingExtractor(parsed)})
    work = tmp_path / "work"
    _write(work, initial)
    db = str(tmp_path / "g.db")
    _ingest(work, db, pipeline)
    for _ in range(2):
        parsed.clear()
        _write(work, edit)
        _ingest(work, db, pipeline)
        assert parsed == sorted(edit), "a body edit re-parsed the tree"
        edit = {path: text + "\n" for path, text in edit.items()}


def test_file_whose_ids_all_collide_is_not_new(tmp_path: Path) -> None:
    """With files_state lost, a module whose every id another file holds must not rebuild.

    `api.py` and `api/__init__.py` both map to `api`; the store keeps the rows of
    whichever was written last, so `api.py` has no stored rows of its own. Treating
    that as a new file rebuilt the owner-api graph on every lost-cache run.
    """
    parsed: list[str] = []
    pipeline = IngestionPipeline({".py": _CountingExtractor(parsed)})
    work = tmp_path / "work"
    _write(
        work, {"api.py": "def f():\n    return 1\n", "api/__init__.py": "def f():\n    return 2\n"}
    )
    db = str(tmp_path / "g.db")
    _ingest(work, db, pipeline)
    with SQLiteStore(db) as store:
        owners = {n.file_path for n in store.get_all_nodes() if n.id.startswith("api")}
    assert len(owners) == 1, "fixture no longer collides — the test would pass vacuously"

    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM files_state")
    parsed.clear()
    _ingest(work, db, pipeline)
    assert sorted(parsed) == ["api.py", "api/__init__.py"], (
        "the run rebuilt instead of staying incremental"
    )


def test_rebuild_that_fails_while_writing_keeps_the_old_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old graph is replaced inside the write transaction, so a failed write rolls back."""
    pipeline = IngestionPipeline({".py": PythonExtractor()})
    work = tmp_path / "work"
    _write(work, {"pkg/a.py": "def foo():\n    return 1\n", "pkg/b.py": _B_IMPORTS_FOO})
    db = str(tmp_path / "g.db")
    _ingest(work, db, pipeline)
    before = _graph(db)

    def broken_row(_self: SQLiteStore, _edge: Edge) -> tuple[object, ...]:
        msg = "disk full"
        raise sqlite3.OperationalError(msg)

    monkeypatch.setattr(SQLiteStore, "_edge_to_row", broken_row)
    root = str(work)
    with SQLiteStore(db) as store, pytest.raises(sqlite3.OperationalError):
        pipeline.run(root, store=store, rebuild=True)
    monkeypatch.undo()
    assert _graph(db) == before
