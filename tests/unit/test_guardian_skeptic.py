"""Unit tests for skeptic verdict models and the pure merge logic (spec §5)."""

from cgis.guardian.findings import Finding
from cgis.guardian.skeptic import (
    FindingJudgement,
    SkepticResult,
    SkepticVerdict,
    apply_judgements,
    apply_verdicts,
    build_skeptic_prompt,
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


def _verdict(index: int, verdict: str, rationale: str = "because") -> SkepticVerdict:
    return SkepticVerdict(finding_index=index, verdict=verdict, rationale=rationale)  # type: ignore[arg-type]


def test_confirmed_sets_verdict_and_note() -> None:
    """confirmed → verdict + skeptic_note on a new frozen copy."""
    merged = apply_verdicts([_FINDING], SkepticResult(verdicts=[_verdict(0, "confirmed")]))
    assert merged[0].verdict == "confirmed"
    assert merged[0].skeptic_note == "because"
    assert _FINDING.verdict is None  # original untouched (frozen)


def test_refuted_marks_but_keeps_finding() -> None:
    """refuted → marked, kept in the list (metrics must see killed findings)."""
    merged = apply_verdicts([_FINDING], SkepticResult(verdicts=[_verdict(0, "refuted")]))
    assert merged[0].verdict == "refuted"


def test_uncertain_discounts_confidence_but_keeps_finding() -> None:
    """uncertain → confidence x0.9 (ranking signal), verdict stays uncertain."""
    f = _FINDING.model_copy(update={"confidence": 89})
    merged = apply_verdicts([f], SkepticResult(verdicts=[_verdict(0, "uncertain")]))
    assert merged[0].verdict == "uncertain"
    assert merged[0].confidence == 80


def test_uncertain_low_confidence_is_kept_not_refuted() -> None:
    """Recall-lean: a low-confidence uncertain finding survives (no gate auto-refute)."""
    f = _FINDING.model_copy(update={"confidence": 30})
    merged = apply_verdicts([f], SkepticResult(verdicts=[_verdict(0, "uncertain")]))
    assert merged[0].verdict == "uncertain"
    assert merged[0].confidence == 27  # round(30 * 0.9)
    assert visible_findings(merged) == merged  # not dropped


def test_out_of_range_and_duplicate_indices_discarded() -> None:
    """Out-of-range or duplicate finding_index verdicts are dropped, not applied."""
    verdicts = SkepticResult(
        verdicts=[_verdict(5, "refuted"), _verdict(0, "confirmed"), _verdict(0, "refuted")]
    )
    merged = apply_verdicts([_FINDING], verdicts)
    assert merged[0].verdict == "confirmed"  # first valid verdict wins; duplicate ignored


def test_unruled_finding_keeps_none_verdict() -> None:
    """A finding the skeptic never ruled on keeps verdict=None and is not filtered."""
    merged = apply_verdicts([_FINDING], SkepticResult(verdicts=[]))
    assert merged[0].verdict is None
    assert visible_findings(merged) == merged


def test_visible_findings_drops_only_refuted() -> None:
    """visible_findings filters refuted; confirmed/uncertain/None stay."""
    kept = _FINDING.model_copy(update={"verdict": "confirmed"})
    dropped = _FINDING.model_copy(update={"verdict": "refuted", "title": "x"})
    assert visible_findings([kept, dropped]) == [kept]


def test_skeptic_prompt_contains_findings_and_stance() -> None:
    """The skeptic prompt lists indexed findings and the confirm-by-default stance."""
    prompt = build_skeptic_prompt({"diff": "the-diff"}, [_FINDING])
    assert "the-diff" in prompt
    assert "[0]" in prompt
    assert "off-by-one" in prompt
    assert "confirm what you cannot disprove" in prompt


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
