"""Cross-model skeptic pass: verdict models, prompt, and pure merge logic (spec §5)."""

from collections.abc import Iterable
from typing import Literal

import structlog
from pydantic import BaseModel

from cgis.guardian.findings import Finding

log = structlog.getLogger(__name__)

_CONFIDENCE_GATE = 80
_UNCERTAIN_MULTIPLIER = 0.9  # keeps original confidence >= 89; smaller would
# refute EVERY uncertain finding and make the branch dead code (spec §5.3).


class SkepticVerdict(BaseModel, frozen=True):
    """The skeptic's ruling on one pass-1 finding, addressed by list index."""

    finding_index: int
    verdict: Literal["confirmed", "refuted", "uncertain"]
    rationale: str


class SkepticResult(BaseModel, frozen=True):
    """All verdicts from one skeptic call (spec §5.2: one call, not N)."""

    verdicts: list[SkepticVerdict]


# Confirm-by-default stance. The original refute-by-default wording over-killed
# in benchmarks: a gemini-3.5-flash skeptic refuted 7/7 finder findings,
# including 2 ground-truth matches on PR 122 (gate allows at most 1 lost match).
SKEPTIC_SYSTEM_PROMPT = (
    "You are a skeptical senior reviewer double-checking another reviewer's findings. "
    "For each finding, verify the quoted evidence against the diff and judge whether "
    "the claimed defect is real. Refute a finding ONLY when you can point to concrete "
    "evidence that it is wrong: the quoted code does not appear in the diff, the case "
    "is already handled, or the claim misreads what the code does. "
    "If a finding is plausible and you cannot disprove it, confirm it — the finder "
    "already applied a confidence gate; your job is to catch its hallucinations, "
    "not to second-guess its judgement calls."
)


def build_skeptic_prompt(context: dict[str, str], findings: list[Finding]) -> str:
    """Assemble the skeptic user prompt: same diff context + indexed findings list."""
    listed = "\n".join(
        f"[{i}] {f.severity} {f.category} at {f.file}:{f.line} — {f.title}\n"
        f"    evidence: {f.evidence}\n    problem: {f.problem}"
        for i, f in enumerate(findings)
    )
    return f"""Another reviewer produced the findings below for this diff.
Check each one against the diff: refute only with concrete contrary evidence;
confirm what you cannot disprove.

### DIFF
{context.get("diff", "")}

### FULL FILE CONTENTS (if available)
{context.get("full_files", "")}

### FINDINGS TO VERIFY
{listed}

### OUTPUT FORMAT
Return ONLY a JSON object: {{"verdicts": [{{"finding_index": 0,
"verdict": "confirmed|refuted|uncertain", "rationale": "one sentence"}}]}}
Rule on every finding exactly once, by its [index]."""


def apply_verdicts(findings: list[Finding], skeptic: SkepticResult) -> list[Finding]:
    """Merge skeptic verdicts into new frozen Finding copies (spec §5.3).

    Out-of-range / duplicate indices are discarded and logged. Unruled findings
    keep verdict=None. uncertain discounts confidence x0.9; below the 80 gate
    it becomes refuted.
    """
    by_index: dict[int, SkepticVerdict] = {}
    for v in skeptic.verdicts:
        if not 0 <= v.finding_index < len(findings):
            log.warning("Skeptic verdict index out of range; discarded.", index=v.finding_index)
            continue
        if v.finding_index in by_index:
            log.warning("Duplicate skeptic verdict index; discarded.", index=v.finding_index)
            continue
        by_index[v.finding_index] = v

    merged: list[Finding] = []
    for i, finding in enumerate(findings):
        verdict = by_index.get(i)
        if verdict is None:
            merged.append(finding)  # absence of a verdict is not a refutation
            continue
        if verdict.verdict == "uncertain":
            discounted = round(finding.confidence * _UNCERTAIN_MULTIPLIER)
            final = "refuted" if discounted < _CONFIDENCE_GATE else "uncertain"
            merged.append(
                finding.model_copy(
                    update={
                        "verdict": final,
                        "skeptic_note": verdict.rationale,
                        "confidence": discounted,
                    }
                )
            )
            continue
        merged.append(
            finding.model_copy(
                update={"verdict": verdict.verdict, "skeptic_note": verdict.rationale}
            )
        )
    return merged


def visible_findings(findings: Iterable[Finding]) -> list[Finding]:
    """Findings that appear in the rendered report: everything not refuted."""
    return [f for f in findings if f.verdict != "refuted"]
