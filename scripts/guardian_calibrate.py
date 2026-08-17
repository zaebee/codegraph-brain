"""Phase 1 calibration runner: re-score recorded reviews with Martian's judge (#342).

Costs no finder calls. Every review it scores was already run and recorded in
`benchmarks/guardian/results.jsonl`, including each finding's full text — so
this replays the *scoring*, not the review.

Usage:
    uv run python scripts/guardian_calibrate.py run --dry-run
    uv run python scripts/guardian_calibrate.py run --provider gemini --model gemini-2.5-flash
    uv run python scripts/guardian_calibrate.py report
    uv run python scripts/guardian_calibrate.py rescore

`run` is resumable: a (row, judge) pair already recorded *cleanly* is skipped,
so an interrupted run continues where it stopped and a row whose judge calls
partly failed is re-judged rather than left biased low.
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
from collections import ChainMap, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cgis.guardian.bench import GroundTruth, load_ground_truth
from cgis.guardian.calibrate import (
    DEFAULT_JUDGE_CONCURRENCY,
    JudgePair,
    JudgeVerdict,
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
    `impact_score` is constrained to >= 0. Verified exact against all 118
    recorded rows, zero mismatches:
    len(visible) == len(matched) + noise + len(ambiguous_hits).
    """
    return [f for f in findings if f.get("verdict") != "refuted"]


def strict_precision(row: dict[str, Any]) -> float:
    """Our precision WITHOUT the per-file `ambiguous` exemption (gate G2).

    Martian's judge has no such concept — every candidate is either a match or a
    false positive — so this is the number that belongs next to theirs.

    **Since #345 this is also what `bench.score` reports.** The exemption was
    removed on the strength of the very comparison this column supplied, so for
    any row recorded after that the two precision columns are equal by
    construction and G2's inflation is 0 by definition. The gap remains
    measurable only over the 118 rows recorded before it — which is why the
    report labels G2 as historical rather than dropping it.
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
    unstated population choice — they flip to PASS on the full set.

    That 51 counts reviews whose *recorded* findings list is empty. `report`
    separately names 55 reviews as having "found nothing", counting the ones
    with no finding left after the skeptic's refutations (`n_candidates == 0`).
    Both are right; they are different questions, and only the second one
    decides what the judge was ever asked about.

    Empty reviews cost zero judge calls (no candidates, no pairs), so there is
    no reason to exclude them at collection time; the report separates the two
    populations instead, because agreement on a review that said nothing is
    agreement by shared convention rather than by measurement.
    """
    rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines() if line]
    scored = [r for r in rows if "matched" in r and "precision" in r]
    if pr_filter is not None:
        scored = [r for r in scored if r["pr"] == pr_filter]
    return scored


def load_truths(fixtures: Path) -> dict[int, GroundTruth]:
    """Every pr-N.yaml in the benchmark directory, keyed by PR number.

    Two fixtures declaring the same `pr:` is a corpus error, not a preference
    for the alphabetically later file — silently keeping one would rescore a
    whole PR against ground truth nobody chose.
    """
    truths: dict[int, GroundTruth] = {}
    sources: dict[int, Path] = {}
    for path in sorted(fixtures.glob("pr-*.yaml")):
        truth = load_ground_truth(path)
        if truth.pr in truths:
            _msg = f"Duplicate pr: {truth.pr} in {path} and {sources[truth.pr]}"
            raise ValueError(_msg)
        truths[truth.pr] = truth
        sources[truth.pr] = path
    return truths


def load_records(out: Path) -> list[dict[str, Any]]:
    """Calibration records, keeping only the last write per (row, judge).

    The file is an append-only log and `run` may legitimately re-judge a row
    (see `done_keys`), so the same (row_key, judge_model) can appear more than
    once. Later wins: a re-judged row exists precisely because the earlier one
    was incomplete.
    """
    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line]
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        latest[record["row_key"], record["judge_model"]] = record
    return list(latest.values())


def done_keys(out: Path, judge_model: str) -> set[str]:
    """Row keys this model has judged *cleanly*, so `run` can resume.

    A row with unruled pairs is deliberately not "done". `report` tells the
    operator to "re-run those rows before quoting their precision"; keying
    resume on the row alone made that instruction impossible to follow without
    hand-editing the JSONL, because the re-run skipped exactly the rows it was
    supposed to repair.
    """
    if not out.exists():
        return set()
    return {
        record["row_key"]
        for record in load_records(out)
        if record.get("judge_model") == judge_model and not record.get("judge_failures")
    }


def dense_decisions(pairs: list[JudgePair], n_goldens: int, n_candidates: int) -> list[int | None]:
    """Pair verdicts in (golden, candidate) row-major order; None where a call failed.

    Dense and positional so two judges' decision lists align element-wise for
    the G3 agreement statistic without re-deriving which pairs each one saw.

    Confidences are deliberately not stored. They would be dead weight: since
    `assign_matches` became a maximum matching, the size of the assignment — and
    so tp, precision and recall — depends only on these booleans, which is what
    lets `rescore` re-derive every published number from the committed file.
    """
    grid: list[int | None] = [None for _ in range(n_goldens * n_candidates)]
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
        # Carried from the results row this record judges, never recomputed here
        # (#390). Recomputing would hash *this* checkout, but the record grades a
        # review that ran under whatever guardian produced `row` — often months
        # and hundreds of commits earlier. A fresh digest would therefore
        # attribute someone else's recall to today's reviewer, which is the merge
        # direction #375 exists to prevent.
        #
        # `.get` rather than `[...]`: a results row written before #390 has no
        # fingerprint, and a null here is inert — a consumer simply cannot
        # attribute the row, exactly as it could not before. A *wrong*
        # fingerprint is permanent downstream. `run` counts these and says so.
        #
        # Absence degrades; nothing here validates the *shape* of a value that is
        # present, so a malformed digest is carried verbatim. That asymmetry is
        # deliberate but is not the absence-degrades/malformed-raises pair from
        # #382, and an earlier version of this comment claimed it was. A junk
        # digest lands on the safe side of the asymmetry — it splits an identity
        # into an inert one rather than merging two — so validating it would buy
        # a refusal against nothing unrecoverable.
        "review_fingerprint": row.get("review_fingerprint"),
        "review_fingerprint_source": row.get("review_fingerprint_source"),
        "finder_provider": row.get("finder_provider"),
        "skeptic_provider": row.get("skeptic_provider"),
        "skeptic_model": row.get("skeptic_model"),
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
    if not args.results.is_file():
        print(
            f"No recorded reviews at {args.results}. Run scripts/guardian_bench.py first.",
            file=sys.stderr,
        )
        return 1
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

    # Layered rather than `dict(os.environ)` plus assignment: `build_provider`
    # only reads its mapping, so there is no reason for CLI input to ever be
    # written into a copy of the environment.
    overrides = {
        key: value
        for key, value in (("GUARDIAN_PROVIDER", args.provider), ("GUARDIAN_MODEL", args.model))
        if value
    }
    provider, judge_model = build_provider(ChainMap(overrides, dict(os.environ)))

    already = done_keys(args.out, judge_model)
    pending = [r for r in pending if row_key(r) not in already]
    print(f"Judge {judge_model}: {len(already)} already done, {len(pending)} to go.")

    # Said once, up front, rather than per row: a run of hundreds would bury the
    # notice in its own progress output. Not fatal — see `calibrate_row` — but
    # not silent either, because a fingerprint-less record is a recall figure
    # that cannot be attributed to anyone, which is the defect #390 reports.
    unattributed = [r for r in pending if not r.get("review_fingerprint")]
    if unattributed:
        print(
            f"{len(unattributed)}/{len(pending)} results rows carry no review_fingerprint; "
            f"their calibration records will not be attributable to a reviewer. "
            f"Run scripts/backfill_calibration_fingerprint.py over {args.results} first.",
            file=sys.stderr,
        )

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


def pairs_from_decisions(record: dict[str, Any]) -> list[JudgePair]:
    """Rebuild judge pairs from a stored decision grid.

    Confidence is not stored and is not needed: `assign_matches` is a maximum
    matching, whose size — and therefore tp, precision and recall — depends only
    on which pairs the judge called a match. A constant stands in so the
    replayed pairs typecheck; it cannot influence the score.
    """
    n_candidates = record["n_candidates"]
    return [
        JudgePair(
            golden_index=index // n_candidates,
            candidate_index=index % n_candidates,
            verdict=JudgeVerdict(reasoning="replayed", match=bool(decision), confidence=1.0),
        )
        for index, decision in enumerate(record["decisions"])
        if decision is not None
    ]


def rescored(record: dict[str, Any]) -> dict[str, Any]:
    """One record with tp/fp/fn and both judge rates re-derived from its grid."""
    assignment = assign_matches(pairs_from_decisions(record))
    scored = judge_score(
        n_goldens=record["n_goldens"],
        n_candidates=record["n_candidates"],
        assignment=assignment,
    )
    return record | {
        "tp": scored.tp,
        "fp": scored.fp,
        "fn": scored.fn,
        "judge_precision": scored.precision,
        "judge_recall": scored.recall,
        "assignment": {str(k): v for k, v in sorted(assignment.items())},
    }


def rescore(args: argparse.Namespace) -> int:
    """Re-derive every stored score from its decision grid; write back with --apply.

    Exists because the scorer was wrong once. `assign_matches` was greedy and
    under-counted true positives, and the raw judge rulings — which cost real
    money — were still on disk and still correct. Replaying the scoring is the
    honest repair; re-running the judge would have burned the budget to
    reproduce the same decisions.

    It doubles as the reproducibility check the spec needs: on an already-correct
    file it reports zero changes, which is the assertion that every published
    number follows from the committed evidence.
    """
    if not args.out.exists():
        print(f"No calibration records at {args.out}. Run `run` first.", file=sys.stderr)
        return 1
    records = load_records(args.out)
    updated = [rescored(r) for r in records]
    changes = [
        (old, new) for old, new in zip(records, updated, strict=True) if old["tp"] != new["tp"]
    ]

    print(f"{len(records)} records, {len(changes)} with a different tp.")
    for old, new in changes:
        print(
            f"  {old['row_key']} [{old['judge_model']}] "
            f"tp {old['tp']} -> {new['tp']}  "
            f"P {old['judge_precision']:.3f} -> {new['judge_precision']:.3f}  "
            f"R {old['judge_recall']:.3f} -> {new['judge_recall']:.3f}"
        )
    if not args.apply:
        print("\nDry run. Re-run with --apply to write these back.")
        return 0
    args.out.write_text("".join(json.dumps(record) + "\n" for record in updated), encoding="utf-8")
    print(f"\nWrote {len(updated)} records to {args.out}.")
    return 0


def numeric_column(rows: list[dict[str, Any]], key: str) -> list[float]:
    """One numeric column out of a list of calibration records."""
    return [float(r[key]) for r in rows]


def rho_line(label: str, xs: list[float], ys: list[float]) -> str:
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
    print(
        rho_line(
            "per-run",
            numeric_column(rows, "our_precision"),
            numeric_column(rows, "judge_precision"),
        )
    )
    print(
        rho_line(
            "per-run (strict, no ambig)",
            numeric_column(rows, "our_precision_strict"),
            numeric_column(rows, "judge_precision"),
        )
    )
    print(
        rho_line(
            "per-PR (run-averaged)",
            [statistics.fmean(numeric_column(by_pr[p], "our_precision")) for p in prs],
            [statistics.fmean(numeric_column(by_pr[p], "judge_precision")) for p in prs],
        )
    )
    print(
        rho_line(
            "per-run recall",
            numeric_column(rows, "our_recall"),
            numeric_column(rows, "judge_recall"),
        )
    )

    deltas = [(r["our_precision"] - r["our_precision_strict"]) * 100 for r in rows]
    mean_delta, max_delta = statistics.fmean(deltas), max(deltas)
    g2 = "PASS" if mean_delta <= G2_MAX_POINTS else "FAIL"
    print(
        f"\nG2  ambiguous exemption inflates precision (<= {G2_MAX_POINTS:.0f} pts)"
        "  [HISTORICAL: exemption removed in #345]"
    )
    print(f"  mean +{mean_delta:.1f} pts, max +{max_delta:.1f} pts  [{g2}]")
    print("  Rows recorded after #345 score 0 by construction — the two columns are equal.")

    print("\n    per-PR means (ours / ours-strict / judge)")
    for p in prs:
        group = by_pr[p]
        print(
            f"      pr-{p}: n={len(group):<3} "
            f"P {statistics.fmean(numeric_column(group, 'our_precision')):.2f} / "
            f"{statistics.fmean(numeric_column(group, 'our_precision_strict')):.2f} / "
            f"{statistics.fmean(numeric_column(group, 'judge_precision')):.2f}   "
            f"R {statistics.fmean(numeric_column(group, 'our_recall')):.2f} / "
            f"{statistics.fmean(numeric_column(group, 'judge_recall')):.2f}"
        )


def _grid_shape(record: dict[str, Any]) -> tuple[int, int]:
    """The (goldens, candidates) a record's decision grid describes."""
    return record["n_goldens"], record["n_candidates"]


def _aligned_decisions(
    left: dict[str, dict[str, Any]], right: dict[str, dict[str, Any]]
) -> tuple[list[bool], list[bool], list[str]]:
    """Two judges' rulings on the pairs both of them actually ruled on.

    Returns the two aligned decision vectors and the row keys that had to be
    skipped. Alignment is positional and only holds while both records describe
    the same grid: a fixture edited between the two judge runs changes
    `n_goldens`, after which element *i* is a different (golden, candidate) pair
    on each side. Comparing those would invent agreement, and a bare
    `zip(strict=True)` would abort the whole report over one stale row.
    """
    a: list[bool] = []
    b: list[bool] = []
    skipped: list[str] = []
    for key in sorted(left.keys() & right.keys()):
        if _grid_shape(left[key]) != _grid_shape(right[key]):
            skipped.append(key)
            continue
        for x, y in zip(left[key]["decisions"], right[key]["decisions"], strict=True):
            if x is not None and y is not None:
                a.append(bool(x))
                b.append(bool(y))
    return a, b, skipped


def _print_pair_agreement(first: str, second: str, a: list[bool], b: list[bool]) -> None:
    """Print G3 for one pair of judges: as registered, then chance-corrected."""
    agreement = decision_agreement(a, b)
    if agreement is None:
        print(f"  {first} vs {second}: no comparable decisions")
        return
    verdict = "PASS" if agreement >= G3_MIN_AGREEMENT else "FAIL"
    kappa = cohen_kappa(a, b)
    positive = positive_agreement(a, b)
    rate_a, rate_b = sum(a) / len(a), sum(b) / len(b)
    chance = rate_a * rate_b + (1 - rate_a) * (1 - rate_b)
    print(f"\n  {first} vs {second} — {len(a)} comparable decisions")
    print(f"    raw agreement    {agreement:.1%}  [{verdict} as registered]")
    print(f"    chance agreement {chance:.1%}  <- what the registered gate mostly measures")
    print(f"    match rates      {rate_a:.1%} vs {rate_b:.1%}")
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
    for index, first in enumerate(judges):
        for second in judges[index + 1 :]:
            a, b, skipped = _aligned_decisions(
                {r["row_key"]: r for r in by_judge[first]},
                {r["row_key"]: r for r in by_judge[second]},
            )
            for key in skipped:
                print(f"    skipping {key}: grids differ in shape between the two judges")
            _print_pair_agreement(first, second, a, b)


def report(args: argparse.Namespace) -> int:
    """Print the three pre-registered gates from the calibration records."""
    if not args.out.exists():
        print(f"No calibration records at {args.out}. Run `run` first.", file=sys.stderr)
        return 1
    records = load_records(args.out)
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

    rescore_parser = sub.add_parser("rescore", help="re-derive scores from stored decisions")
    rescore_parser.add_argument("--out", type=Path, default=CALIBRATION)
    rescore_parser.add_argument("--apply", action="store_true", help="write the records back")

    args = parser.parse_args()
    if args.command == "run":
        return asyncio.run(run(args))
    if args.command == "rescore":
        return rescore(args)
    return report(args)


if __name__ == "__main__":
    sys.exit(main())
