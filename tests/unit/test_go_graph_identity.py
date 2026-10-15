"""The Go graph a fixed corpus produces (language-packs step 7b, Task B5).

Its own corpus and snapshot, beside the Python/TS one, which the Go pack must
leave untouched. Regenerate only in a step meant to change Go behaviour:

    uv run python scripts/graph_identity.py snapshot tests/fixtures/graph_identity_go/corpus \
        --root go=. --out tests/fixtures/graph_identity_go/snapshot.json
"""

import json
import os
import subprocess
import sys
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import graph_identity as gi  # noqa: E402

CORPUS = REPO / "tests" / "fixtures" / "graph_identity_go" / "corpus"
SNAPSHOT = REPO / "tests" / "fixtures" / "graph_identity_go" / "snapshot.json"
ROOTS = {"go": "."}
_ONTOLOGY = REPO / "docs" / "ontology" / "core.yaml"

Row = dict[str, Any]


def _snapshot(seed: str, out: Path) -> dict[str, dict[str, Any]]:
    """The corpus snapshot, taken in a subprocess under one hash seed."""
    env = {**os.environ, "PYTHONHASHSEED": seed}
    script = str(REPO / "scripts" / "graph_identity.py")
    result = subprocess.run(
        [sys.executable, script, "snapshot", str(CORPUS), "--root", "go=.", "--out", str(out)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"snapshot failed (PYTHONHASHSEED={seed}):\n{result.stderr}"
    snapshot: dict[str, dict[str, Any]] = json.loads(out.read_text(encoding="utf-8"))
    return snapshot


@cache
def _recorded() -> dict[str, Any]:
    recorded: dict[str, Any] = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["go"]
    return recorded


@pytest.mark.parametrize("seed", ["0", "1"])
def test_the_go_corpus_graph_matches_the_snapshot(seed: str, tmp_path: Path) -> None:
    recorded = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    actual = _snapshot(seed, tmp_path / "snapshot.json")
    assert set(actual) == set(recorded) == set(ROOTS)
    lines = gi.diff(recorded["go"], actual["go"])
    assert not lines, f"go graph changed (PYTHONHASHSEED={seed}):\n" + "\n".join(lines[:40])


def test_the_snapshot_command_takes_roots_in_process(tmp_path: Path) -> None:
    """The same snapshot through `main`, so coverage sees `--root` (`--cov=scripts`)."""
    out = tmp_path / "snapshot.json"
    assert gi.main(["snapshot", str(CORPUS), "--root", "go=.", "--out", str(out)]) == 0
    assert gi.diff(_recorded(), json.loads(out.read_text(encoding="utf-8"))["go"]) == []


def test_every_go_corpus_file_is_tracked_by_git() -> None:
    files = [str(p.relative_to(REPO)) for p in CORPUS.rglob("*") if p.is_file()]
    ignored = subprocess.run(
        ["git", "check-ignore", *files], cwd=REPO, capture_output=True, text=True, check=False
    ).stdout.split()
    assert not ignored, f"git ignores corpus files: {ignored}"


def _ontology_types(section: str) -> set[str]:
    ontology = yaml.safe_load(_ONTOLOGY.read_text(encoding="utf-8"))
    return {value for values in ontology[section].values() for value in values}


def test_go_corpus_uses_only_the_ontology() -> None:
    """Spec acceptance: Go emits the existing enums, and an unresolved call stays `raw_call:`."""
    nodes: list[Row] = _recorded()["nodes"]
    edges: list[Row] = _recorded()["edges"]
    assert {n["type"] for n in nodes} <= _ontology_types("node_types")
    assert {e["type"] for e in edges} <= _ontology_types("edge_types")
    ids = {n["id"] for n in nodes}
    dangling = [
        e["target"]
        for e in edges
        if e["type"] == "CALLS"
        and e["target"] not in ids
        and not e["target"].startswith("raw_call:")
    ]
    assert not dangling, f"CALLS targets with no node and no raw_call: prefix: {dangling}"


def _namespace(node_id: str) -> str:
    return next(n["namespace"] for n in _recorded()["nodes"] if n["id"] == node_id)


def _calls() -> set[tuple[str, str]]:
    return {(e["source"], e["target"]) for e in _recorded()["edges"] if e["type"] == "CALLS"}


def test_the_snapshot_holds_what_the_pilot_promises() -> None:
    """Reviewed by hand when recorded; pinned so a re-record cannot quietly lose it."""
    calls = _calls()
    # Cross-file methods hang under one type; receiver calls reach own and embedded methods.
    assert ("internal.server.Server.Start", "internal.server.Server.Serve") in calls
    assert ("internal.server.Server.Start", "internal.server.Logger.Log") in calls
    # The same-package tiebreak picks this package's `validate`, not the store's.
    assert ("internal.server.Server.Start", "internal.server.validate") in calls
    assert ("internal.store.Store.Save", "internal.store.validate") in calls
    # Package-qualified calls through the module path and an internal replace.
    assert ("main.main", "internal.server.New") in calls
    assert ("main.main", "money.Format") in calls
    # Classification: cgo and the stdlib are STDLIB, the required module EXTERNAL.
    assert _namespace("C.malloc") == "STDLIB"
    assert _namespace("fmt.Println") == "STDLIB"
    assert _namespace("github.com.google.uuid.NewString") == "EXTERNAL"


def test_the_test_file_is_flagged_and_the_rest_is_not() -> None:
    flags = {n["id"]: n["is_test"] for n in _recorded()["nodes"] if n.get("file_path")}
    assert flags["internal.server.TestStart"]
    assert not flags["internal.server.Server.Start"]


@pytest.mark.parametrize("root", ["go", "=."])
def test_a_root_without_a_name_and_a_path_is_refused(root: str, tmp_path: Path) -> None:
    """`--root go` used to read as `go=` — the whole corpus — and say nothing (delta review)."""
    argv = ["snapshot", str(CORPUS), "--root", root, "--out", str(tmp_path / "s.json")]
    with pytest.raises(SystemExit):
        gi.main(argv)
    assert not (tmp_path / "s.json").exists()
