"""Phase 2 runner for the Martian Code Review Bench (#342).

    uv run python scripts/guardian_martian.py plan          # cached, free
    uv run python scripts/guardian_martian.py plan --refresh # hits the network

`plan` resolves which slice each of the 50 PRs belongs to and prints the
populations gate G5 is registered on. It costs no LLM calls: the only network
traffic is `gh pr diff --name-only`, and the answers are cached in
`benchmarks/martian/plan.json` so the plan is reproducible and reviewable as a
diff rather than re-derived on every run.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from cgis.guardian.martian import (
    UNKNOWN_SLICE,
    BenchPr,
    PrPlan,
    SliceCounts,
    build_plan,
    load_corpus,
    plan_population,
)

CORPUS_DIR = Path("benchmarks/martian")

#: A sibling of the corpus directory, deliberately not inside it. `load_corpus`
#: globs `*.json`, so a generated file dropped in there is read back as corpus
#: and crashes the loader — which is exactly what the first version of this
#: script did to itself. The directory is also documented as a verbatim copy of
#: upstream, and a file we generate does not belong in something described that
#: way.
PLAN_FILE = Path("benchmarks/martian-plan.json")

#: Registered in docs/specs/2026-08-11-guardian-code-review-bench.md, §"Corpus
#: reconnaissance". The plan asserts against these rather than reporting
#: whatever it finds: a population that has drifted from the one the gate was
#: registered on is the failure mode Phase 1 was retracted for (R1).
REGISTERED = {"graph": 19, "diff-only": 26}


def fetch_changed_files(pr: BenchPr) -> tuple[str, ...]:
    """Paths a PR touches, via `gh pr diff --name-only`.

    `stdin=DEVNULL` is not decoration. `gh` reads stdin, so calling it inside a
    loop that itself reads stdin makes it swallow the remaining input — which
    is exactly how this corpus was first mis-counted at 49 PRs instead of 50.

    Raises with gh's stderr included rather than passing `check=True`.
    `CalledProcessError` stringifies to "Command '[...]' returned non-zero exit
    status 1" and drops the captured stderr entirely — and since `build_plan`
    records the message on the row instead of raising, that message is the only
    diagnostic anyone gets. An expired token and a rate limit would be
    indistinguishable.
    """
    result = subprocess.run(
        ["gh", "pr", "diff", pr.url, "--name-only"],
        capture_output=True,
        # encoding, not text=True: that decodes with the platform's preferred
        # encoding, and a diff can name a file with non-ASCII characters in it.
        encoding="utf-8",
        check=False,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    if result.returncode != 0:
        _msg = f"gh exited {result.returncode}: {result.stderr.strip() or '(no stderr)'}"
        raise RuntimeError(_msg)
    return tuple(line for line in result.stdout.splitlines() if line.strip())


def load_cached(path: Path) -> dict[str, tuple[str, ...]]:
    """Previously fetched file lists, keyed by PR URL; empty when absent.

    An unreadable cache degrades to an empty one *loudly*. This is a pure
    optimisation — nothing about the plan's correctness depends on it, and the
    fallback is to fetch, which is free — so a half-written file from an
    interrupted run should not require deleting it by hand. It warns rather
    than passing silently, because a cache that has quietly stopped working
    (a renamed field, say) otherwise just looks like a slow script.
    """
    if not path.is_file():
        return {}
    try:
        return {
            row["url"]: tuple(row["changed_files"])
            for row in json.loads(path.read_text(encoding="utf-8"))
            if row.get("fetch_error") is None
        }
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError, OSError) as exc:
        print(f"Ignoring unreadable cache {path}: {exc}", file=sys.stderr)
        return {}


def plan(args: argparse.Namespace) -> int:
    """Resolve every PR's slice and print the populations, without spending."""
    prs = load_corpus(args.corpus)
    cache = {} if args.refresh else load_cached(args.out)

    def fetch(pr: BenchPr) -> tuple[str, ...]:
        if pr.url in cache:
            return cache[pr.url]
        print(f"  fetching {pr.url}", file=sys.stderr)
        return fetch_changed_files(pr)

    plans = build_plan(prs, fetch, args.profile)
    args.out.write_text(
        json.dumps([p.model_dump() for p in plans], indent=1) + "\n", encoding="utf-8"
    )
    return _report(plans, args.out)


def _report(plans: list[PrPlan], out: Path) -> int:
    """Print the plan and return non-zero if it does not match the registration."""
    population = plan_population(plans)
    excluded = [p for p in plans if not p.reproducible]
    failed = [p for p in plans if p.fetch_error]

    print(f"\n{len(plans)} PRs planned; written to {out}\n")
    # Measured, not a constant: `cal_dot_com` is 11 characters and silently
    # overflowed a hardcoded 10, shifting every column on that one row.
    width = max((len(p.project) for p in plans), default=0) + 2
    print(f"{'project':{width}}{'PRs':>5}{'graph':>7}{'diff':>6}{'unknown':>9}{'excluded':>10}")
    for project in sorted({p.project for p in plans}):
        rows = [p for p in plans if p.project == project]
        ok = [p for p in rows if p.reproducible]
        print(
            f"  {project:{width - 2}}{len(rows):5}"
            f"{sum(1 for p in ok if p.pr_slice == 'graph'):7}"
            f"{sum(1 for p in ok if p.pr_slice == 'diff-only'):6}"
            f"{sum(1 for p in ok if p.pr_slice == UNKNOWN_SLICE):9}"
            f"{len(rows) - len(ok):10}"
        )

    print("\nevaluated populations (gate G5 is registered on these):")
    for name, counts in sorted(population.items()):
        print(f"  {name:10} {counts.prs:3} PRs  {counts.comments:4} golden comments")

    if excluded:
        print(f"\n{len(excluded)} excluded as unreproducible — reported, never scored as misses:")
        for p in excluded:
            print(f"  {p.project:{width - 2}} #{p.number:<8} {p.url}")

    if failed:
        print(f"\n{len(failed)} FAILED TO FETCH — classified `unknown`, not `diff-only`:")
        for p in failed:
            print(f"  {p.url}: {p.fetch_error}")

    return check_registration(population)


def check_registration(population: dict[str, SliceCounts]) -> int:
    """Compare the measured populations against the pre-registered ones.

    A mismatch is an error, not a note. The gate was registered on specific
    slice sizes; if the corpus or the collector's language support has moved
    since, the honest response is to re-register deliberately in the spec, not
    to run against whatever the numbers happen to be today. That substitution
    is what Phase 1 was retracted for (R1).
    """
    unclassified = population[UNKNOWN_SLICE].prs
    if unclassified:
        print(
            f"\n{unclassified} PRs could not be classified. Populations are incomplete, "
            "so the registration check is meaningless until the fetch succeeds.",
            file=sys.stderr,
        )
        return 1
    drift = {
        name: (registered, population[name].prs)
        for name, registered in REGISTERED.items()
        if population[name].prs != registered
    }
    if drift:
        print("\nPOPULATION DRIFT from the registered gate:", file=sys.stderr)
        for name, (registered, found) in sorted(drift.items()):
            print(f"  {name}: registered {registered}, measured {found}", file=sys.stderr)
        print("Re-register deliberately in the spec before running.", file=sys.stderr)
        return 1
    print("\nPopulations match the registration in the spec.")
    return 0


def main() -> int:
    """Parse arguments and dispatch."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan", help="resolve slices and print populations")
    plan_parser.add_argument("--corpus", type=Path, default=CORPUS_DIR)
    plan_parser.add_argument("--out", type=Path, default=PLAN_FILE)
    plan_parser.add_argument("--profile", default="core", choices=("strict", "core", "all"))
    plan_parser.add_argument("--refresh", action="store_true", help="ignore the cache")
    args = parser.parse_args()
    return plan(args)


if __name__ == "__main__":
    sys.exit(main())
