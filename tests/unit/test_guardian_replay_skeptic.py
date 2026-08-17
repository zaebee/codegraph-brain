"""The replay arm: same findings, same diff, one variable (#401).

Running the guardian twice cannot measure what evidence does, because the finder
is sampled and the two runs disagree about what was claimed before anything is
judged. A recorded pass removes that: its findings carry the verdicts of the run
that produced them, so re-judging the same list *with* evidence is one arm,
paired per finding.

Everything here is exercised except the model call itself.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from cgis.guardian.recording import load_finder_recording
from cgis.guardian.skeptic import FindingJudgement

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from guardian_replay_skeptic import (
    NoBaselineError,
    baseline_verdicts,
    changed_files,
    cites_a_checker,
    flips,
    worktree_at,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_DIFF = (
    "diff --git a/src/a.py b/src/a.py\n"
    "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x\n+y\n"
    "diff --git a/src/b.py b/src/b.py\n"
    "--- a/src/b.py\n+++ b/src/b.py\n@@ -1 +1 @@\n-p\n+q\n"
)


def _finding(verdict: str | None) -> dict[str, Any]:
    base: dict[str, Any] = {
        "file": "src/a.py",
        "line": 1,
        "severity": "major",
        "category": "logic",
        "title": "t",
        "evidence": "e",
        "problem": "p",
        "fix": "f",
        "confidence": 80,
    }
    if verdict is not None:
        base["verdict"] = verdict
    return base


def _recording(tmp_path: Path, verdicts: list[str | None]) -> Path:
    path = tmp_path / "guardian_finder.json"
    path.write_text(
        json.dumps(
            {
                "result": {"findings": [_finding(v) for v in verdicts], "summary": ""},
                "diff": _DIFF,
            }
        ),
        encoding="utf-8",
    )
    return path


def _judgement(verdict: str, rationale: str = "because") -> FindingJudgement:
    return FindingJudgement(verdict=verdict, impact_score=0, rationale=rationale)  # type: ignore[arg-type]


class TestTheBaselineMustExist:
    """A replay without a control arm produces a column and calls it a result."""

    def test_recorded_verdicts_are_read_in_order(self, tmp_path: Path) -> None:
        path = _recording(tmp_path, ["confirmed", "refuted", "confirmed"])
        assert baseline_verdicts(path) == ["confirmed", "refuted", "confirmed"]

    def test_a_recording_with_no_verdicts_is_refused(self, tmp_path: Path) -> None:
        """A finder pass captured before any skeptic ran has nothing to compare to."""
        path = _recording(tmp_path, [None, None])
        with pytest.raises(NoBaselineError, match="no usable baseline"):
            baseline_verdicts(path)

    def test_one_unruled_finding_refuses_the_whole_recording(self, tmp_path: Path) -> None:
        """Partly-judged is not a baseline, and silently dropping the gap would bias it.

        The unruled findings are exactly the ones whose skeptic call failed —
        which correlates with size and difficulty, not with nothing.
        """
        path = _recording(tmp_path, ["confirmed", None, "refuted"])
        with pytest.raises(NoBaselineError, match="1 of 3"):
            baseline_verdicts(path)

    def test_the_loader_strips_verdicts_so_the_replay_starts_blind(self, tmp_path: Path) -> None:
        """The reason the baseline is read from raw JSON rather than the model.

        `load_finder_recording` removes verdicts on purpose. If the replay saw
        them it would be judging findings that already carry an answer, and the
        arm would measure agreement with itself.
        """
        path = _recording(tmp_path, ["confirmed", "refuted"])
        loaded = load_finder_recording(path)
        assert [f.verdict for f in loaded.result.findings] == [None, None]
        assert baseline_verdicts(path) == ["confirmed", "refuted"]


class TestPairing:
    """Positional, because `judge_all` and `apply_judgements` both are."""

    def test_a_transition_matrix_counts_pairs(self) -> None:
        matrix = flips(
            ["confirmed", "confirmed", "refuted"],
            [_judgement("refuted"), _judgement("confirmed"), _judgement("refuted")],
        )
        assert matrix["confirmed -> refuted"] == 1
        assert matrix["confirmed -> confirmed"] == 1
        assert matrix["refuted -> refuted"] == 1

    def test_a_failed_call_is_its_own_outcome(self) -> None:
        """Counting it as a verdict would let an API outage look like a change of mind."""
        matrix = flips(["confirmed"], [None])
        assert matrix["confirmed -> call failed"] == 1

    def test_mismatched_lengths_refuse_rather_than_zip_short(self) -> None:
        """`zip` without `strict` would silently drop the tail and still report a matrix."""
        replayed = [_judgement("refuted")]
        with pytest.raises(ValueError, match="not paired"):
            flips(["confirmed", "refuted"], replayed)


class TestAttribution:
    """Which flips the evidence can actually claim."""

    @pytest.mark.parametrize(
        "rationale",
        [
            "the provided Ruff checker output explicitly shows 'All checks passed!'",
            "mypy reports no error for this file",
            "the type check is clean",
            "the linter does not flag it",
        ],
    )
    def test_a_rationale_resting_on_the_checkers_counts(self, rationale: str) -> None:
        assert cites_a_checker(_judgement("refuted", rationale))

    def test_a_rationale_resting_on_the_diff_does_not(self) -> None:
        """The skeptic reading code is not the feature under test.

        Counting these would let the arm claim every ordinary refutation, which
        is most of them — on the live run that produced this design, 8 of 11.
        """
        assert not cites_a_checker(
            _judgement("refuted", "the claim misreads what Path.resolve does")
        )

    def test_a_failed_call_cites_nothing(self) -> None:
        assert not cites_a_checker(None)


def test_changed_files_come_from_the_recorded_diff() -> None:
    """From the recording, not from git: the claims are about those files.

    A path list re-derived today would follow the branch, and the checkers would
    then report on code the finder never saw.
    """
    assert changed_files(_DIFF) == ("src/a.py", "src/b.py")


def _enter_and_raise(ref: str, seen: list[Path]) -> None:
    """Open a worktree, record it, and fail inside — the case the finally must cover."""
    with worktree_at(ref, REPO_ROOT) as tree:
        seen.append(tree)
        assert (tree / "pyproject.toml").is_file()
        _msg = "boom"
        raise RuntimeError(_msg)


def test_the_worktree_is_removed_even_when_the_body_raises() -> None:
    """A leaked worktree makes the next run fail on a path that already exists.

    The body is a helper so the `pytest.raises` block holds one call
    (python:S5778) — otherwise an assertion inside it could raise and the test
    would pass on the wrong exception.
    """
    ref = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    seen: list[Path] = []
    with pytest.raises(RuntimeError, match="boom"):
        _enter_and_raise(ref, seen)
    assert seen
    assert not seen[0].exists()
    listed = subprocess.run(
        ["git", "worktree", "list"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    assert str(seen[0]) not in listed
