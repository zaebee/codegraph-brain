# Review fingerprint: an identity that survives a commit

Closes #375.

## 1. The problem

`guardian_sha` is the only field in a `ReviewRecord` that says *how* the review
was produced. As an over-approximation it is sound — two behaviourally different
reviewers can never collide, because any behavioural change implies a different
sha. But it splits reviewers that are the same: the sha moves on every commit,
including ones that touch nothing a review sees.

This was measured from both ends.

**From the producing end.** Five distinct `guardian_sha` values produced the 83
reviews in `benchmarks/martian-reviews.jsonl`, `martian-p3-run1.jsonl` and
`martian-repeat-reviews.jsonl`:

```
1ecd9629 (#357) -> d0d807ef (#361) -> f9c36f5c (#367) -> 4d1fe6a8 (#373) -> 112e4373 (#377)
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
`cgis` package.

Seeds:

```
cgis.guardian.core        cgis.guardian.axes      cgis.guardian.runner
cgis.guardian.collector   cgis.guardian.chunked
```

This is not a cosmetic difference from a declared list. Before the provider
scoping of §3.3 narrows it, the closure is 37 modules where a hand-written list
of the guardian package would have been nine, and the eleven it would have
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
wrong exclusion is a silent merge — the unrecoverable direction. The cost of
keeping them is one spurious split per unrelated edit, which the consumer can
absorb.

### 3.3 Providers are scoped to the ones actually used

A naive `providers/*` glob would make adding a provider nobody runs move
everybody's fingerprint. This is not hypothetical: `providers/ollama.py` was
added at `4d1fe6a8`, between the gemini runs and the mistral runs. Under a glob,
a gemini review after that commit would have split from an identical gemini
review before it.

The walk therefore does not traverse into a concrete provider module unless that
provider produced the review. `providers/base.py` is always included; anything a
selected provider imports is reached through the selected provider.

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
genome anyway.

### 4.1 Bytes, not commits

The digest reads file contents from disk, not from git. This is a deliberate
gain over `guardian_sha`, and it recovers a class of data that has already been
lost once: `docs/specs/2026-07-31-finder-bug-class-taxonomy.md` §"Why the raw
rows are not in results.jsonl" records that both arms of an experiment carried
the same `guardian_sha`, because the treatment was an uncommitted working-tree
edit, and the rows had to be discarded as indistinguishable. A byte-level
fingerprint distinguishes them.

### 4.2 Computed at the recording boundary

The module is `src/cgis/guardian/review_fingerprint.py`, and it is called from
`scripts/guardian_martian.py` and `scripts/guardian_review.py` — never from a
module inside the closure.

This is structural, not stylistic. If anything in the closure imported it, the
fingerprint module would enter its own hashed set, and editing the hasher would
split identities without changing review behaviour. A test asserts
`cgis.guardian.review_fingerprint` is not in the closure.

The repository root is an explicit argument, never the CWD. `review_one` spends
most of its time standing in a checkout of somebody else's repository; the same
hazard is already documented at `scripts/guardian_martian.py:108` for
`guardian_sha`, where a `rev-parse` without an explicit cwd would record a wrong
value that looks entirely right.

## 5. Record changes

On `ReviewRecord` (`src/cgis/guardian/martian.py`):

```python
review_fingerprint: str
finder_provider: str
skeptic_provider: str | None = None
```

`finder_provider` / `skeptic_provider` exist so §3.3's set is stated by the
producer rather than reconstructed by a reader from a model-name prefix, which
breaks on the first model whose name does not carry its vendor.

On the production path (`metrics.record_review`): the same
`review_fingerprint`. That record carries no `guardian_sha` at all today, so
every live CI review is currently unattributable to any version of the reviewer.
One field and one call site; the call stays at the script boundary per §4.2.

## 6. Backfill

All three corpora are backfilled by a one-shot
`scripts/backfill_review_fingerprint.py`. For each record it takes that record's
`guardian_sha`, rebuilds the closure **at that sha** via `git show`, and computes
the digest. No review is re-run and no model is called.

A sha that does not resolve is a hard failure, not a `null`. A mixed corpus —
some records keyed on a fingerprint, some on a sha — would mean two identity
schemes coexisting, under which one reviewer appears as two entities depending
on which run it came from. That is strictly worse than either scheme alone.

Provider attribution for historical records comes from an explicit model →
provider table in the backfill script, which raises on an unknown model rather
than defaulting.

The result is known in advance, from running the algorithm across the five shas:

```
1ecd9629  fp=600405522794
d0d807ef  fp=600405522794   changed: none
f9c36f5c  fp=600405522794   changed: none
4d1fe6a8  fp=9dd97d78c6bd   changed: providers/mistral.py, providers/ollama.py, runner.py
112e4373  fp=9dd97d78c6bd   changed: none
```

Two digests over 83 reviews, and the boundary falls on the gemini/mistral line,
which is already a configuration boundary. Identities become: gemini·graph = 1,
gemini·diff-only = 1, mistral·graph = 1. Seven identities become three, and the
fingerprint contributes no split of its own.

The one digest change is #380 — sampling reaching the provider instead of the
chat template deciding. That is a real behavioural change and it *should* move
the fingerprint.

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
  such calls within the closure.
- **No packaged data files.** `src/cgis` today contains no data files besides
  `py.typed`, and `patterns.yaml` is read from the analysed repository, so it is
  subject input. But an import walk cannot see a data file by construction, so a
  test pins the absence: if a `.yaml`/`.json` ever ships inside the package, it
  fails and forces the question.
- **The hasher is not in its own set** (§4.2).
- **Positive and negative movement.** Editing `prompts.py` moves the digest;
  editing a file outside the closure does not; ordering is deterministic across
  runs and platforms.

## 8. Residual risks

- A pure reformat or docstring edit inside the closure splits an identity. This
  is accepted, not overlooked. Normalising the source first — stripping
  comments, or hashing an AST — was considered and rejected: a normaliser is
  code that can be wrong, and every way it can be wrong over-normalises, which
  merges. Raw bytes cannot fail that way.
- A module reached only through a dynamic import escapes the walk. Mitigated by
  the test in §7, which is a lint rather than a proof.
- The closure is computed by static analysis, so a plugin-style provider loaded
  by name at runtime would need this design revisited.

## 9. Consumer note

hivemark's stated plan is to replace `guardian_version` with the fingerprint in
its genome and bump `genome_schema_version`, which re-mints every identity. That
is its user's call and is not a constraint on the timing here; nothing in this
change requires a consumer to move.

The concrete requests that shaped §3.1 and §3.3 came from that side during
design, along with the 108-review measurement in §1.
