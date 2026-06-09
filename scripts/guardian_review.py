"""CLI entry point for running a Guardian AI code review."""

import argparse
import asyncio
import os
from pathlib import Path

import structlog

from cgis.guardian.collector import ContextCollector
from cgis.guardian.core import GuardianReviewer
from cgis.guardian.metrics import record_review
from cgis.guardian.providers.base import BaseProvider
from cgis.guardian.providers.gemini import GeminiProvider
from cgis.guardian.providers.mistral import MistralProvider

log = structlog.getLogger(__name__)

_DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
_DEFAULT_MISTRAL_MODEL = "mistral-medium-latest"


def _build_provider() -> tuple[BaseProvider, str]:
    """Return (provider, model_name) based on GUARDIAN_PROVIDER or available API keys."""
    model_override = os.environ.get("GUARDIAN_MODEL")
    provider_name = os.environ.get("GUARDIAN_PROVIDER", "").lower()

    if provider_name == "mistral" or (not provider_name and os.environ.get("MISTRAL_API_KEY")):
        mistral_key = os.environ.get("MISTRAL_API_KEY")
        if not mistral_key:
            _msg = "MISTRAL_API_KEY must be set when GUARDIAN_PROVIDER=mistral"
            raise RuntimeError(_msg)
        model = model_override or _DEFAULT_MISTRAL_MODEL
        return MistralProvider(api_key=mistral_key, model_name=model), model

    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        model = model_override or _DEFAULT_GEMINI_MODEL
        return GeminiProvider(api_key=gemini_key, model_name=model), model

    _msg = "Set MISTRAL_API_KEY or GEMINI_API_KEY to run Guardian."
    raise RuntimeError(_msg)


async def main() -> None:
    """Run Guardian review and write the result to stdout or a file."""
    parser = argparse.ArgumentParser(description="Run CGIS Guardian AI code review.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the review to this file instead of stdout.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to graph.db for structural impact context (optional).",
    )
    parser.add_argument(
        "--pr",
        type=int,
        default=None,
        help="GitHub PR number being reviewed (used for metrics tracking).",
    )
    parser.add_argument(
        "--metrics",
        type=Path,
        default=Path("guardian_metrics.jsonl"),
        help="Path to the append-only metrics log (default: guardian_metrics.jsonl).",
    )
    args = parser.parse_args()

    provider, model = _build_provider()
    project_root = Path(__file__).parent.parent.absolute()
    collector = ContextCollector(project_root=project_root, db_path=args.db)
    reviewer = GuardianReviewer(provider=provider, context_collector=collector)

    log.info("Collecting context and building prompts...", model=model)
    review_result = await reviewer.run_review()
    log.info("Review complete.")

    usage = provider.last_usage
    if usage.total_tokens > 0:
        log.info(
            "Token usage",
            prompt=usage.prompt_tokens,
            completion=usage.completion_tokens,
            total=usage.total_tokens,
        )

    stats = collector.graph_stats
    if stats["total"] > 0:
        pct = round(stats["with_graph"] / stats["total"] * 100)
        log.info(
            "Graph context",
            files_with_graph=stats["with_graph"],
            total_changed=stats["total"],
            coverage_pct=f"{pct}%",
        )

    footer_parts = [f"🤖 **{model}**"]
    if usage.total_tokens > 0:
        footer_parts.append(
            f"{usage.prompt_tokens:,} prompt + {usage.completion_tokens:,} completion"
            f" = **{usage.total_tokens:,} tokens**"
        )
    if stats["total"] > 0:
        pct = round(stats["with_graph"] / stats["total"] * 100)
        footer_parts.append(f"graph {stats['with_graph']}/{stats['total']} files ({pct}%)")
    footer = "\n\n---\n> " + " · ".join(footer_parts)

    metrics_path = record_review(
        model=model,
        pr=args.pr,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        review_text=review_result,
        metrics_path=args.metrics,
    )
    log.info("Metrics recorded.", path=str(metrics_path))

    if args.output:
        safe_root = Path.cwd().resolve()
        output_path = (safe_root / args.output).resolve()
        if not output_path.is_relative_to(safe_root):
            _msg = f"--output must be within the working directory: {output_path}"
            raise ValueError(_msg)
        output_path.write_text(review_result + footer)
        log.info("Review written to file.", path=str(output_path))
    else:
        print(review_result)


if __name__ == "__main__":
    asyncio.run(main())
