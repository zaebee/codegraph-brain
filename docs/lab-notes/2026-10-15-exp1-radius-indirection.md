# Exp1: change radius and indirection vs agent cost — pre-registration

Date: 2026-10-15. Committed before the first paid session. Research note: "Код для кремниевых
авторов" (H1, H2). Spike that motivated it: #627.

## Question

Does the cost of an impact question for an agent depend on the change radius (files on the answer's
caller chains) and on indirection (an attribute-held or injected dependency on a chain), rather than
on how well the target's domain fits a drift template?

- **H1:** radius drives cost; domain drift adds nothing once radius is known.
- **H2:** at equal radius, an indirect hop raises cost or lowers recall.

## Design

- **Corpus:** owner-api at 8de22452 (the existing tasks' pin), source root `ownima-backend/app`.
  The two existing owner-api targets (`credit_wallet`, `occupied_window`) are excluded: their
  transcripts were used to tune cgis.
- **Arm:** `control` only (no cgis), model `claude-sonnet-5-5` (harness default),
  `--max-budget-usd 2.0`, 3 runs per task.
- **Question:** the existing impact template — every function or method under `app/` (not tests,
  not scripts) that can end up calling the target, up to an entry point.
- **Cells** (radius = distinct files among the target and its gold callers; indirect = some edge on
  a chain is a call `self.<attr>.<method>(...)`, confirmed by hand):

  |  | direct | indirect |
  |---|---|---|
  | small (1–2 files) | 6 | 6 |
  | big (≥5 files) | 6 | 6 |

- **Gold:** cgis proposes; every gold set is re-derived independently. An AST closure over
  final call names (a superset) is compared with the cgis closure, and every difference is
  adjudicated by reading the code, with file:line evidence. cgis is never the sole source of gold.
- **Billing:** sessions run on a separate organisation API key (`ANTHROPIC_API_KEY`), not the
  subscription. The first session's stream-json `init` event must show
  `apiKeySource: ANTHROPIC_API_KEY`, or the run stops.

## Gates (fixed now)

1. **Radius (H1):** median `cost_usd` in the big row ≥ 1.5× the small row.
2. **Indirection (H2):** in both rows, the indirect cell's median cost ≥ 1.3× the direct cell's,
   or its median recall is ≥ 0.10 lower.
3. **Template irrelevant (H1):** the partial Spearman correlation of cost with the drift score of
   the target's domain, controlling for radius, has |ρ| < 0.2. If |ρ| ≥ 0.3, drift matters and H1
   is refuted. Between 0.2 and 0.3 is inconclusive.
4. **Ceiling:** if recall is 1.00 in every cell, no correctness claim is made; only cost is reported.

## Pilot (first spend, about $3)

One cell, big × indirect: 6 tasks × 3 runs. Purpose: confirm billing, gold quality and the
cost spread before building the other 18 tasks. Pilot sessions count toward the full run only if
no task, gold set or gate changes after them.

## Known risk, found while building tasks

Indirection and radius are confounded in owner-api. Of 1978 candidates with 1–18 transitive callers (before dropping external nodes),
the small × indirect cell has 35, and only 3 after requiring distinct files and closure overlap
below 0.2 (big × indirect: 28 → 8). If small × indirect cannot reach 6 tasks, H2 is tested in the
big row only, and that limit is stated in the result rather than patched afterwards.

## Rulings before the pilot (no session run yet)

- **reservation-conflict-query:** a callable passed as an argument
  (`self._check_conflicts_internal` → `_execute_with_metrics`,
  services/reservation_conflict_service.py:183-185) reaches the target. Neither cgis nor the AST
  closure follows it. Its 12 known callers are a lower bound, so they go in `allowed_symbols`:
  naming them costs nothing and they are not required. Cost if wrong: that task's recall reads high.
- **lock-wallet:** its callers contain the whole `credit_wallet` chain, the excluded tuned task. Kept,
  because the exclusion guards the cgis arm against tuning and the pilot runs control only. The cost
  is weaker independence between two tasks in the full set.
- Gold for the 6 pilot tasks: 82 cgis callers checked, none a false positive. Of the 24 AST-only
  candidates, 21 were accepted and 3 rejected as same-name calls to other objects, each with file:line.

## Pilot result and gold correction (after the pilot; the pilot therefore does not count)

18 sessions, all billed to the organisation key (`apiKeySource: ANTHROPIC_API_KEY` in every transcript),
$4.12 in total. Per-task median cost $0.16–$0.39.

The pilot found defects in the gold, not in the agent:

- **reservation-conflict-query.** All 3 runs named a chain of real callers above two resolver gaps (#632):
  a bound method passed as an argument, and `ReservationService.check_booking_window` inherited from a
  mixin. Neither cgis nor the AST closure nor the adjudicator had it. Every name the agents added lies
  in the AST closure above the break, so the 15 names go into `allowed_symbols`. They are never required,
  because the full set above the break is unknown.
- **Inherited methods.** Agents spell `ReservationService.advance_confirmation`; gold had only the
  definer, `ReservationTransitions.advance_confirmation`. Each gold method now also accepts every
  subclass spelling, taken from the EXTENDS edges.
- **Nested functions** accept the spelling without the enclosing function.
- **`worker/base.py`** (task registration) is an allowed file, as in the older credit_wallet task.

Rescored offline from the saved transcripts, with no new spend: median recall 0.97–1.00 and precision
0.85–1.00. The remaining misses are real: a nested function left unnamed (2 runs) and one catalog
route (1 run). **Gate 4 (ceiling) is all but certain**, so the full run answers H1 and H2 on cost only.

## Rulings for the remaining cells (before any of their sessions)

- **Small × direct: 4–12 callers, not 1.** The first draw sorted small-radius candidates by fewest
  callers and returned targets with one caller. That confounds radius (files) with answer size
  (symbols). The cell now draws targets with 4–12 transitive callers in 1–2 files and at most 2
  definitions sharing the target's name. Cost if wrong: the small row's answers have 8–12 symbols
  against the big row's 10–22, so part of any cost gap is answer size.
- **Small × indirect has 2 usable tasks, not 6.** Of the 3 candidates left after the diversity
  filter, `OpenSearchClient.close` has 464 same-name call sites, so no clean gold exists. As stated
  above, H2 is therefore tested in the big row only. The 2 small × indirect tasks run and are
  reported descriptively.
- **Big × direct:** the 6 candidates with the fewest AST-only differences and no cgis-only callers.
- Every gold set is adjudicated as in the pilot, now also searching for callers both tools miss. A
  task whose adjudicated chain changes cell (gains an indirect hop, or its file count crosses a
  boundary) moves to the cell it belongs in. It is not dropped.

## Final cells, after adjudication (before any full-run session)

The candidate classifier counted only `self.<attr>.<method>(...)` as indirect. Adjudication found that
a FastAPI route receiving a service through `Depends` is indirect too, and almost every big-radius chain
in owner-api ends in such a route. Under the rule above, tasks moved to the cells they belong in:

| cell | tasks | moved in |
|---|---|---|
| big × indirect | 11 | 6 pilot; live_dispute_clause, credit_document, extra_options_filter, calculate_average_rating (route `Depends`); `_thread_for` (9 files, `getattr` hook dispatch) |
| big × direct | 2 | vehicle_by_id_query, calculate_pagination |
| small × direct | 4 | — |
| small × indirect | 3 | `_counterparty_cards` (`ChatServiceDep`) |

- **H2 cannot be tested as designed.** In this codebase, indirection is the norm at big radius and
  rare at small radius, so the 2×2 collapses: big × direct has n=2. H2 is reported descriptively.
  That collapse is itself a finding about the corpus.
- **H1 runs as designed:** big row n=13, small row n=7.
- **`_thread_for`:** the adjudicator enumerated all 31 callers both tools miss (a callback through
  `_guarded`, and `getattr(self.chat_lifecycle, hook)` driven by the reservation state machine), so
  they are required gold here, not a lower bound. This is the one task where a correctness
  difference is likely.
- The full run is 20 tasks × 3 runs, all fresh. Pilot sessions are not reused.

## Gate 3 operationalised (written while the full run is in flight, before any of its results were read)

The pre-registration said "drift score of the target's domain" without defining the domain.
owner-api's own `patterns.yaml` leaves several packages the targets live in undeclared (rider, rating,
pricing, admin). So, uniformly for every task:

- **domain:** the package `app.domains.<x>`, or `app.<x>` outside `domains/`, that contains the
  target, read from a graph ingested at `ownima-backend/` so FQNs carry `app.`;
- **drift:** the gate-free `shape_residual` to the nearest template of cgis's alphabet, under the
  `python` profile. This is the "how well does the domain fit a template" that H1 claims does not
  matter;
- **radius:** the gold file count (target plus required callers);
- **cost:** the per-task median `cost_usd` over its 3 runs.

The partial Spearman correlation of cost with drift, controlling for radius, is taken over the 20
task medians.

## Result (full run, 2026-10-15)

60 sessions, all billed to the organisation key (`apiKeySource: ANTHROPIC_API_KEY` ×60), none failed and
none contaminated; $10.29. Model `claude-sonnet-5-5`, control arm, 3 runs per task, scored with the
corrected gold.

| cell | n | median cost | median recall |
|---|---|---|---|
| big × direct | 2 | $0.123 | 1.00 |
| big × indirect | 11 | $0.206 | 1.00 |
| small × direct | 4 | $0.108 | 1.00 |
| small × indirect | 3 | $0.163 | 1.00 |

| gate | result |
|---|---|
| G1 radius (H1) | **PASS.** Big row $0.189 (n=13) against small row $0.114 (n=7): ×1.66, threshold ×1.5. |
| G2 indirection (H2) | Descriptive, as ruled above. Indirect costs ×1.68 in the big row and ×1.51 in the small row; recall is unchanged. |
| G3 template irrelevant (H1) | **PASS.** Spearman cost~files +0.47, cost~drift −0.02; partial cost~drift given files +0.11, under the 0.2 limit. Caveat: drift spans only 0.24–0.37 over 8 domains. |
| G4 ceiling | **Not hit.** Recall is 1.00 in 47 of 60 runs, minimum 0.76. |

### Reading

- **H1 holds as registered.** Cost scales with radius, and how well the domain fits a template adds
  nothing once radius is known.
- **The registered G1 overstates radius.** The big row is 11/13 indirect and the small row 3/7. Within
  one call type, radius alone gives ×1.14 (direct, n=2/4) and ×1.26 (indirect, n=11/3), while indirection
  gives ×1.5–1.7 in both rows. Descriptively, indirection moves cost more than radius does. The big ×
  direct cell is too small to claim it, so this is the hypothesis for the next experiment.
- **Correctness drops where the edge is dynamic.** `_thread_for` (a `getattr` hook dispatch plus a
  callback) recalls 0.80 and `calculate_average_rating` 0.93. The two most expensive tasks
  (`_thread_for`, `reservation_conflict_query`, about $0.35) are the two whose chains run through
  callback or dynamic hops, the same class of edge cgis does not see (#632). What is hard for the agent
  and what is hard for static analysis largely coincide.
- **Low precision is mostly a gold-style question, not agent error.** `notify_top_up_failed` scores
  0.69 because agents also name the concrete `TopUpNotificationHandler` behind the Protocol stub, which
  is defensible. That was not re-ruled after the results, so the number stands as scored.

### Next

H2 needs a corpus where big-radius direct chains exist; FastAPI routes make every big chain indirect.
A follow-up should vary indirection with radius held fixed, for example tasks built on cgis itself
(no DI framework) set against owner-api.

## Post-review corrections (PR #633 independent review; supersedes parts of "Reading")

Every number above reproduces from the raw jsonl in an independent implementation, rescoring the 60
transcripts gives 0 mismatches, and the gates and rulings were committed before the sessions they
govern. But the review found a gold error and several overstated readings.

**C1, retracted: "correctness drops where the edge is dynamic".** The `_thread_for` gold followed the
hook dispatch `getattr(self.chat_lifecycle, hook)` to every caller of `_apply_transition`. Three
transitions carry no `Chat(...)` effect: CREATE_PENDING (machine.py:643), CONFIRM_AS_RIDER (:672), and
MARK_OVERDUE, where it is absent deliberately (:446–450). Nine required symbols and `rider/routes.py`
therefore never reach the target. Runs 0 and 1 missed exactly those 10 items: the agents read the state
machine correctly and the gold over-approximated. The task YAML is corrected (40 → 31 symbols,
9 → 8 files). `results-exp1-full.jsonl` keeps the pre-correction scores. Rescored post hoc and declared
as such: `_thread_for` recall is 1.00 / 1.00 / 0.95, and 11 of 60 runs fall below 1.00 instead of 13.
G4 is still "not hit", but only barely. The claim that agent difficulty and static-analysis difficulty
"largely coincide" loses its main evidence and is withdrawn.

**I1.** `calculate_average_rating` at 0.93 is not a dynamic-edge miss. Every run misses a nested
function (`create_rider_review._row`), and one run is penalised for the real import alias
`CatalogWorker` (worker/base.py:19). The gold should accept alias spellings.

**I2, answer size confounds radius.** Over the 20 tasks, Spearman of cost with required-symbol count is
+0.58, with radius +0.47, and the two predictors correlate at +0.74. The most expensive tasks are also
the largest answers. G1's "radius" effect cannot be separated from answer size in this design.

**I3–I5, uncertainty.**
- A bootstrap over tasks and runs puts G1 at ×1.66, 95% interval [0.98, 2.09], with a 36% chance of
  falling below ×1.5.
- G3's partial ρ has a 95% interval of [−0.35, +0.51] over only 9 distinct drift values (9 domains, not
  8). Read it as "no evidence that drift matters, at n=20 over a 0.24–0.37 range", not "adds nothing".
- "Indirection moves cost more than radius" rests on a big × direct cell of n=2 whose costs fall inside
  the indirect range. It remains a hypothesis only.

**Minor.**
- **M1, timing.** The gate-3 operationalisation was committed 4 s after the first full-run row was
  written; drift does not depend on cost.
- **M3, precision.** `notify_top_up_failed`'s 0.69 is only partly a gold-style question: agents also
  name DI plumbing and a non-existent `TopUpHandler`.
- **M4, answer size.** The small × indirect cell has 1, 6 and 8 required symbols, so it carries the
  answer-size confound too.
- **M5, pilot tuning.** The pilot tasks' gold was corrected from the same model's answers, which biases
  those tasks' scores upward.

### Revised reading

1. On read-only impact questions in owner-api, Sonnet 5.5 is near the recall ceiling once the gold is
   right. Its residual misses are nested functions and spelling, not dynamic dispatch.
2. Cost rises with answer size and radius, which are collinear here. Template fit shows no measurable
   effect, within a narrow range.
3. Indirection may matter, but this corpus cannot say: FastAPI `Depends` makes every long chain
   indirect.
4. Exp2's design follows from these limits: hold radius **and answer size** fixed, vary indirection in
   matched pairs, and derive gold path-sensitively for dynamic dispatch.
