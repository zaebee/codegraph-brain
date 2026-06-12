# Drift Empty/No-Signal Domains (#178) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A mis-targeted `fqn_prefix` fails loudly (`⛔ EMPTY`, exit 1, "did you mean" suggestions) instead of reporting 100% green — closing #178 (roadmap #179 P0).

**Architecture:** Per `docs/specs/2026-06-12-drift-empty-domains-design.md` INCLUDING Amendment 1 (read both). `PatternFingerprint` gains `node_count`/`edge_count` (default 1 = "hand-built fingerprints are measurable"; only `extract()` produces real zeros). `DriftScorer.score()` gets two guards before all scoring paths: `empty` (node_count==0) and `no_signal` (edge_count==0). `analyze_drift` gains the status-aware empty gate term (enforce-respecting via zip), closest-prefix suggestions via `find_nodes_by_suffix`, and a `profile` filter. CLI/MCP wire it through. Pure query/report layer — graph, census, ratchets untouched.

**Tech Stack:** Python 3.12, dataclasses, pytest, mypy strict, ruff, interrogate ≥90%.

**Branch:** `feat/issue-178-empty-domains`

**Hard rules for every task:**
- Run the FULL unit suite (`uv run pytest tests/unit/ -q`) before committing, never just the task's file.
- All 22 existing hand-built `PatternFingerprint` fixtures must pass UNCHANGED — if one breaks, your guard logic is wrong (see Amendment 1), do not edit fixtures.
- NEVER touch `drift_tolerance` values. Docstrings everywhere (interrogate ≥90%).
- Shell quirk: use `/bin/rm -f` instead of bare rm.

---

### Task 1: Fingerprint counts (TDD)

**Files:**
- Modify: `src/cgis/query/fingerprint.py` (`PatternFingerprint` fields + both paths of `extract`)
- Test: `tests/unit/test_fingerprint.py` (append)

- [ ] **Step 1: Write the failing tests**

Read `tests/unit/test_fingerprint.py` first to reuse its store/extractor fixture pattern, then append (adapt fixture mechanics to what the file already uses — it builds a `FingerprintExtractor` over an in-memory/SQLite store with Node/Edge fixtures):

```python
def test_extract_sets_node_and_edge_counts(...existing fixture...) -> None:
    """extract() records how many nodes and intra-domain edges it measured."""
    # Using the file's existing populated-store fixture for a domain with
    # at least 2 nodes and 1 intra-domain edge:
    fp = extractor.extract("<existing-fixture-prefix>")
    assert fp.node_count >= 2
    assert fp.edge_count >= 1


def test_extract_zero_match_sets_zero_counts(...existing fixture...) -> None:
    """A prefix matching nothing yields node_count == edge_count == 0."""
    fp = extractor.extract("totally.missing.prefix")
    assert fp.node_count == 0
    assert fp.edge_count == 0


def test_extract_isolated_node_has_no_signal_counts(...) -> None:
    """A single matched node with no intra-domain edges: nodes>0, edges==0."""
    # Build a store with one node under prefix "lone" and no edges between
    # domain members (mirror the file's store-construction helper):
    fp = extractor.extract("lone")
    assert fp.node_count == 1
    assert fp.edge_count == 0


def test_hand_built_fingerprint_defaults_are_measurable() -> None:
    """Hand-built fingerprints default to node_count=1/edge_count=1 (Amendment 1)."""
    fp = PatternFingerprint(
        domain="x",
        hub_count=0,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )
    assert fp.node_count == 1
    assert fp.edge_count == 1
```

The first three tests must follow the EXACT fixture idiom already in the file (named fixtures or inline store construction) — the assertions above are the contract; the setup lines mirror neighbors.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_fingerprint.py -q`
Expected: new tests FAIL with `TypeError: unexpected keyword` / `AttributeError: node_count`.

- [ ] **Step 3: Implement**

In `src/cgis/query/fingerprint.py`:

a) `PatternFingerprint` — after the `t_calls` field add:

```python
    # How many graph nodes / intra-domain edges the selector matched.
    # Defaults are 1 ("hand-built fingerprints are assumed measurable",
    # spec Amendment 1); only extract() produces real zeros. 0 nodes means
    # the fqn_prefix selected nothing; 0 edges with >0 nodes means there is
    # no structure to score (isolated symbols / alias-only matches).
    node_count: int = 1
    edge_count: int = 1
```

b) `extract()` early return (`if not domain_nodes:`) — add `node_count=0, edge_count=0` to the returned `PatternFingerprint(...)`.

c) `extract()` normal-path return — add `node_count=len(domain_nodes), edge_count=len(internal_edges)` to the final `PatternFingerprint(...)` (the `internal_edges` list already exists in scope).

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/unit/test_fingerprint.py -q && uv run pytest tests/unit/ -q && make type-check`
Expected: all pass — INCLUDING all 22 untouched hand-built fixtures in test_drift.py.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/query/fingerprint.py tests/unit/test_fingerprint.py
git commit -m "feat(fingerprint): node_count/edge_count with measurable defaults (#178 task 1)"
```

---

### Task 2: Scorer guards (TDD)

**Files:**
- Modify: `src/cgis/query/drift.py` (`DriftReport.status` Literal + `note` field, two guards in `score()`, `_signal_report` helper)
- Test: `tests/unit/test_drift.py` (append; existing tests untouched)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_drift.py` (it already imports `DomainConfig`, `DriftScorer`, `PatternFingerprint`, `pytest` and has the `scorer` + `pure_util_domain` fixtures):

```python
# ---------------------------------------------------------------------------
# empty / no_signal guards (#178)
# ---------------------------------------------------------------------------


def _structural_zero_fp(node_count: int, edge_count: int) -> PatternFingerprint:
    """All-zero fingerprint with explicit match counts (#178 guard tests)."""
    return PatternFingerprint(
        domain="cgis.extractors",
        hub_count=0,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
        node_count=node_count,
        edge_count=edge_count,
    )


def test_zero_match_domain_is_empty_not_clean(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """node_count == 0 → status 'empty', score 0.0, no violations (#178)."""
    report = scorer.score(_structural_zero_fp(node_count=0, edge_count=0), pure_util_domain)
    assert report.status == "empty"
    assert report.drift_score == pytest.approx(0.0)
    assert report.violations == []


def test_edgeless_domain_is_no_signal_not_clean(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """Nodes matched but zero intra-domain edges → status 'no_signal' (#178)."""
    report = scorer.score(_structural_zero_fp(node_count=3, edge_count=0), pure_util_domain)
    assert report.status == "no_signal"
    assert report.drift_score == pytest.approx(0.0)


def test_guards_precede_hygiene_only_path(scorer: DriftScorer) -> None:
    """A hygiene-only domain (no expected_pattern) with 0 nodes is 'empty' too."""
    hygiene_domain = DomainConfig(
        name="ghost",
        fqn_prefix="ghost.prefix",
        expected_pattern=None,
        profile=None,
        drift_tolerance=0.2,
    )
    report = scorer.score(_structural_zero_fp(node_count=0, edge_count=0), hygiene_domain)
    assert report.status == "empty"


def test_measured_domain_status_unchanged(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """A normal fingerprint (counts > 0) keeps today's classification path."""
    perfect = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=1,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
        node_count=4,
        edge_count=3,
    )
    report = scorer.score(perfect, pure_util_domain)
    assert report.status == "clean"
```

(If `DomainConfig` requires different kwargs, mirror the file's existing `pure_util_domain` fixture shape.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_drift.py -q -k "empty or no_signal or guards_precede or unchanged"`
Expected: the empty/no_signal/guards tests FAIL (status is "clean" today); `test_measured_domain_status_unchanged` may pass already.

- [ ] **Step 3: Implement**

In `src/cgis/query/drift.py`:

a) `DriftReport` — change the status type and add `note`:

```python
    status: Literal["clean", "warning", "critical", "empty", "no_signal"]
    tolerance: float
    tv_imports: float | None = None
    tv_calls: float | None = None
    # Human-readable diagnostic (e.g. closest-prefix suggestions for "empty").
    note: str | None = None
```

b) At the TOP of `score()` (before `self._resolve_template(domain)`):

```python
        if actual.node_count == 0:
            return self._signal_report(actual, domain, status="empty")
        if actual.edge_count == 0:
            return self._signal_report(actual, domain, status="no_signal")
```

c) New private method on `DriftScorer` (place near `_zero_drift_report`):

```python
    def _signal_report(
        self,
        actual: PatternFingerprint,
        domain: DomainConfig,
        status: Literal["empty", "no_signal"],
    ) -> DriftReport:
        """Report for a domain with nothing to score: matched 0 nodes (empty)
        or matched nodes but 0 intra-domain edges (no_signal). Score is 0.0 by
        definition — the gate handles 'empty' separately (spec §2.3)."""
        return DriftReport(
            domain=domain.name,
            fqn_prefix=domain.fqn_prefix,
            expected_pattern=domain.expected_pattern,
            actual=actual,
            ideal=actual,
            drift_score=0.0,
            violations=[],
            status=status,
            tolerance=domain.drift_tolerance,
        )
```

(`ideal=actual` mirrors the no-information situation; nothing downstream reads `ideal` for these statuses.)

NOTE: `DriftScorer` is in `_KNOWN_GOD_OBJECTS` — one more method there is fine; do NOT add methods anywhere else.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/unit/test_drift.py -q && uv run pytest tests/unit/ -q && make type-check`
Expected: all pass, all pre-existing fixtures green.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/query/drift.py tests/unit/test_drift.py
git commit -m "feat(drift): empty/no_signal scorer guards before all scoring paths (#178 task 2)"
```

---

### Task 3: Service — gate term, suggestions, profile filter (TDD)

**Files:**
- Modify: `src/cgis/query/drift_service.py`
- Test: `tests/unit/test_drift_service.py` (append; existing tests untouched)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_drift_service.py` (it has `graph_db`/`patterns_file` fixtures and the `_YAML` template with domain `extraction → cgis.extractors`; reuse `Node`/`Edge`/`SQLiteStore` imports):

```python
# ---------------------------------------------------------------------------
# empty / no_signal in analyze_drift (#178)
# ---------------------------------------------------------------------------

_YAML_MISTARGETED = _YAML.replace('fqn_prefix: "cgis.extractors"', 'fqn_prefix: "click.core"')


def test_empty_domain_trips_any_critical(graph_db: str, tmp_path: Path) -> None:
    """A zero-match enforced domain fails the gate despite score 0.0 (#178)."""
    p = tmp_path / "mistargeted.yaml"
    p.write_text(_YAML_MISTARGETED)
    analysis = analyze_drift(graph_db, str(p), max_drift=1.0)
    assert analysis.reports[0].status == "empty"
    assert analysis.any_critical is True


def test_empty_domain_note_suggests_real_prefix(graph_db: str, tmp_path: Path) -> None:
    """The empty note carries closest-prefix suggestions via the suffix index."""
    # graph_db has nodes under cgis.extractors.*; mis-target with the suffix
    # so find_nodes_by_suffix can recover the real id:
    p = tmp_path / "suggest.yaml"
    p.write_text(_YAML.replace('fqn_prefix: "cgis.extractors"', 'fqn_prefix: "extractors.a"'))
    analysis = analyze_drift(graph_db, str(p), max_drift=1.0)
    report = analysis.reports[0]
    assert report.status == "empty"
    assert report.note is not None
    assert "matched 0 nodes" in report.note
    assert "cgis.extractors.a" in report.note


def test_unenforced_empty_domain_does_not_trip(graph_db: str, tmp_path: Path) -> None:
    """enforce: false keeps observe-only semantics for the new empty term."""
    yaml_text = _YAML_MISTARGETED.replace(
        'drift_tolerance: 0.15', 'drift_tolerance: 0.15\n    enforce: false'
    )
    p = tmp_path / "observed.yaml"
    p.write_text(yaml_text)
    analysis = analyze_drift(graph_db, str(p), max_drift=1.0)
    assert analysis.reports[0].status == "empty"
    assert analysis.any_critical is False


def test_no_signal_does_not_trip(tmp_path: Path) -> None:
    """A single isolated node matches → no_signal, gate stays green."""
    db = str(tmp_path / "lone.db")
    lone = Node(
        id="cgis.extractors.lonely",
        type=NodeType.FUNCTION,
        name="lonely",
        file_path="a.py",
        start_line=1,
        end_line=2,
    )
    with SQLiteStore(db) as store:
        store.save_graph([lone], [])
    p = tmp_path / "patterns.yaml"
    p.write_text(_YAML)
    analysis = analyze_drift(db, str(p), max_drift=1.0)
    assert analysis.reports[0].status == "no_signal"
    assert analysis.any_critical is False


def test_profile_filter_excludes_other_profiles(graph_db: str, tmp_path: Path) -> None:
    """profile='python' scores python + profile-less domains, skips typescript."""
    yaml_text = (
        _YAML
        + """
  - name: "ui"
    fqn_prefix: "components"
    expected_pattern: pure_utility
    profile: typescript
    drift_tolerance: 0.15
  - name: "agnostic"
    fqn_prefix: "cgis.extractors"
    drift_tolerance: 0.99
"""
    )
    p = tmp_path / "multi.yaml"
    p.write_text(yaml_text)
    filtered = analyze_drift(graph_db, str(p), max_drift=1.0, profile="python")
    names = {r.domain for r in filtered.reports}
    assert "ui" not in names          # different explicit profile: excluded
    assert "extraction" in names      # profile: python (from _YAML... see note)
    assert "agnostic" in names        # profile None matches any filter
    unfiltered = analyze_drift(graph_db, str(p), max_drift=1.0)
    assert {r.domain for r in unfiltered.reports} >= {"ui", "extraction", "agnostic"}
```

LOADER CHECK (do this FIRST): verify `DriftScorer.load_project_domains` / `_build_domain_config` actually parses an `enforce:` key for project_domains (it certainly does for project_level). If it silently ignores it, `test_unenforced_empty_domain_does_not_trip` will fail with `enforce=True` — in that case wire the key through the domain builder as part of this task (one line, same parsing as the quotient path; the spec's zip-term requires it).

IMPORTANT before writing: check whether `_YAML`'s `extraction` domain carries `profile: python` — if it does NOT (no profile key), adjust the assertion comment accordingly (it then matches via the None-matches-any rule, which is equally valid for the test's purpose). Add the needed imports (`NodeType` etc.) to the file's existing import block if missing.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_drift_service.py -q -k "empty or no_signal or profile"`
Expected: FAIL — `analyze_drift` has no `profile` kwarg; statuses are "clean"; gate stays False.

- [ ] **Step 3: Implement**

In `src/cgis/query/drift_service.py`:

a) Signature + docstring update:

```python
def analyze_drift(
    db_path: str,
    patterns_path: str,
    max_drift: float = 0.50,
    profile: str | None = None,
) -> DriftAnalysis:
```

Docstring gains: `profile: when set, only domains (and project_level bindings) whose declared profile matches are scored; domains without a profile match any filter (language-agnostic hygiene). Zero-match domains report status "empty" and trip any_critical when enforced (spec §2.3/§2.5).`

b) After `domains = scorer.load_project_domains()` add the filter (and the same for `level_bindings` after it is loaded):

```python
    if profile is not None:
        domains = [d for d in domains if d.profile is None or d.profile == profile]
```

```python
        level_bindings = [
            b for b in level_bindings if b.profile is None or b.profile == profile
        ] if profile is not None else level_bindings
```

(Adapt placement to the actual code flow — `level_bindings` is loaded inside the store context.)

c) Suggestion decoration — inside the `with SQLiteStore(db_path) as store:` block, after reports are built:

```python
        reports = [
            dataclasses.replace(r, note=_empty_note(store, r.fqn_prefix))
            if r.status == "empty"
            else r
            for r in reports
        ]
```

(Convert the existing `reports.extend(generator)` into a list built first, then decorated; import `dataclasses`.)

d) Module-level helper:

```python
def _empty_note(store: SQLiteStore, fqn_prefix: str) -> str:
    """'matched 0 nodes' diagnostic with closest-prefix suggestions (spec §2.4).

    Short-circuits on blank prefixes (no DB query). Tries the full prefix as a
    dot-boundary suffix first, then its last segment; caps at 3 suggestions.
    """
    base = f"fqn_prefix '{fqn_prefix}' matched 0 nodes"
    if not fqn_prefix.strip():
        return base
    matches = store.find_nodes_by_suffix(fqn_prefix, limit=4)
    if not matches:
        last_segment = fqn_prefix.rsplit(".", maxsplit=1)[-1]
        matches = store.find_nodes_by_suffix(last_segment, limit=4)
    if not matches:
        return base
    ids = sorted(n.id for n in matches)[:3]
    return f"{base}; did you mean: {', '.join(ids)}?"
```

(Check `find_nodes_by_suffix`'s exact return type — it returns Node objects per the suffix-FQN work in #162; adapt `n.id` if it returns rows/ids.)

e) The gate:

```python
    any_critical = (
        any(r.drift_score >= max_drift for r in reports)
        or any(r.status == "empty" for d, r in zip(domains, reports, strict=True) if d.enforce)
        or any(
            (r.drift_score >= max_drift or r.status == "empty")
            for b, r in quotient
            if b.enforce
        )
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/unit/test_drift_service.py -q && uv run pytest tests/unit/ -q && make type-check && make lint`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/query/drift_service.py tests/unit/test_drift_service.py
git commit -m "feat(drift): empty gate term, did-you-mean notes, profile filter (#178 task 3)"
```

---

### Task 4: CLI + MCP wiring (TDD where testable)

**Files:**
- Modify: `src/cgis/cli.py` (`_drift_status_label`, `_render_drift_table`, `drift` command)
- Modify: `src/cgis/api/mcp_server.py` (`cgis_drift` gains `profile`)
- Test: `tests/unit/test_cli.py` if it exists (check!) else cover via a small render unit test file `tests/unit/test_cli_drift_render.py`; MCP: extend the existing mcp test module (find with `grep -rl cgis_drift tests/`)

- [ ] **Step 1: Write the failing render tests**

Create or extend the CLI test location (read the repo's existing CLI test idiom first):

```python
def test_status_label_empty_and_no_signal() -> None:
    """Status-driven labels precede score-driven ones (#178)."""
    assert "EMPTY" in _drift_status_label(0.0, 0.5, status="empty")
    assert "no signal" in _drift_status_label(0.0, 0.5, status="no_signal")
    assert "clean" in _drift_status_label(0.0, 0.5, status="clean")
    assert "critical" in _drift_status_label(0.9, 0.5, status="critical")
```

- [ ] **Step 2: Implement CLI**

a) `_drift_status_label` — new signature `(score: float, max_drift: float, status: str = "clean")`, status-first:

```python
def _drift_status_label(score: float, max_drift: float, status: str = "clean") -> str:
    """Return a Rich-formatted status label; empty/no_signal override score."""
    if status == "empty":
        return "[bold red]⛔ EMPTY[/bold red]"
    if status == "no_signal":
        return "[yellow]◌ no signal[/yellow]"
    if score >= max_drift:
        return "[bold red]❌ critical[/bold red]"
    if score >= 0.20:
        return "[yellow]⚠️  warning[/yellow]"
    return "[green]✅ clean[/green]"
```

b) `_render_drift_table` — pass `r.status` into the label call; after a row whose `r.note` is set, add a dim full-width note row:

```python
        table.add_row(
            r.fqn_prefix,
            r.expected_pattern or "(hygiene)",
            f"{r.drift_score:.2f}",
            f"{r.tv_imports:.2f}" if r.tv_imports is not None else "—",
            f"{r.tv_calls:.2f}" if r.tv_calls is not None else "—",
            _drift_status_label(r.drift_score, max_drift, r.status),
        )
        if r.note:
            table.add_row(f"[dim]{escape(r.note)}[/dim]", "", "", "", "", "")
```

(Use the file's existing Rich-markup-escape helper/idiom — #148 history: unescaped brackets in console output broke before. Check how other dynamic strings are escaped in cli.py and mirror it; `from rich.markup import escape` if nothing local exists.)

c) `drift` command — add the option + pass-through:

```python
    profile: str | None = typer.Option(
        None,
        "--profile",
        "-P",
        help=(
            "Score only domains with this profile (plus profile-less ones). "
            "Without it, zero-match domains of OTHER profiles report EMPTY "
            "and fail the gate — use --profile when your patterns.yaml mixes "
            "languages but the db holds one graph."
        ),
    ),
```

and `analyze_drift(db, patterns, max_drift=max_drift, profile=profile)`.

The exit path needs no change — `any_critical` already drives `typer.Exit(code=1)`.

- [ ] **Step 3: Implement MCP**

`cgis_drift` gains `profile: str | None = None` parameter, passes it to `analyze_drift`, and the docstring documents it (mirror the CLI help wording, shorter). The payload needs NO change (`dataclasses.asdict` serializes `status`/`note`/counts automatically).

- [ ] **Step 4: MCP test**

Extend the existing MCP drift test (find it: `grep -rln cgis_drift tests/unit/`) with: a mis-targeted patterns file → payload JSON contains `"status": "empty"`, a non-null `"note"`, and `"any_critical": true`; and that passing `profile="typescript"` against the python fixture graph filters the domain out (empty domains list or no such report).

- [ ] **Step 5: Run everything**

Run: `uv run pytest tests/unit/ -q && make type-check && make lint`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/cgis/cli.py src/cgis/api/mcp_server.py tests/
git commit -m "feat(cli,mcp): EMPTY/no-signal rendering, notes, --profile filter (#178 task 4)"
```

---

### Task 5: Self-parse + docs + full gates

**Files:**
- Modify: `docs/ontology/patterns.yaml` (HEADER COMMENT ONLY — document the --profile interplay)
- Verify: `tests/self_parsing/` untouched and green

- [ ] **Step 1: patterns.yaml header comment**

At the top of `docs/ontology/patterns.yaml` (where the file-level comments live), add:

```yaml
# NOTE (#178): a domain whose fqn_prefix matches 0 nodes reports status
# "empty" and FAILS the gate when enforced. This file mixes python and
# typescript domains — running `cgis drift` against a single-language graph
# requires --profile (e.g. `cgis drift --profile python`) or the
# other-language domains fail loudly as EMPTY. That loud failure is the
# feature: a silent zero-match green is how mis-targeted ontologies hide.
```

No value changes anywhere in the file.

- [ ] **Step 2: Self-parsing suite**

Run: `uv run pytest tests/self_parsing/ -q`
Expected: ALL pass — test_drift.py filters by profile already and every python domain matches nodes+edges. If anything fails: STOP, report BLOCKED (do not adjust ratchets).

- [ ] **Step 3: Live repro check (the actual #178 acceptance)**

```bash
uv run cgis ingest src --source-root src -o /tmp/cgis178.db
# Mis-targeted on purpose — expect ⛔ EMPTY rows + "did you mean" + exit 1:
printf 'version: "1.0.0"\ndrift_weights:\n  hub_count: 1.0\npatterns:\n  pure_utility:\n    description: "x"\n    hub_count: {min: 0}\nproject_domains:\n  - name: "ghost"\n    fqn_prefix: "click.core"\n    expected_pattern: pure_utility\n    drift_tolerance: 0.5\n' > /tmp/ghost.yaml
uv run cgis drift --db /tmp/cgis178.db --patterns /tmp/ghost.yaml; echo "exit=$?"
# Correctly-targeted control with --profile:
uv run cgis drift --db /tmp/cgis178.db --patterns docs/ontology/patterns.yaml --profile python; echo "exit=$?"
```

Expected: first command shows `⛔ EMPTY` + a note + `exit=1`; second shows the usual table, no EMPTY rows, `exit=0`. Paste both outputs in your report. (If the ghost.yaml shape fights the loader, mirror `_YAML` from test_drift_service.py instead — the point is one mis-targeted domain end-to-end.)

- [ ] **Step 4: Full gates**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
```

- [ ] **Step 5: Commit**

```bash
git add docs/ontology/patterns.yaml
git commit -m "docs(ontology): document the empty-domain gate and --profile interplay (#178 task 5)"
```

---

## Final checklist (controller, before PR)

- [ ] All 22 pre-existing hand-built fingerprints untouched (`git diff main -- tests/unit/test_drift.py` shows only appended lines)
- [ ] `git diff main -- docs/ontology/patterns.yaml` touches ONLY the header comment
- [ ] Live repro outputs captured for the PR description
- [ ] PR body: `Closes #178`, link spec + Amendment 1, note the CLI behavior change (mixed-yaml runs need `--profile`)
