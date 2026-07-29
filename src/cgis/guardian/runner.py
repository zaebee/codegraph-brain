"""Testable orchestration for the guardian review script."""

import asyncio
from collections.abc import Mapping
from pathlib import Path

import structlog

from cgis.guardian.chunked import run_review_routed
from cgis.guardian.collector import ContextCollector
from cgis.guardian.diff_index import diff_line_content
from cgis.guardian.findings import ReviewResult
from cgis.guardian.github_poster import post_inline_review
from cgis.guardian.metrics import SkepticRecord, record_review
from cgis.guardian.providers.base import BaseProvider, ProviderUsage
from cgis.guardian.providers.gemini import GeminiProvider
from cgis.guardian.providers.mistral import MistralProvider
from cgis.guardian.providers.ollama import DEFAULT_OLLAMA_NUM_CTX, OllamaProvider
from cgis.guardian.render import render_report

log = structlog.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_MISTRAL_MODEL = "mistral-medium-latest"


def _ollama_host(env: Mapping[str, str]) -> str | None:
    """GUARDIAN_OLLAMA_HOST, or None so the ollama client uses localhost:11434.

    Collapses empty/whitespace-only values to None at the boundary so a blank
    env var never reaches the client as an invalid host.
    """
    return (env.get("GUARDIAN_OLLAMA_HOST") or "").strip() or None


def impact_threshold(env: Mapping[str, str]) -> int:
    """GUARDIAN_IMPACT_THRESHOLD clamped to 0-10; 0 (nothing hidden) on absence or garbage.

    Ships inert on purpose (#246 §3.5): the threshold is chosen from the
    benchmark's score distribution, not guessed. A garbage value must not
    silently hide findings, so it degrades to "hide nothing".
    """
    raw = (env.get("GUARDIAN_IMPACT_THRESHOLD") or "").strip()
    if not raw:
        return 0
    try:
        return max(0, min(10, int(raw)))
    except ValueError:
        log.warning("Invalid GUARDIAN_IMPACT_THRESHOLD; hiding nothing.", value=raw)
        return 0


def _ollama_num_ctx(env: Mapping[str, str]) -> int:
    """GUARDIAN_OLLAMA_NUM_CTX as an int, or the provider default (avoids silent truncation)."""
    raw = (env.get("GUARDIAN_OLLAMA_NUM_CTX") or "").strip()
    if not raw:
        return DEFAULT_OLLAMA_NUM_CTX
    try:
        return int(raw)
    except ValueError:
        log.warning("Invalid GUARDIAN_OLLAMA_NUM_CTX; using default.", value=raw)
        return DEFAULT_OLLAMA_NUM_CTX


def _build_mistral(env: Mapping[str, str], model_override: str | None) -> tuple[BaseProvider, str]:
    """Construct a MistralProvider; MISTRAL_API_KEY is required."""
    key = env.get("MISTRAL_API_KEY")
    if not key:
        _msg = "MISTRAL_API_KEY must be set when GUARDIAN_PROVIDER=mistral"
        raise RuntimeError(_msg)
    model = model_override or DEFAULT_MISTRAL_MODEL
    return MistralProvider(api_key=key, model_name=model), model


def _build_gemini(env: Mapping[str, str], model_override: str | None) -> tuple[BaseProvider, str]:
    """Construct a GeminiProvider; GEMINI_API_KEY is required."""
    key = env.get("GEMINI_API_KEY")
    if not key:
        _msg = "GEMINI_API_KEY must be set when GUARDIAN_PROVIDER=gemini"
        raise RuntimeError(_msg)
    model = model_override or DEFAULT_GEMINI_MODEL
    return GeminiProvider(api_key=key, model_name=model), model


def _build_ollama(env: Mapping[str, str], model_override: str | None) -> tuple[BaseProvider, str]:
    """Construct an OllamaProvider; GUARDIAN_MODEL names the model (no API key)."""
    if not model_override:
        _msg = "GUARDIAN_MODEL must name an Ollama model when GUARDIAN_PROVIDER=ollama"
        raise RuntimeError(_msg)
    provider = OllamaProvider(
        model_name=model_override, host=_ollama_host(env), num_ctx=_ollama_num_ctx(env)
    )
    return provider, model_override


def build_provider(env: Mapping[str, str]) -> tuple[BaseProvider, str]:
    """Return (provider, model_name) from GUARDIAN_PROVIDER / available API keys.

    With no explicit GUARDIAN_PROVIDER, auto-select: mistral if its key is set,
    else gemini if its key is set, else an error.
    """
    model_override = env.get("GUARDIAN_MODEL")
    provider_name = env.get("GUARDIAN_PROVIDER", "").lower() or _autodetect_provider(env)

    builders = {"mistral": _build_mistral, "gemini": _build_gemini, "ollama": _build_ollama}
    builder = builders.get(provider_name)
    if builder is None:
        _msg = f"Unknown GUARDIAN_PROVIDER={provider_name!r}. Use 'mistral', 'gemini', or 'ollama'."
        raise RuntimeError(_msg)
    return builder(env, model_override)


def _autodetect_provider(env: Mapping[str, str]) -> str:
    """Pick a provider from available API keys when GUARDIAN_PROVIDER is unset."""
    if env.get("MISTRAL_API_KEY"):
        return "mistral"
    if env.get("GEMINI_API_KEY"):
        return "gemini"
    _msg = "Set MISTRAL_API_KEY or GEMINI_API_KEY to run Guardian."
    raise RuntimeError(_msg)


def build_skeptic_provider(
    env: Mapping[str, str], *, primary: str
) -> tuple[BaseProvider, str] | None:
    """Return (skeptic_provider, model) or None for single-pass (spec §5.5).

    Default skeptic = the provider opposite to the primary; GUARDIAN_SKEPTIC
    overrides ('gemini'|'mistral'|'ollama'|'off'); GUARDIAN_SKEPTIC_MODEL overrides
    the model, enabling same-provider/different-model pairs (incl. two distinct
    local Ollama models — a cross-model skeptic with no API cost). A missing API
    key / model degrades to None — a review never fails because of the skeptic.
    """
    choice = env.get("GUARDIAN_SKEPTIC", "").lower()
    if choice == "off":
        return None
    if choice not in ("", "gemini", "mistral", "ollama"):
        log.warning("Unknown GUARDIAN_SKEPTIC; skeptic disabled.", value=choice)
        return None
    name = choice or ("mistral" if primary == "gemini" else "gemini")
    model_override = env.get("GUARDIAN_SKEPTIC_MODEL")
    if name == "ollama":
        model = model_override or env.get("GUARDIAN_MODEL")
        if not model:
            log.warning(
                "Skeptic disabled: set GUARDIAN_SKEPTIC_MODEL (or GUARDIAN_MODEL) "
                "for an ollama skeptic."
            )
            return None
        provider = OllamaProvider(
            model_name=model, host=_ollama_host(env), num_ctx=_ollama_num_ctx(env)
        )
        return provider, model
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


def build_footer(
    *,
    model: str,
    usage: ProviderUsage,
    stats: dict[str, int],
    result: ReviewResult | None = None,
) -> str:
    """Build the markdown footer with model, token usage, graph coverage and skeptic reach."""
    parts = [f"🤖 **{model}**"]
    if usage.total_tokens > 0:
        parts.append(
            f"{usage.prompt_tokens:,} prompt + {usage.completion_tokens:,} completion"
            f" = **{usage.total_tokens:,} tokens**"
        )
    if stats.get("total", 0) > 0:
        pct = round(stats.get("with_graph", 0) / stats["total"] * 100)
        parts.append(f"graph {stats.get('with_graph', 0)}/{stats['total']} files ({pct}%)")
    if result is not None and result.skeptic_total:
        parts.append(f"skeptic {result.skeptic_judged}/{result.skeptic_total}")
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
    threshold: int = 0,
) -> tuple[str, bool]:
    """Run the review; try the inline path when configured.

    Returns (rendered report + footer, posted_inline). posted_inline=False
    covers both "not configured" and "API rejected" — the caller posts the
    big comment in either case (spec §6.5).
    """
    routed = await run_review_routed(
        provider=provider,
        collector=collector,
        skeptic_provider=skeptic[0] if skeptic else None,
    )
    result = routed.result
    report = render_report(result, threshold)
    # Built BEFORE the inline attempt: a successful inline post skips the
    # fallback comment, so the footer must ride inside the review body too
    # (live finding on PR #157 — inline reviews arrived footerless).
    footer = build_footer(
        model=model,
        usage=provider.cumulative_usage,
        stats=collector.graph_stats,
        result=result,
    )

    posted = False
    if inline_repo is not None and pr is not None:
        try:
            diff_text = collector.get_git_diff()
            content = diff_line_content(diff_text)
            await asyncio.to_thread(  # subprocess `gh api` call — keep the loop responsive
                post_inline_review,
                repo=inline_repo,
                pr=pr,
                result=result,
                diff_index={path: set(lines) for path, lines in content.items()},
                skeptic_model=skeptic[1] if skeptic else None,
                footer=footer,
                diff_content=content,
                threshold=threshold,
            )
            posted = True
        except Exception:
            log.warning("Inline review failed; falling back to comment.", exc_info=True)

    record_review(
        model=model,
        pr=pr,
        prompt_tokens=provider.cumulative_usage.prompt_tokens,
        completion_tokens=provider.cumulative_usage.completion_tokens,
        findings_total=len(result.findings),
        # lgtm counts pre-skeptic findings on purpose: all-refuted is "finder
        # flagged something, skeptic killed it" — not a clean LGTM.
        lgtm=not result.findings and not result.parse_failed,
        parse_failed=result.parse_failed,
        skeptic=SkepticRecord(
            model=skeptic[1] if skeptic else None,
            status=result.skeptic_status,
            judged=result.skeptic_judged,
            total=result.skeptic_total,
            impact_threshold=threshold,
        ),
        chunk_count=routed.chunk_count,
        metrics_path=metrics_path,
    )
    return report + footer, posted
