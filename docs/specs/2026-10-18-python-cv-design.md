# Python function values: CV-1, CV-2 and own invokers (CV-4) — design

Date: 2026-10-18. Status: proposed. Addresses #632 gap 1; turns the three
`xfail` Python cases in `tests/fixtures/conformance/python/` into `pass`.

## Why

Go got edges for function values in #668 (clauses CV-1, CV-2, CV-3 in
`docs/specs/core/ontology.yml`); a held-out check measured 21 of 21 (#695).
Python has none. The mechanism is already language-neutral: an extractor emits
a raw edge whose context is `value-arg:<pos>:<site>` (CV-1) or `value-local:`
(CV-2), and `resolver/engine.py::_value_call_edge` keeps it only when the value
resolves to a real function or method and, for CV-1, the call resolves to an
invoker the pack lists at that position.

Two things keep Python out:

1. **Python passes values by keyword.** `threading.Thread(target=self.run)`,
   `sorted(xs, key=f)`. `ResolutionRules.invokers` holds positions only.
2. **The measured Python miss is not a library invoker.** Exp1 (#632) broke on
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

Positions of a method count after `self`/`cls`, since the call is bound
(`self.m(a, fn)`); a `@staticmethod` counts from 0. A keyword-only parameter
(after `*` or `*args`) is invoked by name only, a positional-only one (before
`/`) by position only. Parameters after `*args` have no position.

Known limits (CV-4):
- A parameter passed on rather than called (`def run(fn): pool.submit(fn)`)
  makes `run` no invoker: one level only. Fixture `cv4-forwarded-param`, xfail.
- A parameter stored (`self._cb = fn`) is CV-3: no edge, by design.
- A method invoked through its class (`C.m(obj, name, fn)`) shifts positions
  by one; the value at the shifted position is usually not a function, which
  the resolver drops, so the effect is a missed edge, rarely a wrong one.
- A call-site argument after a `*xs` splat has no position and is skipped.

**CV-2 for Python** follows the Go clause text: a local bound to a function
value (`cb = self.handle`, `for cb in (self.a, self.b)`) and called in the same
function is a call by that function. A rebinding in the same block replaces
the value; one inside an `if`/`for`/`while`/`try`/`with` body adds to it. The
Go known limits (single pass, closures read where written) apply as written.

**CV-3** is unchanged and already passes for Python (`cv3-registry`).

## Core changes

- `cgis.core.resolution.ResolutionRules.invokers`:
  `Mapping[str, frozenset[int | str]]`. An `int` is a 0-based position, a
  `str` a keyword name. Go's profile type-checks unchanged.
- `VALUE_ARG_CONTEXT` token: `value-arg:<slot>:<site>`, where `<slot>` is a
  decimal position or a Python identifier (a keyword). `_value_call_edge`
  parses `isdecimal()` as `int`, `isidentifier()` as `str`, anything else is
  malformed (edge dropped, as today).
- Own invokers: `_value_call_edge` takes the invoker's slots from
  `rules.invokers`, or else from the resolved invoker node's
  `metadata["invokes"]` (a JSON list of ints and strings). Language-neutral:
  Go may fill the same field later. A value edge whose invoker has neither is
  dropped, as today.

## Python extractor

- **Value forms** an argument or a bound local may name: a name (`double`),
  `self.m`, `cls.m`, a dotted name (`mod.f`, `C.m`), and `functools.partial(f,
  ...)` / `partial(f, ...)`, which names `f`. Anything else (a literal, a call,
  a subscript, a lambda) is not a value candidate; a lambda's body calls are
  attributed to the enclosing function already.
- **CV-1/CV-4 candidates:** every call, not only calls named like an invoker,
  since an own invoker may be called anything. For each argument of a value
  form: one raw CALLS edge with `value-arg:<slot>:<site>`, where `<site>` is
  the call's `<raw name>@<row>:<col>`. A local holding values passes what it
  holds (as in Go). The resolver discards candidates whose value is not a
  function, so a data argument costs an in-memory edge, not a stored one.
- **`metadata["invokes"]`** on each FUNCTION/METHOD node: the slots (position
  and name) of each own parameter its body calls, with the position rules
  above. Calls inside a nested `def` or `lambda` do not count for the outer
  function (the nested one is its own node).
- **CV-2:** a per-function map local → held values, block-scoped as above;
  a call whose callee is such a local emits one `value-local:` edge per value.
- Edge ids: `<file>:value_<arg start>_<arg end>` (and `_<i>` per held value),
  unique against the existing `<file>:edge_<start>_<end>` ids.

## Python profile (`languages/python/profile.py`, data only)

| invoker (resolved target) | slots |
|---|---|
| `map`, `filter` | 0 |
| `sorted`, `min`, `max` | `key` |
| `list.sort` | `key` |
| `functools.reduce` | 0 |
| `threading.Thread` | 1, `target` |
| `threading.Timer` | 1, `function` |
| `concurrent.futures.Executor.submit`, `.map` (and the Thread/Process pool subclasses) | 0 |
| `asyncio.to_thread` | 0 |
| `asyncio.AbstractEventLoop.run_in_executor` | 1 |
| `asyncio.AbstractEventLoop.call_soon`, `call_soon_threadsafe` | 0 |
| `asyncio.AbstractEventLoop.call_later` | 1 |

Exact target spellings are what the Python resolver produces for those calls
(builtins as bare names, stdlib as dotted paths); the implementation checks
each against a fixture, as Go's `test_every_invoker_the_go_caveat_names...`
does. Storing registrations (`atexit.register`, `signal.signal`, `app.get`,
`@register`) are CV-3 and not listed.

## Acceptance (in the PR)

- Conformance: `cv1-map`, `cv1-thread-target`, `cv2-local-var` → `pass`. New:
  `cv1-keyword-key`, `cv1-partial`, `cv1-wrong-position`, `cv4-own-invoker`,
  `cv4-method-offset`, `cv4-staticmethod`, `cv4-keyword-only`,
  `cv4-forwarded-param` (xfail, known limit), `cv2-rebound-in-if`,
  `cv2-tuple-loop`, `cv3-decorator-register`, `cv3-stored-param`.
- owner-api (`ownima-backend/app`): the impact set of
  `reservation_conflict_query` contains
  `rider.routes._filter_available_vehicles_by_date`. Rider routes above it
  need #632 gap 2 (inherited `ReservationService.check_booking_window`) and
  are reported, not required.
- Precision: an edge diff (old extractor vs new, same source) on owner-api
  and two public Python repositories. Every added edge read against the code
  when there are 50 or fewer per repository, else a seeded sample of 50. No
  removed edges.
- Identity: the Go and TypeScript snapshots unchanged; `tests/self_parsing`
  green.
- Docs: the coverage caveat and `plugin/skills/cgis/SKILL.md` say Python has
  these edges, with no recall figure.
- Cost: ingest time and node/edge counts on owner-api before and after,
  reported in the PR.

Recall is measured after merge, in its own issue: a pre-registered seeded
draw of Python repositories with an AST-based labelled oracle, held out from
the fixes.

## Order

1. Core: keyword slots in `invokers` and the context token; Go unchanged.
2. Core: CV-4 via `metadata["invokes"]`.
3. Python extractor: value forms, CV-1/CV-4 candidates, `invokes`; profile.
4. Python CV-2.
5. Audit, docs, PR.
