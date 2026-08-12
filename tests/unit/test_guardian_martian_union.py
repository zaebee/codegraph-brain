"""Tests for the offline union arm (#342 Phase 3).

The union is scored from committed decision grids and never re-runs a finder, so
these tests pin two things the spec's registration depends on.

First, that the union reuses the *audited* maximum matching rather than a second
implementation of it: the greedy-vs-maximum bug (retraction R4) was found in a
scorer that had been justified by symmetry with another scorer, so a second copy
of Kuhn's algorithm here would be the same mistake with a new name.

Second, that the gate statistic is the paired per-PR one G7 registers, not the
pooled aggregate. On the pilot those two disagree about whether the effect is
three noise floors or a marginal fail, which is the whole reason G7 is worded the
way it is.
"""

import pytest

from cgis.guardian.calibrate import JudgePair, JudgeVerdict, assign_from_grid, assign_matches
from cgis.guardian.martian import (
    JudgedReview,
    UnionRun,
    f_beta,
    paired_effect,
    scorable,
    union_judged,
)


def _judged(
    url: str,
    *,
    n_goldens: int,
    n_candidates: int,
    decisions: list[int | None],
    judge_failures: int = 0,
) -> JudgedReview:
    """A judged row carrying only the fields the union reads."""
    matched = len(assign_from_grid(decisions, n_goldens, n_candidates))
    return JudgedReview(
        url=url,
        project="p",
        pr_slice="graph",
        arm="graph",
        had_graph=True,
        profile="core",
        judge_model="m",
        n_goldens=n_goldens,
        n_candidates=n_candidates,
        tp=matched,
        fp=n_candidates - matched,
        fn=n_goldens - matched,
        precision=matched / n_candidates if n_candidates else 1.0,
        recall=matched / n_goldens if n_goldens else 1.0,
        judge_failures=judge_failures,
        decisions=decisions,
        judged_at="2026-08-12T00:00:00+00:00",
    )


class TestAssignFromGrid:
    """The grid entry point must be the same algorithm as the pair entry point."""

    def test_agrees_with_assign_matches_where_greedy_would_not(self) -> None:
        """The R4 case: golden 1's only candidate is the one golden 0 also wants.

        Greedy by confidence seats golden 0 on candidate 0 and strands golden 1.
        A maximum matching seats both, and both entry points must find both.
        """
        pairs = [
            JudgePair(
                golden_index=0,
                candidate_index=0,
                verdict=JudgeVerdict(reasoning="", match=True, confidence=0.9),
            ),
            JudgePair(
                golden_index=0,
                candidate_index=1,
                verdict=JudgeVerdict(reasoning="", match=True, confidence=0.1),
            ),
            JudgePair(
                golden_index=1,
                candidate_index=0,
                verdict=JudgeVerdict(reasoning="", match=True, confidence=0.5),
            ),
        ]
        grid: list[int | None] = [1, 1, 1, 0]

        assert len(assign_matches(pairs)) == 2
        assert len(assign_from_grid(grid, 2, 2)) == 2

    def test_a_failed_judge_call_is_not_a_match(self) -> None:
        """`None` means the call failed, and an unknown is not a true positive."""
        assert assign_from_grid([None, None], 1, 2) == {}
        assert len(assign_from_grid([None, 1], 1, 2)) == 1

    def test_rejects_a_grid_that_does_not_fit_its_sides(self) -> None:
        """A length mismatch means the caller mixed up two runs; it must not score."""
        with pytest.raises(ValueError, match="grid of 3 does not fit"):
            assign_from_grid([1, 0, 1], 2, 2)


class TestUnionJudged:
    """Union concatenates candidates, keeps goldens, and re-runs the matching."""

    def test_union_recovers_a_golden_neither_run_found_alone(self) -> None:
        """Two runs, each finding one of two goldens; the union finds both."""
        run_a = [_judged("u", n_goldens=2, n_candidates=1, decisions=[1, 0])]
        run_b = [_judged("u", n_goldens=2, n_candidates=1, decisions=[0, 1])]

        rows = union_judged([run_a, run_b])

        assert len(rows) == 1
        assert rows[0].tp == 2
        assert rows[0].n_candidates == 2
        assert rows[0].fn == 0

    def test_a_duplicate_finding_is_charged_as_a_false_positive(self) -> None:
        """Both runs find the same single golden; the second copy cannot be matched.

        This is the registered precision cost of the raw union, and it is a
        property of maximum matching rather than of a missing dedup step: a
        golden can be spent once, so the duplicate candidate is unmatched.
        """
        run_a = [_judged("u", n_goldens=1, n_candidates=1, decisions=[1])]
        run_b = [_judged("u", n_goldens=1, n_candidates=1, decisions=[1])]

        rows = union_judged([run_a, run_b])

        assert rows[0].tp == 1
        assert rows[0].fp == 1

    def test_judge_failures_accumulate_rather_than_vanish(self) -> None:
        """A union that hid its failed judge calls would look cleaner than it is."""
        run_a = [_judged("u", n_goldens=1, n_candidates=1, decisions=[None], judge_failures=1)]
        run_b = [_judged("u", n_goldens=1, n_candidates=1, decisions=[1], judge_failures=2)]

        assert union_judged([run_a, run_b])[0].judge_failures == 3

    def test_a_pr_missing_from_any_run_is_excluded(self) -> None:
        """An unpaired PR would make the union of N a union of fewer, silently."""
        run_a = [
            _judged("kept", n_goldens=1, n_candidates=1, decisions=[1]),
            _judged("dropped", n_goldens=1, n_candidates=1, decisions=[1]),
        ]
        run_b = [_judged("kept", n_goldens=1, n_candidates=1, decisions=[1])]

        rows = union_judged([run_a, run_b])

        assert [r.url for r in rows] == ["kept"]

    def test_refuses_runs_that_disagree_about_the_goldens(self) -> None:
        """Different golden counts mean different corpora, and rows would misalign.

        The grids are concatenated row-wise on the assumption that golden *i* is
        the same golden in every run. If the corpus or profile moved between
        runs that assumption is false and the union would score nonsense.
        """
        run_a = [_judged("u", n_goldens=2, n_candidates=1, decisions=[1, 0])]
        run_b = [_judged("u", n_goldens=3, n_candidates=1, decisions=[1, 0, 0])]

        with pytest.raises(ValueError, match="disagree about goldens"):
            union_judged([run_a, run_b])

    def test_refuses_a_single_run(self) -> None:
        """A union of one is a single run wearing the union's name."""
        only = [_judged("u", n_goldens=1, n_candidates=1, decisions=[1])]

        with pytest.raises(ValueError, match="at least two runs"):
            union_judged([only])


class TestPairedEffect:
    """G7's statistic: paired, per-PR, in true-positive counts."""

    def test_reproduces_the_pilot_reported_in_the_spec(self) -> None:
        """The six-PR pilot: d = [0.5, 1.5, 1.5, 0, 0, 0] gives 1.94, a marginal fail.

        Pinned because the spec published this number as the reason the gate is
        paired rather than pooled, and because it is the number a later pass
        would be compared against.
        """
        effect = paired_effect([0.5, 1.5, 1.5, 0.0, 0.0, 0.0])

        assert effect.mean == pytest.approx(0.5833, abs=1e-4)
        assert effect.standard_error == pytest.approx(0.3005, abs=1e-4)
        assert effect.ratio == pytest.approx(1.941, abs=1e-3)
        assert effect.passes is False

    def test_passes_only_above_two_standard_errors(self) -> None:
        """The gate is `mean(d) > 2 * se(d)`, registered before Phase 3 ran."""
        assert paired_effect([1.0, 1.0, 1.0, 0.9]).passes is True
        assert paired_effect([1.0, -1.0, 1.0, -1.0]).passes is False

    def test_a_uniform_effect_has_no_dispersion_and_passes(self) -> None:
        """Zero variance is a real answer, not a division to guard against."""
        effect = paired_effect([2.0, 2.0, 2.0])

        assert effect.standard_error == 0.0
        assert effect.passes is True

    def test_a_uniform_zero_effect_does_not_pass(self) -> None:
        """Zero mean with zero dispersion is the null, and `0 > 0` is false."""
        assert paired_effect([0.0, 0.0, 0.0]).passes is False

    def test_a_uniform_regression_reports_a_negative_ratio(self) -> None:
        """The union being consistently worse must not print as an infinite win.

        Zero dispersion sends the ratio to infinity, and it has to carry the
        sign of the mean. An earlier nested conditional returned bare `+inf` for
        any non-zero mean, so a uniform regression printed as an overwhelming
        effect next to a correct FAIL.
        """
        effect = paired_effect([-2.0, -2.0, -2.0])

        assert effect.mean == -2.0
        assert effect.ratio == float("-inf")
        assert effect.passes is False

    def test_refuses_fewer_than_two_pairs(self) -> None:
        """A standard error over one observation is undefined, not zero."""
        with pytest.raises(ValueError, match="at least two"):
            paired_effect([1.0])


class TestUnionRun:
    """The assembled arm: per-PR deltas, the pooled headline, and both gates."""

    def _runs(self) -> list[list[JudgedReview]]:
        """Two runs over two PRs; the union finds a golden neither found alone."""
        return [
            [
                _judged("a", n_goldens=2, n_candidates=1, decisions=[1, 0]),
                _judged("b", n_goldens=2, n_candidates=1, decisions=[1, 0]),
            ],
            [
                _judged("a", n_goldens=2, n_candidates=1, decisions=[0, 1]),
                _judged("b", n_goldens=2, n_candidates=1, decisions=[1, 0]),
            ],
        ]

    def test_deltas_are_union_minus_the_mean_of_the_runs(self) -> None:
        """PR a: union 2 against a mean of 1. PR b: union 1 against a mean of 1."""
        run = UnionRun.build(self._runs(), beta=2.0)

        assert run.deltas == [1.0, 0.0]

    def test_publishes_the_pooled_headline_beside_the_paired_gate(self) -> None:
        """Both are named wherever either appears — the spec requires it."""
        run = UnionRun.build(self._runs(), beta=2.0)

        assert run.union.recall == pytest.approx(0.75)
        assert run.mean_recall == pytest.approx(0.5)
        assert run.effect.ratio == pytest.approx(1.0)

    def test_g8_compares_f_beta_of_the_union_against_the_mean_run(self) -> None:
        """At the registered beta of 2.0 the recall gain outweighs the added noise."""
        run = UnionRun.build(self._runs(), beta=2.0)

        assert run.union.f_beta > run.mean_f_beta
        assert run.g8_passes is True

    def test_the_same_arm_fails_g8_when_beta_weights_precision(self) -> None:
        """Union is a beta dial: the identical data reverses under beta 0.5.

        This is why beta is registered before the run rather than chosen from
        the report.
        """
        run = UnionRun.build(self._runs(), beta=0.5)

        assert run.g8_passes is False

    def test_beta_reaches_the_mean_as_well_as_the_union(self) -> None:
        """A report mixing two betas in one comparison would not be a comparison."""
        runs = self._runs()
        by_two = UnionRun.build(runs, beta=2.0)
        by_half = UnionRun.build(runs, beta=0.5)

        assert by_two.mean_f_beta == pytest.approx(
            f_beta(by_two.mean_precision, by_two.mean_recall, 2.0)
        )
        assert by_half.mean_f_beta != by_two.mean_f_beta

    def test_both_sides_of_g8_are_on_the_same_scale(self) -> None:
        """The union's f_beta is a fraction, so the mean's must be one too.

        `SliceScore` stores rates on 0-1 and only `_score_line` renders percent.
        An earlier version scaled the mean by 100 and not the union, which made
        G8 pass on every possible input — a gate that cannot fail.
        """
        run = UnionRun.build(self._runs(), beta=2.0)

        assert 0.0 <= run.mean_f_beta <= 1.0
        assert 0.0 <= run.union.f_beta <= 1.0


class TestScorable:
    """A judged row is a measurement only if every pair was actually ruled on.

    Registered 2026-08-12, after the Mistral subscription hit its limit mid-run.
    The judge kept being called, every call failed with HTTP 402, and a row was
    written anyway — `tp=0`, every candidate a false positive, `P=0.00 R=0.00`.
    Nine of eleven rows looked exactly like measurements of a reviewer that found
    nothing, and `union` and `report` would have read them as such.

    `judge_failures` already existed and its docstring already said such a row
    "must not be quoted as a precision number". Nothing enforced it — the same
    shape as `parse_failed`, which was recorded and read by nobody.

    A failed pair is an *unknown*, not a non-match, and the scorer counts
    unknowns as non-matches. So any failure makes the row's tp a lower bound
    rather than a value, which is why the rule is strict rather than
    proportional. Phase 2 carries zero failed pairs, so nothing already
    published moves.
    """

    def _row(self, url: str, *, failures: int) -> JudgedReview:
        return _judged(url, n_goldens=1, n_candidates=1, decisions=[1]).model_copy(
            update={"judge_failures": failures}
        )

    def test_a_row_with_no_failures_is_scorable(self) -> None:
        assert len(scorable([self._row("u", failures=0)])) == 1

    def test_a_single_failed_pair_disqualifies_the_row(self) -> None:
        """tp becomes a lower bound, and a lower bound is not a precision figure."""
        assert scorable([self._row("u", failures=1)]) == []

    def test_the_exclusion_removes_the_pr_from_every_run(self) -> None:
        """Via the rule already registered for parse failures: missing in one, out of all.

        Otherwise a run whose judge died would contribute an artificial zero to
        the mean while the union, which loses nothing to an empty candidate set,
        kept its findings — inflating the very advantage G7 and G8 test.
        """
        run_a = [
            _judged("kept", n_goldens=1, n_candidates=1, decisions=[1]),
            _judged("lost", n_goldens=1, n_candidates=1, decisions=[1]),
        ]
        run_b = [
            _judged("kept", n_goldens=1, n_candidates=1, decisions=[1]),
            self._row("lost", failures=1),
        ]

        rows = union_judged([scorable(run_a), scorable(run_b)])

        assert [r.url for r in rows] == ["kept"]
