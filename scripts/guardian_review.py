"""CLI entry point for running a Guardian AI code review."""

import argparse
import asyncio
import os
from pathlib import Path

import structlog

from cgis.guardian.collector import ContextCollector
from cgis.guardian.core import GuardianReviewer
from cgis.guardian.providers.gemini import GeminiProvider

log = structlog.getLogger(__name__)

_DEFAULT_MODEL = "gemini-2.5-flash"


async def main() -> None:
    """Run Guardian review and write the result to stdout or a file."""
    parser = argparse.ArgumentParser(description="Run CGIS Guardian AI code review.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the review to this file instead of stdout.",
    )
    args = parser.parse_args()

    api_key = os.environ["GEMINI_API_KEY"]
    model = os.environ.get("GUARDIAN_MODEL") or _DEFAULT_MODEL
    project_root = Path(__file__).parent.parent.absolute()

    provider = GeminiProvider(api_key=api_key, model_name=model)
    collector = ContextCollector(project_root=project_root)
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
