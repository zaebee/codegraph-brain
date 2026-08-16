"""Calibration backfill: a recall figure that names the reviewer that earned it (#390)."""

import asyncio
import collections
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from guardian_stubs import FlakyProvider

# This repository sets no pytest `pythonpath`, so every test that imports a
# script does this explicitly (test_backfill_review_fingerprint.py:16 and five
# others). Required, not optional.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

import guardian_calibrate as gc
from backfill_calibration_fingerprint import (
    CARRIED_FIELDS,
    DuplicateRowKeyError,
    UnjudgeableRowError,
    _results_by_row_key,
    carry_to_calibration,
    fingerprint_results,
    is_scored,
    main,
)
from backfill_review_fingerprint import git_reader, provider_for

from cgis.guardian.review_fingerprint import compute_fingerprint, resolve_active_providers

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BENCH_DIR = REPO_ROOT / "benchmarks" / "guardian"

#: A commit this repository really has, with a guardian tree to walk.
REAL_SHA = "d0d807ef01c556b882dc85b9fc0d2851d92aa1e5"


@pytest.fixture
def corpus_dir() -> Iterator[Path]:
    """A writable directory *inside* the repository.

    `safe_corpus_path` refuses any corpus outside `repo_root`, and `tmp_path`
    resolves under the system temp directory. Same reasoning, and same
    `.pytest_cache/` location, as the fixture in
    `test_backfill_review_fingerprint.py`.
    """
    cache_dir = REPO_ROOT / ".pytest_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    created = Path(tempfile.mkdtemp(dir=cache_dir))
    try:
        yield created
    finally:
        shutil.rmtree(created, ignore_errors=True)


def _scored_row(**overrides: object) -> dict[str, Any]:
    """A results row shaped like one `guardian_bench` writes after scoring."""
    base: dict[str, Any] = {
        "timestamp": "2026-08-01T00:00:00+00:00",
        "pr": 900,
        "run": 0,
        "model": "gemini-2.5-flash",
        "guardian_sha": REAL_SHA,
        "precision": 1.0,
        "recall": 1.0,
        "matched": {},
        "findings": [],
    }
    return base | overrides


def _error_row(**overrides: object) -> dict[str, Any]:
    """A results row shaped like the one `guardian_bench` writes on an API failure.

    No `guardian_sha`, no `matched`, no `precision` — the review never ran.
    """
    base: dict[str, Any] = {
        "timestamp": "2026-08-01T00:00:01+00:00",
        "pr": 901,
        "run": 0,
        "error": "429 RESOURCE_EXHAUSTED",
    }
    return base | overrides


def _write(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


class TestIsScored:
    """The line between a recorded review and a recorded failure."""

    def test_a_scored_row_is_scored(self) -> None:
        assert is_scored(_scored_row())

    def test_an_api_failure_is_not(self) -> None:
        assert not is_scored(_error_row())

    def test_half_a_row_is_not_scored(self) -> None:
        """Both keys are required; `guardian_calibrate.load_rows` requires both too."""
        assert not is_scored({"precision": 1.0})
        assert not is_scored({"matched": {}})


class TestFingerprintResults:
    """Pass one: reconstruct a digest for every row that recorded a review."""

    def test_a_scored_row_gains_a_reconstructed_digest(self, corpus_dir: Path) -> None:
        corpus = _write(corpus_dir / "results.jsonl", [_scored_row()])
        assert fingerprint_results(corpus, REPO_ROOT) == (1, 0, 0)
        row = json.loads(corpus.read_text().splitlines()[0])
        assert len(row["review_fingerprint"]) == 12
        assert row["review_fingerprint_source"] == "reconstructed"
        assert row["finder_provider"] == "gemini"
        assert row["skeptic_provider"] is None

    def test_an_api_failure_is_left_untouched(self, corpus_dir: Path) -> None:
        """A row with no review has no reviewer to name.

        Asserted as *field absence*, not as a null: a null would still be a
        claim that this row participates in the identity scheme, and these rows
        do not. They are also the rows with no `guardian_sha`, so any attempt to
        fingerprint them could only invent one.
        """
        corpus = _write(corpus_dir / "results.jsonl", [_scored_row(), _error_row()])
        assert fingerprint_results(corpus, REPO_ROOT) == (1, 0, 1)
        scored, failed = (json.loads(line) for line in corpus.read_text().splitlines())
        assert "review_fingerprint" in scored
        assert "review_fingerprint" not in failed
        assert failed["error"] == "429 RESOURCE_EXHAUSTED"

    def test_the_skeptic_provider_changes_the_digest(self, corpus_dir: Path) -> None:
        """A cross-provider skeptic must not hash to the same reviewer as none.

        This is what makes `codestral-latest`'s provider mapping load-bearing
        rather than cosmetic: an entry in the wrong vendor's column would widen
        or narrow the closure and mint a different, confidently wrong identity.
        Written as a comparison rather than a shape check because a shape check
        passes on two digests that should differ and do not.
        """
        alone = _write(corpus_dir / "a.jsonl", [_scored_row()])
        paired = _write(
            corpus_dir / "b.jsonl", [_scored_row(skeptic_model="mistral-medium-latest")]
        )
        fingerprint_results(alone, REPO_ROOT)
        fingerprint_results(paired, REPO_ROOT)
        first = json.loads(alone.read_text().splitlines()[0])
        second = json.loads(paired.read_text().splitlines()[0])
        assert second["skeptic_provider"] == "mistral"
        assert first["review_fingerprint"] != second["review_fingerprint"]

    def test_a_measured_row_is_never_downgraded(self, corpus_dir: Path) -> None:
        """A digest taken from the tree that ran outranks anything rebuilt from git.

        The sentinel is impossible on purpose: a reconstructed digest over
        `REAL_SHA` is a valid 12-hex string, so a length or non-emptiness check
        would pass on exactly the clobbering this guards against.

        Scenario without the guard: someone runs `guardian_bench` (writing
        `measured` digests over a possibly dirty tree), then re-runs this script
        — which is billed one-shot but is safely idempotent, so re-running looks
        harmless. Every measured digest becomes a reconstructed one, naming a
        different reviewer wherever the tree had uncommitted edits.
        """
        corpus = _write(
            corpus_dir / "results.jsonl",
            [
                _scored_row(
                    review_fingerprint="measured-not-a-digest",
                    review_fingerprint_source="measured",
                ),
                _scored_row(timestamp="2026-08-01T00:00:02+00:00"),
            ],
        )
        assert fingerprint_results(corpus, REPO_ROOT) == (1, 1, 0)
        kept, rebuilt = (json.loads(line) for line in corpus.read_text().splitlines())
        assert kept["review_fingerprint"] == "measured-not-a-digest"
        assert kept["review_fingerprint_source"] == "measured"
        assert len(rebuilt["review_fingerprint"]) == 12
        assert rebuilt["review_fingerprint_source"] == "reconstructed"


class TestResultsIndex:
    """`row_key` is the exact join, so a collision must stop the run."""

    def test_rows_are_indexed_by_pr_and_timestamp(self, corpus_dir: Path) -> None:
        corpus = _write(corpus_dir / "results.jsonl", [_scored_row(), _error_row()])
        index = _results_by_row_key(corpus)
        assert set(index) == {"900@2026-08-01T00:00:00+00:00", "901@2026-08-01T00:00:01+00:00"}

    def test_a_duplicate_key_is_refused(self, corpus_dir: Path) -> None:
        """Two reviews in one microsecond leave no way to tell which was judged."""
        corpus = _write(corpus_dir / "results.jsonl", [_scored_row(), _scored_row(run=1)])
        with pytest.raises(DuplicateRowKeyError, match="900@2026-08-01T00:00:00"):
            _results_by_row_key(corpus)


class TestCarryToCalibration:
    """Pass two: copy the subject's identity, never mint a fresh one."""

    def test_the_subject_digest_is_copied_verbatim(self, corpus_dir: Path) -> None:
        """A sentinel no computation could produce proves this is a copy.

        A recomputed digest over today's checkout would be a valid 12-hex
        string and would satisfy any length or non-emptiness assertion, while
        naming the wrong reviewer.
        """
        subject = _scored_row(
            review_fingerprint="not-a-real-digest",
            review_fingerprint_source="reconstructed",
            finder_provider="gemini",
            skeptic_provider="mistral",
            skeptic_model="mistral-medium-latest",
        )
        corpus = _write(
            corpus_dir / "calibration.jsonl",
            [{"row_key": "900@2026-08-01T00:00:00+00:00", "tp": 3}],
        )
        assert carry_to_calibration(corpus, {"900@2026-08-01T00:00:00+00:00": subject}) == 1
        row = json.loads(corpus.read_text().splitlines()[0])
        assert row["tp"] == 3
        for field in CARRIED_FIELDS:
            assert row[field] == subject[field]

    def test_an_unresolvable_row_key_is_refused(self, corpus_dir: Path) -> None:
        corpus = _write(corpus_dir / "calibration.jsonl", [{"row_key": "900@never", "tp": 1}])
        with pytest.raises(UnjudgeableRowError, match="900@never"):
            carry_to_calibration(corpus, {})

    def test_a_missing_row_key_is_refused(self, corpus_dir: Path) -> None:
        """A record that names no subject must not be handed one by default."""
        corpus = _write(corpus_dir / "calibration.jsonl", [{"tp": 1}])
        with pytest.raises(UnjudgeableRowError):
            carry_to_calibration(corpus, {"900@2026-08-01T00:00:00+00:00": _scored_row()})

    def test_a_key_naming_an_api_failure_is_refused(self, corpus_dir: Path) -> None:
        """An unscored subject cannot lend an identity it never had."""
        corpus = _write(
            corpus_dir / "calibration.jsonl",
            [{"row_key": "901@2026-08-01T00:00:01+00:00", "tp": 1}],
        )
        with pytest.raises(UnjudgeableRowError, match="901@"):
            carry_to_calibration(corpus, {"901@2026-08-01T00:00:01+00:00": _error_row()})

    def test_a_refusal_leaves_the_corpus_unwritten(self, corpus_dir: Path) -> None:
        """The write happens after every row resolves, not row by row.

        A partially rewritten calibration corpus is worse than an untouched
        one: some rows would carry an identity and some would not, and nothing
        in the file would say which pass produced which.
        """
        corpus = _write(
            corpus_dir / "calibration.jsonl",
            [{"row_key": "900@2026-08-01T00:00:00+00:00", "tp": 1}, {"row_key": "900@never"}],
        )
        before = corpus.read_text()
        with pytest.raises(UnjudgeableRowError):
            carry_to_calibration(
                corpus,
                {"900@2026-08-01T00:00:00+00:00": _scored_row(review_fingerprint="abc")},
            )
        assert corpus.read_text() == before


class TestMainRefusesBeforeWriting:
    """A refusal must leave *both* corpora as they were, not one of the two."""

    def test_an_unjudgeable_record_stops_the_run_before_results_is_touched(
        self, corpus_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`main` resolves the join first, then writes.

        Without that ordering `fingerprint_results` rewrote `results.jsonl` and
        only then did `carry_to_calibration` refuse, leaving one corpus migrated
        and the other untouched with nothing in either file saying which pass had
        run. That compounds with the measured-digest guard: a half-migration is
        also where a `measured` row would have been silently rebuilt.
        """
        results = _write(corpus_dir / "results.jsonl", [_scored_row()])
        calibration = _write(corpus_dir / "calibration.jsonl", [{"row_key": "900@never"}])
        before = results.read_text()
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "backfill_calibration_fingerprint.py",
                "--results",
                str(results),
                "--calibration",
                str(calibration),
                "--repo-root",
                str(REPO_ROOT),
            ],
        )
        with pytest.raises(UnjudgeableRowError, match="900@never"):
            main()
        assert results.read_text() == before
        assert "review_fingerprint" not in json.loads(results.read_text().splitlines()[0])


def test_the_forward_path_carries_every_field_this_one_does() -> None:
    """`guardian_calibrate` must carry each field the backfill carries.

    The two lists are written out separately — a live script should not import
    from a one-shot backfill — so nothing but this test keeps them in step. Add
    a sixth entry to `CARRIED_FIELDS` without touching `calibrate_row` and the
    committed corpus would satisfy
    `test_every_calibration_row_carries_its_subject_identity` by comparing
    `None` against `None` on every row.

    Asserted against the record `calibrate_row` actually produces, not against
    the source text: a regex over the file would pass on a field that is
    mentioned in a comment and never written.
    """
    truth = gc.load_truths(BENCH_DIR)[122]
    row = {
        "timestamp": "2026-08-01T00:00:00+00:00",
        "pr": 122,
        "run": 0,
        "model": "gemini-2.5-flash",
        "guardian_sha": REAL_SHA,
        "precision": 1.0,
        "recall": 1.0,
        "noise": 0,
        "matched": {},
        "ambiguous_hits": [],
        "findings": [],
    } | {field: f"sentinel-{field}" for field in CARRIED_FIELDS}
    record = asyncio.run(
        gc.calibrate_row(
            FlakyProvider([], '{"reasoning": "r", "match": false, "confidence": 0.5}'),
            row,
            truth,
            concurrency=2,
        )
    )
    missing = [field for field in CARRIED_FIELDS if record.get(field) != f"sentinel-{field}"]
    assert not missing, (
        f"CARRIED_FIELDS names {missing}, which scripts/guardian_calibrate.py does not "
        "carry onto the calibration record — the two passthroughs have drifted."
    )


def _committed_rows(name: str) -> list[dict[str, Any]]:
    """Every row of a committed bench corpus."""
    path = BENCH_DIR / name
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_every_scored_results_row_is_fingerprinted() -> None:
    """The committed results corpus must name a reviewer on every recorded review.

    The floor is a ratchet (`>=`), not a fixed count: a future bench run only
    raises it. Without it, an emptied `benchmarks/guardian/results.jsonl` makes
    the row list empty, `offenders` stays empty, and the test passes having
    inspected nothing — the reassuring answer produced by looking at no data,
    which this branch has produced repeatedly (#375 review). A *missing* file
    raises `FileNotFoundError` in `_committed_rows` and never reaches the floor;
    an earlier version of this docstring claimed the floor covered that case
    too, which it does not.
    """
    rows = _committed_rows("results.jsonl")
    scored = [r for r in rows if is_scored(r)]
    assert len(scored) >= 118, (
        f"found only {len(scored)} scored rows in results.jsonl, expected at least 118 — "
        "this usually means benchmarks/ was not found rather than that the corpus "
        "shrank, but either way the check below cannot be trusted over an empty result."
    )
    offenders = [
        r["timestamp"]
        for r in scored
        if not r.get("review_fingerprint")
        or r.get("review_fingerprint_source") not in {"measured", "reconstructed"}
    ]
    assert not offenders, f"scored results rows without a fingerprint: {offenders[:5]}"


def test_no_api_failure_row_acquired_a_fingerprint() -> None:
    """The unscored rows must stay out of the identity scheme entirely.

    Stated as its own test rather than folded into the one above, because the
    completeness check only looks at scored rows and would pass just as happily
    if the backfill had invented digests for the failures too.
    """
    rows = _committed_rows("results.jsonl")
    unscored = [r for r in rows if not is_scored(r)]
    assert len(unscored) >= 32, (
        f"found only {len(unscored)} unscored rows, expected at least 32 — an empty "
        "result would make the check below vacuous."
    )
    offenders = [r["timestamp"] for r in unscored if "review_fingerprint" in r]
    assert not offenders, f"API-failure rows given a fingerprint: {offenders[:5]}"


def test_every_calibration_row_carries_its_subject_identity() -> None:
    """Each calibration record must hold exactly the identity of the review it graded.

    Compared field by field against the results row rather than merely checked
    for presence: a calibration corpus where every row carries *some* digest,
    but not its own subject's, is the failure this whole change exists to
    prevent, and a presence check cannot see it.
    """
    calibration = _committed_rows("calibration.jsonl")
    assert len(calibration) >= 236, (
        f"found only {len(calibration)} calibration rows, expected at least 236 — "
        "an empty result must not read as 'every row is attributed'."
    )
    index = {f"{r['pr']}@{r['timestamp']}": r for r in _committed_rows("results.jsonl")}
    mismatches: list[str] = []
    for row in calibration:
        subject = index.get(row.get("row_key", ""))
        if subject is None:
            mismatches.append(f"{row.get('row_key')}: no such results row")
            continue
        mismatches.extend(
            f"{row['row_key']}.{field}: {row.get(field)!r} != {subject.get(field)!r}"
            for field in CARRIED_FIELDS
            if row.get(field) != subject.get(field)
        )
    assert not mismatches, f"calibration rows disagreeing with their subject: {mismatches[:5]}"


def test_a_same_provider_skeptic_shares_the_finders_identity() -> None:
    """Two digests in the committed corpus each cover two provider pairs. By design.

    `bc43a53129ba` covers `(gemini, no skeptic)` and `(gemini, gemini skeptic)`;
    `782f10a63759` does the same for mistral. That looks like a merge and is not:
    `resolve_active_providers` returns the *union*, so a same-provider skeptic
    adds nothing to the set, the closure is identical, and the digest must be
    too (#375 spec §3.3). The fingerprint answers "what code could this review
    read", not "how many passes ran" — the model names live in `finder_model`
    and `skeptic_model`, which are recorded separately and are what a consumer
    should key on if it needs the two apart.

    Pinned as an invariant because the alternative is worse than the confusion:
    someone reads the corpus, sees one identity spanning two configurations,
    "fixes" `resolve_active_providers` to distinguish them, and every stored
    digest in the repository silently goes stale — the exact drift #375's
    reproducibility test was added to catch after it happened once.
    """
    assert resolve_active_providers("gemini", "gemini") == resolve_active_providers("gemini", None)
    assert resolve_active_providers("gemini", "mistral") != resolve_active_providers("gemini", None)

    pairs_per_identity: dict[str, set[tuple[str, str | None]]] = collections.defaultdict(set)
    for row in _committed_rows("calibration.jsonl"):
        pairs_per_identity[row["review_fingerprint"]].add(
            (row["finder_provider"], row["skeptic_provider"])
        )
    assert len(pairs_per_identity) >= 11, (
        f"found only {len(pairs_per_identity)} identities, expected at least 11 — "
        "an empty result would make the check below vacuous."
    )
    # Stated directly as "every pair under this identity resolves to one active
    # set". The first version compared the finder names against the skeptic
    # names, which passes on {(gemini, None), (mistral, None)} — a real merge —
    # because both sides come out {gemini, mistral}. A check that cannot fail on
    # the thing it names is worse than no check.
    for identity, pairs in sorted(pairs_per_identity.items()):
        active_sets = {resolve_active_providers(finder, skeptic) for finder, skeptic in pairs}
        # `p[1] or ""` because a pair's skeptic is None when there was no skeptic,
        # and `sorted` on raw tuples raises TypeError comparing None to a str. The
        # corpus contains exactly that mix — ("gemini", None) and ("gemini",
        # "gemini") share one identity — so the message this assertion exists to
        # print would have crashed instead of printing, and only on the run where
        # it finally mattered. Verified: sorted([("g", None), ("g", "m")]) raises.
        readable = sorted(pairs, key=lambda p: (p[0], p[1] or ""))
        assert len(active_sets) == 1, (
            f"identity {identity} spans provider pairs {readable} resolving to "
            f"{[sorted(s) for s in active_sets]} — distinct active-provider sets sharing "
            "one digest is a genuine merge, not the same-provider equivalence."
        )


def test_committed_bench_fingerprints_are_reproducible() -> None:
    """A stored digest must be recomputable by the code that ships today.

    Same guard, same reasoning, as `test_stored_fingerprints_match_what_current_code_computes`
    in `test_backfill_review_fingerprint.py`: #375 shipped a corpus whose digests
    had gone stale under a widened closure walk, and nothing noticed until a
    downstream consumer tried to regenerate one. Reproducibility is the single
    property the downstream identity construction rests on.

    Grouped by `(guardian_sha, active_providers)` — the same cache key the
    backfill uses — so 118 rows cost one closure walk per distinct pair.
    """
    rows_by_key: dict[tuple[str, frozenset[str]], set[str]] = collections.defaultdict(set)
    for row in _committed_rows("results.jsonl"):
        if not is_scored(row):
            continue
        finder = provider_for(row["model"])
        skeptic_model = row.get("skeptic_model")
        skeptic = provider_for(skeptic_model) if skeptic_model else None
        active = resolve_active_providers(finder, skeptic)
        rows_by_key[(row["guardian_sha"], active)].add(row["review_fingerprint"])

    assert len(rows_by_key) >= 10, (
        f"found only {len(rows_by_key)} distinct (guardian_sha, active_providers) pairs, "
        "expected at least 10 — an empty result must not read as 'every stored "
        "fingerprint is reproducible' when it means 'nothing was compared'."
    )
    mismatches: list[str] = []
    # Sorted on the sha and the *sorted* provider set. Ten shas carry fourteen
    # (sha, active) keys, so shas tie, and a tie falls through to comparing two
    # frozensets — where `<` means proper-subset, a partial order. Incomparable
    # sets neither raise nor order, so `sorted` silently degraded to input order
    # and the "deterministic iteration" this sort implies was not there.
    for (sha, active), stored in sorted(
        rows_by_key.items(), key=lambda kv: (kv[0][0], sorted(kv[0][1]))
    ):
        recomputed = compute_fingerprint(git_reader(sha, REPO_ROOT), active)
        if stored != {recomputed}:
            mismatches.append(
                f"{sha[:8]} {sorted(active)}: stored {sorted(stored)} != {recomputed}"
            )
    assert not mismatches, f"stale digests in results.jsonl: {mismatches}"


def test_every_guardian_sha_in_the_corpora_is_a_commit_this_repository_has() -> None:
    """A stored digest is only worth having if a stranger can recompute it.

    Recomputation needs the tree at that row's `guardian_sha`, so a corpus that
    names a commit the repository does not publish carries an identity nobody
    but its author can verify — and #375 rests the whole downstream
    genome/address construction on exactly that verifiability.

    This is not hypothetical. All ten `guardian_sha` values in
    `calibration.jsonl` were reachable only in one developer's clone: their
    branches had been squash-merged and deleted, so `fetch-depth: 0` never
    fetched them and CI could not resolve a single one. They are now published
    as lightweight tags under `bench/guardian-sha/`, which `actions/checkout`
    does fetch. Deleting those tags makes 236 rows of measured recall
    unverifiable again, and this test is what says so.

    The review corpora under `benchmarks/martian-*.jsonl` are covered too, and
    two of their shas (`112e4373`, `aeebde91`) currently survive only because
    `origin/feat/skip-parse-failed` still exists. If that branch is ever deleted
    this test goes red; the remedy is the same as the one already applied here:

        git push origin <sha>:refs/tags/bench/guardian-sha/<short-sha>

    Runs `rev-parse` per distinct sha rather than per row — 16 distinct values
    over 351 rows at the time of writing.
    """
    shas: dict[str, set[str]] = collections.defaultdict(set)
    for path in sorted((REPO_ROOT / "benchmarks").rglob("*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            sha = json.loads(line).get("guardian_sha")
            if sha:
                shas[sha].add(str(path.relative_to(REPO_ROOT)))

    assert len(shas) >= 16, (
        f"found only {len(shas)} distinct guardian_sha values under benchmarks/, "
        "expected at least 16 — an empty result must not read as 'every commit is "
        "present' when it means 'no corpus was found'."
    )
    missing = {
        sha: sorted(paths)
        for sha, paths in shas.items()
        if subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"],
            capture_output=True,
            cwd=REPO_ROOT,
            check=False,
        ).returncode
        != 0
    }
    assert not missing, (
        "these guardian_sha values name commits this repository does not have, so "
        f"their digests cannot be recomputed by anyone: {missing}. Publish each as "
        "a tag: git push origin <sha>:refs/tags/bench/guardian-sha/<short-sha>"
    )
