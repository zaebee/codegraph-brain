"""Mine held-out v2: the breaking PR is the one `git blame` says wrote the lines a fix changed.

The rule is written in ``benchmarks/data_keys/SEALING.md`` (v2), before this ran. Like the
v1 miner it prints only counts and PR numbers: diffs and blamed lines are parsed in memory
and never printed or stored.
"""

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mine_heldout import (
    _NON_CODE_TITLE,
    DEFAULT_REPO,
    DEV_PRS,
    MAX_PAIRS,
    PR,
    REPO_ROOT,
    fetch_prs,
    first_parent,
)

DEFAULT_OUT = REPO_ROOT / "benchmarks" / "data_keys" / "heldout-v2.json"
NOISE_PATH = REPO_ROOT / "benchmarks" / "data_keys" / "noise.json"
DEFAULT_CHECKOUT = Path("/home/zaebee/projects/ownima/owner-api")

#: The breaking PR is merged at most this long before the fix.
WINDOW = timedelta(days=60)

_FIX_TITLE = re.compile(r"^fix", re.IGNORECASE)
_CODE_EXTENSIONS = (".py", ".json", ".yaml", ".yml")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+")
_BLAME_HEADER = re.compile(r"^([0-9a-f]{40}) \d+ \d+")
_DIFF_FILE = re.compile(r"^--- a/(.+)$")


def is_measured_file(path: str) -> bool:
    """True for a non-test source or data file under ``ownima-backend/``."""
    parts = path.split("/")
    name = parts[-1]
    return (
        path.startswith("ownima-backend/")
        and name.endswith(_CODE_EXTENSIONS)
        and "tests" not in parts[:-1]
        and not name.startswith("test_")
        and name != "conftest.py"
    )


def changed_ranges(diff: str) -> dict[str, list[tuple[int, int]]]:
    """Old-side ``(start, count)`` ranges per measured file from a ``-U0`` diff.

    Pure additions (count 0) contribute nothing; a deleted or modified line does.
    """
    ranges: dict[str, list[tuple[int, int]]] = {}
    current: str | None = None
    for line in diff.splitlines():
        file_match = _DIFF_FILE.match(line)
        if file_match:
            path = file_match.group(1)
            current = path if is_measured_file(path) else None
            continue
        hunk = _HUNK.match(line)
        if hunk and current is not None:
            count = 1 if hunk.group(2) is None else int(hunk.group(2))
            if count > 0:
                ranges.setdefault(current, []).append((int(hunk.group(1)), count))
    return ranges


def blamed_shas(porcelain: str) -> list[str]:
    """One commit sha per blamed line, from ``git blame --porcelain`` output."""
    return [m.group(1) for line in porcelain.splitlines() if (m := _BLAME_HEADER.match(line))]


def blame_pairs(
    prs: Sequence[PR],
    blamed: Mapping[int, Sequence[str]],
    excluded: frozenset[int],
    limit: int = MAX_PAIRS,
) -> list[tuple[PR, PR, int]]:
    """``(breaking, fix, blamed_lines)`` for each fix PR, by the v2 rule.

    ``blamed`` maps a fix PR number to the commit sha blamed for each line it changed.
    The breaking PR is the PR whose merge commit is blamed most, merged 0-60 days before
    the fix (ties: the most recent). Chains are judged over all raw pairs, then ``excluded``
    PRs on either side are removed, and up to ``limit`` pairs are kept, newest fix first.
    """
    by_sha = {pr.merge_sha: pr for pr in prs}
    raw: list[tuple[PR, PR, int]] = []
    for fix in prs:
        if not _FIX_TITLE.match(fix.title) or _NON_CODE_TITLE.match(fix.title):
            continue
        counts = Counter(
            by_sha[sha].number
            for sha in blamed.get(fix.number, ())
            if sha in by_sha and by_sha[sha].number != fix.number
        )
        in_window = [
            (n, c)
            for n, c in counts.items()
            if timedelta(0)
            <= fix.merged_at - next(p for p in prs if p.number == n).merged_at
            <= WINDOW
        ]
        if not in_window:
            continue
        merged = {p.number: p.merged_at for p in prs}
        number, count = max(in_window, key=lambda nc: (nc[1], merged[nc[0]]))
        broken = next(p for p in prs if p.number == number)
        raw.append((broken, fix, count))
    fixes = {fix.number for _, fix, _ in raw}
    kept = [
        t
        for t in raw
        if t[0].number not in fixes
        and not ({t[0].number, t[1].number} & excluded)
        and not _NON_CODE_TITLE.match(t[0].title)
    ]
    kept.sort(key=lambda t: (t[1].merged_at, t[1].number), reverse=True)
    return kept[:limit]


def _git(checkout: Path, args: Sequence[str]) -> str:
    """Run a read-only git command in ``checkout``; an empty string when it fails."""
    done = subprocess.run(
        ["git", "-C", str(checkout), *args], capture_output=True, text=True, check=False
    )
    return done.stdout if done.returncode == 0 else ""


def blame_fix(checkout: Path, base: str, merge: str) -> list[str]:
    """The commit sha blamed, at ``base``, for each measured line the fix changed."""
    diff = _git(checkout, ["diff", "-U0", "--no-renames", base, merge, "--", "ownima-backend"])
    shas: list[str] = []
    for path, spans in changed_ranges(diff).items():
        flags = [arg for s, c in spans for arg in ("-L", f"{s},+{c}")]
        shas += blamed_shas(
            _git(checkout, ["blame", "-w", "--porcelain", *flags, base, "--", path])
        )
    return shas


def main(argv: Sequence[str] | None = None) -> int:
    """Mine v2, write ``--out``, print counts and PR numbers only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--checkout", type=Path, default=DEFAULT_CHECKOUT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    prs = fetch_prs(args.repo)
    noise = {p["pr"] for p in json.loads(NOISE_PATH.read_text(encoding="utf-8"))["prs"]}
    blamed: dict[int, list[str]] = {}
    for fix in prs:
        if _FIX_TITLE.match(fix.title):
            base = first_parent(args.repo, fix.merge_sha)
            blamed[fix.number] = blame_fix(args.checkout, base, fix.merge_sha)
    pairs = blame_pairs(prs, blamed, frozenset(DEV_PRS | noise))
    record = {
        "repo": args.repo,
        "as_of": datetime.now(UTC).date().isoformat(),
        "rule": "SEALING.md v2: git blame of the lines a fix changes; window 60 days",
        "pairs": [
            {
                "breaking": {
                    "pr": b.number,
                    "base_sha": first_parent(args.repo, b.merge_sha),
                    "merge_sha": b.merge_sha,
                },
                "fix": {"pr": f.number, "merge_sha": f.merge_sha},
                "blamed_lines": n,
            }
            for b, f, n in pairs
        ],
    }
    args.out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"fix PRs blamed: {len(blamed)}; v2 pairs: {len(pairs)}")
    for b, f, n in pairs:
        print(f"  breaking #{b.number}  fix #{f.number}  lines {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
