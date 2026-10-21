"""Label which findings of the frozen skeptic corpus really match their ground truth (#246 E).

Every same-file (finding, entry) pair of the 37 frozen passes goes to each judge
once, through the Martian judge (`calibrate.judge_pair`: summary vs the comment
a human read, no file or line). The labels are written to one file that every
skeptic arm then scores against (`skeptic_arms.py --labels`), so the judging is
paid once per corpus, not once per arm.

Resumable: an existing output is loaded, and only pairs a judge has no verdict
for — never asked, or a failed call — are asked again.

    ANTHROPIC_API_KEY=... uv run python scripts/label_gt_matches.py \\
        --judges claude-haiku-5-5,claude-sonnet-5-5 \\
        --out benchmarks/guardian/semantic-labels.json \\
        --budget-usd 1 --price "claude-haiku-5-5=0.10:0.50;100000=0.50:2.50" \\
        --price "claude-sonnet-5-5=2:10"
"""

import argparse
import asyncio
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from cgis.guardian.bench import GroundTruth, load_ground_truth
from cgis.guardian.calibrate import (
    DEFAULT_JUDGE_CONCURRENCY,
    candidate_text,
    golden_text,
    judge_pair,
)
from cgis.guardian.findings import Finding
from cgis.guardian.providers.base import BaseProvider
from cgis.guardian.recording import load_finder_recording

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_budget import Budget, make_budget
from semantic_match import Labels, candidate_pairs, dump_labels, load_labels
from skeptic_arms import BENCH_DIR, REPO_ROOT, _passes_by_pr, arm_provider

#: (pass stem, the pass's findings, its PR's ground truth)
Pass = tuple[str, Sequence[Finding], GroundTruth]


async def label(
    passes: Sequence[Pass],
    judges: Mapping[str, BaseProvider],
    labels: Labels,
    budget: Budget | None,
    concurrency: int = DEFAULT_JUDGE_CONCURRENCY,
) -> bool:
    """Fill `labels` in place with each judge's verdict on each pair.

    False once the budget stopped it, checked before each (pass, judge) unit.
    """
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _one(provider: BaseProvider, golden: str, candidate: str) -> bool | None:
        async with semaphore:
            verdict = await judge_pair(provider, golden, candidate)
        return None if verdict is None else verdict.match

    for stem, findings, truth in passes:
        entries = {entry.id: entry for entry in truth.findings}
        for name, provider in judges.items():
            if budget is not None and budget.exhausted:
                budget.stopped = True
                return False
            todo = [
                (index, gt_id)
                for index, gt_id in candidate_pairs(findings, truth)
                if labels.verdicts.get((stem, index, gt_id), {}).get(name) is None
            ]
            first_call = len(provider.call_usages)
            results = await asyncio.gather(
                *(
                    _one(provider, golden_text(entries[gt_id]), candidate_text(findings[index]))
                    for index, gt_id in todo
                )
            )
            for (index, gt_id), match in zip(todo, results, strict=True):
                labels.verdicts.setdefault((stem, index, gt_id), {})[name] = match
            if budget is not None:
                budget.charge(name, provider.call_usages[first_call:])
    return True


def write(labels: Labels, out: Path) -> None:
    """Write the labels; a separate step so a stopped run still keeps what it paid for."""
    dump_labels(labels, out)


def corpus_passes(tmp: Path) -> list[Pass]:
    """The frozen passes with findings, in the order `skeptic_arms` judges them."""
    passes: list[Pass] = []
    by_pr = _passes_by_pr(REPO_ROOT, tmp, None)
    for pr in sorted(by_pr):
        truth = load_ground_truth(BENCH_DIR / f"pr-{pr}.yaml")
        passes.extend(
            (path.stem, load_finder_recording(path).result.findings, truth) for path in by_pr[pr]
        )
    return passes


def main() -> int:
    """Label the corpus with each judge and write the result; 3 when the budget stopped it."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--judges", required=True, help="comma-separated anthropic model ids")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--budget-usd", type=float, default=None)
    parser.add_argument("--price", action="append", default=[], metavar="MODEL=IN:OUT")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_JUDGE_CONCURRENCY)
    args = parser.parse_args()
    names = [n.strip() for n in args.judges.split(",") if n.strip()]
    try:
        budget = make_budget(args.budget_usd, args.price, names)
    except ValueError as exc:
        parser.error(str(exc))
    judges = {name: arm_provider("anthropic", os.environ, name)[0] for name in names}
    labels = (
        load_labels(args.out) if args.out.exists() else Labels(judges=tuple(names), verdicts={})
    )
    if labels.judges != tuple(names):
        parser.error(f"{args.out} was labelled by {list(labels.judges)}, not {names}")
    with tempfile.TemporaryDirectory(prefix="labels-") as tmp:
        passes = corpus_passes(Path(tmp))
        try:
            finished = asyncio.run(label(passes, judges, labels, budget, args.concurrency))
        finally:
            write(labels, args.out)
    print(
        f"{len(labels.verdicts)} pairs, {labels.unlabelled()} with a failed call", file=sys.stderr
    )
    if budget is not None:
        print(f"Spent ${budget.spent_usd:.4f} of ${budget.cap_usd:.2f}.", file=sys.stderr)
    return 0 if finished else 3


if __name__ == "__main__":
    sys.exit(main())
