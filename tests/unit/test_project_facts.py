"""Per-pack project readers and the collector that merges their facts (language-packs step 2)."""

import json
from pathlib import Path

import pytest

from cgis.import_names import ImportNameCollector, ImportNames
from cgis.languages import extractors, project_readers
from cgis.languages.python.dependencies import declared_import_roots
from cgis.languages.python.project import PythonFacts, PythonProjectReader
from cgis.languages.typescript.project import TypeScriptFacts, TypeScriptProjectReader
from cgis.storage.sqlite_store import SQLiteStore

_STORE_KEYS = {"workspace_packages", "tsconfig_paths", "python_dependencies", "npm_dependencies"}


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["PyYAML>=6", "requests"]\n', encoding="utf-8"
    )
    (root / "package.json").write_text(
        json.dumps({"name": "mono", "dependencies": {"react": "18"}}), encoding="utf-8"
    )
    (root / "packages" / "ui").mkdir(parents=True)
    (root / "packages" / "ui" / "package.json").write_text(
        json.dumps({"name": "@acme/ui"}), encoding="utf-8"
    )
    return root


def test_every_pack_contributes_a_reader_in_manifest_order(tmp_path: Path) -> None:
    readers = project_readers(tmp_path, extractors([]))
    assert [type(r) for r in readers] == [PythonProjectReader, TypeScriptProjectReader]


def test_the_packs_own_exactly_the_store_keys() -> None:
    """Ruling S1: facts are keyed by the ingest_state key the store has always used."""
    python = set(PythonFacts([]).state())
    typescript = set(TypeScriptFacts({}, {}, []).state())
    assert python | typescript == _STORE_KEYS
    assert not python & typescript


def test_python_facts_are_the_declared_roots(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    reader = PythonProjectReader(root)
    assert reader.note(root / "pyproject.toml") is False  # read at the root, not from the walk
    assert reader.facts().state() == {"python_dependencies": sorted(declared_import_roots(root))}
    assert reader.facts().state()["python_dependencies"] == ["requests", "yaml"]


def test_the_typescript_reader_takes_only_its_files(tmp_path: Path) -> None:
    reader = TypeScriptProjectReader(tmp_path, extractors([]))
    assert reader.note(tmp_path / "package.json") is True
    assert reader.note(tmp_path / "tsconfig.json") is True
    assert reader.note(tmp_path / "pyproject.toml") is False


def test_two_packs_claiming_one_key_fail() -> None:
    """Last-writer-wins would silently drop one pack's facts."""
    facts = [PythonFacts(["a"]), PythonFacts(["b"])]
    with pytest.raises(ValueError, match="python_dependencies"):
        ImportNames.from_facts(facts)


def test_a_missing_pack_reads_as_no_facts() -> None:
    names = ImportNames.from_facts([PythonFacts(["yaml"])])
    assert names == ImportNames(
        workspace_packages={}, path_aliases={}, python_dependencies=["yaml"], npm_dependencies=[]
    )


def test_collected_facts_round_trip_through_the_store(tmp_path: Path) -> None:
    """Recorded under the old keys, and an unchanged project asks for no rebuild."""
    root = _repo(tmp_path)
    collector = ImportNameCollector(root, extractors([]))
    for path in sorted(root.rglob("package.json")):
        collector.note(path)
    names = collector.collect()
    assert names.python_dependencies == ["requests", "yaml"]
    assert names.npm_dependencies == ["react"]
    assert names.workspace_packages == {"@acme.ui": "packages.ui"}
    with SQLiteStore(str(tmp_path / "g.db")) as store:
        names.record(store)
        assert names.differ_from(store) is False
        keys = {row[0] for row in store._conn.execute("SELECT key FROM ingest_state")}  # noqa: SLF001
    assert keys >= _STORE_KEYS


class _Facts:
    """A pack's facts as a new pack might write them."""

    def __init__(self, state: dict[str, object]) -> None:
        self._state = state

    def state(self) -> dict[str, object]:
        return self._state


def test_a_key_the_collector_cannot_store_fails() -> None:
    """Accepted and dropped, a new pack's facts would never be stored, compared or resolved."""
    facts = [_Facts({"go_modules": ["example.com/x"]})]
    with pytest.raises(ValueError, match="go_modules"):
        ImportNames.from_facts(facts)


def test_a_fact_of_the_wrong_shape_fails() -> None:
    """A tuple never equals the list read back from JSON: every incremental run would rebuild."""
    facts = [_Facts({"npm_dependencies": ("react",)})]
    with pytest.raises(TypeError, match="npm_dependencies"):
        ImportNames.from_facts(facts)
