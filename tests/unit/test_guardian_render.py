"""Golden tests for ReviewResult → markdown rendering (spec §2.5)."""

from cgis.guardian.findings import Finding, ReviewResult
from cgis.guardian.render import render_finding, render_report

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
