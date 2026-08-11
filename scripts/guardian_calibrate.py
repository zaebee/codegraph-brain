"""Phase 1 calibration runner: re-score recorded reviews with Martian's judge (#342).

Costs no finder calls. Every review it scores was already run and recorded in
`benchmarks/guardian/results.jsonl`, including each finding's full text — so
this replays the *scoring*, not the review.

Usage:
    uv run python scripts/guardian_calibrate.py run --dry-run
    uv run python scripts/guardian_calibrate.py run --provider gemini --model gemini-2.5-flash
    uv run python scripts/guardian_calibrate.py report

`run` is resumable: a (row, judge) pair already present in the output file is
skipped, so an interrupted run continues where it stopped.
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cgis.guardian.bench import GroundTruth, load_ground_truth
from cgis.guardian.calibrate import (
    DEFAULT_JUDGE_CONCURRENCY,
    JudgePair,
    assign_matches,
    candidate_text,
    cohen_kappa,
    decision_agreement,
    golden_text,
    judge_matrix,
    judge_score,
    positive_agreement,
    spearman,
)
from cgis.guardian.findings import Finding
from cgis.guardian.providers.base import BaseProvider
from cgis.guardian.runner import build_provider

BENCH_DIR = Path("benchmarks/guardian")
RESULTS = BENCH_DIR / "results.jsonl"
CALIBRATION = BENCH_DIR / "calibration.jsonl"

#: Gate thresholds, pre-registered in
#: docs/specs/2026-08-11-guardian-code-review-bench.md §"Phase 1".
G1_MIN_RHO = 0.6
G2_MAX_POINTS = 10.0
G3_MIN_AGREEMENT = 0.8


def visible(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The findings the scorer actually saw.

    `annotate_matches` records every finding including the ones the skeptic hid,
    while `match_findings` ran on `visible_findings(...)` at threshold 0 — where
    the only hiding rule that can fire is `verdict == "refuted"`, because
    `impact_score` is constrained to >= 0. Verified exact against all 67
    recorded rows: len(visible) == len(matched) + noise + len(ambiguous_hits).
    """
    return [f for f in findings if f.get("verdict") != "refuted"]


def strict_precision(row: dict[str, Any]) -> float:
    """Our precision WITHOUT the per-file `ambiguous` exemption (gate G2).

    `score()` drops ambiguous hits from the denominator entirely. Martian's
    judge has no such concept — every candidate is either a match or a false
    positive — so this is the number that belongs next to theirs.
    """
    tp = len(row["matched"])
    denominator = tp + row["noise"] + len(row["ambiguous_hits"])
    return tp / denominator if denominator else 1.0


def row_key(row: dict[str, Any]) -> str:
    """Stable identity for one recorded review: its timestamp is already unique."""
    return f"{row['pr']}@{row['timestamp']}"


def load_rows(results: Path, pr_filter: int | None) -> list[dict[str, Any]]:
    """Every scored review, optionally filtered to one PR.

    Deliberately includes reviews that found nothing. An earlier version
    required `r["findings"]` to be non-empty, which silently dropped 51 of 118
    scored reviews and made both Phase 1 gate verdicts an artefact of an
    unstated population choice — they flip to PASS on the full set. Empty
    reviews cost zero judge calls (no candidates, no pairs), so there is no
    reason to exclude them at collection time; the report separates the two
    populations instead, because agreement on a review that said nothing is
    agreement by shared convention rather than by measurement.
    """
    rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines() if line]
    scored = [r for r in rows if "matched" in r and "precision" in r]
    if pr_filter is not None:
        scored = [r for r in scored if r["pr"] == pr_filter]
    return scored


def load_truths(fixtures: Path) -> dict[int, GroundTruth]:
    """Every pr-N.yaml in the benchmark directory, keyed by PR number."""
    truths = {}
    for path in sorted(fixtures.glob("pr-*.yaml")):
        truth = load_ground_truth(path)
        truths[truth.pr] = truth
    return truths


def done_keys(out: Path, judge_model: str) -> set[str]:
    """Row keys already judged by this model, so `run` can resume."""
    if not out.exists():
        return set()
    seen = set()
    for line in out.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        record = json.loads(line)
        if record.get("judge_model") == judge_model:
            seen.add(record["row_key"])
    return seen


def dense_decisions(pairs: list[JudgePair], n_goldens: int, n_candidates: int) -> list[int | None]:
    """Pair verdicts in (golden, candidate) row-major order; None where a call failed.

    Dense and positional so two judges' decision lists align element-wise for
    the G3 agreement statistic without re-deriving which pairs each one saw.
    """
    grid: list[int | None] = [None] * (n_goldens * n_candidates)
    for pair in pairs:
        grid[pair.golden_index * n_candidates + pair.candidate_index] = int(pair.verdict.match)
    return grid


async def calibrate_row(
    provider: BaseProvider, row: dict[str, Any], truth: GroundTruth, concurrency: int
) -> dict[str, Any]:
    """Judge one recorded review and return its calibration record."""
    goldens = [golden_text(e) for e in truth.findings]
    findings = [Finding.model_validate(f) for f in visible(row["findings"])]
    candidates = [candidate_text(f) for f in findings]

    pairs, failures = await judge_matrix(provider, goldens, candidates, concurrency)
    assignment = assign_matches(pairs)
    scored = judge_score(
        n_goldens=len(goldens), n_candidates=len(candidates), assignment=assignment
    )

    return {
        "row_key": row_key(row),
        "pr": row["pr"],
        "run": row.get("run"),
        "finder_model": row.get("model"),
        "guardian_sha": row.get("guardian_sha"),
        "features": row.get("features", ""),
        "bench_arm": row.get("bench_arm"),
        "n_goldens": len(goldens),
        "n_candidates": len(candidates),
        "our_precision": row["precision"],
        "our_precision_strict": strict_precision(row),
        "our_recall": row["recall"],
        "judge_precision": scored.precision,
        "judge_recall": scored.recall,
        "tp": scored.tp,
        "fp": scored.fp,
        "fn": scored.fn,
        "judge_failures": failures,
        "decisions": dense_decisions(pairs, len(goldens), len(candidates)),
        "assignment": {str(k): v for k, v in sorted(assignment.items())},
    }


async def run(args: argparse.Namespace) -> int:
    """Judge every unjudged recorded review; append one record per review."""
    rows = load_rows(args.results, args.pr)
    truths = load_truths(args.fixtures)
    missing = sorted({r["pr"] for r in rows} - truths.keys())
    if missing:
        print(f"No fixture for PRs {missing}; those rows are skipped.", file=sys.stderr)
        rows = [r for r in rows if r["pr"] in truths]

    pending = rows
    projected = sum(len(truths[r["pr"]].findings) * len(visible(r["findings"])) for r in pending)
    print(f"{len(pending)} recorded reviews, {projected} judge calls projected.")
    if args.dry_run:
        by_pr: dict[int, int] = defaultdict(int)
        for r in pending:
            by_pr[r["pr"]] += len(truths[r["pr"]].findings) * len(visible(r["findings"]))
        for pr in sorted(by_pr):
            print(f"  pr-{pr}: {by_pr[pr]} calls")
        return 0

    env = dict(os.environ)
    if args.provider:
        env["GUARDIAN_PROVIDER"] = args.provider
    if args.model:
        env["GUARDIAN_MODEL"] = args.model
    provider, judge_model = build_provider(env)

    already = done_keys(args.out, judge_model)
    pending = [r for r in pending if row_key(r) not in already]
    print(f"Judge {judge_model}: {len(already)} already done, {len(pending)} to go.")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as fh:
        for index, row in enumerate(pending, 1):
            record = await calibrate_row(provider, row, truths[row["pr"]], args.concurrency)
            record["judge_model"] = judge_model
            record["judged_at"] = datetime.now(UTC).isoformat()
            fh.write(json.dumps(record) + "\n")
            fh.flush()
            print(
                f"[{index}/{len(pending)}] pr-{record['pr']} "
                f"ours P={record['our_precision']:.2f} R={record['our_recall']:.2f} | "
                f"judge P={record['judge_precision']:.2f} R={record['judge_recall']:.2f}"
                + (f"  ({record['judge_failures']} failed)" if record["judge_failures"] else ""),
                # A full run takes tens of minutes; block buffering would hide
                # every line of it until the process exits.
                flush=True,
            )
    return 0


def _col(rows: list[dict[str, Any]], key: str) -> list[float]:
    """One numeric column out of a list of calibration records."""
    return [float(r[key]) for r in rows]


def _rho_line(label: str, xs: list[float], ys: list[float]) -> str:
    """One correlation line: rho, n, and whether it clears the G1 threshold."""
    rho = spearman(xs, ys)
    if rho is None:
        return f"  {label:<28} rho=undefined (n={len(xs)})"
    verdict = "PASS" if rho >= G1_MIN_RHO else "FAIL"
    return f"  {label:<28} rho={rho:+.3f}  n={len(xs)}  [{verdict}]"


def _report_one_judge(judge: str, all_rows: list[dict[str, Any]]) -> None:
    """Print G1 and G2 for one judge model, over both populations."""
    failures = sum(r["judge_failures"] for r in all_rows)
    dirty = sum(1 for r in all_rows if r["judge_failures"])
    nonempty = [r for r in all_rows if r["n_candidates"] > 0]
    print(f"\n=== judge: {judge} — {len(all_rows)} reviews, {failures} failed pair calls ===")
    if failures:
        print(
            f"  WARNING: {dirty} rows carry unruled pairs; their tp is biased low. "
            "Re-run those rows before quoting their precision."
        )
    print(
        f"  populations: all scored reviews n={len(all_rows)}, "
        f"non-empty reviews n={len(nonempty)} "
        f"({len(all_rows) - len(nonempty)} reviews found nothing)"
    )
    print(
        "  Both are reported because neither was pre-registered and the gate\n"
        "  verdicts differ between them. Empty reviews agree by shared vacuous\n"
        "  convention (precision 1.0 on both sides), not by measurement."
    )
    for label, subset in (("ALL", all_rows), ("NON-EMPTY", nonempty)):
        if subset:
            _report_population(label, subset)


def _report_population(label: str, rows: list[dict[str, Any]]) -> None:
    """Print G1 and G2 for one population of reviews, plus the per-PR breakdown."""
    print(f"\n--- population: {label} (n={len(rows)}) ---")
    by_pr: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_pr[r["pr"]].append(r)
    prs = sorted(by_pr)

    print(f"\nG1  our precision vs judge precision (>= {G1_MIN_RHO:.2f})")
    print(_rho_line("per-run", _col(rows, "our_precision"), _col(rows, "judge_precision")))
    print(
        _rho_line(
            "per-run (strict, no ambig)",
            _col(rows, "our_precision_strict"),
            _col(rows, "judge_precision"),
        )
    )
    print(
        _rho_line(
            "per-PR (run-averaged)",
            [statistics.fmean(_col(by_pr[p], "our_precision")) for p in prs],
            [statistics.fmean(_col(by_pr[p], "judge_precision")) for p in prs],
        )
    )
    print(_rho_line("per-run recall", _col(rows, "our_recall"), _col(rows, "judge_recall")))

    deltas = [(r["our_precision"] - r["our_precision_strict"]) * 100 for r in rows]
    mean_delta, max_delta = statistics.fmean(deltas), max(deltas)
    g2 = "PASS" if mean_delta <= G2_MAX_POINTS else "FAIL"
    print(f"\nG2  ambiguous exemption inflates precision (<= {G2_MAX_POINTS:.0f} pts)")
    print(f"  mean +{mean_delta:.1f} pts, max +{max_delta:.1f} pts  [{g2}]")

    print("\n    per-PR means (ours / ours-strict / judge)")
    for p in prs:
        group = by_pr[p]
        print(
            f"      pr-{p}: n={len(group):<3} "
            f"P {statistics.fmean(_col(group, 'our_precision')):.2f} / "
            f"{statistics.fmean(_col(group, 'our_precision_strict')):.2f} / "
            f"{statistics.fmean(_col(group, 'judge_precision')):.2f}   "
            f"R {statistics.fmean(_col(group, 'our_recall')):.2f} / "
            f"{statistics.fmean(_col(group, 'judge_recall')):.2f}"
        )


def _report_agreement(by_judge: dict[str, list[dict[str, Any]]]) -> None:
    """Print G3: per-decision agreement between every pair of judges.

    Raw agreement is printed because that is how G3 was registered, and kappa
    beside it because raw agreement cannot fail at this base rate: a judge
    matches ~5% of pairs, so two judges agree by chance ~91% of the time,
    already above the 80% threshold. The registered verdict is shown as
    registered and then explicitly discounted, rather than swapped for a
    friendlier statistic after the fact.
    """
    judges = sorted(by_judge)
    print(f"\nG3  judge agreement (registered form: raw >= {G3_MIN_AGREEMENT:.0%})")
    if len(judges) < 2:
        print("  only one judge present — rerun `run` with a second --model")
        return
    for i, first in enumerate(judges):
        for second in judges[i + 1 :]:
            left = {r["row_key"]: r for r in by_judge[first]}
            right = {r["row_key"]: r for r in by_judge[second]}
            a: list[bool] = []
            b: list[bool] = []
            for key in sorted(left.keys() & right.keys()):
                for x, y in zip(left[key]["decisions"], right[key]["decisions"], strict=True):
                    if x is not None and y is not None:
                        a.append(bool(x))
                        b.append(bool(y))
            agreement = decision_agreement(a, b)
            if agreement is None:
                print(f"  {first} vs {second}: no comparable decisions")
                continue
            verdict = "PASS" if agreement >= G3_MIN_AGREEMENT else "FAIL"
            kappa = cohen_kappa(a, b)
            positive = positive_agreement(a, b)
            chance = (sum(a) / len(a)) * (sum(b) / len(b)) + (1 - sum(a) / len(a)) * (
                1 - sum(b) / len(b)
            )
            print(f"\n  {first} vs {second} — {len(a)} comparable decisions")
            print(f"    raw agreement    {agreement:.1%}  [{verdict} as registered]")
            print(f"    chance agreement {chance:.1%}  <- what the registered gate mostly measures")
            print(f"    match rates      {sum(a) / len(a):.1%} vs {sum(b) / len(b):.1%}")
            print(
                "    Cohen's kappa    "
                + ("undefined" if kappa is None else f"{kappa:+.3f}")
                + "  <- the statistic G3 should have used"
            )
            print(
                "    positive overlap "
                + ("undefined" if positive is None else f"{positive:.1%}")
                + f"  ({sum(x and y for x, y in zip(a, b, strict=True))} pairs matched by both)"
            )


def report(args: argparse.Namespace) -> int:
    """Print the three pre-registered gates from the calibration records."""
    if not args.out.exists():
        print(f"No calibration records at {args.out}. Run `run` first.", file=sys.stderr)
        return 1
    records = [
        json.loads(line) for line in args.out.read_text(encoding="utf-8").splitlines() if line
    ]
    by_judge: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_judge[r["judge_model"]].append(r)

    for judge, rows in sorted(by_judge.items()):
        _report_one_judge(judge, rows)
    _report_agreement(by_judge)
    return 0


def main() -> int:
    """Parse arguments and dispatch to `run` or `report`."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="judge recorded reviews")
    run_parser.add_argument("--results", type=Path, default=RESULTS)
    run_parser.add_argument("--fixtures", type=Path, default=BENCH_DIR)
    run_parser.add_argument("--out", type=Path, default=CALIBRATION)
    run_parser.add_argument("--pr", type=int, default=None)
    run_parser.add_argument("--provider", default=None, help="gemini | mistral | ollama")
    run_parser.add_argument("--model", default=None, help="judge model name")
    run_parser.add_argument("--concurrency", type=int, default=DEFAULT_JUDGE_CONCURRENCY)
    run_parser.add_argument("--dry-run", action="store_true", help="print projected calls only")

    report_parser = sub.add_parser("report", help="print the pre-registered gates")
    report_parser.add_argument("--out", type=Path, default=CALIBRATION)

    args = parser.parse_args()
    if args.command == "run":
        return asyncio.run(run(args))
    return report(args)


if __name__ == "__main__":
    sys.exit(main())
