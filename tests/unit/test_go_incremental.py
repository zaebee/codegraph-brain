"""An incremental Go ingest re-resolves what another file's types changed (#608).

A call through a field or a call's result resolves against metadata of another
file — a struct's `self_types`, a function's `returns` — so the Go manifest
declares them cross-file; an edit to one must leave the incremental graph equal
to a full ingest.
"""

from pathlib import Path

import pytest

from cgis.core.models import VIRTUAL_FILE_PATH
from cgis.languages import extractors
from cgis.pipeline import IngestionPipeline
from cgis.storage.sqlite_store import SQLiteStore

_GO_MOD = "module example.com/app\n"
_TYPES = (
    "package a\n"
    "type Fast struct{}\nfunc (f *Fast) Get() {}\n"
    "type Slow struct{}\nfunc (s *Slow) Get() {}\n"
)
_USE = (
    "package b\n"
    'import "example.com/app/a"\n'
    "type User struct{ c *a.Fast }\n"
    "func (u *User) Field() { u.c.Get() }\n"
    "func Result() { x := a.New(); x.Get() }\n"
)

SCENARIOS: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "re-type a result another file calls through": (
        {
            "go.mod": _GO_MOD,
            "a/types.go": _TYPES,
            "a/new.go": "package a\nfunc New() *Fast { return nil }\n",
            "b/b.go": _USE,
        },
        {"a/new.go": "package a\nfunc New() *Slow { return nil }\n"},
    ),
    "re-type a field another file calls through": (
        {
            "go.mod": _GO_MOD,
            "a/types.go": _TYPES,
            "a/holder.go": "package a\ntype Holder struct{ C *Fast }\n",
            "a/use.go": "package a\nfunc (h *Holder) Use() { h.C.Get() }\n",
        },
        {"a/holder.go": "package a\ntype Holder struct{ C *Slow }\n"},
    ),
}

#: The edge each scenario's edit must produce, so a scenario that stops exercising
#: its metadata fails rather than passes on an empty difference.
_EXPECTED = {
    "re-type a result another file calls through": ("b.Result", "a.Slow.Get", "CALLS"),
    "re-type a field another file calls through": ("a.Holder.Use", "a.Slow.Get", "CALLS"),
}


def _graph(db: str) -> tuple[set[tuple[str, str, str]], set[str]]:
    with SQLiteStore(db) as store:
        edges = {(e.source, e.target, str(e.type)) for e in store.get_all_edges()}
        real = {n.id for n in store.get_all_nodes() if n.file_path != VIRTUAL_FILE_PATH}
    return edges, real


def _ingest(root: Path, db: str) -> None:
    with SQLiteStore(db) as store:
        IngestionPipeline(extractors([])).run(str(root), store=store)


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content, encoding="utf-8")


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_incremental_go_graph_equals_full_ingest(scenario: str, tmp_path: Path) -> None:
    initial, edit = SCENARIOS[scenario]
    work = tmp_path / "work"
    _write(work, initial)
    incremental = str(tmp_path / "incremental.db")
    _ingest(work, incremental)
    _write(work, edit)
    _ingest(work, incremental)
    full = str(tmp_path / "full.db")
    _ingest(work, full)
    inc_edges, inc_nodes = _graph(incremental)
    full_edges, full_nodes = _graph(full)
    assert _EXPECTED[scenario] in full_edges, "the scenario no longer exercises #608"
    assert inc_nodes == full_nodes
    assert inc_edges - full_edges == set(), "stale edges survived the incremental run"
    assert full_edges - inc_edges == set(), "edges a full ingest has are missing"
