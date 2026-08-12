"""Phase 2 runner for the Martian Code Review Bench (#342).

    uv run python scripts/guardian_martian.py plan            # cached, free
    uv run python scripts/guardian_martian.py plan --refresh   # hits the network
    uv run python scripts/guardian_martian.py prepare --pr N   # clone + ingest, free
    uv run python scripts/guardian_martian.py review --pr N    # COSTS MONEY
    uv run python scripts/guardian_martian.py judge            # COSTS MONEY (little)
    uv run python scripts/guardian_martian.py report           # free

`plan` resolves which slice each of the 50 PRs belongs to and prints the
populations gate G5 is registered on. It costs no LLM calls: the only network
traffic is `gh pr diff --name-only`, and the answers are cached in
`benchmarks/martian/plan.json` so the plan is reproducible and reviewable as a
diff rather than re-derived on every run.
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from cgis.extractors.registry import is_supported, language_for
from cgis.guardian.calibrate import (
    DEFAULT_JUDGE_CONCURRENCY,
    assign_matches,
    candidate_text,
    judge_matrix,
    judge_score,
)
from cgis.guardian.chunked import run_review_routed
from cgis.guardian.collector import ContextCollector, parse_features
from cgis.guardian.martian import (
    G5_MIN_GAP_PP,
    GRAPHITE_F05,
    UNKNOWN_SLICE,
    BenchPr,
    JudgedReview,
    Profile,
    PrPlan,
    ReviewRecord,
    SliceCounts,
    SliceScore,
    as_profile,
    build_plan,
    candidate_findings,
    dense_grid,
    golden_texts,
    graph_slices,
    load_corpus,
    plan_population,
    pr_number,
    score_slice,
)
from cgis.guardian.providers.base import BaseProvider
from cgis.guardian.providers.mistral import MistralProvider
from cgis.guardian.runner import build_provider, build_skeptic_provider
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

#: Sidecar naming the commit a graph was built from. A suffix rather than three
#: string literals: the writer, the reader and the cleanup must agree, and
#: nothing but convention was holding them together.
COMMIT_MARKER_SUFFIX = ".commit"

REVIEWS_FILE = Path("benchmarks/martian-reviews.jsonl")
JUDGED_FILE = Path("benchmarks/martian-judged.jsonl")

#: This repository, resolved from the script's own location. `guardian_sha` must
#: describe the reviewer, and `review_one` spends most of its time with a
#: checkout of somebody else's repository on disk — a `rev-parse` without an
#: explicit cwd would record whichever repository the caller happened to be
#: standing in, which is a wrong value that looks entirely right.
REPO_ROOT = Path(__file__).resolve().parent.parent

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


async def review_one(row: PrPlan, args: argparse.Namespace) -> ReviewRecord:
    """Review one prepared PR and return its record. Costs finder + skeptic calls."""
    checkout = args.workspace / row.repo.replace("/", "__")
    if not (checkout / ".git").is_dir():
        _msg = f"not prepared: {checkout} is not a checkout. Run `prepare --pr {row.number}`."
        raise RuntimeError(_msg)
    base, head = pr_refs(row.url)
    _git("checkout", "--quiet", "--force", head, cwd=checkout)

    db = args.workspace / f"{row.repo.replace('/', '__')}.db"
    # The graph is per repository but the corpus has several PRs per repository,
    # each at its own commit. `prepare` rebuilds the database for whichever PR
    # it ran last, so reviewing an earlier one would silently use a graph of a
    # different tree — and `graph_alignment` would not notice, because the file
    # names mostly still match. The commit is checked, not assumed.
    # Rebuilt unless the marker *proves* the graph is this commit's. An earlier
    # version skipped when the marker was absent, which let a database of
    # unknown provenance through — the very case the marker exists to catch.
    if row.pr_slice == "graph" and graph_commit(db) != head:
        ensure_graph(checkout, db, head)
    had_graph = (
        row.pr_slice == "graph" and db.is_file() and graph_alignment(db, row.changed_files)[0] > 0
    )

    provider, model = build_provider(os.environ)
    # Asked of the provider, not of the model name: `build_skeptic_provider`
    # picks the *opposite* provider, so a hardcoded "gemini" would hand a
    # mistral finder a mistral skeptic and quietly retire the cross-provider
    # design. Same test `scripts/guardian_review.py` uses.
    primary = "mistral" if isinstance(provider, MistralProvider) else "gemini"
    skeptic = build_skeptic_provider(os.environ, primary=primary)
    collector = ContextCollector(
        project_root=checkout,
        base_ref=base,
        db_path=db if had_graph else None,
        source_root=SOURCE_ROOT,
        features=parse_features(os.environ.get("GUARDIAN_FEATURES", "")),
    )
    started = time.monotonic()
    routed = await run_review_routed(
        provider=provider, collector=collector, skeptic_provider=skeptic[0] if skeptic else None
    )
    return ReviewRecord(
        url=row.url,
        project=row.project,
        pr_slice=row.pr_slice,
        base_sha=base,
        head_sha=head,
        had_graph=had_graph,
        finder_model=model,
        skeptic_model=skeptic[1] if skeptic else None,
        findings=routed.result.findings,
        prompt_tokens=provider.cumulative_usage.prompt_tokens,
        completion_tokens=provider.cumulative_usage.completion_tokens,
        skeptic_prompt_tokens=skeptic[0].cumulative_usage.prompt_tokens if skeptic else 0,
        skeptic_completion_tokens=skeptic[0].cumulative_usage.completion_tokens if skeptic else 0,
        duration_s=round(time.monotonic() - started, 2),
        parse_failed=routed.result.parse_failed,
        guardian_sha=guardian_version(),
        reviewed_at=datetime.now(UTC).isoformat(),
    )


def guardian_version() -> str:
    """The reviewer's commit, or "unknown" outside a checkout.

    Provenance, not correctness: a tarball deployment with no `.git` should not
    lose a paid review over a field that only says which Guardian produced it.
    """
    try:
        return _git("rev-parse", "HEAD", cwd=REPO_ROOT).strip()
    except (RuntimeError, OSError):
        return os.environ.get("GUARDIAN_SHA", "unknown")


def done_urls(path: Path) -> set[str]:
    """URLs already reviewed, so a rerun does not pay for them twice."""
    if not path.is_file():
        return set()
    urls: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            urls.add(json.loads(line)["url"])
        except (json.JSONDecodeError, KeyError, TypeError):
            # A crash mid-append leaves a truncated last line. Refusing to parse
            # it would block resume entirely, which is the one thing protecting
            # work already paid for; skipping it costs at most one re-review.
            print(f"Skipping unreadable record in {path}: {line[:80]!r}", file=sys.stderr)
    return urls


async def review(args: argparse.Namespace) -> int:
    """Review prepared PRs, appending one record each. THIS SPENDS MONEY."""
    rows = selected(load_plan(args.plan), args.pr, args.limit)
    already = done_urls(args.out)
    pending = [r for r in rows if r.url not in already]
    print(
        f"{len(rows)} selected, {len(already & {r.url for r in rows})} already done, "
        f"{len(pending)} to review."
    )
    if args.dry_run:
        for index, row in enumerate(pending, 1):
            print(f"  [{index}/{len(pending)}] {row.project} #{row.number} {row.url}")
        return 0
    if not pending:
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    failures = 0
    with args.out.open("a", encoding="utf-8") as fh:
        for index, row in enumerate(pending, 1):
            label = f"[{index}/{len(pending)}] {row.project} #{row.number}"
            try:
                record = await review_one(row, args)
            except Exception as exc:
                failures += 1
                print(
                    f"{label}: FAILED - {type(exc).__name__}: {exc}"[:300],
                    file=sys.stderr,
                    flush=True,
                )
                continue
            fh.write(record.model_dump_json() + "\n")
            fh.flush()
            visible = len(candidate_findings(record))
            print(
                f"{label}: {visible} findings, graph={record.had_graph}, "
                f"{record.prompt_tokens}+{record.completion_tokens} tok, {record.duration_s}s",
                flush=True,
            )
    return 1 if failures else 0


def load_reviews(path: Path) -> list[ReviewRecord]:
    """Recorded reviews, last write per URL.

    Later wins: `review` re-reviews a PR only when the earlier attempt was
    incomplete, so the newest row is the one that should be judged.
    """
    if not path.is_file():
        _msg = f"No reviews at {path}. Run `guardian_martian.py review` first."
        raise FileNotFoundError(_msg)
    latest: dict[str, ReviewRecord] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = ReviewRecord.model_validate_json(line)
        except ValidationError:
            # Same reasoning as `done_urls`: a crash mid-append leaves one bad
            # line, and refusing to read the file would strand every review
            # already paid for.
            print(f"Skipping unreadable review in {path}: {line[:80]!r}", file=sys.stderr)
            continue
        latest[record.url] = record
    return list(latest.values())


async def judge_one(
    record: ReviewRecord,
    pr: BenchPr,
    provider: BaseProvider,
    model: str,
    profile: Profile,
    concurrency: int = DEFAULT_JUDGE_CONCURRENCY,
) -> JudgedReview:
    """Score one recorded review against its golden comments, semantically.

    `concurrency` is exposed because it is the difference between a usable run
    and an unusable one on Mistral: Phase 1's first Mistral judge lost 76.5% of
    its pairs to 429s at the default of 4, and 0.4% at 1. The retry path handles
    a rate limit; it cannot handle being rate-limited constantly.
    """
    goldens = golden_texts(pr, profile)
    candidates = [candidate_text(f) for f in candidate_findings(record)]
    pairs, failures = await judge_matrix(provider, goldens, candidates, concurrency)
    assignment = assign_matches(pairs)
    scored = judge_score(
        n_goldens=len(goldens), n_candidates=len(candidates), assignment=assignment
    )
    return JudgedReview(
        url=record.url,
        project=record.project,
        pr_slice=record.pr_slice,
        had_graph=record.had_graph,
        profile=profile,
        judge_model=model,
        n_goldens=len(goldens),
        n_candidates=len(candidates),
        tp=scored.tp,
        fp=scored.fp,
        fn=scored.fn,
        precision=scored.precision,
        recall=scored.recall,
        judge_failures=failures,
        decisions=dense_grid(pairs, len(goldens), len(candidates)),
        judged_at=datetime.now(UTC).isoformat(),
    )


def judged_keys(path: Path) -> set[tuple[str, str]]:
    """(url, judge_model) pairs already scored cleanly, so a rerun resumes."""
    if not path.is_file():
        return set()
    keys: set[tuple[str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not row["judge_failures"]:
                keys.add((row["url"], row["judge_model"]))
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            # A bare list or a row missing its keys is as unusable as broken
            # JSON, and none of them may stop a resume.
            print(f"Skipping unreadable judged row in {path}: {line[:80]!r}", file=sys.stderr)
    return keys


async def judge(args: argparse.Namespace) -> int:
    """Score recorded reviews with the semantic judge. Spends judge calls only."""
    profile = as_profile(args.profile)
    # Read once, outside the loop. Inside it, a missing or bad value would be
    # caught by the per-PR `except` and reported as 45 judge failures — a
    # programming error wearing the costume of a provider outage.
    concurrency = args.concurrency
    corpus = {pr.url: pr for pr in load_corpus(args.corpus)}
    reviews = [
        r
        for r in load_reviews(args.reviews)
        # Not `f"/{args.pr}" in r.url`: that matched PR 1234 when asked for 123.
        if args.pr is None or pr_number(r.url) == args.pr
    ]
    if args.dry_run:
        # Deliberately before `build_provider`: a mode that spends nothing must
        # not demand credentials, and the model name is only needed to work out
        # what has already been judged.
        print(f"{len(reviews)} reviews would be judged under profile {profile}:")
        for record in reviews:
            candidates = len(candidate_findings(record))
            print(f"  {record.project:10} {record.url}  ({candidates} candidates)")
        return 0

    provider, model = build_provider(os.environ)
    already = judged_keys(args.out)
    pending = [r for r in reviews if (r.url, model) not in already]
    print(
        f"{len(reviews)} reviews, {len(reviews) - len(pending)} already judged by {model}, "
        f"{len(pending)} to judge."
    )
    if not pending:
        return 0

    failures = 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as fh:
        for index, record in enumerate(pending, 1):
            label = f"[{index}/{len(pending)}] {record.project} {record.url.rsplit('/', 1)[-1]}"
            pr = corpus.get(record.url)
            if pr is None:
                # A review whose PR is no longer in the corpus cannot be scored
                # against anything. Reported per row like any other failure —
                # the alternative is a KeyError that ends the whole run.
                failures += 1
                print(f"{label}: FAILED - not in the corpus: {record.url}", file=sys.stderr)
                continue
            try:
                judged = await judge_one(record, pr, provider, model, profile, concurrency)
            except Exception as exc:
                failures += 1
                print(f"{label}: FAILED - {type(exc).__name__}: {exc}"[:250], file=sys.stderr)
                continue
            fh.write(judged.model_dump_json() + "\n")
            fh.flush()
            print(
                f"{label}: tp={judged.tp} fp={judged.fp} fn={judged.fn} "
                f"P={judged.precision:.2f} R={judged.recall:.2f}"
                + (f"  ({judged.judge_failures} unruled)" if judged.judge_failures else ""),
                flush=True,
            )
    return 1 if failures else 0


def load_judged(path: Path) -> list[JudgedReview]:
    """Judged rows, last write per (url, judge), skipping anything unreadable."""
    if not path.is_file():
        _msg = f"No judged reviews at {path}. Run `guardian_martian.py judge` first."
        raise FileNotFoundError(_msg)
    latest: dict[tuple[str, str], JudgedReview] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = JudgedReview.model_validate_json(line)
        except ValidationError:
            print(f"Skipping unreadable judged row in {path}: {line[:80]!r}", file=sys.stderr)
            continue
        latest[row.url, row.judge_model] = row
    return list(latest.values())


def _score_line(score: SliceScore) -> str:
    """One slice as a row, with n attached — a rate without its n is not reportable."""
    return (
        f"  {score.name:12} n={score.prs:<3} "
        f"P={score.precision * 100:5.1f}  R={score.recall * 100:5.1f}  "
        f"F{score.beta:g}={score.f_beta * 100:5.1f}   "
        f"tp={score.tp:<3} fp={score.fp:<3} fn={score.fn}"
    )


def report(args: argparse.Namespace) -> int:
    """Print G4/G5/G6 from the judged records. Free; reads only what is on disk."""
    rows = load_judged(args.judged)
    if not rows:
        # Exiting 0 here would let a CI step that was supposed to evaluate
        # something pass by evaluating nothing — the quietest possible failure.
        print(f"No judged reviews in {args.judged}; nothing to report.", file=sys.stderr)
        return 1
    judges = sorted({r.judge_model for r in rows})
    profiles = sorted({r.profile for r in rows})
    exit_code = 0

    print(
        f"\n{len(rows)} judged reviews | judges: {', '.join(judges)} | "
        f"profile: {', '.join(profiles)}"
    )
    if len(profiles) > 1:
        print(
            "  REFUSING to mix profiles in one report: they are different corpora. "
            "Re-judge under one profile, or point --judged at a file holding only one.",
            file=sys.stderr,
        )
        return 1

    for judge_model in judges:
        mine = [r for r in rows if r.judge_model == judge_model]
        print(f"\n=== judge: {judge_model}, profile {profiles[0]} ===")
        print(_score_line(score_slice("ALL", mine, args.beta)))

        slices = graph_slices(mine)
        for name in ("graph", "diff-only"):
            print(_score_line(score_slice(name, slices[name], args.beta)))
        print("\n  per project:")
        for project in sorted({r.project for r in mine}):
            print(
                _score_line(
                    score_slice(project, [r for r in mine if r.project == project], args.beta)
                )
            )

        exit_code |= _gates(mine, slices)
    return exit_code


def _gates(rows: Sequence[JudgedReview], slices: dict[str, list[JudgedReview]]) -> int:
    """Print the three pre-registered gates and return non-zero if any fails."""
    python_rows = [r for r in rows if r.project == "sentry"]
    g4 = score_slice("sentry", python_rows, 0.5)
    g4_pass = g4.f_beta * 100 >= GRAPHITE_F05
    print(
        f"\nG4  Python-slice F0.5 >= {GRAPHITE_F05} (Graphite's floor)\n"
        f"  F0.5={g4.f_beta * 100:.1f} on n={g4.prs}  [{'PASS' if g4_pass else 'FAIL'}]"
    )
    if g4.prs < 10:
        print(f"  n={g4.prs}: too few to headline whatever it says (spec, §reconnaissance).")

    graph = score_slice("graph", slices["graph"])
    diff = score_slice("diff-only", slices["diff-only"])
    print(f"\nG5  recall(graph) - recall(diff-only) >= {G5_MIN_GAP_PP:.0f} pp")
    if not graph.prs or not diff.prs:
        # UNDEFINED, not FAIL. An empty slice scores recall 1.0 by the vacuous
        # convention every scorer here shares, so comparing against it produces
        # a confident-looking number that measures nothing — the exact shape of
        # the bug #345 was retracted for. A gate with a missing side has no
        # verdict to give.
        g5_pass = False
        print(
            f"  UNDEFINED: n={graph.prs} graph vs {diff.prs} diff-only. "
            "An empty slice scores recall 1.0 vacuously, so the difference "
            "would be an artefact rather than a measurement."
        )
    else:
        gap = (graph.recall - diff.recall) * 100
        g5_pass = gap >= G5_MIN_GAP_PP
        print(
            f"  {graph.recall * 100:.1f} - {diff.recall * 100:.1f} = {gap:+.1f} pp  "
            f"(n={graph.prs} vs {diff.prs})  [{'PASS' if g5_pass else 'FAIL'}]"
        )

    print(
        "\nG6  every number above names its judge, its profile and its slice, "
        "and carries n.  [PASS by construction]"
    )
    return 0 if (g4_pass and g5_pass) else 1


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
                ensure_graph(checkout, db, head)
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

    rev = sub.add_parser("review", help="review prepared PRs (COSTS MONEY)")
    rev.add_argument("--plan", type=Path, default=PLAN_FILE)
    rev.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    rev.add_argument("--out", type=Path, default=REVIEWS_FILE)
    rev.add_argument("--pr", type=int, default=None)
    rev.add_argument("--limit", type=int, default=None)
    rev.add_argument("--dry-run", action="store_true", help="print what would be reviewed")

    jud = sub.add_parser("judge", help="score recorded reviews semantically (COSTS MONEY)")
    jud.add_argument("--corpus", type=Path, default=CORPUS_DIR)
    jud.add_argument("--reviews", type=Path, default=REVIEWS_FILE)
    jud.add_argument("--out", type=Path, default=JUDGED_FILE)
    jud.add_argument("--profile", default="core", choices=("strict", "core", "all"))
    jud.add_argument("--pr", type=int, default=None)
    jud.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_JUDGE_CONCURRENCY,
        help="parallel judge calls; use 1 on Mistral, which 429s hard above it",
    )
    jud.add_argument("--dry-run", action="store_true")

    rep = sub.add_parser("report", help="print G4/G5/G6 from the judged records")
    rep.add_argument("--judged", type=Path, default=JUDGED_FILE)
    rep.add_argument("--beta", type=float, default=0.5, help="F-beta; 0.5 weights precision")

    args = parser.parse_args()
    if args.command == "report":
        return report(args)
    if args.command == "judge":
        return asyncio.run(judge(args))
    if args.command == "prepare":
        return prepare(args)
    if args.command == "review":
        return asyncio.run(review(args))
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
    try:
        refs = json.loads(result.stdout)
        return refs["baseRefOid"], refs["headRefOid"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        # `prepare` records this message on the row instead of raising, so the
        # message is the whole diagnostic. A bare JSONDecodeError says
        # "Expecting value: line 1 column 1" and hides what gh actually said —
        # which, when gh is misconfigured, is usually the answer.
        _msg = f"unreadable gh output ({exc}); gh said: {result.stdout[:200]!r}"
        raise RuntimeError(_msg) from exc


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


def graph_commit(db_path: Path) -> str | None:
    """The commit a graph was built from, or None when unknown.

    Recorded in a sidecar because the database itself does not know: the store
    holds nodes, not provenance.

    A marker without its database is not provenance either — it describes a
    graph that no longer exists — so the database's absence answers None here
    rather than at each call site. Otherwise a stray marker left by a partial
    cleanup would vouch for a graph that was deleted, and the review would run
    with no graph context and no complaint.
    """
    if not db_path.is_file():
        return None
    marker = db_path.with_name(db_path.name + COMMIT_MARKER_SUFFIX)
    return marker.read_text(encoding="utf-8").strip() if marker.is_file() else None


def ensure_graph(checkout: Path, db_path: Path, head_sha: str) -> None:
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
    for path in (
        db_path,
        *(
            db_path.with_name(db_path.name + suffix)
            for suffix in ("-wal", "-shm", COMMIT_MARKER_SUFFIX)
        ),
    ):
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
    db_path.with_name(db_path.name + COMMIT_MARKER_SUFFIX).write_text(head_sha, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
