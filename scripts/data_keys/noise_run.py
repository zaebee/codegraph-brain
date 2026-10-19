"""Run the diff check over the noise PRs and record every item it lists.

For each PR in ``noise.json`` (owner-api and the pinned public repository): export the
tree at ``merge_sha``, ingest it, run the check for ``base_sha..merge_sha`` with no cap,
and record each item (kind, changed, site, file, line, score, shown at the default cap or
hidden), the counts, and the check's wall time. The exported tree and database of a PR are
removed once it is done, since the corpus is large.

Usage: ``uv run python scripts/data_keys/noise_run.py [--out FILE] [--limit N]``
"""

import argparse
import json
import shutil
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from statistics import median
from typing import Any

from evaluate import PREFIXES, REPOS, SHOWN, SOURCE_ROOTS, export_tree, ingest

from cgis.query.diffcheck import DiffError, run_diff_check
from cgis.storage.sqlite_store import SQLiteStore

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
NOISE = REPO_ROOT / "benchmarks" / "data_keys" / "noise.json"
DEFAULT_OUT = REPO_ROOT / "benchmarks" / "data_keys" / "results" / "noise-items.json"
WORK = Path("/home/zaebee/bench-corpora/data-keys/noise-work")
PUBLIC_CLONE = Path("/home/zaebee/bench-corpora/data-keys/rq")


def run_pr(
    repo_name: str, repo: Path, pr: dict[str, Any], prefix: str, roots: Sequence[str]
) -> dict[str, Any]:
    """Check one PR against a fresh graph of its merge tree."""
    case_dir = WORK / f"{repo_name.replace('/', '-')}-{pr['pr']}"
    export_tree(repo, pr["merge_sha"], prefix, case_dir)
    db = case_dir / "graph.db"
    try:
        ingest(case_dir / prefix, db, roots)
        with SQLiteStore(str(db)) as store:
            started = time.monotonic()
            try:
                report = run_diff_check(
                    store, repo, pr["base_sha"], pr["merge_sha"], cap=10**9, prefix=prefix
                )
            except DiffError as error:
                # A check that fails is a result, not a reason to stop the series.
                return {
                    "repo": repo_name,
                    "pr": pr["pr"],
                    "base_sha": pr["base_sha"],
                    "merge_sha": pr["merge_sha"],
                    "error": str(error),
                }
            seconds = time.monotonic() - started
    finally:
        shutil.rmtree(case_dir, ignore_errors=True)
    items = [
        {
            "kind": i.kind,
            "changed": i.changed,
            "site": i.site,
            "file": i.file,
            "line": i.line,
            "score": i.score,
            "shown": index < SHOWN,
        }
        for index, i in enumerate(report.items)
    ]
    return {
        "repo": repo_name,
        "pr": pr["pr"],
        "base_sha": pr["base_sha"],
        "merge_sha": pr["merge_sha"],
        "listed": len(items),
        "shown": min(len(items), SHOWN),
        "hidden": max(len(items) - SHOWN, 0),
        "in_tests": report.in_tests,
        "changed_keys": len(report.changed_keys),
        "changed_symbols": len(report.changed_symbols),
        "check_seconds": round(seconds, 2),
        "items": items,
    }


def summarise(records: Sequence[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Per repository: PR count, median shown and listed items, the largest list."""
    out: dict[str, dict[str, float]] = {}
    for name in sorted({r["repo"] for r in records}):
        rows = [r for r in records if r["repo"] == name and "error" not in r]
        out[name] = {
            "prs": len(rows),
            "errors": sum(r["repo"] == name and "error" in r for r in records),
            "median_shown": median(r["shown"] for r in rows),
            "median_listed": median(r["listed"] for r in rows),
            "max_listed": max(r["listed"] for r in rows),
        }
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """Run every noise PR and write the records."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, help="only the first N PRs of each repository")
    args = parser.parse_args(argv)
    noise = json.loads(NOISE.read_text())
    public = noise["public_repo"]
    plan = [
        (
            "Ownima/owner-api",
            REPOS["owner-api"],
            noise["prs"],
            PREFIXES["owner-api"],
            SOURCE_ROOTS["owner-api"],
        ),
        (public["repo"], PUBLIC_CLONE, public["prs"], "", []),
    ]
    records: list[dict[str, Any]] = []
    for name, repo, prs, prefix, roots in plan:
        for pr in prs[: args.limit]:
            record = run_pr(name, repo, pr, prefix, roots)
            print(json.dumps({k: v for k, v in record.items() if k != "items"}), file=sys.stderr)
            records.append(record)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {"public_sha": public["sha"], "summary": summarise(records), "records": records},
            indent=1,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
