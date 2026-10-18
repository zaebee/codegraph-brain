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
