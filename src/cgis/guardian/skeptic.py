"""Skeptic pass: one judgement per finding on two axes (spec §5, amended by #246).

Each finding gets its own LLM call carrying only its file's hunks, and comes
back with a ``verdict`` (is the claim true) and an ``impact_score`` 0-10 (does
it matter). The batch call this replaced put every finding in one window and
addressed verdicts by list index; it could neither rank nor survive a partial
failure.
"""

import asyncio
from collections.abc import Iterable
from typing import Literal

import structlog
from pydantic import BaseModel, Field

from cgis.guardian.diff_index import split_diff_by_file
from cgis.guardian.findings import Finding, extract_json
from cgis.guardian.providers.base import BaseProvider

log = structlog.getLogger(__name__)

_UNCERTAIN_MULTIPLIER = 0.9  # an 'uncertain' verdict discounts confidence as a
# ranking signal only — it NEVER refutes. The recall-lean finder emits genuine
# low-confidence findings on purpose; only an explicit 'refuted' verdict drops one.


class FindingJudgement(BaseModel, frozen=True):
    """One skeptic ruling on one finding, on two orthogonal axes (#246 spec §3.1).

    ``verdict`` answers "is this claim true" and only an explicit 'refuted'
    drops the finding. ``impact_score`` answers "does it matter" and only hides
    below a threshold. There is no index: a judgement belongs to the call that
    produced it, which is why the batch API's index-mapping failure modes
    (out-of-range, duplicate) cannot occur here.
    """

    verdict: Literal["confirmed", "refuted", "uncertain"]
    impact_score: int = Field(ge=0, le=10)
    rationale: str


# Confirm-by-default stance. The original refute-by-default wording over-killed
# in benchmarks: a gemini-3.5-flash skeptic refuted 7/7 finder findings,
# including 2 ground-truth matches on PR 122 (gate allows at most 1 lost match).
# The finder is now recall-lean (no confidence gate, no cap), so it surfaces
# genuine low-confidence findings BY DESIGN and leans on this pass for precision —
# the skeptic must cut hallucinations without re-introducing the gate it removed.
SKEPTIC_SYSTEM_PROMPT = (
    "You are a skeptical senior reviewer double-checking another reviewer's findings. "
    "That reviewer optimises for RECALL: it deliberately surfaces plausible, sometimes "
    "low-confidence candidates and relies on you to remove only the ones that are wrong. "
    "For each finding, verify the quoted evidence against the diff and judge whether the "
    "claimed defect is real. Refute a finding ONLY when you can point to concrete evidence "
    "that it is wrong: the quoted code does not appear in the diff, the case is already "
    "handled, or the claim misreads what the code does. Do NOT refute a finding merely for "
    "being low-confidence, speculative, or a judgement call — if it is plausible and you "
    "cannot disprove it, mark it 'confirmed' or 'uncertain' (both are kept), never 'refuted'."
)


IMPACT_RUBRIC = """
Also rate how much this finding MATTERS, independent of whether it is true,
as impact_score 0-10:

- 0-2  true but not actionable: style, taste, "consider X for explicitness",
       or restating something the project's tooling already enforces.
       If `ruff`, `ruff format` or `mypy --strict` would catch it, score <= 2:
       those run as mandatory gates in this repo, so such issues are already
       covered.
- 3-5  a minor real issue: local clarity or robustness, no behaviour change.
- 6-8  a real defect with a concrete failure path in this diff.
- 9-10 a broken contract, a security hole, or data loss.

A true finding can score 0. Scoring is NOT a second chance to refute: judge
truth and importance independently."""


def build_judgement_prompt(finding: Finding, hunks: str) -> str:
    """Assemble the user prompt judging ONE finding against its own file's hunks.

    The finder's ``confidence`` and ``severity`` are deliberately omitted: both
    are its own guess at what this pass re-derives independently, and showing
    them anchors the judge on the claim it is meant to check (#246 §3.3).
    """
    location = f"{finding.file}:{finding.line}" if finding.line is not None else finding.file
    return f"""Another reviewer claims this diff contains a defect.

### THE CLAIM
Location: {location}
Title: {finding.title}
Quoted code: {finding.evidence}
Problem: {finding.problem}
Proposed fix: {finding.fix}

### THE DIFF HUNKS FOR THAT FILE
{hunks or "(no hunks available for this file)"}

### HOW TO JUDGE
Verify the quoted code against the hunks above. If the claim depends on code
that is NOT in these hunks, you cannot check it: that is grounds for
'uncertain', never for 'refuted'.
{IMPACT_RUBRIC}

### OUTPUT FORMAT
Return ONLY a JSON object:
{{"verdict": "confirmed|refuted|uncertain", "impact_score": 0, "rationale": "one sentence"}}"""


DEFAULT_SKEPTIC_CONCURRENCY = 3
# Bounded because provider rate limits are the binding constraint, not local CPU:
# mistral's free tier caps tokens per MINUTE, and a local ollama skeptic
# serialises on one model instance anyway.


async def judge_finding(
    provider: BaseProvider, finding: Finding, hunks: str
) -> FindingJudgement | None:
    """Judge one finding; None means this call failed (#246 §3.2).

    Every failure mode — transport error, rate limit, unparseable output —
    collapses to None so the caller keeps the finding unruled and visible. A
    skeptic that cannot answer must never be able to silence a finding.
    """
    try:
        raw = await provider.generate_structured(
            SKEPTIC_SYSTEM_PROMPT, build_judgement_prompt(finding, hunks), FindingJudgement
        )
        return FindingJudgement.model_validate_json(extract_json(raw))
    except Exception:
        log.warning("Skeptic judgement failed; finding stays unruled.", file=finding.file)
        return None


def skeptic_status_for(judged: int, total: int) -> Literal["ok", "partial", "failed"]:
    """Map judged/total onto the reported skeptic status (#246 §3.4).

    Public and shared: the chunked orchestrator reports the same statuses, and
    two copies of this mapping would be free to drift apart.
    """
    if judged == 0:
        return "failed"
    return "ok" if judged == total else "partial"


async def judge_all(
    provider: BaseProvider,
    findings: list[Finding],
    diff: str,
    concurrency: int = DEFAULT_SKEPTIC_CONCURRENCY,
) -> list[FindingJudgement | None]:
    """Judge every finding concurrently, each against its own file's hunks.

    Returns one entry per finding, positionally aligned, with None where that
    finding's call failed — so a single failure costs one verdict instead of the
    whole pass, which is what the batch call it replaces did.
    """
    blocks = split_diff_by_file(diff)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _one(finding: Finding) -> FindingJudgement | None:
        async with semaphore:
            return await judge_finding(provider, finding, blocks.get(finding.file, ""))

    return list(await asyncio.gather(*(_one(f) for f in findings)))


def apply_judgements(
    findings: list[Finding], judgements: list[FindingJudgement | None]
) -> list[Finding]:
    """Merge per-finding judgements into new frozen copies, positionally (#246 §3.2).

    ``judgements[i]`` rules on ``findings[i]``; a ``None`` means that call failed
    and the finding stays unruled — absence of a judgement is not a refutation.
    'uncertain' discounts confidence x0.9 as a ranking signal only, identical to
    the batch merge it replaces.
    """
    merged: list[Finding] = []
    for finding, judgement in zip(findings, judgements, strict=True):
        if judgement is None:
            merged.append(finding)
            continue
        update: dict[str, object] = {
            "verdict": judgement.verdict,
            "skeptic_note": judgement.rationale,
            "impact_score": judgement.impact_score,
        }
        if judgement.verdict == "uncertain":
            update["confidence"] = round(finding.confidence * _UNCERTAIN_MULTIPLIER)
        merged.append(finding.model_copy(update=update))
    return merged


def visible_findings(findings: Iterable[Finding], threshold: int = 0) -> list[Finding]:
    """Findings that appear in the rendered report (#246 §3.2).

    Hidden: anything refuted, and anything the skeptic scored below
    ``threshold``. An unjudged finding (``impact_score is None``) has no score to
    compare and is always shown — a failed judgement call must never silence a
    finding. Hidden findings stay in ``ReviewResult.findings`` so metrics and the
    benchmark still see what was cut.
    """
    return [
        f
        for f in findings
        if f.verdict != "refuted"
        and not (f.impact_score is not None and f.impact_score < threshold)
    ]
