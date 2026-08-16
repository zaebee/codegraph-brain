"""One-shot: give the bench and calibration corpora a reviewer identity (#390).

`benchmarks/guardian/calibration.jsonl` holds the only measured **recall** this
repository has — 236 rows with `n_goldens`/`tp`/`fp`/`fn`, i.e. a denominator for
what was never mentioned, which a review record cannot supply. None of it could
be attributed: the rows carry no `review_fingerprint`, so downstream a reviewer
that stays quiet is indistinguishable from one that misses nothing.

Two passes, in this order, because the second reads what the first writes:

1. `results.jsonl` — reconstruct the fingerprint from `model`, `skeptic_model`
   and `guardian_sha`, exactly as `backfill_review_fingerprint` does for the
   review corpora, and for the same reason it labels the result
   `reconstructed`.
2. `calibration.jsonl` — **copy** it from the results row each record judges,
   resolved by `row_key`. Never recomputed: a calibration record grades a review
   that ran under an older guardian, so a digest rebuilt from that record's own
   fields would still be right, but a digest rebuilt from *this* checkout would
   silently attribute someone else's recall to today's reviewer.

`row_key` is `pr@timestamp` and is unique across every results row, so the join
is exact rather than inferred. The first version of this analysis joined on
`(pr, run, guardian_sha, finder_model)` instead and found 9 ambiguous groups
disagreeing on `skeptic_model` — pure artefact of a composite key collapsing
distinct timestamps, in a corpus that already carried an exact one. That is why
`_results_by_row_key` refuses on a duplicate key rather than picking a winner:
the ambiguity was imaginary once, and if it ever becomes real, guessing which
row a record graded is the merge direction.

Error rows are left alone. 32 of the 150 results rows are API failures (429/503)
with no `guardian_sha` and no review: they are written by a different path in
`guardian_bench`, `load_rows` already drops them, and they have no reviewer to
identify.
"""

import argparse
import json
from pathlib import Path
from typing import Any

from backfill_review_fingerprint import (
    git_reader,
    provider_for,
    safe_corpus_path,
)

from cgis.guardian.review_fingerprint import compute_fingerprint, resolve_active_providers

#: Fields a calibration record copies from the results row it judges.
#:
#: `scripts/guardian_calibrate.py` spells the same five out as dict entries
#: rather than importing this tuple — a one-shot backfill script is the wrong
#: thing for the live path to depend on. So this is *not* a single definition,
#: and saying it was would be the kind of claim that reads as a guarantee and
#: is not one. What actually keeps the two in step is
#: `test_the_forward_path_carries_every_field_this_one_does`, which fails if a
#: field is added here and not there.
CARRIED_FIELDS = (
    "review_fingerprint",
    "review_fingerprint_source",
    "finder_provider",
    "skeptic_provider",
    "skeptic_model",
)


class UnjudgeableRowError(RuntimeError):
    """Raised when a calibration record names a results row that cannot be used.

    Either the `row_key` resolves to nothing, or it resolves to a row that was
    never scored (an API failure carries no `guardian_sha`). Both mean the
    record's subject is unknown, and a record whose subject is unknown must not
    receive a fingerprint invented for it.
    """


class DuplicateRowKeyError(RuntimeError):
    """Raised when two results rows share a `row_key`.

    `row_key` is `pr@timestamp`; a collision means two reviews were recorded in
    the same microsecond for the same PR, and there is no way left to tell which
    one a calibration record graded.
    """


def is_scored(row: dict[str, Any]) -> bool:
    """True for a row that records an actual review rather than an API failure.

    Mirrors `scripts/guardian_calibrate.py::load_rows`, which admits a row to
    judging on exactly these two keys. Kept in that shape deliberately: if the
    two ever disagree, this script fingerprints rows the judge never sees, or
    skips rows it does.
    """
    return "matched" in row and "precision" in row


def row_key(row: dict[str, Any]) -> str:
    """Identity of one recorded review — the same key `guardian_calibrate` writes."""
    return f"{row['pr']}@{row['timestamp']}"


def is_measured(row: dict[str, Any]) -> bool:
    """True for a row whose digest was taken from the tree that actually ran.

    A `measured` digest is strictly better evidence than anything this script
    can produce, and on a bench run with uncommitted edits the two genuinely
    disagree — a `git show` rebuild at `guardian_sha` cannot see a working-tree
    change, which is precisely the case `guardian_bench` records `measured` to
    capture, and which this repository has already lost experimental rows to
    (`backfill_review_fingerprint`, spec §6.1).
    """
    return row.get("review_fingerprint_source") == "measured"


def fingerprint_results(corpus: Path, repo_root: Path) -> tuple[int, int, int]:
    """Reconstruct digests for scored rows; return (reconstructed, measured, unscored).

    Rows that already carry a `measured` digest are left exactly as they are.
    Without that guard this script downgraded them: it is billed as one-shot but
    is safely idempotent, which invites a re-run, and a re-run after any bench
    pass would silently replace every measured digest with a reconstructed one —
    naming a different reviewer wherever the working tree had been dirty. That
    is the unrecoverable direction, arrived at by a routine repeat of a command
    that had always been harmless.
    """
    rows = [
        json.loads(line) for line in corpus.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    cache: dict[tuple[str, frozenset[str]], str] = {}
    scored = 0
    measured = 0
    for row in rows:
        if not is_scored(row):
            continue
        if is_measured(row):
            measured += 1
            continue
        finder = provider_for(row["model"])
        skeptic_model = row.get("skeptic_model")
        skeptic = provider_for(skeptic_model) if skeptic_model else None
        active = resolve_active_providers(finder, skeptic)
        key = (row["guardian_sha"], active)
        if key not in cache:
            cache[key] = compute_fingerprint(git_reader(row["guardian_sha"], repo_root), active)
        row["review_fingerprint"] = cache[key]
        row["review_fingerprint_source"] = "reconstructed"
        row["finder_provider"] = finder
        row["skeptic_provider"] = skeptic
        scored += 1
    corpus.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return scored, measured, len(rows) - scored - measured


def _results_by_row_key(corpus: Path) -> dict[str, dict[str, Any]]:
    """Index the results corpus by `row_key`, refusing a duplicate."""
    index: dict[str, dict[str, Any]] = {}
    for line in corpus.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if "timestamp" not in row or "pr" not in row:
            continue
        key = row_key(row)
        if key in index:
            _msg = f"Two results rows share row_key {key!r}; cannot tell which one was judged."
            raise DuplicateRowKeyError(_msg)
        index[key] = row
    return index


def resolve_subjects(
    corpus: Path, results: dict[str, dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Pair every calibration record with the results row it graded, or raise.

    Separated from the copying so it can run *before* anything is written.
    `main` calls it once against the pre-backfill results index purely for the
    refusal: an unresolvable `row_key` used to surface only after
    `fingerprint_results` had already rewritten `results.jsonl`, leaving one
    corpus migrated and the other untouched with no record in either file of
    which pass had run.

    The pairing is by `row_key` (`pr@timestamp`), which is exact rather than
    inferred, so a record either names its subject or has none.
    """
    rows = [
        json.loads(line) for line in corpus.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        key = row.get("row_key")
        subject = results.get(key) if key else None
        if subject is None or not is_scored(subject):
            _msg = (
                f"Calibration record row_key={key!r} names no scored results row. "
                f"Refusing to invent an identity for a record whose subject is unknown."
            )
            raise UnjudgeableRowError(_msg)
        pairs.append((row, subject))
    return pairs


def carry_to_calibration(corpus: Path, results: dict[str, dict[str, Any]]) -> int:
    """Copy the identity fields onto every calibration record; return rows touched.

    Every record is resolved before any is written, so a refusal leaves the file
    exactly as it was rather than partly carrying an identity and partly not.
    """
    pairs = resolve_subjects(corpus, results)
    for row, subject in pairs:
        for field in CARRIED_FIELDS:
            row[field] = subject.get(field)
    corpus.write_text("\n".join(json.dumps(row) for row, _ in pairs) + "\n", encoding="utf-8")
    return len(pairs)


def main() -> None:
    """Fingerprint the results corpus, then carry those values to calibration."""
    parser = argparse.ArgumentParser(description=__doc__)
    repo_root = Path(__file__).resolve().parent.parent
    bench = repo_root / "benchmarks" / "guardian"
    parser.add_argument("--results", type=Path, default=bench / "results.jsonl")
    parser.add_argument("--calibration", type=Path, default=bench / "calibration.jsonl")
    parser.add_argument("--repo-root", type=Path, default=repo_root)
    args = parser.parse_args()

    # Everything that can refuse, refuses before anything is written. The second
    # pass reads the file the first pass rewrites, so a bad calibration path — or
    # a calibration record naming no scored subject, or a duplicate row_key —
    # discovered afterwards would leave the two corpora half-migrated, with
    # nothing in either file recording which pass had run.
    results_path = safe_corpus_path(args.results, args.repo_root)
    calibration_path = safe_corpus_path(args.calibration, args.repo_root)
    resolve_subjects(calibration_path, _results_by_row_key(results_path))

    scored, measured, unscored = fingerprint_results(results_path, args.repo_root)
    print(
        f"{results_path}: {scored} scored rows fingerprinted, "
        f"{measured} already measured left alone, {unscored} unscored left alone"
    )
    carried = carry_to_calibration(calibration_path, _results_by_row_key(results_path))
    print(f"{calibration_path}: {carried} rows carried")


if __name__ == "__main__":
    main()
