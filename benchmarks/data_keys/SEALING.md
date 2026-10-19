# How the evaluation sets were sealed, and what that does not prove

- **Order.** `labelling.md` was written before the mining run, in the same session. Both
  landed in one commit (fb7a714), so history cannot show the order. History was not
  rewritten: a split after the fact would prove nothing.
- **Rule used.** Only the reference branch of the spec's mining rule: a `fix` PR whose title
  or body names an earlier PR merged 0-30 days before it. The spec's other branch (a fix
  whose diff reverts lines the earlier PR introduced) needs diffs and was not used.
- **Reach.** `gh pr list --limit 1000` returns the newest 1000 PRs; the mined pairs reach
  back to about #1700. Older history is not covered.
- **Run date and pins.** `as_of` is 2026-10-18 in both `heldout.json` and `noise.json`
  (the 60-day noise window ends on that date). Public repository: `rq/rq` at
  `a0048377aceaadef3bea0e71c70b3e326fb78687`. owner-api shas are in the two JSON files.
- **Chain rule (pre-registered before any diff was read).** A pair whose breaking PR is
  itself the fix of another pair is dropped, so every kept breakage is independent. The
  first mining run had the chain #1916 -> #1919 -> #1920; the rule removed the pair
  (breaking #1919, fix #1920). The list then refilled to 15 with one older pair
  (breaking #1759, fix #1765). The noise draw still excludes both sides of every original
  pair, chains included (`all_pairs`), and was not redrawn.
- **Other mechanical exclusions.** Development PRs, release-please PRs, "Bump ..." and
  "build(deps" titles. A breaking PR keeps only its earliest fix.

## v2 (maintainer decision, 2026-10-19), written before the v2 miner was run

v1 retired before any check ran on it: 0/15 in zone by independent labelling
(`heldout-labels.json`). In 11 pairs the fix only cites the earlier PR (context, review
nit, partial fix) rather than the PR that caused the bug. `heldout.json` and
`heldout-labels.json` stay untouched as the v1 record. The check has not been run on v1.

v2 uses the spec's other branch, `git blame`. The rule, fixed before the run:

- Candidates: merged owner-api PRs whose title starts `fix` (same `gh` listing as v1;
  release and dependency-bump titles excluded as before).
- For each fix PR: the lines its diff deletes or modifies (old side of the hunks, pure
  additions contribute none) in non-test files under `ownima-backend/` with extension
  `.py`, `.json`, `.yaml` or `.yml`. A test file is one under a `tests` directory, named
  `test_*`, or `conftest.py`. They are blamed with `git blame -w --porcelain <base> -- <file>`
  at the fix's base (first parent of its merge sha) in a read-only owner-api checkout,
  never checked out.
- Each blamed commit maps to a PR through the listing's merge commit oids (squash merges);
  a commit that is no PR's merge commit is ignored.
- The breaking PR is the earlier PR blamed for the most of those lines, merged 0-60 days
  before the fix; ties go to the most recent. No blamed PR in the window: no pair.
- Exclusions: the development PRs (`DEV_PRS`) on either side, any owner-api PR in
  `noise.json`, and the chain rule: a pair whose breaking PR is itself the fix of another
  v2 pair is dropped (chains judged over all raw v2 pairs). Up to 15 pairs kept, newest
  fix first.
- Sealing as before: the miner prints counts and PR numbers only, never a diff or a blamed
  line. Output: `heldout-v2.json`, the v1 record shape plus `blamed_lines` (a count) per pair.
