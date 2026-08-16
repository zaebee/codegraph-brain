"""A rebuilt finder pass must replay as the run it came from (#246).

The recordings exist so a skeptic variant can be measured without paying the
finder. That only works if a replayed number is the *same run's* number, so this
verifies the tool before anything is measured with it — the alternative is
finding out after the calls have been spent.

Identity is asserted before metrics, at the downstream consumer's suggestion:
comparing scores first lets a green result mean "reproduced *a* run equally
well", which is not the claim.
"""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from cgis.guardian.bench import load_ground_truth, match_findings, score
from cgis.guardian.recording import load_finder_recording

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from recordings_from_corpus import (
    MissingFixtureError,
    NotAFrozenPassError,
    build,
    diff_for,
    is_frozen_pass,
    recording_for,
    row_key,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BENCH_DIR = REPO_ROOT / "benchmarks" / "guardian"
RESULTS = BENCH_DIR / "results.jsonl"


def _rows() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in RESULTS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture(scope="module")
def rebuilt(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Every frozen pass rebuilt once, keyed by the row it came from."""
    out = tmp_path_factory.mktemp("recordings")
    written = build(RESULTS, out, BENCH_DIR, REPO_ROOT)
    return {p.stem: p for p in written}


class TestSelection:
    """Which rows may stand in for an unjudged finder pass."""

    def test_a_judged_row_is_refused(self) -> None:
        """A skeptic rewrote confidence on its `uncertain` findings (#279).

        `load_finder_recording` strips verdicts, so the danger is not smuggled
        judgements — it is that the confidences are no longer the finder's, on
        exactly the subset a skeptic experiment compares.
        """
        judged = next((r for r in _rows() if r.get("skeptic_model") and "matched" in r), None)
        assert judged is not None, "no judged row in the corpus — this test would inspect nothing"
        assert not is_frozen_pass(judged)
        with pytest.raises(NotAFrozenPassError, match="not an unjudged finder pass"):
            recording_for(judged, "diff")

    def test_an_unscored_row_is_not_a_pass(self) -> None:
        """The API-failure rows carry no findings and no score to reproduce."""
        failed = next((r for r in _rows() if "error" in r and "matched" not in r), None)
        assert failed is not None, (
            "no API-failure row in the corpus — this test would inspect nothing"
        )
        assert not is_frozen_pass(failed)

    def test_a_pr_without_a_fixture_is_refused_not_skipped(self, tmp_path: Path) -> None:
        """An invented empty diff would replay as "the finder saw nothing"."""
        with pytest.raises(MissingFixtureError, match="No ground-truth fixture"):
            diff_for(9999, tmp_path, REPO_ROOT)


class TestReplayReproducesTheRunItCameFrom:
    """Identity first, then the numbers."""

    def test_every_frozen_pass_was_rebuilt(self, rebuilt: dict[str, Path]) -> None:
        expected = {row_key(r).replace(":", "-") for r in _rows() if is_frozen_pass(r)}
        assert len(expected) >= 72, (
            f"only {len(expected)} frozen passes found in the corpus, expected at least 72 — "
            "an empty result must not read as 'every pass was rebuilt'."
        )
        assert set(rebuilt) == expected

    def test_each_recording_is_the_run_it_is_named_after(self, rebuilt: dict[str, Path]) -> None:
        """Identity before metrics: same run, same finding count, same order.

        Load-bearing, not belt-and-braces, and measured rather than assumed:
        **10 groups of runs share a PR and identical recall, precision and noise
        while carrying different finding sets, covering 48 of the 72 frozen
        passes.** A metrics-only check would therefore accept the wrong run for
        two thirds of this corpus and report a clean reproduction.

        Verified by mutation — pairing each recording with the next run's
        findings turns this red before the scoring test even runs.
        """
        by_key = {row_key(r).replace(":", "-"): r for r in _rows() if is_frozen_pass(r)}
        for key, path in rebuilt.items():
            row = by_key[key]
            recording = load_finder_recording(path)
            assert len(recording.result.findings) == len(row.get("findings") or []), key
            for rebuilt_f, stored_f in zip(
                recording.result.findings, row.get("findings") or [], strict=True
            ):
                assert (rebuilt_f.file, rebuilt_f.line, rebuilt_f.title) == (
                    stored_f["file"],
                    stored_f["line"],
                    stored_f["title"],
                ), key

    def test_every_recording_rescores_to_its_recorded_numbers(
        self, rebuilt: dict[str, Path]
    ) -> None:
        """72 of 72, under the scoring policy in force when each row was written.

        Seven rows do not reproduce under today's rule, and they are exactly the
        seven carrying `ambiguous_hits`: #345 moved ambiguous hits into the
        precision denominator, and `CURATION.md` states results.jsonl is
        deliberately not rescored for it. So the assertion is structural — a row
        reproduces under the current rule, or it has ambiguous hits and
        reproduces under the old one — rather than a count that a new bench run
        would break.
        """
        by_key = {row_key(r).replace(":", "-"): r for r in _rows() if is_frozen_pass(r)}
        truths: dict[int, Any] = {}
        offenders: list[str] = []
        old_policy: set[str] = set()
        for key, path in rebuilt.items():
            row = by_key[key]
            pr = row["pr"]
            truths.setdefault(pr, load_ground_truth(BENCH_DIR / f"pr-{pr}.yaml"))
            recording = load_finder_recording(path)
            match = match_findings(recording.result.findings, truths[pr])
            current = score(match, truths[pr])
            if _same(current.recall, row["recall"]) and _same(current.precision, row["precision"]):
                continue
            relevant = len(match.matched) + len(match.noise)
            old_precision = len(match.matched) / relevant if relevant else 1.0
            if _same(current.recall, row["recall"]) and _same(old_precision, row["precision"]):
                old_policy.add(key)
                continue
            offenders.append(f"{key}: stored p={row['precision']} got {current.precision}")

        assert not offenders, f"recordings that reproduce under neither policy: {offenders}"
        assert old_policy == {key for key in rebuilt if by_key[key].get("ambiguous_hits")}, (
            "the rows needing the pre-#345 precision rule are no longer exactly the rows "
            f"carrying ambiguous_hits: {sorted(old_policy)}"
        )


def _same(a: float, b: float) -> bool:
    return abs(a - b) < 1e-9
