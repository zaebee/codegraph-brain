"""Backfill: a historical digest, honestly labelled as reconstructed."""

import collections
import json
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# This repository sets no pytest `pythonpath`, so every test that imports a
# script does this explicitly (test_guardian_martian_script.py:26 and four
# others). Required, not optional.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from backfill_review_fingerprint import (
    InvalidShaError,
    UnknownModelError,
    UnresolvableShaError,
    UnsafeCorpusPathError,
    backfill,
    git_reader,
    provider_for,
    reject_corpus_path,
)

from cgis.guardian.review_fingerprint import compute_fingerprint

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def corpus_dir() -> Iterator[Path]:
    """A directory *inside* the repository, for tests that write a corpus file.

    `reject_corpus_path` (the pythonsecurity:S2083 fix) refuses any corpus
    path outside `repo_root` — deliberately, since `backfill()` overwrites
    whatever passes it. Pytest's own `tmp_path` fixture resolves under the
    system temp directory, which is outside this repository, so a corpus
    written there would be refused by the very validator most of these tests
    exist to exercise the *legitimate* path through. This fixture gives those
    tests a real corpus location instead.

    Placed under `.pytest_cache/`, which is already gitignored (it carries
    its own `.gitignore` besides), so a test interrupted before its `finally`
    runs leaves nothing for git to see.
    """
    cache_dir = REPO_ROOT / ".pytest_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    created = Path(tempfile.mkdtemp(dir=cache_dir))
    try:
        yield created
    finally:
        shutil.rmtree(created, ignore_errors=True)


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


def test_backfill_marks_rows_reconstructed(corpus_dir: Path) -> None:
    corpus = corpus_dir / "reviews.jsonl"
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


def test_unresolvable_sha_is_a_hard_failure(corpus_dir: Path) -> None:
    """Never a null: a mixed corpus means two identity schemes at once."""
    corpus = corpus_dir / "reviews.jsonl"
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


def test_git_reader_refuses_a_non_hex_sha() -> None:
    """A sha that is not shaped like a sha never reaches a subprocess argument.

    SonarCloud pythonsecurity:S6350: `guardian_sha` flows from a JSON corpus
    row straight into `git rev-parse`/`git show` argv. Argv form already
    blocks classic shell injection, but the taint shape is real regardless,
    and the fix is the same one #350 already established for a path taint
    (`reject_metrics_path`): validate the value's shape before it reaches the
    sink, rather than trust `git` to reject it safely.
    """
    with pytest.raises(InvalidShaError, match="not a hex string"):
        git_reader("not-a-sha; rm -rf /", REPO_ROOT)


def test_backfill_refuses_a_non_hex_guardian_sha(corpus_dir: Path) -> None:
    """The same refusal, reached the way a real corpus row reaches it."""
    corpus = corpus_dir / "reviews.jsonl"
    corpus.write_text(
        json.dumps({"guardian_sha": "../../etc/passwd", "finder_model": "gemini-2.5-flash"}) + "\n"
    )
    with pytest.raises(InvalidShaError):
        backfill(corpus, REPO_ROOT)


def test_reject_corpus_path_requires_a_jsonl_suffix(corpus_dir: Path) -> None:
    not_jsonl = corpus_dir / "reviews.json"
    not_jsonl.write_text("{}\n")
    assert reject_corpus_path(not_jsonl, REPO_ROOT) == "name must end in .jsonl"


def test_reject_corpus_path_requires_an_existing_file(corpus_dir: Path) -> None:
    missing = corpus_dir / "does-not-exist.jsonl"
    assert reject_corpus_path(missing, REPO_ROOT) == "it does not exist, or is not a regular file"


def test_reject_corpus_path_requires_an_existing_file_not_a_directory(corpus_dir: Path) -> None:
    a_directory = corpus_dir / "reviews.jsonl"
    a_directory.mkdir()
    assert (
        reject_corpus_path(a_directory, REPO_ROOT) == "it does not exist, or is not a regular file"
    )


def test_reject_corpus_path_confines_to_the_repo_root(tmp_path: Path) -> None:
    """The one case pytest's own `tmp_path` is exactly the right fixture for.

    Every other test in this file avoids `tmp_path` in favour of `corpus_dir`
    *because* it resolves outside the repository — which is precisely the
    condition this test exists to exercise.
    """
    outside = tmp_path / "reviews.jsonl"
    outside.write_text('{"guardian_sha": "0"}\n')
    refusal = reject_corpus_path(outside, REPO_ROOT)
    assert refusal is not None
    assert "outside the repository root" in refusal


def test_backfill_refuses_a_corpus_path_outside_the_repo_root(tmp_path: Path) -> None:
    """Wired at the call site, and nothing is read or written before the refusal."""
    outside = tmp_path / "reviews.jsonl"
    original = json.dumps({"guardian_sha": "0" * 40, "finder_model": "gemini-2.5-flash"}) + "\n"
    outside.write_text(original)
    with pytest.raises(UnsafeCorpusPathError, match="outside the repository root"):
        backfill(outside, REPO_ROOT)
    assert outside.read_text() == original, "a refused path must not be touched at all"


def test_backfill_refuses_a_non_jsonl_corpus_path(corpus_dir: Path) -> None:
    """Wired at the call site for the suffix check too, not only confinement."""
    not_jsonl = corpus_dir / "reviews.txt"
    not_jsonl.write_text("not json\n")
    with pytest.raises(UnsafeCorpusPathError, match=r"\.jsonl"):
        backfill(not_jsonl, REPO_ROOT)


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

    The floor below is a ratchet, not a fixed count: `>=`, so a future corpus
    file only ever raises it, never breaks this test by existing. Without it,
    an empty or missing `benchmarks/` — a partial checkout, a wrong cwd, a
    corpus accidentally emptied — makes `_review_shaped_rows` return `[]`,
    `offenders` stays empty, and the test passes having inspected nothing.
    That is not "the corpus is clean"; it is "the corpus was not found," and
    this test existed once already without noticing the difference (#375
    review, instance eight of the same shape).
    """
    rows = _review_shaped_rows(REPO_ROOT)
    assert len(rows) >= 115, (
        f"found only {len(rows)} review-shaped rows under benchmarks/, expected "
        "at least 115 — this usually means benchmarks/ was not found (wrong cwd, "
        "partial checkout) rather than that the corpus shrank, but either way the "
        "completeness check below cannot be trusted over an empty result."
    )
    offenders = set()
    for path, row in rows:
        fingerprint = row.get("review_fingerprint")
        source = row.get("review_fingerprint_source")
        if not fingerprint or source not in {"measured", "reconstructed"}:
            offenders.add(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"review-shaped rows without a fingerprint in: {sorted(offenders)}"


def test_stored_fingerprints_match_what_current_code_computes() -> None:
    """A stored digest must be reproducible by the code that ships today.

    Guards against exactly the drift the final review of #375 found: the
    corpora were backfilled with an older `walk_closure` (37 modules), the
    walk then widened to include ancestor package `__init__.py` files (43
    modules), and nothing noticed that every stored digest had gone stale
    until a downstream consumer tried to regenerate one and could not. That
    reproducibility — a stranger can recompute the same value from the same
    inputs — is the one property the downstream genome/address construction
    depends on, so a change to the walk that outpaces the corpus is a defect,
    not a footnote.

    Grouped by `(guardian_sha, active_providers)`, same as `backfill()`'s own
    cache key, so a 115-row corpus costs six `git`-backed closure
    recomputations rather than 115 — the shas and provider pairs repeat
    heavily across rows (measured: 6 distinct pairs over 115 rows). Each
    recomputation walks the ~43-module closure via `git show`, one subprocess
    per file; measured at ~5.1s for all six after `git_reader`'s per-path
    cache, acceptable inside the unit suite.

    The floor on the pair count below is a ratchet (`>=`), not a fixed count,
    for the same reason as the completeness test's row floor: without it, an
    empty or missing `benchmarks/` makes `rows_by_key` empty, the comparison
    loop below never runs, and `assert not mismatches` passes having checked
    zero pairs — the reassuring answer produced by inspecting nothing, which
    is the exact failure shape this branch has now produced eight times.
    """
    rows_by_key: dict[tuple[str, frozenset[str]], list[tuple[Path, str]]] = collections.defaultdict(
        list
    )
    for path, row in _review_shaped_rows(REPO_ROOT):
        finder = provider_for(row["finder_model"])
        skeptic_model = row.get("skeptic_model")
        skeptic = provider_for(skeptic_model) if skeptic_model else None
        active = frozenset({finder} | ({skeptic} if skeptic else set()))
        key = (row["guardian_sha"], active)
        rows_by_key[key].append((path, row["review_fingerprint"]))

    assert len(rows_by_key) >= 6, (
        f"found only {len(rows_by_key)} distinct (guardian_sha, active_providers) "
        "pairs, expected at least 6 — this usually means benchmarks/ was not "
        "found (wrong cwd, partial checkout) rather than that the corpus shrank, "
        "but either way an empty result must not read as 'every stored "
        "fingerprint is reproducible' when it means 'nothing was compared.'"
    )

    mismatches: list[str] = []
    for (sha, active), entries in sorted(rows_by_key.items(), key=lambda kv: kv[0][0]):
        recomputed = compute_fingerprint(git_reader(sha, REPO_ROOT), active)
        stale = {(path, stored) for path, stored in entries if stored != recomputed}
        if stale:
            affected = sorted({str(path.relative_to(REPO_ROOT)) for path, _ in stale})
            stored_values = sorted({stored for _, stored in stale})
            mismatches.append(
                f"guardian_sha={sha}, providers={sorted(active)}: "
                f"stored={stored_values} but current code computes {recomputed!r} "
                f"({len(stale)} row(s) in {affected})"
            )
    assert not mismatches, (
        "Stored review_fingerprint values do not match what today's "
        "compute_fingerprint/walk_closure produce for the same "
        "(guardian_sha, active_providers) — the walk changed since the "
        "corpus was last backfilled. Fix: re-run "
        "`uv run python scripts/backfill_review_fingerprint.py "
        "benchmarks/martian-reviews.jsonl benchmarks/martian-p3-run1.jsonl "
        "benchmarks/martian-p3-run2.jsonl benchmarks/martian-p3-run3.jsonl "
        "benchmarks/martian-repeat-reviews.jsonl`, not edit this test.\n" + "\n".join(mismatches)
    )
