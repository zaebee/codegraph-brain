"""CLI entry point for running a Guardian AI code review."""

import argparse
import asyncio
import os
from pathlib import Path

import structlog

from cgis.guardian.collector import ContextCollector
from cgis.guardian.runner import build_provider, run_guardian

log = structlog.getLogger(__name__)


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
    parser.add_argument(
        "--base-branch",
        default="main",
        help="Base branch for git diff (default: main). Use the PR target branch.",
    )
    args = parser.parse_args()

    provider, model = build_provider(os.environ)
    project_root = Path(__file__).parent.parent.absolute()
    collector = ContextCollector(
        project_root=project_root, db_path=args.db, base_branch=args.base_branch
    )
    log.info("Running guardian review...", model=model)
    report = await run_guardian(
        provider=provider,
        model=model,
        collector=collector,
        pr=args.pr,
        metrics_path=args.metrics,
    )

    if args.output:
        safe_root = Path.cwd().resolve()
        output_path = (safe_root / args.output).resolve()
        if not output_path.is_relative_to(safe_root):
            _msg = f"--output must be within the working directory: {output_path}"
            raise ValueError(_msg)
        output_path.write_text(report)
        log.info("Review written to file.", path=str(output_path))
    else:
        print(report)


if __name__ == "__main__":
    asyncio.run(main())
