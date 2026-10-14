"""The Python/TS graph a fixed corpus produces must not change (language-packs spec, Migration).

Regenerate only in a step meant to change behaviour, with the reason in the
commit message:

    uv run python scripts/graph_identity.py snapshot tests/fixtures/graph_identity/corpus \
        --out tests/fixtures/graph_identity/snapshot.json
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import graph_identity as gi  # noqa: E402

CORPUS = REPO / "tests" / "fixtures" / "graph_identity" / "corpus"
SNAPSHOT = REPO / "tests" / "fixtures" / "graph_identity" / "snapshot.json"


def _snapshot(seed: str, out: Path) -> dict[str, dict[str, object]]:
    """The corpus snapshot, taken in a subprocess under one hash seed.

    Read from `--out`, not stdout: the pipeline logs to stdout through structlog.
    """
    env = {**os.environ, "PYTHONHASHSEED": seed}
    script = str(REPO / "scripts" / "graph_identity.py")
    subprocess.run(
        [sys.executable, script, "snapshot", str(CORPUS), "--out", str(out)],
        env=env,
        capture_output=True,
        check=True,
    )
    snapshot: dict[str, dict[str, object]] = json.loads(out.read_text(encoding="utf-8"))
    return snapshot


@pytest.mark.parametrize("seed", ["0", "1"])
def test_the_corpus_graph_matches_the_snapshot(seed: str, tmp_path: Path) -> None:
    """Same input, same graph — whatever the hash seed, the machine or the temp directory."""
    recorded = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    actual = _snapshot(seed, tmp_path / "snapshot.json")
    assert set(actual) == set(recorded) == set(gi.SNAPSHOT_ROOTS)
    for root in gi.SNAPSHOT_ROOTS:
        lines = gi.diff(recorded[root], actual[root])
        assert not lines, f"{root} graph changed (PYTHONHASHSEED={seed}):\n" + "\n".join(lines[:40])


def test_every_corpus_file_is_tracked_by_git() -> None:
    """A corpus file git ignores exists only on the machine that made the snapshot.

    `.gitignore`'s `lib/` swallowed three corpus files: the snapshot was taken with
    them and every fresh clone ingested without them (step-0 review).
    """
    files = [
        str(p.relative_to(REPO))
        for p in CORPUS.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    ]
    ignored = subprocess.run(
        ["git", "check-ignore", *files], cwd=REPO, capture_output=True, text=True, check=False
    ).stdout.split()
    assert not ignored, f"git ignores corpus files: {ignored}"


def test_the_snapshot_resolves_a_star_import_chain() -> None:
    """`from shop import place` reaches `shop.service.place` through `shop/__init__`'s star import.

    In the `whole` root the src layout is not stripped, so the chain resolves only
    under `py_src`; it must be in the snapshot, since steps 3-4 move that code.
    """
    recorded = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    calls = {
        (e["source"], e["target"]) for e in recorded["py_src"]["edges"] if e["type"] == "CALLS"
    }
    assert ("shop.cli.run", "shop.service.place") in calls


def test_the_snapshot_command_runs_in_process(tmp_path: Path) -> None:
    """The same snapshot through `main`, so coverage sees the script (`--cov=scripts`)."""
    out = tmp_path / "snapshot.json"
    assert gi.main(["snapshot", str(CORPUS), "--out", str(out)]) == 0
    recorded = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    actual = json.loads(out.read_text(encoding="utf-8"))
    assert all(gi.diff(recorded[r], actual[r]) == [] for r in gi.SNAPSHOT_ROOTS)
