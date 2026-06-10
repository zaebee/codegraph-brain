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
    """Render the full review; refuted findings are hidden but never silently."""
    if result.parse_failed:
        return (
            "⚠️ Guardian could not produce structured output; raw response below.\n\n"
            + result.summary
        )
    visible = visible_findings(result.findings)
    refuted_count = len(result.findings) - len(visible)
    notes: list[str] = []
    if refuted_count:
        plural = "finding was" if refuted_count == 1 else "findings were"
        notes.append(f"_{refuted_count} {plural} refuted by the skeptic pass._")
    if result.skeptic_status == "failed":
        notes.append("_Skeptic pass failed; findings are single-pass._")
    suffix = ("\n\n" + "\n".join(notes)) if notes else ""
    if not visible:
        return f"LGTM — no defects found in this diff.\n\n{result.summary}{suffix}"
    ordered = sorted(visible, key=lambda f: _SEVERITY_ORDER[f.severity])
    blocks = [render_finding(f) for f in ordered]
    return "\n\n".join(blocks) + f"\n\n---\n**Summary:** {result.summary}{suffix}"
