"""Phase 2 runner for the Martian Code Review Bench (#342).

    uv run python scripts/guardian_martian.py plan            # cached, free
    uv run python scripts/guardian_martian.py plan --refresh   # hits the network
    uv run python scripts/guardian_martian.py prepare --pr N   # clone + ingest, free

`plan` resolves which slice each of the 50 PRs belongs to and prints the
populations gate G5 is registered on. It costs no LLM calls: the only network
traffic is `gh pr diff --name-only`, and the answers are cached in
`benchmarks/martian/plan.json` so the plan is reproducible and reviewable as a
diff rather than re-derived on every run.
"""

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from cgis.extractors.registry import is_supported, language_for
from cgis.guardian.martian import (
    UNKNOWN_SLICE,
    BenchPr,
    PrPlan,
    SliceCounts,
    build_plan,
    load_corpus,
    plan_population,
)
from cgis.storage.sqlite_store import SQLiteStore

CORPUS_DIR = Path("benchmarks/martian")

#: A sibling of the corpus directory, deliberately not inside it. `load_corpus`
#: globs `*.json`, so a generated file dropped in there is read back as corpus
#: and crashes the loader — which is exactly what the first version of this
#: script did to itself. The directory is also documented as a verbatim copy of
#: upstream, and a file we generate does not belong in something described that
#: way.
PLAN_FILE = Path("benchmarks/martian-plan.json")

#: Checkouts and graph databases. Outside the repo: sentry alone is 431 MB
#: checked out and a 133 MB database, and five projects of that order have no
#: business inside a source tree.
DEFAULT_WORKSPACE = Path(".martian-workspace")

#: The collector strips this prefix from changed-file paths before looking them
#: up. Empty, because `prepare` ingests the whole checkout, so node ids keep
#: their full relative path: `src/sentry_plugins/utils.py` is stored as
#: `src.sentry_plugins.utils`. With the collector's default of "src" every
#: lookup on this corpus misses — see `graph_alignment`.
SOURCE_ROOT = ""

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


def graph_alignment(db_path: Path, changed_files: Sequence[str]) -> tuple[int, int]:
    """How many of a PR's changed files the graph actually knows: (found, checked).

    This exists because the failure it catches is invisible. The collector looks
    a changed file up by the FQN it derives from the path; if that string is not
    byte-identical to the id the extractor stored, `_graph_sections` finds
    nothing, logs at `debug`, and returns a review with no graph context that
    looks completely normal.

    Measured on this corpus: ingesting the whole checkout stores
    `src/sentry_plugins/utils.py` as `src.sentry_plugins.utils`, while the
    collector's default `source_root="src"` derives `sentry_plugins.utils`.
    Every lookup misses. The graph-enabled slice would have carried no graph,
    G5 would have failed, and the conclusion would have been "graph context
    does not help" — from a run where it was never supplied.

    #344 removed this failure mode for languages. It came back for roots.
    """
    supported = [f for f in changed_files if is_supported(f)]
    if not supported:
        return 0, 0
    if not db_path.is_file():
        # SQLiteStore creates a 48 KB empty database when the file is absent,
        # so opening one here would litter the workspace as a side effect of a
        # read-only check — and then report zero anyway.
        return 0, len(supported)
    with SQLiteStore(str(db_path)) as store:
        found = 0
        for path in supported:
            language = language_for(path)
            if language is None:  # pragma: no cover - filtered above
                continue
            if store.get_node(language.file_path_to_module_fqn(path, SOURCE_ROOT)):
                found += 1
    return found, len(supported)


def require_alignment(db_path: Path, changed_files: Sequence[str]) -> str:
    """Describe the graph's coverage of a PR, refusing a graph that knows none of it."""
    found, checked = graph_alignment(db_path, changed_files)
    if not found:
        _msg = (
            f"graph has none of the {checked} supported changed files; a review here "
            "would silently have no graph context"
        )
        raise RuntimeError(_msg)
    return f"ingested, graph knows {found}/{checked} changed files"


def load_plan(path: Path) -> list[PrPlan]:
    """The plan produced by `plan`, or a refusal that says what to run."""
    if not path.is_file():
        _msg = f"No plan at {path}. Run `guardian_martian.py plan` first."
        raise FileNotFoundError(_msg)
    return [PrPlan.model_validate(row) for row in json.loads(path.read_text(encoding="utf-8"))]


def selected(plans: Sequence[PrPlan], only: int | None, limit: int | None) -> list[PrPlan]:
    """The PRs to work on: evaluated ones, optionally narrowed.

    Unreproducible rows are dropped here rather than at report time so no
    expensive step is ever spent on a PR whose result could not be used.
    """
    rows = [p for p in plans if p.reproducible]
    if only is not None:
        rows = [p for p in rows if p.number == only]
    # `rows[:None]` is the whole list, so no branch is needed — and the branch
    # that was here read `--limit 0` as "no limit" instead of "nothing".
    return rows[:limit]


def prepare(args: argparse.Namespace) -> int:
    """Clone, check out and (for the graph slice) ingest. No LLM calls.

    Split from the review deliberately. This is the slow, failure-prone half —
    network, disk, five large repositories — and it costs nothing, so it should
    be possible to get it wrong repeatedly without paying for the privilege.
    """
    rows = selected(load_plan(args.plan), args.pr, args.limit)
    if not rows:
        print("Nothing selected.", file=sys.stderr)
        return 1
    args.workspace.mkdir(parents=True, exist_ok=True)

    failures: list[tuple[PrPlan, str]] = []
    for index, row in enumerate(rows, 1):
        label = f"[{index}/{len(rows)}] {row.project} #{row.number} ({row.pr_slice})"
        try:
            base, head = pr_refs(row.url)
            checkout = ensure_checkout(row.repo, base, head, args.workspace)
            note = "checked out"
            if row.pr_slice == "graph" and not args.no_graph:
                db = args.workspace / f"{row.repo.replace('/', '__')}.db"
                ensure_graph(checkout, db)
                note = require_alignment(db, row.changed_files)
            print(f"{label}: {note} at {head[:12]}", flush=True)
        except Exception as exc:  # recorded per row; one bad repo is not fifty
            failures.append((row, f"{type(exc).__name__}: {exc}"[:300]))
            print(f"{label}: FAILED - {failures[-1][1]}", file=sys.stderr, flush=True)

    print(f"\nprepared {len(rows) - len(failures)}/{len(rows)}")
    for row, error in failures:
        print(f"  {row.url}: {error}", file=sys.stderr)
    return 1 if failures else 0


def main() -> int:
    """Parse arguments and dispatch."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan", help="resolve slices and print populations")
    plan_parser.add_argument("--corpus", type=Path, default=CORPUS_DIR)
    plan_parser.add_argument("--out", type=Path, default=PLAN_FILE)
    plan_parser.add_argument("--profile", default="core", choices=("strict", "core", "all"))
    plan_parser.add_argument("--refresh", action="store_true", help="ignore the cache")

    prep = sub.add_parser("prepare", help="clone, check out and ingest; no LLM calls")
    prep.add_argument("--plan", type=Path, default=PLAN_FILE)
    prep.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    prep.add_argument("--pr", type=int, default=None, help="one PR number")
    prep.add_argument("--limit", type=int, default=None)
    prep.add_argument("--no-graph", action="store_true", help="skip ingest even where it applies")

    args = parser.parse_args()
    if args.command == "prepare":
        return prepare(args)
    return plan(args)


def pr_refs(pr_url: str) -> tuple[str, str]:
    """The PR's (base, head) commit SHAs.

    The head ref is frequently deleted after merge, so the branch name is
    useless; the SHA is what `git fetch origin <sha>` can still reach.
    """
    result = subprocess.run(
        ["gh", "pr", "view", pr_url, "--json", "baseRefOid,headRefOid"],
        capture_output=True,
        encoding="utf-8",
        check=False,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    if result.returncode != 0:
        _msg = f"gh pr view exited {result.returncode}: {result.stderr.strip() or '(no stderr)'}"
        raise RuntimeError(_msg)
    refs = json.loads(result.stdout)
    return refs["baseRefOid"], refs["headRefOid"]


def _git(verb: str, *args: str, cwd: Path | None = None, timeout: int = 900) -> str:
    """Run git, raising with stderr rather than a bare exit code."""
    result = subprocess.run(
        ["git", verb, *args],
        capture_output=True,
        encoding="utf-8",
        check=False,
        stdin=subprocess.DEVNULL,
        cwd=cwd,
        timeout=timeout,
    )
    if result.returncode != 0:
        _msg = f"git {verb} exited {result.returncode}: {result.stderr.strip()[:300]}"
        raise RuntimeError(_msg)
    return result.stdout


def ensure_checkout(repo: str, base_sha: str, head_sha: str, workspace: Path) -> Path:
    """A worktree of `repo` at `head_sha`, cloning once per repository.

    Blob-filtered and checkout-deferred: these are 500 MB to 1.9 GB repositories
    and the benchmark needs one commit from each. Measured on sentry, clone plus
    checkout is ~23 s and the tree is 431 MB.

    Both SHAs are fetched, not just the head. `ContextCollector` diffs
    `base...HEAD`, so a missing base makes the diff silently empty rather than
    loudly absent — a review of nothing, scored as finding nothing.
    """
    clone = workspace / repo.replace("/", "__")
    if clone.exists() and not (clone / ".git").is_dir():
        # An interrupted clone leaves a directory git will refuse to clone into
        # ("destination path already exists and is not an empty directory"),
        # which would then fail every run until someone cleared it by hand.
        if clone.is_dir():
            shutil.rmtree(clone)
        else:
            clone.unlink()
    if not (clone / ".git").is_dir():
        clone.parent.mkdir(parents=True, exist_ok=True)
        _git(
            "clone",
            "--filter=blob:none",
            "--no-checkout",
            f"https://github.com/{repo}.git",
            str(clone),
        )
    for sha in (base_sha, head_sha):
        _git("fetch", "--quiet", "origin", sha, cwd=clone)
    _git("checkout", "--quiet", "--force", head_sha, cwd=clone)
    return clone


def ensure_graph(checkout: Path, db_path: Path) -> None:
    """Ingest the checkout into `db_path`, replacing any previous graph.

    Removed first rather than re-ingested in place: the store is incremental,
    and a database carried over from another commit would answer lookups with
    nodes that are not in the tree under review.

    The WAL sidecars go too. A clean close checkpoints and removes them, so they
    only survive an interrupted ingest — which is precisely the state this
    workspace gets into, as the partial-clone handling above already attests.
    Named by appending rather than `with_suffix(".db-wal")`: that happens to be
    right for a `.db` path and silently wrong for any other extension.
    """
    for path in (db_path, *(db_path.with_name(db_path.name + s) for s in ("-wal", "-shm"))):
        path.unlink(missing_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "cgis.cli", "ingest", str(checkout), "--output", str(db_path)],
        capture_output=True,
        encoding="utf-8",
        check=False,
        stdin=subprocess.DEVNULL,
        timeout=1800,
    )
    if result.returncode != 0:
        _msg = f"ingest exited {result.returncode}: {result.stderr.strip()[:300]}"
        raise RuntimeError(_msg)


if __name__ == "__main__":
    sys.exit(main())
