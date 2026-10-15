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
