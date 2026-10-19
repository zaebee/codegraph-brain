"""The mining and drawing rules for the data-key evaluation sets, fixed before building.

The rules are pure, so the tests feed them made-up PRs; one test reads the committed
outputs to check that no development case leaked into the held-out set.
"""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "data_keys"))

import draw_noise  # noqa: E402
import mine_blame  # noqa: E402
import mine_heldout  # noqa: E402
from mine_heldout import DEV_PRS, PR, all_pairs, find_pairs, heldout_pairs, repairs  # noqa: E402

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def pr(number: int, days_ago: float, title: str = "feat(x): add", body: str = "") -> PR:
    """A merged PR ``days_ago`` before NOW."""
    return PR(number, NOW - timedelta(days=days_ago), f"sha{number}", title, body)


def test_a_fix_naming_an_earlier_pr_within_30_days_repairs_it() -> None:
    """A fix whose body says #2066, merged 5 days after it, repairs 2066."""
    broken = pr(2066, 10)
    fix = pr(2100, 5, "fix(x): repair", "Repairs #2066.")
    assert repairs(fix, [broken]) == broken


def test_a_reference_older_than_30_days_is_not_a_repair() -> None:
    """Thirty-one days is outside the window."""
    broken = pr(2066, 40)
    fix = pr(2100, 9, "fix(x): repair", "Repairs #2066.")
    assert repairs(fix, [broken]) is None


def test_a_non_fix_commit_repairs_nothing() -> None:
    """Only a title starting ``fix`` can repair."""
    broken = pr(2066, 10)
    for title in ("feat(x): more", "chore: tidy"):
        assert repairs(pr(2100, 5, title, "Follows #2066."), [broken]) is None


def test_a_reference_to_a_later_pr_or_to_itself_is_not_a_repair() -> None:
    """The referenced PR must be earlier, and a PR does not repair itself."""
    later = pr(2200, 1)
    fix = pr(2100, 5, "fix: x", "See #2200 and #2100.")
    assert repairs(fix, [later, fix]) is None


def test_dev_cases_are_excluded() -> None:
    """No development PR is breaking or fix in the held-out set."""
    assert {2093, 2088, 2066, 2071, 2075, 2079, 2085, 2057, 1832, 2082, 2096, 2098} <= DEV_PRS
    assert {1815, 2040} <= DEV_PRS
    prs = [
        pr(2066, 20),
        pr(2100, 15, "fix: a", "Repairs #2066."),  # dev breaking
        pr(2300, 14),
        pr(2093, 10, "fix: b", "Repairs #2300."),  # dev fix
        pr(2400, 9),
        pr(2401, 5, "fix: c", "Repairs #2400."),  # the only held-out pair
    ]
    assert len(find_pairs(prs)) == 3
    kept = heldout_pairs(prs)
    assert [(b.number, f.number) for b, f in kept] == [(2400, 2401)]


def test_heldout_is_capped_newest_first_and_one_pair_per_breakage() -> None:
    """Two fixes of one breakage count once; the cap keeps the newest fixes."""
    prs: list[PR] = []
    for i in range(20):
        prs += [
            pr(3000 + 2 * i, 60 - 2 * i),
            pr(3001 + 2 * i, 59 - 2 * i, "fix: z", f"#{3000 + 2 * i}"),
        ]
    prs.append(pr(4000, 1, "fix: again", "#3000"))
    kept = heldout_pairs(prs, limit=15)
    assert len(kept) == 15
    assert [f.number for _, f in kept] == sorted((f.number for _, f in kept), reverse=True)
    assert len({b.number for b, _ in kept}) == 15


def test_noise_draw_is_seeded_and_excludes_breaking_prs() -> None:
    """The draw repeats exactly, and never picks either side of a repair pair."""
    prs = [pr(n, 50 - n % 40) for n in range(100, 160)]
    prs += [pr(500, 20), pr(501, 18, "fix: q", "Repairs #500.")]
    first = draw_noise.draw(prs, NOW, 30)
    again = draw_noise.draw(list(reversed(prs)), NOW, 30)
    assert [p.number for p in first] == [p.number for p in again]
    assert len(first) == 30
    assert not {500, 501} & {p.number for p in first}
    other = draw_noise.draw(prs, NOW, 30, seed=7)
    assert [p.number for p in other] != [p.number for p in first]


def test_noise_leaves_out_old_dev_and_release_prs() -> None:
    """Only recent, non-dev, non-release PRs are candidates."""
    prs = [
        pr(10, 90),
        pr(2066, 3),
        pr(11, 3, "chore(main): release 1.0.0"),
        pr(12, 3, "Bump codecov/codecov-action from 7.0.0 to 7.1.0"),
        pr(13, 3),
    ]
    assert [p.number for p in draw_noise.candidates(prs, NOW)] == [13]


def test_committed_sets_hold_no_development_case() -> None:
    """The committed held-out file is 10-15 pairs and shares nothing with the dev set."""
    data = json.loads((ROOT / "benchmarks" / "data_keys" / "heldout.json").read_text())
    pairs = data["pairs"]
    assert 10 <= len(pairs) <= 15
    numbers = {p["breaking"]["pr"] for p in pairs} | {p["fix"]["pr"] for p in pairs}
    assert not numbers & mine_heldout.DEV_PRS
    noise = json.loads((ROOT / "benchmarks" / "data_keys" / "noise.json").read_text())
    drawn = {p["pr"] for p in noise["prs"]}
    assert len(drawn) == 30
    assert not drawn & numbers
    assert len(noise["public_repo"]["prs"]) == 15


def test_a_breaking_pr_that_is_itself_a_fix_is_dropped() -> None:
    """In a chain 1 <- 2 <- 3 only the pair whose breaking PR is no fix survives."""
    prs = [pr(2500, 20), pr(2501, 15, "fix: a", "#2500"), pr(2502, 10, "fix: b", "#2501")]
    assert [(b.number, f.number) for b, f in all_pairs(prs)] == [(2500, 2501), (2501, 2502)]
    assert [(b.number, f.number) for b, f in find_pairs(prs)] == [(2500, 2501)]
    noise = [*prs, pr(2600, 3)]
    assert [p.number for p in draw_noise.candidates(noise, NOW)] == [2600]


def test_blame_most_blamed_pr_wins_and_ties_go_to_the_most_recent() -> None:
    """The PR owning most of the changed lines is breaking; equal counts: newer PR."""
    old, new, fix = pr(3001, 30), pr(3002, 20), pr(3100, 5, "fix: q")
    most = mine_blame.blame_pairs(
        [old, new, fix], {3100: ["sha3001"] * 3 + ["sha3002"]}, frozenset()
    )
    assert [(b.number, n) for b, _, n in most] == [(3001, 3)]
    tie = mine_blame.blame_pairs([old, new, fix], {3100: ["sha3001", "sha3002"]}, frozenset())
    assert [b.number for b, _, _ in tie] == [3002]


def test_blame_window_and_non_pr_commits() -> None:
    """A PR merged over 60 days before the fix, or a commit that is no PR, makes no pair."""
    old, fix = pr(3001, 90), pr(3100, 5, "fix: q")
    assert mine_blame.blame_pairs([old, fix], {3100: ["sha3001", "deadbeef"]}, frozenset()) == []
    assert mine_blame.blame_pairs([pr(3001, 30), fix], {3100: ["deadbeef"]}, frozenset()) == []
    assert (
        mine_blame.blame_pairs(
            [pr(3001, 30), pr(3100, 5, "feat: q")], {3100: ["sha3001"]}, frozenset()
        )
        == []
    )


def test_blame_exclusions_and_chain_rule() -> None:
    """Excluded PRs on either side are dropped, and so is a breaking PR that is a fix."""
    a, b, c = pr(3001, 40), pr(3002, 30, "fix: b"), pr(3003, 20, "fix: c")
    blamed = {3002: ["sha3001"], 3003: ["sha3002"]}
    got = mine_blame.blame_pairs([a, b, c], blamed, frozenset())
    assert [(x.number, y.number) for x, y, _ in got] == [(3001, 3002)]
    assert mine_blame.blame_pairs([a, b, c], blamed, frozenset({3001})) == []
    assert mine_blame.blame_pairs([a, b], {3002: ["sha3001"]}, frozenset({3002})) == []
    assert mine_blame.blame_pairs([a, b], {3002: ["sha3001"]}, DEV_PRS | {3001}) == []


def test_blame_parsers_read_hunks_and_porcelain() -> None:
    """Old-side ranges come only from measured files; porcelain yields a sha per line."""
    diff = (
        "--- a/ownima-backend/app/x.py\n+++ b/ownima-backend/app/x.py\n"
        "@@ -10,2 +10,3 @@\n@@ -20 +21 @@\n@@ -30,0 +31,4 @@\n"
        "--- a/ownima-backend/app/tests/test_x.py\n+++ b/x\n@@ -1,5 +1 @@\n"
        "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n"
    )
    assert mine_blame.changed_ranges(diff) == {"ownima-backend/app/x.py": [(10, 2), (20, 1)]}
    sha = "a" * 40
    porcelain = f"{sha} 5 5 2\nauthor x\n\tcode\n{sha} 6 6\n\tmore\n"
    assert mine_blame.blamed_shas(porcelain) == [sha, sha]


def test_no_script_names_a_machines_home_directory() -> None:
    # Final review #32: inputs come from arguments or the environment (`locations.py`).
    scripts = sorted((ROOT / "scripts" / "data_keys").glob("*.py"))
    assert scripts, "no script found: the check below would pass on nothing"
    named = [p.name for p in scripts if "/home/" in p.read_text(encoding="utf-8")]
    assert named == []
