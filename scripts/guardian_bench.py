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

from cgis.guardian.bench import (
    FinderRecording,
    GroundTruth,
    annotate_matches,
    killed_ground_truth,
    load_finder_recording,
    load_ground_truth,
    match_findings,
    save_finder_recording,
    score,
)
from cgis.guardian.chunked import run_review_routed
from cgis.guardian.collector import ContextCollector, parse_features
from cgis.guardian.findings import ReviewResult
from cgis.guardian.providers.base import BaseProvider
from cgis.guardian.review_fingerprint import (
    compute_fingerprint,
    disk_reader,
    resolve_active_providers,
)
from cgis.guardian.runner import build_provider, build_skeptic_provider
from cgis.guardian.skeptic import (
    apply_judgements,
    judge_all,
    skeptic_status_for,
    visible_findings,
)

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


def _ingest_worktree(worktree: Path) -> None:
    """Blocking `cgis ingest` inside the worktree (called via asyncio.to_thread).

    Runs in the worktree on purpose: the PR's own cgis version ingests
    itself, mirroring what guardian CI does on a live PR. uv builds an
    ephemeral venv per worktree (cheap with a warm uv cache).
    """
    try:
        subprocess.run(
            ["uv", "run", "cgis", "ingest", "src", "--output", "graph.db"],
            cwd=worktree,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        _msg = f"cgis ingest failed (rc={exc.returncode}):\n{exc.stderr}"
        raise RuntimeError(_msg) from exc


def _append_jsonl(path: Path, entry: dict[str, object]) -> None:
    """Blocking JSONL append (called via asyncio.to_thread)."""
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


async def _run_one(
    truth: GroundTruth,
    run_idx: int,
    results_path: Path,
    record_finder: Path | None = None,
    replay_finder: Path | None = None,
) -> None:
    """Replay one PR once: worktree → ingest → review → score → JSONL line.

    With ``replay_finder`` the finder is skipped entirely and its recorded
    findings are judged instead, so a skeptic variant is measured against a
    frozen set (spec §4.1) — no worktree, no ingest, no finder tokens.
    With ``record_finder`` the finder pass is written to disk and the skeptic is
    skipped, producing exactly that frozen set.
    """
    provider, model = build_provider(os.environ)
    # Was an isinstance chain (Mistral, then Ollama, else gemini). The provider
    # knows its own name (#375 Task 1).
    primary = provider.name
    # Bench requires explicit opt-in: an implicit skeptic default would silently
    # change scoring vs the committed baseline (review runs keep the implicit default).
    skeptic = (
        build_skeptic_provider(os.environ, primary=primary)
        if os.environ.get("GUARDIAN_SKEPTIC") and record_finder is None
        else None
    )
    features = parse_features(os.environ.get("GUARDIAN_FEATURES", ""))

    if replay_finder is not None:
        recording = load_finder_recording(replay_finder)
        result = await _judge_recording(recording, skeptic)
        await _score_and_record(
            truth, run_idx, results_path, result, model, provider, skeptic, chunks=None
        )
        return

    _git("fetch", "origin", f"pull/{truth.pr}/head")
    with tempfile.TemporaryDirectory(prefix=f"bench-pr{truth.pr}-") as tmp:
        worktree = Path(tmp) / "wt"
        _git("worktree", "add", "--detach", str(worktree), truth.head)
        try:
            await asyncio.to_thread(_ingest_worktree, worktree)
            collector = ContextCollector(
                project_root=worktree,
                db_path=worktree / "graph.db",
                base_ref=truth.base,
                features=features,
                # GUARDIAN_NO_GRAPH → diff-only prompt that fits a small local window.
                include_graph=not (os.environ.get("GUARDIAN_NO_GRAPH") or "").strip(),
            )
            routed = await run_review_routed(
                provider=provider,
                collector=collector,
                skeptic_provider=skeptic[0] if skeptic else None,
            )
            result = routed.result
            if record_finder is not None:
                save_finder_recording(record_finder, result, collector.get_git_diff())
                log.info("Finder pass recorded.", path=str(record_finder), pr=truth.pr)
        finally:
            _git("worktree", "remove", "--force", str(worktree))

    await _score_and_record(
        truth, run_idx, results_path, result, model, provider, skeptic, routed.chunk_count
    )


async def _judge_recording(
    recording: FinderRecording, skeptic: tuple[BaseProvider, str] | None
) -> ReviewResult:
    """Run ONLY the skeptic over a frozen finder pass (spec §4.1)."""
    result = recording.result
    if skeptic is None or not result.findings:
        return result
    judgements = await judge_all(skeptic[0], result.findings, recording.diff)
    judged = sum(1 for j in judgements if j is not None)
    return result.model_copy(
        update={
            "findings": apply_judgements(result.findings, judgements),
            "skeptic_status": skeptic_status_for(judged, len(judgements)),
            "skeptic_judged": judged,
            "skeptic_total": len(judgements),
        }
    )


def _measured_fingerprint(finder_provider: str, skeptic_provider: str | None) -> str | None:
    """The digest of the guardian that just reviewed, or None if it cannot be taken.

    Over `_REPO_ROOT`, not the PR worktree: `_run_one` reviews the PR's code, but
    the guardian doing the reviewing is this checkout's — which is also what
    `guardian_sha` names. Hashing the worktree would fingerprint the subject
    instead of the reviewer.

    Failure degrades to None instead of propagating, and that is the whole
    reason this is a function rather than an expression inside the row literal.
    By the time it runs, the row has already cost a worktree checkout, a
    `cgis ingest`, a paid finder pass and a paid skeptic pass. An exception here
    — `UnknownProviderError` the first time a provider is added, or any reader
    failure — reached `main`'s per-PR `except`, which discarded the whole review
    and wrote an `{"error": ...}` row in its place. That row is
    indistinguishable from a rate-limit failure and drops out of the recall
    corpus permanently, so a reviewer that could not be *named* silently
    destroyed the measurement it had just paid for.

    None is the safe direction and the one the rest of this change already
    takes: an unattributed row is inert, and `guardian_calibrate` carries the
    null through rather than inventing a digest for it. The corpus ratchet in
    `tests/unit/test_backfill_calibration_fingerprint.py` fails on a committed
    row that stayed null, so a degraded run is loud where it matters without
    costing the run itself.
    """
    try:
        active = resolve_active_providers(finder_provider, skeptic_provider)
        return compute_fingerprint(disk_reader(_REPO_ROOT), active)
    except Exception:
        log.exception(
            "Review fingerprint could not be computed; the row is recorded unattributed.",
            finder_provider=finder_provider,
            skeptic_provider=skeptic_provider,
        )
        return None


async def _score_and_record(
    truth: GroundTruth,
    run_idx: int,
    results_path: Path,
    result: ReviewResult,
    model: str,
    provider: BaseProvider,
    skeptic: tuple[BaseProvider, str] | None,
    chunks: int | None,
) -> None:
    """Score one review against ground truth and append the JSONL row."""
    visible = visible_findings(result.findings)
    matches = match_findings(visible, truth)
    bench_score = score(matches, truth)
    skeptic_provider = skeptic[0].name if skeptic else None
    fingerprint = _measured_fingerprint(provider.name, skeptic_provider)
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "pr": truth.pr,
        "run": run_idx,
        "model": model,
        # Measured, never reconstructed: the bench runs from a working tree, so
        # it hashes the bytes that actually ran — including uncommitted edits a
        # `git show` rebuild at `guardian_sha` could not see (#375 spec §6.1).
        # This is the field `scripts/guardian_calibrate.py` carries onto the
        # calibration record, which is the whole point of #390: a measured recall
        # figure that names the reviewer that earned it.
        "review_fingerprint": fingerprint,
        "review_fingerprint_source": "measured" if fingerprint else None,
        "finder_provider": provider.name,
        "skeptic_provider": skeptic_provider,
        "guardian_sha": _git("rev-parse", "HEAD"),
        "features": os.environ.get("GUARDIAN_FEATURES", ""),
        "parse_failed": result.parse_failed,
        "recall": bench_score.recall,
        "precision": bench_score.precision,
        "noise": bench_score.noise,
        "matched": matches.matched,
        "missed": matches.missed,
        # Ground truth the finder DID match and the skeptic then removed —
        # invisible in "missed", which cannot distinguish "never found" from
        # "found then killed" (#270).
        "killed_gt": killed_ground_truth(result.findings, truth),
        # From `matches`, not `bench_score`: both carry `ambiguous_hits`, but
        # MatchResult's is the list of prediction indices and BenchScore's is
        # their count. The list is what belongs in the record — indices let a
        # reader find which findings were exempted, and a count would not.
        # `noise` above is the mirror image, and the asymmetry is deliberate:
        # nothing needs the noise indices, `MatchResult.noise` already has them,
        # and `calibrate.strict_precision` reads the row as
        # `noise + len(ambiguous_hits)`. Swapping either side silently changes
        # the row schema; `test_strict_precision_agrees_with_bench_score` fails
        # if they drift apart.
        "ambiguous_hits": matches.ambiguous_hits,
        # Both providers: in replay mode the finder never runs, so recording
        # only its usage reported a free run for a pass that cost real tokens.
        "prompt_tokens": provider.cumulative_usage.prompt_tokens,
        "completion_tokens": provider.cumulative_usage.completion_tokens,
        "skeptic_prompt_tokens": skeptic[0].cumulative_usage.prompt_tokens if skeptic else 0,
        "skeptic_completion_tokens": (
            skeptic[0].cumulative_usage.completion_tokens if skeptic else 0
        ),
        "chunks": chunks,
        "skeptic_model": skeptic[1] if skeptic else None,
        "skeptic_status": result.skeptic_status,
        "skeptic_judged": result.skeptic_judged,
        "skeptic_total": result.skeptic_total,
        "findings": annotate_matches(result.findings, visible, matches),
    }
    await asyncio.to_thread(_append_jsonl, results_path, entry)
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
    parser.add_argument(
        "--record-finder",
        type=Path,
        default=None,
        help="Write the finder pass here (skeptic skipped) to freeze it for replay.",
    )
    parser.add_argument(
        "--replay-finder",
        type=Path,
        default=None,
        help="Judge a recorded finder pass instead of running the finder.",
    )
    args = parser.parse_args()
    if args.record_finder and args.replay_finder:
        sys.exit("--record-finder and --replay-finder are mutually exclusive.")

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
                await _run_one(
                    truth,
                    run_idx,
                    args.results,
                    record_finder=args.record_finder,
                    replay_finder=args.replay_finder,
                )
            except Exception as exc:  # isolate per-PR failures (spec §7)
                failures += 1
                log.error("PR replay failed.", pr=truth.pr, run=run_idx, error=str(exc))  # noqa: TRY400
                await asyncio.to_thread(
                    _append_jsonl,
                    args.results,
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "pr": truth.pr,
                        "run": run_idx,
                        "error": str(exc),
                    },
                )
    if failures:
        log.warning("Some replays failed.", failures=failures)


if __name__ == "__main__":
    asyncio.run(main())
