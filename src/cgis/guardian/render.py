"""Pure rendering of ReviewResult into the PR-comment markdown (spec §2.5)."""

from cgis.guardian.findings import Finding, ReviewResult
from cgis.guardian.skeptic import visible_findings

_SEVERITY_MARKER = {"critical": "🔴", "major": "🟠", "minor": "🟡"}
_SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2}
_CATEGORY_LABEL = {
    "logic": "Logic Bug",
    "contract": "Library Contract",
    "tests": "Test Coverage",
    "types": "Type Safety",
    "ontology": "Ontology",
    "security": "Security",
}


def _rank_key(finding: Finding) -> tuple[int, int]:
    """Sort key: impact descending, then severity (#246 §3.5).

    An unjudged finding scores -1 rather than 0 so it sorts after every scored
    one — no score is "no ranking signal", not "scored zero".
    """
    impact = finding.impact_score if finding.impact_score is not None else -1
    return (-impact, _SEVERITY_ORDER[finding.severity])


def render_finding(finding: Finding) -> str:
    """Render one finding in the **[Category] — title** block format."""
    location = (
        f"`{finding.file}:{finding.line}`" if finding.line is not None else f"`{finding.file}`"
    )
    impact = f" · Impact: {finding.impact_score}/10" if finding.impact_score is not None else ""
    lines = [
        f"**[{_CATEGORY_LABEL[finding.category]}] — {finding.title}**",
        f"{_SEVERITY_MARKER[finding.severity]} {finding.severity} at {location}"
        f" · Confidence: {finding.confidence}%{impact}",
        f"Lines: `` {finding.evidence} ``",
        f"Problem: {finding.problem}",
        f"Fix: {finding.fix}",
    ]
    if finding.verdict is not None:
        note = f" — {finding.skeptic_note}" if finding.skeptic_note else ""
        lines.append(f"Skeptic: {finding.verdict}{note}")
    return "\n".join(lines)


def render_inline_comment(finding: Finding, *, skeptic_model: str | None) -> str:
    """One finding as a standalone inline comment body (spec §6.3)."""
    lines = [
        f"{_SEVERITY_MARKER[finding.severity]} "
        f"**[{_CATEGORY_LABEL[finding.category]}] — {finding.title}**",
        f"{finding.problem}",
        f"Fix: {finding.fix}",
    ]
    if finding.verdict == "confirmed" and skeptic_model:
        lines.append(f"_Verified by {skeptic_model}_")
    return "\n\n".join(lines)


def render_review_body(result: ReviewResult, *, outside: list[Finding], threshold: int = 0) -> str:
    """The review's top-level body: summary plus any out-of-diff findings (spec §6.3)."""
    if not visible_findings(result.findings, threshold):
        return render_report(result, threshold)
    parts: list[str] = []
    if outside:
        ordered = sorted(outside, key=_rank_key)
        blocks = "\n\n".join(render_finding(f) for f in ordered)
        parts.append(f"### Findings outside the diff\n\n{blocks}")
    parts.append(f"**Summary:** {result.summary}")
    return "\n\n".join(parts)


def render_report(result: ReviewResult, threshold: int = 0) -> str:
    """Render the full review; hidden findings are never hidden silently.

    ``threshold`` suppresses findings the skeptic scored below it (#246 §3.5).
    Both kinds of suppression — refuted and below-threshold — are counted in the
    notes, and both stay in ``result.findings`` for metrics.
    """
    if result.parse_failed:
        return (
            "⚠️ Guardian could not produce structured output; raw response below.\n\n"
            + result.summary
        )
    unrefuted = visible_findings(result.findings)
    visible = visible_findings(result.findings, threshold)
    refuted_count = len(result.findings) - len(unrefuted)
    below_threshold = len(unrefuted) - len(visible)
    notes: list[str] = []
    if refuted_count:
        plural = "finding was" if refuted_count == 1 else "findings were"
        notes.append(f"_{refuted_count} {plural} refuted by the skeptic pass._")
    if below_threshold:
        plural = "finding was" if below_threshold == 1 else "findings were"
        notes.append(f"_{below_threshold} {plural} below the impact threshold ({threshold})._")
    if result.skeptic_status == "failed":
        notes.append("_Skeptic pass failed; findings are single-pass._")
    if result.skeptic_status == "partial":
        notes.append(
            f"_Skeptic judged {result.skeptic_judged} of {result.skeptic_total} findings; "
            "the rest are single-pass._"
        )
    suffix = ("\n\n" + "\n".join(notes)) if notes else ""
    if not visible:
        return f"LGTM — no defects found in this diff.\n\n{result.summary}{suffix}"
    blocks = [render_finding(f) for f in sorted(visible, key=_rank_key)]
    return "\n\n".join(blocks) + f"\n\n---\n**Summary:** {result.summary}{suffix}"
