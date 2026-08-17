"""Re-judge a frozen finder pass with evidence, paired against its own baseline (#401).

#402 gave the skeptic the repository's checker output. Whether that changes
verdicts is unmeasured, and the obvious comparison — run the guardian twice —
cannot answer it: the finder is sampled, so two runs disagree about *what was
claimed* before anyone judges anything. Every number would mix the effect with
the draw.

A recorded finder pass removes that. `guardian.yml` writes `guardian_finder.json`
on every review, and those findings carry the verdicts of the run that produced
them. So the baseline already exists: the same findings, the same diff, the same
skeptic model, judged without evidence. Re-judging them *with* evidence is one
arm, paired at the level of the individual finding, and it costs one skeptic call
per finding and no finder call at all.

Evidence is collected in a worktree at the reviewed commit, not in the current
checkout. The checkers must report on the code the finder actually saw; today's
tree is a different subject, and a verdict flip caused by that would be
attributed to the evidence.

Every step refuses rather than guesses. A recording without verdicts has no
baseline. Evidence that cannot be collected makes this a plain re-run of the
control arm, which is not the experiment and must not be reported as one.
"""

import argparse
import asyncio
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

from cgis.guardian.diff_index import split_diff_by_file
from cgis.guardian.evidence import Evidence, collect_evidence
from cgis.guardian.providers.base import BaseProvider
from cgis.guardian.recording import load_finder_recording
from cgis.guardian.runner import build_provider, build_skeptic_provider
from cgis.guardian.skeptic import FindingJudgement, judge_all

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Verdicts a recorded finding may carry. A finding outside this set was never
#: ruled on, so it cannot be a baseline for anything.
_VERDICTS = frozenset({"confirmed", "refuted", "uncertain"})

#: Words that mark a rationale as resting on the checker output rather than
#: on the diff. Deliberately broad — a false positive here only inflates the
#: attributable count, which is the number under scrutiny, so it is reported
#: beside the total rather than as a conclusion.
_CHECKER_MENTION = re.compile(r"mypy|ruff|checker|type check|linter|All checks passed", re.I)


class NoBaselineError(RuntimeError):
    """Raised when a recording carries no verdicts to compare against.

    A replay without a baseline produces a column of new verdicts and nothing to
    subtract, which reads like a result and is not one.
    """


class NoEvidenceError(RuntimeError):
    """Raised when evidence cannot be collected at the reviewed commit.

    Refused rather than degraded: judging without evidence is the control arm,
    already recorded. Running it again and labelling it the treatment would make
    the experiment report that evidence changes nothing, which is exactly the
    conclusion it must not be able to reach by accident.
    """


def baseline_verdicts(path: Path) -> list[str]:
    """The verdicts of the run that produced this recording, in finding order.

    Read from the raw JSON rather than through `load_finder_recording`, which
    strips verdicts on purpose — the replay must start from unjudged findings or
    the skeptic is shown its own previous answers.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    findings = raw.get("result", {}).get("findings") or []
    verdicts = [f.get("verdict") for f in findings]
    unruled = [i for i, v in enumerate(verdicts) if v not in _VERDICTS]
    if not verdicts or unruled:
        _msg = (
            f"{path} carries no usable baseline: {len(unruled)} of {len(verdicts)} findings "
            f"have no verdict. A replay needs the control arm it is being compared against."
        )
        raise NoBaselineError(_msg)
    return [str(v) for v in verdicts]


def changed_files(diff: str) -> tuple[str, ...]:
    """The files the finder was shown, taken from the diff it was shown.

    From the recording rather than from git: the point is to check the same
    files the claims are about, and a path list re-derived today would drift
    with the branch.
    """
    return tuple(split_diff_by_file(diff))


@contextlib.contextmanager
def worktree_at(ref: str, repo_root: Path) -> Iterator[Path]:
    """A detached worktree at `ref`, removed on the way out."""
    with tempfile.TemporaryDirectory(prefix="replay-") as tmp:
        path = Path(tmp) / "wt"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(path), ref],
            cwd=repo_root,
            capture_output=True,
            check=True,
        )
        try:
            yield path
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(path)],
                cwd=repo_root,
                capture_output=True,
                check=False,
            )


def flips(baseline: Sequence[str], replayed: Sequence[FindingJudgement | None]) -> Counter[str]:
    """Counts of `baseline -> replayed`, positionally paired.

    Positional because `judge_all` returns one entry per finding in order and
    `apply_judgements` relies on that same alignment. A failed call is its own
    outcome rather than folded into a verdict: absence of a judgement is not a
    judgement, and counting it as one would let an API outage look like the
    skeptic changing its mind.
    """
    if len(baseline) != len(replayed):
        _msg = f"{len(baseline)} baseline verdicts against {len(replayed)} replayed; not paired."
        raise ValueError(_msg)
    return Counter(
        f"{was} -> {now.verdict if now is not None else 'call failed'}"
        for was, now in zip(baseline, replayed, strict=True)
    )


def cites_a_checker(judgement: FindingJudgement | None) -> bool:
    """Whether the rationale points at the evidence rather than at the diff.

    This is the attributable half. A flip whose reason is "the quoted code does
    not do what the claim says" is the skeptic working as it always has; a flip
    whose reason is "ruff reported All checks passed" is the evidence doing
    something, and only the second supports any claim about this feature.
    """
    if judgement is None:
        return False
    return bool(_CHECKER_MENTION.search(judgement.rationale or ""))


async def replay(
    recording_path: Path, ref: str, repo_root: Path, env: Mapping[str, str]
) -> tuple[Counter[str], int]:
    """Judge the recorded findings again, with evidence; return the flips and citations."""
    baseline = baseline_verdicts(recording_path)
    recording = load_finder_recording(recording_path)
    findings = recording.result.findings
    if len(findings) != len(baseline):
        _msg = f"{len(findings)} findings loaded against {len(baseline)} baseline verdicts."
        raise NoBaselineError(_msg)

    # The worktree and the checkers are blocking, and `replay` is a coroutine.
    # Nothing else runs on this script's loop today, so the harm is latent rather
    # than active — but the function is importable and its signature promises a
    # coroutine, and two minutes of a frozen loop is not something a caller
    # should have to read the body to discover.
    evidence = await asyncio.to_thread(_evidence_at, ref, repo_root, recording.diff)
    if evidence is None:
        _msg = (
            f"No evidence could be collected at {ref}. Judging without it repeats the "
            f"control arm, which is already recorded; reporting that as the treatment "
            f"would make this experiment conclude that evidence changes nothing."
        )
        raise NoEvidenceError(_msg)
    judgements = await judge_all(skeptic_from(env), findings, recording.diff, evidence=evidence)
    return flips(baseline, judgements), sum(1 for j in judgements if cites_a_checker(j))


def _evidence_at(ref: str, repo_root: Path, diff: str) -> Evidence | None:
    """Checker output for the recorded files, taken at the reviewed commit.

    The worktree lives only as long as the collection: the checkers read the
    tree, and nothing afterwards needs it.
    """
    with worktree_at(ref, repo_root) as tree:
        return collect_evidence(tree, changed_files(diff))


def skeptic_from(env: Mapping[str, str]) -> BaseProvider:
    """The configured skeptic, or a refusal naming what is missing.

    The finder is built only to learn its provider name, which decides the
    skeptic's default opposite — no finder call is ever made here, and that is
    the whole economy of this arm.
    """
    finder, _ = build_provider(env)
    skeptic = build_skeptic_provider(env, primary=finder.name)
    if skeptic is None:
        _msg = (
            "No skeptic is configured, so there is nothing to replay. Set GUARDIAN_SKEPTIC "
            "and the matching API key."
        )
        raise NoBaselineError(_msg)
    return skeptic[0]


def main() -> int:
    """Replay one recording and print the paired comparison."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recording", type=Path, required=True)
    parser.add_argument("--at", required=True, help="ref or sha the recording was taken at")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args()

    matrix, cited = asyncio.run(replay(args.recording, args.at, args.repo_root, os.environ))
    total = sum(matrix.values())
    print(f"{total} findings re-judged with evidence, paired against their recorded verdicts\n")
    for transition, count in sorted(matrix.items()):
        moved = (
            "" if transition.split(" -> ")[0] == transition.split(" -> ")[1] else "   <- changed"
        )
        print(f"  {transition:34} {count:3}{moved}")
    print(f"\n  rationales citing a checker: {cited}/{total}")
    print("  (only these are attributable to the evidence; the rest are the skeptic reading code)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
