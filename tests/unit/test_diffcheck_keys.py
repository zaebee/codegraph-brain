"""The diff check lists the other sites of the data keys a git range touched."""

import json
import subprocess
from pathlib import Path

from cgis.languages import extractors
from cgis.pipeline import IngestionPipeline
from cgis.query.diffcheck import DiffItem, DiffReport, run_diff_check
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


def _check(tmp_path: Path, before: dict[str, str], after: dict[str, str | None]) -> DiffReport:
    """Commit `before`, then `after`, ingest HEAD and run the check over the two."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    base = _commit(repo, dict(before), "base")
    _commit(repo, after, "edit")
    store = SQLiteStore(str(tmp_path / "g.db"))
    store.connect()
    IngestionPipeline(extractors([])).run(str(repo), store=store, rebuild=True)
    with store:
        return run_diff_check(store, repo, base, cap=100)


def _of(report: DiffReport, kind: str) -> list[DiffItem]:
    return [i for i in report.items if i.kind == kind]


def _mapping(properties: dict[str, object]) -> str:
    return json.dumps({"mappings": {"properties": properties}}, indent=2) + "\n"


CREATE = "def create_vehicle(doc):\n    doc['picture'] = {'cover': ''}\n    return doc\n"
FINANCE = (
    "def create_transaction(data_source):\n"
    "    data_source['picture'].pop('extra_previews', None)\n"
    "    return data_source\n"
)


def test_a_changed_write_lists_other_readers(tmp_path: Path) -> None:
    report = _check(
        tmp_path,
        {"vehicle.py": CREATE, "finance.py": FINANCE},
        {"vehicle.py": CREATE.replace("{'cover': ''}", "None")},
    )
    assert "key:|picture" in report.changed_keys
    readers = _of(report, "key-reader")
    assert [(i.site, i.file, i.line) for i in readers] == [
        ("finance.create_transaction", "finance.py", 2)
    ]
    assert readers[0].changed == "key:|picture"
    assert readers[0].key == "key:|picture.extra_previews"
    assert "picture" in readers[0].reason


QUERY = (
    "def count_events(client, kind):\n"
    "    body = {'query': {'term': {'event_type.keyword': kind}}}\n"
    "    return client.search(body=body)\n"
)


def test_undeclared_read_is_reported(tmp_path: Path) -> None:
    events = {"event_type": {"type": "keyword"}, "details": {"type": "object"}}
    labelled = {
        "event_type": {"type": "keyword"},
        "details": {"type": "object", "properties": {"currency": {"type": "keyword"}}},
    }
    report = _check(
        tmp_path,
        {"opensearch/mappings/events.json": _mapping(events), "admin/query.py": QUERY},
        {"opensearch/mappings/events.json": _mapping(labelled)},
    )
    (item,) = _of(report, "undeclared-read")
    assert (item.site, item.file, item.line) == ("admin.query.count_events", "admin/query.py", 2)
    assert item.key == "key:|event_type.keyword"
    assert "events.json" in item.reason


def test_a_declared_subfield_is_not_reported(tmp_path: Path) -> None:
    events = {"event_type": {"type": "text", "fields": {"keyword": {"type": "keyword"}}}}
    report = _check(
        tmp_path,
        {"opensearch/mappings/events.json": _mapping(events), "admin/query.py": QUERY},
        {"opensearch/mappings/events.json": _mapping({**events, "status": {"type": "keyword"}})},
    )
    assert _of(report, "undeclared-read") == []


READ_BRAND = "def brands(doc):\n    return doc['details']['brand']\n"


def test_removed_declaration_still_read_is_reported(tmp_path: Path) -> None:
    before = {"details": {"properties": {"brand": {"type": "keyword"}, "model": {"type": "text"}}}}
    after = {"details": {"properties": {"model": {"type": "text"}}}}
    report = _check(
        tmp_path,
        {"opensearch/mappings/vehicles.json": _mapping(before), "brands.py": READ_BRAND},
        {"opensearch/mappings/vehicles.json": _mapping(after)},
    )
    (item,) = _of(report, "removed-declaration")
    assert (item.site, item.file, item.line) == ("brands.brands", "brands.py", 2)
    assert item.changed == "key:vehicles|details.brand"
    # The same read is not listed a second time as merely undeclared.
    assert _of(report, "undeclared-read") == []


def test_common_unscoped_keys_are_not_listed(tmp_path: Path) -> None:
    writer = "def save(d):\n    d['name'] = 1\n    d['color'] = 1\n"
    readers = {f"r{n}.py": f"def read{n}(d):\n    return d['color']\n" for n in range(9)}
    report = _check(
        tmp_path,
        {"w.py": writer, "only.py": "def show(d):\n    return d['name']\n", **readers},
        {"w.py": writer.replace("= 1", "= 2")},
    )
    # `name` is on the common list; `color` is read from more than eight files.
    assert report.items == []
    assert {"key:|name", "key:|color"} <= set(report.changed_keys)


def _search(index: str, value: str) -> str:
    return (
        f"def find(client):\n"
        f"    return client.search(index='{index}', "
        f"body={{'query': {{'term': {{'details.brand': {value}}}}}}})\n"
    )


def test_scoped_keys_in_other_namespaces_do_not_match(tmp_path: Path) -> None:
    report = _check(
        tmp_path,
        {
            "a.py": _search("events", "1"),
            "same.py": _search("events", "1"),
            "other.py": _search("vehicles", "1"),
        },
        {"a.py": _search("events", "2")},
    )
    assert "key:events|details.brand" in report.changed_keys
    assert [(i.kind, i.site) for i in report.items] == [("key-reader", "same.find")]


def test_specific_keys_rank_above_common_ones(tmp_path: Path) -> None:
    writer = "def save(d):\n    d['status'] = 1\n    d['details']['brand'] = 1\n"
    status = {f"s{n}.py": f"def s{n}(d):\n    return d['status']\n" for n in range(3)}
    report = _check(
        tmp_path,
        {"w.py": writer, "b.py": READ_BRAND, **status},
        {"w.py": writer.replace("= 1", "= 2")},
    )
    assert report.items[0].site == "b.brands"
    assert [i.score for i in report.items] == sorted((i.score for i in report.items), reverse=True)


SEARCH_NAMED = (
    "def find(client):\n"
    "    return client.search(index=EVENTS, body={'query': {'term': {'details.brand': 1}}})\n"
)


def test_a_removed_read_takes_its_namespace_from_a_constant(tmp_path: Path) -> None:
    own = "EVENTS = 'events'\n\n\n" + SEARCH_NAMED
    imported = "from consts import EVENTS\n\n\n" + SEARCH_NAMED
    report = _check(
        tmp_path,
        {
            "own.py": own,
            "imported.py": imported,
            "consts.py": "EVENTS = 'events'\n",
            "same.py": _search("events", "1"),
            "other.py": _search("vehicles", "1"),
        },
        {
            "own.py": "EVENTS = 'events'\n",
            "imported.py": "from consts import EVENTS\n",
        },
    )
    # Both reads exist only on the base side; each resolves `EVENTS` as the resolver would.
    assert "key:events|details.brand" in report.changed_keys
    assert "key:|details.brand" not in report.changed_keys
    assert [(i.kind, i.site) for i in report.items] == [("key-reader", "same.find")]


def test_a_changed_model_field_does_not_join_a_common_unscoped_key(tmp_path: Path) -> None:
    model = (
        "from pydantic import BaseModel\n\n\nclass Car(BaseModel):\n    name: str\n    brand: str\n"
    )
    report = _check(
        tmp_path,
        {
            "car.py": model,
            "names.py": "def label(d):\n    return d['name']\n",
            "brands.py": "def brand(d):\n    return d['brand']\n",
        },
        {"car.py": model.replace(": str", ": str | None")},
    )
    assert {"key:car.Car|name", "key:car.Car|brand"} <= set(report.changed_keys)
    assert [(i.kind, i.site) for i in report.items] == [("key-reader", "brands.brand")]


def test_a_model_field_does_not_make_a_nested_read_undeclared(tmp_path: Path) -> None:
    model = "from pydantic import BaseModel\n\n\nclass Event(BaseModel):\n    details: dict\n"
    report = _check(
        tmp_path,
        {"event.py": model, "brands.py": READ_BRAND},
        {"brands.py": READ_BRAND.replace("doc", "event")},
    )
    assert "key:|details.brand" in report.changed_keys
    assert _of(report, "undeclared-read") == []


def test_a_key_changed_only_in_tests_lists_nothing(tmp_path: Path) -> None:
    test = "def test_brand():\n    doc = {}\n    doc['details']['brand'] = 1\n"
    report = _check(
        tmp_path,
        {"tests/test_brand.py": test, "brands.py": READ_BRAND},
        {"tests/test_brand.py": test.replace("= 1", "= 2")},
    )
    assert "key:|details.brand" in report.changed_keys
    assert report.items == []
