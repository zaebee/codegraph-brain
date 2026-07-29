"""Golden tests for ReviewResult → markdown rendering (spec §2.5)."""

from cgis.guardian.findings import Finding, ReviewResult
from cgis.guardian.render import (
    render_finding,
    render_inline_comment,
    render_report,
    render_review_body,
)

_FINDING = Finding(
    file="src/cgis/cli.py",
    line=42,
    severity="major",
    category="logic",
    title="off-by-one in pagination",
    evidence="for i in range(n + 1):",
    problem="iterates one element past the end.",
    fix="use range(n).",
    confidence=85,
)


def test_render_finding_contains_all_fields() -> None:
    """Header keeps the **[Category] — title** shape; all fields present."""
    text = render_finding(_FINDING)
    assert text.startswith("**[Logic Bug] — off-by-one in pagination**")
    assert "🟠" in text
    assert "`src/cgis/cli.py:42`" in text
    assert "for i in range(n + 1):" in text
    assert "`` for i in range(n + 1): ``" in text
    assert "Confidence: 85%" in text
    assert "Fix: use range(n)." in text


def test_render_finding_file_level_without_line() -> None:
    """line=None renders the bare file path."""
    text = render_finding(_FINDING.model_copy(update={"line": None}))
    assert "`src/cgis/cli.py`" in text
    assert ":None" not in text


def test_render_finding_evidence_with_backtick_survives() -> None:
    """Evidence containing a single backtick stays inside the code span."""
    f = _FINDING.model_copy(update={"evidence": "x = `y`"})
    text = render_finding(f)
    assert "`` x = `y` ``" in text


def test_render_finding_with_skeptic_verdict() -> None:
    """A confirmed verdict adds the Verified line (used from the multi-pass step)."""
    f = _FINDING.model_copy(update={"verdict": "confirmed", "skeptic_note": "reproduced"})
    text = render_finding(f)
    assert "Skeptic: confirmed — reproduced" in text


def test_render_report_lgtm() -> None:
    """Empty findings render the canonical LGTM line plus the summary."""
    text = render_report(ReviewResult(findings=[], summary="Checked A and B."))
    assert text.startswith("LGTM — no defects found in this diff.")
    assert "Checked A and B." in text


def test_render_report_parse_failed() -> None:
    """parse_failed renders the raw text with an explicit warning header."""
    text = render_report(ReviewResult(findings=[], summary="raw blob", parse_failed=True))
    assert text.startswith("⚠️ Guardian could not produce structured output")
    assert "raw blob" in text


def test_render_report_findings_and_summary() -> None:
    """Findings are joined and the summary lands in a trailing section."""
    text = render_report(ReviewResult(findings=[_FINDING], summary="checked X"))
    assert "**[Logic Bug] — off-by-one in pagination**" in text
    assert "**Summary:** checked X" in text
    assert "\n\n---\n**Summary:** checked X" in text


def test_render_report_sorts_by_severity() -> None:
    """Findings render critical -> major -> minor regardless of LLM order."""
    result = ReviewResult(
        findings=[
            _FINDING.model_copy(update={"severity": "minor", "title": "m3"}),
            _FINDING.model_copy(update={"severity": "critical", "title": "m1"}),
            _FINDING.model_copy(update={"severity": "major", "title": "m2"}),
        ],
        summary="s",
    )
    report = render_report(result)
    assert report.index("m1") < report.index("m2") < report.index("m3")


def test_render_report_hides_refuted_findings() -> None:
    """Refuted findings stay in the model (for metrics) but vanish from the report."""
    refuted = _FINDING.model_copy(update={"verdict": "refuted", "title": "killed"})
    kept = _FINDING.model_copy(update={"verdict": "confirmed"})
    text = render_report(ReviewResult(findings=[refuted, kept], summary="s"))
    assert "killed" not in text
    assert "off-by-one in pagination" in text


def test_render_report_all_refuted_is_lgtm_with_note() -> None:
    """All findings refuted → LGTM line plus an explicit skeptic note (never silent)."""
    refuted = _FINDING.model_copy(update={"verdict": "refuted"})
    text = render_report(ReviewResult(findings=[refuted], summary="s", skeptic_status="ok"))
    assert text.startswith("LGTM")
    assert "1 finding was refuted by the skeptic pass" in text


def test_render_report_notes_skeptic_failure() -> None:
    """skeptic_status='failed' adds a visible degradation note (spec §7: never silent)."""
    text = render_report(ReviewResult(findings=[_FINDING], summary="s", skeptic_status="failed"))
    assert "Skeptic pass failed; findings are single-pass." in text


def test_render_inline_comment_fields() -> None:
    """One inline comment = marker, category, problem, fix, verified line."""
    f = _FINDING.model_copy(update={"verdict": "confirmed", "skeptic_note": "checked"})
    text = render_inline_comment(f, skeptic_model="gemini-2.5-flash")
    assert text.startswith("🟠 **[Logic Bug] — off-by-one in pagination**")
    assert "iterates one element past the end." in text
    assert "Fix: use range(n)." in text
    assert "Verified by gemini-2.5-flash" in text


def test_render_inline_comment_unverified_has_no_verified_line() -> None:
    """No confirmed verdict → no Verified line."""
    text = render_inline_comment(_FINDING, skeptic_model=None)
    assert "Verified by" not in text


def test_render_review_body_with_out_of_diff_findings() -> None:
    """Out-of-diff findings land in a dedicated section of the review body."""
    outside = _FINDING.model_copy(update={"line": None, "title": "file-level issue"})
    body = render_review_body(
        ReviewResult(findings=[outside], summary="checked X"), outside=[outside]
    )
    assert "Findings outside the diff" in body
    assert "file-level issue" in body
    assert "checked X" in body


def test_render_review_body_lgtm() -> None:
    """No findings at all → the canonical LGTM body."""
    body = render_review_body(ReviewResult(findings=[], summary="all good"), outside=[])
    assert body.startswith("LGTM")


# ---------------------------------------------------------------------------
# Impact ranking and threshold (#246)
# ---------------------------------------------------------------------------


def _scored(severity: str, title: str, impact_score: int | None) -> Finding:
    """A confirmed finding carrying an impact score."""
    return _FINDING.model_copy(
        update={
            "severity": severity,
            "title": title,
            "verdict": "confirmed",
            "impact_score": impact_score,
        }
    )


def test_report_orders_by_impact_before_severity() -> None:
    """A scored report ranks by what matters, not by the finder's severity guess."""
    low = _scored("critical", "loud but trivial", 1)
    high = _scored("minor", "quiet but real", 9)

    body = render_report(ReviewResult(findings=[low, high], summary="s"))

    assert body.index("quiet but real") < body.index("loud but trivial")


def test_report_shows_the_impact_score() -> None:
    """A human must see the ranking signal, not just its effect."""
    body = render_report(ReviewResult(findings=[_scored("major", "t", 7)], summary="s"))

    assert "Impact: 7/10" in body


def test_report_notes_findings_hidden_by_the_threshold() -> None:
    """Hiding is never silent — same rule the refuted counter already follows."""
    findings = [_scored("major", "trivial", 1), _scored("major", "real", 9)]

    body = render_report(ReviewResult(findings=findings, summary="s"), threshold=5)

    assert "real" in body
    assert "1 finding was below the impact threshold" in body


def test_report_notes_a_partial_skeptic_pass() -> None:
    """partial must read as partial, not as a clean pass."""
    body = render_report(
        ReviewResult(
            findings=[_scored("major", "t", 8)],
            summary="s",
            skeptic_status="partial",
            skeptic_judged=1,
            skeptic_total=3,
        )
    )

    assert "1 of 3" in body


def test_unscored_findings_sort_after_scored_ones() -> None:
    """An unjudged finding has no ranking signal; it must not outrank a scored one."""
    unscored = _FINDING.model_copy(update={"title": "unjudged", "severity": "critical"})
    scored = _scored("minor", "judged high", 9)

    body = render_report(ReviewResult(findings=[unscored, scored], summary="s"))

    assert body.index("judged high") < body.index("unjudged")
