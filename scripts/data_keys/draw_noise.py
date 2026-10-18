"""Draw the noise sets for the data-key layer check, before any building.

Noise set: merged PRs from the last 60 days that are neither side of any repair pair
(the rule of ``mine_heldout``), drawn with ``random.Random(2026).sample``. Two draws:
30 from owner-api, and 15 from one public Python repository that uses Redis, pinned
here with its head sha. Like the mining script, this prints only counts and PR numbers.
"""

import argparse
import json
import random
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mine_heldout import (
    _NON_CODE_TITLE,
    DEFAULT_REPO,
    DEV_PRS,
    PR,
    REPO_ROOT,
    _gh,
    all_pairs,
    fetch_prs,
    first_parent,
)

SEED = 2026
DAYS = 60
OWNER_API_COUNT = 30
PUBLIC_COUNT = 15

#: The pinned public repository: pure Python, built on Redis, active in the window.
PUBLIC_REPO = "rq/rq"

DEFAULT_OUT = REPO_ROOT / "benchmarks" / "data_keys" / "noise.json"


def candidates(prs: Sequence[PR], now: datetime, days: int = DAYS) -> list[PR]:
    """PRs merged within ``days`` of ``now`` that sit on neither side of a repair pair.

    Development PRs and non-code PRs (releases, dependency bumps) are left out, and the
    result is ordered by number so that the seeded draw does not depend on fetch order.
    """
    paired = {pr.number for pair in all_pairs(prs) for pr in pair}
    since = now - timedelta(days=days)
    return sorted(
        (
            pr
            for pr in prs
            if pr.merged_at >= since
            and pr.number not in paired
            and pr.number not in DEV_PRS
            and not _NON_CODE_TITLE.match(pr.title)
        ),
        key=lambda pr: pr.number,
    )


def draw(
    prs: Sequence[PR], now: datetime, count: int, days: int = DAYS, seed: int = SEED
) -> list[PR]:
    """``count`` candidate PRs by a seeded sample, in PR-number order."""
    pool = candidates(prs, now, days)
    picked = random.Random(seed).sample(pool, min(count, len(pool)))
    return sorted(picked, key=lambda pr: pr.number)


def records(picked: Sequence[PR], parent_of: Callable[[str], str]) -> list[dict[str, object]]:
    """The committed record of each drawn PR: number, base and merge sha."""
    return [
        {"pr": pr.number, "base_sha": parent_of(pr.merge_sha), "merge_sha": pr.merge_sha}
        for pr in picked
    ]


def head_sha(repo: str) -> str:
    """The head commit of ``repo``'s default branch, to pin the public repository."""
    return _gh(["api", f"repos/{repo}/commits/HEAD", "--jq", ".sha"]).strip()


def main(argv: Sequence[str] | None = None) -> int:
    """Draw both noise sets, write ``--out``, print counts and PR numbers only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--public-repo", default=PUBLIC_REPO)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    now = datetime.now(UTC)
    own = draw(fetch_prs(args.repo), now, OWNER_API_COUNT)
    pub = draw(fetch_prs(args.public_repo), now, PUBLIC_COUNT)
    record = {
        "as_of": now.date().isoformat(),
        "seed": SEED,
        "window_days": DAYS,
        "repo": args.repo,
        "prs": records(own, lambda sha: first_parent(args.repo, sha)),
        "public_repo": {
            "repo": args.public_repo,
            "sha": head_sha(args.public_repo),
            "prs": records(pub, lambda sha: first_parent(args.public_repo, sha)),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"{args.repo}: {len(own)} noise PRs: {[pr.number for pr in own]}")
    print(f"{args.public_repo}: {len(pub)} noise PRs: {[pr.number for pr in pub]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
