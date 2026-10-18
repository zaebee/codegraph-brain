# Python function values: CV-1, CV-2 and own invokers (CV-4) — design

Date: 2026-10-18. Status: proposed (revision 2, after an independent design
review). Addresses #632 gap 1; turns the three `xfail` Python cases in
`tests/fixtures/conformance/python/` into `pass`.

## Why

Go got edges for function values in #668 (clauses CV-1, CV-2, CV-3 in
`docs/specs/core/ontology.yml`); a held-out check measured 21 of 21 (#695).
Python has none. The mechanism is already language-neutral: an extractor emits
a raw edge whose context is `value-arg:<pos>:<site>` (CV-1) or `value-local:`
(CV-2), and `resolver/engine.py::_value_call_edge` keeps it only when the value
resolves to a real function or method and, for CV-1, the call resolves to an
invoker the pack lists at that position.

Three things keep Python out:

1. **Python passes values by keyword.** `threading.Thread(target=self.run)`,
   `sorted(xs, key=f)`. `ResolutionRules.invokers` holds positions only.
2. **Builtins do not resolve.** `map`, `sorted`, `filter` resolve to None in
   `resolve_global_call`; the call edge gets its bare-name target only from
   `_resolved_call_edge`'s substitution (engine.py). `_value_call_edge` drops a
   value edge whose invoker resolves to None (engine.py:62-64), so a profile
   keyed by builtins could never match.
3. **The measured Python miss is not a library invoker.** Exp1 (#632) broke on
   owner-api at `self._execute_with_metrics("check_conflicts",
   self._check_conflicts_internal, ...)`: an internal method calls its own
   parameter `fn()`. No profile list can name every such function.

## Clauses (`docs/specs/core/ontology.yml`)

**CV-1, amended.** An invoker invokes values at positions *or keyword names*.
The profile lists both: `"threading.Thread": frozenset({1, "target"})`. Go
lists positions only and is unchanged.

**CV-4, new: an own invoker.** A function or method I in the graph whose body
calls its own parameter p (`p()`, `await p()`, `p(*a, **k)`) is an invoker of
p: by p's position and by p's name. When P passes function value F there, P
calls F. Edge: `{type: CALLS, source: P, target: F, confidence: 1.0,
context: "via <I>"}`. The edge never starts at I: that would make every caller
of `_execute_with_metrics` reach every callback.

Positions of a method count after its first parameter, whatever its name,
unless it is a `@staticmethod`, which counts from 0. A keyword-only parameter
(after `*` or `*args`) is invoked by name only, a positional-only one (before
`/`) by position only. A parameter rebound in I's body before it is called
(`def f(cb): cb = g; cb()`) makes I no invoker of it.

Known limits (CV-4):
- A parameter passed on rather than called (`def run(fn): pool.submit(fn)`)
  makes `run` no invoker: one level only. Fixture `cv4-forwarded-param`, xfail.
- A parameter stored (`self._cb = fn`) is CV-3: no edge, by design.
- A method invoked through its class (`C.m(obj, name, fn)`) shifts positions
  by one; the value at the shifted position is usually not a function, which
  the resolver drops, so the effect is a missed edge, rarely a wrong one.
- A call-site argument after a `*xs` splat has no position and is skipped.
- A default-argument callable (`def f(cb=g): cb()`) gives no edge to `g`.
- A property passed as a value (`target=self.hook`) resolves to its getter
  METHOD and gets an edge to it.

The value must be a FUNCTION or METHOD node (`_CALLABLE`, engine.py): a class
passed as a value (`Thread(target=Worker)`) gets no edge.

**CV-2 for Python** follows the Go clause text: a local bound to a function
value (`cb = self.handle`, `for cb in (self.a, self.b)`) and called in the same
function is a call by that function. A rebinding in the same block replaces
the value; one inside an `if`/`for`/`while`/`try`/`with` body adds to it. The
Go known limits (single pass, closures read where written) apply as written.
The plain `raw_call:cb` edge such a call already emits stays as it is; CV-2
adds edges, it does not suppress that one.

**CV-3** is unchanged and already passes for Python (`cv3-registry`).

## Core changes

- `cgis.core.resolution.ResolutionRules.invokers`:
  `Mapping[str, frozenset[int | str]]`. An `int` is a 0-based position, a
  `str` a keyword name. Go's profile type-checks unchanged.
- `VALUE_ARG_CONTEXT` token: `value-arg:<slot>:<site>`, where `<slot>` is a
  decimal position or a Python identifier (a keyword). `_value_call_edge`
  parses `isdecimal()` as `int`, `isidentifier()` as `str`, anything else is
  malformed (edge dropped, as today).
- **An unresolved invoker.** When the invoker call resolves to None and its
  raw name has no dot, the raw name itself is looked up in `rules.invokers`.
  A project function named `map` resolves to itself and never reaches this
  step, so a shadowed builtin is not taken for the builtin.
- **Own invokers.** `_value_call_edge` takes the invoker's slots from
  `rules.invokers`, or else from the resolved invoker node's
  `metadata["invokes"]` (a JSON list of ints and strings, which survives the
  store's JSON round trip on an incremental run). Language-neutral: Go may fill
  the same field later. A value edge whose invoker has neither is dropped.
- A dropped candidate never reaches storage (engine.py: "a candidate that is
  no call leaves no trace"); it is held in memory during resolution and is
  counted in the CLI's `Edges extracted (raw)` line.

## Python extractor

- **Value forms** an argument or a bound local may name:
  - a bare identifier, **only** when it names a `def` or `class` defined at
    module level in the same file, or an imported name, and is not shadowed
    by a parameter or local of the enclosing function; or when it is a local
    tracked by CV-2, in which case it passes what it holds, not its own name
    (as Go does, `go/extractor.py`, #686). A parameter, or a local holding no
    function value, is never passed under its own name: the resolver's
    global-symbol fallback would bind `handler` the parameter to a project
    function `handler` (symbols.py `_resolve_via_global_symbols`);
  - `self.m`;
  - a dotted name whose head is an import or a module-level class (`mod.f`,
    `C.m`);
  - `functools.partial(f, ...)` / `partial(f, ...)`, which names `f` by the
    same rules.
  Anything else (a literal, another call, a subscript, a lambda, `cls.m`) is
  not a value candidate. `cls.m` is a known limit: nothing in the Python pack
  resolves `cls.` today. A lambda's body calls are attributed to the
  enclosing function already.
- **CV-1/CV-4 candidates:** every call, not only calls named like an invoker,
  since an own invoker may be called anything. The value-form rule above is
  the filter that keeps the volume down. For each argument of a value form:
  one raw CALLS edge with `value-arg:<slot>:<site>`, where `<site>` is the
  call's `<raw name>@<row>:<col>`.
- **`metadata["invokes"]`** on each FUNCTION/METHOD node: the slots (position
  and name) of each own parameter its body calls, with the position rules
  above. Calls inside a nested `def` or `lambda` do not count for the outer
  function (the nested one is its own node).
- **CV-2:** a per-function map local → held values, block-scoped as above;
  a call whose callee is such a local emits one `value-local:` edge per value.
- Edge ids: `<file>:value_<value start>_<value end>` (and `_<i>` per held
  value), where the span is the value expression, not the enclosing
  `keyword_argument`; unique against the existing `<file>:edge_<s>_<e>` ids.

## Python profile (`languages/python/profile.py`, data only)

Keys are the spellings the Python resolver produces for those calls (checked
on a scratch ingest), or a builtin's raw name for the unresolved-invoker step:

| invoker | slots |
|---|---|
| `map`, `filter` (builtins) | 0 |
| `sorted`, `min`, `max` (builtins) | `key` |
| `functools.reduce` | 0 |
| `threading.Thread` | 1, `target` |
| `threading.Timer` | 1, `function` |
| `asyncio.to_thread` | 0 |
| `concurrent.futures.ThreadPoolExecutor.submit`, `.map`; the same on `ProcessPoolExecutor` | 0 |
| `asyncio.get_event_loop.run_in_executor`, `asyncio.get_running_loop.run_in_executor` | 1 |
| `asyncio.get_event_loop.call_soon`, `.call_soon_threadsafe`; same on `get_running_loop` | 0 |
| `asyncio.get_event_loop.call_later`; same on `get_running_loop` | 1 |

`list.sort` is not listed: `xs.sort(key=f)` on an untyped receiver does not
resolve. Every row has a conformance fixture that must pass: a row no fixture
reaches is a row the resolver may never produce. Storing registrations
(`atexit.register`, `signal.signal`, `app.get`, `@register`) are CV-3 and not
listed.

## Acceptance (in the PR)

- Conformance: `cv1-map`, `cv1-thread-target`, `cv2-local-var` → `pass`. New,
  each a `must_have` unless marked: one per profile row above;
  `cv1-keyword-key`, `cv1-partial`, `cv4-own-invoker`, `cv4-method-offset`,
  `cv4-staticmethod`, `cv4-keyword-only`, `cv2-rebound-in-if`,
  `cv2-tuple-loop`; `cv4-forwarded-param` (xfail, known limit). Cases that
  check an absence pair it with a `must_have` in the same fixture, so they
  fail when the feature is missing: `cv1-wrong-position`,
  `cv4-param-shadows-function` (a parameter `handler` passed to an own
  invoker beside a module-level `def handler`: no edge to it; a real function
  value in the same call: an edge), `cv4-rebound-param`,
  `cv3-decorator-register`, `cv3-stored-param`.
- owner-api (`ownima-backend/app`): the impact set of
  `reservation_conflict_query` contains
  `ReservationConflictService.check_conflicts` and
  `ReservationCreation.check_booking_window`. The rider routes above them need
  #632 gap 2 (`_filter_available_vehicles_by_date` calls the inherited
  `ReservationService.check_booking_window`) and are reported, not required.
- Precision, pre-registered: an edge diff (old extractor vs new, same source)
  on owner-api and two public Python repositories chosen before the run.
  Every added edge read against the code when there are 50 or fewer per
  repository, else a seeded sample of 50. Pass: at least 95% correct in each
  repository, none from the parameter-shadowing class, and no removed edges.
- Cost: on owner-api, full ingest time at most 15% above main's (median of
  three runs each), with stored node and edge counts and the raw-edge count
  reported separately.
- Identity: the Go and TypeScript snapshots unchanged; `tests/self_parsing`
  green.
- Docs: the coverage caveat and `plugin/skills/cgis/SKILL.md` say Python has
  these edges, with no recall figure.

Recall is measured after merge, in its own issue: a pre-registered seeded
draw of Python repositories with an AST-based labelled oracle, held out from
the fixes.

## Order

1. Core: keyword slots in `invokers`, the context token, the unresolved
   builtin invoker step; Go unchanged.
2. Core: CV-4 via `metadata["invokes"]`.
3. Python extractor: value forms, CV-1/CV-4 candidates, `invokes`; profile.
4. Python CV-2.
5. Audit, docs, PR.
