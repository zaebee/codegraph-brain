# Review fingerprint: an identity that survives a commit

Closes #375.

## 1. The problem

`guardian_sha` is the only field in a `ReviewRecord` that says *how* the review
was produced. As an over-approximation it is sound — two behaviourally different
reviewers can never collide, because any behavioural change implies a different
sha. But it splits reviewers that are the same: the sha moves on every commit,
including ones that touch nothing a review sees.

This was measured from both ends.

**From the producing end.** Six distinct `guardian_sha` values produced the 115
review records under `benchmarks/`:

| file | rows |
|---|---|
| `martian-reviews.jsonl` | 64 |
| `martian-p3-run1.jsonl` | 19 |
| `martian-p3-run2.jsonl` | 19 |
| `martian-p3-run3.jsonl` | 7 |
| `martian-repeat-reviews.jsonl` | 6 |

The set is listed here in full because getting it wrong is this document's own
worst mistake: an earlier draft named three of these files, taking the list from
the issue's prose without enumerating the directory. Every number computed from
those three was internally consistent and agreed with the prediction, so nothing
prompted a recount — a figure that is correct about the wrong set reports the
reassuring answer. It surfaced only because the downstream consumer's manifest
said 115 where this said 89, and a provenance digest travelling with its output
made the disagreement visible.

The judged corpora (`martian-judged.jsonl`, `martian-p3-judged-run1.jsonl`,
`martian-repeat-judged.jsonl`) hold `JudgedReview` records keyed on (url, judge)
and take no fingerprint. `benchmarks/guardian/results.jsonl` is excluded too,
and less obviously: 118 of its 150 rows carry both `guardian_sha` and
`findings`, so a two-field test would sweep them in. They are the Phase 1 bench
corpus under the pre-finder/skeptic schema, carrying `model` rather than
`finder_model` and scored fields (`precision`, `recall`, `matched`, `missed`) —
scores, not reviews. Classification therefore requires `url`: present on every
row of all five review files and absent from every row of
`benchmarks/guardian/results.jsonl` and `calibration.jsonl` (measured), and
resting on "a review without a URL is not a review" — a property of what was
reviewed, not of which model-name field a retired schema happened to use. A
narrower predicate is the dangerous direction here: fewer rows classified as
reviews means fewer rows required to carry a fingerprint, so every conjunct
added is a way for a row to escape the requirement (§8).

```
1ecd9629 (#357) -> d0d807ef (#361) -> f9c36f5c (#367) -> 4d1fe6a8 (#373)
                -> 112e4373 (#377) -> aeebde91
```

Diffing those shas over the prompt and context path — `prompts.py`,
`collector.py`, `core.py`, `skeptic.py`, `findings.py`, `axes.py`, `chunked.py`,
`chunker.py` — changes **zero bytes**. Every one of those commits moved the
bench harness (`scripts/guardian_martian.py` +739, `martian.py` +365), the
providers, docs and tests.

**From the consuming end.** hivemark derives `identity_id =
keccak256(canonical_json(genome))` with `guardian_sha` as a genome field. On its
deduplicated 108-review corpus that yields 8 identities where 3 configurations
were run, one of them holding a track record of a single review because a commit
landed mid-run.

## 2. The decision

Add `review_fingerprint`: a digest over the source of every module that can
affect a review, computed from the working tree at review time.

`guardian_sha` **stays**, unchanged, as provenance. The fingerprint is an added
field, not a replacement, so every existing consumer keeps working; adopting the
fingerprint as the identity key is the consumer's decision, not a consequence of
this change.

The governing principle is an asymmetry between the two ways a fingerprint can
be wrong:

- A wrong **merge** attributes one reviewer's findings to another. Downstream
  this is permanent and unrevisable — hivemark's records are onchain, and an
  attestation's meaning cannot be withdrawn, only added to.
- A wrong **split** mints an entity with a small track record next to a
  behaviourally identical one. Visible, inert, and correctable by the consumer
  with a threshold on when it announces an identity.

Every judgement call below therefore resolves toward over-approximation.
Anything that narrows the hashed set is treated as a defect risk; anything that
widens it is treated as an acceptable cost.

## 3. The boundary

### 3.1 The set is derived, not declared

A hand-maintained list of files fails in the unrecoverable direction: a module
joins the review path, nobody updates the list, and the fingerprint stops moving
when behaviour changes.

The set is therefore the **transitive import closure** of the review entry
points, computed by walking `import` statements with `ast`, restricted to the
`cgis` package — plus, for every module reached, its ancestor packages. Python
executes an ancestor package's `__init__.py` on every import of a module
beneath it; that execution is not optional, so a walk that only follows `import`
statements understates what a review can read. `src/cgis/__init__.py` (an
`importlib.metadata.version` lookup) and `src/cgis/guardian/__init__.py` both
run on every review; neither is named by any `import` statement in the closure,
so both would otherwise be invisible to the digest — the silent-merge
direction this whole design exists to avoid.

Seeds:

```
cgis.guardian.core        cgis.guardian.axes      cgis.guardian.runner
cgis.guardian.collector   cgis.guardian.chunked
```

This is not a cosmetic difference from a declared list. Before the provider
scoping of §3.3 narrows it, the closure is 43 modules — 37 reached by `import`
statements plus 6 ancestor-package `__init__.py` files — where a hand-written
list of the guardian package would have been nine, and the eleven it would have
missed all shape what the model is shown:

| Module | What it decides in the prompt |
|---|---|
| `cgis.query.engine`, `cgis.query.render.mermaid`, `cgis.storage.sqlite_store` | the STRUCTURAL IMPACT GRAPHS section |
| `cgis.query.drift.*` (6 modules) | the ARCHITECTURAL DRIFT section, via `collect_drift` |
| `cgis.extractors.registry` | which changed files count as source, hence what enters `full_files` |

Changing the impact-graph depth in `query/engine.py` changes what the finder
reads. A declared list of `guardian/*.py` would not have noticed.

### 3.2 The closure includes the output side, on purpose

Seeding from `runner` pulls in eight further modules, and precisely those are
downstream of the model: `github_poster`, `metrics`, `recording`, `render`, plus
the concrete providers and `runner` itself. They do not change what the model
sees, so edits to them will split an identity that did not behaviourally change.

They stay in. Excluding them requires a hand-maintained exclusion list, and a
wrong exclusion is a silent merge — the unrecoverable direction.

The cost was measured over this repository's 275 commits, counting how often a
commit moves the digest, and decomposing why:

```
commits in the repository                                    275
commits touching the closure (digest moves)                   95
  of which extractors-only, code no review executes           33
  of which output-side-only (the cost of this section)         3
  touching the model-input side                               62
```

So the fingerprint is stable across 180 of 275 commits, and keeping the output
side costs 3 spurious splits — about 1% of history. That is worth paying to have
no exclusion list at all.

Two corrections to how this section previously argued its case. First, the
dominant residual is not the output side but `cgis.extractors`, at 33 commits
(§8). Second, a downstream threshold on when an identity is announced protects
the *chain* — it stops a permanent record being minted for a spurious entity —
but it does not protect the *measurement*, which is what this issue exists to
fix. Three spurious splits still fragment three track records into six. The
consumer absorbs the irreversible half of the cost, not all of it.

### 3.3 Providers are scoped to the ones actually used

A naive `providers/*` glob would make adding a provider nobody runs move
everybody's fingerprint. This is not hypothetical: `providers/ollama.py` was
added at `4d1fe6a8`, between the gemini runs and the mistral runs. Under a glob,
a gemini review after that commit would have split from an identical gemini
review before it.

The walk therefore does not traverse into a concrete provider module unless that
provider produced the review. `providers/base.py` is always included; anything a
selected provider imports is reached through the selected provider.

This is a **prune during the walk, not a filter afterwards**, and the difference
is load-bearing. `runner.py` imports all three providers at module level
(`runner.py:17-23`), so a walk that follows every edge and subtracts later would
still reach whatever an unselected provider imports. An implementer simplifying
this into a post-hoc exclusion re-creates the coupling §3.3 exists to remove.

The active set is the **union** of both roles:

```python
active = {finder_provider} | ({skeptic_provider} if skeptic_provider else set())
```

A skeptic on a different provider from the finder is a supported configuration
and its code genuinely shapes the review, so it belongs in the digest; a skeptic
on the same provider adds nothing; no skeptic prunes both.

The selection is **not inferred from the model name**. `runner.build_provider`
already knows the provider (`"gemini"` / `"mistral"` / `"ollama"`) and currently
discards it, returning only the model. Two provenance fields are added to the
record so the set is stated rather than guessed (§5).

### 3.4 What is deliberately outside

- **The reviewed repository's own files** — its diff, `CONTRIBUTING.md`,
  `docs/ontology/`, its `patterns.yaml`. These are the subject of the review,
  not the reviewer. They vary per PR by construction.
- **Model identity and sampling** — `finder_model`, `skeptic_model` and
  `temperature` are already separate record fields and part of hivemark's
  genome. The fingerprint describes the harness; the model fields describe the
  brain. Keeping them orthogonal is what lets a consumer ask "same prompt,
  different model?" at all.
- **Test files, CI, docs, `scripts/`** — nothing under them is reachable from
  the seeds, which is the whole point.

## 4. The algorithm

```
review_fingerprint = sha256(
    b"cgis-review-fingerprint/v1\0"
  + for path, content in sorted(closure):
        path.encode() + b"\0" + sha256(content).digest()
).hexdigest()[:12]
```

Paths are part of the hashed input. Without that, dropping a module from the set
would be invisible in the digest — the same silent-merge shape one level up.

The version prefix makes a future change to the scheme itself a distinguishable
event rather than an unexplained discontinuity in the corpus.

Truncation to 12 hex characters (48 bits) matches short-sha ergonomics; the
corpus is on the order of hundreds of records and consumers re-hash the whole
genome anyway. Collision probability is `N²/2^49` — around `2e-7` at ten
thousand distinct review-path states, against a realistic N in the dozens.

**The truncation length is part of the scheme.** Changing `[:12]` to any other
width requires bumping the version prefix, because identical code would
otherwise emit a value that looks different with nothing in the record to say
why.

### 4.1 Bytes, not commits

The digest reads file contents from disk, not from git. This is a deliberate
gain over `guardian_sha`, and it recovers a class of data that has already been
lost once: `docs/specs/2026-07-31-finder-bug-class-taxonomy.md` §"Why the raw
rows are not in results.jsonl" records that both arms of an experiment carried
the same `guardian_sha`, because the treatment was an uncommitted working-tree
edit, and the rows had to be discarded as indistinguishable. A byte-level
fingerprint distinguishes them.

### 4.1.1 Line endings are rejected, not normalised

Reading bytes from disk makes the digest sensitive to line endings. With
`core.autocrlf=true`, or a contributor on Windows, the same commit yields CRLF
in the working tree and LF in the git blob — so the **measured** digest (§4.1)
and the **reconstructed** digest (§6.1) would disagree for one commit on one
machine. That is an inconsistency inside this design, not merely a
cross-platform inconvenience.

The obvious repair is to fold `\r\n` to `\n` before hashing. That is rejected
for the reason §8 rejects every other normaliser: it is code that can be wrong,
and its way of being wrong is to merge — a source file whose string literal
legitimately carries CRLF bytes would hash as though it did not.

Instead: the digest **refuses** a file containing `\r\n` and fails the run. A
stray line ending is a misconfigured checkout, and repairing it silently lets
that checkout go on producing subtly different artefacts elsewhere. The same
reject-rather-than-repair reasoning the repository already applies to paths in
#350 and #315, and to model strings in #382.

Prevention sits alongside the guard: a `.gitattributes` with `*.py text eol=lf`.
The repository has no `.gitattributes` today and `core.autocrlf` is unset, and a
scan of the closure finds no CRLF, so this fires on nobody currently — it exists
so that the first machine configured differently fails loudly instead of minting
a second identity for one reviewer.

### 4.2 Computed at the recording boundary

The module is `src/cgis/guardian/review_fingerprint.py`, and it is called from
`scripts/guardian_martian.py` and `scripts/guardian_review.py` — never from a
module inside the closure.

This is structural, not stylistic. If anything in the closure imported it, the
fingerprint module would enter its own hashed set, and editing the hasher would
split identities without changing review behaviour. A test asserts
`cgis.guardian.review_fingerprint` is not in the closure.

The walk takes its file contents through an injected reader
(`Callable[[str], bytes]`) rather than calling `Path.read_bytes` itself. Live
runs pass a disk reader; the backfill passes one backed by `git show <sha>:<path>`.
One traversal, two sources — otherwise the historical closure is computed by a
second implementation, and the two would have to be kept identical by hand for
the digests to mean the same thing.

The repository root is an explicit argument, never the CWD. `review_one` spends
most of its time standing in a checkout of somebody else's repository; the same
hazard is already documented at `scripts/guardian_martian.py:108` for
`guardian_sha`, where a `rev-parse` without an explicit cwd would record a wrong
value that looks entirely right.

## 5. Record changes

On `ReviewRecord` (`src/cgis/guardian/martian.py`):

```python
review_fingerprint: str
review_fingerprint_source: Literal["measured", "reconstructed"]
finder_provider: str
skeptic_provider: str | None = None
```

`review_fingerprint_source` distinguishes a digest taken from the working tree
at review time from one rebuilt from git afterwards. The two do not carry the
same guarantee (§6), and a record must not claim a precision it does not have —
the same reason `temperature` is `None` rather than `0.0` when a run inherited
the provider's default. It is provenance, not identity: a consumer keys on the
fingerprint and never on this field, so it introduces no second keying scheme.

`finder_provider` / `skeptic_provider` exist so §3.3's set is stated by the
producer rather than reconstructed by a reader from a model-name prefix, which
breaks on the first model whose name does not carry its vendor.

On the production path (`metrics.record_review`): the same
`review_fingerprint`. That record carries no `guardian_sha` at all today, so
every live CI review is currently unattributable to any version of the reviewer.
One field and one call site; the call stays at the script boundary per §4.2.

## 6. Backfill

All five corpora (§1) are backfilled by a one-shot
`scripts/backfill_review_fingerprint.py`. For each record it takes that record's
`guardian_sha`, rebuilds the closure **at that sha** via `git show`, and computes
the digest. No review is re-run and no model is called.

The set is enforced rather than remembered. A test globs every `*.jsonl` under
`benchmarks/`, classifies each row as a review record by its fields rather than
its filename, and fails when a review-shaped row carries no fingerprint. A
corpus file added later, by anyone, fails that test until it is backfilled — a
one-time check would have left the next person to notice, which is precisely
what went wrong the first time.

A sha that does not resolve is a hard failure, not a `null`. A mixed corpus —
some records keyed on a fingerprint, some on a sha — would mean two identity
schemes coexisting, under which one reviewer appears as two entities depending
on which run it came from. That is strictly worse than either scheme alone.

Provider attribution for historical records comes from an explicit model →
provider table in the backfill script, which raises on an unknown model rather
than defaulting.

### 6.1 A reconstructed fingerprint is weaker than a measured one

Rebuilding from `git show` reintroduces exactly the blindness §4.1 exists to
escape: it cannot see an uncommitted working-tree edit, so two behaviourally
different historical runs sharing a `guardian_sha` collapse to one digest. That
is the merge direction, and by the citation in §4.1 it has already happened once
in this repository's own experiment history.

Reconstruction has one trap worth recording, because it was nearly walked into.
`git show` reports an absent path as `does not exist in '<sha>'` but reports a
*nonexistent commit* as `exists on disk, but not in '<sha>'` whenever the file
exists in the working tree. A reader that classifies by substring alone reads an
unresolvable sha as a tree in which every path is missing — an empty closure and
a confident digest of nothing, identical for every bad sha, merging rows from
different reviewers. The backfill therefore resolves the commit once, up front,
and refuses; only after that do both messages legitimately mean "absent here".

This is not an argument against backfilling — a reconstructed fingerprint is
still far better than a sha, and a corpus split between records that have the
field and records that do not is worse than either. It is an argument for the
record saying which kind it holds, which is what
`review_fingerprint_source: "reconstructed"` does. Rows written from now on
carry `"measured"`.

The result was predicted before implementation, by running the algorithm across
the five shas, and the prediction was structural: **two distinct digests, split
on the gemini/mistral line, three identities.** The one digest change falls at
`4d1fe6a8`, where `providers/mistral.py`, `providers/ollama.py` and `runner.py`
moved — #380, sampling reaching the provider instead of the chat template
deciding. That is a real behavioural change and it *should* move the
fingerprint.

### 6.2 Measured

The backfill ran. Across all 115 rows:

```
45  1a2884400bd7  gemini-2.5-flash / gemini-3.5-flash          / had_graph=False
25  1a2884400bd7  gemini-2.5-flash / gemini-3.5-flash          / had_graph=True
45  eebfdf98419c  mistral-medium-latest / mistral-medium-latest / had_graph=True
```

Two digests, splitting on the gemini/mistral line, **three identities**. The
fingerprint contributes no split of its own. Every row carries
`review_fingerprint_source: "reconstructed"`.

The mistral row is the one that shows what this is for. Those 45 rows span three
`guardian_sha` values — `4d1fe6a8`, `112e4373` and `aeebde91` — because Phase 3
ran the same configuration three times to measure the noise floor that
retraction R5 exposed. Under `guardian_sha` those are three reviewers with three
fragmentary track records. Under the fingerprint they are one reviewer measured
three times, which is what they always were: the review path did not move
between those commits.

The digest *strings* above have moved twice since this section was first
drafted, for two different reasons, and both belong in one account rather than
a second apology stacked on the first. An earlier draft, written before §4
existed, predicted `600405522794` and `9dd97d78c6bd` from a throwaway probe
that hashed the path followed by raw file bytes — the shipped preimage is the
scheme string, then the path, then a *nested* SHA-256 of the content, so those
could never have matched by construction. The first real backfill then
measured `e1df55f9e2eb` and `8deeeec52996` — and those stopped being
reproducible the same day, when the final review of #375 found `walk_closure`
silently missing every ancestor package's `__init__.py` (37 modules found
where 43 exist; Python executes an ancestor's `__init__.py` on every import,
not only the modules an `import` statement names). Widening the walk moved the
digest a second time, to the values shown above, and the corpus was re-run to
match.

What was predicted and has held through both moves is the structure: two
distinct values, splitting on the gemini/mistral line, three identities. What
did not hold, twice, was the literal string — and the two failures are not the
same kind. A probe predicting the wrong string before the scheme was even
implemented is harmless. A *shipped* `walk_closure` outpacing a corpus already
hashed with its previous version is a silent correctness gap: the values above
would otherwise be exactly what the earlier probe's were, numbers nobody could
reproduce by running the code in front of them. §7 describes the test that now
makes that gap loud instead of silent.

## 7. Test contract

The tests are asymmetric, because the failure modes are.

- **`closure ⊇ pinned inventory`.** The sorted module list is committed. Growth
  passes silently; a module *disappearing* from the closure fails the build and
  demands a conscious update. This is the ratchet pattern the repository already
  uses for drift tolerances.
- **No dynamic imports in the review path.** The AST walk sees `import`
  statements anywhere, including inside functions, so the providers' lazy
  imports are caught. It cannot see `importlib.import_module(name)` with a
  non-literal argument — a hole in the silent-merge direction. A test rejects
  such calls within the closure. It must key on the *bound name*, not on the
  attribute expression: `from importlib import import_module` followed by a bare
  `import_module(...)` evades a detector that only matches
  `importlib.import_module`, as does `__import__` reached through `getattr`.
- **No CRLF in the closure** (§4.1.1), so a misconfigured checkout fails rather
  than mints a second identity.
- **No packaged data files.** `src/cgis` today contains no data files besides
  `py.typed`, and `patterns.yaml` is read from the analysed repository, so it is
  subject input. But an import walk cannot see a data file by construction, so a
  test pins the absence: if a `.yaml`/`.json` ever ships inside the package, it
  fails and forces the question.
- **The hasher is not in its own set** (§4.2).
- **A stated provider must resolve.** §3.3 scopes the closure by
  `finder_provider`, so a wrong or unknown value would compute the digest over
  the wrong provider module and return a confident wrong answer — the merge
  direction. The walk raises on a provider name it cannot map to a module, and a
  test covers the unknown-provider case. At review time the value comes from
  `build_provider` itself rather than from a caller, so the plumbing, not a
  human, is what states it.
- **Positive and negative movement.** Editing `prompts.py` moves the digest;
  editing a file outside the closure does not; ordering is deterministic across
  runs and platforms.
- **The corpus matches what the code computes today.**
  `test_stored_fingerprints_match_what_current_code_computes`
  (`tests/unit/test_backfill_review_fingerprint.py`) groups every review-shaped
  row under `benchmarks/` by `(guardian_sha, active_providers)` — a handful of
  distinct pairs, not one per row — and recomputes each with the *current*
  `compute_fingerprint`/`walk_closure` over `git_reader`, failing by name if any
  stored value disagrees. This is the mechanism that keeps §6.2's measured
  values from going stale silently the way they did once already, when
  `walk_closure` widened from 37 to 43 modules and the corpus was not
  immediately re-run. A reader who wants to check the numbers in §6.2 does not
  have to trust this document — they can run that test.

## 8. Residual risks

- A pure reformat or docstring edit inside the closure splits an identity. This
  is accepted, not overlooked. Normalising the source first — stripping
  comments, or hashing an AST — was considered and rejected: a normaliser is
  code that can be wrong, and every way it can be wrong over-normalises, which
  merges. Raw bytes cannot fail that way.
- A module reached only through a dynamic import escapes the walk. Mitigated by
  the test in §7, which is a lint rather than a proof. Implementation narrowed
  the residue: the detector keys on the bound name, so
  `from importlib import import_module` followed by a bare call is caught. It
  still misses `import importlib as il; il.import_module(...)`, because the
  attribute check names `importlib` literally.
- **Relative imports are resolved, not skipped.** The first implementation
  followed only `ImportFrom` nodes whose module began with `cgis`, so
  `from .base import X` would have been dropped along with everything reachable
  only through it — silently, in the merge direction. The walk now resolves
  `node.level` against the importing module's package, with the module-versus-
  `__init__` asymmetry handled. No relative imports exist in `src/cgis/` today,
  so this closed a latent hole rather than a live one.
- The closure is computed by static analysis, so a plugin-style provider loaded
  by name at runtime would need this design revisited.
- **Dependency versions are not in the digest.** `uv.lock` is committed and
  pins the SDKs (`google-genai`, `mistralai`, `ollama`, `httpx`), so two runs at
  one fingerprint in this repository resolve the same versions — but the lock is
  not hashed, and it has moved 35 times. A lock bump that changes an SDK's
  default sampling or request shaping is a behavioural change the digest will
  not show. Hashing the lock was rejected for now because it inverts the cost: a
  routine bump would split every identity at once. The floors in
  `pyproject.toml` are `>=`, so an install that ignores the lock has no
  guarantee at all.
- **`cgis.extractors` is the dominant residual, at 33 commits** — more than ten
  times the cost of §3.2's output-side decision. It enters the closure through
  `extractors.registry`, which the collector uses only to decide which changed
  files count as source; no extractor code runs during a review, and the graph
  was built by a separate ingest whose commit the `.commit` marker already
  records. Kept in, because dropping it means an exclusion list. Named here
  because if this churn ever becomes uncomfortable, this edge — not the `runner`
  seed — is where to look, and §7's ratchet makes narrowing it a reviewed act.
- **The ratchet permits silent growth.** §7 fails the build when the closure
  shrinks and passes when it grows, which is correct for safety but means a
  newly-reachable churn-prone module joins with no signal. The churn figures in
  §3.2 and above describe today's closure, not a fixed property.
- **A module reached by a mechanism other than an import statement or an
  ancestor package escapes the walk.** §3.1 closed the ancestor-package gap —
  the class a package's `__init__.py` belongs to — but the walk still only
  models two ways Python reaches code: an `import` statement and package
  initialisation. A plugin discovered by entry point, a class registered by a
  metaclass or decorator side effect at import time in a module the walk
  never visits, or any other mechanism that runs review-shaping code without
  a static `import` of it, would move review behaviour without moving the
  digest. Nothing in `src/cgis` uses such a mechanism today, so this is a
  named class of gap, not a live instance of one.
- **`scripts/guardian_bench.py` still writes `model` rather than
  `finder_model`.** Its 150 scored rows in `benchmarks/guardian/results.jsonl`
  therefore have no provider to resolve a fingerprint against and are
  correctly excluded by §6's backfill test (§1, §6). But a future schema
  tidy-up that renames that field to `finder_model` — for consistency with
  `ReviewRecord`, say — would make those rows indistinguishable from genuine
  reviews under a classifier keyed on `finder_model`'s mere presence. This is
  exactly why §1 and §6's `_review_shaped_rows` classifier keys on `url`
  instead: a property of what was reviewed, not of which model-name field a
  schema happens to use.

## 9. Consumer note

hivemark's stated plan is to replace `guardian_version` with the fingerprint in
its genome and bump `genome_schema_version`, which re-mints every identity. That
is its user's call and is not a constraint on the timing here; nothing in this
change requires a consumer to move.

The concrete requests that shaped §3.1 and §3.3 came from that side during
design, along with the 108-review measurement in §1.
