"""The diff check lists the subclasses and callers of what a git range changed."""

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cgis.cli import app
from cgis.languages import extractors
from cgis.pipeline import IngestionPipeline
from cgis.query.diffcheck import DiffError, changed_files, run_diff_check
from cgis.storage.sqlite_store import SQLiteStore


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def _commit(repo: Path, files: dict[str, str | None], message: str) -> str:
    """Write (or delete, for None) files, commit, and return the sha."""
    for name, text in files.items():
        path = repo / name
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path, files: dict[str, str]) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo, _commit(repo, files, "base")


def _store(repo: Path, tmp_path: Path) -> SQLiteStore:
    """Ingest the working tree at HEAD into a fresh db."""
    pipeline = IngestionPipeline(extractors([]))
    store = SQLiteStore(str(tmp_path / "g.db"))
    store.connect()
    pipeline.run(str(repo), store=store, rebuild=True)
    return store


BASE_A = "class A:\n    def run(self):\n        return 1\n"
SUB_B = "from a import A\n\n\nclass B(A):\n    pass\n"


def test_a_changed_base_class_lists_its_subclasses(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, {"a.py": BASE_A, "b.py": SUB_B})
    _commit(repo, {"a.py": BASE_A.replace("return 1", "return 2")}, "edit")
    with _store(repo, tmp_path) as store:
        report = run_diff_check(store, repo, base, "HEAD")
    assert [(i.kind, i.site, i.file) for i in report.items if i.kind == "subclass"] == [
        ("subclass", "b.B", "b.py")
    ]
    assert "a.A" in report.changed_symbols


def test_a_changed_function_lists_its_callers(tmp_path: Path) -> None:
    a = "def f():\n    return 1\n"
    b = "from a import f\n\n\ndef g():\n    return f()\n"
    repo, base = _repo(tmp_path, {"a.py": a, "b.py": b})
    _commit(repo, {"a.py": a.replace("1", "2")}, "edit")
    with _store(repo, tmp_path) as store:
        report = run_diff_check(store, repo, base)
    callers = [i for i in report.items if i.kind == "caller"]
    assert [(i.changed, i.site, i.line) for i in callers] == [("a.f", "b.g", 5)]


def test_a_removed_function_still_lists_its_callers(tmp_path: Path) -> None:
    a = "def f():\n    return 1\n\n\ndef keep():\n    return 0\n"
    b = "from a import f\n\n\ndef g():\n    return f()\n"
    repo, base = _repo(tmp_path, {"a.py": a, "b.py": b})
    _commit(repo, {"a.py": "def keep():\n    return 0\n"}, "remove f")
    with _store(repo, tmp_path) as store:
        report = run_diff_check(store, repo, base)
    assert "a.f" in report.changed_symbols
    assert [(i.changed, i.site) for i in report.items if i.kind == "caller"] == [("a.f", "b.g")]


def test_test_sites_are_counted_not_listed(tmp_path: Path) -> None:
    a = "def f():\n    return 1\n"
    b = "from a import f\n\n\ndef g():\n    return f()\n"
    t = "from a import f\n\n\ndef test_f():\n    assert f()\n"
    repo, base = _repo(tmp_path, {"a.py": a, "b.py": b, "tests/test_a.py": t})
    _commit(repo, {"a.py": a.replace("1", "2")}, "edit")
    with _store(repo, tmp_path) as store:
        report = run_diff_check(store, repo, base)
    assert [i.site for i in report.items] == ["b.g"]
    assert report.in_tests == 1


def test_cap_shows_five_and_counts_the_rest(tmp_path: Path) -> None:
    a = "def f():\n    return 1\n"
    callers = {f"c{n}.py": f"from a import f\n\n\ndef g{n}():\n    return f()\n" for n in range(8)}
    repo, base = _repo(tmp_path, {"a.py": a, **callers})
    _commit(repo, {"a.py": a.replace("1", "2")}, "edit")
    with _store(repo, tmp_path) as store:
        report = run_diff_check(store, repo, base)
        everything = run_diff_check(store, repo, base, cap=100)
    assert len(report.items) == 5
    assert report.hidden == 3
    assert len(everything.items) == 8
    assert everything.hidden == 0


def test_deleted_and_renamed_files_are_checked(tmp_path: Path) -> None:
    gone = "def old():\n    return 1\n"
    moved = "def moved():\n    return 2\n" + "\n".join(f"# pad {n}" for n in range(20)) + "\n"
    use = (
        "from gone import old\nfrom moved import moved\n\n\ndef g():\n    return old() + moved()\n"
    )
    repo, base = _repo(tmp_path, {"gone.py": gone, "moved.py": moved, "use.py": use})
    _git(repo, "mv", "moved.py", "moved2.py")
    _commit(repo, {"gone.py": None}, "delete and rename")
    changes = {(c.old_path, c.new_path) for c in changed_files(repo, base, "HEAD")}
    assert ("gone.py", None) in changes
    assert ("moved.py", "moved2.py") in changes
    with _store(repo, tmp_path) as store:
        report = run_diff_check(store, repo, base)
    assert {"gone.old", "moved.moved"} <= set(report.changed_symbols)
    assert {(i.changed, i.site) for i in report.items} == {
        ("gone.old", "use.g"),
        ("moved.moved", "use.g"),
    }


def test_a_prefix_maps_diff_paths_onto_graph_files(tmp_path: Path) -> None:
    a = "def f():\n    return 1\n"
    b = "from a import f\n\n\ndef g():\n    return f()\n"
    repo, base = _repo(tmp_path, {"app/a.py": a, "app/b.py": b, "other/x.py": "X = 1\n"})
    _commit(repo, {"app/a.py": a.replace("1", "2"), "other/x.py": "X = 2\n"}, "edit")
    pipeline = IngestionPipeline(extractors([]))
    store = SQLiteStore(str(tmp_path / "p.db"))
    store.connect()
    pipeline.run(str(repo / "app"), store=store, rebuild=True)
    with store:
        report = run_diff_check(store, repo, base, prefix="app")
    assert [(i.changed, i.site) for i in report.items] == [("a.f", "b.g")]


def test_bad_range_is_a_clear_error(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path, {"a.py": BASE_A})
    with pytest.raises(DiffError, match="nope"):
        changed_files(repo, "nope", "HEAD")
    db = tmp_path / "g.db"
    with _store(repo, tmp_path):
        pass
    result = CliRunner().invoke(
        app, ["diff-check", "--db", str(db), "--repo", str(repo), "--base", "nope..range"]
    )
    assert result.exit_code == 2
    assert "nope..range" in result.output
