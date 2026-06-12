# Drift Gate Semantics v2 (#176 + #170) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Intra-domain cycle_ratio (httpx wall disappears), undilutable `gate_failed` with acknowledgeable hygiene baselines, and per-domain tolerance precedence — closing #176 and #170.

**Architecture:** Per `docs/specs/2026-06-12-gate-semantics-design.md` (read it FULLY — it contains a corrected diagnosis and five review-driven constraints). `FingerprintExtractor` computes its own intra-domain IMPORTS SCC via the shared `cgis.query._scc` utils. `DriftScorer` evaluates hygiene and template constraint sets SEPARATELY (the merged-dict design had a live `unresolved_ratio` collision); hygiene violations against operator-aware, baseline-relaxed bounds force `status="gate_failed"`. Classification becomes relative to the domain's effective tolerance (`drift_tolerance` or the `default_tolerance` the caller passes); the gate becomes uniformly status-based and enforce-respecting. `ontology_init` emits `_ceil2`-rounded `hygiene_baseline` for measured breaches, preserving the #174 round-trip.

**Tech Stack:** Python 3.12, dataclasses, pytest, mypy strict, ruff, interrogate ≥90%.

**Branch:** `feat/issue-176-170-gate-semantics` (worktree `.claude/worktrees/issue-176-170-gate` — Lane A)

**Hard rules:**
- FULL suite (`uv run pytest -q`) before every commit. Docstrings everywhere. `/bin/rm -f`.
- `docs/ontology/patterns.yaml`: ONLY the documented header/comment changes listed in Task 5; never touch tolerance VALUES.
- Existing test updates are allowed ONLY where the spec §2.5 owns the semantic change (old absolute `_classify` thresholds → relative). Each such update must cite "spec §2.5 semantic change" in its diff context. Everything else: tests untouched.
- `HealthScorer` is NOT modified at all.

---

### Task 1: Intra-domain cycle_ratio (TDD)

**Files:**
- Modify: `src/cgis/query/fingerprint.py` (extract's cycle computation)
- Test: `tests/unit/test_fingerprint.py` (append)

- [ ] **Step 1: Write the failing tests.** Append to `tests/unit/test_fingerprint.py` (reuse the file's `_store()` / node-building idioms — READ the neighbors first; the fixtures below need FILE-or-MODULE nodes for the import graph plus FUNCTION children sharing the same `file_path`):

```python
# ---------------------------------------------------------------------------
# intra-domain cycle_ratio (#176)
# ---------------------------------------------------------------------------


def _module_with_funcs(prefix: str, fname: str, n_funcs: int) -> list[Node]:
    """One MODULE node + n FUNCTION children, all sharing file_path."""
    mod = Node(
        id=prefix,
        type=NodeType.MODULE,
        name=prefix.rsplit(".", maxsplit=1)[-1],
        file_path=fname,
        start_line=1,
        end_line=99,
    )
    funcs = [
        Node(
            id=f"{prefix}.f{i}",
            type=NodeType.FUNCTION,
            name=f"f{i}",
            file_path=fname,
            start_line=i + 1,
            end_line=i + 2,
        )
        for i in range(n_funcs)
    ]
    return [mod, *funcs]


def test_single_file_domain_in_cross_cycle_has_zero_cycle_ratio() -> None:
    """The httpx case: a one-file domain inside a CROSS-domain import cycle → 0.0."""
    nodes = _module_with_funcs("pkg.alpha", "pkg/alpha.py", 3) + _module_with_funcs(
        "pkg.beta", "pkg/beta.py", 3
    )
    edges = [
        Edge(id="i1", source="pkg.alpha", target="pkg.beta", type=EdgeType.IMPORTS),
        Edge(id="i2", source="pkg.beta", target="pkg.alpha", type=EdgeType.IMPORTS),
    ]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("pkg.alpha")
    assert fp.cycle_ratio == 0.0  # the cycle is cross-domain; not this domain's smell


def test_intra_domain_cycle_counts_blast_radius() -> None:
    """Two modules of ONE domain importing each other → their nodes count."""
    nodes = (
        _module_with_funcs("app.svc.a", "app/svc/a.py", 2)
        + _module_with_funcs("app.svc.b", "app/svc/b.py", 2)
        + _module_with_funcs("app.svc.clean", "app/svc/clean.py", 2)
    )
    edges = [
        Edge(id="i1", source="app.svc.a", target="app.svc.b", type=EdgeType.IMPORTS),
        Edge(id="i2", source="app.svc.b", target="app.svc.a", type=EdgeType.IMPORTS),
        Edge(id="i3", source="app.svc.clean", target="app.svc.a", type=EdgeType.IMPORTS),
    ]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("app.svc")
    # 6 of 9 nodes live in the two cyclic files (module + 2 funcs each)
    assert fp.cycle_ratio == pytest.approx(6 / 9)


def test_acyclic_domain_keeps_zero() -> None:
    """A chain of imports inside one domain stays 0.0."""
    nodes = _module_with_funcs("lib.x", "lib/x.py", 1) + _module_with_funcs(
        "lib.y", "lib/y.py", 1
    )
    edges = [Edge(id="i1", source="lib.x", target="lib.y", type=EdgeType.IMPORTS)]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("lib")
    assert fp.cycle_ratio == 0.0
```

(Adapt `_store` to the file's actual helper; if it differs, mirror the existing fixture mechanics exactly. The assertions are the contract.)

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/unit/test_fingerprint.py -q -k cycle` — the first test FAILS today (global in_cycle marks pkg.alpha 100%).

- [ ] **Step 3: Implement.** In `src/cgis/query/fingerprint.py`:

a) Import the SHARED utils (spec §2.1: no HealthScorer reach-in): `from cgis.query._scc import build_adjacency, tarjan_scc` and `EdgeType`/`NodeType` if not present.

b) In `extract()`, REPLACE the `cycle_count = sum(1 for n in domain_nodes if n.metadata.get("in_cycle", False))` line with an intra-domain computation:

```python
        cycle_ratio = self._intra_domain_cycle_ratio(domain_nodes, all_edges)
```

c) New private method on `FingerprintExtractor`:

```python
    def _intra_domain_cycle_ratio(
        self, domain_nodes: list[Node], all_edges: list[Edge]
    ) -> float:
        """Blast radius of the domain's OWN import cycles (spec §2.1, #176).

        Tarjan SCC over IMPORTS edges whose endpoints are both FILE/MODULE
        nodes of this domain; the ratio counts domain nodes living in files
        that participate in an intra-domain cycle. Single-file domains are
        0.0 by construction; cross-domain cycles are the quotient layer's
        concern and never count here.
        """
        file_types = {NodeType.FILE, NodeType.MODULE}
        domain_files = {n.id for n in domain_nodes if n.type in file_types}
        if len(domain_files) < 2:
            return 0.0
        adj = build_adjacency(all_edges, frozenset({EdgeType.IMPORTS}))
        adj = {
            k: [v for v in vs if v in domain_files]
            for k, vs in adj.items()
            if k in domain_files
        }
        cyclic_files = {n for scc in tarjan_scc(adj) if len(scc) > 1 for n in scc}
        if not cyclic_files:
            return 0.0
        cyclic_paths = {
            n.file_path for n in domain_nodes if n.id in cyclic_files
        }
        cycle_count = sum(1 for n in domain_nodes if n.file_path in cyclic_paths)
        return cycle_count / len(domain_nodes)
```

NOTE: keep the method count of `FingerprintExtractor` in mind — check it's nowhere near the god-object threshold (≥10 with Ce≥5) after adding one method; report the count.

- [ ] **Step 4:** `uv run pytest tests/unit/test_fingerprint.py -q` all pass; then FULL `uv run pytest -q` — EXPECT possible self-parse drift movement is NOT expected (cgis domains acyclic; cycle_ratio stays 0 everywhere) but VERIFY: if any ratchet moves, STOP and report BLOCKED with numbers.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/query/fingerprint.py tests/unit/test_fingerprint.py
git commit -m "feat(fingerprint): intra-domain cycle_ratio — own cycles only (#176 task 1)"
```

---

### Task 2: DriftScorer core — separate evaluation, gate_failed, relative classify (TDD)

**Files:**
- Modify: `src/cgis/query/drift.py`
- Test: `tests/unit/test_drift.py` (append new + update ONLY the spec-§2.5-owned status assertions)

This is the load-bearing task. Read spec §2.2-2.3 + §2.5 first.

CONSUMER MAP (from `cgis_analyze_impact` on `DriftScorer.score`, depth 3 —
verify nothing is missed): `drift_service.analyze_drift`,
`ontology_init.{_fit_templates,_hygiene_score}`, AND
`guardian/collector.py::ContextCollector.collect_drift` (transitively
chunked/runner/core). The new `default_tolerance` kwarg defaults to 0.50 so
collector keeps compiling, but the relative `_classify` re-shades statuses
embedded in guardian prompts — run `uv run pytest tests/unit/ -q -k guardian`
explicitly in Step 5 and apply the owned-update rule to any guardian test
that pinned an absolute-threshold status.

- [ ] **Step 1: Write the failing tests.** Append to `tests/unit/test_drift.py`:

```python
# ---------------------------------------------------------------------------
# gate_failed + hygiene baselines + relative classification (#170)
# ---------------------------------------------------------------------------


def test_hygiene_breach_forces_gate_failed(scorer: DriftScorer, pure_util_domain: DomainConfig) -> None:
    """A cycle_ratio breach is undilutable by a low TV score (#170A)."""
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=1,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.07,  # breaches hygiene max 0.0
        unresolved_ratio=0.0,
        node_count=20,
        edge_count=10,
    )
    report = scorer.score(fp, pure_util_domain)
    assert report.status == "gate_failed"
    assert any("cycle_ratio" in v for v in report.violations)


def test_acknowledged_baseline_passes_with_note(scorer: DriftScorer) -> None:
    """measured <= baseline → not gate_failed; the acknowledgment is visible."""
    domain = DomainConfig(
        name="legacy",
        fqn_prefix="cgis.extractors",
        expected_pattern="pure_utility",
        drift_tolerance=0.5,
        hygiene_baseline={"cycle_ratio": 0.08},
    )
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=1,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.07,
        unresolved_ratio=0.0,
        node_count=20,
        edge_count=10,
    )
    report = scorer.score(fp, domain)
    assert report.status != "gate_failed"
    assert any("acknowledged" in v for v in report.violations)


def test_debt_beyond_baseline_gate_fails(scorer: DriftScorer) -> None:
    """measured > baseline → gate_failed (new debt is absolute)."""
    domain = DomainConfig(
        name="legacy",
        fqn_prefix="cgis.extractors",
        expected_pattern="pure_utility",
        drift_tolerance=0.5,
        hygiene_baseline={"cycle_ratio": 0.05},
    )
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=1,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.07,
        unresolved_ratio=0.0,
        node_count=20,
        edge_count=10,
    )
    assert scorer.score(fp, domain).status == "gate_failed"


def test_template_breach_does_not_gate_fail(scorer: DriftScorer, pure_util_domain: DomainConfig) -> None:
    """The unresolved_ratio collision (colleague catch): template 0.1 < x <= hygiene 0.2
    violates the TEMPLATE bound only → score-driven status, never gate_failed."""
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=1,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.15,
        node_count=20,
        edge_count=10,
    )
    report = scorer.score(fp, pure_util_domain)
    assert report.status != "gate_failed"


def test_classification_relative_to_tolerance(scorer: DriftScorer) -> None:
    """critical iff score > tolerance_eff; warning above 0.75x (#170B)."""
    domain = DomainConfig(
        name="roomy",
        fqn_prefix="cgis.extractors",
        expected_pattern="pure_utility",
        drift_tolerance=0.60,
    )
    # Build a fingerprint whose score lands between 0.45 (0.75x) and 0.60:
    # reuse an existing mid-drift fixture shape from this file; assert:
    #   report.drift_score <= 0.60  → status in {"clean", "warning"}, not "critical"
    # and a tighter domain (tolerance 0.10) over the same fp → "critical".
    tight = DomainConfig(
        name="tight",
        fqn_prefix="cgis.extractors",
        expected_pattern="pure_utility",
        drift_tolerance=0.10,
    )
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=0,  # violates min:1 — guaranteed nonzero drift
        star_count=2,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
        node_count=20,
        edge_count=10,
    )
    roomy_report = scorer.score(fp, domain)
    tight_report = scorer.score(fp, tight)
    assert roomy_report.drift_score == tight_report.drift_score  # score is tolerance-free
    assert tight_report.status == "critical"
    assert roomy_report.status != "critical"


def test_missing_tolerance_falls_back_to_default(scorer: DriftScorer) -> None:
    """A domain without drift_tolerance uses the caller's default_tolerance."""
    domain = DomainConfig(
        name="lazy",
        fqn_prefix="cgis.extractors",
        expected_pattern="pure_utility",
        drift_tolerance=None,
    )
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=0,
        star_count=2,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
        node_count=20,
        edge_count=10,
    )
    strict = scorer.score(fp, domain, default_tolerance=0.01)
    lax = scorer.score(fp, domain, default_tolerance=1.0)
    assert strict.status == "critical"
    assert lax.status in {"clean", "warning"}


def test_stray_baseline_key_rejected(tmp_path: Path) -> None:
    """A hygiene_baseline key naming no hygiene constraint fails at load time."""
    bad_yaml = _YAML_FOR_SCORER.replace(  # see note below on the fixture name
        'drift_tolerance: 0.15',
        'drift_tolerance: 0.15\n    hygiene_baseline:\n      not_a_gate: 0.5',
    )
    p = tmp_path / "bad.yaml"
    p.write_text(bad_yaml)
    with pytest.raises(ValueError, match="hygiene_baseline"):
        DriftScorer(str(p)).load_project_domains()
```

FIXTURE NOTE: the file builds its scorer from a module-level YAML string (find its actual name — `_YAML_FOR_SCORER` above is a stand-in; use the real constant feeding the `scorer` fixture, and confirm it has a `hygiene:` block with `cycle_ratio {max: 0.0}` and `unresolved_ratio {max: 0.2}` plus a `pure_utility` template with `unresolved_ratio {max: 0.1}` — if the minimal fixture lacks these, EXTEND the fixture yaml minimally so the collision test is real, without touching other tests' expectations).

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/unit/test_drift.py -q -k "gate_failed or acknowledged or beyond_baseline or relative or falls_back or stray"` — all FAIL (no such status/params yet).

- [ ] **Step 3: Implement in `src/cgis/query/drift.py`** (anchored changes; bodies you don't touch stay byte-identical):

a) `DomainConfig`:

```python
    drift_tolerance: float | None = None
    profile: str | None = None
    params: dict[str, float] = field(default_factory=dict)
    # Acknowledged hygiene debt: per-constraint relaxed bound (ratchet-down
    # convention; values may only decrease over time). Spec §2.2.
    hygiene_baseline: dict[str, float] = field(default_factory=dict)
    enforce: bool = True
```

(Field ORDER: keep `drift_tolerance` in its current position but now optional-with-None-default — verify no positional construction sites break; the codebase constructs DomainConfig by keyword everywhere, confirm with grep.)

b) `_build_domain_config`: null-safe tolerance (`float(d["drift_tolerance"]) if d.get("drift_tolerance") is not None else None`); parse `hygiene_baseline` via the existing `_validate_mapping` helper; validate each baseline key against the hygiene block's keys — unknown key → `ValueError` naming the key and listing valid ones.

c) `DriftReport.status` Literal gains `"gate_failed"` (full set: clean/warning/critical/gate_failed/empty/no_signal).

d) `_classify` becomes relative:

```python
def _classify(score: float, tolerance: float) -> Literal["clean", "warning", "critical"]:
    """Status from the score RELATIVE to the binding's effective tolerance (#170B)."""
    if score > tolerance:
        return "critical"
    if score > 0.75 * tolerance:
        return "warning"
    return "clean"
```

Delete `_STATUS_WARNING`/`_STATUS_CRITICAL`.

e) `score()` signature: `def score(self, actual, domain, default_tolerance: float = 0.50) -> DriftReport`. Compute `tolerance_eff = domain.drift_tolerance if domain.drift_tolerance is not None else default_tolerance`. The #178 guards stay first. Hygiene/template separation:

```python
        hygiene = self._parse_constraints(self._hygiene, {})
        template_constraints = self._parse_constraints(template, params)
        constraints = {**hygiene, **template_constraints}  # SCORE math unchanged
```

The merged dict still feeds the v1/v2 SCORE paths exactly as today (the score number is not what #170A changes). SEPARATELY, evaluate hygiene with effective bounds for the STATUS:

```python
        hygiene_eff = self._apply_baseline(hygiene, domain.hygiene_baseline)
        gate_violations, acknowledged = self._hygiene_check(actual, hygiene_eff, domain)
```

f) Two new private methods (DriftScorer is in `_KNOWN_GOD_OBJECTS`; +2 methods there is sanctioned):

```python
    def _apply_baseline(
        self,
        hygiene: dict[str, tuple[str, float]],
        baseline: dict[str, float],
    ) -> dict[str, tuple[str, float]]:
        """Relax hygiene bounds by the domain's acknowledged debt (spec §2.2).

        Operator-aware: max-bounds take max(global, baseline), min-bounds take
        min(global, baseline), exact-bounds are overridden — a baseline can
        only ever RELAX, never tighten.
        """
        out = dict(hygiene)
        for key, ack in baseline.items():
            op, bound = out[key]  # key validity enforced at load time
            if op == "max":
                out[key] = (op, max(bound, ack))
            elif op == "min":
                out[key] = (op, min(bound, ack))
            else:  # exact
                out[key] = (op, ack)
        return out

    def _hygiene_check(
        self,
        actual: PatternFingerprint,
        hygiene_eff: dict[str, tuple[str, float]],
        domain: DomainConfig,
    ) -> tuple[list[str], list[str]]:
        """(breaches, acknowledgments) against the EFFECTIVE hygiene bounds.

        A breach of any effective bound forces status="gate_failed" upstream.
        A measurement over the GLOBAL bound but within the acknowledged
        baseline produces a visibility note, not a breach.
        """
        breaches: list[str] = []
        acknowledged: list[str] = []
        for key, (op, bound) in hygiene_eff.items():
            value = float(getattr(actual, key))
            violated = (
                (op == "max" and value > bound)
                or (op == "min" and value < bound)
                or (op == "exact" and value != bound)
            )
            if violated:
                breaches.append(f"hygiene {key} {value:.4f} violates {op} {bound}")
            elif key in domain.hygiene_baseline:
                acknowledged.append(
                    f"{key} {value:.4f} acknowledged (baseline {domain.hygiene_baseline[key]})"
                )
        return breaches, acknowledged
```

(Adapt the exact violated-check to `_weighted_constraint_drift`'s existing operator semantics so the two never disagree — READ it and mirror its comparison directions; `getattr(actual, key)` works because hygiene keys are fingerprint field names.)

g) Status assembly in both `_score_v1` and `_score_v2` return paths (and `_zero_drift_report`): append `gate_violations + acknowledged` to the violations list; status:

```python
        status = "gate_failed" if gate_violations else _classify(drift, tolerance_eff)
```

Thread `tolerance_eff`, `gate_violations`, `acknowledged` into the helpers as parameters (smallest-churn route: compute them in `score()` and pass down; the helpers' DriftReport constructions switch `tolerance=domain.drift_tolerance` → `tolerance=tolerance_eff`).

h) `_signal_report` (#178 guards) keeps its `empty`/`no_signal` statuses untouched (they run before any of this).

- [ ] **Step 4: Update the spec-§2.5-owned status assertions.** Run `uv run pytest tests/unit/test_drift.py -q`; every failure must be ONE of: (a) a new test from Step 1 now passing, (b) an OLD test asserting an absolute-threshold status ("warning"/"critical"/"clean") that the relative semantics legitimately change — update ONLY the expected status, citing the new tolerance-relative rule in the assertion's docstring; (c) anything else → your implementation is wrong, fix it, don't touch the test. List every (b) update in your report with old→new and the fixture's tolerance that justifies it.

- [ ] **Step 5:** FULL `uv run pytest -q` + `make type-check && make lint`. The drift_service/CLI tests may fail on the `score >= max_drift` gate expectations — those are Task 3's; if so note them and verify they're the ONLY failures, then proceed to commit with `-q` scoped... NO: the suite must be green per the hard rule. Instead, Task 2 includes the MINIMAL service-side shim: in `drift_service.analyze_drift`, pass `default_tolerance=max_drift` into every `scorer.score(...)` call and replace the gate with the uniform status form (spec §2.3):

```python
    any_critical = any(
        r.status in ("critical", "gate_failed", "empty")
        for d, r in zip(domains, reports, strict=True)
        if d.enforce
    ) or any(
        r.status in ("critical", "gate_failed", "empty") for b, r in quotient if b.enforce
    )
```

Update `tests/unit/test_drift_service.py` expectations that encoded score-vs-max_drift semantics the same owned way as Step 4(b) (e.g. `test_analyze_drift_any_critical_threshold` becomes a default_tolerance test). CLI label changes remain Task 3.

- [ ] **Step 6: Commit**

```bash
git add src/cgis/query/drift.py src/cgis/query/drift_service.py tests/unit/test_drift.py tests/unit/test_drift_service.py
git commit -m "feat(drift): gate_failed + acknowledgeable baselines + tolerance-relative status (#170 task 2)"
```

---

### Task 3: CLI + MCP surfaces

**Files:**
- Modify: `src/cgis/cli.py` (drift region only), `src/cgis/api/mcp_server.py` (cgis_drift docstring)
- Test: `tests/unit/test_cli.py`, `tests/unit/test_mcp_server.py` (append + owned updates)

- [ ] **Step 1: Failing tests** (append):

```python
def test_status_label_gate_failed() -> None:
    """gate_failed renders distinctly and precedes score-driven labels."""
    assert "gate failed" in _drift_status_label(0.0, 0.5, status="gate_failed")
```

Plus a CLI run-level test: a db with an intra-domain cycle + patterns with `cycle_ratio {max: 0.0}` hygiene → output contains "gate failed", exit 1; the same db with a `hygiene_baseline` covering the measured value → exit 0. (Build fixtures with the MODULE+IMPORTS shape from Task 1's tests; reuse/extend `tests/unit/conftest.py` helpers rather than copy-pasting — Sonar dup lesson from #211.)

- [ ] **Step 2: Implement.**

a) `_drift_status_label(score, max_drift, status="clean")` gains the branch FIRST:

```python
    if status == "gate_failed":
        return "[bold red]⛔ gate failed[/bold red]"
```

and the second parameter is RENAMED `max_drift` → `tolerance` (it now means the binding's effective tolerance): call sites pass `r.tolerance` (`_render_drift_table(reports)` drops its max_drift parameter; same for the quotient line). `r.tolerance` carries the effective tolerance from Task 2(g).

b) `drift` command: `--max-drift` help text becomes "Default tolerance for domains that do not declare drift_tolerance (no longer caps domains that do — see #170)." Pass-through unchanged (`analyze_drift(..., max_drift=max_drift)`).

c) MCP `cgis_drift` docstring: same semantic note, one sentence. No schema work.

- [ ] **Step 3:** Full suite + gates; owned-update rule from Task 2 applies to any label-shape assertions.

- [ ] **Step 4: Commit**

```bash
git add src/cgis/cli.py src/cgis/api/mcp_server.py tests/
git commit -m "feat(cli,mcp): gate-failed rendering; max-drift demoted to default tolerance (#170 task 3)"
```

---

### Task 4: init-ontology baseline emission (TDD)

**Files:**
- Modify: `src/cgis/query/ontology_init.py` (`_domain_entry` + `_assemble_yaml` region)
- Test: `tests/unit/test_ontology_init.py` (append)

- [ ] **Step 1: Failing tests:**

```python
def test_cyclic_domain_proposal_emits_baseline_and_round_trips(tmp_path: Path) -> None:
    """A domain with an intra-domain import cycle gets an acknowledged baseline (spec §2.2).

    The emitted value is _ceil2'd UP (colleague catch: flooring below the true
    measurement would gate_fail the proposal on its own graph).
    """
    db = str(tmp_path / "cyc.db")
    # Two modules importing each other + enough functions to clear min_nodes:
    nodes, edges = [], []
    for mod, n in (("app.loop.a", 6), ("app.loop.b", 6)):
        nodes += _module_with_funcs_for_init(mod, f"{mod.replace('.', '/')}.py", n)
    edges += [
        Edge(id="c1", source="app.loop.a", target="app.loop.b", type=EdgeType.IMPORTS),
        Edge(id="c2", source="app.loop.b", target="app.loop.a", type=EdgeType.IMPORTS),
    ]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    text = propose_ontology(db, min_nodes=10)
    assert "hygiene_baseline" in text
    assert "acknowledged at baseline by init-ontology" in text
    out = tmp_path / "p.yaml"
    out.write_text(text)
    analysis = analyze_drift(db, str(out))
    assert analysis.any_critical is False
    assert all(r.status != "gate_failed" for r in analysis.reports)
```

(Define `_module_with_funcs_for_init` locally or promote the Task-1 helper into `tests/unit/conftest.py` and import in both — prefer the conftest promotion, Sonar lesson.)

- [ ] **Step 2: Implement.** In `_domain_entry`: after the fingerprint is computed, for each hygiene constraint key (read them from `_PARSED_HEADER["hygiene"]`), if the measured fingerprint value breaches the GLOBAL bound, collect `key: _ceil2(measured)`. If non-empty, emit:

```yaml
    hygiene_baseline:
      cycle_ratio: 0.67  # acknowledged at baseline by init-ontology — ratchet down over time
```

(String-assembly like the rest of the entry; one line per acknowledged key; applies to BOTH labeled and hygiene-only entries.) The breach check must reuse the same operator semantics as Task 2's `_hygiene_check` direction (max → measured > bound).

- [ ] **Step 3:** `uv run pytest tests/unit/test_ontology_init.py -q` all pass (the #174 round-trip and self-graph tests must stay green untouched); FULL suite + gates.

- [ ] **Step 4: Commit**

```bash
git add src/cgis/query/ontology_init.py tests/unit/test_ontology_init.py tests/unit/conftest.py
git commit -m "feat(ontology-init): emit ceil'd hygiene_baseline for measured breaches (#176/#170 task 4)"
```

---

### Task 5: Acceptance fixtures + docs + full gates

**Files:**
- Modify: `docs/ontology/patterns.yaml` (header comment only)
- Test: `tests/unit/test_drift_service.py` (two acceptance scenarios)
- Verify: `tests/self_parsing/` (re-measure)

- [ ] **Step 1: The two issue-repro acceptance tests** (append to test_drift_service.py, building on conftest helpers):

a) `test_httpx_shape_no_cycle_wall`: five single-file domains (MODULE+functions each), cross-file import cycles BETWEEN them, patterns yaml binding each as its own domain with `cycle_ratio {max: 0.0}` hygiene → NO report has status gate_failed/critical from cycles; `any_critical is False`.

b) `test_owner_api_shape_cycle_gate_fails`: one multi-file domain with an internal 2-module cycle, low TV drift → its report status == "gate_failed", `any_critical is True`; and with `hygiene_baseline: {cycle_ratio: <ceil of measured>}` in the yaml → green.

Plus the spec-§3.1 quotient assertion: extend the EXISTING quotient test
(`test_quotient_observe_only_does_not_flip_any_critical` or a sibling using
`_triangle_quotient_db`) with one assertion that the cross-domain cycle
remains VISIBLE at the quotient layer (its report's violations or census
mention the 030C/triangle signal) — pinning the "cross-domain cycles are
the quotient's job" routing.

Plus c) `test_per_domain_tolerance_binds_over_default`: the #170B repro — domain tolerance 0.55, score ~0.527 (reuse a fixture shape that scores there or assert relationally: score < tolerance → status != critical even with `max_drift=0.5`).

- [ ] **Step 2: patterns.yaml header** — append to the file-level comment block (NO value changes):

```yaml
# NOTE (#170): per-domain drift_tolerance takes precedence; the CLI/MCP
# max_drift is only the default for domains that omit it. Hygiene breaches
# force ⛔ gate_failed regardless of the TV score; acknowledged debt may be
# declared per-domain via hygiene_baseline (ratchet-down convention, #151).
```

- [ ] **Step 3: Self-parse + drift re-measure.** `uv run pytest tests/self_parsing/ -q` (all green; ratchet movement → BLOCKED report). Then the CLI table for the record:

```bash
uv run cgis ingest src --source-root src -o /tmp/cgis176.db
uv run cgis drift --db /tmp/cgis176.db --patterns docs/ontology/patterns.yaml --profile python; echo "exit=$?"
```

Capture the table — expect identical scores to main, statuses re-shaded by the relative bands, exit 0.

- [ ] **Step 4: Full gates.** `make format && make lint && make type-check && make pytest && make doc-coverage`

- [ ] **Step 5: Acceptance checks**

```bash
git diff origin/main -- docs/ontology/patterns.yaml   # header comment ONLY
grep -c "gate_failed" src/cgis/query/drift.py          # > 0
```

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_drift_service.py docs/ontology/patterns.yaml
git commit -m "test(drift): httpx/owner-api acceptance shapes; document gate v2 (#176/#170 task 5)"
```

---

## Final checklist (controller, before PR)

- [ ] Both issue repros encoded as tests and green
- [ ] Every owned test update lists old→new with its §2.5 justification
- [ ] patterns.yaml: comment-only diff; ratchets untouched
- [ ] #174 round-trip suite untouched and green
- [ ] PR body: `Closes #176, closes #170`, spec link, ⚠️ breaking `--max-drift` semantics, drift table before/after statuses
