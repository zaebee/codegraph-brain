"""Rebuild frozen finder passes from the recorded corpus, without paying a model (#246).

`--replay-finder` isolates the skeptic: the finder is skipped and its recorded
findings are judged instead, so a skeptic variant costs skeptic calls only. That
is what makes #246 answerable on a budget — but it needs recordings, and none
were ever written to disk.

They do not have to be re-run. `benchmarks/guardian/results.jsonl` already holds
the findings of every scored review, and the diff they were found in is
regenerable from git, because the benchmark reviews this repository's own PRs
and every fixture's `base` and `head` resolve locally. So a recording costs one
`git diff` and nothing else.

**Only rows that had no skeptic are eligible**, and the reason is not
tidiness. `load_finder_recording` strips verdicts on read, so a judged row would
not smuggle its verdicts into the next arm — but `apply_judgements` rewrites
confidence to `round(confidence * 0.9)` on an `uncertain` verdict and the load
path does not restore it (#279). A recording taken from a judged run therefore
carries confidences that are not the finder's own, on exactly the subset a
skeptic experiment is about.

Verified before use rather than after: `tests/unit/test_recordings_from_corpus.py`
re-scores every rebuilt recording and reproduces the number the corpus recorded —
72 of 72, under the scoring policy in force when each row was written. The seven
that differ under today's rule are exactly the seven carrying `ambiguous_hits`,
which is the #345 policy change that `CURATION.md` says results.jsonl is
deliberately not rescored for.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from cgis.guardian.findings import Finding, ReviewResult
from cgis.guardian.recording import save_finder_recording

#: Fields `annotate_matches` adds to a stored finding. Stripped on the way back
#: to a `Finding`, which does not declare them.
_ANNOTATIONS = ("matched", "verdict", "skeptic_note")


class NotAFrozenPassError(RuntimeError):
    """Raised when a row cannot stand in for an unjudged finder pass.

    Refused rather than cleaned up: a row whose findings were already judged has
    confidences the skeptic rewrote (#279), and silently accepting it would make
    two arms of an experiment differ by something nobody chose.
    """


class MissingFixtureError(RuntimeError):
    """Raised when a row names a PR with no ground-truth fixture, or unresolvable shas.

    The diff is the whole reason a replay needs no worktree. Without one there
    is nothing to record, and inventing an empty diff would produce a recording
    that replays as "the finder saw nothing".
    """


def is_frozen_pass(row: dict[str, Any]) -> bool:
    """True for a scored row whose findings are the finder's own, unjudged."""
    if "matched" not in row or "precision" not in row:
        return False
    if row.get("skeptic_model"):
        return False
    return all(f.get("verdict") is None for f in (row.get("findings") or []))


def row_key(row: dict[str, Any]) -> str:
    """`pr@timestamp` — the same identity `guardian_calibrate` writes.

    Used as the recording's filename so a replayed number can be traced to the
    exact run it came from. Without that, a green replay could mean "reproduced
    *a* run", which is not the claim being made.
    """
    return f"{row['pr']}@{row['timestamp']}"


def diff_for(pr: int, bench_dir: Path, repo_root: Path) -> str:
    """The diff the finder saw, regenerated from the fixture's base and head.

    `base...head`, three dots, mirroring `ContextCollector._diff_range` — the
    two-dot form would include everything that landed on the base branch after
    the PR was cut, which is not what any recorded review was shown.
    """
    fixture = bench_dir / f"pr-{pr}.yaml"
    if not fixture.is_file():
        _msg = f"No ground-truth fixture for pr-{pr} at {fixture}."
        raise MissingFixtureError(_msg)
    spec = yaml.safe_load(fixture.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        _msg = (
            f"pr-{pr}: {fixture} is not a mapping ({type(spec).__name__}); "
            f"it names no base or head."
        )
        raise MissingFixtureError(_msg)
    base, head = spec.get("base"), spec.get("head")
    for name, sha in (("base", base), ("head", head)):
        if (
            not sha
            or subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"],
                capture_output=True,
                cwd=repo_root,
                check=False,
            ).returncode
        ):
            _msg = (
                f"pr-{pr}: {name} {sha!r} does not resolve in {repo_root}. Fixture shas are a "
                f"second population beside the corpora's `guardian_sha`, and PR heads are never "
                f"ancestors of the trunk because everything here is squash-merged — so they "
                f"survive only if pinned. Publish it: "
                f"git push origin {sha}:refs/tags/bench/fixture/pr-{pr}-{name}"
            )
            raise MissingFixtureError(_msg)
    result = subprocess.run(
        ["git", "diff", f"{base}...{head}"],
        capture_output=True,
        text=True,
        cwd=repo_root,
        check=True,
    )
    if not result.stdout.strip():
        _msg = f"pr-{pr}: {base}...{head} is an empty diff; a review of nothing is not a pass."
        raise MissingFixtureError(_msg)
    return result.stdout


def recording_for(row: dict[str, Any], diff: str) -> tuple[ReviewResult, str]:
    """A `ReviewResult` + diff pair equivalent to what the finder produced.

    `summary` is empty because the corpus never stored one. Nothing downstream
    reads it — the skeptic judges findings against diff hunks and the scorer
    counts matches — so an empty string is honest about what is known rather
    than a reconstruction of prose nobody kept.
    """
    if not is_frozen_pass(row):
        _msg = (
            f"{row_key(row)} is not an unjudged finder pass (skeptic={row.get('skeptic_model')!r})."
        )
        raise NotAFrozenPassError(_msg)
    findings = [
        Finding.model_validate({k: v for k, v in f.items() if k not in _ANNOTATIONS})
        for f in (row.get("findings") or [])
    ]
    return ReviewResult(
        findings=findings, summary="", parse_failed=bool(row.get("parse_failed"))
    ), diff


def build(results: Path, out_dir: Path, bench_dir: Path, repo_root: Path) -> list[Path]:
    """Write one recording per frozen pass; return the paths written."""
    rows = [
        json.loads(line)
        for line in results.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    frozen = [r for r in rows if is_frozen_pass(r)]
    out_dir.mkdir(parents=True, exist_ok=True)
    diffs: dict[int, str] = {}
    written: list[Path] = []
    for row in frozen:
        pr = row["pr"]
        if pr not in diffs:
            diffs[pr] = diff_for(pr, bench_dir, repo_root)
        result, diff = recording_for(row, diffs[pr])
        path = out_dir / f"{row_key(row).replace(':', '-')}.json"
        save_finder_recording(path, result, diff)
        written.append(path)
    return written


def main() -> int:
    """Rebuild every frozen finder pass and report what was written."""
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results", type=Path, default=repo_root / "benchmarks/guardian/results.jsonl"
    )
    parser.add_argument("--out", type=Path, default=repo_root / ".guardian-recordings")
    parser.add_argument("--bench-dir", type=Path, default=repo_root / "benchmarks/guardian")
    parser.add_argument("--repo-root", type=Path, default=repo_root)
    args = parser.parse_args()
    written = build(args.results, args.out, args.bench_dir, args.repo_root)
    print(f"{len(written)} recordings written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
