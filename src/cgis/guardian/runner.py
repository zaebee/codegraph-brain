"""Testable orchestration for the guardian review script."""

import asyncio
import time
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
from cgis.guardian.providers.ollama import (
    DEFAULT_OLLAMA_NUM_CTX,
    DEFAULT_OLLAMA_NUM_PREDICT,
    OllamaProvider,
)
from cgis.guardian.recording import save_finder_recording
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


def temperature(env: Mapping[str, str]) -> float | None:
    """GUARDIAN_TEMPERATURE as a float, or None to leave the API's own default.

    None rather than an invented default, because Phases 1 and 2 of #342 ran on
    provider defaults and the spec keeps that frozen; only a phase that
    registered a value before spending sets one.

    An unparseable value warns and falls back rather than raising: the same
    policy as GUARDIAN_OLLAMA_NUM_CTX, and a typo should not abort a run that
    costs money partway through.
    """
    raw = (env.get("GUARDIAN_TEMPERATURE") or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        log.warning("Invalid GUARDIAN_TEMPERATURE; using the provider default.", value=raw)
        return None


def _ollama_num_predict(env: Mapping[str, str]) -> int:
    """GUARDIAN_OLLAMA_NUM_PREDICT as an int, or the provider default.

    Warns and falls back on a typo rather than raising: the same policy as
    GUARDIAN_OLLAMA_NUM_CTX, and a bad value should not abort a run that has
    already spent an hour.
    """
    raw = (env.get("GUARDIAN_OLLAMA_NUM_PREDICT") or "").strip()
    if not raw:
        return DEFAULT_OLLAMA_NUM_PREDICT
    try:
        return int(raw)
    except ValueError:
        log.warning("Invalid GUARDIAN_OLLAMA_NUM_PREDICT; using default.", value=raw)
        return DEFAULT_OLLAMA_NUM_PREDICT


def _ollama_penalties(env: Mapping[str, str]) -> Mapping[str, float] | None:
    """None for this project's extraction defaults, {} for the model's own.

    A switch rather than a decision, because the decision is unmeasured.
    Neutralising the repetition penalties was argued from the shape of the task
    — the finder's JSON repeats its keys by construction — and the run after it
    did not terminate. The fixtures differed, so that neither confirms nor
    refutes it, which is precisely why it belongs behind a flag that an
    experiment can flip and report.
    """
    if (env.get("GUARDIAN_OLLAMA_PENALTIES") or "").strip().lower() == "model":
        return {}
    return None


def _build_mistral(env: Mapping[str, str], model_override: str | None) -> tuple[BaseProvider, str]:
    """Construct a MistralProvider; MISTRAL_API_KEY is required."""
    key = env.get("MISTRAL_API_KEY")
    if not key:
        _msg = "MISTRAL_API_KEY must be set when GUARDIAN_PROVIDER=mistral"
        raise RuntimeError(_msg)
    model = model_override or DEFAULT_MISTRAL_MODEL
    provider = MistralProvider(api_key=key, model_name=model, temperature=temperature(env))
    return provider, model


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
        model_name=model_override,
        host=_ollama_host(env),
        num_ctx=_ollama_num_ctx(env),
        temperature=temperature(env),
        penalties=_ollama_penalties(env),
        num_predict=_ollama_num_predict(env),
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
            model_name=model,
            host=_ollama_host(env),
            num_ctx=_ollama_num_ctx(env),
            temperature=temperature(env),
            penalties=_ollama_penalties(env),
            num_predict=_ollama_num_predict(env),
        )
        return provider, model
    if name == "mistral":
        key = env.get("MISTRAL_API_KEY")
        if not key:
            log.warning("Skeptic disabled: MISTRAL_API_KEY not set.")
            return None
        model = model_override or DEFAULT_MISTRAL_MODEL
        # Same temperature as the finder: the run registers one sampling
        # setting, not one per pass.
        skeptic = MistralProvider(api_key=key, model_name=model, temperature=temperature(env))
        return skeptic, model
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
    record_finder: Path | None = None,
    review_fingerprint: str,
) -> tuple[str, bool]:
    """Run the review; try the inline path when configured.

    Returns (rendered report + footer, posted_inline). posted_inline=False
    covers both "not configured" and "API rejected" — the caller posts the
    big comment in either case (spec §6.5).

    ``review_fingerprint`` is opaque here — computed by the caller (never inside
    this module; see review_fingerprint.py's closure contract, #375) — and
    passed straight to ``record_review`` so a live review is attributable to a
    reviewer version, not just a commit. Keyword-required with no default (final
    review, #375): the forwarding line at the bottom of this function is easy to
    delete without breaking a single test, since every existing caller passed
    the value explicitly to `record_review`, not to this parameter — an omitted
    caller now fails mypy strict rather than silently shipping an
    unattributable live review. ``record_review`` itself keeps `str | None`,
    because it also reads old rows and must stay tolerant of rows that predate
    this field.

    ``record_finder`` writes the finder pass (findings + diff) to that path so
    the review can be re-scored offline against a benchmark entry (#279).
    """
    started = time.monotonic()
    routed = await run_review_routed(
        provider=provider,
        collector=collector,
        skeptic_provider=skeptic[0] if skeptic else None,
    )
    # monotonic, not time(): a wall-clock adjustment mid-review would otherwise
    # produce a negative or nonsense duration.
    duration_s = round(time.monotonic() - started, 2)
    result = routed.result
    if record_finder is not None:
        # Recorded AFTER the skeptic on purpose: load_finder_recording strips
        # verdicts on read, and refuted findings stay in result.findings — so a
        # replay still starts clean, with no second API call to pay for (#279).
        try:
            save_finder_recording(record_finder, result, collector.get_git_diff())
            log.info("Finder pass recorded.", path=str(record_finder))
        except OSError:
            # OSError only, and the narrowness is the point: an environment
            # failure (disk, permissions) must not cost a review that is already
            # done, but a bad --record-finder path raises ValueError from the
            # validator and MUST stay loud. Swallowing that would leave a
            # misconfigured pipeline silently producing no recordings — the very
            # failure this feature exists to prevent (#279).
            log.warning(
                "Finder recording failed; continuing without it.",
                path=str(record_finder),
                exc_info=True,
            )
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
        duration_s=duration_s,
        review_fingerprint=review_fingerprint,
        metrics_path=metrics_path,
    )
    return report + footer, posted
