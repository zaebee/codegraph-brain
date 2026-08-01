"""Per-axis review: one finder call per focus area instead of one carrying all (#331).

The finder appears to work against a roughly fixed attention budget spread over
the axes named in a single prompt. Editing that prompt reallocates the budget
rather than adding capacity — measured twice:

- a targeted float few-shot cracked nothing and dropped pr-144 from 3/5 to 1/5,
  pulling focus off the yaml class it had been catching (#247);
- exculpating clauses took pr-313 from recall 0.000 to 1.000 while the corpus
  total stayed at matched 17 -> 17, two lost elsewhere (#330).

Axes cannot compete for attention if they are not in the same context window.
Each call still sees the *whole* diff — only the question narrows, which is what
separates this from chunked review (#154), where each call saw a fragment and
part of the failure was dilution inside the chunk.

The known risk is the one chunking failed on: every extra call is another
opportunity to invent a false positive, and there noise grew superlinearly
(#160). That is why this ships behind a flag and why the pre-registered gate in
#331 puts noise first.
"""

import asyncio

import structlog

from cgis.guardian.collector import ContextCollector
from cgis.guardian.core import finder_pass
from cgis.guardian.findings import Finding, ReviewResult, dedup_findings
from cgis.guardian.prompts import FOCUS_AREAS
from cgis.guardian.providers.base import BaseProvider
from cgis.guardian.skeptic import apply_judgements, judge_all, skeptic_status_for

log = structlog.getLogger(__name__)


async def _axis_pass(
    provider: BaseProvider, context: dict[str, str], axis: str
) -> ReviewResult | None:
    """Run the finder for one axis; None when that axis's call fails.

    A flaky call must cost one axis, not the review — the same guard the chunked
    orchestrator puts around each chunk.
    """
    try:
        return await finder_pass(provider, {**context, "focus_axis": axis})
    except Exception:
        log.warning("Axis finder call failed; axis skipped.", axis=axis, exc_info=True)
        return None


async def run_axis_review(
    *,
    provider: BaseProvider,
    collector: ContextCollector,
    skeptic_provider: BaseProvider | None,
    concurrency: int = 4,
) -> ReviewResult:
    """Fan out over focus areas, merge, dedup, then one skeptic pass over the union.

    One skeptic pass rather than one per axis: judging is already the larger half
    of the token bill, and per-axis judging would multiply it for no expected
    gain — the axes disagree about *what to look for*, not about whether a given
    finding is real.
    """
    context = collector.collect_all()
    axes = list(FOCUS_AREAS)

    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(axis: str) -> ReviewResult | None:
        async with semaphore:
            return await _axis_pass(provider, context, axis)

    results = await asyncio.gather(*(guarded(axis) for axis in axes))

    findings: list[Finding] = []
    summaries: list[str] = []
    failed = 0
    for axis, result in zip(axes, results, strict=True):
        if result is None:
            failed += 1
            continue
        if result.parse_failed:
            # Unparseable output is not a finding-free axis; count it as failed so
            # the summary cannot read as "this axis found nothing".
            failed += 1
            log.warning("Axis output did not parse; axis skipped.", axis=axis)
            continue
        findings.extend(result.findings)
        summaries.append(f"- [{axis}]: {result.summary}")

    if failed == len(axes):
        return ReviewResult(
            findings=[],
            summary="Every axis pass failed; no review produced.",
            parse_failed=True,
        )

    merged = ReviewResult(
        findings=dedup_findings(findings),
        summary="\n".join(summaries),
    )
    log.info(
        "Axis fan-out complete.",
        axes=len(axes),
        failed=failed,
        raw_findings=len(findings),
        after_dedup=len(merged.findings),
    )

    if skeptic_provider is None or not merged.findings:
        return merged

    judgements = await judge_all(
        skeptic_provider, merged.findings, context.get("diff", ""), concurrency
    )
    judged = sum(1 for j in judgements if j is not None)
    if judged == 0:
        log.warning("Every skeptic judgement failed; returning unverified findings.")
    return merged.model_copy(
        update={
            "findings": apply_judgements(merged.findings, judgements),
            "skeptic_status": skeptic_status_for(judged, len(judgements)),
            "skeptic_judged": judged,
            "skeptic_total": len(judgements),
        }
    )
