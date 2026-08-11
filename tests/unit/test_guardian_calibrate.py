"""Unit tests for the Phase 1 calibration scorer (#342).

Everything here is pure: the LLM judge is never called. What is under test is
the rendering of both sides of a judge pair, the 1:1 assignment, the score
derived from it, and the two agreement statistics the gates are stated in.
"""

import pytest

from cgis.guardian.bench import GroundTruthEntry
from cgis.guardian.calibrate import (
    JudgePair,
    JudgeVerdict,
    assign_matches,
    candidate_text,
    decision_agreement,
    golden_text,
    judge_score,
    spearman,
)
from cgis.guardian.findings import Finding


def _entry(**overrides: object) -> GroundTruthEntry:
    base = {
        "id": "yaml-mapping-guard",
        "file": "src/cgis/query/drift.py",
        "lines": (180, 196),
        "severity": "major",
        "category": "types",
        "summary": "_ideal_layer never validates its layer argument is a mapping before set(layer)",
        "source": "gemini",
    }
    return GroundTruthEntry.model_validate(base | overrides)


def _finding(**overrides: object) -> Finding:
    base = {
        "file": "src/cgis/query/drift.py",
        "line": 184,
        "severity": "major",
        "category": "types",
        "title": "Unvalidated layer argument",
        "evidence": "return set(layer)",
        "problem": "A non-mapping layer reaches set() and raises TypeError at runtime.",
        "fix": "Guard with isinstance(layer, dict) before calling set().",
        "confidence": 90,
    }
    return Finding.model_validate(base | overrides)


class TestGoldenText:
    """The golden side must read as prose and must not leak its anchor."""

    def test_is_the_summary(self) -> None:
        assert golden_text(_entry()) == _entry().summary

    def test_omits_file_and_lines(self) -> None:
        rendered = golden_text(_entry())
        assert "drift.py" not in rendered
        assert "180" not in rendered


class TestCandidateText:
    """The candidate side carries the claim, not the position."""

    def test_contains_title_problem_and_fix(self) -> None:
        rendered = candidate_text(_finding())
        assert "Unvalidated layer argument" in rendered
        assert "raises TypeError at runtime" in rendered
        assert "isinstance(layer, dict)" in rendered

    def test_omits_file_line_and_evidence(self) -> None:
        """Evidence is a verbatim source line — an anchor the golden side lacks.

        Handing it to the judge would make the comparison asymmetric: the
        candidate would carry positional information no golden comment has.
        """
        rendered = candidate_text(_finding())
        assert "drift.py" not in rendered
        assert "184" not in rendered
        assert "return set(layer)" not in rendered


class TestAssignMatches:
    """Greedy 1:1, descending judge confidence — one golden, one candidate."""

    def test_empty_input_yields_no_assignment(self) -> None:
        assert assign_matches([]) == {}

    def test_non_matches_are_ignored(self) -> None:
        pairs = [
            JudgePair(
                golden_index=0,
                candidate_index=0,
                verdict=JudgeVerdict(reasoning="different bug", match=False, confidence=0.9),
            )
        ]
        assert assign_matches(pairs) == {}

    def test_highest_confidence_wins_a_contested_golden(self) -> None:
        pairs = [
            JudgePair(
                golden_index=0,
                candidate_index=0,
                verdict=JudgeVerdict(reasoning="weak", match=True, confidence=0.4),
            ),
            JudgePair(
                golden_index=0,
                candidate_index=1,
                verdict=JudgeVerdict(reasoning="strong", match=True, confidence=0.95),
            ),
        ]
        assert assign_matches(pairs) == {0: 1}

    def test_a_candidate_matching_two_goldens_is_used_once(self) -> None:
        """One review comment cannot be credited with finding two defects."""
        pairs = [
            JudgePair(
                golden_index=0,
                candidate_index=0,
                verdict=JudgeVerdict(reasoning="yes", match=True, confidence=0.9),
            ),
            JudgePair(
                golden_index=1,
                candidate_index=0,
                verdict=JudgeVerdict(reasoning="also yes", match=True, confidence=0.8),
            ),
        ]
        assert assign_matches(pairs) == {0: 0}

    def test_ties_break_on_index_so_the_result_is_deterministic(self) -> None:
        pairs = [
            JudgePair(
                golden_index=0,
                candidate_index=1,
                verdict=JudgeVerdict(reasoning="tie", match=True, confidence=0.7),
            ),
            JudgePair(
                golden_index=0,
                candidate_index=0,
                verdict=JudgeVerdict(reasoning="tie", match=True, confidence=0.7),
            ),
        ]
        assert assign_matches(pairs) == {0: 0}


class TestJudgeScore:
    """TP/FP/FN follow from the assignment; empty sides degrade like score()."""

    def test_counts_and_rates(self) -> None:
        result = judge_score(n_goldens=4, n_candidates=5, assignment={0: 0, 2: 3})
        assert (result.tp, result.fn, result.fp) == (2, 2, 3)
        assert result.recall == pytest.approx(0.5)
        assert result.precision == pytest.approx(0.4)

    def test_no_goldens_makes_recall_vacuously_one(self) -> None:
        """Matches bench.score(): a PR with no curated defect cannot miss one."""
        result = judge_score(n_goldens=0, n_candidates=3, assignment={})
        assert result.recall == pytest.approx(1.0)
        assert result.precision == pytest.approx(0.0)

    def test_no_candidates_makes_precision_vacuously_one(self) -> None:
        result = judge_score(n_goldens=3, n_candidates=0, assignment={})
        assert result.precision == pytest.approx(1.0)
        assert result.recall == pytest.approx(0.0)

    def test_rejects_an_assignment_larger_than_its_sides(self) -> None:
        """A tp exceeding either axis means the caller mismatched inputs."""
        with pytest.raises(ValueError, match="assignment"):
            judge_score(n_goldens=1, n_candidates=5, assignment={0: 0, 1: 1})


class TestSpearman:
    """G1 is stated in Spearman rho, so its degenerate cases must be explicit."""

    def test_perfect_monotonic_agreement(self) -> None:
        assert spearman([1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 30.0, 40.0]) == pytest.approx(1.0)

    def test_perfect_inversion(self) -> None:
        assert spearman([1.0, 2.0, 3.0, 4.0], [4.0, 3.0, 2.0, 1.0]) == pytest.approx(-1.0)

    def test_ranks_not_values(self) -> None:
        """Monotone but wildly non-linear still scores 1.0 — that is the point."""
        assert spearman([1.0, 2.0, 3.0, 4.0], [1.0, 4.0, 900.0, 1000.0]) == pytest.approx(1.0)

    def test_constant_input_is_undefined_not_zero(self) -> None:
        """A scorer that returned the same number everywhere has no correlation.

        Reporting 0.0 would read as 'measured, and they disagree'. None reads as
        'not measurable', which is what a constant column actually means — and
        pr-141 (no ground truth, precision 0 on every run) produces exactly one.
        """
        assert spearman([1.0, 1.0, 1.0, 1.0], [1.0, 2.0, 3.0, 4.0]) is None

    def test_too_few_points_is_undefined(self) -> None:
        assert spearman([1.0, 2.0], [2.0, 1.0]) is None

    def test_length_mismatch_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="length"):
            spearman([1.0, 2.0, 3.0], [1.0, 2.0])


class TestDecisionAgreement:
    """G3 counts per-decision agreement between two judges, not score deltas."""

    def test_full_agreement(self) -> None:
        assert decision_agreement([True, False, True], [True, False, True]) == pytest.approx(1.0)

    def test_partial_agreement(self) -> None:
        assert decision_agreement([True, False, True, True], [True, True, True, False]) == (
            pytest.approx(0.5)
        )

    def test_empty_is_undefined(self) -> None:
        assert decision_agreement([], []) is None

    def test_length_mismatch_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="length"):
            decision_agreement([True], [True, False])
