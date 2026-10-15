"""A graph built by other extraction code is rebuilt in full, not patched file by file.

An incremental run re-parses only the files whose text changed, so after an
upgrade that changes how nodes and edges are built, the graph mixed old and new
extraction with nothing to say so: Go's receiver types (#608) reached only the
edited files, and a security audit by route role (#566) would silently see only
the handlers in them. `ingest_state` now records a fingerprint of the extraction
code; a graph whose fingerprint differs is rebuilt once.
"""

from pathlib import Path

import pytest

from cgis.core.models import Edge, Node
from cgis.languages.python.extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline, extraction_fingerprint
from cgis.storage.sqlite_store import SQLiteStore


def _package(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def test_the_fingerprint_follows_the_extraction_code_only(tmp_path: Path) -> None:
    pkg = _package(
        tmp_path / "cgis",
        {
            "languages/py.py": "a = 1\n",
            "resolver/r.py": "b = 1\n",
            "query/q.py": "c = 1\n",
            "storage/sqlite_store.py": "d = 1\n",
        },
    )
    before = extraction_fingerprint(pkg)
    assert extraction_fingerprint(pkg) == before  # stable
    (pkg / "query/q.py").write_text("c = 2\n", encoding="utf-8")
    assert extraction_fingerprint(pkg) == before  # a query change builds the same graph
    (pkg / "languages/py.py").write_text("a = 2\n", encoding="utf-8")
    assert extraction_fingerprint(pkg) != before
    (pkg / "languages/py.py").write_text("a = 1\n", encoding="utf-8")
    (pkg / "resolver/r.py").write_text("b = 2\n", encoding="utf-8")
    assert extraction_fingerprint(pkg) != before  # the resolver decides stored edges too
    (pkg / "resolver/r.py").write_text("b = 1\n", encoding="utf-8")
    (pkg / "storage/sqlite_store.py").write_text("d = 2\n", encoding="utf-8")
    assert extraction_fingerprint(pkg) != before  # so does how rows are written (#617 review)


def test_the_store_owns_the_fingerprint_key(tmp_path: Path) -> None:
    """A pack cannot write it, and recording pack state never deletes it."""
    with SQLiteStore(str(tmp_path / "g.db")) as store:
        store.record_extraction_fingerprint("abc")
        with pytest.raises(ValueError, match="extraction_fingerprint"):
            store.record_state("extraction_fingerprint", "x")
        store.record_pack_state({})
        assert store.get_extraction_fingerprint() == "abc"
        assert "extraction_fingerprint" not in store.pack_state()


class _Counting(PythonExtractor):
    def __init__(self, parsed: list[str]) -> None:
        super().__init__()
        self._parsed = parsed

    def parse(self, code: str, file_path: str) -> tuple[list[Node], list[Edge]]:
        self._parsed.append(file_path)
        return super().parse(code, file_path)


def _second_run(tmp_path: Path, stored: str | None) -> tuple[list[str], str | None]:
    """Files parsed by an unchanged second run, against a stored fingerprint of `stored`."""
    work = _package(
        tmp_path / "w", {"a.py": "def f():\n    pass\n", "b.py": "def g():\n    pass\n"}
    )
    db = str(tmp_path / "g.db")
    parsed: list[str] = []
    with SQLiteStore(db) as store:
        IngestionPipeline({".py": _Counting(parsed)}).run(str(work), store=store)
        if stored is None:
            store._conn.execute("DELETE FROM ingest_state WHERE key = 'extraction_fingerprint'")  # type: ignore[union-attr]  # noqa: SLF001
            store._conn.commit()  # type: ignore[union-attr]  # noqa: SLF001
        else:
            store.record_extraction_fingerprint(stored)
        parsed.clear()
        IngestionPipeline({".py": _Counting(parsed)}).run(str(work), store=store)
        return sorted(parsed), store.get_extraction_fingerprint()


def test_an_unchanged_tree_with_the_same_extraction_parses_nothing(tmp_path: Path) -> None:
    parsed, recorded = _second_run(tmp_path, stored=extraction_fingerprint())
    assert parsed == []
    assert recorded == extraction_fingerprint()


def test_other_extraction_code_rebuilds_every_file_and_records_its_own(tmp_path: Path) -> None:
    parsed, recorded = _second_run(tmp_path, stored="built-by-an-older-cgis")
    assert [Path(p).name for p in parsed] == ["a.py", "b.py"]
    assert recorded == extraction_fingerprint()


def test_a_graph_from_before_fingerprints_is_rebuilt(tmp_path: Path) -> None:
    parsed, recorded = _second_run(tmp_path, stored=None)
    assert [Path(p).name for p in parsed] == ["a.py", "b.py"]
    assert recorded == extraction_fingerprint()


def test_an_install_without_sources_has_no_fingerprint(tmp_path: Path) -> None:
    """A .pyc-only install or a zipapp: no fingerprint, not the hash of nothing.

    The hash of nothing is a constant, so the check would pass forever and say nothing.
    """
    assert extraction_fingerprint(tmp_path / "cgis") is None


def test_a_rebuild_that_keeps_the_stored_graph_does_not_bless_it(tmp_path: Path) -> None:
    """#617 review: an empty walk keeps the old graph, which must not be recorded as current.

    A mistyped path (or an extractor that fails on every file) extracts nothing, so
    the rebuild keeps the stored graph; recording the current fingerprint then would
    leave a graph built by older code that no later run rebuilds.
    """
    work = _package(tmp_path / "w", {"a.py": "def f():\n    pass\n"})
    empty = tmp_path / "empty"
    empty.mkdir()
    db = str(tmp_path / "g.db")
    with SQLiteStore(db) as store:
        IngestionPipeline({".py": PythonExtractor()}).run(str(work), store=store)
        store.record_extraction_fingerprint("built-by-an-older-cgis")
        IngestionPipeline({".py": PythonExtractor()}).run(str(empty), store=store)
        assert store.get_extraction_fingerprint() == "built-by-an-older-cgis"
