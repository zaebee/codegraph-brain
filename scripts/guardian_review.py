"""CLI entry point for running a Guardian AI code review."""

import argparse
import asyncio
import os
from pathlib import Path

import structlog

from cgis.guardian.collector import ContextCollector, parse_features
from cgis.guardian.metrics import reject_metrics_path
from cgis.guardian.review_fingerprint import compute_fingerprint, disk_reader
from cgis.guardian.runner import (
    build_provider,
    build_skeptic_provider,
    impact_threshold,
    run_guardian,
)

log = structlog.getLogger(__name__)


def _append_github_output(path: str, posted_inline: bool) -> None:
    """Blocking append of the posted_inline workflow output (called via asyncio.to_thread)."""
    with Path(path).open("a", encoding="utf-8") as fh:
        fh.write(f"posted_inline={'true' if posted_inline else 'false'}\n")


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
    parser.add_argument(
        "--record-finder",
        type=Path,
        default=None,
        help="Write the finder pass (findings + diff) to this .json for offline re-scoring.",
    )
    parser.add_argument(
        "--inline",
        action="store_true",
        help="Post findings as an inline GitHub review; fall back to the report file on failure.",
    )
    args = parser.parse_args()

    # Checked here as well as inside record_review, because record_review runs
    # last: an unusable --metrics would otherwise surface only after every LLM
    # call is paid for. `--output` below has had the same shape of guard all
    # along; --metrics was simply missed (#347).
    refusal = reject_metrics_path(args.metrics)
    if refusal is not None:
        _msg = f"Refusing metrics path '{args.metrics}': {refusal}."
        raise ValueError(_msg)

    provider, model = build_provider(os.environ)
    # Was an isinstance sniff that returned "gemini" for an Ollama provider,
    # which then picked the wrong default skeptic. The provider knows its own
    # name (#375 Task 1).
    primary = provider.name
    skeptic = build_skeptic_provider(os.environ, primary=primary)
    features = parse_features(os.environ.get("GUARDIAN_FEATURES", ""))
    inline_repo = os.environ.get("GITHUB_REPOSITORY") if args.inline else None
    project_root = Path(__file__).parent.parent.absolute()
    # Computed here rather than inside runner.py: nothing in the review closure
    # may import the fingerprint module, or the hasher enters its own hashed set
    # (#375 §4.2). project_root is this repository, not the reviewed checkout.
    active_providers = frozenset({provider.name} | ({skeptic[0].name} if skeptic else set()))
    review_fingerprint = compute_fingerprint(disk_reader(project_root), active_providers)
    collector = ContextCollector(
        project_root=project_root, db_path=args.db, base_branch=args.base_branch, features=features
    )
    log.info("Running guardian review...", model=model)
    report, posted_inline = await run_guardian(
        provider=provider,
        model=model,
        collector=collector,
        pr=args.pr,
        metrics_path=args.metrics,
        skeptic=skeptic,
        inline_repo=inline_repo,
        threshold=impact_threshold(os.environ),
        record_finder=args.record_finder,
        review_fingerprint=review_fingerprint,
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

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        await asyncio.to_thread(_append_github_output, github_output, posted_inline)


if __name__ == "__main__":
    asyncio.run(main())
