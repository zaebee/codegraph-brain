"""Run the diff check over a sets file and record whether each expected site is listed.

For each case with a merge commit: export the tree at ``merge_sha``, ingest it into a
case database, run the check for ``base_sha..merge_sha`` against the git repository, and
record whether ``expected_site`` is among all the items (shown or hidden) and whether it is
among the ones shown at the default cap. A hit is an item whose ``site`` equals the
expected ``id``, else one in the expected file (after the prefix mapping) whose line lies
within the span of the expected symbol, else, when the ``id`` is a key path rather than a
node, one in the expected file whose key (at the site, or the changed one) has that path.

Usage: ``uv run python scripts/data_keys/evaluate.py [--sets FILE] [--out FILE] [--only CASE]``
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from cgis.core.keys import KEY_ID_PREFIX, parse_key_id
from cgis.languages import extractors
from cgis.pipeline import IngestionPipeline
from cgis.query.diffcheck import DiffItem, run_diff_check
from cgis.storage.sqlite_store import SQLiteStore

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SETS = REPO_ROOT / "benchmarks" / "data_keys" / "dev_cases.yaml"
DEFAULT_OUT = REPO_ROOT / "benchmarks" / "data_keys" / "results" / "dev-task1.json"
CASES_DIR = Path("/home/zaebee/bench-corpora/data-keys/cases")

#: Where each repository named in a sets file lives, and its ingest root inside it.
REPOS: Mapping[str, Path] = {
    "owner-api": Path("/home/zaebee/projects/ownima/owner-api"),
    "cgis": REPO_ROOT,
}
#: The ingest root inside each repository, and the source roots under it. owner-api is
#: ingested at ownima-backend, where its OpenSearch mappings live beside app/, with app
#: as the source root so Python ids stay `models.owner.UserAdmin` (Task 4 ruling).
PREFIXES: Mapping[str, str] = {"owner-api": "ownima-backend", "cgis": "src"}
SOURCE_ROOTS: Mapping[str, list[str]] = {"owner-api": ["app"], "cgis": []}

#: Items a reader sees by default; the evaluation's "shown" is the first this many.
SHOWN = 5


def _slug(name: str) -> str:
    """A directory name for a case id."""
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name).strip("-")


def export_tree(repo: Path, sha: str, prefix: str, dest: Path) -> None:
    """Extract `prefix` of the tree at `sha` into a clean `dest`."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    archive = subprocess.Popen(
        ["git", "-C", str(repo), "archive", sha, *([prefix] if prefix else [])],
        stdout=subprocess.PIPE,
    )
    subprocess.run(["tar", "-x", "-C", str(dest)], stdin=archive.stdout, check=True)
    if archive.wait() != 0:
        message = f"git archive {sha} {prefix} failed"
        raise RuntimeError(message)


def ingest(root: Path, db: Path, source_roots: Sequence[str] = ()) -> None:
    """Ingest `root` into a fresh database at `db`, as `cgis ingest --source-root` does.

    The options are recorded, so the diff check re-parses base files with the same roots.
    """
    db.unlink(missing_ok=True)
    roots = list(source_roots)
    pipeline = IngestionPipeline(extractors(roots))
    with SQLiteStore(str(db)) as store:
        pipeline.run(str(root), store=store, rebuild=True)
        store.record_ingest_options(roots, None)


def _graph_file(repo_path: str, prefix: str) -> str:
    """A repository path as the graph spells it (the ingest prefix stripped)."""
    return (
        repo_path[len(prefix) + 1 :] if prefix and repo_path.startswith(prefix + "/") else repo_path
    )


def match(
    items: Sequence[DiffItem],
    expected: Mapping[str, str],
    prefix: str,
    span: tuple[str, int, int] | None,
) -> int | None:
    """Index of the first item that hits `expected`, else None.

    `span` is (file, start, end) of the expected symbol in the graph, when it is a node.
    """
    for index, item in enumerate(items):
        if item.site == expected["id"]:
            return index
    want = _graph_file(expected["file"], prefix)
    for index, item in enumerate(items):
        if item.file != want:
            continue
        if span is not None and item.line is not None and span[1] <= item.line <= span[2]:
            return index
        if span is None and expected["id"] in {_key_path(item.key), _key_path(item.changed)}:
            return index
    return None


def _key_path(key: str) -> str | None:
    """The path of a key id; None for anything else (a symbol id, an empty key)."""
    return parse_key_id(key)[1] if key.startswith(KEY_ID_PREFIX) else None


def run_case(case: Mapping[str, Any], work: Path) -> dict[str, Any]:
    """Evaluate one case; the result is a JSON-ready dict."""
    name = str(case["case"])
    result: dict[str, Any] = {"case": name, "in_zone": case.get("in_zone")}
    if not case.get("merge_sha") or not case.get("base_sha"):
        return {**result, "status": "skipped", "reason": "no merge commit"}
    repo = REPOS[case["repo"]]
    prefix = case.get("prefix", PREFIXES[case["repo"]])
    case_dir = work / _slug(name)
    started = time.monotonic()
    export_tree(repo, case["merge_sha"], prefix, case_dir)
    db = case_dir / "graph.db"
    ingest(case_dir / prefix, db, case.get("source_roots", SOURCE_ROOTS[case["repo"]]))
    ingested = time.monotonic()
    with SQLiteStore(str(db)) as store:
        report = run_diff_check(
            store, repo, case["base_sha"], case["merge_sha"], cap=10**9, prefix=prefix
        )
        expected = case.get("expected_site")
        hit: int | None = None
        if expected:
            node = store.get_node(expected["id"])
            span = (node.file_path, node.start_line, node.end_line) if node else None
            hit = match(report.items, expected, prefix, span)
    return {
        **result,
        "status": "done",
        "expected_site": expected,
        "listed": hit is not None if expected else None,
        "shown": hit is not None and hit < SHOWN if expected else None,
        "rank": hit,
        "items": len(report.items),
        "shown_items": [
            {"kind": i.kind, "site": i.site, "file": i.file, "line": i.line, "score": i.score}
            for i in report.items[:SHOWN]
        ],
        "kinds": {
            k: sum(i.kind == k for i in report.items)
            for k in sorted({i.kind for i in report.items})
        },
        "changed_keys": len(report.changed_keys),
        "in_tests": report.in_tests,
        "changed_symbols": len(report.changed_symbols),
        "ingest_seconds": round(ingested - started, 1),
        "check_seconds": round(time.monotonic() - ingested, 1),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Evaluate every case of the sets file and write the results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sets", type=Path, default=DEFAULT_SETS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--only", help="run only the case with this name")
    args = parser.parse_args(argv)
    loaded = yaml.safe_load(args.sets.read_text())
    cases = loaded["cases"] if isinstance(loaded, dict) else loaded
    results = []
    for case in cases:
        if args.only and case["case"] != args.only:
            continue
        outcome = run_case(case, CASES_DIR)
        print(json.dumps(outcome), file=sys.stderr)
        results.append(outcome)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"sets": str(args.sets.name), "results": results}, indent=2) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
