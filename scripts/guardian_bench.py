"""Replay guardian on past PRs and score against curated ground truth (spec §3.4).

Usage:
    uv run python scripts/guardian_bench.py            # all benchmarks/guardian/pr-*.yaml
    uv run python scripts/guardian_bench.py --pr 144 --runs 3

Requires: full git history (refuses shallow clones), one provider API key.
Appends one JSON line per (pr, run) to benchmarks/guardian/results.jsonl.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import structlog

from cgis.guardian.bench import GroundTruth, load_ground_truth, match_findings, score
from cgis.guardian.collector import ContextCollector
from cgis.guardian.core import GuardianReviewer
from cgis.guardian.runner import build_provider

log = structlog.getLogger(__name__)

_REPO_ROOT = Path(__file__).parent.parent.absolute()
_BENCH_DIR = _REPO_ROOT / "benchmarks" / "guardian"


def _git(*args: str, cwd: Path = _REPO_ROOT) -> str:
    """Run a git command, return stdout, raise on failure."""
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _ensure_full_history() -> None:
    """Refuse to run in a shallow clone — the merge base would be missing."""
    if _git("rev-parse", "--is-shallow-repository") == "true":
        sys.exit("guardian_bench requires full git history; run: git fetch --unshallow")


async def _run_one(truth: GroundTruth, run_idx: int, results_path: Path) -> None:
    """Replay one PR once: worktree → ingest → review → score → JSONL line."""
    provider, model = build_provider(os.environ)
    _git("fetch", "origin", f"pull/{truth.pr}/head")
    with tempfile.TemporaryDirectory(prefix=f"bench-pr{truth.pr}-") as tmp:
        worktree = Path(tmp) / "wt"
        _git("worktree", "add", "--detach", str(worktree), truth.head)
        try:
            try:
                # Runs in the worktree on purpose: the PR's own cgis version ingests
                # itself, mirroring what guardian CI does on a live PR. uv builds an
                # ephemeral venv per worktree (cheap with a warm uv cache).
                subprocess.run(  # noqa: ASYNC221 — intentional blocking ingest, no async alternative
                    ["uv", "run", "cgis", "ingest", "src", "--output", "graph.db"],
                    cwd=worktree,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                _msg = f"cgis ingest failed (rc={exc.returncode}):\n{exc.stderr}"
                raise RuntimeError(_msg) from exc
            collector = ContextCollector(
                project_root=worktree,
                db_path=worktree / "graph.db",
                base_ref=truth.base,
            )
            reviewer = GuardianReviewer(provider=provider, context_collector=collector)
            result = await reviewer.run_review()
        finally:
            _git("worktree", "remove", "--force", str(worktree))

    matches = match_findings(result.findings, truth)
    bench_score = score(matches, truth)
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "pr": truth.pr,
        "run": run_idx,
        "model": model,
        "guardian_sha": _git("rev-parse", "HEAD"),
        "features": os.environ.get("GUARDIAN_FEATURES", ""),
        "parse_failed": result.parse_failed,
        "recall": bench_score.recall,
        "precision": bench_score.precision,
        "noise": bench_score.noise,
        "matched": matches.matched,
        "missed": matches.missed,
        "ambiguous_hits": matches.ambiguous_hits,
        "prompt_tokens": provider.last_usage.prompt_tokens,
        "completion_tokens": provider.last_usage.completion_tokens,
        "findings": [f.model_dump() for f in result.findings],
    }
    with results_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    log.info(
        "Scored.",
        pr=truth.pr,
        run=run_idx,
        recall=bench_score.recall,
        noise=bench_score.noise,
    )


async def main() -> None:
    """Run the benchmark over selected PRs, isolating per-PR failures."""
    parser = argparse.ArgumentParser(description="Replay guardian on past PRs and score.")
    parser.add_argument(
        "--pr",
        type=int,
        action="append",
        default=None,
        help="PR number(s) to run; default: every pr-*.yaml",
    )
    parser.add_argument("--runs", type=int, default=1, help="Repetitions per PR (default 1).")
    parser.add_argument("--results", type=Path, default=_BENCH_DIR / "results.jsonl")
    args = parser.parse_args()

    _ensure_full_history()
    args.results.parent.mkdir(parents=True, exist_ok=True)
    paths = sorted(_BENCH_DIR.glob("pr-*.yaml"))
    truths = [load_ground_truth(p) for p in paths]
    if args.pr:
        truths = [t for t in truths if t.pr in set(args.pr)]
    if not truths:
        sys.exit("No ground-truth files selected.")

    failures = 0
    for truth in truths:
        for run_idx in range(args.runs):
            try:
                await _run_one(truth, run_idx, args.results)
            except Exception as exc:  # isolate per-PR failures (spec §7)
                failures += 1
                log.error("PR replay failed.", pr=truth.pr, run=run_idx, error=str(exc))  # noqa: TRY400
                with args.results.open("a", encoding="utf-8") as fh:
                    fh.write(
                        json.dumps(
                            {
                                "timestamp": datetime.now(UTC).isoformat(),
                                "pr": truth.pr,
                                "run": run_idx,
                                "error": str(exc),
                            }
                        )
                        + "\n"
                    )
    if failures:
        log.warning("Some replays failed.", failures=failures)


if __name__ == "__main__":
    asyncio.run(main())
