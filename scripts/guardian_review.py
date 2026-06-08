"""CLI entry point for running a Guardian AI code review."""

import argparse
import asyncio
import os
from pathlib import Path

import structlog

from cgis.guardian.collector import ContextCollector
from cgis.guardian.core import GuardianReviewer
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
    args = parser.parse_args()

    provider, model = _build_provider()
    project_root = Path(__file__).parent.parent.absolute()
    collector = ContextCollector(project_root=project_root, db_path=args.db)
    reviewer = GuardianReviewer(provider=provider, context_collector=collector)

    log.info("Collecting context and building prompts...", model=model)
    review_result = await reviewer.run_review()
    log.info("Review complete.")

    if args.output:
        safe_root = Path.cwd().resolve()
        output_path = (safe_root / args.output).resolve()
        if not output_path.is_relative_to(safe_root):
            _msg = f"--output must be within the working directory: {output_path}"
            raise ValueError(_msg)
        output_path.write_text(review_result)
        log.info("Review written to file.", path=str(output_path))
    else:
        print(review_result)


if __name__ == "__main__":
    asyncio.run(main())
