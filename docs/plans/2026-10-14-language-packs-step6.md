# Language Packs — Step 6: Gates — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the observe-only `project_level` quotient binding with the enforced gates this migration built. Give each language pack and `core` its own drift domain, so the next pack is measured against the same bar the moment it lands. Close #146.

**Architecture:** This step changes ontology configuration and self-parse tests only. No product code moves, so no stored graph can change.
- `docs/ontology/patterns.yaml` loses its `project_level` binding. `extraction` becomes one domain per pack, plus a `core` domain. `tolerances.lock` follows.
- `tests/self_parsing/test_drift.py` loses the `project_level` test. The plane-quotient report builds its own binding from `QUOTIENT_PREFIX` instead of borrowing the deleted one.
- A new test makes every registered pack declare its domain at the shared tolerance. A pack added in step 7 without a domain then fails the suite.
- The `project_level` feature itself (`DriftScorer.load_project_level`, the CLI, the guardian collector, `cgis_init_ontology`'s skeleton) stays as it is. It serves other repositories' `patterns.yaml`. Only this repository stops using it.

**Tech Stack:** Python 3.12, pytest, ruff, mypy strict, YAML.

**Spec:** `docs/specs/2026-10-14-language-packs-design.md` (rev 3): "Drift gates" (the **Domains** and **Plane quotients** paragraphs), and Migration row 6. Outline: `docs/plans/2026-10-14-language-packs.md` "Step 6". Steps 1–5: #597, #599, #600, #601, #602.

## Global Constraints

- **Full gate before every commit:** `make format && make lint && make type-check && make pytest && make doc-coverage` passes. Chain with `&&`, never with `;` or a pipe on the gating command.
- **Snapshot:** `tests/fixtures/graph_identity/snapshot.json` is not modified. Nothing in `src/` changes, so no identity diff is required. Task 3 checks that with `git diff --stat main -- src`.
- **Tolerances:**
  - A tolerance may only go down, or be set for a new domain, with the measurement in the commit message (spec, Migration).
  - The new domains' tolerances and hygiene baselines are **measured on the branch**, each set at the measurement plus the project's usual small headroom (≈0.02–0.03, rounded up to two places).
  - **Stop and report** if a domain's best-fit measurement contradicts the pattern ruled below by more than that headroom.
- **Test-count floor:** raise `.github/test-count-baseline` (now 3095) if the suite passes 3245.
- **Commits:** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- **Workspace:** worktree `/home/zaebee/projects/ownima/cgb-step6`, branch `feat/language-packs-step6`.

## Measurements taken while writing this plan (main ca32430, fresh ingest of `src/`)

| domain (prefix) | layered_dag | pure_utility | pipeline_stage | orchestrator | funnel | best fit | unresolved | nodes |
|---|---|---|---|---|---|---|---|---|
| `cgis.languages.python` | **0.393** | 0.494 | 0.523 | 0.483 | 0.328 | funnel | 0.356 | 131 |
| `cgis.languages.typescript` | **0.184** | 0.636 | 0.463 | 0.323 | 0.410 | layered_dag | 0.295 | 103 |
| `cgis.core` | **0.215** | 0.554 | 0.377 | 0.377 | 0.377 | layered_dag | 0.191 | 59 |

## Rulings — two of them need your decision (G1, G2)

- **G1 — `core` is `layered_dag`, not the spec's `pure_utility`.**
  - Measured against `pure_utility`, `core` scores 0.554, which would fail any sensible tolerance. Against `layered_dag` it scores 0.215, its best fit.
  - `core` is no longer a flat bag of utilities. Steps 1–4 put the pack contract there, and the contract is layered: `extractor → models`, `index → models`, `resolution → index, models`, `project`.
  - **Ruling (recommended):** `core` gets `layered_dag`, drift_tolerance 0.25, hygiene 0.21.
  - **Alternative:** keep `pure_utility` and restructure `core`. That is out of scope here and would undo step 1's ruling R4.
- **G2 — packs share one pattern and one tolerance, set by the loosest pack.**
  - The spec asks for one shared tolerance, so that a new pack faces the same bar as the existing ones.
  - The Python pack is the loosest on `layered_dag` at 0.393. Its best fit is `funnel` (0.328): many extractor handlers converge on `_syntax`.
  - **Ruling (recommended):** both packs `layered_dag`, shared drift_tolerance **0.42** (0.393 + headroom). This is still at or above the old `extraction` lock of 0.40, which was a single domain over both packs; it is set as new domains, with the measurement in the commit. Hygiene baselines are per pack, since unresolved ratios are language-specific: python 0.37 (the old extraction baseline), typescript 0.31.
  - **Cost:** the TypeScript pack (0.184) gets a loose bar, and so will Go. The test in Task 2 enforces the *same* tolerance for every pack, so tightening it later tightens it for all.
  - **Alternative A:** per-pack tolerances (python 0.42, typescript 0.21). That gives a tighter TS bar but drops the spec's "shared".
  - **Alternative B:** the Python pack on `funnel` (0.36) and TypeScript on `layered_dag` (0.21). Each pack gets its best fit, but there is no single bar for Go.
- **G3 — the plane-quotient report keeps running.** Deleting `project_level` removes the binding the plane test borrowed its `fqn_prefix` from. The test now builds a `DomainConfig` from `QUOTIENT_PREFIX` itself. It stays observe-only, as the spec says.
- **G4 — `_PLANES` follows the domain names** (deferred minor from step 0). The build plane becomes `pipeline, languages.python, languages.typescript, resolution, storage`, and `core` is added to neither plane (it is shared by both). If a domain name containing a dot breaks the quotient (`quotient.languages.python`), the domains are named `languages-python` / `languages-typescript` instead. Ledger which.

## Review Focus

- **A pack registered without a drift domain**, which is what step 7's Go pack would be if someone forgot. It must fail the suite and say which pack (Task 2, `test_every_pack_has_a_domain_at_the_shared_tolerance`).
- **A pack domain given a looser tolerance than the others.** It must fail, because that is what "shared" means (same test).
- **The `project_level` feature for other repositories.** It must still load and score: the `tests/unit/test_drift.py` and `test_drift_service.py` cases that write their own `project_level` YAML stay green and unmodified.
- **`cgis drift` on this repository.** With no `project_level` binding it must still print a report (no crash on an empty binding list), checked in Task 1 Step 5.
- **The tolerance lock.** It must cover exactly the declared domains (`test_tolerances_lock_covers_exactly_the_declared_domains`). `extraction` leaves and the three new domains enter in the same commit.

---

### Task 1: Retire the `project_level` binding

**Files:**
- Modify:
  - `docs/ontology/patterns.yaml:260-273` (delete the binding and its comment block)
  - `docs/ontology/tolerances.lock` (delete `project_level:`)
  - `tests/self_parsing/test_drift.py` (delete `test_py_quotient_drift_observed_not_enforced`; rewrite the plane test's binding)
  - `tests/unit/test_patterns_yaml.py:201-209` (`test_project_level_binding_observe_only` becomes the absence check below)

- [ ] **Step 1: Write the failing test.** In `tests/unit/test_patterns_yaml.py`, replace `test_project_level_binding_observe_only` with:

```python
def test_this_repository_binds_no_project_level_quotient() -> None:
    """Retired in language-packs step 6 (#146): the enforced topology gate is the
    forbidden-edge test in test_architecture.py; plane quotients are reported, not bound.
    The feature itself stays for other repositories' patterns.yaml."""
    assert "project_level" not in _load()
```

- [ ] **Step 2: Run it and watch it fail.**
  - Run: `uv run pytest tests/unit/test_patterns_yaml.py -q -k project_level`
  - Expected: FAIL. The binding is still present.

- [ ] **Step 3: Delete the binding.**
  1. In `patterns.yaml`, delete everything from the `# ── Project-level quotient binding` comment through the `enforce: false` line.
  2. In its place, leave one comment:

```yaml
# ── Project-level quotient ───────────────────────────────────────────────────
# Retired in language-packs step 6 (#146). cgis's topology is enforced by the
# forbidden-edge gate (tests/self_parsing/test_architecture.py, exact shrinking
# allowlists); the build/read plane quotients are printed observe-only by
# tests/self_parsing/test_drift.py. The `project_level` section remains a
# supported feature for other repositories.
```

  3. In `tolerances.lock`, delete the `project_level:` section (two lines).
  4. In `test_drift.py`:
     - delete `test_py_quotient_drift_observed_not_enforced`;
     - in `test_plane_quotients_observed_not_enforced`, replace `binding = scorer.load_project_level()[0]` with a binding the test owns:

```python
    binding = DomainConfig(
        name="plane", fqn_prefix=QUOTIENT_PREFIX, expected_pattern="", profile="python",
        drift_tolerance=1.0, enforce=False,
    )  # fmt: skip
```

     - import `DomainConfig` from wherever `DriftScorer.load_project_level` gets it (`git grep -n "class DomainConfig" src`), and `QUOTIENT_PREFIX` from `cgis.query.drift.quotient`;
     - read `DomainConfig`'s required fields first and pass exactly those. If `profile` is not a field, drop it.
  5. Update the `test_drift.py` module docstring if it mentions the project-level binding.

- [ ] **Step 4: Run the tests.**
  - Run: `uv run pytest tests/unit/test_patterns_yaml.py tests/self_parsing/test_drift.py tests/unit/test_drift.py tests/unit/test_drift_service.py -q -s -k "plane or project_level or quotient or tolerance"`
  - Expected:
    - all pass;
    - the plane test still prints its two `[plane observe-only]` lines with the same numbers as before (build 0.238, read 0.676 at step 0; whatever main prints now). Compare against `git stash`-free main, run from `/home/zaebee/projects/ownima/codegraph-brain`.

- [ ] **Step 5: `cgis drift` on this repository.**
  - Run: `uv run cgis drift --db <fresh self ingest under the scratchpad> --profile python`
  - Expected: a report with no `Quotient k=1 [cgis-project]` line, and no traceback. Delete the database afterwards.

- [ ] **Step 6: Gate and commit.**
  - Message: `chore(ontology): retire cgis's project_level quotient binding (#146)`.
  - Body: the gate it is replaced by, and that the feature stays for other repositories.

---

### Task 2: One drift domain per pack, and `core`

**Files:**
- Modify:
  - `docs/ontology/patterns.yaml` (the `extraction` entry → `languages.python`, `languages.typescript`; new `core` entry)
  - `docs/ontology/tolerances.lock`
  - `tests/self_parsing/test_drift.py` (`_PLANES`)
- Test: `tests/unit/test_patterns_yaml.py` (append)

**Interfaces:**
- Consumes: `cgis.languages.MANIFESTS`, each with `.name`.

- [ ] **Step 1: Write the failing test.** Append to `tests/unit/test_patterns_yaml.py`:

```python
from cgis.languages import MANIFESTS

#: One bar for every language pack (spec: Drift gates, Domains) — set by the
#: loosest pack at step 6 (ruling G2); a new pack meets it or the suite says so.
_PACK_PATTERN = "layered_dag"


def test_every_pack_has_a_domain_at_the_shared_tolerance() -> None:
    domains = {d["name"]: d for d in _load()["project_domains"]}
    packs = {m.name: domains.get(f"languages.{m.name}") for m in MANIFESTS}
    missing = sorted(name for name, domain in packs.items() if domain is None)
    assert not missing, f"packs without a drift domain in patterns.yaml: {missing}"
    tolerances = {name: domain["drift_tolerance"] for name, domain in packs.items()}
    assert len(set(tolerances.values())) == 1, f"pack tolerances must be shared: {tolerances}"
    for name, domain in packs.items():
        assert domain["fqn_prefix"] == f"cgis.languages.{name}"
        assert domain["expected_pattern"] == _PACK_PATTERN
        assert domain["profile"] == "python"


def test_core_has_a_domain() -> None:
    domains = {d["name"]: d for d in _load()["project_domains"]}
    assert domains["core"]["fqn_prefix"] == "cgis.core"
```

  Move the import to the import block.

- [ ] **Step 2: Run them and watch them fail.**
  - Run: `uv run pytest tests/unit/test_patterns_yaml.py -q -k "every_pack or core_has"`
  - Expected: FAIL, with `packs without a drift domain ...: ['python', 'typescript']` and `KeyError: 'core'`.

- [ ] **Step 3: Replace `extraction`.**
  1. In `patterns.yaml`, replace the whole `extraction` entry, comments included, with the entries below. Keep the history in one line: `# Was the single "extraction" domain over cgis.extractors / cgis.languages (#116, #183, steps 1–5).`

```yaml
  # Language packs (spec: Drift gates, Domains) — one domain per pack, one shared
  # bar (ruling G2), so a new pack is measured against it from its first commit;
  # tests/unit/test_patterns_yaml.py fails for a pack without one.
  # Measured at step 6 (main ca32430): python layered_dag 0.393 (best fit funnel
  # 0.328), typescript layered_dag 0.184. Shared tolerance = loosest + headroom.
  - name: "languages.python"
    fqn_prefix: "cgis.languages.python"
    expected_pattern: layered_dag
    profile: python
    drift_tolerance: 0.42
    hygiene_baseline:
      unresolved_ratio: 0.37 # measured 0.356 at step 6 (the old extraction baseline)

  - name: "languages.typescript"
    fqn_prefix: "cgis.languages.typescript"
    expected_pattern: layered_dag
    profile: python
    drift_tolerance: 0.42
    hygiene_baseline:
      unresolved_ratio: 0.31 # measured 0.295 at step 6

  # The pack contract and the model (spec: Core). layered_dag, not the spec's
  # pure_utility — measured 0.215 vs 0.554 at step 6: the contract is layered
  # (extractor/index/resolution/project -> models) since steps 1-4 (ruling G1).
  - name: "core"
    fqn_prefix: "cgis.core"
    expected_pattern: layered_dag
    profile: python
    drift_tolerance: 0.25
    hygiene_baseline:
      unresolved_ratio: 0.21 # measured 0.191 at step 6
```

     If the user chose an alternative to G1 or G2 at plan review, use those values instead and ledger them.
  2. In `tolerances.lock`, replace `extraction: 0.40` with `languages.python: 0.42`, `languages.typescript: 0.42` and `core: 0.25`, in `project_domains` order.
  3. In `test_drift.py`, set `_PLANES["build"]` to `("pipeline", "languages.python", "languages.typescript", "resolution", "storage")`.

- [ ] **Step 4: Run the tests.**
  - Run: `uv run pytest tests/unit/test_patterns_yaml.py tests/self_parsing/test_drift.py -q -s`
  - Expected:
    - all pass;
    - `test_py_self_drift_within_tolerance` passes with the new domains, and their measured drift matches the table above within ±0.01, because no code changed;
    - the plane report prints.
  - If the quotient breaks on the dotted names (`a domain matched nothing`, or the plane node count is off), apply G4's fallback names `languages-python` / `languages-typescript` in the yaml, the lock, `_PLANES` and the test's `f"languages.{m.name}"`, and ledger it.

- [ ] **Step 5: Mutation-check the pack test.**
  - Delete the `languages.typescript` entry from the yaml for one run: the test must fail naming `typescript`.
  - Set its tolerance to 0.43 for another run: the test must fail on "shared".
  - Restore the file and confirm with `git diff --stat`.

- [ ] **Step 6: Gate and commit.**
  - Message: `chore(ontology): one drift domain per language pack, and core`.
  - Body: the measurements table and rulings G1 and G2 (or the user's alternative). Both are "set for a new domain with the measurement", which the ratchet allows.

---

### Task 3: Docs, review, PR, close #146

- [ ] **Step 1: Spec note.** In "Drift gates", add `> **Implementation note (step 6):** …` covering:
  - `project_level` retired for this repository (the feature stays);
  - the plane report owns its binding;
  - the per-pack domains with the measured table and G2's shared 0.42;
  - `core` on `layered_dag` and why (G1).

  Then run `git diff --stat main -- src`. Expected: empty.

- [ ] **Step 2: Gate and commit.**
  - Message: `docs: step 6 implementation note`.

- [ ] **Step 3: Independent review.** Dispatch a reviewer with execution tools. Give it:
  - the branch diff, this plan, the spec and the ledger;
  - the Review Focus;
  - the disk warning.

  Ask it to re-measure the three domains on main and the branch, and to mutation-check both new tests. Fix Critical and Important findings test-first.

- [ ] **Step 4: PR.**
  - Title: `chore(ontology): step 6 — gates`.
  - Body:
    - what was retired and what replaced it;
    - the table;
    - G1–G4;
    - "closes #146" wording **only in prose**. Close #146 by hand after merge, with a comment summarising steps 0–6. Step 7, the Go pilot, does not block it.
  - Check the Gemini and Guardian comments and Sonar, and reply in every thread.
- [ ] **Step 5: After merge, close #146** with a comment:
  - the enforced gate (forbidden edges, all pack-related allowlists empty);
  - the plane quotients observe-only;
  - the per-pack domains;
  - links #596–#602 and the follow-ups #603 and #604.

<!-- START_CGIS_RELATED -->
**Related:** [language-packs-step1](2026-10-14-language-packs-step1.md) · [language-packs-step5](2026-10-14-language-packs-step5.md) · [language-packs-step4](2026-10-14-language-packs-step4.md) · [language-packs-step3](2026-10-14-language-packs-step3.md) · [language-packs](../specs/2026-10-14-language-packs-design.md)

**Code:** [`src/cgis/core/`](../../src/cgis/core/) · [`src/cgis/languages/`](../../src/cgis/languages/) · [`src/cgis/query/`](../../src/cgis/query/) · [`tests/self_parsing/test_drift.py`](../../tests/self_parsing/test_drift.py) · [`tests/fixtures/graph_identity/snapshot.json`](../../tests/fixtures/graph_identity/snapshot.json)

<!-- END_CGIS_RELATED -->
