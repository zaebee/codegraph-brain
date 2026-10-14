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
