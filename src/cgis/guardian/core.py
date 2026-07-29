"""Main orchestrator that wires together collector, prompts, and LLM provider."""

import structlog
from pydantic import ValidationError

from cgis.guardian.collector import ContextCollector
from cgis.guardian.findings import ReviewResult, extract_json
from cgis.guardian.prompts import PromptBuilder
from cgis.guardian.providers.base import BaseProvider
from cgis.guardian.skeptic import (
    SKEPTIC_SYSTEM_PROMPT,
    SkepticResult,
    apply_verdicts,
    build_skeptic_prompt,
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
            log.exception("Structured output failed twice; falling back to raw text.")
            return ReviewResult(findings=[], summary=raw, parse_failed=True)


class GuardianReviewer:
    """Orchestrates the entire review process."""

    def __init__(
        self,
        provider: BaseProvider,
        context_collector: ContextCollector,
        skeptic_provider: BaseProvider | None = None,
    ) -> None:
        """Wire up the LLM provider, context collector, and optional skeptic."""
        self.provider = provider
        self.context_collector = context_collector
        self.skeptic_provider = skeptic_provider

    async def _finder_pass(self, context: dict[str, str]) -> ReviewResult:
        """Delegate to the module-level finder_pass (kept for call-site stability)."""
        return await finder_pass(self.provider, context)

    async def run_review(self) -> ReviewResult:
        """Run the review; optionally verify findings with the skeptic pass (spec §5)."""
        context = self.context_collector.collect_all()
        result = await self._finder_pass(context)
        if self.skeptic_provider is None or not result.findings or result.parse_failed:
            return result
        try:
            raw = await self.skeptic_provider.generate_structured(
                SKEPTIC_SYSTEM_PROMPT,
                build_skeptic_prompt(context, result.findings),
                SkepticResult,
            )
            verdicts = SkepticResult.model_validate_json(extract_json(raw))
        except Exception:
            log.warning("Skeptic pass failed; returning single-pass results.", exc_info=True)
            return result.model_copy(update={"skeptic_status": "failed"})
        merged = apply_verdicts(result.findings, verdicts)
        return result.model_copy(update={"findings": merged, "skeptic_status": "ok"})
