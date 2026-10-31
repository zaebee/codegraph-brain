"""Per-pack project readers and the collector that merges their facts (language-packs step 2)."""

import dataclasses
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

import cgis.resolver.indices as indices
from cgis.core.models import Node
from cgis.core.project import resolves_inside
from cgis.import_names import ImportNameCollector, ImportNames
from cgis.languages import extractors, project_readers
from cgis.languages.data import DataProjectReader
from cgis.languages.go.project import GoProjectReader
from cgis.languages.python.dependencies import declared_import_roots
from cgis.languages.python.project import PythonFacts, PythonProjectReader
from cgis.languages.typescript.project import TypeScriptFacts, TypeScriptProjectReader
from cgis.pipeline import IngestionPipeline
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
    assert [type(r) for r in readers] == [
        PythonProjectReader,
        TypeScriptProjectReader,
        GoProjectReader,
        DataProjectReader,
    ]


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


def test_the_typescript_reader_claims_a_config_outside_the_root_but_does_not_read_it(
    tmp_path: Path,
) -> None:
    """A PR-committed symlink must not make the ingest read a file the checkout lacks (#732)."""
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps({"name": "@evil/ui", "dependencies": {"evil-dep": "^1"}}), encoding="utf-8"
    )
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").symlink_to(outside)

    reader = TypeScriptProjectReader(root, extractors([]))
    assert reader.note(root / "package.json") is True  # our config, just not a readable one
    facts = reader.facts()
    assert facts.npm_dependencies == []
    assert facts.workspace_packages == {}


def test_containment_is_decided_on_the_resolved_path(tmp_path: Path) -> None:
    """The pipeline's rule (#731) as a helper: a link pointing out of the root is not read."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "inside.json").write_text("{}", encoding="utf-8")
    (tmp_path / "outside.json").write_text("{}", encoding="utf-8")
    (root / "link-inside.json").symlink_to(root / "inside.json")
    (root / "link-outside.json").symlink_to(tmp_path / "outside.json")

    assert resolves_inside(root / "inside.json", root) is True
    assert resolves_inside(root / "link-inside.json", root) is True
    assert resolves_inside(root / "link-outside.json", root) is False


def test_the_walk_reads_no_config_that_resolves_outside_the_root(tmp_path: Path) -> None:
    """All three config sources at once: `package.json`, `tsconfig.json` and `go.mod` (#732).

    `IngestionPipeline` notes every walked file before it checks containment, so the
    readers are the gate: one walk, three links out, and no fact from any of them.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "package.json").write_text(
        json.dumps({"name": "@evil/ui", "dependencies": {"evil-dep": "^1"}}), encoding="utf-8"
    )
    (outside / "tsconfig.json").write_text(
        json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@evil/*": ["src/*"]}}}),
        encoding="utf-8",
    )
    (outside / "go.mod").write_text("module evil.example/app\n", encoding="utf-8")
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    configs = ("package.json", "tsconfig.json", "go.mod")
    for name in configs:
        (root / name).symlink_to(outside / name)

    collector = ImportNameCollector(root, extractors([]))
    for name in configs:
        collector.note(root / name)
    names = collector.collect()

    assert names.npm_dependencies == []
    assert names.workspace_packages == {}
    assert names.path_aliases == {}
    assert names.extra == {}  # no go_module: the module was never read


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


def test_a_pack_owned_key_is_carried_recorded_and_compared(tmp_path: Path) -> None:
    """Step 7 H3: a key the four built-ins do not cover travels as `extra`.

    Replaces step 2's `test_a_key_the_collector_cannot_store_fails`: that refusal
    stood in for store and resolver support, which `extra` now provides.
    """
    names = ImportNames.from_facts([_Facts({"go_module": {"path": "example.com/app"}})])
    assert names.extra == {"go_module": {"path": "example.com/app"}}
    with SQLiteStore(str(tmp_path / "g.db")) as store:
        names.record(store)
        assert names.differ_from(store) is False
        changed = ImportNames.from_facts([_Facts({"go_module": {"path": "example.com/other"}})])
        assert changed.differ_from(store) is True


def test_a_pack_owned_value_that_would_not_read_back_equal_fails() -> None:
    """A tuple stores as a JSON list: it would differ from itself and rebuild every run."""
    facts = [_Facts({"go_module": ("example.com/app",)})]
    with pytest.raises(TypeError, match="go_module"):
        ImportNames.from_facts(facts)


@pytest.mark.parametrize(
    "key",
    [
        "root",
        "ingested_at",
        "ingest_options",
        "workspace_packages",
        "tsconfig_paths",
        "python_dependencies",
        "npm_dependencies",
    ],
)
def test_the_store_refuses_a_key_with_its_own_recorder(tmp_path: Path, key: str) -> None:
    """The store's own keys drive the freshness probe; the built-ins have typed recorders."""
    with SQLiteStore(str(tmp_path / "g.db")) as store:
        with pytest.raises(ValueError, match=key):
            store.record_state(key, ["x"])
        assert store.get_state(key) is None


def test_a_pack_value_json_cannot_write_names_its_key() -> None:
    """A set has no JSON form: the error says which fact, not just which type (PR #606)."""
    facts = [_Facts({"go_module": {"example.com/app"}})]
    with pytest.raises(TypeError, match="go_module"):
        ImportNames.from_facts(facts)


def test_pack_state_reaches_index_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    """IndexBuilder merges a pack's extra state into what index_facts receives (H3)."""
    seen: dict[str, object] = {}
    real = indices.pack_index_facts

    def recording(nodes: Mapping[str, Node], state: Mapping[str, object]) -> object:
        seen.update(state)
        return real(nodes, state)

    monkeypatch.setattr(indices, "pack_index_facts", recording)
    indices.IndexBuilder().build([], project_state={"go_module": {"path": "example.com/app"}})
    assert seen["go_module"] == {"path": "example.com/app"}
    assert "python_dependencies" in seen  # the built-ins are still there


def test_a_pack_key_that_shadows_a_built_in_fails() -> None:
    builder = indices.IndexBuilder()
    with pytest.raises(ValueError, match="npm_dependencies"):
        builder.build([], project_state={"npm_dependencies": []})


def test_a_fact_of_the_wrong_shape_fails() -> None:
    """A tuple never equals the list read back from JSON: every incremental run would rebuild."""
    facts = [_Facts({"npm_dependencies": ("react",)})]
    with pytest.raises(TypeError, match="npm_dependencies"):
        ImportNames.from_facts(facts)


def test_pack_state_travels_from_the_collector_to_index_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole H3 chain: collector -> pipeline -> ResolverEngine -> SymbolResolver -> index.

    `test_pack_state_reaches_index_facts` calls the builder directly; this one
    catches a link of the chain that stops passing `project_state` on.
    """
    seen: dict[str, object] = {}
    real_facts = indices.pack_index_facts
    real_collect = ImportNameCollector.collect

    def recording(nodes: Mapping[str, Node], state: Mapping[str, object]) -> object:
        seen.update(state)
        return real_facts(nodes, state)

    def with_extra(self: ImportNameCollector) -> ImportNames:
        names = real_collect(self)
        return dataclasses.replace(names, extra={"go_module": {"path": "example.com/app"}})

    monkeypatch.setattr(indices, "pack_index_facts", recording)
    monkeypatch.setattr(ImportNameCollector, "collect", with_extra)
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    IngestionPipeline(extractors([])).run(str(tmp_path))
    assert seen["go_module"] == {"path": "example.com/app"}


def test_a_pack_key_that_vanishes_calls_for_a_rebuild(tmp_path: Path) -> None:
    """Recorded once, then gone (a `go.mod` deleted): resolution against it is stale (M1)."""
    with SQLiteStore(str(tmp_path / "g.db")) as store:
        ImportNames.from_facts([_Facts({"go_module": {"path": "example.com/app"}})]).record(store)
        gone = ImportNames.from_facts([_Facts({})])
        assert gone.differ_from(store) is True


def test_recording_drops_a_pack_key_that_vanished(tmp_path: Path) -> None:
    """Else the stale row would read as a difference on every later run, and rebuild each time."""
    with SQLiteStore(str(tmp_path / "g.db")) as store:
        store.record_ingest(str(tmp_path))
        ImportNames.from_facts([_Facts({"go_module": {"path": "example.com/app"}})]).record(store)
        gone = ImportNames.from_facts([_Facts({})])
        gone.record(store)
        assert store.get_state("go_module") is None
        assert gone.differ_from(store) is False
        recorded = store.get_ingest_state()  # the store's own keys are not pack state
        assert recorded is not None
        assert recorded[0] == str(tmp_path.resolve())
