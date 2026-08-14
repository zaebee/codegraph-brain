"""Backfill: a historical digest, honestly labelled as reconstructed."""

import json
import sys
from pathlib import Path

import pytest

# This repository sets no pytest `pythonpath`, so every test that imports a
# script does this explicitly (test_guardian_martian_script.py:26 and four
# others). Required, not optional.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from backfill_review_fingerprint import (
    UnknownModelError,
    UnresolvableShaError,
    backfill,
    git_reader,
    provider_for,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_provider_for_known_models() -> None:
    assert provider_for("gemini-2.5-flash") == "gemini"
    assert provider_for("mistral-medium-latest") == "mistral"


def test_provider_for_unknown_model_raises() -> None:
    """A default here would compute the digest over the wrong provider module."""
    with pytest.raises(UnknownModelError, match="claude-opus-5"):
        provider_for("claude-opus-5")


def test_git_reader_reads_a_historical_blob() -> None:
    read = git_reader("d0d807ef", REPO_ROOT)
    content = read("src/cgis/guardian/prompts.py")
    assert content is not None
    assert b"CGIS Guardian finder" in content


def test_git_reader_returns_none_for_a_missing_path() -> None:
    read = git_reader("d0d807ef", REPO_ROOT)
    assert read("src/cgis/guardian/does_not_exist.py") is None


def test_backfill_marks_rows_reconstructed(tmp_path: Path) -> None:
    corpus = tmp_path / "reviews.jsonl"
    corpus.write_text(
        json.dumps(
            {
                "url": "u",
                "project": "p",
                "pr_slice": "graph",
                "base_sha": "b",
                "head_sha": "h",
                "had_graph": True,
                "finder_model": "gemini-2.5-flash",
                "skeptic_model": "gemini-3.5-flash",
                "findings": [],
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "duration_s": 1.0,
                "parse_failed": False,
                "guardian_sha": "d0d807ef01c556b882dc85b9fc0d2851d92aa1e5",
                "reviewed_at": "2026-08-12T00:00:00+00:00",
            }
        )
        + "\n"
    )
    assert backfill(corpus, REPO_ROOT) == 1
    row = json.loads(corpus.read_text().splitlines()[0])
    assert row["review_fingerprint_source"] == "reconstructed"
    assert len(row["review_fingerprint"]) == 12
    assert row["finder_provider"] == "gemini"
    assert row["skeptic_provider"] == "gemini"


def test_unresolvable_sha_is_a_hard_failure(tmp_path: Path) -> None:
    """Never a null: a mixed corpus means two identity schemes at once."""
    corpus = tmp_path / "reviews.jsonl"
    # A *mapped* model on purpose. With an unmapped one, provider_for raises
    # UnknownModelError before git is ever called, and the test would pass
    # without exercising the unresolvable-sha path at all.
    corpus.write_text(
        json.dumps({"guardian_sha": "0" * 40, "finder_model": "gemini-2.5-flash"}) + "\n"
    )
    with pytest.raises(UnresolvableShaError):
        backfill(corpus, REPO_ROOT)


def test_unresolvable_sha_does_not_yield_an_empty_tree_digest() -> None:
    """The specific wrong answer this guards against.

    Without the up-front commit check, every `git show` against a nonexistent
    commit returns "exists on disk, but not in ...", the reader reads it as an
    absent path, the closure comes back empty, and the digest is a confident
    hash of nothing — identical for every unresolvable sha, which merges rows
    from different reviewers into one identity.
    """
    with pytest.raises(UnresolvableShaError):
        git_reader("0" * 40, REPO_ROOT)


def _review_shaped_rows(repo_root: Path) -> list[tuple[Path, dict[str, object]]]:
    """Every row under `benchmarks/` that has the shape of a `ReviewRecord`.

    Enumerated by globbing `benchmarks/**/*.jsonl`, never a hardcoded filename
    list — a hardcoded list of three names is exactly what left
    `martian-p3-run2.jsonl` and `martian-p3-run3.jsonl` unfingerprinted after
    the first backfill pass (#375), even though they were genuine reviews.

    Classified by fields, not filename or directory: `guardian_sha`,
    `findings`, and `url` all present. Two of the three are not enough —
    `benchmarks/guardian/results.jsonl` is a retired, pre-finder/skeptic
    single-model bench format that also carries both `guardian_sha` and
    `findings` on 118 of its 150 rows, and has no provider to resolve a
    fingerprint against.

    `url` rather than `finder_model`: for *this* test a narrower predicate is
    the dangerous direction — fewer rows classified as reviews means fewer
    rows required to carry a fingerprint, so every conjunct added is a way for
    a row to escape the requirement. That is inverted from the closure walk,
    where narrowing loses coverage of what is hashed; same word, opposite
    risk. `finder_model` keys the fingerprint's own provider lookup, which
    reads like the natural discriminator, but it rests on which model-name
    field a retired schema happened to use rather than on what a review
    fundamentally is — and it would silently stop discriminating the day
    `guardian_bench.py`'s `model` field is renamed to `finder_model` in a
    schema tidy-up (§8 residual risk), sweeping the 150 scored (not reviewed)
    rows in `benchmarks/guardian/results.jsonl` into the requirement. `url` is
    present on every row of all five review files and absent from every row of
    `benchmarks/guardian/results.jsonl` and `benchmarks/guardian/calibration.jsonl`
    (measured), and rests on "a review without a URL is not a review" — a
    property of what was reviewed, not of a field name a schema happens to use.

    Do not be tempted to classify by validating rows against `ReviewRecord`
    instead: `review_fingerprint` is a required field on it, so a row lacking
    a fingerprint fails validation and is never classified as a review at
    all — this test would then pass on exactly the corpus it exists to
    reject.
    """
    rows: list[tuple[Path, dict[str, object]]] = []
    for path in sorted((repo_root / "benchmarks").rglob("*.jsonl")):
        for raw_line in path.read_text().splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            row = json.loads(stripped)
            if "guardian_sha" in row and "findings" in row and "url" in row:
                rows.append((path, row))
    return rows


def test_every_review_shaped_row_under_benchmarks_is_fingerprinted() -> None:
    """No review-shaped row anywhere under `benchmarks/` may lack a fingerprint.

    A corpus file added tomorrow, by anyone, fails this test until it is
    backfilled — a one-time check leaves the next person to notice a gap; this
    notices for them.
    """
    offenders = set()
    for path, row in _review_shaped_rows(REPO_ROOT):
        fingerprint = row.get("review_fingerprint")
        source = row.get("review_fingerprint_source")
        if not fingerprint or source not in {"measured", "reconstructed"}:
            offenders.add(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"review-shaped rows without a fingerprint in: {sorted(offenders)}"
