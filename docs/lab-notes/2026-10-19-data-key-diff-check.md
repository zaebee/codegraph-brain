# A diff check that knows data keys: results

Date: 2026-10-19. Pre-registered in `docs/specs/2026-10-18-data-key-layer-design.md`
(Evaluation, Pass thresholds); plan `docs/plans/2026-10-18-data-key-layer.md`, Task 6.
Measured at branch `docs/data-key-layer` 44c21ea, merge-base with main 8b030a0.

## Question and what was built

Does the graph stop an agent from breaking someone else's code when it edits? The spec
picked the most frequent breakage the call graph cannot see: a dependency through a string
key. The round built `cgis diff-check` (MCP tool `cgis_diff_check`). Given a git range, it
lists the subclasses of changed classes, the callers of changed functions, and the other
readers, writers and declarers of the data keys the diff touches. It also lists a read of a
dotted path whose parent is declared in a JSON or YAML file while the path itself is not
(the #2071 shape). Keys come from Python only, through library profiles (opensearch-py,
redis, pydantic) and a JSON/YAML data pack. This is variant A: no data flow across
functions, so a key is scoped to a namespace only where its own site shows one. Most keys
are unscoped and are filtered by specificity alone.

## Thresholds and results

Thresholds are verbatim from the spec.

| # | Threshold | Result | Verdict |
|---|---|---|---|
| 1 | Held-out recall: at least 3 in 4 of the in-zone cases caught; report the overall fraction of all held-out cases too. | 0 in-zone cases in 30 mined pairs (v1: 0/15, v2: 0/15), two independent labellings. No held-out run is recorded. | NOT MEASURED |
| 2 | Noise: median at most 5 listed items per PR on the noise set, and at least half of all listed items judged "worth a look" by an independent agent reviewer with the PR in front of it. | owner-api, 28 of 30 PRs: median listed 10.5; estimated worth share of all listed items about 25%. | FAIL |
| 3 | Generality: no owner-api-specific code; the public repository meets threshold 2. | No owner-api index, field or path in the code the branch adds to `src/` (Task 5 review; comments cite owner-api measurements). rq/rq, 15 PRs: median listed 2; worth 56.4% of shown items; the 28 hidden items were not judged. | PASS on the median; worth share PASS on shown items, not measured on all listed |
| 4 | Speed: the check finishes in under 10 s on an owner-api diff with a fresh graph. | Median 0.21 s over the first five owner-api noise PRs (0.16–0.36 s); max 1.48 s over all 28. | PASS |
| 5 | Ingest cost: a full owner-api ingest with the key layer and the data pack takes at most 1.15× main's time (median of 5 clean interleaved pairs, fresh databases), and the stored graph grows by a reported amount. | 1.076× (main 29.8 s, branch 32.0 s). Graph 75,218,944 → 83,623,936 bytes (+11.2%). | PASS |

**Threshold 2 in detail.** It is judged on *listed* items: shown plus hidden. The shown
count is capped at 5 and cannot fail; the owner-api median shown is 5, on the cap. The
median listed is 10.5 (`results/noise-items.json`). The distribution is bimodal: 9 PRs list
0 or 1 items, 7 list 38 or more, the maximum is 123.

The worth share was not judged on all 684 listed owner-api items. The judge saw all 94
shown items and a seeded sample of 60 of the 590 hidden ones
(`random.Random(2026).sample(range(590), 60)`, `results/noise-judgements.json`):

- shown: 55 of 94 worth a look (58.5%);
- hidden sample: 12 of 60 (20%);
- estimate for all listed: (55 + 0.20 × 590) / 684 = 173 / 684 ≈ 25%.

This is an estimate with sampling error on the hidden part. It would have to be wrong by a
factor of two to reach 50%.

Two owner-api noise PRs (#1381, #1812) errored and are excluded from the medians. The
cause is a product bug: each adds an empty `__init__.py`. git writes no `---`/`+++` lines
for a file with no text hunks, so the hunk reader kept the path from the `diff --git`
header as the old path, and the check then read a file that does not exist at the base.
An earlier draft of this note blamed rename detection; that was wrong. The bug is fixed
(I1 below); the two PRs were not re-run. Counted as 0, the median listed would be about
10.

## The held-out set: recall was not measurable

The spec fixed the selection rule before mining, and an independent agent labelled each
pair without seeing the check's output. A pair is in zone when the breakage travelled
through a data key or an inheritance link. The labellers made explicit that the breaking
PR's own change must have caused the repaired breakage.

**v1, PR references** (`heldout.json`, `heldout-labels.json`): a `fix` PR whose title or
body names a PR merged 0–30 days earlier. 15 pairs, 0 in zone. In 11 pairs the fix cites
the earlier PR for context, a review nit or a partial sweep; it does not repair it. The 4
real breakages were not key-shaped: a dropped tzdata package, a CI runner without network,
a pre-commit config, a business rule. v1 was retired before the check ran on it.

**v2, git blame** (`heldout-v2.json`, `heldout-v2-labels.json`; rule committed in
`SEALING.md` before the run): the breaking PR is the one blamed for most of the lines a fix
changes, within 60 days. 319 fix PRs were blamed; 15 pairs were kept. Again 0 in zone.
Blame points at the wrong PR: 7 movers and renamers, 6 correct changes next to older bugs,
1 fix that caused its own repair. Many pairs rest on 1 to 3 blamed lines.

**Ambiguous cases.** Some pairs held real key bugs that came from *other* PRs. In v1:
`created_at` against documents that carry `created_date` (from #1788), `rider.full_name`
where documents have `rider.name`, a camelCase dict read with snake keys, a numeric status
against a stored enum name. In v2: #1866 → #2061 (span validity), #1893 → #1920 (the
`"5xx"` label string), #1633 → #1839 (an `_id` field path, moved verbatim). All were
labelled out under the rule.

**Conclusion.** In-zone recall is not measurable on owner-api history with automated
selection. A fix rarely repairs the PR it cites, and blame lands on whoever last moved the
line. The known in-zone cases (#2093, #2066, #2075) came from agents' own memory of their
breakages, not from mining. The maintainer recorded recall as not measurable by these
rules (0/30 in zone).

## Development set (development data, not a measurement)

These cases were used while building (`dev_cases.yaml`, `results/dev-task5.json`). Ranks
are zero-based.

- **#2093 after #2088:** caught. `UserAdmin` is listed at rank 1, the second of two
  subclass items.
- **#2071 (breaking PR #2075):** caught at rank 0 as an undeclared read of
  `event_type.keyword`. Before the kind priority it sat at rank 4, a tie among 11 items at
  score 2.0. The priority (removed declaration, undeclared read, subclass, caller,
  key-writer, key-declarer, key-reader) was fixed before any noise or held-out
  measurement.
- **#2066:** missed. At its breaking commit (the PR's first commit), the only non-test
  change to `picture` moves a string between two frozenset literals. That is not a key
  position, so this is an extraction-recall miss.
- **cgis #701:** missed, as expected. The key `invokes` is declared in a string tuple in
  code; registry completeness is deferred by the spec.

## What the noise is made of

From the judge's reasons and labels (`results/noise-judgements.json`):

- **Common-word keys.** `hits.hits`, `total`, `buckets` and `status` come from response
  parsing and appear across the repository. `hits.hits` and `owner_id.keyword` are the two
  most frequent keys among judged items, 10 each.
- **Dotted paths escape the drop rule.** The drop rule for common keys applies to single
  segments. `owner_id.keyword`, `status.keyword` and `currency.keyword` are dotted and pass.
- **Subclasses that override nothing changed.** owner-api: 2 of 9 shown subclass items
  worth a look; rq/rq: 1 of 12. The judge's typical reason: the subclass neither overrides
  nor calls what the PR changed.

By kind (tallied from the same file), owner-api shown items: callers 44 of 57 worth, key
items (reader, writer, declarer, undeclared read) 9 of 28, subclasses 2 of 9. In the hidden
sample, key readers were 1 of 40.

Being "worth a look" is not the same as adding value. A worth item whose site the PR
itself edits is already in front of the reviewer. Worth items outside the PR diff:
owner-api 16% of shown items (15 of 94), rq/rq 30.8% (12 of 39), owner-api hidden sample
6.7% (4 of 60). The 16% is the better measure of what the check adds on owner-api.

## Calibration changed during the round

The maintainer approved two self-parsing band moves; both go in the PR body. The
name-reference band moved 293 → 320 (main 317; +3 from the new keys modules). The
annotation band moved 1095 → 1167 (+17 from the data pack).

Other bands were kept green by changing the code instead. Key logic left `ResolverEngine`
for `resolver/keys.py`. `KEY_ID_PREFIX` went into `core/keys.py`. Generators replaced
appends, avoiding `self.<dict>.<method>` calls. These are Goodhart pressure: bands on
absolute counts push code toward the count, not toward a better shape.

## What the results argue for

These are options, not conclusions.

1. **A stronger specificity signal for dotted and common keys.** For example, the
   repository-wide frequency of a whole path, not only its leaf, and a profile entry for
   response-parsing keys such as `hits.hits`.
2. **Variant B, data flow, to scope keys.** Most keys are unscoped because the namespace
   is set in a helper or after a search. That is the case the spec named for a data-flow
   round.
3. **Subclass items only when the subclass overrides or calls what changed.**
4. **Prospective collection of real agent breakages, run blind.** This is the only route
   to a recall number seen here. History mining produced 0 in-zone cases from 30 pairs.

## Fixed after the measurement

The final branch review found five bugs after every number above was taken. The numbers
were measured before these fixes, and none of them was re-measured.

- **I1.** A diff that adds or deletes a file with no text hunks (an empty or binary file)
  broke the whole check. This was the #1381/#1812 crash. Fixed: `new file mode` and
  `deleted file mode` now clear the missing side.
- **I2.** A user's `diff.noprefix=true` made the hunk reader cut two characters off every
  path. Fixed: git runs with `--src-prefix=a/ --dst-prefix=b/ --no-textconv`.
- **I3.** A missing or wrong prefix silently reported every symbol of the changed files as
  removed, and the MCP tool's default prefix (empty) set that trap. Fixed: the prefix
  defaults to the graph's recorded ingest root; when changed files under it exist and
  none is in the graph, the check fails with a message (CLI exit 2). A `--root` outside
  the repository exits 2.
- **I4.** A plain JSON or YAML document counted as a declaring parent for an undeclared
  read: a ConfigMap's `data:` made every `resp["data"]["id"]` one. Fixed: only a mapping
  declaration (a property type or an index namespace) is a parent.
- **I5.** Dotted paths made only of common keys (`data.id`, `result.type`) were never
  dropped, and the file-count cut applied to single segments only. Fixed for both.

I4 and I5 target noise causes the judge found (common-word keys, dotted paths escaping the
drop rule), so the noise numbers above are probably worse than the shipped check's. The
noise set was not re-measured: it has been seen, and a re-run on it would measure fixes
made with its failures in view. A fresh noise draw is the follow-up.

## Reproduction

- Branch `docs/data-key-layer` at 44c21ea (code at 632d4d1; measurements e53eecb, 44c21ea).
  Main: 8b030a0.
- Sets: owner-api shas in `benchmarks/data_keys/{heldout,heldout-v2,noise}.json`;
  public repository `rq/rq` at `a0048377aceaadef3bea0e71c70b3e326fb78687`. How the sets
  were sealed, and what that does not prove: `benchmarks/data_keys/SEALING.md`.
- Mining: `scripts/data_keys/mine_heldout.py` (v1), `scripts/data_keys/mine_blame.py` (v2),
  `scripts/data_keys/draw_noise.py`.
- Development set: `uv run python scripts/data_keys/evaluate.py` → `results/dev-task5.json`.
  The #2066 breaking commit 2b4dcfcb is reachable only via `pull/2066/head`; re-fetch it if
  missing.
- Noise and speed: `uv run python scripts/data_keys/noise_run.py` →
  `results/noise-items.json`. Each PR's tree at `merge_sha` is ingested fresh, then
  `base_sha..merge_sha` is checked with no cap; "shown" is the first 5.
- Ingest cost: `python scripts/data_keys/ingest_pairs.py --pairs 5 --main-src <main
  checkout>/src`, run from the scratch directory, on the owner-api export `owner-2bd0fb7`
  (`ownima-backend`, source root `app`) → `results/ingest-pairs.txt`. Where the scripts
  look for the corpus and the owner-api checkout: `benchmarks/data_keys/SEALING.md`,
  "Reproducing on another machine".
- One check by hand: `cgis diff-check --db <graph.db> --repo <owner-api> --base <base_sha>
  --head <merge_sha> --prefix ownima-backend --format json`.
- Judging: `results/noise-judgements.json` (sample rule, indices, labels, summary).
  Held-out labels: `heldout-labels.json`, `heldout-v2-labels.json`. Every ruling and
  maintainer decision is in the controller ledger (`.superpowers/sdd/2026-10-18-data-key-layer/progress.md`,
  local and gitignored), under "Task 6"; the decisions also go in the PR body.
