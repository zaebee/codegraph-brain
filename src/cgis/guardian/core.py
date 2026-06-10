"""Main orchestrator that wires together collector, prompts, and LLM provider."""

import structlog
from pydantic import ValidationError

from cgis.guardian.collector import ContextCollector
from cgis.guardian.findings import ReviewResult, extract_json
from cgis.guardian.prompts import PromptBuilder
from cgis.guardian.providers.base import BaseProvider

log = structlog.getLogger(__name__)

_RETRY_SUFFIX = (
    "\n\n---\nYour previous response failed validation against the required JSON schema:\n"
    "{error}\n"
    "Respond again with ONLY the JSON object — no prose, no markdown fences."
)


class GuardianReviewer:
    """Orchestrates the entire review process."""

    def __init__(self, provider: BaseProvider, context_collector: ContextCollector) -> None:
        """Wire up the LLM provider, context collector, and prompt builder."""
        self.provider = provider
        self.context_collector = context_collector
        self.prompt_builder = PromptBuilder()

    async def run_review(self) -> ReviewResult:
        """Run the review and return structured findings.

        Parse policy (spec §2.3): one retry with the validation error appended;
        on a second failure the raw text becomes the summary with parse_failed=True.
        """
        context = self.context_collector.collect_all()
        system_prompt = self.prompt_builder.build_system_prompt()
        user_prompt = self.prompt_builder.build_user_prompt(context)
        raw = await self.provider.generate_structured(system_prompt, user_prompt, ReviewResult)
        try:
            return ReviewResult.model_validate_json(extract_json(raw))
        except ValidationError as exc:
            log.warning(
                "Structured output failed validation; retrying once.",
                validation_error=str(exc),
            )
            retry_prompt = user_prompt + _RETRY_SUFFIX.format(error=exc)
            raw = await self.provider.generate_structured(system_prompt, retry_prompt, ReviewResult)
            try:
                return ReviewResult.model_validate_json(extract_json(raw))
            except ValidationError:
                log.exception("Structured output failed twice; falling back to raw text.")
                return ReviewResult(findings=[], summary=raw, parse_failed=True)
