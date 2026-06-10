"""Pure rendering of ReviewResult into the PR-comment markdown (spec §2.5)."""

from cgis.guardian.findings import Finding, ReviewResult

_SEVERITY_MARKER = {"critical": "🔴", "major": "🟠", "minor": "🟡"}
_CATEGORY_LABEL = {
    "logic": "Logic Bug",
    "contract": "Library Contract",
    "tests": "Test Coverage",
    "types": "Type Safety",
    "ontology": "Ontology",
}


def render_finding(finding: Finding) -> str:
    """Render one finding in the **[Category] — title** block format."""
    location = (
        f"`{finding.file}:{finding.line}`" if finding.line is not None else f"`{finding.file}`"
    )
    lines = [
        f"**[{_CATEGORY_LABEL[finding.category]}] — {finding.title}**",
        f"{_SEVERITY_MARKER[finding.severity]} {finding.severity} at {location}"
        f" · Confidence: {finding.confidence}%",
        f"Lines: `` {finding.evidence} ``",
        f"Problem: {finding.problem}",
        f"Fix: {finding.fix}",
    ]
    if finding.verdict is not None:
        note = f" — {finding.skeptic_note}" if finding.skeptic_note else ""
        lines.append(f"Skeptic: {finding.verdict}{note}")
    return "\n".join(lines)


def render_report(result: ReviewResult) -> str:
    """Render the full review; visually matches the pre-structured format."""
    if result.parse_failed:
        return (
            "⚠️ Guardian could not produce structured output; raw response below.\n\n"
            + result.summary
        )
    if not result.findings:
        return f"LGTM — no defects found in this diff.\n\n{result.summary}"
    blocks = [render_finding(f) for f in result.findings]
    return "\n\n".join(blocks) + f"\n\n---\n**Summary:** {result.summary}"
