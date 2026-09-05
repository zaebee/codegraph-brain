# Annotation-aware graph: receiver resolution (#414) and orphan classes (#415)

Status: approved design, not yet implemented
Issues: [#414](https://github.com/zaebee/codegraph-brain/issues/414), [#415](https://github.com/zaebee/codegraph-brain/issues/415)

## Problem

Two reports, one missing capability underneath both: the graph does not record
what a name's *type annotation* says.

**#414** — a call through an injected collaborator (`self.client.search(...)`)
never resolves. The edge lands on a placeholder node named `self.client.search`,
so a dependency-injected layer reads as calling nothing.

**#415** — there is no query for "classes nothing constructs or extends", and it
cannot be precise without an annotation edge: an abstract port that three modules
type against but nobody constructs looks identical to dead code.

## Measurements

All figures from `src/cgis/` and its own `graph.db` (3 973 edges), reproduced
during design.

| Measurement | Value |
|---|---|
| `self.<attr>.<method>` placeholder edges | 134 |
| …classified `INTERNAL` (i.e. counted as **resolved**) | 134 / 134 |
| `self.<attr>.<method>` call sites in source | 117 |
| …receiver resolvable from an annotation | 105 (90%) |
| Annotation positions (param / return / AnnAssign) | 2 328 |
| …whose type resolves to an internal CLASS | 233 (+5.9% edges) |
| Current whole-graph `unresolved_ratio` | 0.2157 |
| Projected after #414 | ~0.2187 (gate: 0.30) |

### Two findings that change the issues as filed

**The placeholder is INTERNAL, so it improves the health verdict.**
`SymbolIndex.classify_fqn` (`resolver/indices.py:96`) returns `INTERNAL` for any
FQN starting with `self.`, and `get_edge_stats` (`storage/sqlite_store.py:511`)
counts `INTERNAL` as resolved. The 134 edges are not merely uncounted by
`unresolved_ratio` — they are counted on the good side of it. The metric moves
the *wrong way* as a codebase adopts more DI. #414 filed this as
"resolved-to-EXTERNAL"; it is worse than that.

**The rule as filed reaches 19%, not 66%.** Attributing each of the 117 call
sites to the construct that would let its receiver resolve:

| attr → type source | sites | in #414 as filed |
|---|---|---|
| `__init__(self, x: T)` + `self.x = x` | 17 | yes |
| class-body annotation `x: T` | 5 | yes |
| `self.x: T = ...` (annotated assignment) | 68 | **no** |
| `self.x = SomeClass(...)` (constructor assignment) | 15 | **no** |
| **total** | **105 / 117 (90%)** | 22 / 117 (19%) |

The 66% reported on `owner-api` reflects that repo's DI style. On code that
annotates attributes directly, the dominant construct is the annotated
assignment, which the proposed rule does not mention.

## Decisions

### D1 — `attr → type` map lives in CLASS node metadata as `self_types`

Exact mirror of `local_types`, which already lives in FUNCTION/METHOD node
metadata and is already read by `_resolve_local_type_call`
(`resolver/symbols.py:145`). No new index, no new storage shape.

### D2 — annotation edges reuse `REFERENCES`; no new EdgeType

`REFERENCES` already exists and already carries two properties this work needs:

* it is deliberately excluded from `_ENFORCEMENT_EDGE_TYPES` in
  `query/context/audit.py`, documented as protection against a false "covered"
  verdict in an authz audit;
* the drift fingerprint counts only `IMPORTS` and `CALLS`
  (`query/drift/fingerprint.py:331-332`), so adding these edges cannot move a
  drift baseline.

A new EdgeType inherits neither. `BEHAVIORAL_EDGE_TYPES` (`query/engine.py:9`)
is defined as *everything that is not structural*, so any new type silently
joins every default traversal, and each consumer would have to be re-audited to
restore the safety `REFERENCES` already has.

### D3 — emit an annotation edge only when the type resolves to an INTERNAL node

Stdlib, external, and unresolved annotations are dropped. This is the difference
between +233 edges (+5.9%) and +2 328 (+58.6%); an `x: str` annotation says
nothing about orphan classes, and edging everything would swamp every metric.

### D4 — annotation positions: parameter, return, `AnnAssign`

Parameters already traverse the whole path: `collect_param_type`
(`extractors/_python_functions.py:266`) emits `raw_dep:<Type>`, and
`_resolved_dep_edge` (`resolver/engine.py:110`) drops it unless it resolves to a
VARIABLE. The change is a second branch, not a new mechanism:

```
raw_dep:<T>  →  VARIABLE (DI alias)   →  DEPENDS_ON   (unchanged)
             →  internal CLASS        →  REFERENCES   (new)
             →  anything else         →  dropped      (unchanged)
```

Return and `AnnAssign` positions then reuse that same road with new `raw_dep:`
emissions.

**Deliberately excluded: `response_model=SomeSchema`.** That is a value in a call
keyword, not an annotation — a different extraction path. Request schemas are
caught as parameter annotations; **response schemas will not be**. #415 reports
that 11 of its 14 `app/domains` findings are request/response schemas, so this
leaves a known precision gap on the response half. It is measured in PR3, where
the false positives it causes are observable, rather than guessed at now.

### D5 — `is_test` is a column, populated at ingest from one `is_test_path()` helper

cgis has no notion of a test source today. `_TEST_FILE_PATTERN`
(`pipeline.py:243`) matches only `foo.test.py` — a JS convention — while
`tests/`, `test_*.py` and `conftest.py` are ingested as ordinary code. Verified
on a fixture: a test's construction of a class becomes an ordinary `CALLS` edge,
indistinguishable from production. #415's own table shows that without this
filter all six rows read as live, including both real orphans.

A column follows the `namespace` precedent exactly, including the `ALTER TABLE`
path in `_migrate()` (`storage/sqlite_store.py:113`). The decision itself lives in
a single `is_test_path()` function so that "what is a test" has one definition;
the column is its cached result, visible to the schema and filterable in SQL.

Patterns are hardcoded: any path segment named `tests`, plus `test_*.py`,
`*_test.py`, `conftest.py`. That covers both repositories that exist —
`codegraph-brain/tests/` and `owner-api/app/tests/_shared/`. Configuration is
deferred until a third layout gives it something concrete to express.

**Scope limit, deliberate.** The existing `_get_extractor` drop of `.test.py` /
`.test.ts` files is left alone. Marking Python tests is purely additive — no node
appears or disappears, so every existing metric is byte-identical and a drift
movement cannot be blamed on this work. Unifying the drop would add nodes to the
`ui/` TypeScript graph and move its fingerprint in the same diff, making both
changes unmeasurable. The inconsistency that remains (Python tests marked, JS
tests dropped) gets an explanatory comment at `_TEST_FILE_PATTERN` and a
follow-up issue — it is not left silent.

### D6 — resolution confidence 0.9, not 0.8

In `_resolved_call_edge` (`resolver/engine.py:99`), `0.8` is the value assigned on
resolution *failure*. Reusing it for an annotation-based success would make the
two indistinguishable on the column that `min_confidence` traversal filters read.

### D7 — reuse the existing hierarchy walk and phantom-method policy

`_resolve_method_on_class_hierarchy` (`resolver/symbols.py:113`) already walks
EXTENDS with cycle protection. `_resolve_local_type_call` already implements the
policy #414 needs for its motivating bug: a method that does not exist on an
*internal* receiver is dropped rather than fabricated, while a real library call
is kept. Routing `self.<attr>.<method>` through that path is what makes a
deleted-`# type: ignore[attr-defined]`-shaped call visible, and it needs no new
policy.

### D8 — scope: exactly two segments after `self`

`self.<attr>.<method>` only. Deeper chains (`self._conn.execute.fetchall`, 7 of
the 134) behave exactly as they do today.

## Work breakdown

Three sequential PRs. Each merges before the next starts.

### PR1 — extractor foundation

* `self_types` on CLASS node metadata, from all four sources in the table above.
* `raw_dep:` emission extended to return and `AnnAssign` positions.
* `_resolved_dep_edge` gains the internal-CLASS → `REFERENCES` branch (D4).

Serves both issues; neither can be precise without it.

### PR2 — #414 receiver resolution

* `resolve_self_call` handles the dotted form: split `client.search` into
  receiver and method, look up `self_types`, then delegate to the existing
  hierarchy walk (D7).
* `classify_fqn` stops reporting `self.`-prefixed FQNs as `INTERNAL`. The
  leading-`.` (relative import) branch is unchanged; the two are currently
  merged in one condition and must be split.
* Fixes the placeholder collision as a consequence: two unrelated classes with a
  same-named attribute no longer share one vertex.

### PR3 — #415 orphan query

* `is_test_path()` + `Node.is_test` + column migration (D5).
* Orphans query: internal CLASS nodes with no incoming `CALLS` or `EXTENDS` from
  a non-test source. `IMPORTS_SYMBOL` excluded.
* Surfaced as a CLI command and an MCP tool, following the existing
  `audit_reachability` shape.
* Measure the `response_model=` precision gap (D4) and decide it here.

## Testing

Every assertion below must be able to fail. The paired "would fail if" is part of
the test, not commentary — a self-parsing assertion that merely counts nodes
passes whether or not the feature works.

| Test | Would fail if |
|---|---|
| Fixture: `VehicleAdapter.search` resolves to `SearchClient.search` | receiver resolution regresses to a placeholder |
| Fixture: `self.client.search_available_vehicles` resolves to nothing and is dropped | the phantom-method policy (D7) stops applying to `self.` receivers |
| Fixture: two classes with a same-named attribute produce two distinct targets | the placeholder collision returns |
| Self-parse: count of `self.*` placeholder edges drops 134 → ≤ 12 | any of the four `self_types` sources stops being collected |
| Self-parse: `unresolved_ratio` stays under the 0.30 gate | the `classify_fqn` split miscounts |
| Self-parse: `REFERENCES` edge count is 233 ± tolerance | D3's internal-only filter breaks in either direction |
| Fixture: a class constructed only from `tests/` is reported as an orphan | the `is_test` filter stops being applied |
| Fixture: a class referenced only by a parameter annotation is **not** an orphan | the annotation edge stops being emitted |

The last two are the pair #415 identifies as load-bearing: without the first all
six of its rows read as live, and without the second every abstract port reads as
dead.

## Risks

* **`classify_fqn` split (PR2).** Changing what counts as INTERNAL touches
  virtual-node creation and every namespace-filtered query. The `.`-prefixed
  relative-import case shares the condition and must keep its current behavior.
  Verify each `NodeNamespace.INTERNAL` consumer before the split.
* **Traversal density (PR1).** `impact` and `trace_flow` default to no edge-type
  filter, so +233 `REFERENCES` edges will widen their results. Arguably correct —
  an annotation is a real dependency — but it changes existing output and should
  be stated in the PR, not discovered.
* **Response-schema precision (PR3).** Known and deliberate (D4). PR3 must report
  the measured false-positive rate rather than ship silently.
