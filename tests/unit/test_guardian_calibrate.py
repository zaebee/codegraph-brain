"""Unit tests for the Phase 1 calibration scorer (#342).

Everything here is pure: the LLM judge is never called. What is under test is
the rendering of both sides of a judge pair, the 1:1 assignment, the score
derived from it, and the two agreement statistics the gates are stated in.
"""

import random
import statistics

import pytest
from guardian_stubs import FlakyProvider
from pydantic import BaseModel

from cgis.guardian.bench import GroundTruthEntry
from cgis.guardian.calibrate import (
    _MIN_CORRELATION_POINTS,
    JudgePair,
    JudgeVerdict,
    assign_matches,
    candidate_text,
    cohen_kappa,
    decision_agreement,
    golden_text,
    is_rate_limited,
    judge_matrix,
    judge_pair,
    judge_score,
    positive_agreement,
    spearman,
    tied_ranks,
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


def _match(golden: int, candidate: int, confidence: float, *, match: bool = True) -> JudgePair:
    return JudgePair(
        golden_index=golden,
        candidate_index=candidate,
        verdict=JudgeVerdict(reasoning="r", match=match, confidence=confidence),
    )


class TestAssignMatches:
    """Maximum 1:1 matching — one golden, one candidate, as many pairs as exist."""

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

    def test_reseats_an_incumbent_rather_than_stranding_a_golden(self) -> None:
        """The bug that made this a maximum matching instead of a greedy one.

        Golden 0 can only be matched by candidate 0. Golden 1 can take either.
        Greedy by confidence spends candidate 0 on golden 1 first — the
        strongest pair on the board — and golden 0 is then unmatchable, giving
        one true positive where two exist. Kuhn's re-seats golden 1 onto
        candidate 1 and keeps both.
        """
        pairs = [_match(1, 0, 0.95), _match(0, 0, 0.80), _match(1, 1, 0.70)]
        assert assign_matches(pairs) == {0: 0, 1: 1}

    def test_size_does_not_depend_on_confidence(self) -> None:
        """What makes a stored decision grid enough to re-derive tp (`rescore`)."""
        pairs = [_match(1, 0, 0.95), _match(0, 0, 0.80), _match(1, 1, 0.70)]
        flattened = [_match(p.golden_index, p.candidate_index, 1.0) for p in pairs]
        assert len(assign_matches(flattened)) == len(assign_matches(pairs))


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


class TestTiedRanks:
    """The rank transform is hand-rolled, so it owes the stdlib an equivalence proof."""

    def test_distinct_values_rank_one_to_n(self) -> None:
        assert tied_ranks([30.0, 10.0, 20.0]) == [3.0, 1.0, 2.0]

    def test_ties_share_the_average_of_the_positions_they_span(self) -> None:
        """Ranks 2 and 3 both become 2.5; the next value still gets rank 4."""
        assert tied_ranks([1.0, 5.0, 5.0, 9.0]) == [1.0, 2.5, 2.5, 4.0]

    def test_all_equal_collapses_to_one_rank(self) -> None:
        assert tied_ranks([7.0] * 4) == [2.5] * 4

    def test_empty_is_empty(self) -> None:
        assert tied_ranks([]) == []

    def test_matches_the_stdlib_ranked_method(self) -> None:
        """Pearson-on-our-ranks == `statistics.correlation(..., method="ranked")`.

        The whole reason `tied_ranks` exists is to avoid a `method=` keyword
        that SonarCloud's stale typeshed rejects (python:S930, a Blocker). That
        trade is only acceptable if the number does not move, so this asserts
        it over tie-heavy random inputs rather than on a hand-picked case.
        """
        rng = random.Random(20260811)
        compared = 0
        for _ in range(200):
            size = rng.randint(_MIN_CORRELATION_POINTS, 40)
            # A small value pool on purpose: real calibration columns are mostly
            # 0.0 and 1.0, so ties are the common case, not the edge case.
            xs = [rng.choice([0.0, 0.5, 1.0, rng.random()]) for _ in range(size)]
            ys = [rng.choice([0.0, 1.0, rng.random()]) for _ in range(size)]
            try:
                expected = statistics.correlation(xs, ys, method="ranked")
            except statistics.StatisticsError:
                continue
            assert spearman(xs, ys) == pytest.approx(expected)
            compared += 1
        assert compared > 100, "too few non-degenerate cases to call this a comparison"


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


VERDICT_JSON = '{"reasoning": "same bug", "match": true, "confidence": 0.9}'


def _flaky(errors: list[Exception]) -> FlakyProvider:
    return FlakyProvider(errors, VERDICT_JSON)


class _SDKError(Exception):
    """Stand-in for mistralai.SDKError, which is an optional dependency here."""


def _rate_limited() -> Exception:
    return _SDKError(
        'API error occurred: Status 429. Body: {"object":"error",'
        '"message":"Rate limit exceeded","type":"rate_limited","code":"1300"}'
    )


class TestIsRateLimited:
    """Rate limits are detected by message, because the SDK types are optional deps."""

    def test_detects_mistral_429(self) -> None:
        assert is_rate_limited(_rate_limited())

    def test_detects_wording_without_a_status_code(self) -> None:
        assert is_rate_limited(RuntimeError("RESOURCE_EXHAUSTED: rate limit exceeded"))

    def test_ignores_an_auth_failure(self) -> None:
        """Retrying a 401 burns attempts on something that will never succeed."""
        assert not is_rate_limited(RuntimeError("Status 401. Unauthorized: bad api key"))

    def test_ignores_a_429_inside_an_unrelated_number(self) -> None:
        assert not is_rate_limited(RuntimeError("parsed 1429 tokens"))


class _RecordingSleep:
    """A stand-in for asyncio.sleep that records the backoff instead of waiting."""

    def __init__(self) -> None:
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        """Record one backoff and return immediately."""
        self.seconds.append(seconds)


class TestJudgePairRetries:
    """The calibration makes thousands of tiny calls; 429 is the expected failure."""

    @pytest.mark.asyncio
    async def test_retries_a_rate_limit_then_succeeds(self) -> None:
        provider, slept = _flaky([_rate_limited(), _rate_limited()]), _RecordingSleep()
        verdict = await judge_pair(provider, "golden", "candidate", sleep=slept)
        assert verdict is not None
        assert verdict.match is True
        assert provider.calls == 3
        assert slept.seconds == [2.0, 4.0]

    @pytest.mark.asyncio
    async def test_gives_up_after_max_attempts(self) -> None:
        provider, slept = _flaky([_rate_limited()] * 10), _RecordingSleep()
        assert await judge_pair(provider, "golden", "candidate", 3, sleep=slept) is None
        assert provider.calls == 3

    @pytest.mark.asyncio
    async def test_does_not_retry_a_non_rate_limit_error(self) -> None:
        """A malformed response or an auth failure must cost exactly one call."""
        provider, slept = _flaky([RuntimeError("Status 401. Unauthorized")]), _RecordingSleep()
        assert await judge_pair(provider, "golden", "candidate", sleep=slept) is None
        assert provider.calls == 1
        assert slept.seconds == []

    @pytest.mark.asyncio
    async def test_a_budget_of_zero_attempts_asks_nothing(self) -> None:
        provider = _flaky([])
        assert await judge_pair(provider, "golden", "candidate", 0) is None
        assert provider.calls == 0

    @pytest.mark.asyncio
    async def test_unparseable_output_is_not_recorded_as_a_non_match(self) -> None:
        """None, not a False verdict — judge downtime must not read as precision."""
        provider = FlakyProvider([], "not json at all")
        assert await judge_pair(provider, "golden", "candidate", sleep=_RecordingSleep()) is None
        assert provider.calls == 1


class _Escaped(BaseException):
    """The one category `judge_pair`'s `except Exception` cannot catch."""


class _ExplodingProvider(FlakyProvider):
    """A provider that fails in a way the retry loop lets through."""

    async def generate_structured(
        self,
        system_prompt: str,  # noqa: ARG002
        user_prompt: str,  # noqa: ARG002
        schema: type[BaseModel],  # noqa: ARG002
    ) -> str:
        """Raise past the retry loop's `except Exception`."""
        self.calls += 1
        raise _Escaped


class TestJudgeMatrix:
    """Every pair of one review, and an honest count of the ones that went unruled."""

    @pytest.mark.asyncio
    async def test_judges_the_full_cross_product(self) -> None:
        provider = FlakyProvider([], VERDICT_JSON)
        pairs, failures = await judge_matrix(provider, ["g1", "g2"], ["c1", "c2", "c3"])
        assert provider.calls == 6
        assert failures == 0
        assert {(p.golden_index, p.candidate_index) for p in pairs} == {
            (g, c) for g in range(2) for c in range(3)
        }

    @pytest.mark.asyncio
    async def test_an_unruled_pair_is_counted_not_dropped(self) -> None:
        """The count is what bounds how much of the resulting score is real."""
        provider = FlakyProvider([], "not json")
        pairs, failures = await judge_matrix(provider, ["g1"], ["c1", "c2"])
        assert pairs == []
        assert failures == 2

    @pytest.mark.asyncio
    async def test_an_escaping_exception_costs_one_pair_not_the_whole_review(self) -> None:
        """Without return_exceptions, gather would abandon every in-flight pair."""
        pairs, failures = await judge_matrix(_ExplodingProvider([], VERDICT_JSON), ["g"], ["c1"])
        assert pairs == []
        assert failures == 1

    @pytest.mark.asyncio
    async def test_an_empty_side_asks_nothing(self) -> None:
        provider = FlakyProvider([], VERDICT_JSON)
        assert await judge_matrix(provider, ["g"], []) == ([], 0)
        assert provider.calls == 0


class TestCohenKappa:
    """G3's registered raw-agreement form is uninformative at a low base rate.

    The judge said `match` on 4.8% of pairs in the first run. Two such judges
    agree by chance ~91% of the time, above the 80% gate — so the gate cannot
    fail. Kappa is what the gate should have been written in, and these tests
    pin the degenerate cases that make that true.
    """

    def test_perfect_agreement_is_one(self) -> None:
        assert cohen_kappa([True, False, True, False], [True, False, True, False]) == (
            pytest.approx(1.0)
        )

    def test_systematic_disagreement_scores_below_zero(self) -> None:
        """Both judges say yes a quarter of the time, and never on the same pair.

        Negative kappa is worse than chance, not chance: at these base rates
        independence would give 0.0, and never coinciding gives -1/3.
        """
        a = [True, False, False, False] * 25
        b = [False, True, False, False] * 25
        assert cohen_kappa(a, b) == pytest.approx(-1 / 3, abs=0.01)

    def test_high_raw_agreement_at_a_low_base_rate_scores_poorly(self) -> None:
        """96% raw agreement — passing G3 as registered — yet kappa is near zero.

        This is the exact failure mode of the registered gate: 100 pairs, each
        judge says yes twice, never on the same pair.
        """
        a = [True, True] + [False] * 98
        b = [False, False, True, True] + [False] * 96
        assert decision_agreement(a, b) == pytest.approx(0.96)
        kappa = cohen_kappa(a, b)
        assert kappa is not None
        assert kappa < 0.1

    def test_both_judges_constant_is_undefined(self) -> None:
        """No expected disagreement means kappa has a zero denominator.

        Returning 1.0 would claim perfect agreement between two judges that
        never made a distinction; None says the statistic does not apply.
        """
        assert cohen_kappa([False] * 10, [False] * 10) is None

    def test_empty_is_undefined(self) -> None:
        assert cohen_kappa([], []) is None

    def test_length_mismatch_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="length"):
            cohen_kappa([True], [True, False])


class TestPositiveAgreement:
    """The substantive G3 number: do the judges match the SAME pairs?"""

    def test_jaccard_on_the_positive_class(self) -> None:
        a = [True, True, False, False]
        b = [True, False, True, False]
        assert positive_agreement(a, b) == pytest.approx(1 / 3)

    def test_no_positives_anywhere_is_undefined(self) -> None:
        assert positive_agreement([False, False], [False, False]) is None
