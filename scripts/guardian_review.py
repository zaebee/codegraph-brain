"""CLI entry point for running a Guardian AI code review."""

import argparse
import asyncio
import os
from collections.abc import Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath

import structlog

import cgis
from cgis.guardian.collector import ContextCollector, parse_features
from cgis.guardian.evidence import EVIDENCE_FLAG
from cgis.guardian.metrics import reject_metrics_path
from cgis.guardian.review_fingerprint import (
    compute_fingerprint,
    disk_reader,
    resolve_active_providers,
)
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


def reviewed_root(requested: Path | None, project_root: Path, env: Mapping[str, str]) -> Path:
    """The checkout to review: `--repo-root`, or this repository when it is not given.

    A foreign checkout is data only (#730): the evidence pass would run
    `uv run mypy/ruff` inside it, which executes code and config the PR's author
    controls while the job holds provider keys, so that combination is refused.
    """
    this_repository = project_root.resolve()
    if requested is None:
        return this_repository
    root = requested.resolve()
    if not root.is_dir():
        _msg = f"--repo-root is not a directory: {requested}"
        raise ValueError(_msg)
    if root != this_repository and (env.get(EVIDENCE_FLAG) or "").strip() == "1":
        _msg = (
            f"{EVIDENCE_FLAG}=1 runs the reviewed checkout's own toolchain, and {requested} "
            f"is not this repository; unset it for a --repo-root review (#730)."
        )
        raise ValueError(_msg)
    return root


def relative_source_root(raw: str) -> str:
    """`--source-root` as a plain relative path; `..` or an absolute path is refused.

    It arrives from a workflow input (#739). Reads are already confined to the
    checkout (#731), so this is about failing loudly on a wrong value rather
    than reviewing with no graph context.
    """
    path = PurePosixPath(raw or ".")
    # A backslash or a drive has no use in a repository path, and on another
    # platform each hides an escape. Whitespace is refused rather than stripped:
    # the workflow passes the raw value to the ingest, which does not strip it.
    if (
        path.is_absolute()
        or PureWindowsPath(raw).is_absolute()
        or PureWindowsPath(raw).drive
        or ".." in path.parts
        or "\\" in raw
        or any(c.isspace() for c in raw)
    ):
        _msg = f"--source-root must be a path inside the checkout: {raw!r}"
        raise ValueError(_msg)
    return "" if str(path) == "." else str(path)


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
        "--repo-root",
        type=Path,
        default=None,
        help=(
            "The checkout to review (default: this repository). guardian.yml passes the PR "
            "checkout here, so the PR is read and parsed but none of its code runs (#730)."
        ),
    )
    parser.add_argument(
        "--source-root",
        default="src",
        help=(
            "The ingest root inside --repo-root that graph.db was built from (default: src); "
            "changed-file paths are stripped of it before graph lookups (#739)."
        ),
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

    project_root = Path(__file__).parent.parent.absolute()
    repo_root = reviewed_root(args.repo_root, project_root, os.environ)
    source_root = relative_source_root(args.source_root)

    provider, model = build_provider(os.environ)
    # Was an isinstance sniff that returned "gemini" for an Ollama provider,
    # which then picked the wrong default skeptic. The provider knows its own
    # name (#375 Task 1).
    primary = provider.name
    skeptic = build_skeptic_provider(os.environ, primary=primary)
    features = parse_features(os.environ.get("GUARDIAN_FEATURES", ""))
    inline_repo = os.environ.get("GITHUB_REPOSITORY") if args.inline else None
    # Computed here rather than inside runner.py: nothing in the review closure
    # may import the fingerprint module, or the hasher enters its own hashed set
    # (#375 §4.2). project_root is this repository, not the reviewed checkout.
    active = resolve_active_providers(provider.name, skeptic[0].name if skeptic else None)
    review_fingerprint = compute_fingerprint(disk_reader(project_root), active)
    collector = ContextCollector(
        project_root=repo_root,
        db_path=args.db,
        base_branch=args.base_branch,
        source_root=source_root,
        features=features,
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
    cgis.log_to_stderr()  # stdout is the script's output (#619)
    asyncio.run(main())
