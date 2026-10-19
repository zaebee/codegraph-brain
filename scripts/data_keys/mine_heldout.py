"""Mine the held-out breakages for the data-key layer check, before any building.

The rule is fixed here, once, and written down in
``docs/specs/2026-10-18-data-key-layer-design.md`` (Evaluation, Sets): a ``fix`` pull
request whose title or body names an earlier pull request merged 0 to 30 days before it
repairs that earlier one. The earlier PR is the *breaking* PR.

The script prints only counts and PR numbers. It never fetches a diff and never prints a
title or a body, so whoever runs it sees nothing of the held-out pull requests beyond
their numbers. They are measured once, at the end, by someone else.
"""

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from locations import checked_arg, checked_repo

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: The default repository mined, and where the committed output goes.
DEFAULT_REPO = "Ownima/owner-api"
DEFAULT_OUT = REPO_ROOT / "benchmarks" / "data_keys" / "heldout.json"

#: A fix repairs a pull request merged at most this long before it.
WINDOW = timedelta(days=30)

#: How many pairs are kept, newest fix first.
MAX_PAIRS = 15

#: The development set (spec case table): never held out, whether as breaking or fix.
DEV_PRS: frozenset[int] = frozenset(
    {2093, 2088, 2066, 2071, 2075, 2079, 2085, 2057, 1832, 2082, 2096, 2098, 1815, 2040}
)

_FIX_TITLE = re.compile(r"^fix", re.IGNORECASE)
#: ``#123`` that is not part of a longer word or a ``org/repo#123`` cross-repository link.
_REFERENCE = re.compile(r"(?<![\w/])#(\d+)\b")
#: Pull requests that change no code worth measuring: release bumps and dependency bumps.
_NON_CODE_TITLE = re.compile(r"^(chore\(main\): release|bump |build\(deps)", re.IGNORECASE)


@dataclass(frozen=True)
class PR:
    """One merged pull request, as much of it as the mining rule reads."""

    number: int
    merged_at: datetime
    merge_sha: str
    title: str
    body: str


def references(pr: PR) -> frozenset[int]:
    """Every ``#N`` named in the title or the body of ``pr``, except its own number."""
    found = {int(n) for n in _REFERENCE.findall(f"{pr.title}\n{pr.body}")}
    return frozenset(found - {pr.number})


def repairs(fix: PR, earlier: Sequence[PR]) -> PR | None:
    """The earlier PR that ``fix`` repairs, or None.

    ``fix`` must have a title starting ``fix`` and name, in its title or body, a PR in
    ``earlier`` that was merged no later than ``fix`` and no more than 30 days before it.
    When it names several, the most recently merged one is the breaking PR.
    """
    if not _FIX_TITLE.match(fix.title):
        return None
    named = references(fix)
    hits = [
        pr
        for pr in earlier
        if pr.number in named and timedelta(0) <= fix.merged_at - pr.merged_at <= WINDOW
    ]
    return max(hits, key=lambda pr: pr.merged_at, default=None)


def all_pairs(prs: Sequence[PR]) -> list[tuple[PR, PR]]:
    """Every ``(breaking, fix)`` pair in ``prs`` under the rule, development PRs included.

    A breaking PR keeps only its earliest fix, so no breakage is counted twice. Chains
    are kept here: the noise draw must stay clear of both sides of every original pair.
    """
    ordered = sorted(prs, key=lambda pr: (pr.merged_at, pr.number))
    first_fix: dict[int, tuple[PR, PR]] = {}
    for fix in ordered:
        broken = repairs(fix, [pr for pr in ordered if pr.number != fix.number])
        if broken is not None and broken.number not in first_fix:
            first_fix[broken.number] = (broken, fix)
    return list(first_fix.values())


def find_pairs(prs: Sequence[PR]) -> list[tuple[PR, PR]]:
    """The independent pairs: ``all_pairs`` minus those whose breaking PR is itself a fix.

    Pre-registered before any diff was read: in a chain (#1916, then #1919 fixing it,
    then #1920 fixing that) the middle PR's breakage is not independent of the first.
    """
    pairs = all_pairs(prs)
    fixes = {fix.number for _, fix in pairs}
    return [(broken, fix) for broken, fix in pairs if broken.number not in fixes]


def heldout_pairs(prs: Sequence[PR], limit: int = MAX_PAIRS) -> list[tuple[PR, PR]]:
    """The held-out pairs: development and non-code PRs removed, newest fix first, capped."""
    kept = [
        (broken, fix)
        for broken, fix in find_pairs(prs)
        if not ({broken.number, fix.number} & DEV_PRS)
        and not _NON_CODE_TITLE.match(broken.title)
        and not _NON_CODE_TITLE.match(fix.title)
    ]
    kept.sort(key=lambda pair: (pair[1].merged_at, pair[1].number), reverse=True)
    return kept[:limit]


def parse_prs(raw: str) -> list[PR]:
    """Parse the JSON of ``gh pr list --json number,mergedAt,mergeCommit,title,body``."""
    prs: list[PR] = []
    for item in json.loads(raw):
        commit = item.get("mergeCommit") or {}
        if not item.get("mergedAt") or not commit.get("oid"):
            continue
        prs.append(
            PR(
                number=int(item["number"]),
                merged_at=datetime.fromisoformat(item["mergedAt"].replace("Z", "+00:00")),
                merge_sha=str(commit["oid"]),
                title=str(item.get("title") or ""),
                body=str(item.get("body") or ""),
            )
        )
    return prs


def _gh(args: Sequence[str]) -> str:
    """Run ``gh`` and return its stdout; a failure stops the run with gh's own message."""
    done = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        message = f"gh {' '.join(args[:2])} failed: {done.stderr.strip()}"
        raise SystemExit(message)
    return done.stdout


def fetch_prs(repo: str) -> list[PR]:
    """Merged PRs of ``repo``, newest first, through ``gh`` (up to 1000)."""
    raw = _gh(
        [
            "pr",
            "list",
            "--repo",
            checked_repo(repo),
            "--state",
            "merged",
            "--json",
            "number,mergedAt,mergeCommit,title,body",
            "--limit",
            "1000",
        ]
    )
    return parse_prs(raw)


def first_parent(repo: str, sha: str) -> str:
    """The first parent of ``sha`` in ``repo``: a squash merge's base, by ``gh api``."""
    path = f"repos/{checked_repo(repo)}/commits/{checked_arg(sha)}"
    return _gh(["api", path, "--jq", ".parents[0].sha"]).strip()


def build_output(
    pairs: Sequence[tuple[PR, PR]], parent_of: Callable[[str], str], repo: str, as_of: str
) -> dict[str, object]:
    """The committed JSON record: rule, repository and one ``breaking``/``fix`` per pair."""
    return {
        "repo": repo,
        "as_of": as_of,
        "rule": "fix title; names a PR merged 0-30 days earlier; dev set excluded; max 15",
        "pairs": [
            {
                "breaking": {
                    "pr": broken.number,
                    "base_sha": parent_of(broken.merge_sha),
                    "merge_sha": broken.merge_sha,
                },
                "fix": {"pr": fix.number, "merge_sha": fix.merge_sha},
            }
            for broken, fix in pairs
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Mine ``--repo``, write ``--out``, print counts and PR numbers only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    prs = fetch_prs(args.repo)
    pairs = heldout_pairs(prs)
    as_of = datetime.now(UTC).date().isoformat()
    record = build_output(pairs, lambda sha: first_parent(args.repo, sha), args.repo, as_of)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"merged PRs read: {len(prs)}; held-out pairs: {len(pairs)}")
    for broken, fix in pairs:
        print(f"  breaking #{broken.number}  fix #{fix.number}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
