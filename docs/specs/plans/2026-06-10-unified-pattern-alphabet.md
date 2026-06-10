# Unified Pattern Alphabet (Part A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kill the `patterns_ui.yaml` ontology fork: one closed pattern alphabet in `patterns.yaml` with parameterized templates, per-graph measurement profiles, a global hygiene block, and a confidence discount on CALLS-derived weights.

**Architecture:** All changes concentrate in `DriftScorer` (`src/cgis/query/drift.py`) — it learns four things: profile-based weight selection, `$param` substitution, hygiene-merge for domains without `expected_pattern`, and the `(1 - unresolved_ratio)` discount. The ontology file is then rewritten to the unified layout and `patterns_ui.yaml` is deleted. Spec: `docs/specs/2026-06-09-pattern-alphabet-motif-basis-design.md` §2 (approved in PR #142).

**Tech Stack:** Python 3.12, PyYAML, pytest, strict mypy. No new dependencies.

**Measured baseline (2026-06-09, both graphs):** `unresolved_ratio = 0.00` and `cycle_ratio = 0.00` for every domain — so the discount and the hygiene block change no current score; Python ratchet values stay. UI: `components dag_depth=2`, `layout hub=1 star=0`, others all-zero topology.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/cgis/query/drift.py` | modify | DomainConfig fields, profile weights, params, hygiene, discount |
| `tests/unit/test_drift.py` | modify | unit tests for every new scorer behavior |
| `docs/ontology/patterns.yaml` | rewrite | unified alphabet + profiles + hygiene + all 11 domains |
| `docs/ontology/patterns_ui.yaml` | delete | — |
| `tests/unit/test_patterns_yaml.py` | modify | structural validation of the new layout |
| `tests/self_parsing/test_drift.py` | modify | both graphs score against the single file |
| `scripts/gen_ideal_graph.py` | modify | skip hygiene-only domains |

Conventions that hold throughout: fail-loud `ValueError`/`TypeError` discipline from PR #140; `make format && make lint && make type-check && make pytest && make doc-coverage` before the final commit; every new function gets a docstring (interrogate ≥90%).

---

### Task 1: DomainConfig gains `profile`, `params`, optional `expected_pattern`

**Files:**
- Modify: `src/cgis/query/drift.py:27-34` (DomainConfig), `:72-82` (load_project_domains)
- Test: `tests/unit/test_drift.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_drift.py` (note: the module-level `_YAML` constant stays untouched; new tests build their own YAML inline):

```python
# ── Task 1: extended DomainConfig loading ─────────────────────────────────────

_YAML_EXTENDED = """\
version: "2.0.0"
profiles:
  python:
    drift_weights:
      hub_count:        0.15
      star_count:       0.15
      chain_len:        0.10
      dag_depth:        0.10
      router_count:     0.10
      cycle_ratio:      0.25
      unresolved_ratio: 0.15
patterns:
  layered_dag:
    description: "Layered imports"
    params:
      min_depth: 3
    dag_depth:        {min: $min_depth}
    cycle_ratio:      {max: 0.0}
project_domains:
  - name: "components"
    fqn_prefix: "components"
    expected_pattern: layered_dag
    profile: python
    params: {min_depth: 2}
    drift_tolerance: 0.15
  - name: "hooks"
    fqn_prefix: "hooks"
    drift_tolerance: 0.15
"""


@pytest.fixture
def extended_scorer(tmp_path: pytest.TempPathFactory) -> DriftScorer:
    """Return a DriftScorer loaded from the extended (v2) YAML fixture."""
    p = tmp_path / "patterns.yaml"  # type: ignore[operator]
    p.write_text(_YAML_EXTENDED)
    return DriftScorer(str(p))


def test_domain_config_loads_profile_and_params(extended_scorer: DriftScorer) -> None:
    """profile and params are read from the domain binding."""
    domains = extended_scorer.load_project_domains()
    d = domains[0]
    assert d.profile == "python"
    assert d.params == {"min_depth": 2.0}


def test_domain_config_defaults_for_legacy_yaml(scorer: DriftScorer) -> None:
    """Bindings without profile/params load with profile=None, params={}."""
    d = scorer.load_project_domains()[0]
    assert d.profile is None
    assert d.params == {}


def test_domain_config_expected_pattern_optional(extended_scorer: DriftScorer) -> None:
    """A binding without expected_pattern loads with expected_pattern=None."""
    hooks = extended_scorer.load_project_domains()[1]
    assert hooks.expected_pattern is None
```

Note: `$min_depth` inside a YAML flow mapping parses as the plain string `"$min_depth"` — no quoting needed.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_drift.py -v -k "domain_config"`
Expected: FAIL — `TypeError: DomainConfig.__init__() got an unexpected keyword argument` / `AttributeError: profile`

- [ ] **Step 3: Implement**

In `src/cgis/query/drift.py`, replace the `DomainConfig` dataclass and `load_project_domains`:

```python
@dataclass(frozen=True)
class DomainConfig:
    """Project-level domain expectation loaded from patterns.yaml."""

    name: str
    fqn_prefix: str
    expected_pattern: str | None
    drift_tolerance: float
    profile: str | None = None
    params: dict[str, float] = field(default_factory=dict)
```

Add `field` to the dataclasses import: `from dataclasses import dataclass, field`.

```python
    def load_project_domains(self) -> list[DomainConfig]:
        """Return all project domains declared in patterns.yaml."""
        return [
            DomainConfig(
                name=d["name"],
                fqn_prefix=d["fqn_prefix"],
                expected_pattern=d.get("expected_pattern"),
                drift_tolerance=float(d["drift_tolerance"]),
                profile=d.get("profile"),
                params={k: float(v) for k, v in (d.get("params") or {}).items()},
            )
            for d in self._project_domains
        ]
```

In `__init__`, also load the new sections (used by Tasks 2–4):

```python
        self._profiles: dict[str, dict[str, Any]] = raw.get("profiles") or {}
        self._hygiene: dict[str, Any] = raw.get("hygiene") or {}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_drift.py -v`
Expected: all PASS (including all pre-existing tests — `expected_pattern` stays a positional-friendly third field, existing constructors use keywords).

Note: `score()` still annotates `domain.expected_pattern` as `str`; mypy may flag the new `str | None` at the `self._patterns.get(...)` call. If `make type-check` complains here, it is fixed in Task 4 (hygiene-only path) — for this commit add a temporary guard at the top of `score()`:

```python
        if domain.expected_pattern is None:
            msg = f"Domain '{domain.name}' has no expected_pattern (hygiene-only domains land in a later commit)."
            raise NotImplementedError(msg)
```

- [ ] **Step 5: Type-check and commit**

Run: `make type-check`
Expected: success.

```bash
git add src/cgis/query/drift.py tests/unit/test_drift.py
git commit -m "feat(drift): DomainConfig gains profile, params, optional expected_pattern"
```

---

### Task 2: Profile-based weight selection

**Files:**
- Modify: `src/cgis/query/drift.py` (`score()`, new `_weights_for`)
- Test: `tests/unit/test_drift.py`

- [ ] **Step 1: Write the failing tests**

```python
# ── Task 2: profile-based weights ─────────────────────────────────────────────
# Dedicated YAML without $params so this task does not depend on Task 3.

_YAML_PROFILES = """\
version: "2.0.0"
profiles:
  python:
    drift_weights:
      hub_count:        0.40
      star_count:       0.20
      chain_len:        0.10
      dag_depth:        0.10
      router_count:     0.05
      cycle_ratio:      0.10
      unresolved_ratio: 0.05
patterns:
  pure_utility:
    description: "Hub"
    hub_count:   {min: 1}
    star_count:  {exact: 0}
project_domains:
  - name: "lib"
    fqn_prefix: "lib"
    expected_pattern: pure_utility
    profile: python
    drift_tolerance: 0.15
"""


def _profile_fp(hub: int, star: int) -> PatternFingerprint:
    """Fingerprint helper for profile-weight tests."""
    return PatternFingerprint(
        domain="lib",
        hub_count=hub,
        star_count=star,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )


def test_profile_weights_used_when_domain_names_profile(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """A domain naming a profile is scored with that profile's drift_weights."""
    p = tmp_path / "patterns.yaml"  # type: ignore[operator]
    p.write_text(_YAML_PROFILES)
    scorer = DriftScorer(str(p))
    domain = scorer.load_project_domains()[0]
    # hub violated, star clean. Profile weights hub .40 / star .20 → drift = .4/.6 = 2/3.
    # (Asymmetric weights on purpose: the equal-split fallback would give 0.5,
    # so 2/3 proves the profile weights were actually used.)
    report = scorer.score(_profile_fp(hub=0, star=0), domain)
    assert report.drift_score == pytest.approx(2 / 3, abs=1e-6)


def test_unknown_profile_raises(tmp_path: pytest.TempPathFactory) -> None:
    """A domain naming an undeclared profile is a config error."""
    yaml_text = _YAML_PROFILES.replace("profile: python", "profile: golang")
    p = tmp_path / "patterns.yaml"  # type: ignore[operator]
    p.write_text(yaml_text)
    scorer = DriftScorer(str(p))
    domain = scorer.load_project_domains()[0]
    with pytest.raises(ValueError, match="golang"):
        scorer.score(_profile_fp(hub=1, star=0), domain)


def test_top_level_weights_remain_default(scorer: DriftScorer, pure_util_domain: DomainConfig) -> None:
    """Domains without a profile keep using top-level drift_weights (legacy layout)."""
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=0,  # violates min:1 → nonzero drift proves weights were found
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )
    report = scorer.score(fp, pure_util_domain)
    assert report.drift_score > 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_drift.py -v -k "profile or top_level"`
Expected: `test_unknown_profile_raises` FAILS (no ValueError raised); `test_profile_weights_used_when_domain_names_profile` FAILS (profile weights ignored → empty top-level weights trigger the equal-split fallback → drift 0.5, not 2/3). `test_top_level_weights_remain_default` should already PASS (regression guard).

- [ ] **Step 3: Implement**

Add to `DriftScorer`:

```python
    def _weights_for(self, domain: DomainConfig) -> dict[str, float]:
        """Return the drift weights for a domain: its profile's, or the top-level default."""
        if domain.profile is None:
            return self._weights
        profile = self._profiles.get(domain.profile)
        if profile is None:
            msg = f"Domain '{domain.name}' names unknown profile '{domain.profile}'."
            raise ValueError(msg)
        weights: dict[str, float] = profile.get("drift_weights") or {}
        return weights
```

In `score()`, replace every use of `self._weights` with a local `weights = self._weights_for(domain)` resolved once at the top:

```python
        weights = self._weights_for(domain)
        ...
        total_weight = sum(weights.get(name, 0.0) for name in constraints)
        ...
            weight = (
                weights.get(name, 0.0) / total_weight
                if total_weight > 0.0
                else 1.0 / len(constraints)
            )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_drift.py -v -k "profile or top_level"`
Expected: `test_unknown_profile_raises` and `test_top_level_weights_remain_default` PASS. (`test_profile_weights_used_when_domain_names_profile` still fails on the unsubstituted `$min_depth` — Task 3.)

- [ ] **Step 5: Commit**

```bash
git add src/cgis/query/drift.py tests/unit/test_drift.py
git commit -m "feat(drift): per-graph measurement profiles for drift weights"
```

---

### Task 3: Parameterized templates (`params:` + `$name` substitution)

**Files:**
- Modify: `src/cgis/query/drift.py` (`_parse_constraints`, `score()`)
- Test: `tests/unit/test_drift.py`

- [ ] **Step 1: Write the failing tests**

```python
# ── Task 3: parameterized templates ───────────────────────────────────────────


def _fp_with_dag(depth: int) -> PatternFingerprint:
    """Fingerprint with only dag_depth set — helper for param tests."""
    return PatternFingerprint(
        domain="components",
        hub_count=0,
        star_count=0,
        chain_len=0.0,
        dag_depth=depth,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )


def test_domain_params_override_template_default(extended_scorer: DriftScorer) -> None:
    """components overrides min_depth to 2 — dag_depth=2 is clean."""
    domain = extended_scorer.load_project_domains()[0]
    report = extended_scorer.score(_fp_with_dag(2), domain)
    assert report.drift_score == pytest.approx(0.0)
    assert report.violations == []


def test_template_param_default_applies_without_override(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Without a domain override, the template default min_depth=3 governs."""
    yaml_text = _YAML_EXTENDED.replace("    params: {min_depth: 2}\n", "")
    p = tmp_path / "patterns.yaml"  # type: ignore[operator]
    p.write_text(yaml_text)
    scorer = DriftScorer(str(p))
    domain = scorer.load_project_domains()[0]
    report = scorer.score(_fp_with_dag(2), domain)
    assert any("dag_depth" in v for v in report.violations)  # 2 < min 3


def test_unknown_param_key_raises(tmp_path: pytest.TempPathFactory) -> None:
    """Overriding a parameter the template never declared is a config error."""
    yaml_text = _YAML_EXTENDED.replace(
        "params: {min_depth: 2}", "params: {max_fanout: 5}"
    )
    p = tmp_path / "patterns.yaml"  # type: ignore[operator]
    p.write_text(yaml_text)
    scorer = DriftScorer(str(p))
    domain = scorer.load_project_domains()[0]
    with pytest.raises(ValueError, match="max_fanout"):
        scorer.score(_fp_with_dag(2), domain)


def test_unresolvable_placeholder_raises(tmp_path: pytest.TempPathFactory) -> None:
    """A $placeholder with no declared parameter is a config error."""
    yaml_text = _YAML_EXTENDED.replace("    params:\n      min_depth: 3\n", "")
    p = tmp_path / "patterns.yaml"  # type: ignore[operator]
    yaml_no_override = yaml_text.replace("    params: {min_depth: 2}\n", "")
    p.write_text(yaml_no_override)
    scorer = DriftScorer(str(p))
    domain = scorer.load_project_domains()[0]
    with pytest.raises(ValueError, match="min_depth"):
        scorer.score(_fp_with_dag(2), domain)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_drift.py -v -k "param or placeholder"`
Expected: FAIL — `float("$min_depth")` raises `ValueError: could not convert string to float` inside `_parse_constraints` (the wrong error path: it must become a *named* config error, and valid substitutions must work).

- [ ] **Step 3: Implement**

In `drift.py`, change `_parse_constraints` from `@staticmethod`-style instance method into a params-aware one and add `_merge_params`:

```python
    @staticmethod
    def _merge_params(
        template: dict[str, Any], domain: DomainConfig
    ) -> dict[str, float]:
        """Merge template parameter defaults with domain overrides; unknown keys fail loud."""
        declared = {
            k: float(v) for k, v in (template.get("params") or {}).items()
        }
        unknown = set(domain.params) - set(declared)
        if unknown:
            msg = (
                f"Domain '{domain.name}' overrides undeclared parameter(s) "
                f"{sorted(unknown)} for pattern '{domain.expected_pattern}'."
            )
            raise ValueError(msg)
        return {**declared, **domain.params}

    def _parse_constraints(
        self, template: dict[str, Any], params: dict[str, float]
    ) -> dict[str, tuple[str, float]]:
        """Extract (operator, value) pairs for each constrained component, resolving $params."""
        result: dict[str, tuple[str, float]] = {}
        for name in _COMPONENT_NAMES:
            constraint = template.get(name)
            if constraint is None or not isinstance(constraint, dict):
                continue
            for op in ("min", "max", "exact"):
                if op in constraint:
                    result[name] = (op, self._resolve_value(constraint[op], params))
                    break
        return result

    @staticmethod
    def _resolve_value(value: Any, params: dict[str, float]) -> float:
        """Return a numeric constraint value, substituting a $name placeholder if present."""
        if isinstance(value, str) and value.startswith("$"):
            key = value[1:]
            if key not in params:
                msg = f"Constraint placeholder '${key}' has no declared parameter."
                raise ValueError(msg)
            return params[key]
        return float(value)
```

In `score()`, wire it up (replaces the old `constraints = self._parse_constraints(template)` line):

```python
        params = self._merge_params(template, domain)
        constraints = self._parse_constraints(template, params)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_drift.py -v`
Expected: all PASS, including Task 2's `test_profile_weights_used_when_domain_names_profile`.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/query/drift.py tests/unit/test_drift.py
git commit -m "feat(drift): parameterized templates — params block with \$name substitution"
```

---

### Task 4: Global hygiene block + hygiene-only domains

Spec §2.1: hygiene constraints are global invariants applied to **every** domain; a domain with no `expected_pattern` is scored against hygiene alone (replaces `leaf_module`). Template constraints win over hygiene on conflict.

**Files:**
- Modify: `src/cgis/query/drift.py` (`score()`)
- Test: `tests/unit/test_drift.py`

- [ ] **Step 1: Write the failing tests**

```python
# ── Task 4: hygiene block ─────────────────────────────────────────────────────

_YAML_HYGIENE = """\
version: "2.0.0"
drift_weights:
  hub_count:        0.15
  star_count:       0.15
  chain_len:        0.10
  dag_depth:        0.10
  router_count:     0.10
  cycle_ratio:      0.25
  unresolved_ratio: 0.15
hygiene:
  cycle_ratio:      {max: 0.0}
  unresolved_ratio: {max: 0.2}
patterns:
  pure_utility:
    description: "Hub"
    hub_count:        {min: 1}
    unresolved_ratio: {max: 0.1}
project_domains:
  - name: "hygiene_only"
    fqn_prefix: "hooks"
    drift_tolerance: 0.15
  - name: "templated"
    fqn_prefix: "lib"
    expected_pattern: pure_utility
    drift_tolerance: 0.15
"""


@pytest.fixture
def hygiene_scorer(tmp_path: pytest.TempPathFactory) -> DriftScorer:
    """Return a DriftScorer with a global hygiene block."""
    p = tmp_path / "patterns.yaml"  # type: ignore[operator]
    p.write_text(_YAML_HYGIENE)
    return DriftScorer(str(p))


def _fp(cycle: float = 0.0, unresolved: float = 0.0, hub: int = 1) -> PatternFingerprint:
    """Fingerprint helper for hygiene tests."""
    return PatternFingerprint(
        domain="hooks",
        hub_count=hub,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=cycle,
        unresolved_ratio=unresolved,
    )


def test_hygiene_only_domain_clean_when_hygienic(hygiene_scorer: DriftScorer) -> None:
    """No expected_pattern + clean cycles/resolution → zero drift."""
    domain = hygiene_scorer.load_project_domains()[0]
    report = hygiene_scorer.score(_fp(), domain)
    assert report.drift_score == pytest.approx(0.0)
    assert report.expected_pattern is None


def test_hygiene_only_domain_flags_cycles(hygiene_scorer: DriftScorer) -> None:
    """A cycle in a hygiene-only domain violates the global invariant."""
    domain = hygiene_scorer.load_project_domains()[0]
    report = hygiene_scorer.score(_fp(cycle=0.5), domain)
    assert report.drift_score > 0.0
    assert any("cycle_ratio" in v for v in report.violations)


def test_template_constraint_wins_over_hygiene(hygiene_scorer: DriftScorer) -> None:
    """pure_utility's unresolved max:0.1 overrides hygiene's max:0.2."""
    domain = hygiene_scorer.load_project_domains()[1]
    report = hygiene_scorer.score(_fp(unresolved=0.15), domain)
    # 0.15 is legal under hygiene (0.2) but violates the template (0.1)
    assert any("unresolved_ratio" in v for v in report.violations)


def test_hygiene_applies_to_templated_domain(hygiene_scorer: DriftScorer) -> None:
    """pure_utility has no cycle constraint of its own — hygiene's max:0.0 still applies."""
    domain = hygiene_scorer.load_project_domains()[1]
    report = hygiene_scorer.score(_fp(cycle=0.3), domain)
    assert any("cycle_ratio" in v for v in report.violations)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_drift.py -v -k "hygiene"`
Expected: FAIL — `NotImplementedError` (Task 1's guard) for hygiene-only; missing cycle violation for templated domain.

- [ ] **Step 3: Implement**

In `score()`, remove the Task 1 `NotImplementedError` guard and restructure the template/constraint resolution (top of the method becomes):

```python
        if domain.expected_pattern is None:
            template = {}
            params: dict[str, float] = {}
        else:
            found = self._patterns.get(domain.expected_pattern)
            if found is None:
                msg = f"Expected pattern '{domain.expected_pattern}' not found in patterns config."
                raise ValueError(msg)
            if not isinstance(found, dict):
                msg = f"Pattern '{domain.expected_pattern}' must be a mapping of constraints."
                raise TypeError(msg)
            template = found
            params = self._merge_params(template, domain)

        hygiene = self._parse_constraints(self._hygiene, {})
        constraints = {**hygiene, **self._parse_constraints(template, params)}
```

(The dict-merge order implements "template wins": template entries overwrite hygiene entries per component.)

`DriftReport.expected_pattern` becomes `str | None` (update the dataclass annotation at `drift.py:43`). Check the one renderer: `src/cgis/cli.py:_render_drift_table` prints `r.expected_pattern` — render `"(hygiene)"` for `None`:

```python
            r.expected_pattern or "(hygiene)",
```

- [ ] **Step 4: Run tests + type-check**

Run: `uv run pytest tests/unit/ -v && make type-check`
Expected: all PASS, mypy clean (the `str | None` flows through `DriftReport` and the CLI renderer).

- [ ] **Step 5: Commit**

```bash
git add src/cgis/query/drift.py src/cgis/cli.py tests/unit/test_drift.py
git commit -m "feat(drift): global hygiene invariants; hygiene-only domains replace leaf_module"
```

---

### Task 5: Confidence discount on CALLS-derived weights

Spec §2.3: `effective_weight(c) = weight(c) * (1 - unresolved_ratio)` for `hub_count`, `star_count`, `chain_len`, `router_count`. No floor. IMPORTS-derived (`dag_depth`, `cycle_ratio`) and `unresolved_ratio` itself are not discounted. Renormalize after discount.

**Files:**
- Modify: `src/cgis/query/drift.py` (`score()`, new module constant)
- Test: `tests/unit/test_drift.py`

- [ ] **Step 1: Write the failing tests**

```python
# ── Task 5: confidence discount ───────────────────────────────────────────────

_YAML_DISCOUNT = """\
version: "2.0.0"
drift_weights:
  hub_count:        0.25
  star_count:       0.25
  chain_len:        0.0
  dag_depth:        0.25
  router_count:     0.0
  cycle_ratio:      0.25
  unresolved_ratio: 0.0
patterns:
  mixed:
    description: "CALLS + IMPORTS constraints"
    hub_count:   {min: 2}
    star_count:  {exact: 0}
    dag_depth:   {min: 2}
    cycle_ratio: {max: 0.0}
project_domains:
  - name: "m"
    fqn_prefix: "m"
    expected_pattern: mixed
    drift_tolerance: 0.5
"""


@pytest.fixture
def discount_scorer(tmp_path: pytest.TempPathFactory) -> DriftScorer:
    """Return a DriftScorer for confidence-discount tests."""
    p = tmp_path / "patterns.yaml"  # type: ignore[operator]
    p.write_text(_YAML_DISCOUNT)
    return DriftScorer(str(p))


def _violating_fp(unresolved: float) -> PatternFingerprint:
    """All four constraints violated; only unresolved_ratio varies."""
    return PatternFingerprint(
        domain="m",
        hub_count=0,  # violates min:2
        star_count=3,  # violates exact:0
        chain_len=0.0,
        dag_depth=0,  # violates min:2
        router_count=0,
        cycle_ratio=1.0,  # violates max:0.0
        unresolved_ratio=unresolved,
    )


def test_full_unresolved_zeroes_calls_layer(discount_scorer: DriftScorer) -> None:
    """At unresolved=1.0 the CALLS components carry zero weight; only IMPORTS drift remains.

    All components maximally violated: with no discount the score would be 1.0.
    With CALLS (hub 0.25 + star 0.25) zeroed, renormalized IMPORTS keep score = 1.0
    for the two violated IMPORTS components — so compare against the half-violated case.
    """
    domain = discount_scorer.load_project_domains()[0]
    fp_imports_clean = PatternFingerprint(
        domain="m",
        hub_count=0,  # violates min:2 (CALLS)
        star_count=3,  # violates exact:0 (CALLS)
        chain_len=0.0,
        dag_depth=2,  # satisfies (IMPORTS)
        router_count=0,
        cycle_ratio=0.0,  # satisfies (IMPORTS)
        unresolved_ratio=1.0,
    )
    report = discount_scorer.score(fp_imports_clean, domain)
    # CALLS violations exist but their weight is fully discounted → zero drift.
    assert report.drift_score == pytest.approx(0.0)


def test_discount_scales_calls_contribution(discount_scorer: DriftScorer) -> None:
    """All components violated: discount renormalizes but everything is drift 1.0.

    Pins the invariant: the discount changes *relative weights*; it must not
    deflate a uniformly-violated domain below 1.0.
    """
    domain = discount_scorer.load_project_domains()[0]
    assert discount_scorer.score(_violating_fp(0.0), domain).drift_score == pytest.approx(1.0)
    assert discount_scorer.score(_violating_fp(0.6), domain).drift_score == pytest.approx(1.0)


def test_discount_scaling_visible_when_only_calls_violated(
    discount_scorer: DriftScorer,
) -> None:
    """With only CALLS components violated, drift falls as unresolved rises."""
    domain = discount_scorer.load_project_domains()[0]

    def fp(unresolved: float) -> PatternFingerprint:
        return PatternFingerprint(
            domain="m",
            hub_count=0,  # violated (CALLS)
            star_count=3,  # violated (CALLS)
            chain_len=0.0,
            dag_depth=2,  # clean (IMPORTS)
            router_count=0,
            cycle_ratio=0.0,  # clean (IMPORTS)
            unresolved_ratio=unresolved,
        )

    d0 = discount_scorer.score(fp(0.0), domain).drift_score
    d6 = discount_scorer.score(fp(0.6), domain).drift_score
    # u=0.0: eff = (.25,.25,.25,.25), violated share = 0.5 → drift 0.5
    # u=0.6: eff = (.1,.1,.25,.25) tot .7, violated share = .2/.7 ≈ 0.2857
    assert d0 == pytest.approx(0.5)
    assert d6 == pytest.approx(0.2 / 0.7, abs=1e-6)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_drift.py -v -k "discount or unresolved_zeroes"`
Expected: `test_full_unresolved_zeroes_calls_layer` FAILS (score > 0 without the discount) and `test_discount_scaling_visible_when_only_calls_violated` FAILS on the `d6` value.

- [ ] **Step 3: Implement**

Add a module constant next to `_COMPONENT_NAMES` in `drift.py`:

```python
_CALLS_LAYER = frozenset({"hub_count", "star_count", "chain_len", "router_count"})
```

In `score()`, replace the weight computation:

```python
        discount = 1.0 - actual.unresolved_ratio
        raw_weights = {name: weights.get(name, 0.0) for name in constraints}
        eff_weights = {
            name: w * discount if name in _CALLS_LAYER else w
            for name, w in raw_weights.items()
        }
        raw_total = sum(raw_weights.values())
        eff_total = sum(eff_weights.values())
```

and inside the loop:

```python
            if raw_total > 0.0:
                weight = eff_weights[name] / eff_total if eff_total > 0.0 else 0.0
            else:
                weight = 1.0 / len(constraints)
            drift_sum += weight * component_drift
```

The `raw_total == 0` branch preserves the legacy equal-split fallback for weightless configs; the `eff_total == 0` case (every constrained component CALLS-discounted to nothing) yields zero drift — "no data → no structural claim" (§2.3, no floor).

Update the `DriftScorer` class docstring to mention the discount.

- [ ] **Step 4: Run full unit suite**

Run: `uv run pytest tests/unit/ -v`
Expected: all PASS. One pre-existing test interacts with the discount and must be re-verified by hand: `test_status_critical_for_god_object` uses `unresolved_ratio=0.9` → CALLS weights drop ×0.1. Recompute: constraints hub(min:1, w=.15), star(exact:0, w=.15), cyc(max:0, w=.25), unres(max:.1, w=.15); effective weights hub .015, star .015, cyc .25, unres .15, total .43. Component drifts: hub 1.0 (raw 1/norm 1), star 1.0 (5/1 capped), cyc 0.8 (.8/1), unres 0.8 (.8/1). Drift = (.015·1 + .015·1 + .25·.8 + .15·.8)/.43 = .35/.43 ≈ 0.814 ≥ 0.5 → still critical, still ≥ 0.50. PASSES unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/query/drift.py tests/unit/test_drift.py
git commit -m "feat(drift): confidence discount on CALLS-layer weights, no floor"
```

---

### Task 6: Rewrite `docs/ontology/patterns.yaml` to the unified layout

**Files:**
- Rewrite: `docs/ontology/patterns.yaml`
- Modify: `tests/unit/test_patterns_yaml.py`

- [ ] **Step 1: Update the structural validation tests first**

Rewrite `tests/unit/test_patterns_yaml.py` — full new content:

```python
"""Structural validation for docs/ontology/patterns.yaml (unified alphabet, spec §2)."""

from pathlib import Path

import yaml

PATTERNS_PATH = Path(__file__).parent.parent.parent / "docs" / "ontology" / "patterns.yaml"
PATTERNS_UI_PATH = PATTERNS_PATH.parent / "patterns_ui.yaml"

_COMPONENT_NAMES = frozenset(
    {
        "hub_count",
        "star_count",
        "chain_len",
        "dag_depth",
        "router_count",
        "cycle_ratio",
        "unresolved_ratio",
    }
)

_ALPHABET = frozenset(
    {"pure_utility", "pipeline_stage", "orchestrator", "layered_dag", "dispatcher"}
)


def _load() -> dict:  # type: ignore[type-arg]
    return yaml.safe_load(PATTERNS_PATH.read_text())


def test_patterns_yaml_exists() -> None:
    """The patterns.yaml file must exist at docs/ontology/patterns.yaml."""
    assert PATTERNS_PATH.exists()


def test_patterns_ui_yaml_deleted() -> None:
    """The forked UI ontology must not exist — the alphabet is single (spec §2.4)."""
    assert not PATTERNS_UI_PATH.exists()


def test_required_top_level_keys() -> None:
    """Top-level keys: version, profiles, hygiene, patterns, project_domains."""
    data = _load()
    for key in ("version", "profiles", "hygiene", "patterns", "project_domains"):
        assert key in data, f"Missing top-level key '{key}'"


def test_alphabet_is_closed() -> None:
    """Exactly the five templates of the closed alphabet — no more, no less."""
    data = _load()
    assert set(data["patterns"].keys()) == _ALPHABET


def test_each_profile_weights_cover_all_components_and_sum_to_one() -> None:
    """Every profile's drift_weights has exactly the 7 components, summing to 1.0."""
    data = _load()
    assert set(data["profiles"].keys()) >= {"python", "typescript"}
    for name, profile in data["profiles"].items():
        weights = profile["drift_weights"]
        assert set(weights.keys()) == _COMPONENT_NAMES, f"profile '{name}'"
        assert abs(sum(weights.values()) - 1.0) < 1e-9, f"profile '{name}'"


def test_hygiene_uses_known_components() -> None:
    """The hygiene block constrains only known components."""
    data = _load()
    assert set(data["hygiene"].keys()) <= _COMPONENT_NAMES


def test_all_pattern_constraints_use_known_components() -> None:
    """No template references an unknown component (params/description excluded)."""
    data = _load()
    for pattern_name, template in data["patterns"].items():
        for key in template:
            if key in ("description", "params"):
                continue
            assert key in _COMPONENT_NAMES, f"Unknown component '{key}' in '{pattern_name}'"


def test_each_constraint_has_exactly_one_operator() -> None:
    """Each constraint (templates and hygiene) has exactly one of min/max/exact."""
    data = _load()
    blocks = [*data["patterns"].values(), data["hygiene"]]
    for template in blocks:
        for key, value in template.items():
            if key in ("description", "params") or not isinstance(value, dict):
                continue
            ops = set(value.keys()) & {"min", "max", "exact"}
            assert len(ops) == 1, f"Constraint '{key}' needs exactly one operator, got {ops}"


def test_placeholders_resolve_to_declared_params() -> None:
    """Every $name in a template resolves to that template's params block."""
    data = _load()
    for pattern_name, template in data["patterns"].items():
        declared = set((template.get("params") or {}).keys())
        for key, value in template.items():
            if key in ("description", "params") or not isinstance(value, dict):
                continue
            for op_value in value.values():
                if isinstance(op_value, str) and op_value.startswith("$"):
                    assert op_value[1:] in declared, (
                        f"'{op_value}' in '{pattern_name}.{key}' is undeclared"
                    )


def test_project_domains_have_required_fields() -> None:
    """Each domain has name, fqn_prefix, profile, drift_tolerance (expected_pattern optional)."""
    data = _load()
    for domain in data["project_domains"]:
        for key in ("name", "fqn_prefix", "profile", "drift_tolerance"):
            assert key in domain, f"Domain '{domain.get('name', '?')}' missing '{key}'"


def test_project_domains_reference_known_patterns_and_profiles() -> None:
    """expected_pattern (when present) and profile must reference declared entries."""
    data = _load()
    known_patterns = set(data["patterns"].keys())
    known_profiles = set(data["profiles"].keys())
    for domain in data["project_domains"]:
        if "expected_pattern" in domain:
            assert domain["expected_pattern"] in known_patterns, domain["name"]
        assert domain["profile"] in known_profiles, domain["name"]


def test_domain_params_override_only_declared_params() -> None:
    """Domain params keys must be declared in the bound template's params block."""
    data = _load()
    for domain in data["project_domains"]:
        if "params" not in domain:
            continue
        template = data["patterns"][domain["expected_pattern"]]
        declared = set((template.get("params") or {}).keys())
        assert set(domain["params"].keys()) <= declared, domain["name"]
```

- [ ] **Step 2: Run to verify the new expectations fail against the old file**

Run: `uv run pytest tests/unit/test_patterns_yaml.py -v`
Expected: FAIL — `test_patterns_ui_yaml_deleted`, `test_required_top_level_keys` (no profiles/hygiene), `test_project_domains_have_required_fields` (no profile keys).

- [ ] **Step 3: Rewrite `docs/ontology/patterns.yaml`** (full new content)

```yaml
version: "2.0.0"

# ── Measurement profiles (spec §2.3) ─────────────────────────────────────────
# The pattern alphabet is single and closed; graphs differ only in how they
# are *measured*. Each domain binding names its profile. Weights per profile
# must sum exactly to 1.0.
#
# CALLS-derived components (hub_count, star_count, chain_len, router_count)
# are additionally discounted at scoring time by (1 - unresolved_ratio) —
# the noisier the call resolution, the less the call topology asserts.
profiles:
  python:
    drift_weights:
      hub_count:        0.15
      star_count:       0.15
      chain_len:        0.10
      dag_depth:        0.10
      router_count:     0.10
      cycle_ratio:      0.25
      unresolved_ratio: 0.15
  typescript:
    drift_weights:
      hub_count:        0.10
      star_count:       0.10
      chain_len:        0.10
      dag_depth:        0.15
      router_count:     0.10
      cycle_ratio:      0.30
      unresolved_ratio: 0.15

# ── Global hygiene invariants (spec §2.1) ────────────────────────────────────
# Applied to EVERY domain, template-bound or not. A domain that wants
# "hygiene only" simply declares no expected_pattern. Template constraints
# win over hygiene on the same component.
hygiene:
  cycle_ratio:      {max: 0.0}
  unresolved_ratio: {max: 0.2}

# ── The closed alphabet: five templates (spec §2.1) ──────────────────────────
# Each component key accepts exactly one of: {min: X}, {max: X}, {exact: X}.
# Values may reference template parameters as $name; parameters live in the
# params block and may be overridden per-domain (unknown keys fail loud).
patterns:
  pure_utility:
    description: "Shared library called by many; depends on nothing"
    hub_count:        {min: 1}
    star_count:       {exact: 0}
    unresolved_ratio: {max: 0.1}

  pipeline_stage:
    description: "Sequential transformer — one input domain, one output domain"
    chain_len:        {min: 2.0}
    star_count:       {max: 1}

  orchestrator:
    description: "Coordinates N independent services; no leaf-to-leaf edges"
    star_count:       {min: 1}
    hub_count:        {max: 1}

  layered_dag:
    description: "Clean layered architecture; no upward dependencies"
    params:
      min_depth: 3
    dag_depth:        {min: $min_depth}

  dispatcher:
    # Planned — requires ResolverEngine split (#115).
    description: "Routes to the first matching strategy; mutually exclusive paths"
    router_count:     {min: 1}
    star_count:       {exact: 0}

# ── Project domain bindings ──────────────────────────────────────────────────
# drift_tolerance is a ratchet: it captures the accepted drift of the current
# codebase (measured by tests/self_parsing/test_drift.py) and must only go
# DOWN as the structure improves. Raising it requires a review discussion.
project_domains:
  # Python graph (ingest root: src/ → FQNs carry the cgis. prefix)
  - name: "extraction"
    fqn_prefix: "cgis.extractors"
    expected_pattern: pure_utility
    profile: python
    drift_tolerance: 0.25 # measured 0.21 (11 internal stars)

  - name: "resolution"
    fqn_prefix: "cgis.resolver"
    expected_pattern: pipeline_stage
    profile: python
    drift_tolerance: 0.35 # measured 0.30 (6 internal stars)

  - name: "pipeline"
    fqn_prefix: "cgis.pipeline"
    expected_pattern: orchestrator
    profile: python
    drift_tolerance: 0.25 # measured 0.00

  - name: "storage"
    fqn_prefix: "cgis.storage"
    expected_pattern: pure_utility
    profile: python
    drift_tolerance: 0.25 # measured 0.21 (1 internal star)

  - name: "query"
    fqn_prefix: "cgis.query"
    expected_pattern: layered_dag
    profile: python
    drift_tolerance: 0.20 # measured 0.00

  # TypeScript graph (ingest root: ui/src → components/Foo.tsx = components.Foo)
  - name: "components"
    fqn_prefix: "components"
    expected_pattern: layered_dag
    profile: typescript
    params: {min_depth: 2} # JSX component trees are legitimately shallower
    drift_tolerance: 0.15 # measured 0.00

  - name: "layout"
    fqn_prefix: "layout"
    expected_pattern: pure_utility
    profile: typescript
    drift_tolerance: 0.15 # measured 0.00

  # Hygiene-only domains (no expected_pattern): acyclic + mostly resolved.
  - name: "hooks"
    fqn_prefix: "hooks"
    profile: typescript
    drift_tolerance: 0.15 # measured 0.00

  - name: "store"
    fqn_prefix: "store"
    profile: typescript
    drift_tolerance: 0.15 # measured 0.00

  - name: "providers"
    fqn_prefix: "providers"
    profile: typescript
    drift_tolerance: 0.15 # measured 0.00

  - name: "utils"
    fqn_prefix: "utils"
    profile: typescript
    drift_tolerance: 0.15 # measured 0.00
```

Notes locked by measurement (2026-06-09): templates drop their per-template `cycle_ratio: {max: 0.0}` lines — the hygiene block now provides exactly that constraint globally, and the dict-merge keeps scoring identical (all measured cycle_ratios are 0.00). `pure_utility` keeps its stricter `unresolved_ratio max 0.1` (template wins over hygiene's 0.2).

- [ ] **Step 4: Delete the fork and run validation**

```bash
git rm docs/ontology/patterns_ui.yaml
uv run pytest tests/unit/test_patterns_yaml.py tests/unit/test_drift.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/ontology/patterns.yaml tests/unit/test_patterns_yaml.py
git commit -m "feat(ontology): unified pattern alphabet — profiles, hygiene, params; delete patterns_ui.yaml"
```

---

### Task 7: Self-parsing drift tests against the single file

**Files:**
- Modify: `tests/self_parsing/test_drift.py`

- [ ] **Step 1: Update the self-parsing tests**

In `tests/self_parsing/test_drift.py`:

1. Delete line 18: `_PATTERNS_UI = str(ONTOLOGY_DIR / "patterns_ui.yaml")`.
2. Module docstring: replace `patterns*.yaml` with `patterns.yaml`.
3. The Python tests score only `cgis.*` domains and the TS tests only UI domains — but `load_project_domains()` now returns all 11. Filtering is required or `_assert_domains_not_empty` fails cross-graph (UI prefixes match nothing in the cgis graph and vice versa). Domains are selected by FQN root — the `profile` field is *measurement* metadata, not graph membership. Add `DomainConfig` to the import from `cgis.query.drift` and replace both helpers:

```python
def _selected_domains(scorer: DriftScorer, graph: str) -> list[DomainConfig]:
    """Return the project domains belonging to one graph: 'python' or 'typescript'."""
    domains = scorer.load_project_domains()
    if graph == "python":
        return [d for d in domains if d.fqn_prefix.startswith("cgis.")]
    return [d for d in domains if not d.fqn_prefix.startswith("cgis.")]


def _assert_within_tolerance(store: SQLiteStore, patterns_path: str, graph: str) -> None:
    """Score one graph's project domains and fail if any exceeds its declared tolerance."""
    scorer = DriftScorer(patterns_path)
    extractor = FingerprintExtractor(store)
    failures: list[str] = []

    for domain in _selected_domains(scorer, graph):
        fp = extractor.extract(domain.fqn_prefix)
        report = scorer.score(fp, domain)
        if report.drift_score > domain.drift_tolerance:
            failures.append(
                _EXCEEDED.format(
                    name=domain.name,
                    prefix=domain.fqn_prefix,
                    score=report.drift_score,
                    pattern=domain.expected_pattern or "(hygiene)",
                    tol=domain.drift_tolerance,
                    violations="\n".join(f"  - {v}" for v in report.violations) or "  (none)",
                )
            )

    assert not failures, "\n\n".join(failures)


def _assert_domains_not_empty(store: SQLiteStore, patterns_path: str, graph: str) -> None:
    """Every selected domain must match at least one real node in the graph.

    Guards against silent all-zero fingerprints when FQN prefixes and ingest
    roots drift apart (e.g. ingesting src/cgis instead of src/ produces
    pipeline.* FQNs that no cgis.* prefix matches).
    """
    scorer = DriftScorer(patterns_path)
    all_ids = [n.id for n in store.get_all_nodes()]
    empty = [
        f"{d.name} ({d.fqn_prefix})"
        for d in _selected_domains(scorer, graph)
        if not any(i == d.fqn_prefix or i.startswith(d.fqn_prefix + ".") for i in all_ids)
    ]
    assert not empty, f"Declared domains match no nodes in the graph: {', '.join(empty)}"
```

The four tests become:

```python
def test_py_domains_match_nodes(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """All cgis.* domains declared in patterns.yaml exist in the self-parsed graph."""
    store, _, _ = root_graph_data
    _assert_domains_not_empty(store, _PATTERNS, graph="python")


def test_py_self_drift_within_tolerance(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Every cgis domain's drift score stays within its declared ratchet tolerance."""
    store, _, _ = root_graph_data
    _assert_within_tolerance(store, _PATTERNS, graph="python")


@skip_if_no_ui
def test_ts_domains_match_nodes(
    ts_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """All UI domains declared in patterns.yaml exist in the ui/src graph."""
    store, _, _ = ts_graph_data
    _assert_domains_not_empty(store, _PATTERNS, graph="typescript")


@skip_if_no_ui
def test_ts_self_drift_within_tolerance(
    ts_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Every UI domain's drift score stays within its declared ratchet tolerance."""
    store, _, _ = ts_graph_data
    _assert_within_tolerance(store, _PATTERNS, graph="typescript")
```

- [ ] **Step 2: Run the self-parsing suite**

Run: `uv run pytest tests/self_parsing/ -v`
Expected: all PASS. Measured 2026-06-09 (this plan's baseline): every UI domain scores 0.00 under the unified file (`components` dag=2 with min_depth=2; `layout` hub=1 star=0; hygiene-only domains all cyc=0.00 unres=0.00) and Python scores are unchanged (unres=0.00 everywhere → discount is a no-op; hygiene cyc/unres constraints all satisfied). If a score unexpectedly exceeds its tolerance, STOP — that is a scorer regression introduced in Tasks 2–5, not a ratchet to renegotiate.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS (446 + new tests).

- [ ] **Step 4: Commit**

```bash
git add tests/self_parsing/test_drift.py
git commit -m "test(self-parsing): both graphs ratchet against the single unified patterns.yaml"
```

---

### Task 8: `gen_ideal_graph.py` guard + full verification

**Files:**
- Modify: `scripts/gen_ideal_graph.py:~350` (`--from-ontology` path)

- [ ] **Step 1: Inspect and guard hygiene-only domains**

`scripts/gen_ideal_graph.py` reads `project_domains` dicts directly (line ~350: `domains: list[dict[str, Any]] = raw.get("project_domains") or []`) and generates an ideal subgraph per `expected_pattern`. Hygiene-only domains have no pattern to generate. Find the loop consuming `domains` and add a skip with a notice:

```python
    for domain in domains:
        pattern = domain.get("expected_pattern")
        if pattern is None:
            print(f"  (skip) {domain['name']}: hygiene-only domain, no ideal topology")
            continue
```

(Adapt variable names to the actual loop — read the function before editing. If the loop already uses `domain["expected_pattern"]`, this is the KeyError-in-waiting the guard removes.)

- [ ] **Step 2: Smoke-test the script and the CLI**

```bash
uv run python scripts/gen_ideal_graph.py --from-ontology docs/ontology/patterns.yaml --output /tmp/ideal.graph.json
uv run cgis ingest src --output /tmp/part_a_smoke.db
uv run cgis drift --db /tmp/part_a_smoke.db --patterns docs/ontology/patterns.yaml
```

Expected: script lists 7 generated + 4 skipped domains; `cgis drift` renders the table with `(hygiene)` rows and exits 0. (The cgis.db smoke run scores UI domains as empty fingerprints — all-zero topology passes hygiene; that's acceptable for a smoke test, the real gate is the self-parsing suite.)

- [ ] **Step 3: Full verification (pre-PR gate)**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
```

Expected: all green, doc coverage ≥ 90%.

- [ ] **Step 4: Commit**

```bash
git add scripts/gen_ideal_graph.py
git commit -m "fix(scripts): gen_ideal_graph skips hygiene-only domains"
```

---

## Out of Scope (Part B/C — do not implement here)

- Triad census, TV distance, quotient/fractal drift (`spec §3`) — separate plan.
- Typed codons / ATCG (`spec §4`) — gated on #47.
- Violation suppression for fully-discounted components — violations stay reported even when their weight is discounted to zero; only the *score* honors the discount. Revisit in Part B if noisy.

## Verification Summary

1. `uv run pytest -q` — full suite green.
2. `make format && make lint && make type-check && make pytest && make doc-coverage` — all green.
3. `docs/ontology/patterns_ui.yaml` does not exist; `grep -rn patterns_ui` over `src/ tests/ scripts/ docs/ontology/` returns nothing.
4. Self-drift ratchets: Python tolerances byte-identical to PR #141 values; UI tolerances 0.15 with measured 0.00.
