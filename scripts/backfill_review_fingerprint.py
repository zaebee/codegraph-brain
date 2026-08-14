"""One-shot: give the existing corpora the fingerprint they were run without (#375).

No review is re-run and no model is called. For each row this rebuilds the
closure at that row's `guardian_sha` via `git show` and hashes it, which is why
the result is labelled `reconstructed`: a digest rebuilt from git cannot see an
uncommitted working-tree edit, and this repository has already lost experimental
rows to exactly that (spec §6.1).

The alternative — leaving old rows null — is worse. A corpus where some rows key
on a fingerprint and some on a sha means two identity schemes coexisting, under
which one reviewer appears as two entities depending on which run it came from.
"""

import argparse
import json
import re
import subprocess
from pathlib import Path

from cgis.guardian.review_fingerprint import ReadFile, compute_fingerprint

#: Historical models only. Stated as a table rather than a prefix rule because a
#: prefix rule breaks on the first model whose name does not carry its vendor —
#: which is why records gained `finder_provider` (#375 §3.3). New rows never
#: reach this table; they state their provider.
MODEL_PROVIDERS: dict[str, str] = {
    "gemini-2.5-flash": "gemini",
    "gemini-3.5-flash": "gemini",
    "mistral-medium-latest": "mistral",
}

#: A git abbreviated-or-full object id: hex only, 7-40 characters. `git`
#: itself accepts abbreviations down to 4, but this repository's own
#: `guardian_sha` values are never shorter than 7 (see the fixtures in
#: `tests/unit/test_backfill_review_fingerprint.py`), so 7 is the floor that
#: rejects the most garbage without rejecting anything real.
_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")

#: Corpus files this script is willing to overwrite. Deliberately narrow: the
#: whole reason `reject_corpus_path` exists is that `backfill()` overwrites
#: whatever passes it.
_CORPUS_SUFFIXES = frozenset({".jsonl"})


class UnresolvableShaError(RuntimeError):
    """Raised when a corpus row names a commit this repository does not have.

    A row we cannot place is not a row to guess about: the alternative is a
    digest computed over an empty tree, which looks exactly like a real one.
    """


class InvalidShaError(RuntimeError):
    """Raised when a corpus row's `guardian_sha` is not shaped like a git sha.

    `sha` reaches `git rev-parse`/`git show` as a subprocess argument — argv
    form already blocks classic shell injection, but a value that never
    looked like a sha has no business reaching a subprocess regardless
    (pythonsecurity:S6350). Checked before `UnresolvableShaError`'s own
    resolution attempt, and for the same reason: a row whose sha does not
    resolve is already a hard failure here, not a `null`, and this extends
    that same refusal to a row whose sha is not a sha at all, rather than
    relying on `git` to safely reject a malformed argument it was never
    guaranteed to.
    """


class UnsafeCorpusPathError(RuntimeError):
    """Raised when a corpus path is not one `backfill()` should read or overwrite.

    The path arrives from `argparse` and is then read *and overwritten in
    place* — the write is the dangerous half (pythonsecurity:S2083), because
    every real corpus under `benchmarks/` is a committed artefact that cost
    real review time, and real money for hosted models, to produce. Nothing
    before this class stopped a mistyped argument, or a wrong `--repo-root`,
    from truncating one. See `reject_corpus_path` for what is actually
    checked.
    """


class UnknownModelError(RuntimeError):
    """Raised for a model with no provider mapping.

    Refused rather than defaulted: a wrong provider computes the closure over
    the wrong module and returns a confident wrong digest.
    """


def provider_for(model: str) -> str:
    """The provider that serves `model`, or raise."""
    provider = MODEL_PROVIDERS.get(model)
    if provider is None:
        _msg = f"No provider mapping for model {model!r}. Add it to MODEL_PROVIDERS."
        raise UnknownModelError(_msg)
    return provider


def reject_corpus_path(corpus_path: Path, repo_root: Path) -> str | None:
    """Return a refusal for a corpus path `backfill()` must not touch, or None if it is fine.

    Modelled on `reject_metrics_path` (`src/cgis/guardian/metrics.py`, #350),
    which cleared the same taint shape (pythonsecurity:S2083) to Sonar's
    satisfaction by validating rather than suppressing — read that function
    first, this follows its shape. The sink here is more dangerous than the
    append `reject_metrics_path` guards: `backfill()` reads a corpus and then
    *overwrites* it, so a bad path is not a stray file created somewhere, it
    is an existing committed corpus destroyed in place.

    Judged on the **resolved** path for the same reason: a dangling symlink
    makes `is_file()` False and would otherwise let the checks below it pass
    while a write lands somewhere unintended.

    Unlike `reject_metrics_path`, this one *does* confine to `repo_root` —
    deliberately the opposite choice, made explicitly rather than by default.
    `reject_metrics_path` avoids confinement because the metrics path is a
    library parameter with a legitimate ad-hoc/`tmp_path` use; a fingerprint
    backfill's corpus argument has no such use — every real corpus lives
    under this repository's `benchmarks/`, and the whole point of the check
    is that a mistyped path argument must not silently reach for a file
    outside the tree this script is even fingerprinting.

    Requiring an *existing* file (not merely a valid location) is
    deliberate too: this script never creates a corpus, only rewrites one
    that is already there, so a nonexistent target is refused rather than
    quietly begun.
    """
    try:
        root = repo_root.resolve()
        path = corpus_path.resolve()
    except (OSError, RuntimeError) as exc:
        return f"path is inaccessible ({exc})"
    if path.suffix.lower() not in _CORPUS_SUFFIXES:
        return f"name must end in {', '.join(sorted(_CORPUS_SUFFIXES))}"
    try:
        if not path.is_file():
            return "it does not exist, or is not a regular file"
    except OSError as exc:
        return f"path is inaccessible ({exc})"
    if not path.is_relative_to(root):
        return f"it is outside the repository root {root}"
    return None


def git_reader(sha: str, repo_root: Path) -> ReadFile:
    """A reader over the tree at `sha`.

    The commit is resolved ONCE, up front, and an unresolvable one raises here
    rather than at some later read. This is not tidiness. `git show` reports a
    missing path and a nonexistent commit with messages that cannot be told
    apart by substring:

        fatal: path 'x.py' does not exist in 'd0d807ef'          <- valid sha, absent path
        fatal: path 'x.py' exists on disk, but not in '000...'   <- INVALID sha

    Matching on those strings alone makes an unresolvable sha look like a tree
    where every path is missing — an empty closure, and a confident digest of
    nothing. That is the merge direction: two corpus rows from different
    reviewers would receive the same fingerprint.

    Once the commit is known to exist, both messages mean the same thing and
    the path is legitimately absent at that commit, so None is correct.

    Reads are cached by path, on both the hit and the miss branch. This is
    not only a subprocess-count optimisation — `compute_fingerprint` reads
    each closure path twice (once while walking, once while hashing) — it is
    what makes the digest consistent *by construction*: a cached reader
    cannot return different bytes for the same path within one computation,
    where an uncached `git show` in principle could (a concurrent write to
    the object store, a transient failure on the second call). The trade:
    `compute_fingerprint`'s `BrokenReaderError` guard, which exists to catch
    exactly a reader going `None` on a path it already served, becomes
    unreachable for *this* reader, because the walk's read and the hash's
    read now hit the same cache entry. The guard stays — it still protects
    every non-caching reader, including `disk_reader` — but for `git_reader`
    specifically, consistency-by-construction has already done the guard's
    job before the guard gets a chance to.
    """
    if not _SHA_PATTERN.fullmatch(sha):
        _msg = f"guardian_sha {sha!r} is not a hex string of 7-40 characters."
        raise InvalidShaError(_msg)

    verify = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"],
        capture_output=True,
        cwd=repo_root,
        check=False,
    )
    if verify.returncode != 0:
        _msg = f"Cannot resolve guardian_sha {sha!r} to a commit in {repo_root}."
        raise UnresolvableShaError(_msg)

    cache: dict[str, bytes | None] = {}

    def read(path: str) -> bytes | None:
        """Contents of `path` at `sha`, or None if absent at that commit.

        `None` here means only "absent" — the commit itself was already
        verified to resolve above, so this can no longer be an unresolvable
        sha wearing an absent-path disguise. Cached either way, so a second
        call for the same path never shells out again.
        """
        if path in cache:
            return cache[path]
        result = subprocess.run(
            ["git", "show", f"{sha}:{path}"],
            capture_output=True,
            cwd=repo_root,
            check=False,
        )
        if result.returncode == 0:
            cache[path] = result.stdout
            return cache[path]
        if b"does not exist" in result.stderr or b"exists on disk" in result.stderr:
            cache[path] = None
            return None
        raise subprocess.CalledProcessError(
            result.returncode, "git show", result.stdout, result.stderr
        )

    return read


def backfill(path: Path, repo_root: Path) -> int:
    """Rewrite `path` in place with fingerprints; return the rows touched.

    Refuses before touching the filesystem at all — see `reject_corpus_path`.
    """
    refusal = reject_corpus_path(path, repo_root)
    if refusal is not None:
        _msg = f"Refusing corpus path {path}: {refusal}."
        raise UnsafeCorpusPathError(_msg)

    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    cache: dict[tuple[str, frozenset[str]], str] = {}
    for row in rows:
        finder = provider_for(row["finder_model"])
        skeptic_model = row.get("skeptic_model")
        skeptic = provider_for(skeptic_model) if skeptic_model else None
        active = frozenset({finder} | ({skeptic} if skeptic else set()))
        key = (row["guardian_sha"], active)
        if key not in cache:
            cache[key] = compute_fingerprint(git_reader(row["guardian_sha"], repo_root), active)
        row["review_fingerprint"] = cache[key]
        row["review_fingerprint_source"] = "reconstructed"
        row["finder_provider"] = finder
        row["skeptic_provider"] = skeptic
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return len(rows)


def main() -> None:
    """Backfill every corpus named on the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpora", nargs="+", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    for corpus in args.corpora:
        count = backfill(corpus, args.repo_root)
        print(f"{corpus}: {count} rows")


if __name__ == "__main__":
    main()
