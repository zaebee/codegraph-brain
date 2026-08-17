"""Unit tests for per-finding judgement, the pure merge, and the impact threshold (#246)."""

import asyncio
from typing import ClassVar

import pytest
from guardian_stubs import BoomProvider, StubProvider
from pydantic import BaseModel

from cgis.guardian.evidence import Evidence
from cgis.guardian.findings import Finding
from cgis.guardian.providers.base import BaseProvider
from cgis.guardian.skeptic import (
    SKEPTIC_SYSTEM_PROMPT,
    FindingJudgement,
    apply_judgements,
    build_judgement_prompt,
    judge_all,
    judge_finding,
    visible_findings,
)

_FINDING = Finding(
    file="src/cgis/cli.py",
    line=42,
    severity="major",
    category="logic",
    title="off-by-one",
    evidence="range(n + 1)",
    problem="iterates past the end.",
    fix="use range(n).",
    confidence=85,
)


# ---------------------------------------------------------------------------
# Per-finding judgement: two orthogonal axes (#246)
# ---------------------------------------------------------------------------


def _judgement(verdict: str, score: int, rationale: str = "because") -> FindingJudgement:
    return FindingJudgement(verdict=verdict, impact_score=score, rationale=rationale)  # type: ignore[arg-type]


def test_judgement_merges_verdict_note_and_score() -> None:
    """A judgement writes verdict, note and impact_score onto a new frozen copy."""
    merged = apply_judgements([_FINDING], [_judgement("confirmed", 7)])
    assert merged[0].verdict == "confirmed"
    assert merged[0].skeptic_note == "because"
    assert merged[0].impact_score == 7
    assert _FINDING.impact_score is None  # original untouched (frozen)


def test_judgement_uncertain_discounts_confidence_and_keeps_finding() -> None:
    """uncertain keeps the finding and discounts confidence x0.9, exactly as before."""
    f = _FINDING.model_copy(update={"confidence": 30})
    merged = apply_judgements([f], [_judgement("uncertain", 4)])
    assert merged[0].verdict == "uncertain"
    assert merged[0].confidence == 27
    assert merged[0].impact_score == 4
    assert visible_findings(merged) == merged


def test_refuted_is_marked_but_stays_in_the_list() -> None:
    """refuted hides the finding from the report but never removes it from the result.

    Metrics and the benchmark must still see what the skeptic killed.
    """
    merged = apply_judgements([_FINDING], [_judgement("refuted", 0)])
    assert merged[0].verdict == "refuted"
    assert len(merged) == 1
    assert visible_findings(merged) == []


def test_visible_findings_drops_only_refuted_at_default_threshold() -> None:
    """confirmed/uncertain/unjudged all stay; only refuted goes."""
    kept = _FINDING.model_copy(update={"verdict": "confirmed", "impact_score": 2})
    unsure = _FINDING.model_copy(update={"verdict": "uncertain", "title": "u"})
    unjudged = _FINDING.model_copy(update={"title": "n"})
    dropped = _FINDING.model_copy(update={"verdict": "refuted", "title": "x"})
    assert visible_findings([kept, unsure, unjudged, dropped]) == [kept, unsure, unjudged]


def test_missing_judgement_is_not_a_refutation() -> None:
    """None = the judgement call failed; the finding survives unruled and visible."""
    merged = apply_judgements([_FINDING], [None])
    assert merged[0].verdict is None
    assert merged[0].impact_score is None
    assert visible_findings(merged) == merged


def test_threshold_hides_low_impact_but_keeps_it_in_the_list() -> None:
    """Below-threshold findings are hidden from the report, never dropped from the result."""
    low = _FINDING.model_copy(update={"verdict": "confirmed", "impact_score": 1})
    high = _FINDING.model_copy(update={"verdict": "confirmed", "impact_score": 8, "title": "x"})
    assert visible_findings([low, high], threshold=3) == [high]
    assert visible_findings([low, high]) == [low, high]  # default 0 hides nothing


def test_unjudged_finding_survives_a_threshold() -> None:
    """An unjudged finding has no score to compare; a threshold must not hide it."""
    assert visible_findings([_FINDING], threshold=5) == [_FINDING]


def test_judgement_prompt_hides_the_finders_self_assessment() -> None:
    """confidence and severity are the finder's guess at what this pass re-derives."""
    prompt = build_judgement_prompt(_FINDING, "@@ -1 +1 @@\n+x", evidence=None)
    assert "off-by-one" in prompt  # the claim itself is shown
    assert "range(n + 1)" in prompt  # and its evidence
    assert "85" not in prompt  # but not the finder's confidence
    assert "major" not in prompt  # nor its severity


def test_judgement_prompt_states_out_of_hunk_claims_are_uncertain() -> None:
    """Narrow context must not become a false-refutation generator (#246 §3.3)."""
    prompt = build_judgement_prompt(_FINDING, "@@ -1 +1 @@\n+x", evidence=None)
    assert "cannot check it" in prompt
    assert "never for 'refuted'" in prompt


def test_judgement_prompt_carries_the_impact_rubric() -> None:
    """The importance axis needs its anchors, including the tooling rule."""
    prompt = build_judgement_prompt(_FINDING, "", evidence=None)
    assert "impact_score 0-10" in prompt
    assert "mypy --strict" in prompt


@pytest.mark.asyncio
async def test_judge_finding_parses_a_judgement() -> None:
    """A well-formed provider response becomes a FindingJudgement."""
    provider = StubProvider(
        ['{"verdict": "confirmed", "impact_score": 7, "rationale": "real off-by-one"}']
    )
    judgement = await judge_finding(provider, _FINDING, "@@ -1 +1 @@\n+x", evidence=None)
    assert judgement is not None
    assert judgement.verdict == "confirmed"
    assert judgement.impact_score == 7


@pytest.mark.asyncio
async def test_judge_finding_returns_none_on_unparseable_response() -> None:
    """A failed call yields None — the caller keeps the finding unruled, never drops it."""
    assert await judge_finding(StubProvider(["not json"]), _FINDING, "", evidence=None) is None


@pytest.mark.asyncio
async def test_judge_finding_returns_none_when_the_provider_raises() -> None:
    """Provider errors are contained per finding (#246 §3.4)."""
    assert await judge_finding(BoomProvider(), _FINDING, "", evidence=None) is None


@pytest.mark.asyncio
async def test_judge_all_returns_one_result_per_finding_in_order() -> None:
    """Positional contract: judgements[i] rules on findings[i]."""
    provider = StubProvider(
        [
            '{"verdict": "confirmed", "impact_score": 8, "rationale": "a"}',
            '{"verdict": "refuted", "impact_score": 0, "rationale": "b"}',
        ]
    )
    findings = [_FINDING, _FINDING.model_copy(update={"title": "second"})]

    judgements = await judge_all(provider, findings, "", concurrency=1, evidence=None)

    assert [j.verdict for j in judgements if j] == ["confirmed", "refuted"]


@pytest.mark.asyncio
async def test_judge_all_isolates_a_failing_call() -> None:
    """One bad response costs one verdict, not the whole pass."""
    provider = StubProvider(
        ["not json", '{"verdict": "confirmed", "impact_score": 6, "rationale": "ok"}']
    )
    findings = [_FINDING, _FINDING.model_copy(update={"title": "second"})]

    judgements = await judge_all(provider, findings, "", concurrency=1, evidence=None)

    assert judgements[0] is None
    assert judgements[1] is not None


@pytest.mark.asyncio
async def test_judge_all_feeds_each_finding_only_its_own_file_hunks() -> None:
    """Per-finding context is the point of the isolation (#246 §3.3)."""
    provider = StubProvider(['{"verdict": "confirmed", "impact_score": 5, "rationale": "x"}'])
    diff = (
        "diff --git a/src/cgis/cli.py b/src/cgis/cli.py\n"
        "--- a/src/cgis/cli.py\n+++ b/src/cgis/cli.py\n"
        "@@ -1,1 +1,1 @@\n-old\n+cli_line\n"
        "diff --git a/other.py b/other.py\n"
        "--- a/other.py\n+++ b/other.py\n"
        "@@ -1,1 +1,1 @@\n-x\n+other_line\n"
    )

    await judge_all(provider, [_FINDING], diff, concurrency=1, evidence=None)

    assert "cli_line" in provider.prompts[0]
    assert "other_line" not in provider.prompts[0]


@pytest.mark.asyncio
async def test_judge_all_never_exceeds_the_concurrency_limit() -> None:
    """The semaphore is what keeps mistral's per-minute token cap out of reach."""

    class _ConcurrencyProbe(BaseProvider):
        """Counts how many judgement calls are in flight at once."""

        name: ClassVar[str] = "gemini"

        def __init__(self) -> None:
            """Start with no calls in flight."""
            super().__init__()
            self.active = 0
            self.peak = 0

        async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
            """Not used in tests."""
            raise NotImplementedError

        async def generate_structured(
            self,
            system_prompt: str,  # noqa: ARG002
            user_prompt: str,  # noqa: ARG002
            schema: type[BaseModel],  # noqa: ARG002
        ) -> str:
            """Track overlap, yielding so concurrent calls can pile up."""
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0)
            self.active -= 1
            return '{"verdict": "confirmed", "impact_score": 5, "rationale": "x"}'

    probe = _ConcurrencyProbe()

    await judge_all(probe, [_FINDING] * 10, "", concurrency=3, evidence=None)

    assert probe.peak <= 3
    assert probe.peak > 1  # and it really is concurrent, not accidentally serial


class TestEvidenceInTheJudgementPrompt:
    """Static checker output, and the narrow licence it grants (#401)."""

    def test_no_evidence_says_so_instead_of_saying_nothing(self) -> None:
        """The gap the model was filling itself (#407).

        This test used to assert the opposite — that an absent checker left the
        prompt untouched, on the reasoning that an unsupported repository must
        lose nothing. Measuring #401 showed what it gained instead: judging the
        same 24 findings with no checker output, **6 rationales cited mypy or
        ruff anyway**, every one to confirm a false claim on a conditional it
        could not check. Silence is not neutral.
        """
        prompt = build_judgement_prompt(_FINDING, "@@ -1 +1 @@\n+x", evidence=None)
        assert "NO CHECKER OUTPUT IS AVAILABLE" in prompt

    def test_the_no_evidence_notice_names_the_verdict_rather_than_forbidding_one(
        self,
    ) -> None:
        """A directive, because the prohibition was measured and did not work.

        The first version said "do not rest a verdict on what a checker would
        report". On the same 24 findings, 5 of 6 checker-appeals survived and
        the language grew *more* assertive: a conditional became a flat claim
        about a "mandatory `mypy --strict` gate" that does not exist as
        described. Told what not to do, the model complied in form and confirmed
        anyway — so the prompt now says which verdict such a claim takes.
        """
        prompt = build_judgement_prompt(_FINDING, "@@ -1 +1 @@\n+x", evidence=None)
        assert "Mark it\n'uncertain'." in prompt or "Mark it 'uncertain'." in prompt

    def test_the_no_evidence_notice_rules_out_both_other_verdicts(self) -> None:
        """Symmetry is the recall guard, and it is a sentence rather than a code path.

        Naming only the confirming direction would leave "the linter would have
        caught it" available as a refutation — buying precision with recall, the
        trade #246 records for a skeptic tuned too aggressively.
        """
        prompt = build_judgement_prompt(_FINDING, "@@ -1 +1 @@\n+x", evidence=None)
        assert "Not 'confirmed'" in prompt
        assert "Not 'refuted'" in prompt

    def test_no_evidence_presents_no_checker_verdict(self) -> None:
        """The notice names checkers; it must not appear to report one.

        A section that mentioned mypy and looked like output would be worse than
        the silence it replaces — the model would have something to quote.
        """
        prompt = build_judgement_prompt(_FINDING, "@@ -1 +1 @@\n+x", evidence=None)
        assert "WHAT THIS REPOSITORY'S OWN CHECKERS REPORT" not in prompt
        assert "Success: no issues" not in prompt

    def test_evidence_appears_with_the_commands_that_produced_it(self) -> None:
        """The model is asked to treat this as disproof, so it must be re-runnable."""
        evidence = Evidence(commands=("uv run mypy --strict a.py",), output="Success: no issues")
        prompt = build_judgement_prompt(_FINDING, "@@ -1 +1 @@\n+x", evidence=evidence)
        assert "uv run mypy --strict a.py" in prompt
        assert "Success: no issues" in prompt

    def test_the_licence_to_refute_is_narrow(self) -> None:
        """Silence from mypy does not disprove a logic bug.

        This is the whole hazard of the feature. A clean type check refutes
        "mypy would reject this" and nothing else; read as general absolution it
        would refute real findings that the checkers were never asked about —
        turning a precision fix into a recall regression.

        Asserted on the prompt text because the instruction *is* the mechanism.
        There is no code path to test: the narrowing lives in the sentence.
        """
        evidence = Evidence(commands=("uv run ruff check a.py",), output="All checks passed!")
        prompt = build_judgement_prompt(_FINDING, "", evidence=evidence)
        assert "only" in prompt.lower()
        assert "does not" in prompt.lower()
        # The claim-about-the-checker framing, not "the checkers found nothing".
        assert "about what these checkers report" in prompt

    def test_the_existing_refutation_bar_is_not_restated_lower(self) -> None:
        """The system prompt still governs; evidence adds a means, not a lower bar."""
        assert "ONLY when you can point to concrete evidence" in SKEPTIC_SYSTEM_PROMPT
