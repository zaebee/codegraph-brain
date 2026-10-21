"""Replay a skeptic over frozen Martian reviews, scored on their judge grids (#246 G).

Experiment F passed its gate on the corpus its verdict was designed from. This
is the holdout: B's frozen finder reviews of the Martian P3 PRs, which that
design never looked at, judged once already against the golden comments.

No finder call and no judge call are made. For each review, the skeptic rules
on the candidates the judge saw (`candidate_findings`), over the PR's diff
(`git diff base...head`, the range the finder was shown). The stored judge grid
(golden x candidate, row-major) then says what was matched before the skeptic
and what is still matched among the findings it left visible:

* ``killed`` — goldens matched by the whole review but not by the visible part;
* ``noise_after`` — visible candidates the maximum matching leaves unused.

A grid with a failed pair, or sized for a different candidate count, is not
scored: guessing a pair would decide the very number being measured.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cgis
from cgis.guardian.calibrate import assign_from_grid
from cgis.guardian.findings import Finding
from cgis.guardian.martian import ReviewRecord, candidate_findings
from cgis.guardian.providers.base import BaseProvider
from cgis.guardian.skeptic import apply_judgements, judge_all, visible_findings

sys.path.insert(0, str(Path(__file__).resolve().parent))

from guardian_martian import judgeable, load_reviews
from run_budget import Budget, make_budget
from skeptic_arms import UNKNOWN_VENDOR, NoArmError, _vendor, arm_provider


@dataclass(frozen=True)
class JudgeGrid:
    """One judged review's grid, with what it was judged as — the checks a pairing must pass."""

    project: str
    n_goldens: int
    n_candidates: int
    decisions: list[int]


@dataclass(frozen=True)
class GridScore:
    """What a judge grid says about one review before and after the skeptic."""

    matched_before: int
    matched_after: int
    killed: int
    noise_before: int
    noise_after: int


def score_against_grid(
    grid: Sequence[int], n_goldens: int, n_candidates: int, visible: Sequence[int]
) -> GridScore:
    """Matched, killed and noise from a maximum matching, before and after the skeptic."""
    if len(grid) != n_goldens * n_candidates:
        _msg = f"grid of {len(grid)} does not fit {n_goldens} goldens x {n_candidates} candidates"
        raise ValueError(_msg)
    before = assign_from_grid(grid, n_goldens, n_candidates)
    columns = list(visible)
    sub = [grid[g * n_candidates + c] for g in range(n_goldens) for c in columns]
    after = assign_from_grid(sub, n_goldens, len(columns))
    return GridScore(
        matched_before=len(before),
        matched_after=len(after),
        killed=len(before) - len(after),
        noise_before=n_candidates - len(before),
        noise_after=len(columns) - len(after),
    )


def clone_dir(url: str, workspace: Path) -> Path:
    """The Martian clone a PR's URL lives in (`owner__repo`), as `guardian_martian` makes it."""
    owner, repo = url.split("/")[3:5]
    return workspace / f"{owner}__{repo}"


def pr_diff(record: ReviewRecord, workspace: Path) -> str:
    """The diff the finder was shown: `base...head`, from the commits, no checkout."""
    result = subprocess.run(
        [
            "git",
            "-C",
            str(clone_dir(record.url, workspace)),
            "diff",
            f"{record.base_sha}...{record.head_sha}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def load_grids(path: Path) -> dict[tuple[str, str], JudgeGrid]:
    """(url, arm) → grid for every fully ruled judged row; the last row of a key wins.

    Keyed as the judge keys its own rows (review of #726): by url alone, a graph
    and an ablated row of one PR overwrote each other, and a failed row for one
    arm dropped the other's grid. A later row that failed or does not fit
    removes the key rather than leaving an older grid standing, and a file
    holding several judges is refused — its grids would not be one measurement.
    """
    grids: dict[tuple[str, str], JudgeGrid] = {}
    judges: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        judges.add(str(row["judge_model"]))
        key = (str(row["url"]), str(row.get("arm", "graph")))
        decisions = row["decisions"]
        if any(d is None for d in decisions) or len(decisions) != (
            row["n_goldens"] * row["n_candidates"]
        ):
            grids.pop(key, None)
            continue
        grids[key] = JudgeGrid(
            project=str(row["project"]),
            n_goldens=int(row["n_goldens"]),
            n_candidates=int(row["n_candidates"]),
            decisions=[int(d) for d in decisions],
        )
    if len(judges) > 1:
        _msg = f"{path} holds grids from several judges: {sorted(judges)}"
        raise ValueError(_msg)
    return grids


async def replay_one(
    provider: BaseProvider,
    findings: list[Finding],
    diff: str,
    grid: list[int],
    n_goldens: int,
    *,
    misdirected: bool,
) -> dict[str, Any]:
    """One review's candidates judged by the skeptic, and the grid's verdict on what it hid."""
    judgements = await judge_all(provider, findings, diff, evidence=None, misdirected=misdirected)
    judged = apply_judgements(findings, judgements)
    shown = {id(f) for f in visible_findings(judged)}
    visible = [i for i, f in enumerate(judged) if id(f) in shown]
    score = score_against_grid(grid, n_goldens, len(findings), visible)
    return {
        **asdict(score),
        "n_candidates": len(findings),
        "n_goldens": n_goldens,
        "unruled": sum(1 for j in judgements if j is None),
        "misdirected": sum(1 for j in judgements if j is not None and j.verdict == "misdirected"),
        "verdicts": [
            None
            if j is None
            else {"verdict": j.verdict, "impact_score": j.impact_score, "rationale": j.rationale}
            for j in judgements
        ],
    }


def _done(out: Path) -> set[tuple[str, ...]]:
    """Keys already recorded, so a rerun resumes rather than pays twice."""
    if not out.exists():
        return set()
    keys: set[tuple[str, ...]] = set()
    for line in out.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            keys.add(
                (
                    r["url"],
                    r["arm"],
                    r["source"],
                    r["variant"],
                    r["skeptic_model"],
                    str(r["skeptic_effort"]),
                    str(r["rep"]),
                )
            )
    return keys


def _scorable(
    record: ReviewRecord, grids: dict[tuple[str, str], JudgeGrid], workspace: Path
) -> tuple[list[Finding], JudgeGrid, str] | str:
    """The review's candidates, its grid and its diff, or why it cannot be scored."""
    findings = candidate_findings(record)
    grid = grids.get((record.url, record.arm))
    if grid is None or grid.project != record.project or grid.n_candidates != len(findings):
        return "no clean grid for its candidates"
    try:
        return findings, grid, pr_diff(record, workspace)
    except subprocess.CalledProcessError as exc:
        return f"no diff ({exc})"


@dataclass
class _Pass:
    """One (model, variant, rep) pass: what it is, where it writes, what it has done."""

    args: argparse.Namespace
    provider: BaseProvider
    model: str
    budget: Budget | None
    done: set[tuple[str, ...]]
    written: int = 0
    skipped: int = 0

    @property
    def variant(self) -> str:
        """The skeptic prompt this pass runs."""
        return "misdirected" if self.args.misdirected else "base"

    def key(self, record: ReviewRecord, source: str) -> tuple[str, ...]:
        """The resume key of one review under this pass."""
        effort = str(self.provider.effort)
        return (
            record.url,
            record.arm,
            source,
            self.variant,
            self.model,
            effort,
            str(self.args.rep),
        )

    async def row(
        self, record: ReviewRecord, source: str, scorable: tuple[list[Finding], JudgeGrid, str]
    ) -> dict[str, Any]:
        """Judge one review and build its row, charging the budget for its calls."""
        findings, grid, diff = scorable
        first = len(self.provider.call_usages)
        scored = await replay_one(
            self.provider,
            findings,
            diff,
            grid.decisions,
            grid.n_goldens,
            misdirected=self.args.misdirected,
        )
        calls = self.provider.call_usages[first:]
        if self.budget is not None:
            self.budget.charge(self.model, calls)
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "url": record.url,
            "project": record.project,
            "arm": record.arm,
            "source": source,
            "finder_model": record.finder_model,
            "skeptic_model": self.model,
            "skeptic_effort": self.provider.effort,
            "variant": self.variant,
            "rep": self.args.rep,
            "skeptic_prompt_tokens": sum(c.prompt_tokens for c in calls),
            "skeptic_completion_tokens": sum(c.completion_tokens for c in calls),
            **scored,
        }


async def _run(
    args: argparse.Namespace, provider: BaseProvider, model: str
) -> tuple[int, int, bool]:
    """Every pair's reviews under one (model, variant, rep): (written, skipped, budget-stopped)."""
    run = _Pass(
        args, provider, model, make_budget(args.budget_usd, args.price, [model]), _done(args.out)
    )
    stopped = False
    with args.out.open("a", encoding="utf-8") as fh:
        for spec in args.pair:
            reviews_path, judged_path = (Path(p) for p in spec.split(":", 1))
            grids = load_grids(judged_path)
            source = str(reviews_path.resolve())
            # As the judge reads them: the last row per (url, arm), and only rows
            # it could score (review of #726) — reading every line paired a stale
            # duplicate with the newest row's grid and counted the PR twice.
            for record in judgeable(load_reviews(reviews_path)):
                key = run.key(record, source)
                if key in run.done:
                    continue
                scorable = _scorable(record, grids, args.workspace)
                if isinstance(scorable, str):
                    run.skipped += 1
                    print(f"  skip {record.url}: {scorable}", file=sys.stderr)
                    continue
                if run.budget is not None and run.budget.exhausted:
                    run.budget.stopped = stopped = True
                    break
                row = await run.row(record, source, scorable)
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                run.done.add(key)
                run.written += 1
                print(
                    f"  {record.url} [{run.variant} r{args.rep}] killed {row['killed']}",
                    file=sys.stderr,
                )
            if stopped:
                break
    if run.budget is not None:
        print(f"Spent ${run.budget.spent_usd:.4f} of ${run.budget.cap_usd:.2f}.", file=sys.stderr)
    return run.written, run.skipped, stopped


def main() -> int:
    """Replay one (model, variant, rep) over every --pair; 3 when the budget stopped it."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--pair", action="append", required=True, metavar="REVIEWS.jsonl:JUDGED.jsonl"
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--misdirected", action="store_true")
    parser.add_argument(
        "--rep", type=int, default=1, help="repetition label, part of the resume key"
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=Path(".martian-workspace"))
    parser.add_argument("--budget-usd", type=float, default=None)
    parser.add_argument("--price", action="append", default=[], metavar="MODEL=IN:OUT")
    args = parser.parse_args()
    if _vendor(args.model) == UNKNOWN_VENDOR:
        parser.error(f"cannot tell the vendor of {args.model!r}")
    try:
        make_budget(args.budget_usd, args.price, [args.model])
        provider, _ = arm_provider(_vendor(args.model), os.environ, args.model)
    except (ValueError, NoArmError) as exc:
        parser.error(str(exc))
    written, skipped, stopped = asyncio.run(_run(args, provider, args.model))
    print(f"{written} rows written, {skipped} reviews unscorable", file=sys.stderr)
    return 3 if stopped else 0


if __name__ == "__main__":
    cgis.log_to_stderr()  # stdout is the script's output (#619)
    raise SystemExit(main())
