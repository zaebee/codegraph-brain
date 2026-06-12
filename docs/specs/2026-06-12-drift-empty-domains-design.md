# Drift: Loud Zero-Match Domains — `empty` / `no_signal` (#178)

**Date:** 2026-06-12
**Issue:** #178 — cgis_drift: zero-match / no-signal domains report "clean" instead of "empty"
**Status:** Draft
**Roadmap:** #179 P0 (trust blocker); pairs with #174 (authoring blocker, separate effort)

## 1. Problem

A `project_domain` whose `fqn_prefix` matches zero nodes returns
`drift_score 0.0, status "clean"` — indistinguishable from a healthy domain.
Real repro (dogfooding, click): ontology written with `fqn_prefix:
"click.core"` against a graph whose FQNs are `src.click.*` → every domain
green, `any_critical=false`, while measuring **nothing**. The worst CI
failure mode: the gate passes on an ontology that covers nothing.

Mechanism (verified): `FingerprintExtractor.extract` returns an all-zero
fingerprint for 0 matched nodes — with no record of HOW MANY nodes matched;
`DriftScorer.score` scores the zeros honestly (max-constraints pass, census
layers empty→excluded) → 0.0 → `_classify` → "clean".

A second, subtler variant: a prefix matching one isolated symbol (or only
aliases) produces zero connected triples in every census layer — also 0.0
"clean" while carrying no structural signal.

## 2. Design

### 2.1 Model changes

`src/cgis/query/fingerprint.py` — `PatternFingerprint` gains:

```python
    # Number of graph nodes matched by the domain's fqn_prefix. 0 means the
    # selector matched nothing — the fingerprint carries no information.
    node_count: int = 0
```

`extract()` sets `node_count=len(domain_nodes)` on the normal path and
`node_count=0` on the early return. Default 0 keeps every existing
hand-built test fingerprint valid (they construct non-empty semantics
explicitly where needed; tests for the new guard pass `node_count` > 0).

`src/cgis/query/drift.py` — `DriftReport`:

```python
    status: Literal["clean", "warning", "critical", "empty", "no_signal"]
    # Human-readable diagnostic, e.g. closest-prefix suggestions for "empty".
    note: str | None = None
```

### 2.2 Guards in `DriftScorer.score()`

At the top of `score()`, BEFORE template resolution (so v1, v2, and
hygiene-only paths all share the guard):

```python
        if actual.node_count == 0:
            return self._signal_report(actual, domain, status="empty")
        if actual.t_imports == ZERO_TRIADS and actual.t_calls == ZERO_TRIADS:
            return self._signal_report(actual, domain, status="no_signal")
```

`_signal_report` is a small private helper returning a `DriftReport` with
`drift_score=0.0`, `violations=[]`, empty ideal fingerprint, the given
status, `tolerance=domain.drift_tolerance`. One new method on `DriftScorer`
(currently in `_KNOWN_GOD_OBJECTS` — adding a method is acceptable there;
no new god-object risk anywhere else).

Definitions, pinned:

- **empty** — selector matched 0 nodes. The ontology is mis-targeted;
  nothing was measured.
- **no_signal** — nodes matched, but zero connected triples in BOTH census
  layers (`t_imports` and `t_calls` are both the zero vector). Single
  symbols, alias-only matches, edge-free fragments. Honest "we measured,
  there is no structure to score".

Note: `no_signal` subsumes domains with <3 nodes or no intra-domain edges —
the census zero-vector is the single source of truth, no separate edge
counting.

### 2.3 Gate semantics (`drift_service.analyze_drift`)

Today `any_critical` is computed purely from `drift_score >= max_drift`
(status is ignored — that broader precedence question is #170, NOT fixed
here). #178 adds exactly one status-aware term:

```python
    any_critical = (
        any(r.drift_score >= max_drift for r in reports)
        or any(r.status == "empty" for d, r in zip(domains, reports) if d.enforce)
        or any(
            (r.drift_score >= max_drift or r.status == "empty")
            for b, r in quotient
            if b.enforce
        )
    )
```

- `empty` on an ENFORCED binding (project domain or quotient) → CI fails.
  A broken ontology is critical by definition. The NEW empty term respects
  `DomainConfig.enforce` for project domains via the zip (gemini catch on
  the spec PR: prose and code disagreed).
- `empty` on an `enforce: false` binding → reported, never trips.
- The EXISTING score term (`r.drift_score >= max_drift for r in reports`)
  is enforce-blind for project domains today; that pre-existing semantics
  question belongs to #170 and is deliberately not changed here.
- `no_signal` NEVER trips the gate — a tiny-but-real domain is legitimate;
  it just must not render as "clean".

### 2.4 Closest-prefix suggestions (`drift_service`)

For each `empty` report, `analyze_drift` decorates `note` (reports are
frozen dataclasses → `dataclasses.replace`):

1. Query `store.find_nodes_by_suffix(fqn_prefix, limit=4)` — the existing
   dot-boundary suffix search; `click.core` finds `src.click.core` directly.
2. If nothing, retry with the prefix's last segment (`core`).
3. `note = "fqn_prefix '<p>' matched 0 nodes; did you mean: <id1>, <id2>?"`
   capped at 3 suggestions; without matches,
   `note = "fqn_prefix '<p>' matched 0 nodes"`.

The suggestion helper short-circuits on an empty or whitespace-only
`fqn_prefix` (no DB query; note degrades to the bare "matched 0 nodes"
message) — defensive guard, gemini suggestion on the spec PR.

This is the minimal slice of #173 (find_symbol) — full symbol search stays
out of scope. Suggestion lookup runs inside the existing `SQLiteStore`
context in `analyze_drift`; the scorer itself stays store-free.

### 2.5 Profile filter (decided with the user)

`analyze_drift(db_path, patterns_path, max_drift=0.50, profile=None)` —
`profile: str | None`; when set, only domains (and project_level bindings)
whose `binding.profile == profile` are scored. `None` keeps today's
score-everything behavior.

Rationale: cgis's own `patterns.yaml` mixes python and typescript domains;
TS domains against a python-only graph are zero-match TODAY (they render
"0.00 clean" — instances of this very bug). With the guard they would
become `empty` and fail every CLI run. Scoring TS domains against a python
graph IS a mis-target — the loud failure is correct; `--profile` is the
explicit opt-out.

- CLI: `cgis drift --profile/-P <name>` (plain string, e.g. `python`;
  omitted = all). Help text documents the empty-trip interplay.
- MCP `cgis_drift`: optional `profile: str | None = None` parameter,
  passed through.
- Pinned semantics for domains without a `profile:` field
  (`DomainConfig.profile is None`): they match ANY requested profile —
  hygiene-style gates are language-agnostic, and excluding them under a
  filter would silently drop coverage. The filter excludes only domains
  carrying a DIFFERENT explicit profile. (cgis's own patterns.yaml has no
  None-profile domains — all hygiene domains declare `profile: typescript` —
  so this rule changes nothing for the self-graph.)

### 2.6 Rendering

CLI (`cli.py`): `_drift_status_label` currently recomputes from score; it
gains the two status-driven labels checked FIRST (report.status passed in):

- `empty` → `[bold red]⛔ EMPTY[/bold red]`; the `note` is printed as a
  dim sub-row / caption under the table row.
- `no_signal` → `[yellow]◌ no signal[/yellow]`.
- Other statuses keep today's score-based labels (full status/score
  precedence rework is #170).

MCP JSON needs no schema work: `dataclasses.asdict` already serializes the
new `status` values, `note`, and `node_count` automatically.

### 2.7 Self-graph and ratchet safety

Pure query/report layer — the graph, census mechanics, and every
`drift_tolerance` are untouched. `tests/self_parsing/test_drift.py`
filters domains by profile already (each graph scores its own profile's
domains) and every python domain matches >0 nodes with non-zero censuses —
statuses unchanged there. The CLI-on-mixed-yaml behavior change (TS domains
now EMPTY + exit 1 without `--profile python`) is the intended loud
failure; documented in the patterns.yaml header comment.

## 3. Testing

1. **Scorer guards** (`tests/unit/test_drift.py` additions): empty →
   status "empty" + score 0.0; nodes-but-zero-censuses → "no_signal";
   a normal domain unchanged; hygiene-only domain with 0 nodes → "empty"
   (guard precedes v1 path).
2. **Service** (`tests/unit/test_drift_service.py` additions): empty domain
   trips `any_critical` despite score 0.0; `no_signal` does not;
   `enforce: false` quotient empty does not; suggestion note populated
   ("click.core" → "src.click.core" fixture); profile filter selects /
   excludes; `profile: None` domain survives any filter.
3. **CLI**: `--profile` wiring + exit-code-1 on empty (runner-level test if
   the file has CLI tests; otherwise covered via service tests + a render
   unit test for `_drift_status_label`).
4. **MCP**: `cgis_drift` passes `profile` through; payload contains
   `status: "empty"` and `note` (extend existing mcp tests).
5. **Self-parsing**: full suite green, ratchets untouched.
6. Gates: `make format && make lint && make type-check && make pytest &&
   make doc-coverage`.

## 4. Acceptance

- [ ] The click repro from #178: mis-prefixed ontology → every domain
      `⛔ EMPTY` with `did you mean: src.click...` note, exit code 1.
- [ ] `no_signal` distinct from `clean` in CLI and MCP JSON.
- [ ] cgis's own CI (pytest path) green without modification.
- [ ] mypy strict, ruff, interrogate ≥90%.

## 5. Out of scope

- #170 full status/score precedence semantics (hygiene-breach → critical,
  max_drift vs tolerance) — only the single `empty` term is added to the
  gate here.
- #177 fit-quality/residual reporting.
- #173 full `find_symbol` tool — only the internal suffix-based suggestion.
- #174 init-ontology.
- Any graph/census/ratchet change.

## Amendment 1 (2026-06-12): fingerprint defaults and the no_signal trigger

Plan-stage research found §2.1/§2.2 as written would break the existing test
suite: 22 hand-built `PatternFingerprint` fixtures construct without
`node_count` (default 0 → every one becomes "empty"), and v1-path fixtures
leave both censuses at `ZERO_TRIADS` (→ every one becomes "no_signal").

Superseding §2.1/§2.2 details:

- `PatternFingerprint` gains TWO fields, both defaulting to **1**:
  `node_count: int = 1` and `edge_count: int = 1` (intra-domain edge count,
  all layers). The defaults mean "hand-built fingerprints are assumed
  measurable"; only `extract()` produces real zeros (the early-return path
  sets both to 0; the normal path sets `len(domain_nodes)` /
  `len(internal_edges)`).
- Guards become: `node_count == 0` → **empty**;
  `node_count > 0 and edge_count == 0` → **no_signal**.
  The census-zero-vector trigger is dropped: it cannot distinguish a
  v1-style fingerprint (censuses legitimately absent) from a measured
  no-structure domain, while `edge_count == 0` says exactly "nothing to
  measure beyond node existence" — which also covers the issue's
  single-symbol / alias-only repro (isolated nodes have no intra-domain
  edges). A domain with edges but too few nodes for triples still carries
  v1 signal (chain_len, fan-out) and is correctly NOT no_signal.
- Every existing hand-built fixture passes unchanged.
