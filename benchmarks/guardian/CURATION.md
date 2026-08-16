# Ground-truth curation notes (Task 10, 2026-06-10)

Provenance for `pr-*.yaml`. Mined from gemini inline threads, Sonar, and
review-fix commits by 6 mining subagents; line coords re-anchored to the
**review head** (parent of the first review-fix commit — the snapshot the
first review actually ran on; the final `refs/pull/N/head` contains the
FIXES, so replaying it would make every fixed finding unfindable).
Spec §3.1 erratum: `head` = review head, not final pull head.

## What `ambiguous` does to the score (decided 2026-08-11, #345)

**An `ambiguous` hit counts as a false positive.** It is recorded apart from
`noise` — `MatchResult.ambiguous_hits`, `BenchScore.ambiguous_hits` — but that
separation is curation diagnostics, not a scoring adjustment. Reported
precision is `TP / (TP + noise + ambiguous_hits)`.

It used to be exempt from the precision denominator. Two findings retired that.

**It stopped the number describing the review.** The exemption was per *file*:
one entry on `drift.py` exempted every prediction in `drift.py`. On pr-142
every candidate landed on a file carrying one entry, the denominator emptied,
and `score()` returned the vacuous `1.0` reserved for "nothing wrong was said".
Two independent LLM judges scored those same reviews at 0.14 and 0.19. Ten of
118 recorded reviews reported that vacuous 1.0, across three of the six PRs
with runs — normal, not exceptional. pr-144 shows the reach: its entries cover
`drift.py` and `triads.py`, the files carrying 3 of its 5 ground-truth
findings.

**And the exemption was never argued for.** The rule below routes style nits
here to keep them from depressing **recall** — but omitting them from
`findings` already does that, since recall divides by `len(findings)`. Removing
them from the precision denominator as well came along with the mechanism, and
it points the wrong way: guardian's precision rules forbid style nits, so
emitting one *is* a precision failure, and the exemption hid exactly the
failure those rules exist to catch.

Measured over the same 118 reviews against two judges (#342 Phase 1), the new
definition is also the one that tracks an independent scorer:

| | old (exempt) | new (strict) |
|---|---|---|
| Spearman ρ vs judge, all reviews | +0.72 / +0.71 | **+0.92 / +0.92** |
| ρ vs judge, non-empty reviews | +0.42 / +0.35 | **+0.76 / +0.72** |
| mean \|difference from judge\| | 0.25 / 0.26 | **0.10 / 0.10** |

What this costs, stated plainly: a genuinely debatable suggestion — pr-144's
three declined clip proposals, which gemini raised and then agreed to drop —
now counts against precision. That is the price of a number an external scorer
can be compared to, and `ambiguous_hits` keeps the fact visible.

`benchmarks/guardian/results.jsonl` is **not** rescored. Its `precision` was
correct under the policy in force when each row was written, and every row
carries `matched`, `noise` and `ambiguous_hits`, so either definition can be
re-derived from it. (Contrast `calibration.jsonl`, which *was* rewritten — that
was a scorer bug, wrong under its own stated algorithm, not a policy change.)

## Repeated rows are samples, not corrections (2026-08-16, #390)

**Do not deduplicate any corpus in this repository by "one row per subject,
latest wins."** Every repeat here was paid for on purpose, and collapsing to the
newest draw silently discards the experiment it belongs to.

Written down because a downstream consumer adopted exactly that rule — reasoning
that a re-run is a correction, so counting both would let a reviewer improve its
record by re-running — and nothing in this repository contradicted it. The
reasoning is sound for a corpus of corrections. It is wrong for these.

### `benchmarks/martian-*.jsonl` — 115 review rows, 83 distinct subjects

Keyed by subject and genome (`url`, `head_sha`, `review_fingerprint`,
`finder_model`, `had_graph`), 32 of 115 rows are repeats. Every one of them
comes from a registered sampling arm:

| repeat group | count | source |
|---|---|---|
| `p3-run1` + `p3-run2` | 12 | Phase 3 union arm |
| `p3-run1` + `p3-run2` + `p3-run3` | 7 | Phase 3 union arm |
| `reviews` + `repeat-reviews` | 6 | R5 repeat probe |

Phase 3 registered three runs at `temperature = 0.7` **because the runs must
differ** — see the spec, "Configuration under test": at temperature 0 "the three
runs collapse toward one and the union arm becomes identically equal to a single
run." Gate G8 is defined as `F₂(union) > F₂(mean of the 3 runs)`; a mean over
three draws is not computable from one of them.

The variance is not theoretical. **21 of the 25 repeat groups disagree on how
many findings the review produced**, with spreads including 8→14, 15→24, 26→38
and one 0→25 — the same reviewer, the same commit, the same configuration. Keep
one draw and the number you publish is a coin flip over that range.

### `benchmarks/guardian/results.jsonl` — 118 scored rows, 53 distinct

Repeats here carry an explicit `run` index (0/1/2) for the same reason. A row is
identified by `pr` + `model` + `guardian_sha` + `run`, and dropping `run` averages
away the per-PR sampling noise that R5 was written to measure.

### `benchmarks/guardian/calibration.jsonl` — 236 rows, 118 subjects

Exactly two rows per recorded review, one per judge (`gemini-2.5-flash` and
`mistral-medium-latest`). The pair *is* the measurement — inter-judge agreement
is the G3 statistic — so `row_key` alone is not a unique key here; `row_key` +
`judge_model` is.

### If you do need one row per reviewer

Aggregate over the draws (mean, or union where the arm defines one); do not
select among them. Selecting by recency is the one choice guaranteed to be
uncorrelated with quality, and it deletes 28% of this corpus.

**Aggregate over the subject's *draws*, which is not the same set as its rows.**
Say what a draw is rather than which rows to exclude: an exclusion list is
open-ended and the next entry arrives after it has already been believed, while
a positive definition is closed.

**A draw is a review that completed and whose output was parsed** — a row with
`parse_failed: false`. That is the whole test; `error` is `None` on all 115 rows,
because a run that failed outright never reached a record.

**A `parse_failed` row is not a draw.** Its structured output could not be read,
so it carries zero findings, and averaging it in charges the model for a harness
failure. This is exactly the distinction #374 established ("a truncated review is
not a review that found nothing"); before it, `parse_failed` was recorded and
read by nothing.

**A row with zero findings and `parse_failed: false` *is* a draw.** Three exist
(PRs 6, 77754 and 107534, all gemini), and each spent real tokens — 106, 117 and
124 completion tokens. The reviewer ran and said nothing, which is evidence about
the reviewer. Same output shape as the parse failure, opposite meaning; keeping
them and dropping the parse failure is the one combination that is right about
both. The `arm` field exists for the mirror of this hazard: a failure and a
controlled removal must not look alike in the record.

There is exactly **one** such row in the review corpora, and it is
`martian-p3-run1.jsonl` for PR 11059 — the same row #374 was written about. One
row out of 115 sounds ignorable and is not: it is one of three draws for its
subject, so averaging it in pulls that subject's weight down and moves
mistral/graph's confirmed rate on `critical` from 86.6% to 86.9%. That is how the
gap was found — a downstream consumer computed 86.6%, this repository computed
86.9%, and the disagreement had exactly one cause.

The same applies to the `error` rows in `results.jsonl`, for the same reason and
by the same rule: they are already excluded there because `is_scored` requires
`matched` and `precision`, which a failed run never gets.

## Curation policy

- Style/idiom-only findings → `ambiguous` (guardian's PRECISION RULES forbid
  style nits; penalizing recall for them would measure the wrong thing —
  keeping them out of GT `findings` is what achieves that, see above).
- Review-dialogue resolutions (open questions answered during review) →
  `ambiguous` (not defects present in the diff).
- Declined-with-reason suggestions → `ambiguous` (per spec §3.1).
- Refuted claims → omitted from GT entirely, NOT `ambiguous`. A finding
  disproved by execution (the code cannot behave as claimed) is an error, not a
  judgement call. The original reason was that `ambiguous` exempted hits per
  file, so filing errors there made the suite structurally unable to observe a
  precision failure (#279); since #345 removed the exemption the rule no longer
  protects the score, but it still keeps `ambiguous_hits` meaning what it says —
  "landed on ground we already marked debatable" — rather than becoming a
  second name for noise.
- `lines` required wherever the source provides line provenance (quality
  review I3: rangeless entries are gameable by file-level predictions).
- Sonar quality-gate findings (cognitive complexity, float equality) stay in
  GT: machine-verified, objectively fixed. Pure idiom preferences (Counter,
  literal dedup) → `ambiguous` — applied consistently across all PRs.
- Same-site duplicates merged into one entry (one defect site = one GT entry);
  distinct defects sharing a range stay separate (greedy matcher handles it).

## PR 122 — feat(ui): IslandLayoutEngine (PR 2/4)

- merge-base (replay base): 33c31455f9f3914b5542dfcd94c42a10b8482b2d
- final pull head: 0d888c9ba482b7d4936454381afa09d0416ed80f
- fix commits chronological: 9fec0454 (r1) → acc5e1ba (r2) → 565ea84 (r3) → 0d888c9 (r4); 061b755c = guardian LAYOUT_DIRECTION fix
- **review_head = 27ccbcc4a62358298b02cf97860dd1bf046807dd** (parent of 9fec0454);
  all coords below re-anchored and verified present at this snapshot.

Findings (all gemini unless noted; coords in pr-122.yaml):
| id | file | sev | cat | summary |
|---|---|---|---|---|
| dagre-center-topleft | ui/src/layout/IslandLayoutEngine.ts | critical | contract | dagre returns center coords, RF expects top-left; direct assignment shifts all nodes +w/2,+h/2 |
| container-zorder | same | major | logic | islandContainer pushed after children → renders on top, obscures them |
| sort-instability | same | major | logic | area-sort makes islands swap on expand/collapse |
| dangling-edges | same | major | logic | edges to absent nodes passed to React Flow |
| dagre-pos-null | same | major | contract | g.node() undefined unguarded → TypeError |
| layout-direction-constant | same | major | contract | rankdir 'TB' hardcoded vs LAYOUT_DIRECTION='LR' (source: fix-commit 061b755c, guardian-caught) |
| file-container-regression | same | major | logic | fileContainer wrapping from old layout.ts omitted (file-level) |
| run-async-unnecessary | same | minor | contract | async run() though dagre is sync (G-9/10/11) |
| namespace-dedup | same | minor | logic | namespace extraction ×3 in partition() |
| bbox-find-perf | same | minor | logic | O(N) bboxes.find() per iteration |
| redundant-casts | same (+IslandContainerNode.tsx) | minor | types | `as object` / `as Record` redundant casts |

Dropped: G-12 (test mock returns Promise — only valid AFTER sync refactor).
Ambiguous: none.

## PR 140 — feat(#139): fingerprint & drift engine

- merge-base: d4a89bd3d46a411b2cdf30655793d2586ed411d8
- final pull head: 32e613b0157b533e639bdd506eba8898a82cfc7a
- fix commits chronological: 67cdfe5 (w1) → 8b8e567 (w2) → fc609fa7 (w3) → 2793e557 (sonar float) → bde6c304 (sonar CC) → 32e613b (dedup)
- **review_head = d6b7eb6c84e893684721df652d0938e6621e3da6** (parent of 67cdfe5);
  all wave-2/3 findings verified present at this snapshot.
- Merged: `pattern-template-none-or-dict` absorbed into `keyerror-unknown-pattern`
  (same site drift.py [83,88] — one defect site, one entry).
- Split: `float-equality-tests` → two entries (test_drift.py [96,101],
  test_fingerprint.py [41,44]) — matcher entries are single-file.
- Dropped: `dfs-code-duplication` — the duplicated DFS was introduced BY the
  wave-1 fix; review_head still has BFS.

| id | file | sev | cat | src | summary |
|---|---|---|---|---|---|
| dag-bfs-wrong-visited | src/cgis/query/fingerprint.py | critical | logic | gemini | BFS global visited underestimates DAG longest path (2 sites) |
| zero-division-eff-total | src/cgis/query/drift.py | major | contract | gemini | ZeroDivisionError when total_weight==0 |
| keyerror-unknown-pattern | drift.py | major | contract | gemini | raw KeyError for unknown expected_pattern |
| drift-no-try-except | src/cgis/cli.py | major | contract | gemini | drift cmd leaks raw tracebacks |
| yaml-safe-load-none-gen-ideal | scripts/gen_ideal_graph.py | major | contract | gemini | safe_load None → .get TypeError |
| missing-drift-output-format | src/cgis/cli.py | major | contract | gemini | wrong enum: --format json rejected |
| count-routers-on-squared | fingerprint.py | major | logic | gemini | O(N×E) nested scan in _count_routers |
| fingerprint-extractor-no-cache | fingerprint.py | major | logic | gemini | refetch+enrich per domain, no cache |
| weights-keyerror-custom-yaml | drift.py | major | contract | gemini | _weights[name] KeyError on custom yaml |
| nondeterministic-set-traversal | fingerprint.py | major | logic | gemini | set iteration unsorted → irreproducible metrics |
| yaml-none-dict-drift-init | drift.py | major | contract | gemini | DriftScorer.__init__ safe_load no isinstance guard |
| pattern-template-none-or-dict | drift.py | major | contract | gemini | patterns.get result not isinstance-guarded |
| float-equality-tests | tests/unit/test_drift.py, test_fingerprint.py | minor | tests | sonar | bare == on floats |
| cognitive-complexity-score | drift.py | minor | logic | sonar | score() CC 18 |
| dfs-code-duplication | fingerprint.py | minor | logic | sonar | duplicate DFS blocks 13.6% |

Ambiguous: none.

## PR 141 — feat(query): intra-domain fan metrics

- merge-base: 74fe052a70c29cce23aa8c64a4a8af34986df2b4
- final pull head: aeb0da01320d1dd73a1042d6f07055a7133af993
- fix commit: da99dba; **review_head = 176569e6a9a11044eb7844856ded3dd7c390cd47**

ALL findings → ambiguous (style/maintainability only; guardian precision rules forbid):
- counter-fan-metrics (src/cgis/query/fingerprint.py, gemini): manual dict.get(k,0) → Counter idiom
- db-literal-duplication (tests/self_parsing/conftest.py, sonar): "graph.db" ×3
- single-pass-fan (fingerprint.py, gemini DECLINED): O(2E)→O(E), declined for readability

GT findings: NONE → recall vacuously 1.0; PR measures noise on a clean-ish PR.

## PR 142 — docs(spec): pattern alphabet (docs-only)

- merge-base: 74fe052a70c29cce23aa8c64a4a8af34986df2b4
- final pull head: 6294c872faadc9d5b09a02375d746d87f06702c3
- fix commits: cc327f40 (cosine→TV) → 6294c872 (open questions);
  **review_head = b545a883d8747b3e65581ccb3d73d9c74295721e** (parent of cc327f40)
- Coords verified: §3.3 block [200,221], OQ2 block [346,355].

GT:
- cosine-component-masking (docs/specs/2026-06-09-pattern-alphabet-motif-basis-design.md, major, logic, gemini): cosine distance masks small components, no per-triad decomposition; fixed → weighted TV (§3.3 block)
- cosine-oq2 (same file, major, logic, gemini): second occurrence in Open Question 2 block

Ambiguous: Q1 (discount floor), Q3 (quotient gate burn-in), Q4 (single yaml layout) — review-dialogue resolutions, not diff defects.

## PR 143 — feat: unified pattern alphabet (Part A)

- merge-base: aac47113d0fdb5ef2ffa9aed6c0c6d50aac3d792
- final pull head: 5e935d653dec2c6e1e789b56b7181b55670fcdff
- **TWO-WAVE REVIEW.** Chronology: aa27993 → bede5c9 → 7f9a05b → 2394bd5 →
  d5c7e89 → 8b19262 → 9a5a29ac (fix r2a) → 5e935d653 (fix r2b = final head).
  At the first-round head (39990e5, parent of 86a3784) four of the six defects
  did not exist yet — they were introduced by later feature commits (b6fd830,
  7f9a05b) and caught by the SECOND review round.
- **review_head = 8b1926273f1f14640c34c8658c3f0737b4209e34** (parent of
  9a5a29ac): all six defects verified present there — _load_params [85,96],
  _merge_params [131,144], unclipped discount line 183 → [180,186], hardcoded
  "cgis." prefix test_drift.py [25,30], monolithic score() [160,228],
  nested-loop tests test_patterns_yaml.py [70,90] (CC fixes landed only in
  9a5a29ac, so they too are still present at 8b19262).

| id | file | sev | cat | src | summary |
|---|---|---|---|---|---|
| params-mapping-domain | src/cgis/query/drift.py | major | contract | gemini | domain params list/scalar → opaque AttributeError on .items() |
| params-mapping-template | drift.py | major | contract | gemini | same for template params |
| confidence-discount-clip | drift.py | major | logic | gemini | 1-unresolved_ratio unclipped → negative weights |
| selected-domains-profile | tests/self_parsing/test_drift.py | major | logic | gemini | hardcoded "cgis." prefix instead of profile field |
| cognitive-complexity-score | drift.py | minor | logic | sonar | score() CC 17 |
| cognitive-complexity-test-patterns-yaml | tests/unit/test_patterns_yaml.py | minor | tests | sonar | two tests CC 17/16 |

Ambiguous: none.

## PR 144 — feat: motif-basis fingerprint v2 (Part B)

- merge-base: d9ca22a64aa83c0764710acd7a40c6284bb773ed
- final pull head: 3f25b0a7aea5d9c1ea15199045fd5e00c6186f3b
- fix commits: 4bad004 (r1: guards+CC+float-eq) → 3f25b0a (r2: Counter);
  **review_head = 30d616681938d3a7effcaa09e395f05de3e12d87** (parent of 4bad004;
  polish commits 530c864/a072b2c/82129f2/2b743b9/566b202 confirmed earlier in
  the log, so they are included in this snapshot).
- Moved to ambiguous: `quotient-counter-comprehension` — Counter-idiom
  preference, same class as PR 141's counter-fan-metrics (style policy applied
  consistently). Also ambiguous: test_quotient.py bare float == (integer-valued).

| id | file | sev | cat | src | summary |
|---|---|---|---|---|---|
| yaml-mapping-guard-ideal-layer | src/cgis/query/drift.py | major | types | gemini | _ideal_layer no mapping guard |
| yaml-mapping-guard-layers-for | drift.py | major | types | gemini | layers_for no guard/sum-to-1 |
| yaml-mapping-guard-triad-weights | drift.py | major | types | gemini | triad_weights_for no guard/negatives |
| triad-census-cognitive-complexity | src/cgis/query/triads.py | minor | logic | sonar | triad_census CC ~21 |
| float-equality-in-tests | tests/unit/test_triads.py (+test_quotient, test_drift) | minor | tests | sonar | bare == floats |
| quotient-counter-comprehension | src/cgis/query/quotient.py | minor | logic | gemini | manual accumulator → Counter (fix in r2; verify present at review_head) |

Ambiguous (3 declined clips, gemini agreed with rationale):
- src/cgis/query/triads.py: clip tv_distance to [0,1] — violates tv==sum(contribs) invariant
- src/cgis/query/drift.py: clip drift_sum (v1) — would mask config bugs
- src/cgis/query/drift.py: clip layered drift_score — convex combo already non-negative

## PR 278 — fix(guardian): request timeout + bounded retry

- merge-base: 2e768cef585f10481970697657b0a9b40a411f47
- **review_head = 6ee175e553c784a363c190e00021be5d365fb8dd** (later commits on
  the branch postdate the review)

Guardian returned 10 findings, all skeptic-confirmed, all refuted (#279):

- `base.py:102` remove the "unreachable" `raise AssertionError` — **would break
  the build**; deleting it fails mypy with `Missing return statement`
- `base.py:7` `RETRYABLE_EXCEPTIONS` lacks type annotations — it is annotated
  `tuple[type[Exception], ...]`, and mypy strict passes
- `base.py:84` the tuple may miss httpx subclasses — every relevant transport
  exception is covered; the finding names ones that ARE caught
- `base.py:69` `_sleep` may receive a negative value — no caller can produce one
- `mistral.py:26`, `gemini.py:25` integer overflow in `timeout * 1000` — Python
  ints are arbitrary precision (counted twice, same non-issue)
- `runner.py:217` 2-dp rounding loses sub-ms precision — 10 ms on a 65 s value
- `metrics.py:68` no runtime validation of `duration_s` — no field in that
  module has any; it is a plain dict → JSON
- two test findings on brittle call counts — one's premise is false, the test
  already derives its expectation from `MAX_ATTEMPTS`

### PR 278 — one GT finding added 2026-07-31 (#258)

Originally filed with `findings: []` as a pure precision entry. It now carries one:

- `gemini-client-never-closed` (`src/cgis/guardian/providers/gemini.py`, lines
  41-58, minor, contract, source `fix-commit`)

The diff **modified** the `client = genai.Client(...)` construction line and left
both httpx pools unclosed. Guardian reviewed that hunk and returned ten findings,
none about resource cleanup — Resource Management is a class absent from the
finder's focus areas (see `docs/specs/2026-07-31-finder-bug-class-taxonomy.md`).
Fixed later by human-directed review as #283 (`b2ae97b`), which is the
`fix-commit` provenance.

Precision is unaffected: `matched` stays 0, so the entry still scores `0/10`. The
addition supplies a recall signal — a real **0/1** where the empty ground truth
gave a vacuous 1.0.

## pr-313 (added 2026-08-01, #315) — first security fixture

`security(mcp): validate db_path before cgis_ingest creates a database`.

Provenance is stronger than the rest of the corpus: neither entry was mined from
review dialogue. Both were **reproduced by execution** on the pre-fix code and
re-run after the fix.

- `dangling-symlink-write-primitive` (110-121, major, **security**, `fix-commit`
  `8bcda77`). A `.db` symlink whose target does not exist passes every check —
  `is_file()` is False for a dangling link, so the magic-byte test never runs —
  and SQLite then follows it and creates the target. Demonstrated by writing a
  SQLite header into a non-existent `.ssh/authorized_keys`; refused afterwards.
- `oserror-escapes-the-tool` (114-124, minor, `logic`, `fix-commit` `72c34eb`).
  The guard runs before `cgis_ingest`'s own try/except and `Path.is_dir()`,
  `.stat()`, `.open()` propagate `OSError`, so a `PermissionError` on the parent
  crashes the MCP tool. Demonstrated with a `chmod 000` parent.

### Why `head` is `2d79671` and not the final pull head

Standard policy, restated because this PR has four commits: `2d79671` is the
snapshot the first review ran on and the only one where **both** findings are
present. The later three are the fixes.

### Overlapping ranges are deliberate

The two ranges overlap on 114-121. A reviewer can legitimately anchor the
symlink bypass either at the unresolved `Path(db_path)` (110) or at the
`is_file()` test where it slips through (121), and the OSError leak sits across
the whole probe block. Matching is greedy per entry, so one prediction still
consumes only one entry.

### Scoring the historical runs would be unfair

Three reviews ran over this PR, but on **different commits** — guardian started
at 23:38:58, after `72c34eb` had already fixed the OSError defect, so it never
had a shot at that entry:

| reviewer | commit reviewed | findings | GT available | GT hit |
|---|---|---|---|---|
| gemini (1st) | `2d79671` | 1 | 2 | 1 (OSError) |
| guardian | `72c34eb` | 5 | 1 | 0 |
| gemini (2nd) | `c90bbf8` | 2 | 1 | 1 (symlink) |

A replay at `head` gives every reviewer both findings, which is the point of
pinning the review head. The table above must not be read as guardian missing
two.

### A refuted claim that is deliberately absent

Guardian's `multi-dot suffix` finding on this PR was a false positive — it
asserted `graph.tar.db` should be rejected, when ending in `.db` is exactly the
stated policy, and its suggested `path.suffixes` fix would have broken a
legitimate `my.project.db`. Per the policy above, refuted claims are omitted
from ground truth entirely rather than filed as `ambiguous`, so that precision
failures stay observable. Recorded here because the cross-family skeptic
**confirmed** it (`Verified by gemini-2.5-flash`) — the cleanest example yet of
a skeptic passing a systematic reasoning error (#246).

### `category: security`

New value, added with this fixture (`findings.py`). The finder prompt does not
yet offer it — teaching the finder to emit it is #258 and needs bench
validation. Scoring is unaffected: `_entry_accepts` compares file and line
range, never category.
