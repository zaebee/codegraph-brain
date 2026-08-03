# Auto-evolution PoC — unit of selection, fitness, and the first mutation gate (#335)

**Status:** research input + **first live results** (2026-08-01)
**Issue:** #335
**Subject repo:** `~/projects/aura`

> **Update.** Gate 0's instrumentation was built and shipped to aura
> (#243–#247) and has run twelve real cycles. **§9 records what the data
> showed, including two findings that falsify parts of this document.** Where
> §9 contradicts an earlier section, §9 wins — the earlier text is left standing
> rather than edited into agreement, so the falsification stays visible.

## Verdict up front

The two blocking definitions **can** be written, but writing them changes the
framing in #335:

1. **cgis is not the metabolism. cgis is the membrane.** The metabolic
   quantity (tokens, USD) is aura's, not cgis's. cgis's defensible role is a
   deterministic constraint checker on mutations — the thing that says *no*
   before a patch lands. That is a smaller and much more testable claim than
   "metabolic organelle", and it is the one the evidence supports.
2. **Fitness is definable but not currently measurable.** aura's cost
   accounting exists as a *field* (`token_usage` in `dna.proto:274`,
   `metabolism.proto:340`, populated in both bees' Transformers).
   `HIVE_STATE.md` records `Governance Cost (Last): 0 tokens / 0.00s`.
   bee-keeper's Generator does append `token_usage` per audit cycle into the
   `## Audit Log` section, but truncated to `old_log[:5000]` characters and
   unstructured — so **no usable time series exists**, which is the accurate
   claim; "zero cycles recorded" would be too strong for bee-keeper. For
   **bee-evolver** — the bee §6 actually experiments on — it does hold cleanly:
   its Connector writes `token_usage` into a Telegram message
   (`connector/__init__.py:269`) and persists nothing. No USD anywhere. Metric
   inventory is one counter *factory*
   (`core/src/hive/proteins/telemetry/engine.py:22-25`) registering three
   counters — `negotiation_total`, `negotiation_accepted_total`,
   `heartbeat_total` — **none cost-related**. So the PoC is one instrumentation
   step away from being able to start, and that step costs no LLM spend.
3. **The missing organ is `evaluate`, not `apply`.** This contradicts the
   framing in #335 Q5 and is the single most useful finding here — see §5.

## Measurements taken this session

All against `~/projects/aura` at HEAD, ingested with the current cgis.

| measurement | result |
|---|---|
| `cgis ingest ~/projects/aura` | 3383 nodes, 12840 raw edges, 11069 resolved |
| `cgis validate` | internal 36.1%, unresolved 17.8% (below the 30% threshold — **passes**) |
| `cgis init-ontology` | 12 domains proposed; `core`, `agents`, `packages`, `api-gateway` all land on `layered_dag` |
| `cgis drift` against that proposal | all domains within tolerance; `core` 0.28, `agents` 0.27, `tools` 0.27 warning-band |
| ATCG-M nucleotides visible as FQN prefixes | A 57 nodes / T 58 / C 79 / G 30 / M-ish 99 |
| I/O calls (`httpx.*`, `subprocess.*`) outside the Connector, under `agents/` | **16 call expressions in 11 functions across 4 files** — of which the defensible heresy subset is **3 functions / 6 calls** (see below) |
| `cgis fractal` | IMPORTS `scale_invariant` +0.084 (R² 0.19); CALLS `hierarchical` +0.101 (R² 0.56) — see §8 |

**Correction to an earlier draft of this row, and it matters.** The first
version reported "6 call sites". That number came from a query matching
`target='httpx'` / `target='subprocess'` — which are **IMPORTS** edges, not
calls. It counted import statements and labelled them call sites. Real call
expressions carry the attribute (`httpx.AsyncClient`, `subprocess.run`), and
the corrected query returns:

| file | calls | functions |
|---|---|---|
| `bee-evolver/src/hive/aggregator/__init__.py` | 5 | 4 |
| `bee-keeper/src/hive/aggregator/__init__.py` | 5 | 4 |
| `bee-evolver/src/hive/generator/__init__.py` | 4 | 2 |
| `bee-evolver/src/hive/metabolism.py` | 2 | 1 |

bee-**evolver**'s own aggregator — the exact component §6 proposes replacing —
was absent from the original count entirely.

**The predicate was also wrong, in the loose direction.** "No direct I/O
bypassing the Connector" is `FOUNDATIONS.md` §4 prose, but aura's own design
docs assign read-side I/O to the Aggregator *on purpose*: `atcg-fractal.md`
describes it as "Senses: git diff, Prometheus, filesystem", and
`hive-manifest.yaml` names its chamber `SensoryNexus`. Eight of the eleven
functions are aggregator reads and are therefore **not** heresies under aura's
own declaration. Testing a rule aura never made would have been a value
proposition built on a misreading.

**What survives is narrower and much harder to argue with: write-side I/O
outside the Connector — 3 functions, 6 calls.**

| function | calls | what it does |
|---|---|---|
| `EvolverGenerator._apply_patch` | 3 | `git apply` |
| `EvolverGenerator._git` | 1 | git mutation |
| `EvolverMetabolism._configure_git` | 2 | `git config` |

The Generator's declared job is "Pulses events to NATS"; the Connector's is
"Acts via Proteins/IO". Git mutation from a Generator is a boundary violation
under aura's own definitions, not under an imported one.

The predicate is a query, not a grep — `grep` cannot distinguish an import from
a call or a Connector from a Generator. Recorded verbatim so the number is
reproducible and so a future disagreement is about the rule, not the count:

```sql
SELECT source, line_number FROM edges
WHERE type = 'CALLS'
  AND file_path LIKE 'agents/%'
  AND file_path NOT LIKE '%connector%'
  AND file_path NOT LIKE '%aggregator%'   -- read-side is declared design
  AND target LIKE 'subprocess.%'          -- write-side I/O
ORDER BY source;
```

**And nothing in aura checks it.** `bee.Keeper`'s deterministic auditor
(`transformer/__init__.py:113-241`) enforces root layout, sanctioned chambers,
metric thresholds, and `print`/`os.getenv` in the diff — **no call-graph rule of
any kind**. The I/O-boundary rule exists only as prose, adjudicated by an LLM
reading a git diff, and the last recorded audit says `PURE`.

That is the cgis value proposition here, stated without metaphor and after
being cut down by its own measurement: one mechanically-decidable boundary rule
that aura declares, cares about, and cannot currently check.

## 1. Unit of selection

Separate two things that #335 Q1 runs together:

- **Unit of variation** (what a mutation *is*): a single file-scoped patch.
  aura's `bee.Evolver` already types these — `code` (unified diff), `prompt`
  (whole-file replacement), `doc`, `issue`.
- **Unit of selection** (what carries the fitness number): **the bee** — one
  agent service, one metabolic cycle, one cost meter, one success/failure.

Selection has to act on the bee because that is the smallest thing with an
attributable cost and outcome. You cannot assign tokens-per-cycle to a
function. Variation acts on files because that is what a diff is.

**The cost of this answer:** cgis sees modules and domains. It sees a `code`
mutation. It is **blind to a `prompt` mutation** — a changed `.md` under
`agents/*/prompts/` produces no graph delta at all. And prompt/context
mutations are the likeliest high-yield cost lever (they are most of what the
Ouroboros write-up attributes its cost wins to). So cgis constrains a
*minority* of the useful mutation space. Stating that plainly now is cheaper
than discovering it after building an organelle.

## 2. Fitness — and the drift contradiction

The contradiction is real, and it is not resolved by making drift smarter.
Drift measures conformance to a human-written `patterns.yaml`. Its optimum is
a fixed point that a human already wrote down. An organism that drives drift
to zero has finished, not adapted.

The escape is **not** "let the ideal evolve too" — an organism permitted to
edit its own target can always lower the bar, and fitness becomes unfalsifiable.

**Resolution: drift is a constraint, not the objective.**

```
maximize    F  = -(cost per successful outcome)
subject to  drift(domain) ≤ tolerance(domain)     for all domains
            tests green, mypy strict, ruff clean
            no new write-side I/O edges outside the Connector
```

Why this dissolves the tension: F's optimum **moves** — with workload, model
prices, counterparties, none of which the organism controls — so there is
always a gradient. Drift's job is to stop the optimizer from buying cheapness
by destroying structure (inlining the whole hive into one file is cheap to run
and annihilates the graph). Blocking exactly that is what a constraint is for.

This also makes the #151 ratchet **coherent instead of contradictory**: the
ratchet monotonically shrinks the feasible region. Evolution happens inside it.
A ratchet on an objective would be a stopping condition; a ratchet on a
constraint is a safety property.

**F, concretely, for aura:** median tokens (and USD) per ***successful*** cycle
— per bee-keeper audit, per bee-evolver evolution cycle, per negotiation.
"Successful" must be defined per cycle type before measuring (for the evolver:
a proposal that clears deterministic preflight).

*Per-**completed**-cycle is the wrong denominator and is a second instance of
the degenerate-solution class below:* a mutation that makes the agent fail fast
and cheap lowers cost-per-completed-cycle while raising cost-per-success. Cycles
that fail still count their tokens in the numerator; only successes count in the
denominator.

**F's degenerate solution, named in advance:** propose nothing, spend nothing,
score perfectly. So F is never used alone. Every experiment carries a
**guardrail metric** measuring that work still got done. This is the direct
transfer of the Ouroboros contamination lesson (§7): always ask how the metric
can be satisfied *without doing the work*, and price that answer in.

**What is not fitness:** `HIVE_STATE.md` reports
`Negotiation Success Rate: 1.00`. Saturated, and from simulation. Zero
gradient. It cannot be the fitness function.

## 3. Metabolism

Tokens and USD, per cycle. Honestly:

- **Catabolic only.** aura currently spends and does not earn — `HIVE_STATE.md`
  §Economy is entirely unchecked boxes ("Shift from Travel to Compute",
  "Unit of Account: SOL / Stars"). "Converts structural information into cost
  savings" is measurable today. "Earns" is not, and nothing in the PoC should
  depend on it.
- **Metabolic rate** = `{prompt_tokens, completion_tokens, usd, wall_clock}`
  per cycle, per bee, appended to a durable store — not overwritten into
  `HIVE_STATE.md` as a single "last" value, which is what happens now.
- **cgis's metabolic contribution** is exactly one hypothesis, and it is
  falsifiable: the evolver's Aggregator today feeds the LLM a git history plus
  a filesystem map. `cgis context` produces a graph-derived context package
  for a focal FQN. If that package is smaller *and* the proposals stay as
  good, cgis has converted structure into cost savings. If it is smaller and
  the proposals get worse, it has not. That is the first experiment (§6).

## 4. What may mutate, and what stops a bad mutation

**Mutable, ordered by ascending risk:**

1. `agents/*/prompts/*.md` — reversible, no runtime surface
2. context-assembly code inside Aggregators — directly moves F
3. Level 3–4 modules (`components/proteins`, `agents`) — real code

**Immutable — hard-coded, not advisory:**

- `packages/aura-core` (Level 1 Genome; `FOUNDATIONS.md` already declares it
  immutable DNA) and the `proto/` contracts
- Membrane guards (`M`) — the immune system does not get to edit itself
- **`patterns.yaml`, tolerances, `hive-manifest.yaml`, and the fitness
  definition.** The organism may not mutate its own constraints or its own
  scoring. This is the Goodhart firewall and it is the one rule with no
  exceptions.
- credentials, `deploy/`, CI workflow files

**Barriers, defence in depth — all of these already exist somewhere:**

| barrier | status |
|---|---|
| mutations land on a branch + PR, never on main | **already how `bee.Evolver` works** (`EvolverConnector._open_pr`) |
| deterministic preflight: tests, mypy strict, ruff | exists in both repos |
| feature-flag gating, ship only on a pass | precedent: `GUARDIAN_FEATURES` (#154, #331) |
| drift not worse than the ratcheted baseline | **cgis** — measured above, works on aura today |
| no new write-side I/O edges outside the Connector | **cgis** — measured above, 3 functions / 6 calls currently |
| blast-radius check on the changed FQNs | **cgis** `impact` — deterministic where Ouroboros uses a model to read |
| cost gate: mutation may not raise tokens/cycle | needs §3 instrumentation first |
| diff fingerprint, so a rejected mutation is not re-proposed | not present; cheap; prevents evolver looping |
| multi-model quorum on the diff | guardian already is a skeptic-scored reviewer; expensive; later |

Note what this table says: five of the nine barriers exist, and the two cgis
contributes are the two nobody else in the stack can do deterministically.

## 5. Where the loop closes — and why `apply` is not the hard part

```
sense    → cgis + aura Aggregators                    EXISTS
propose  → bee.Evolver, typed mutations, JSON contract EXISTS
evaluate → —                                           MISSING
apply    → branch → PR → human merge                   EXISTS, human-gated
```

#335 Q5 assumes `apply` is the danger. In aura it is already the safest link:
`bee.Evolver` cannot write to main; it opens a PR and a human merges. The
danger is real only at Stage 2 below, which the PoC does not reach.

**The missing organ is `evaluate`.** There is no shadow run in which a mutation
executes and produces a cost/outcome number before a human sees it. Without it,
the loop has *variation* and *inheritance* but **no selection** — which means
what aura does today is an LLM opening PRs, not evolution. Everything in this
document that matters reduces to: build `evaluate`, and the rest is already
there.

**Apply ladder (declare it now so Stage 2 is a decision, not a drift):**

- **Stage 0** — propose only. Today.
- **Stage 1** — propose + auto-evaluate in shadow, human still merges. **This
  is the PoC. Nothing beyond it is in scope.**
- **Stage 2** — auto-merge for `prompt`-only mutations that clear every gate,
  behind a flag, with auto-revert on cost regression. Requires: a kill switch,
  a hard spend cap enforced *outside* the agent process, and an evolver token
  with no write access to anything in the immutable list.
- **Stage 3** — auto-merge of `code`. Explicitly out of scope.

## 6. Pre-registered gate — first mutation experiment

> **STATUS after first live data (§9): this gate CANNOT RUN AS WRITTEN.**
> Two of three guardrails have no measurable source, and the drift hard
> constraint was shown to reward deleting tests. Gate 0 contemplates exactly
> this outcome. The section is left intact below rather than quietly re-tuned —
> §9 records what broke and what would have to be true first.

**Hypothesis:** replacing the evolver Aggregator's context assembly (git log +
filesystem map) with a cgis-derived context package reduces tokens per evolver
cycle without degrading proposal quality.

**Design:** paired. Both arms run on the *same* repo state, same cycle, same
model, same seed where available. Pairing removes commit-to-commit
heterogeneity, which is expected to dominate the variance.

- **Arm A** — current Aggregator.
- **Arm B** — `cgis context`-derived package.

**Arm configuration must be byte-identical apart from the intervention** —
model, temperature, timeouts, retry policy, env vars, resource limits, logging
verbosity. Any of these moves token consumption on its own, and a difference
there produces a win that evaporates on deployment. Record the diff of the two
configs with the results; it should contain exactly the Aggregator swap.

### Gate 0 — precondition, costs no LLM spend

Must all hold before Arm A/B spending begins:

- [ ] per-cycle `{prompt_tokens, completion_tokens, usd, wall_clock}` persisted
      durably for **≥ 20 baseline cycles** (append-only; not the
      overwritten `HIVE_STATE.md` field)
- [ ] baseline coefficient of variation reported
- [ ] **run a 5-pair pilot and measure the paired log-ratio spread directly.**
      Power in a paired design turns on `Var(ln B − ln A)`, which depends on the
      A–B correlation — and baseline cycles are Arm A only, so that correlation
      cannot be estimated from them. Resampling Arm A independently to stand in
      for Arm B assumes zero correlation and would inflate the required N
      absurdly. **Do not paper over this with an assumed ρ.** A borrowed
      constant here is the same defect as a borrowed Hill coefficient: a magic
      number standing where a measurement belongs. Five pairs (~25% of the N=20
      budget) removes the assumption instead of parameterising it, and Gate 0
      already contemplates not running at all — a pilot is cheaper than a wrong N.
      Pilot pairs are **discarded**, not folded into the verdict sample.
      **The pilot yields an order of magnitude for the spread, not a precise
      estimate** — a variance from 5 pairs is itself nearly random, and the
      pair count it implies must be reported with that caveat attached. Its
      purpose is to rule out a wildly wrong N and to replace an unfalsifiable
      correlation assumption with a measured one; it is not a substitute for a
      properly sized calibration study, and must not be presented as one.
      The pilot also measures the **median-vs-mean efficiency loss on this
      data** rather than importing it. An earlier draft asserted "~⅓", which is
      the asymptotic relative efficiency `2/π ≈ 0.637` under near-normality — a
      borrowed constant applied at n = 20, i.e. structurally the same defect as
      the assumed ρ this checkbox refuses. Compute it from the pilot log ratios;
      if the pilot cannot support the estimate, report that and treat the pair
      count as unknown rather than assumed.
- [ ] **if** detecting a 25% shift at the measured paired spread needs more
      pairs than the cost cap affords → **do not run.** Record "underpowered at
      budget" and stop. Power is defined against the decision rule actually used
      below, not a separate significance test: **≥ 80% probability that the
      *whole* PASS condition fires** — CI upper bound < 1.0 **and** point
      estimate ≤ 0.75 — **evaluated at a design alternative of a 35% true
      reduction**, which is deliberately *not* the 0.75 PASS threshold.

      An earlier draft defined power on the CI condition alone and evaluated it
      at a true reduction of exactly 25%. That is wrong twice over, and badly:
      PASS is a conjunction, and at a true effect sitting exactly on the point
      threshold the second conjunct fires roughly half the time. Joint power
      would have been ≈ 40–50% while the document claimed 80% — an experiment
      underpowered by half, advertised as adequately powered. Power must be
      computed on the conjunction, and the design alternative must sit strictly
      inside the PASS region, or the second conjunct silently halves it.
      Budget for the pilot-measured efficiency loss of the median against the
      mean (see Primary): that cost is paid here, in pairs, and is the price of
      a fixed estimator.
- [ ] **pin the cgis version** used for the drift and I/O gates, and record it
      with the results. Both arms must be measured on the same build.
      Precedent from this session: 25b2ce6 (#329) changed the IMPORTS census,
      which silently makes pre- and post-#329 IMPORTS figures incomparable.
      A gate that moves mid-experiment invalidates the experiment.
- [ ] if baseline cost variance is driven by task heterogeneity rather than
      context size → **the cost-fitness framing is wrong.** Record, and close
      #335 as not-ready. This is a real possible outcome, not a formality.

### Criteria — fixed before any spend

- **Primary (must pass):** the **paired** log ratio
  `d_i = ln(tokens_B,i / tokens_A,i)`, bootstrapped **by resampling pairs**.
  PASS when the 90% CI upper bound on `exp(median(d))` is **< 1.0** *and* the
  point estimate is **≤ 0.75**.

  **Bootstrap variant: BCa, 10 000 resamples, seed recorded.** The median is a
  non-smooth statistic and plain percentile intervals have unreliable coverage
  at n ≈ 20; leaving the variant unstated would have put the loosest part of the
  specification at the point of highest risk. BCa's bias and acceleration
  corrections are the cheapest available fix, and naming it now removes a
  post-hoc choice.

  Two further things this fixes. First, an earlier draft specified a ratio of
  two independently-computed medians, which discards the pairing — the one
  property the design was built to exploit. Second, the estimator is the
  **median** of `d_i`, not the mean, and the reason is pre-registration rather
  than skew: the estimator must be fixed before the data, so it is not
  permissible to see one wild pair and switch. Under a fixed choice, robustness
  beats efficiency; the efficiency loss is measured in the pilot and paid in
  pairs at Gate 0, not asserted from an asymptotic constant.

- **Guardrail (must not regress), both terms:**
  1. **Acceptance rate** — fraction of proposed improvements clearing
     deterministic preflight (tests + mypy + ruff + drift).
     **Arm B ≥ max(Arm A − 10 pp, 0.75 × Arm A).** The stricter binds, so there
     is no discretion at verdict time. The relative floor exists because a flat
     10 pp is meaningless at a low baseline — at Arm A = 12% it would permit 2%,
     an 83% relative collapse; `max()` binds at 9% there. At Arm A = 80% the
     absolute term binds at 70%.
  2. **Accepted proposals per cycle** (absolute throughput).
     **Arm B ≥ 0.80 × Arm A.**

  Both guardrail terms are proportions/rates estimated from the same ~20 pairs
  as the primary, and get **the same statistical treatment**: each is evaluated
  on the 90% BCa CI of the paired Arm B − Arm A difference, and the guardrail
  regresses only when the CI lies **wholly below** its threshold. Point-estimate
  thresholds on a 20-sample proportion would have made the gate fire on noise in
  both directions — a false FAIL when Arm B is fine, and (through the same
  noise) a false PASS when it is not. Applying a careful interval to the primary
  and a bare threshold to the guardrail was an asymmetry in rigour with no
  justification behind it.

  The second term exists because the first is a *ratio* and cannot see an
  intervention that cuts tokens by cutting the volume of proposals rather than
  their quality: propose three things instead of ten, keep the same acceptance
  fraction, bank the token saving, deliver less work. That is a clean false PASS
  against every other criterion here, and it is the same degenerate-solution
  family as §2's "propose nothing".

  3. **Mutation-type mix.** Arm B's share of `code`-type proposals must be
     **≥ Arm A's share − 10 pp**, same CI treatment.

  The third term closes most of a loophole an earlier draft declared unclosable.
  That draft said an intervention could hold count *and* acceptance rate
  constant while making each patch shallower, and that no cheap deterministic
  proxy for "substance" exists. The second half was wrong: `bee.Evolver` already
  **types** every proposal — `code` / `prompt` / `doc` / `issue` — so a shift
  toward trivially-passing work (docstrings, formatting, `doc`-type filler) is
  visible as a change in the type mix, using a field that already exists. No new
  instrumentation, no human judgement.

  It also patches a circularity worth naming: §3 defines a *successful* evolver
  cycle as one whose proposal clears preflight, which is the **same predicate**
  as guardrail-1. F and guardrail-1 therefore move together and are not
  independent evidence. Terms 2 and 3 are what stop that pair from being
  satisfiable by trivia.

  **What remains open, and is knowingly accepted:** within `code`-type
  proposals, patches could still get shallower without shifting the mix. Diff
  line count rewards verbosity and complexity deltas are gameable, so the
  residue needs human judgement on a sample. **Recorded so a PASS is read with
  it in mind rather than discovered afterwards** — and if the experiment passes,
  a human read of a sample of Arm B `code` patches is the first follow-up,
  before Stage 1 ships.

- **Hard constraints (any breach = FAIL, no discussion):** for every domain,
  **`drift_after ≤ drift_baseline`, on the score, not the status band**; zero
  new write-side I/O edges outside the Connector; no mutation touching the
  immutable list in §4.

  Score, not status, because status is a coarse band: a domain can degrade
  substantially inside `clean` or inside `warning` without changing label, and
  the optimiser would be free to spend exactly that slack — trading structure
  for tokens, which is the precise failure §2 introduces the constraint to
  prevent. A pure per-domain ratchet also resolves the mismatch with §2's
  standing `drift ≤ tolerance`: where baseline is under tolerance the ratchet is
  strictly tighter and implies it, and where a domain is *already* in breach the
  ratchet is the only honest demand to make of a single mutation. This is #151's
  convention applied per experiment.

  **Two different drift checks were conflated in an earlier draft, and
  separating them dissolves an objection raised in review.** The objection: five
  of eight measured aura domains already sit in the warning band, `agents`
  (home to both bees) at 0.27, so a zero-slack ratchet looks like a standing
  false-FAIL risk — a marginal regression on one cycle kills a 25-pair
  experiment while saying nothing about the hypothesis. That is right about the
  arithmetic and wrong about when the check runs, because the draft never said:

  1. **Drift on the intervention** — does Arm B's *own* code change move
     `agents`? This is a property of a diff, not of a cycle. `cgis drift` is
     **deterministic** (verified: two independent ingests of the same tree
     produce byte-identical reports), so this is evaluated **once, statically,
     before any spend, at zero LLM cost.** A breach means fixing Arm B's code
     and re-checking — no pairs are consumed and no experiment is lost.
  2. **Drift on each proposal** — already inside the acceptance guardrail's
     preflight. A breach there is a *rejected proposal*, which lowers the
     acceptance rate and is exactly what that metric is for. It cannot kill the
     experiment.

  With the two separated, no epsilon is needed anywhere, and none is invented.
  Determinism is what makes that work: the ratchet never fires on measurement
  noise, because there is none.

**N = 20 verdict pairs, plus 5 discarded pilot pairs = 25 paired cycles of
spend.** The pilot is Gate 0's; it never enters the verdict sample, and if the
pilot says 20 pairs are not enough, the correct move is to stop, not to top up.
**Cost cap: a hard USD figure covering all 25, agreed before the first call,
enforced outside the agent process.**

### Stopping rule — three outcomes, no fourth

The verdict is computed **once, after all 20 pairs**, from the metrics above.
No peeking-and-stopping on a good interim result.

Early stop is permitted only for: cost cap reached, or ≥ 3 consecutive cycles in
**either** arm producing malformed output (that is an infrastructure failure and
is recorded as such, not as a result). Watching only Arm B would let a failing
baseline or a broken harness masquerade as an Arm B win.

- **PASS** — primary met **and** guardrail held **and** hard constraints clean.
  → ship behind a flag, Stage 1.
- **FAIL** — primary missed **or** guardrail regressed **or** any hard
  constraint breached. → write it up in `docs/specs/`, ship nothing, and **do
  not re-tune the threshold and re-run against the same data.**
- **INCONCLUSIVE** — CI straddles the threshold. → record; the follow-up must
  be a *different intervention*, not more N on the same one.

**"Partially passed" is not an outcome.** Tokens down 30% with acceptance down
15 pp is a **FAIL**, and it is written down as a FAIL. Precedent for why this
matters: three guardian hypotheses failed their pre-registered gates on
2026-07-31 and were cheap and legible precisely because the gates were fixed
first (#330, #331, `docs/specs/2026-07-31-finder-bug-class-taxonomy.md`).

## 7. The Ouroboros write-up — what transfers and what does not

Source: habr.com/ru/companies/airi/articles/1065428/. Read this session.
Self-reported, single author. Its numbers are evidence about *that* system on
*those* benchmarks; none of them transfer to aura as evidence.

**Transfers:**

- **Fitness = external benchmarks + token cost.** Independent arrival at §2's
  conclusion: conformance-to-declaration is not fitness.
- **Scope review** ("does this change break the project"). Their model reads
  the repo; **cgis `impact` answers it deterministically.** Straight upgrade.
- **Diff fingerprinting** so rejected mutations are not re-proposed. Cheap,
  prevents the evolver looping. Adopt.
- **Multi-model quorum before merge.** cgis guardian is already this shape.
  Expensive; after the PoC.
- **A constitution loaded every cycle.** aura already has it —
  `FOUNDATIONS.md` + `hive-manifest.yaml`. No work.
- **The contamination lesson.** 68 of 115 GAIA tasks had published solutions;
  the benchmark was measuring lookup, not capability. Generalised: assume the
  metric can be satisfied without doing the work, and find out how *before*
  running. For cost-per-cycle that answer is "propose nothing" — hence §2's
  guardrail.
- **Instructions in comments are not a control.** Their Grok-4.5 run downloaded
  benchmark solutions despite explicit warnings in the code. Every control in
  §4 must be mechanical — branch protection, token scope, spend cap outside the
  process — never an instruction the model is asked to respect.

**Does not transfer:**

- **The benchmark suite** (Terminal-Bench, OSWorld, SWE-Bench Pro, GAIA). aura
  has no equivalent and building one is the dominant cost of a real fitness
  function. Do not paper over this with `Negotiation Success Rate: 1.00`.
- **Swarms of hundreds of subagents.** No budget, and pointless without
  `evaluate` — unselected variation is just spend.
- **"~75% of commits written autonomously over months."** That is a multi-month
  token budget, not a PoC. Stage 1 is where this document stops.
- **Hierarchical markdown memory over vector RAG.** aura already has pgvector.

## 8. Fractality — a declared property, measured

aura states the claim literally, which makes it testable.
`docs/visual/hive/atcg-fractal.md`: *"This pattern repeats at **every scale** —
from individual services to the entire Hive organism."* In the vocabulary of
#186 that is `scale_invariant` — the motif mix is the same at every rung, i.e.
slope ≈ 0. This is the first time the #186 measure has been pointed at a
codebase that asserts its own fractality.

```
IMPORTS  scale_invariant  slope=+0.084  R²=0.19  band=±0.144  live=8
CALLS    hierarchical     slope=+0.101  R²=0.56  band=±0.068  live=9
```

Population for scale (CALLS). The first three rows were run **today, on the
same build**, so they are directly comparable; the rest are the #186 baseline
and are shown for range only:

| repo | slope | R² | band `2·SE` | slope/SE | live | verdict |
|---|---|---|---|---|---|---|
| cgis (today) | +0.194 | 0.88 | ±0.082 | 4.7 | 5 | hierarchical |
| owner-api (today) | +0.148 | 0.75 | ±0.076 | 3.9 | 7 | hierarchical |
| **aura (today)** | **+0.101** | **0.56** | **±0.068** | **3.0** | **9** | **hierarchical** |
| sqlalchemy 2.x (#186) | +0.171 | 0.86 | 4 | hierarchical |
| django 6.x (#186) | +0.133 | 0.75 | 6 | hierarchical |
| httpx 0.28 (#186) | −0.676 | 0.89 | 3 | flat |
| flask 3.1 (#186) | −1.020 | 0.97 | 3 | flat |

> **IMPORTS numbers are not comparable across #329.** owner-api's IMPORTS today
> reads `hierarchical +0.108 (R² 0.76)`; the #186 baseline recorded
> `scale_invariant −0.020 (R² 0.15)`. That is not a change in owner-api — 25b2ce6
> (#329) rewrote `fingerprint.py` so the IMPORTS census looks through transparent
> re-exports. Any IMPORTS comparison spanning that commit is invalid. CALLS is
> unaffected. This is also a gate-hardening item — see Gate 0.

**Three findings.**

**a) The claim does not hold on the call graph — and aura is nominally the
closest of the three to holding it, but that ranking is weak.** `hierarchical`
at slope/SE ≈ 3, so the motif mix is not the same at every rung. Its slope is
the *lowest* positive of the three same-build repos (0.101 vs owner-api 0.148
and cgis 0.194).

The ordering holds on standardised terms too, not just raw magnitude — slope/SE
is 3.0 / 3.9 / 4.7, same order as the slopes — which is why the bands are in the
table above rather than left implicit.

The failure verdict is solid: it survives its own dead-band on all three. **The
ranking is still weak, for a different reason.** The three fits differ
substantially in quality (R² 0.56 / 0.75 / 0.88), and aura's 0.56 sits below the
entire range the measure validated itself on (0.71–0.86,
`docs/specs/2026-07-30-cgis-fractal-design.md`). A standardised slope from a
poorly-fitting line is still a summary of a line that does not fit. Read (a) as
"aura fails the claim"; treat "by less than the others" as suggestive only.

**b) The declared hierarchy is real in the import graph.** At T3/T4/T5 a single
motif holds 0.98 / 0.95 / 0.90 of the census, entropy 0.14 / 0.34 / 0.61 —
unusually clean layering. aura's Genome → Nucleus → Organs → Citizens ladder
(`FOUNDATIONS.md` §1) shows up as measured structure, not just documentation.
Confirming a declared property is rare; it usually goes the other way.

**c) The CALLS curve is a hump, not a line — in all three repos. The hump is
not an aura finding.**

An earlier draft of this section read the entropy rise and tangle rise at
T4/T5 as "ATCG-M holds inside a bee and comes apart between bees." **Running
the comparison refutes that.** cgis and owner-api produce the same hump in the
same place, and neither claims fractality:

| repo | CALLS entropy by rung | peak | tangle at peak |
|---|---|---|---|
| cgis | 0.91 / 1.34 / 1.34 / 1.96 / **3.00** / 0.92 | T4 (9 groups) | 0.265 |
| owner-api | 0.33 / 1.20 / 1.29 / 1.20 / 1.61 / **2.66** / 2.35 | T5 (9 groups) | 0.277 |
| aura | 1.02 / 1.12 / 1.07 / 1.07 / 1.81 / **2.18** / 1.95 / 1.71 / 1.45 | T5 (33 groups) | 0.223 |

Coarse rungs have few groups and few triads, and the top of any dependency tree
is where cross-cutting glue lives. The hump is a property of the measure at
coarse rungs, not a defect of the repo under it.

What survives the comparison is the *shape*, and it points the other way from
the earlier draft:

- aura's peak entropy is the **lowest** of the three (2.18 vs 2.66 and 3.00),
  and its tangle at peak is the **lowest** (0.223 vs 0.277 and 0.265). aura is
  less tangled at the top of its tree than either of the other two.
- aura's hump is **broad and shallow** — entropy stays in 1.45–2.18 across four
  rungs (T4–T7) — where cgis spikes to 3.00 at a single rung and collapses.
  With 9 live rungs against 5 and 7, aura's ladder is the deepest and its
  transition the most gradual.

That gradual ramp is also the mechanical reason for the two headline numbers:
the **lowest slope (0.101)** and the **worst R² (0.56)** are what a long shallow
ramp looks like under a linear fit. Not data poverty — aura has the *most* live
rungs of any repo measured. Per the #186 design note, the curve is the evidence
and the slope only the headline; here that caveat decides the reading.

The measure does not say whether a broad shallow transition is better than a
narrow spike. It says aura's is different, and it is not the pathology the
first draft claimed.

**Caveat, stated so the finding is not over-read.** `cgis fractal` measures
triad-motif entropy across FQN-path coarsening. aura's claim is about repeated
*composition* (every bee has A/T/C/G/M). Related predicates, not identical
ones. This is evidence against the strong sentence "repeats at every scale" as
applied to the call graph. It is **not** a refutation of the composition claim,
and should not be reported as one.

## 9. First live data — and what it breaks

Gate 0's instrumentation shipped to aura (#243–#247) and ran. **Twelve real
cycles.** Everything below is measured, not argued, and most of it contradicts
something written above.

### The instrumentation works

| measurement | result |
|---|---|
| cost per cycle | **$0.0028** before the context fix, **$0.0056** after |
| prompt tokens | 1560 → **8112**, identical across all replicates (CV = 0.000) |
| completion tokens | CV = 0.219 — real sampling variance |
| wall clock | CV = 0.874 |

**Cost was never the binding constraint.** All 25 cycles of §6 come to roughly
**14 cents**. The cost cap that §6 spends a paragraph on is a rounding error;
the real constraints are cadence, provider rate limits, and whether a proposal
can be applied at all.

### Finding 1 — the drift hard constraint rewards deleting tests

§6 requires `drift_after(domain) ≤ drift_baseline(domain)`. Applied to a real
patch (aura #247 — a bug fix plus the agent's first regression tests):

```
baseline (main)                          agents 0.2493
+ the patch (adds 3 tests)               agents 0.2590   → FAIL
the patch with ALL agent tests deleted   agents 0.2207   → best score
```

Deleting the suite improves the metric three times more than adding three tests
worsens it. **An optimiser under this constraint deletes the tests.** The
component that moved is `T_calls[021U]` (0.34 → 0.37 against an ideal of 0.00) —
several tests calling one shared fixture is a 021U burst by construction.

Three separable causes, and the first is mine:

1. **A measured baseline was used as a declared ideal.** The `patterns.yaml`
   came from `cgis init-ontology`, whose own header reads *"a measured baseline,
   not a verdict."* It was generated from a tree that had no agent tests, so the
   "ideal" encodes a codebase without tests, and conformance to it means "be
   like you were yesterday." §2 says drift measures conformance to a *declared*
   ideal — nobody declared this one.
2. **Test code and production code share a fingerprint.** Most architecture
   metrics exclude tests; cgis does not. Folding them together makes the metric
   structurally anti-test. Fixable: separate domain, or exclusion.
3. **Used as a zero-slack veto on a single mutation**, the constraint has no
   discriminating power — it responds to *change*, not to *quality*. §2 argues
   drift must be a constraint rather than an objective; this shows that a
   constraint applied at zero slack against an inferred target is a stasis gate
   wearing a quality gate's clothes.

**Amendment:** drop drift from §6's hard constraints. The write-side I/O gate
stays — it behaved correctly on the same patch (7 → 7 edges, no regression) and
is the one gate with demonstrated discriminating power.

### Finding 2 — guardrails 1 and 2 have no source

**`applied` = 0 across all 10 cycles.** Every `code`-type patch fails
`git apply --check` with `corrupt patch at <stdin>:N` — malformed hunk headers.

This survived the fix that was expected to cause it. Before aura #247 the
evolver never received its persona (see Finding 4), so it was generating diffs
having never been told the diff contract. After the fix it receives all 3012
characters, *including* "Must be valid unified diff format", and **still
produces zero applicable patches.** The instruction is necessary and not
sufficient; LLM unified-diff generation fails on hunk line counts regardless.

Guardrail-1 (acceptance rate over preflight) and guardrail-2 (accepted proposals
per cycle) therefore measure nothing: no proposal ever reaches a preflight.

**Amendment:** §6 is blocked until patches apply. Either loosen application
(`git apply --3way`), or drop unified diffs for whole-file replacement — the
`prompt`/`doc` path already works that way and is the only one that never fails.

### Finding 3 — rate limits trip Gate 0's own stop criterion

Ten cycles dispatched in quick succession: **5 succeeded, 5 returned
`llm_error`** — almost certainly provider rate limiting. That is a **50%
`unknown_usage` rate**, and Gate 0 already names a high unknown share as a
"data unusable, do not proceed" outcome.

The first genuine collection attempt hit a pre-registered stop criterion — not
through any flaw in the metric, but through the pace of dispatch.

**Amendment:** collection must pace requests and record the null rate per batch.
An unpaced burst cannot produce a usable baseline.

### Finding 4 — the fluctuation test: proposals are retrieval, not diagnosis

Adapted from Luria–Delbrück. The literal method does not transfer — its signal
comes from inheritance and amplification producing jackpot lineages, and our
cycles inherit nothing. The *logic* transfers: run parallel replicates and let
the distribution discriminate, rather than inspecting one case.

Five successful replicates, identical repo state. All five propose the same
thing first:

```
Migrate persistence layer to async SQLAlchemy
Resolve async SQLAlchemy migration in persistence layer
Migrate persistence layer to async SQLAlchemy
Resolve async SQLAlchemy migration in persistence layer
Migrate Persistence Layer to Async SQLAlchemy
```

Near-total concentration, which reads as a stable expert diagnosis. It is not.
aura has an **open GitHub issue** titled *"Migrate the persistence layer to
async SQLAlchemy (remove DB `asyncio.to_thread`)"*; the Aggregator pulls open
issues into the context, and the persona's priority list says *"2. Address open
GitHub Issues that have clear, bounded solutions."* The agent is restating a
human-written issue, sometimes verbatim.

Twice now, apparent purposefulness has been a property of the input:

| cycle batch | looked like | was |
|---|---|---|
| before aura #247 | focused on the persistence layer | could see 53 of 420 files — nothing else to propose |
| after aura #247 | consistently diagnoses the top problem | echoing an open issue |

This is not a failure of the model — it is doing exactly what its persona
instructs. But "executing priority #2" and "reasoning about what the organism
needs" are different things, and they are indistinguishable from the outside
until a fluctuation test separates them.

**Consequence for §6.** The hypothesis is that a cgis-derived context reduces
tokens "without degrading proposal quality". Proposal content is currently
dominated by explicit hints already present in the context. Any context swap
that drops the issues list would change proposals completely — for reasons that
have nothing to do with cgis. §6 would be measuring hint retrieval, not context
quality.

### Finding 5 — two more silent failures, found only by running

- **bee.Evolver's LLM had been dead for at least seven weeks** while every
  scheduled run reported success. Both API keys were unauthorized; each cycle
  produced zero improvements, exited 0, and sent a Telegram message announcing
  completion. `main.py` checks `not observation.success and not observation.plan`,
  and the plan object always exists — carrying the narrative "The Evolver's
  brain is offline".
- **`find_hive_root` returned the bee's own directory**, because every bee ships
  its own `hive-manifest.yaml` and the search stopped at the first. The evolver
  sensed 53 of 420 files and silently loaded a one-line fallback persona instead
  of its 3012-character one.

Both are instances of the pathology §5 names: a system reporting health it never
measured. Neither was found by reading — the first by a workflow step that fails
when the log is empty, the second by arithmetic that did not add up (a 16 KB
filesystem map cannot fit in 1563 prompt tokens).

### What has to be true before §6 can run

1. Patches apply at a non-zero rate — otherwise there is nothing to gate.
2. A preflight exists for evolver proposals — guardrail-1's stated source.
3. Drift is either excluded from the hard constraints or given a *declared*
   ideal that does not penalise tests.
4. Collection is paced so the `unknown_usage` share stays low.
5. The proposal-hint confound is handled — hold the issues list fixed across
   arms, or accept that the experiment measures retrieval.

None of these is expensive. All of them were invisible before the first cycle
ran, and every one of them would have silently corrupted the experiment.

## Recommendation

Do not build an organelle. Do three bounded things, in order:

1. ~~**Instrument the metabolism** (Gate 0).~~ **Done** — shipped to aura in
   #243–#247 and run for twelve live cycles. §9 has the numbers.
2. **Make patches apply.** This moved ahead of `evaluate`, because §9 Finding 2
   showed 0 of 10 proposals are applicable: a preflight over patches that never
   apply measures nothing. Either `git apply --3way` or whole-file replacement.
3. **Add `evaluate`** — a shadow run producing a cost + preflight number per
   proposed mutation. Still the missing organ; still the thing that turns
   variation into selection.
4. **Then reconsider §6** against the five preconditions at the end of §9. Two
   of its three guardrails and its main hard constraint are currently
   non-functional, so "run it once" is not yet an available move.

**One claim in §4 survived contact with data and one did not.** The write-side
I/O gate behaved correctly on a real patch (7 → 7 edges) and remains the
membrane role cgis can defend. The drift ratchet did not: §9 Finding 1 shows it
scores an empty test suite higher than a tested one. Reconciles with #305 —
cgis stays a measurement tool, and one of its two proposed gates has now been
falsified by measurement rather than argument, which is the outcome that
discipline is for.
