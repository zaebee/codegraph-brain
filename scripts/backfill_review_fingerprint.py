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


class UnresolvableShaError(RuntimeError):
    """Raised when a corpus row names a commit this repository does not have.

    A row we cannot place is not a row to guess about: the alternative is a
    digest computed over an empty tree, which looks exactly like a real one.
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
    """
    verify = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"],
        capture_output=True,
        cwd=repo_root,
        check=False,
    )
    if verify.returncode != 0:
        _msg = f"Cannot resolve guardian_sha {sha!r} to a commit in {repo_root}."
        raise UnresolvableShaError(_msg)

    def read(path: str) -> bytes | None:
        """Contents of `path` at `sha`, or None if absent at that commit.

        `None` here means only "absent" — the commit itself was already
        verified to resolve above, so this can no longer be an unresolvable
        sha wearing an absent-path disguise.
        """
        result = subprocess.run(
            ["git", "show", f"{sha}:{path}"],
            capture_output=True,
            cwd=repo_root,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout
        if b"does not exist" in result.stderr or b"exists on disk" in result.stderr:
            return None
        raise subprocess.CalledProcessError(
            result.returncode, "git show", result.stdout, result.stderr
        )

    return read


def backfill(path: Path, repo_root: Path) -> int:
    """Rewrite `path` in place with fingerprints; return the rows touched."""
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
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
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
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
