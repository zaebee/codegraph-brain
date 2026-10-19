"""The diff check lists the subclasses and callers of what a git range changed."""

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cgis.cli import app
from cgis.languages import extractors
from cgis.pipeline import IngestionPipeline
from cgis.query.diffcheck import (
    DiffError,
    changed_files,
    render_text,
    report_to_dict,
    run_diff_check,
)
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


@pytest.mark.parametrize("side", ["base", "head"])
def test_a_revision_that_looks_like_a_flag_is_refused(tmp_path: Path, side: str) -> None:
    repo, sha = _repo(tmp_path, {"a.py": BASE_A})
    target = tmp_path / "pwned"
    flag = f"--output={target}"
    args = (flag, "HEAD") if side == "base" else (sha, flag)
    with pytest.raises(DiffError):
        changed_files(repo, *args)
    assert not target.exists()


def test_a_removed_content_line_that_looks_like_a_header_is_content(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path, {"q.py": "x = 1\n"})
    # A removed line that starts with "-- " reads as "--- ..." in a -U0 diff.
    (repo / "q.py").write_text("-- note\nx = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "q")
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, {"q.py": "x = 1\n"}, "drop the first line")
    (change,) = changed_files(repo, base, "HEAD")
    assert (change.old_path, change.new_path) == ("q.py", "q.py")
    assert change.old_lines == frozenset({1})


def test_base_reparse_uses_the_graphs_source_roots(tmp_path: Path) -> None:
    a = "def f():\n    return 1\n"
    b = "from a import f\n\n\ndef g():\n    return f()\n"
    repo, base = _repo(tmp_path, {"src/a.py": a, "src/b.py": b})
    _commit(repo, {"src/a.py": a.replace("1", "2")}, "edit")
    store = SQLiteStore(str(tmp_path / "s.db"))
    store.connect()
    IngestionPipeline(extractors(["src"])).run(str(repo), store=store, rebuild=True)
    store.record_ingest_options(["src"], None)
    with store:
        report = run_diff_check(store, repo, base)
    assert report.changed_symbols == ["a.f"]
    assert [(i.changed, i.site) for i in report.items] == [("a.f", "b.g")]


def test_a_removed_class_lists_its_subclasses(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, {"a.py": BASE_A + "\n\nclass Z:\n    pass\n", "b.py": SUB_B})
    _commit(repo, {"a.py": "class Z:\n    pass\n"}, "remove A")
    with _store(repo, tmp_path) as store:
        report = run_diff_check(store, repo, base)
    assert "a.A" in report.changed_symbols
    assert [(i.kind, i.changed, i.site) for i in report.items] == [("subclass", "a.A", "b.B")]


# --- Final review fixes (I1-I3) ------------------------------------------------------


def test_an_added_empty_file_has_no_old_path(tmp_path: Path) -> None:
    """I1: git writes no ---/+++ for an empty file, so the header's paths must not stand."""
    a = "def f():\n    return 1\n"
    b = "from pkg.a import f\n\n\ndef g():\n    return f()\n"
    repo, base = _repo(tmp_path, {"pkg/a.py": a, "pkg/b.py": b})
    _commit(repo, {"pkg/__init__.py": "", "pkg/a.py": a.replace("1", "2")}, "add package")
    changes = {(c.old_path, c.new_path) for c in changed_files(repo, base, "HEAD")}
    assert (None, "pkg/__init__.py") in changes
    with _store(repo, tmp_path) as store:
        report = run_diff_check(store, repo, base)
    assert [(i.changed, i.site) for i in report.items] == [("pkg.a.f", "pkg.b.g")]


def test_a_deleted_empty_file_has_no_new_path(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, {"pkg/__init__.py": "", "pkg/a.py": BASE_A})
    _commit(repo, {"pkg/__init__.py": None}, "drop the package marker")
    assert [(c.old_path, c.new_path) for c in changed_files(repo, base, "HEAD")] == [
        ("pkg/__init__.py", None)
    ]
    with _store(repo, tmp_path) as store:
        run_diff_check(store, repo, base)


def test_an_added_binary_file_has_no_old_path(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, {"a.py": BASE_A})
    (repo / "blob.py").write_bytes(b"\x00\x01\x02binary\x00")
    _commit(repo, {"a.py": BASE_A.replace("return 1", "return 2")}, "add a binary")
    changes = {(c.old_path, c.new_path) for c in changed_files(repo, base, "HEAD")}
    assert (None, "blob.py") in changes
    with _store(repo, tmp_path) as store:
        report = run_diff_check(store, repo, base)
    assert "a.A.run" in report.changed_symbols


def test_a_user_noprefix_config_does_not_cut_paths(tmp_path: Path) -> None:
    """I2: `diff.noprefix=true` drops `a/` and `b/`; the check must not cut two characters."""
    a = "def f():\n    return 1\n"
    b = "from app.a import f\n\n\ndef g():\n    return f()\n"
    repo, base = _repo(tmp_path, {"app/a.py": a, "app/b.py": b})
    _git(repo, "config", "diff.noprefix", "true")
    _git(repo, "config", "diff.mnemonicPrefix", "true")
    _commit(repo, {"app/a.py": a.replace("1", "2")}, "edit")
    (change,) = changed_files(repo, base, "HEAD")
    assert (change.old_path, change.new_path) == ("app/a.py", "app/a.py")
    with _store(repo, tmp_path) as store:
        report = run_diff_check(store, repo, base)
    assert [(i.changed, i.site) for i in report.items] == [("app.a.f", "app.b.g")]


A_APP = "def f():\n    return 1\n"
B_APP = "from a import f\n\n\ndef g():\n    return f()\n"


def _app_graph(tmp_path: Path, *, record: bool) -> tuple[Path, str, Path]:
    """A repository whose `app/` was ingested on its own; `record` stores the ingest root."""
    repo, base = _repo(tmp_path, {"app/a.py": A_APP, "app/b.py": B_APP})
    _commit(repo, {"app/a.py": A_APP.replace("1", "2")}, "edit")
    db = tmp_path / "app.db"
    store = SQLiteStore(str(db))
    store.connect()
    IngestionPipeline(extractors([])).run(str(repo / "app"), store=store, rebuild=True)
    if record:
        store.record_ingest(str(repo / "app"))
    store.disconnect()
    return repo, base, db


def test_the_prefix_defaults_to_the_recorded_ingest_root(tmp_path: Path) -> None:
    """I3: the graph knows where it was ingested from; no prefix must not mean the repo root."""
    repo, base, db = _app_graph(tmp_path, record=True)
    with SQLiteStore(str(db)) as store:
        report = run_diff_check(store, repo, base)
    assert [(i.changed, i.site) for i in report.items] == [("a.f", "b.g")]
    result = CliRunner().invoke(
        app, ["diff-check", "--db", str(db), "--repo", str(repo), "--base", base]
    )
    assert result.exit_code == 0, result.output
    assert "b.g" in result.output


def test_a_prefix_that_maps_no_changed_file_is_a_clear_error(tmp_path: Path) -> None:
    """I3: a wrong prefix used to invent `removed` items from the base re-parse."""
    repo, base, db = _app_graph(tmp_path, record=False)
    with SQLiteStore(str(db)) as store, pytest.raises(DiffError, match="prefix"):
        run_diff_check(store, repo, base, prefix="")
    result = CliRunner().invoke(
        app, ["diff-check", "--db", str(db), "--repo", str(repo), "--base", base, "--prefix", ""]
    )
    assert result.exit_code == 2
    assert "prefix" in result.output


def test_a_diff_of_only_added_files_passes_on_a_graph_built_after_it(tmp_path: Path) -> None:
    """The mapping guard checks the head side: an added file is in a fresh graph."""
    repo, base = _repo(tmp_path, {"app/a.py": A_APP})
    _commit(repo, {"app/b.py": B_APP}, "add b")
    with _store(repo, tmp_path) as store:
        report = run_diff_check(store, repo, base)
    assert report.items == []


def test_a_diff_of_only_added_files_is_an_error_on_a_graph_built_before_it(
    tmp_path: Path,
) -> None:
    """The same diff against a graph of the base: the added file is not in it."""
    repo, base = _repo(tmp_path, {"app/a.py": A_APP})
    with _store(repo, tmp_path) as store:
        _commit(repo, {"app/b.py": B_APP}, "add b")
        with pytest.raises(DiffError, match="predates"):
            run_diff_check(store, repo, base)


def test_the_mcp_tool_says_when_no_changed_file_maps(tmp_path: Path) -> None:
    from cgis.api.mcp_server import cgis_diff_check  # noqa: PLC0415 — the server is heavy

    repo, base, db = _app_graph(tmp_path, record=False)
    text = cgis_diff_check(str(repo), base, "HEAD", str(db))
    assert text.startswith("❌")
    assert "prefix" in text
    assert "removed" not in text


def test_a_root_outside_the_repository_is_a_clear_error(tmp_path: Path) -> None:
    repo, base, db = _app_graph(tmp_path, record=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    args = ["diff-check", "--db", str(db), "--repo", str(repo), "--base", base]
    result = CliRunner().invoke(app, [*args, "--root", str(elsewhere)])
    assert result.exit_code == 2
    assert "outside" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


# --- data keys are opt-in -----------------------------------------------------------

KEYED = "def save(doc):\n    doc['cover'] = 1\n\n\ndef load(doc):\n    return doc['cover']\n"
MAPPING = '{"mappings": {"properties": {"cover": {"type": "keyword"}}}}\n'


def _keyed_check(tmp_path: Path, *, data_keys: bool) -> tuple[Path, str, Path]:
    """A change to a key writer and to a mapping, ingested with data keys on or off."""
    repo, base = _repo(tmp_path, {"m.py": KEYED, "map.json": MAPPING})
    _commit(
        repo,
        {"m.py": KEYED.replace("= 1", "= 2"), "map.json": MAPPING.replace("keyword", "text")},
        "edit",
    )
    db = tmp_path / "keys.db"
    flag = "--data-keys" if data_keys else "--no-data-keys"
    result = CliRunner().invoke(app, ["ingest", str(repo), "-o", str(db), flag])
    assert result.exit_code == 0, result.output
    return repo, base, db


def test_a_graph_without_data_keys_says_how_to_turn_them_on(tmp_path: Path) -> None:
    repo, base, db = _keyed_check(tmp_path, data_keys=False)
    with SQLiteStore(str(db)) as store:
        report = run_diff_check(store, repo, base, cap=100)
    assert report.data_keys is False
    assert not [i for i in report.items if i.kind.startswith("key-")]
    assert report.changed_symbols == ["m.save"]
    text = render_text(report, base, "HEAD")
    assert sum("--data-keys" in line for line in text.splitlines()) == 1
    assert "data keys are off" in text.lower()
    assert "--data-keys" in str(report_to_dict(report)["note"])


def test_a_graph_with_data_keys_lists_key_items_without_the_note(tmp_path: Path) -> None:
    repo, base, db = _keyed_check(tmp_path, data_keys=True)
    with SQLiteStore(str(db)) as store:
        report = run_diff_check(store, repo, base, cap=100)
    assert report.data_keys is True
    assert report.note == ""
    assert [i for i in report.items if i.kind.startswith("key-")]
    assert "--data-keys" not in render_text(report, base, "HEAD")


def test_a_graph_whose_options_predate_the_mode_reads_it_from_its_key_edges(
    tmp_path: Path,
) -> None:
    """Options recorded before data keys were opt-in carry no `data_keys`: not "off"."""
    repo, base, db = _keyed_check(tmp_path, data_keys=True)
    with sqlite3.connect(db) as conn:
        (raw,) = conn.execute(
            "SELECT value FROM ingest_state WHERE key = 'ingest_options'"
        ).fetchone()
        options = json.loads(raw)
        del options["data_keys"]
        conn.execute(
            "UPDATE ingest_state SET value = ? WHERE key = 'ingest_options'",
            (json.dumps(options),),
        )
    with SQLiteStore(str(db)) as store:
        assert store.get_ingest_data_keys() is None
        report = run_diff_check(store, repo, base, cap=100)
    assert report.data_keys is True
    assert report.note == ""
    assert [i for i in report.items if i.kind.startswith("key-")]
