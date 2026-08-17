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
baseline. Evidence that cannot be collected turns the treatment into a plain
re-run without evidence, which is not the experiment and must not be reported
as one.

`--control` re-judges the same findings *without* evidence, and it is not
optional rigour. The recorded verdicts are one draw, not a fixed point:
`gemini.py` sends no temperature and `guardian.yml` sets none, so the skeptic
runs at the provider default and answers a second asking differently. Baseline
against the evidence arm therefore measures the effect *plus* the resampling
noise, and only a no-evidence replay of the same findings separates them. That
is the confound this module removes at the finder, reappearing one level down at
the skeptic — an earlier draft of this file argued the control arm was "already
recorded", which holds only if the skeptic is deterministic, and it is not.
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
from cgis.guardian.findings import Finding
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


class NoSkepticError(RuntimeError):
    """Raised when no skeptic is configured, so there is nothing to replay.

    Its own type rather than `NoBaselineError`, whose docstring says "a recording
    carries no verdicts". Reusing it here would make that sentence false and put
    a configuration problem and a data problem behind one name — and the two have
    nothing in common but the moment they are noticed.
    """


class NoEvidenceError(RuntimeError):
    """Raised when evidence cannot be collected at the reviewed commit.

    Refused rather than degraded: judging without evidence is the control arm,
    which `--control` runs deliberately and labels as such. Arriving there by
    accident and reporting it as the treatment would make the experiment
    conclude that evidence changes nothing — the one conclusion it must not be
    able to reach by mistake.
    """


def baseline_verdicts(path: Path) -> list[str]:
    """The verdicts of the run that produced this recording, in finding order.

    Read from the raw JSON rather than through `load_finder_recording`, which
    strips verdicts on purpose — the replay must start from unjudged findings or
    the skeptic is shown its own previous answers.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    # `or {}` rather than a `.get` default: a key present and explicitly null
    # returns the null, not the default, so `{"result": null}` would reach
    # `.get("findings")` as None and raise AttributeError. This module promises
    # `NoBaselineError` for a recording it cannot use; crashing with a type error
    # instead makes that promise false for one shape of bad input.
    findings = (raw.get("result") or {}).get("findings") or []
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
        try:
            subprocess.run(
                ["git", "worktree", "add", "--detach", str(path), ref],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            # `CalledProcessError` stringifies to "Command '[...]' returned
            # non-zero exit status 1" and drops the captured stderr entirely —
            # the same reasoning `fetch_changed_files` records in
            # guardian_martian.py:138. Here the lost message is the one that
            # says whether the ref is unknown, the tree is locked, or the path
            # exists, and those have three different fixes.
            _msg = f"Cannot create a worktree at {ref!r}: {exc.stderr.strip() or '(no stderr)'}"
            raise RuntimeError(_msg) from exc
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
    recording_path: Path,
    ref: str | None,
    repo_root: Path,
    env: Mapping[str, str],
    *,
    with_evidence: bool = True,
    out: Path | None = None,
) -> tuple[Counter[str], int]:
    """Judge the recorded findings again; return the flips and the citation count.

    `with_evidence=False` is the control arm: the same findings, the same diff,
    the same skeptic, judged without checker output. Its flips are the floor a
    treatment result has to clear, because the skeptic is sampled and the
    recorded baseline is one draw of it.

    `out` writes the per-finding rows. The matrix says how many verdicts moved
    and never which, so it cannot answer the question that decides whether this
    feature is safe to enable: an arm that refutes eight findings is an
    improvement if all eight were false and a regression if one was the real
    one. Recall is a property of *which*, so the aggregate is not evidence about
    it in either direction.
    """
    baseline = baseline_verdicts(recording_path)
    recording = load_finder_recording(recording_path)
    findings = recording.result.findings
    if len(findings) != len(baseline):
        _msg = f"{len(findings)} findings loaded against {len(baseline)} baseline verdicts."
        raise NoBaselineError(_msg)

    evidence = None
    if with_evidence:
        if ref is None:
            _msg = "The evidence arm needs the commit the recording was taken at; none was given."
            raise NoEvidenceError(_msg)
        # The worktree and the checkers are blocking, and `replay` is a coroutine.
        # Nothing else runs on this script's loop today, so the harm is latent
        # rather than active — but the function is importable and its signature
        # promises a coroutine, and two minutes of a frozen loop is not something
        # a caller should have to read the body to discover.
        evidence = await asyncio.to_thread(_evidence_at, ref, repo_root, recording.diff)
        if evidence is None:
            _msg = (
                f"No evidence could be collected at {ref}. Judging without it is the control "
                f"arm, which --control runs on purpose; reaching it by accident and reporting "
                f"it as the treatment would make this experiment conclude that evidence "
                f"changes nothing."
            )
            raise NoEvidenceError(_msg)
    judgements = await judge_all(skeptic_from(env), findings, recording.diff, evidence=evidence)
    if out is not None:
        write_rows(out, findings, baseline, judgements)
    return flips(baseline, judgements), sum(1 for j in judgements if cites_a_checker(j))


def write_rows(
    out: Path,
    findings: Sequence[Finding],
    baseline: Sequence[str],
    judgements: Sequence[FindingJudgement | None],
) -> None:
    """One JSON line per finding: what was claimed, what was ruled, and why.

    The rationale is kept whole rather than summarised. Reading why a claim was
    withdrawn is how "the checker says otherwise" is told apart from "the model
    changed its mind", and a truncated reason answers neither.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for index, (finding, was, now) in enumerate(
            zip(findings, baseline, judgements, strict=True)
        ):
            handle.write(
                json.dumps(
                    {
                        "index": index,
                        "file": finding.file,
                        "line": finding.line,
                        "title": finding.title,
                        "was": was,
                        "now": now.verdict if now is not None else None,
                        "cites_a_checker": cites_a_checker(now),
                        "rationale": now.rationale if now is not None else None,
                    }
                )
                + "\n"
            )


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
        raise NoSkepticError(_msg)
    return skeptic[0]


def main() -> int:
    """Replay one recording and print the paired comparison."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recording", type=Path, required=True)
    # Exactly one arm per invocation, enforced by argparse rather than by two
    # hand-written checks. Both ways of getting it wrong are the same failure —
    # the run reports an arm it did not perform. Neither flag silently ran the
    # treatment without a commit; `--control --at <sha>` silently ran the
    # control while naming a commit, which reads back as a treatment result and
    # is exactly the mislabelling this module exists to refuse. Caught in review
    # of #406, in the pull request about mislabelled arms.
    arm = parser.add_mutually_exclusive_group(required=True)
    arm.add_argument("--at", help="ref or sha the recording was taken at; the evidence arm")
    arm.add_argument(
        "--control",
        action="store_true",
        help="judge without evidence, to measure how much the sampled skeptic moves on its own",
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--out", type=Path, help="write one JSON line per finding: was, now, and the rationale"
    )
    args = parser.parse_args()

    matrix, cited = asyncio.run(
        replay(
            args.recording,
            args.at,
            args.repo_root,
            os.environ,
            with_evidence=not args.control,
            out=args.out,
        )
    )
    arm = "without evidence (control)" if args.control else "with evidence"
    total = sum(matrix.values())
    print(f"{total} findings re-judged {arm}, paired against their recorded verdicts\n")
    for transition, count in sorted(matrix.items()):
        moved = (
            "" if transition.split(" -> ")[0] == transition.split(" -> ")[1] else "   <- changed"
        )
        print(f"  {transition:34} {count:3}{moved}")
    print(f"\n  rationales citing a checker: {cited}/{total}")
    if args.control:
        # No checker output was supplied, so a citation here is the skeptic
        # asserting what a checker would say. That is the same unverified move
        # the finder made, and it is worth counting rather than assuming zero.
        print("  (no evidence was supplied, so these are claims about checkers, not readings)")
    else:
        print(
            "  (only these are attributable to the evidence; the rest are the skeptic reading code)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
