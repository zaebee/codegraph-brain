"""Main orchestrator that wires together collector, prompts, and LLM provider."""

import os

import structlog
from pydantic import ValidationError

from cgis.guardian.collector import ContextCollector
from cgis.guardian.evidence import evidence_for
from cgis.guardian.findings import ReviewResult, extract_json, salvage_findings
from cgis.guardian.prompts import PromptBuilder
from cgis.guardian.providers.base import BaseProvider
from cgis.guardian.skeptic import (
    DEFAULT_SKEPTIC_CONCURRENCY,
    apply_judgements,
    judge_all,
    skeptic_status_for,
)

log = structlog.getLogger(__name__)

_RETRY_SUFFIX = (
    "\n\n---\nYour previous response failed validation against the required JSON schema:\n"
    "{error}\n"
    "Respond again with ONLY the JSON object — no prose, no markdown fences."
)


def _sanitize_finder_result(result: ReviewResult) -> ReviewResult:
    """Reset skeptic-owned fields the finder LLM may have hallucinated.

    ReviewResult doubles as the finder's structured-output schema, so the
    model sees `skeptic_status`, the judged/total counters and per-finding
    `verdict`/`skeptic_note`/`impact_score`, and sometimes fills them in. A
    hallucinated `verdict="refuted"` would make visible_findings() silently drop
    a finder finding; a hallucinated `impact_score` would hide one behind the
    impact threshold just as silently — only the skeptic pass may set these.
    """
    findings = [
        f.model_copy(update={"verdict": None, "skeptic_note": None, "impact_score": None})
        for f in result.findings
    ]
    return result.model_copy(
        update={
            "findings": findings,
            "skeptic_status": "off",
            "skeptic_judged": 0,
            "skeptic_total": 0,
        }
    )


async def finder_pass(provider: BaseProvider, context: dict[str, str]) -> ReviewResult:
    """Run the finder (pass 1) with parse-retry semantics.

    Parse policy (spec §2.3): one retry with the validation error appended;
    on a second failure the raw text becomes the summary with parse_failed=True.
    Module-level so the chunked orchestrator reuses it per chunk (slice 2).
    """
    builder = PromptBuilder()
    system_prompt = builder.build_system_prompt()
    user_prompt = builder.build_user_prompt(context)
    raw = await provider.generate_structured(system_prompt, user_prompt, ReviewResult)
    try:
        return _sanitize_finder_result(ReviewResult.model_validate_json(extract_json(raw)))
    except ValidationError as exc:
        log.warning(
            "Structured output failed validation; retrying once.",
            validation_error=str(exc),
        )
        retry_prompt = user_prompt + _RETRY_SUFFIX.format(error=exc)
        raw = await provider.generate_structured(system_prompt, retry_prompt, ReviewResult)
        try:
            return _sanitize_finder_result(ReviewResult.model_validate_json(extract_json(raw)))
        except ValidationError:
            # Salvage before giving up: a long finder response is usually severed
            # part-way through the array, so the findings before the cut are
            # intact. Discarding them lost fifteen of eighteen on one PR of the
            # #342 Phase 3 run.
            #
            # `parse_failed` stays True even when findings are recovered. The
            # strict parse did fail, the recovered set is smaller than what the
            # model produced, and the benchmark's exclusion rule reads this flag
            # — so a rescue must not quietly become a measurement. Production
            # renders what was recovered rather than nothing.
            rescued = salvage_findings(raw)
            log.exception(
                "Structured output failed twice; salvaging what parsed.",
                salvaged=len(rescued),
            )
            # Sanitised like any other finder output. `ReviewResult` doubles as
            # the finder's schema, so the model sees `verdict` and
            # `impact_score` and sometimes fills them in — and a hallucinated
            # "refuted" makes `visible_findings` delete the finding silently. A
            # response mangled enough to need salvaging is if anything a likelier
            # place for those fields to have been filled loosely.
            return _sanitize_finder_result(
                ReviewResult(findings=rescued, summary=raw, parse_failed=True)
            )


class GuardianReviewer:
    """Orchestrates the entire review process."""

    def __init__(
        self,
        provider: BaseProvider,
        context_collector: ContextCollector,
        skeptic_provider: BaseProvider | None = None,
        concurrency: int = DEFAULT_SKEPTIC_CONCURRENCY,
    ) -> None:
        """Wire up the LLM provider, context collector, and optional skeptic.

        ``concurrency`` bounds the per-finding judgement calls; the default keeps
        provider rate limits out of reach (#246 §3.4).
        """
        self.provider = provider
        self.context_collector = context_collector
        self.skeptic_provider = skeptic_provider
        self.concurrency = concurrency

    async def _finder_pass(self, context: dict[str, str]) -> ReviewResult:
        """Delegate to the module-level finder_pass (kept for call-site stability)."""
        return await finder_pass(self.provider, context)

    async def run_review(self) -> ReviewResult:
        """Run the review; optionally judge each finding with the skeptic pass (#246 §3.4)."""
        context = self.context_collector.collect_all()
        result = await self._finder_pass(context)
        # Not gated on `parse_failed` any more. It used to imply an empty finding
        # list, so skipping the skeptic was free; since #248 a failed parse can
        # still yield salvaged findings, and shipping those unjudged would trade
        # the old loss of findings for a new gain in noise.
        if self.skeptic_provider is None or not result.findings:
            return result
        judgements = await judge_all(
            self.skeptic_provider,
            result.findings,
            context.get("diff", ""),
            self.concurrency,
            evidence=await evidence_for(self.context_collector, os.environ),
        )
        judged = sum(1 for j in judgements if j is not None)
        if judged == 0:
            log.warning("Every skeptic judgement failed; returning single-pass results.")
        return result.model_copy(
            update={
                "findings": apply_judgements(result.findings, judgements),
                "skeptic_status": skeptic_status_for(judged, len(judgements)),
                "skeptic_judged": judged,
                "skeptic_total": len(judgements),
            }
        )
