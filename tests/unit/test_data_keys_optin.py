"""The data-key layer is opt-in: off by default, on with `--data-keys` / CGIS_DATA_KEYS=1.

Measured on owner-api it found few items worth a look while costing every ingest
time, graph size and a stale graph on every JSON or YAML edit, so it ships off
(docs/lab-notes/2026-10-19-data-key-diff-check.md, "Shipped off by default").
"""

import os
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cgis.api import auto_refresh
from cgis.api.auto_refresh import AUTO_REFRESH_ENV, refresh_if_stale
from cgis.api.mcp_server import cgis_ingest
from cgis.cli import app
from cgis.core.freshness import FreshnessState
from cgis.core.keys import DATA_KEYS_ENV, data_keys_enabled
from cgis.core.models import KEY_EDGE_TYPES, NodeType
from cgis.languages import extractors, is_supported
from cgis.languages.python.extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.storage.sqlite_store import SQLiteStore

_PAST = time.time() - 100
MOD = 'INDEX = "vehicles"\n\n\ndef save(doc):\n    doc["cover"] = 1\n'
MAPPING = '{"mappings": {"properties": {"cover": {"type": "keyword"}}}}\n'


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts without the opt-in, and as a new server process."""
    monkeypatch.delenv(DATA_KEYS_ENV, raising=False)
    monkeypatch.delenv(AUTO_REFRESH_ENV, raising=False)
    monkeypatch.setattr(auto_refresh, "_REBUILT_FOR_CODE", set())
    monkeypatch.setattr(auto_refresh, "_EMPTY_REFRESH", {})


def _repo(tmp_path: Path) -> Path:
    """A module that writes a key, and a mapping that declares it; mtimes in the past."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(MOD, encoding="utf-8")
    (repo / "mapping.json").write_text(MAPPING, encoding="utf-8")
    for p in (repo / "mod.py", repo / "mapping.json", repo):
        os.utime(p, (_PAST, _PAST))
    return repo


def _keys(db: Path) -> tuple[set[str], set[str]]:
    """(DATA_KEY node ids, file paths) of the stored graph."""
    with SQLiteStore(str(db)) as store:
        nodes = store.get_all_nodes()
    return {n.id for n in nodes if n.type is NodeType.DATA_KEY}, {n.file_path for n in nodes}


def _ingest(repo: Path, db: Path, *flags: str) -> str:
    result = CliRunner().invoke(app, ["ingest", str(repo), "-o", str(db), *flags])
    assert result.exit_code == 0, result.output
    return " ".join(result.output.split()).lower()


def _edit_py(repo: Path) -> None:
    with (repo / "mod.py").open("a", encoding="utf-8") as fh:
        fh.write('\n\ndef load(doc):\n    return doc["picture"]\n')
    os.utime(repo / "mod.py", (_PAST + 50, _PAST + 50))


# --- the option itself ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("explicit", "env", "recorded", "expected"),
    [
        (None, None, None, False),
        (None, "1", None, True),
        (None, "0", True, True),
        (None, "1", False, False),
        (None, None, True, True),
        (False, "1", True, False),
        (True, None, False, True),
    ],
)
def test_the_mode_is_explicit_then_recorded_then_env_then_off(
    monkeypatch: pytest.MonkeyPatch,
    explicit: bool | None,
    env: str | None,
    recorded: bool | None,
    expected: bool,
) -> None:
    if env is not None:
        monkeypatch.setenv(DATA_KEYS_ENV, env)
    assert data_keys_enabled(explicit, recorded) is expected


def test_the_python_extractor_emits_no_keys_by_default() -> None:
    nodes, edges = PythonExtractor().parse(MOD, "mod.py")
    assert not [e for e in edges if e.type in KEY_EDGE_TYPES]
    assert "str_constants" not in nodes[0].metadata


def test_the_python_extractor_emits_keys_when_asked() -> None:
    nodes, edges = PythonExtractor(data_keys=True).parse(MOD, "mod.py")
    assert [e for e in edges if e.type in KEY_EDGE_TYPES]
    assert nodes[0].metadata["str_constants"] == {"INDEX": "vehicles"}


def test_the_registry_reads_no_data_file_by_default() -> None:
    assert ".json" not in extractors([])
    assert ".yaml" not in set(extractors([]))
    assert ".json" in extractors([], data_keys=True)
    assert not is_supported("a/mapping.yml", data_keys=False)
    assert is_supported("a/mapping.yml", data_keys=True)


# --- ingest ----------------------------------------------------------------------------


def test_a_default_ingest_has_no_key_nodes_and_reads_no_json(tmp_path: Path) -> None:
    nodes, _raw, edges = IngestionPipeline(extractors([])).run(str(_repo(tmp_path)))
    assert not [n for n in nodes if n.type is NodeType.DATA_KEY]
    assert not [e for e in edges if e.type in KEY_EDGE_TYPES]
    assert {n.file_path for n in nodes if n.type is NodeType.FILE} == {"mod.py"}


def test_editing_json_leaves_a_default_graph_fresh(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    db = tmp_path / "g.db"
    _ingest(repo, db)
    (repo / "mapping.json").write_text(MAPPING.replace("cover", "picture"), encoding="utf-8")
    with SQLiteStore(str(db)) as store:
        assert store.freshness().state is FreshnessState.FRESH


def test_the_flag_turns_keys_on_and_is_recorded(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    db = tmp_path / "g.db"
    _ingest(repo, db, "--data-keys")
    keys, files = _keys(db)
    assert "key:|cover" in keys
    assert "mapping.json" in files
    with SQLiteStore(str(db)) as store:
        assert store.get_ingest_data_keys() is True


def test_a_default_ingest_records_keys_off(tmp_path: Path) -> None:
    db = tmp_path / "g.db"
    _ingest(_repo(tmp_path), db)
    assert _keys(db)[0] == set()
    with SQLiteStore(str(db)) as store:
        assert store.get_ingest_data_keys() is False


def test_the_env_var_turns_keys_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATA_KEYS_ENV, "1")
    db = tmp_path / "g.db"
    _ingest(_repo(tmp_path), db)
    assert "key:|cover" in _keys(db)[0]


def test_an_incremental_run_keeps_the_recorded_mode(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    db = tmp_path / "g.db"
    _ingest(repo, db, "--data-keys")
    _edit_py(repo)
    _ingest(repo, db, "-i")
    assert {"key:|cover", "key:|picture"} <= _keys(db)[0]


def test_auto_refresh_keeps_the_recorded_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    db = tmp_path / "g.db"
    _ingest(repo, db, "-i", "--data-keys")
    monkeypatch.setenv(AUTO_REFRESH_ENV, "1")
    _edit_py(repo)
    assert refresh_if_stale(str(db)) is True
    assert {"key:|cover", "key:|picture"} <= _keys(db)[0]


def test_switching_keys_on_rebuilds_an_unchanged_graph(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    db = tmp_path / "g.db"
    _ingest(repo, db, "-i")
    _ingest(repo, db, "-i", "--data-keys")  # no file changed: a no-op would keep no keys
    keys, files = _keys(db)
    assert "key:|cover" in keys
    assert "mapping.json" in files


def test_switching_keys_off_rebuilds_an_unchanged_graph(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    db = tmp_path / "g.db"
    _ingest(repo, db, "-i", "--data-keys")
    _ingest(repo, db, "-i", "--no-data-keys")
    keys, files = _keys(db)
    assert keys == set()
    assert "mapping.json" not in files


def test_the_mcp_ingest_is_off_by_default_and_takes_the_option(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    off, on = tmp_path / "off.db", tmp_path / "on.db"
    assert cgis_ingest(str(repo), str(off)).startswith("✅")
    assert cgis_ingest(str(repo), str(on), data_keys=True).startswith("✅")
    assert _keys(off)[0] == set()
    assert "key:|cover" in _keys(on)[0]
    with SQLiteStore(str(on)) as store:
        assert store.get_ingest_data_keys() is True


# --- an existing graph keeps its mode; the output says which -------------------------

_SWITCHED_ON = "rebuilt in full: data keys switched on"


@pytest.mark.parametrize(("built", "env"), [("--no-data-keys", "1"), ("--data-keys", "0")])
def test_the_env_var_does_not_change_an_existing_graph_from_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, built: str, env: str
) -> None:
    repo = _repo(tmp_path)
    db = tmp_path / "g.db"
    _ingest(repo, db, built)
    before = _keys(db)
    monkeypatch.setenv(DATA_KEYS_ENV, env)
    _edit_py(repo)
    output = _ingest(repo, db, "-i")
    assert bool(_keys(db)[0]) is bool(before[0])
    assert "data keys" in output
    assert "switched" not in output


@pytest.mark.parametrize(("on", "env"), [(False, "1"), (True, "0")])
def test_the_env_var_does_not_change_an_existing_graph_from_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on: bool, env: str
) -> None:
    repo = _repo(tmp_path)
    db = tmp_path / "g.db"
    cgis_ingest(str(repo), str(db), data_keys=on)
    monkeypatch.setenv(DATA_KEYS_ENV, env)
    _edit_py(repo)
    reply = cgis_ingest(str(repo), str(db)).lower()
    assert bool(_keys(db)[0]) is on
    assert f"data keys: {'on' if on else 'off'}" in reply
    assert "switched" not in reply


def test_the_env_var_applies_to_a_new_store_from_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DATA_KEYS_ENV, "1")
    db = tmp_path / "g.db"
    reply = cgis_ingest(str(_repo(tmp_path)), str(db)).lower()
    assert "key:|cover" in _keys(db)[0]
    assert "data keys: on" in reply


def test_an_explicit_flag_switches_and_the_cli_says_so(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    db = tmp_path / "g.db"
    first = _ingest(repo, db, "-i")
    assert "data keys: off" in first
    output = _ingest(repo, db, "-i", "--data-keys")
    assert _SWITCHED_ON in output
    assert "data keys: on" in output
    assert "full rebuild" in output


def test_an_explicit_param_switches_and_mcp_says_so(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    db = tmp_path / "g.db"
    assert "data keys: off" in cgis_ingest(str(repo), str(db)).lower()
    reply = cgis_ingest(str(repo), str(db), data_keys=True).lower()
    assert _SWITCHED_ON in reply
    assert "(mode: full rebuild)" in reply
    assert "data keys: on" in reply
    assert "no files changed" not in reply
