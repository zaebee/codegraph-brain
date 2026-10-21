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

from run_budget import make_budget
from skeptic_arms import UNKNOWN_VENDOR, NoArmError, _vendor, arm_provider

#: (golden count, row-major golden x candidate grid)
Grid = tuple[int, list[int]]


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


def load_grids(path: Path) -> dict[str, Grid]:
    """url → (goldens, grid) for every fully ruled judged row; the last row of a url wins."""
    grids: dict[str, Grid] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        decisions = row["decisions"]
        if any(d is None for d in decisions):
            grids.pop(row["url"], None)
            continue
        if len(decisions) != row["n_goldens"] * row["n_candidates"]:
            continue
        grids[row["url"]] = (row["n_goldens"], [int(d) for d in decisions])
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
    keys = set()
    for line in out.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            keys.add(
                (
                    r["url"],
                    r["source"],
                    r["variant"],
                    r["skeptic_model"],
                    r["skeptic_effort"],
                    str(r["rep"]),
                )
            )
    return keys


async def _run(
    args: argparse.Namespace, provider: BaseProvider, model: str
) -> tuple[int, int, bool]:
    """Every pair's reviews under one (model, variant, rep): (written, skipped, budget-stopped)."""
    budget = make_budget(args.budget_usd, args.price, [model])
    variant = "misdirected" if args.misdirected else "base"
    done = _done(args.out)
    written = skipped = 0
    with args.out.open("a", encoding="utf-8") as fh:
        for spec in args.pair:
            reviews_path, judged_path = (Path(p) for p in spec.split(":", 1))
            grids = load_grids(judged_path)
            for line in reviews_path.read_text(encoding="utf-8").splitlines():
                record = ReviewRecord.model_validate_json(line)
                key = (
                    record.url,
                    reviews_path.stem,
                    variant,
                    model,
                    provider.effort,
                    str(args.rep),
                )
                if key in done:
                    continue
                findings = candidate_findings(record)
                grid = grids.get(record.url)
                if grid is None or len(grid[1]) != grid[0] * len(findings):
                    skipped += 1
                    print(f"  skip {record.url}: no clean grid for its candidates", file=sys.stderr)
                    continue
                if budget is not None and budget.exhausted:
                    budget.stopped = True
                    print(
                        f"Spent ${budget.spent_usd:.4f} of ${budget.cap_usd:.2f}.", file=sys.stderr
                    )
                    return written, skipped, True
                first = len(provider.call_usages)
                scored = await replay_one(
                    provider,
                    findings,
                    pr_diff(record, args.workspace),
                    grid[1],
                    grid[0],
                    misdirected=args.misdirected,
                )
                calls = provider.call_usages[first:]
                if budget is not None:
                    budget.charge(model, calls)
                row = {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "url": record.url,
                    "project": record.project,
                    "source": reviews_path.stem,
                    "finder_model": record.finder_model,
                    "skeptic_model": model,
                    "skeptic_effort": provider.effort,
                    "variant": variant,
                    "rep": args.rep,
                    "skeptic_prompt_tokens": sum(c.prompt_tokens for c in calls),
                    "skeptic_completion_tokens": sum(c.completion_tokens for c in calls),
                    **scored,
                }
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                written += 1
                print(
                    f"  {record.url} [{variant} r{args.rep}] killed {scored['killed']}",
                    file=sys.stderr,
                )
    if budget is not None:
        print(f"Spent ${budget.spent_usd:.4f} of ${budget.cap_usd:.2f}.", file=sys.stderr)
    return written, skipped, False


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
