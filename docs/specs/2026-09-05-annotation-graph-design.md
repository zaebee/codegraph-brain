# Annotation-aware graph: receiver resolution (#414) and orphan classes (#415)

Status: approved design, revised after independent fact-check
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

## Measurement basis

All figures below are from `src/cgis/` at HEAD and a graph ingested from it —
**except D10's**, which are from five repositories ingested at `c9f39b8`, because
the effect D10 measures is invisible on cgis. That is not incidental: three of
this spec's findings (#417, #424, D10) do not reproduce on cgis's own source, so
a cgis-only measurement is evidence of nothing for resolver and reference work.

All figures below are from `src/cgis/` at HEAD and a graph ingested from it:

```
uv run cgis ingest src --source-root src --output <scratch>.db
  → 1 563 nodes / 5 011 edges
```

**Do not use the repo-root `graph.db` for these numbers.** It is untracked,
gitignored, and dated 2026-07-29 — the v0.4.0 ingest, 113 commits behind. An
earlier draft of this spec mixed counts from that stale graph with counts from
HEAD source, and every graph-derived figure in it was wrong. Any future
re-measurement must re-ingest first.

| Measurement | Value |
|---|---|
| Total edges | 5 011 |
| `self.*` placeholder edges | 139 |
| …classified `INTERNAL` (i.e. counted as **resolved**) | 139 / 139 |
| …with exactly two segments after `self` (in scope for D8) | 118 |
| …deeper or shallower (out of scope for D8) | 21 |
| `self.<attr>.<method>()` call sites in source | 117 |
| …receiver resolvable from an annotation | 105 (90%) |
| Annotation positions (param / return / AnnAssign) | 2 328 |
| …naming at least one internal CLASS | 577 |
| `REFERENCES` edges emitted (D9 rule) | **583 measured** (predicted 596) |
| Current `unresolved_ratio` | 0.2030 |
| Projected after PR2 | 0.2095 (gate: 0.30) |
| Projected after PR1's edges also land | 0.1873 |

Positions exclude 106 bare `-> None` returns, which name no type.

### Validated on owner-api

The implemented feature was run against `Ownima/owner-api` (`ownima-backend/app`,
15 928 nodes / 65 792 edges, ~11s) — the DI-heavy codebase #414 was filed from:

| Measurement | Value |
|---|---|
| `REFERENCES` edges | 2 004 (0 pointing at a non-CLASS node) |
| classes carrying `self_types` | 677 (2 847 attributes) |
| `self.<attr>.<method>` sites, **production only** | 554 |
| …receiver resolvable *(forecast, before PR2 existed)* | 476 (86%) |
| …**actually resolved once PR2 landed** | **347 (63%)** |
| …same, tests only | 445 / 1 732 (26%) |

That 63% is itself a correction. The first measurement said 85%, and an
adversarial review showed it counted 136 **fabricated** edges as successes:
`classify_fqn` judges by root string, and `external_roots` is built from
import-map values, so a first-party FQN whose import root differs from its
ingest-relative node root classifies EXTERNAL — and D7's library branch then
minted a boundary node for a class of ours that does not exist,
`app.api.dependencies.session.AsyncSessionDep.execute` among them, at confidence
1.0. Receiver resolution now refuses a receiver whose path reaches a project
root; fabrications from this path are 0.

The forecast counted a receiver as resolvable when its name appeared in
`self_types`, without asking whether the declared type is a class that has that
method. On `owner-api` the two nearly coincide, because its receivers are real
injected classes. On this repository they do not: the same method forecast 89%
and the truth is 80%, the gap being attributes declared as builtin containers
(`self._profiles: dict[...]` records `dict`), which name no class. Quote the
measured number, not the forecast.

86% against the 66% #414 predicted from its two-source rule: the two sources this
design adds (`self.x: T = ...` and `self.x = SomeClass()`) are worth ~20 points on
the very repository the issue was filed from. The 26% in tests is 876 unannotated
`self.client` (httpx) fixtures, which the rule correctly declines to guess at —
exactly as #414 anticipated. A figure blending the two reads as 40% and is
meaningless; #414 measured production only, and so must any comparison to it.

**The clearest evidence for D9:** of 367 production classes with no incoming
production `CALLS`/`EXTENDS`, **180 are held alive solely by the new annotation
edges**. Without D9 all 180 would read as dead code.

### Two findings that change the issues as filed

**The placeholder is INTERNAL, so it improves the health verdict.**
`SymbolIndex.classify_fqn` (`resolver/indices.py:91`) returns `INTERNAL` for any
FQN starting with `self.`, and `get_edge_stats` (`storage/sqlite_store.py:511`)
counts `INTERNAL` as resolved. All 139 placeholder edges are counted on the good
side of `unresolved_ratio`. The metric moves the *wrong way* as a codebase adopts
more DI. #414 filed this as "resolved-to-EXTERNAL"; it is worse than that.

**The rule as filed reaches 19%, not 66%.** Attributing each of the 117 call
sites to the construct that would let its receiver resolve:

| attr → type source | sites | in #414 as filed |
|---|---|---|
| `__init__(self, x: T)` + `self.x = x` | 17 | yes |
| class-body annotation `x: T` | 5 | yes |
| `self.x: T = ...` (annotated assignment) | 68 | **no** |
| `self.x = SomeClass(...)` (constructor assignment) | 15 | **no** |
| **total** | **105 / 117 (90%)** | 22 / 117 (19%) |

One site carries both an `__init__` parameter and a class-body annotation; it is
counted once, under class-body. The 66% reported on `owner-api` reflects that
repo's DI style. On code that annotates attributes directly, the dominant
construct is the annotated assignment, which the proposed rule does not mention.

## Decisions

### D1 — `attr → type` map lives in CLASS node metadata as `self_types`

Exact mirror of `local_types`, which already lives in FUNCTION/METHOD node
metadata and is already read by `_resolve_local_type_call`
(`resolver/symbols.py:147`). No new index, no new storage shape.

For a generic attribute (`items: list[Node]`), `self_types` records the
*container*, not the argument — `{"items": "pkg.mod.list"}` — because
`local_types` semantics (what type is this attribute for method-resolution
purposes) are correct for it, unlike D9's annotation-edge collection below.

### D2 — annotation edges reuse `REFERENCES`; no new EdgeType

`REFERENCES` already exists and already carries a property this work needs: it is
deliberately excluded from `_ENFORCEMENT_EDGE_TYPES` (`query/context/audit.py:31`),
documented as protection against a false "covered" verdict in an authz audit.

A new EdgeType inherits nothing. `BEHAVIORAL_EDGE_TYPES` (`query/engine.py:8-11`)
is defined as *everything that is not structural*, so any new type silently joins
every default traversal, and each consumer would have to be re-audited to restore
the safety `REFERENCES` already has.

**Drift exposure, stated precisely.** The fingerprint's *censuses* read only
`IMPORTS` and `CALLS` (`query/drift/fingerprint.py:331-332`), and `quotient.py`
and `fractal.py` likewise — so `t_imports`/`t_calls` cannot move. But
`edge_count = len(internal_edges)` (`fingerprint.py:346`) counts **every** edge
type, and `drift.py:365` returns `status="no_signal"` when it is zero. A
`REFERENCES` edge can therefore flip a domain from `no_signal` to scored. No
realistic domain is exposed (anything with a file and a child already has
`CONTAINS` edges), but "cannot move a drift baseline" is stronger than the code
guarantees, and PR1 must check the self-drift status column rather than assume.

### D3 — emit an annotation edge only when the type resolves to an INTERNAL node

Stdlib, external, and unresolved annotations are dropped. Edging every position
regardless of namespace would add 2 328 edges (+46%) of `str`/`dict`/library
noise and swamp every metric; an `x: str` annotation says nothing about orphan
classes. Scoped to internal classes it is 583 edges (+11.6%), measured on the
implemented feature; the 596 in an earlier draft was an AST prediction.

### D4 — annotation positions: parameter, return, `AnnAssign`

Parameters already traverse most of the path: `collect_param_type`
(`extractors/_python_functions.py:267`) emits `raw_dep:<Type>`, and
`_resolved_dep_edge` (`resolver/engine.py:103`) drops it unless it resolves to a
VARIABLE. The resolution branch is an extension, not a new mechanism:

```
raw_dep:<T>  →  VARIABLE (DI alias)   →  DEPENDS_ON   (unchanged)
             →  internal CLASS        →  REFERENCES   (new)
             →  anything else         →  dropped      (unchanged)
```

Return and `AnnAssign` positions reuse that road with new emissions. **What they
cannot reuse is the type-name extraction — see D9.**

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
identical in every column but source and location. #415's own table shows that
without this filter all six of its rows read as live, including both real orphans.

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

In `_resolved_call_edge` (`resolver/engine.py:84`), the `0.8` at line 100 is the
value assigned on resolution *failure*. Reusing it for an annotation-based
success would make the two indistinguishable on the column that `min_confidence`
traversal filters read.

### D7 — reuse the existing hierarchy walk and phantom-method policy

`_resolve_method_on_class_hierarchy` (`resolver/symbols.py:115`) already walks
EXTENDS with cycle protection. `_resolve_local_type_call` (`symbols.py:147`)
already implements the policy #414 needs for its motivating bug: a method that
does not exist is kept only when the receiver's type is EXTERNAL or STDLIB, and
dropped for internal *and unknown* types rather than fabricated. Routing
`self.<attr>.<method>` through that path is what makes a
deleted-`# type: ignore[attr-defined]`-shaped call visible, and it needs no new
policy.

### D8 — receiver resolution covers exactly two segments after `self`

`self.<attr>.<method>` only — 118 of the 139 placeholder edges. The remaining 21
are deeper chains (`self._conn.execute(...).fetchall()`, which the extractor
flattens to target `self._conn.execute.fetchall`) or bare `self._pick_source_root`
attribute calls.

**Those 21 do not keep their current behavior.** PR2's `classify_fqn` split (see
PR2 below) reclassifies *every* `self.`-prefixed FQN, so all 21 flip from
counting as resolved to counting as unresolved, even though no resolution is
attempted for them. That is the honest accounting — they were never resolved —
but it is a change, and the projected ratio above includes it: 12 unresolvable
two-segment sites plus 21 out-of-scope placeholders, `(1017+33)/5011 = 0.2095`.

### D9 — an annotation names every internal class inside it, not its cleaned head

`clean_python_type_string` (`extractors/_python_types.py:44`) reduces `list[Node]`
to `list`: it answers "what type is this variable" — correct for `local_types`,
where the receiver of `x.append()` really is a `list`. It is the wrong question
for a reference edge.

Measured on HEAD: of 577 annotation positions naming an internal class, that
function surfaces the class in only 247. **330 (57%) are swallowed by a generic
wrapper** — `list[...]` (211), `tuple[...]` (33), `dict[...]` (30), `frozenset`
(16), `Sequence` (15) and others. Eleven internal classes are referenced *only*
from inside generics and would receive zero annotation edges:

```
AmbiguousEntry, ArchitecturalAnomaly, Bridge, Community, DuckDBAnalyzer,
GoldenComment, NodeMetric, PrPlan, SliceCounts, _IdAllocator
```

(An earlier draft listed `UnionRun` here too. That was wrong: its only
external-looking mention is the self-referencing return annotation described
above, so it is a genuine orphan candidate rather than a D9 rescue.)

Any of those that is also constructed only from tests would be reported as a
false orphan by #415 — the exact failure the annotation edge exists to prevent.

So annotation extraction collects **all** type names appearing in the annotation
(walking `Name`/`Attribute` nodes, and parsing string annotations), emitting one
`REFERENCES` edge per distinct internal class. `clean_python_type_string` is left
untouched and keeps serving `local_types`. This is what raises the edge count
from 247 to 583.

**Two exclusions, discovered during implementation and not in the original
design.** "Every name in the annotation" is too literal on its own:

* **Typing wrappers unwrap rather than count.** `Optional`, `Union` and
  `Annotated` are not classes anyone would reference, so they are dropped while
  their arguments are kept — `Optional[Node]` yields `Node`, not both. Qualified
  forms (`typing.Optional[X]`) unwrap identically; the module prefix is stripped
  before the wrapper test, matching `clean_python_type_string`. Note that
  tree-sitter parses `Optional[X]` as `generic_type` but `typing.Optional[X]` as
  `subscript`, so both shapes must be handled — an early implementation handled
  only the first and leaked `raw_dep:typing.Optional`.
* **Call nodes are skipped.** In `Annotated[Session, Depends(get_db)]` the second
  argument is metadata, not a type; collecting it would emit candidates for
  `Depends` and `get_db`. The annotation yields `Session` alone.

Ordinary containers are NOT wrappers and keep both parts: `list[Node]` yields
`list` and `Node`, which is the entire point of D9. A dotted non-wrapper base is
preserved whole — `collections.abc.Sequence[Node]` yields both.

**A self-reference is not a reference.** `-> "UnionRun"` on `UnionRun`'s own
classmethod produces no incoming edge for `UnionRun`. This is implemented, not
emergent: `_resolved_dep_edge` (`resolver/engine.py`) drops the candidate when
`edge.source` equals the resolved class target or lives inside it (a method,
or a nested class — checked as `edge.source.startswith(f"{class_target}.")`,
a dot-boundary test so `Foo` is not treated as inside `FooBar`). A class
naming itself is not evidence anyone uses it, and counting it would
manufacture a false negative in the orphan query.

### D10 — a class named in a load position is a reference, whatever the position

PR3's mandate was to "measure the `response_model=` precision gap (D4) and decide
it here". The measurement was taken on five codebases and found a *different*
gap, several times larger. This decision is the answer to it.

**The measurement.** A prototype of the orphan query — internal production CLASS
nodes with no incoming `CALLS`/`EXTENDS`/`REFERENCES` from a non-test source —
was run on five graphs ingested at `c9f39b8`. Each reported orphan was then
checked against the ground truth #415's own reference implementation uses: a bare
name load of that class, in a production file that imports it, outside its own
module.

| repo | internal classes | orphans reported | of those, alive | rate |
|---|---|---|---|---|
| cgis | 93 | 2 | 0 | 0% |
| owner-api | 1 789 | 109 | 44 | **40%** |
| memory-facets | 383 | 18 | 6 | **33%** |
| aura-core | 124 | 18 | 6 | **33%** |
| rich (library) | 181 | 31 | 1 | 3% |

Three unrelated applications agree at a third to two-fifths wrong. A mature
library and cgis itself sit near zero, and the reason is not that they are
better written: **application code hands classes to a framework, library code
constructs them.** `app.add_middleware(SecurityMiddleware)` names a class and
never calls it. This is the third feature in this spec that cgis's own source
cannot exercise, after star imports (#417) and root-prefix mismatch (#424); a
measurement taken only on cgis would have reported the query ready.

The `response_model=` gap D4 predicted turns out to be two occurrences. The
positions that actually matter, pooled across all five repos (57 false positives):

| shape | n | example |
|---|---|---|
| attribute head — enum member, classmethod | 25 | `TransactionType.TOP_UP`, `Rule.stats()` |
| handed to a call as a value | 18 | `add_middleware(X)`, `route_class=X` |
| `except` clause | 5 | `except ConversationNotFoundError:` |
| collection literal | 3 | `("fuel_types", FuelType)` |
| other — `BinOp`, return annotation, `arg` | 6 | |

**The decision: one rule, not five.** Every row above is the same thing — a name
that resolves to an internal class, appearing in a load position. Enumerating
positions would take more code, draw arbitrary boundaries, and still miss the
six-item tail. So the extractor emits a `raw_dep:<name>` candidate for an
`identifier` in a load position when the name is either in the module's
`import_map` or the name of a class defined in that file.

Two positions are excluded because an edge already exists for them: the
`function` field of a call (that is `CALLS`) and the member half of an attribute
(`TOP_UP` in `TransactionType.TOP_UP` is not a class). Import statements are
already unreachable — `_walk` returns on them before recursing.

Everything else in the pipeline is reused unchanged. The source is the nearest
owner — enclosing function, else class, else module — which is the rule #416
settled. `_resolved_dep_edge` already drops any candidate that does not resolve
to an internal CLASS node (D3), so a name that is a function, a variable or a
third-party symbol never reaches the graph. The edge type is `REFERENCES` (D2)
at confidence 0.9 (D6), and the self-reference drop in D9 applies unchanged.

**Cost — stated here so it is not discovered in review.** Counting distinct
`(owner, class)` pairs, which is what dedupes into edges:

| repo | new edges | existing `REFERENCES` | total edges | growth |
|---|---|---|---|---|
| cgis | 694 | 592 | 5 911 | 12% |
| owner-api | 3 626 | 2 793 | 70 334 | 5% |
| memory-facets | 1 651 | 1 182 | 12 773 | 13% |
| aura-core | 221 | 88 | 4 312 | 5% |
| rich | 954 | 652 | 7 519 | 13% |

`REFERENCES` roughly doubles; the whole graph grows 5–13%. These are upper
bounds — a pair that already has a `CALLS` or annotation edge dedupes away.
`audit_reachability` is unaffected: it traverses enforcement edges only.
`impact` and `trace_flow` apply no edge-type filter and will widen
correspondingly, several times more than the PR1 risk already noted.

**Measured outcome.** Implemented and re-run over the same five graphs:

| repo | orphans before | false | orphans after | false | edge growth |
|---|---|---|---|---|---|
| cgis | 2 | 0 (0%) | 2 | 0 (0%) | 11% |
| owner-api | 109 | 44 (40%) | 45 | 7 (16%) | 10% |
| memory-facets | 18 | 6 (33%) | 9 | 0 (0%) | 11% |
| aura-core | 18 | 6 (33%) | 9 | 0 (0%) | 3% |
| rich | 31 | 1 (3%) | 14 | 0 (0%) | 9% |

**All seven of owner-api's residuals are the ground truth being wrong, not the
rule.** Each is a short-name collision the AST sweep cannot resolve and cgis
can: `domains.rating.calculator.UserMetrics` is reported dead while
`domains/admin/users.py` names `UserMetrics` — but that file imports
`app.domains.admin.schemas.UserMetrics`, a different class, and nothing imports
the calculator's. Same for `CancelReservationResponse`,
`CreateReservationRequest` (both shadowed by `grpc.services`), two `Rule`
classes, a `Config`, and a `Message`. Hand-checked one by one; cgis is right in
all seven. #415's reference implementation names this limitation in its own
docstring — "a method sharing a name with a live one elsewhere hides in that
one's shadow" — and resolving through the import map is precisely what a graph
buys over a name sweep.

So the honest statement of the result is: the false-positive rate is 0% on every
repository, and on owner-api the FQN resolution is *more* precise than the sweep
this feature was measured against.

The orphan counts also fall further than the false-positive count alone explains
(owner-api 109 → 45, not 109 → 65). The extra 20 are same-module uses the ground
truth excluded by construction and the rule catches: `register_message("entities",
"Garage", Garage)`, `Dep = Annotated[TransactionWalletMonthlyFilter, Query()]`,
`{"model": BaseValidationError}`. All real. Spot-checked.

Edge growth landed at 3–11%, at the low end of the 5–13% predicted, because
edges dedupe by `(owner, name)`.

**What it cannot do.** The rule is name-based, so a class mentioned only inside
code that is itself dead reads as live. #415's reference implementation carries
the same limitation and states it plainly: it under-reports and never
over-reports. For a check whose whole value is that people trust it, a false
"live" is the cheap direction.

## Work breakdown

Three sequential PRs. Each merges before the next starts.

### PR1 — extractor foundation

* `self_types` on CLASS node metadata, from all four sources in the table above.
* Type-name collection per D9, separate from `clean_python_type_string`.
* `raw_dep:` emission extended to return and `AnnAssign` positions.
* `_resolved_dep_edge` gains the internal-CLASS → `REFERENCES` branch (D4).
* Check the self-drift `status` column for any domain moving off `no_signal` (D2).

Serves both issues; neither can be precise without it.

### PR2 — #414 receiver resolution

* `resolve_self_call` handles the dotted form: split `client.search` into
  receiver and method, look up `self_types`, then delegate to the existing
  hierarchy walk (D7).
* `classify_fqn` stops reporting `self.`-prefixed FQNs as `INTERNAL`. The
  leading-`.` (relative import) branch is unchanged; the two currently share one
  condition (`indices.py:91`) and must be split.
* Fixes the placeholder collision as a consequence: two unrelated classes with a
  same-named attribute no longer share one vertex.

### PR3a — the name-as-value reference edge (D10)

* `raw_dep:` emission for an `identifier` in a load position, gated on the
  module's `import_map` or a class defined in the same file.
* Exclusions: a call's own `function` field, the member half of an attribute.
* Re-measure the orphan query's false-positive rate on all five repos of D10;
  the acceptance number is that rate, not an edge count.
* State the `impact` / `trace_flow` density change in the PR.

The measurement that motivated this split is D10. The original PR3 owed a
decision on the `response_model=` gap; taking that measurement found a larger
one, and shipping the query on top of it would have meant a check that is wrong
a third of the time on every application codebase tested.

### PR3b — #415 orphan query

* `is_test_path()` + `Node.is_test` + column migration (D5).
* Orphans query: internal CLASS nodes with no incoming `CALLS`, `EXTENDS` or
  `REFERENCES` from a non-test source. `IMPORTS_SYMBOL` excluded.
* Surfaced as a CLI command and an MCP tool, following the existing
  `audit_reachability` shape.
* Report the residual false-positive rate measured in PR3a.

## Testing

Every assertion below must be able to fail. The paired "would fail if" is part of
the test, not commentary — a self-parsing assertion that merely counts nodes
passes whether or not the feature works. Counts are calibrated against HEAD and
must be re-derived from a fresh ingest, never from the stale root `graph.db`.

| Test | Would fail if |
|---|---|
| Fixture: `VehicleAdapter.search` resolves to `SearchClient.search` | receiver resolution regresses to a placeholder |
| Fixture: `self.client.search_available_vehicles` resolves to nothing and is dropped | the phantom-method policy (D7) stops applying to `self.` receivers |
| Fixture: two classes with a same-named attribute produce two distinct targets | the placeholder collision returns |
| Self-parse: `self.*` placeholder edges drop 139 → ≤ 33 | any of the four `self_types` sources stops being collected |
| Self-parse: `unresolved_ratio` stays under the 0.30 gate | the `classify_fqn` split miscounts |
| Self-parse: `REFERENCES` edge count is 583 ± 60 | D3's internal-only filter or D9's collection breaks in either direction |
| Fixture: a class referenced only as `list[OnlyInGeneric]` is **not** an orphan | D9 regresses to `clean_python_type_string` |
| Fixture: a class constructed only from `tests/` is reported as an orphan | the `is_test` filter stops being applied |
| Fixture: a class referenced only by a parameter annotation is **not** an orphan | the annotation edge stops being emitted |
| Fixture: a class only ever `except`-ed is **not** an orphan | D10's load-position rule stops covering `except` |
| Fixture: a class only handed to a call (`add_middleware(X)`) is **not** an orphan | D10 regresses to counting constructions |
| Fixture: an enum named only as `E.MEMBER` is **not** an orphan | the attribute head stops being collected |
| Fixture: a *function* handed to a call emits no `REFERENCES` | D3's internal-CLASS filter stops applying to D10 candidates |
| Five-repo sweep: orphan false-positive rate is 0 on the D10 set | any load position stops being covered |

The last two are the pair #415 identifies as load-bearing: without the first all
six of its rows read as live, and without the second every abstract port reads as
dead. The `list[...]` case is the one this spec's fact-check added — it fails
against the design as originally written.

## Risks

* **`classify_fqn` split (PR2).** Changing what counts as INTERNAL touches
  virtual-node creation and every namespace-filtered query, and moves 21 edges
  that D8 does not otherwise address. The `.`-prefixed relative-import case
  shares the condition and must keep its current behavior. Verify each
  `NodeNamespace.INTERNAL` consumer before the split.
* **Traversal density (PR1, and several times more in PR3a).** `impact` and
  `trace_flow` apply no edge-type filter by default, so PR1's +583 `REFERENCES`
  edges widened their results, and D10 roughly doubles `REFERENCES` again on top
  (5–13% of the whole graph, per repo — see D10's cost table). Arguably correct:
  naming a class is a real dependency. But it changes existing output, and each
  PR must state the movement rather than let a consumer discover it.
* **Response-schema precision (PR3).** Predicted by D4 as the query's main
  precision limit. Measured in D10: **two occurrences across five repositories.**
  The real limit was a different one — a class named as a value — which D10
  addresses. Recorded here because the prediction being wrong is the reason PR3
  was split, and a reader of D4 should not still expect `response_model=` to be
  the problem.
* **`self_types` is wrong in two rare shapes — PR2 reads this map, so its author
  must know.** Both were found by an adversarial pass over the extractor's
  heuristics and both measure **zero occurrences** across this repository and
  `owner-api`, which is why they are recorded rather than fixed:
  - An unannotated `self.x = name` in a method other than `__init__` takes its
    type from `__init__`'s parameter of the same name, even when the enclosing
    method annotates it differently — the right answer is sitting in that
    method's own `local_types`. The same lookup is order-dependent: a method
    written above `__init__` sees an empty map. Wrongly *accepted* in the first
    half, wrongly rejected in the second.
  - The constructor heuristic accepts `self.x = HANDLERS[key]()` (the subscript's
    container name is ALL-CAPS and passes the capitalisation test) and
    `self.x = Registry.Instance()` (an uppercase-named classmethod on an imported
    class). It rejects a caseless-script class name (`类`) and a parenthesised
    construction `(Widget())`. The first two write a phantom type; `local_types`
    and `self_types` disagree on the third.
* **Measurement basis.** Every number here has a five-week-stale twin in the root
  `graph.db`. Re-ingest before re-measuring.
* **Module-level construction — [#416](https://github.com/zaebee/codegraph-brain/issues/416), CLOSED (`c9f39b8`).**
  A constructor called at module level emitted no `CALLS` edge at all, so a class
  built only in a registry dict, a singleton, or DI wiring read as dead. Fixed by
  attributing such a call to its nearest owner. On owner-api it rescued 32 classes
  the query would have reported dead.

* **Calls inside a decorator expression are still dropped — [#429](https://github.com/zaebee/codegraph-brain/issues/429), OPEN.**
  `_walk` hands a `decorated_definition` off and never descends into the
  decorators, so `@router.post(..., dependencies=[Depends(guard)])` leaves the
  endpoint with no `DEPENDS_ON` and `cgis audit` reads a superuser-only route as
  unguarded. It does not affect the orphan query — D10's rule covers a class
  *named* in a decorator through the same `import_map` gate — but it is the
  remaining hole in "a call outside a function is still a call".
