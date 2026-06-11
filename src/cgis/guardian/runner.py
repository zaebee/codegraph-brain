"""Testable orchestration for the guardian review script."""

import asyncio
from collections.abc import Mapping
from pathlib import Path

import structlog

from cgis.guardian.collector import ContextCollector
from cgis.guardian.core import GuardianReviewer
from cgis.guardian.diff_index import diff_line_index
from cgis.guardian.github_poster import post_inline_review
from cgis.guardian.metrics import record_review
from cgis.guardian.providers.base import BaseProvider, ProviderUsage
from cgis.guardian.providers.gemini import GeminiProvider
from cgis.guardian.providers.mistral import MistralProvider
from cgis.guardian.render import render_report

log = structlog.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_MISTRAL_MODEL = "mistral-medium-latest"


def build_provider(env: Mapping[str, str]) -> tuple[BaseProvider, str]:
    """Return (provider, model_name) from GUARDIAN_PROVIDER / available API keys."""
    model_override = env.get("GUARDIAN_MODEL")
    provider_name = env.get("GUARDIAN_PROVIDER", "").lower()

    if provider_name == "mistral" or (not provider_name and env.get("MISTRAL_API_KEY")):
        mistral_key = env.get("MISTRAL_API_KEY")
        if not mistral_key:
            _msg = "MISTRAL_API_KEY must be set when GUARDIAN_PROVIDER=mistral"
            raise RuntimeError(_msg)
        model = model_override or DEFAULT_MISTRAL_MODEL
        return MistralProvider(api_key=mistral_key, model_name=model), model

    if provider_name == "gemini":
        gemini_key = env.get("GEMINI_API_KEY")
        if not gemini_key:
            _msg = "GEMINI_API_KEY must be set when GUARDIAN_PROVIDER=gemini"
            raise RuntimeError(_msg)
        model = model_override or DEFAULT_GEMINI_MODEL
        return GeminiProvider(api_key=gemini_key, model_name=model), model

    if provider_name and provider_name != "mistral":
        _msg = f"Unknown GUARDIAN_PROVIDER={provider_name!r}. Use 'mistral' or 'gemini'."
        raise RuntimeError(_msg)

    gemini_key = env.get("GEMINI_API_KEY")
    if gemini_key:
        model = model_override or DEFAULT_GEMINI_MODEL
        return GeminiProvider(api_key=gemini_key, model_name=model), model

    _msg = "Set MISTRAL_API_KEY or GEMINI_API_KEY to run Guardian."
    raise RuntimeError(_msg)


def build_skeptic_provider(
    env: Mapping[str, str], *, primary: str
) -> tuple[BaseProvider, str] | None:
    """Return (skeptic_provider, model) or None for single-pass (spec §5.5).

    Default skeptic = the provider opposite to the primary; GUARDIAN_SKEPTIC
    overrides ('gemini'|'mistral'|'off'); GUARDIAN_SKEPTIC_MODEL overrides the
    model, enabling same-provider/different-model pairs. A missing API key
    degrades to None — a review never fails because of the skeptic.
    """
    choice = env.get("GUARDIAN_SKEPTIC", "").lower()
    if choice == "off":
        return None
    if choice not in ("", "gemini", "mistral"):
        log.warning("Unknown GUARDIAN_SKEPTIC; skeptic disabled.", value=choice)
        return None
    name = choice or ("mistral" if primary == "gemini" else "gemini")
    model_override = env.get("GUARDIAN_SKEPTIC_MODEL")
    if name == "mistral":
        key = env.get("MISTRAL_API_KEY")
        if not key:
            log.warning("Skeptic disabled: MISTRAL_API_KEY not set.")
            return None
        model = model_override or DEFAULT_MISTRAL_MODEL
        return MistralProvider(api_key=key, model_name=model), model
    key = env.get("GEMINI_API_KEY")
    if not key:
        log.warning("Skeptic disabled: GEMINI_API_KEY not set.")
        return None
    model = model_override or DEFAULT_GEMINI_MODEL
    return GeminiProvider(api_key=key, model_name=model), model


def build_footer(*, model: str, usage: ProviderUsage, stats: dict[str, int]) -> str:
    """Build the markdown footer with model, token usage, and graph coverage."""
    parts = [f"🤖 **{model}**"]
    if usage.total_tokens > 0:
        parts.append(
            f"{usage.prompt_tokens:,} prompt + {usage.completion_tokens:,} completion"
            f" = **{usage.total_tokens:,} tokens**"
        )
    if stats.get("total", 0) > 0:
        pct = round(stats.get("with_graph", 0) / stats["total"] * 100)
        parts.append(f"graph {stats.get('with_graph', 0)}/{stats['total']} files ({pct}%)")
    return "\n\n---\n> " + " · ".join(parts)


async def run_guardian(
    *,
    provider: BaseProvider,
    model: str,
    collector: ContextCollector,
    pr: int | None,
    metrics_path: Path,
    skeptic: tuple[BaseProvider, str] | None = None,
    inline_repo: str | None = None,
) -> tuple[str, bool]:
    """Run the review; try the inline path when configured.

    Returns (rendered report + footer, posted_inline). posted_inline=False
    covers both "not configured" and "API rejected" — the caller posts the
    big comment in either case (spec §6.5).
    """
    reviewer = GuardianReviewer(
        provider=provider,
        context_collector=collector,
        skeptic_provider=skeptic[0] if skeptic else None,
    )
    result = await reviewer.run_review()
    report = render_report(result)

    posted = False
    if inline_repo is not None and pr is not None:
        try:
            index = diff_line_index(collector.get_git_diff())
            await asyncio.to_thread(  # subprocess `gh api` call — keep the loop responsive
                post_inline_review,
                repo=inline_repo,
                pr=pr,
                result=result,
                diff_index=index,
                skeptic_model=skeptic[1] if skeptic else None,
            )
            posted = True
        except Exception:
            log.warning("Inline review failed; falling back to comment.", exc_info=True)

    record_review(
        model=model,
        pr=pr,
        prompt_tokens=provider.last_usage.prompt_tokens,
        completion_tokens=provider.last_usage.completion_tokens,
        findings_total=len(result.findings),
        # lgtm counts pre-skeptic findings on purpose: all-refuted is "finder
        # flagged something, skeptic killed it" — not a clean LGTM.
        lgtm=not result.findings and not result.parse_failed,
        parse_failed=result.parse_failed,
        skeptic_model=skeptic[1] if skeptic else None,
        skeptic_status=result.skeptic_status,
        metrics_path=metrics_path,
    )
    footer = build_footer(model=model, usage=provider.last_usage, stats=collector.graph_stats)
    return report + footer, posted
