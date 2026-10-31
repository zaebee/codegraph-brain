# Language Packs — Step 5: Build-Plane Passes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the post-write passes into a `cgis/pipeline/` package so the resolver no longer touches storage, with no stored graph changing.

**Architecture:** `src/cgis/pipeline.py` becomes the package `src/cgis/pipeline/` and keeps its contents in `__init__.py`, so the module FQN `cgis.pipeline` and all 32 `from cgis.pipeline import IngestionPipeline` imports stay as they are. There is no shim: the module simply becomes a package. `src/cgis/resolver/uplift.py` moves to `src/cgis/pipeline/uplift.py`. It is the only resolver module that reads or writes the store, so the `resolver-storage` allowlist (8 edges today) becomes empty. `refresh_dispatch` already lives in the pipeline. Its pure half, `dispatch_edges`, takes methods and an inheritance tree with no store, so it stays in the resolver.

**Tech Stack:** Python 3.12, tree-sitter, SQLite, pytest, ruff, mypy strict, interrogate.

**Spec:** `docs/specs/2026-10-14-language-packs-design.md` (rev 3): "Architecture → Build plane" and Migration row 5. Outline: `docs/plans/2026-10-14-language-packs.md`, "Step 5". Steps 1–4: #597, #599, #600 and #601, plus the spec's implementation notes.

## Global Constraints

- **Full gate before every commit:** `make format && make lint && make type-check && make pytest && make doc-coverage` passes. Chain the commands with `&&`; never use `;`, a bare newline after a heredoc, or a pipe on the gating command.
- **Typing and quality floors:** mypy strict on `src/`; docstring coverage ≥ 90%; Sonar cognitive complexity ≤ 15 per function.
- **Behaviour unchanged:** `tests/fixtures/graph_identity/snapshot.json` is not modified, and a main-vs-branch `graph_identity diff` reads `identical` for cgis `src` @715cd9a, owner-api @8de2245 and `ui/`.
- **Spec acceptance:** the `resolver-storage` allowlist is empty, and the `resolution` domain is re-measured.
- **Drift:**
  - `pipeline` (orchestrator) stands at drift 0.072 against a lock of 0.25, and unresolved 0.402 against 0.43. It gains the uplift module.
  - `resolution` stands at 0.180 / 0.440 against locks of 0.40 / 0.45. It loses the uplift module.
  - Measure both after the move. If either exceeds its bounds, **stop and report**. Never raise a lock.
  - Remember step 4: one stray import moved a domain's drift by 0.2. If a bound breaks, look for an accidental intra-domain edge before anything else.
- **Disk:** the root disk is ~98% full. Keep scratch under the scratchpad (`/tmp`) and delete databases after each measurement.
- **Test-count floor:** raise `.github/test-count-baseline` (currently 3095) if the suite passes 3245.
- **Attribution:** commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; the PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- **Workspace:** worktree `/home/zaebee/projects/ownima/cgb-step5`, branch `feat/language-packs-step5`.

## Rulings (where this plan departs from the spec's or outline's text)

- **P1 — `pipeline.py` becomes `pipeline/__init__.py`, not `pipeline/ingest.py` plus a re-export.**
  - The orchestrator stays at `cgis.pipeline.IngestionPipeline`, so no importer changes and no compatibility re-export is needed (the step-1 rule was "no shims").
  - The package gains the uplift module beside it.
  - *Cost if wrong:* one more move if `__init__` later grows past one responsibility.
- **P2 — `refresh_dispatch` stays where it is.** The spec says it "moves here from `resolver/`", but it is already `cgis.pipeline.refresh_dispatch`. What stays in `resolver/symbols.py` is the pure `dispatch_edges(methods, inheritance)`. The engine calls it on what it extracted, and it never touches a store.
- **P3 — the language residue in the resolver's text is out of scope.** Ruling C4 of step 4 kept generic `resolve_layout` and the JS-builtins root in the generic classify tail; this step leaves them, along with `_is_dunder` and the language-named project-state keys threaded through `IndexBuilder.build`. Removing them changes behaviour, either for a source no pack reads or for the dunder filter of DISPATCHES. That is not what migration row 5 is for. The PR lists them, and the recommendation is a follow-up issue.

## Review Focus

- **`cgis ingest` with `--domains`.** Semantic uplift still runs after resolution, with the same domain tags and `ontology_class`. The existing `test_pipeline.py::...domains...` tests and `test_uplift.py` cover this, and the identity corpus records `domains` per node.
- **An incremental run.** `refresh_dispatch` and uplift run in the same order as before, after `_persist_incremental` and `record`. This is pinned by Task 1, `test_post_write_passes_run_in_order`.
- **Imports of the old module path.** None remain: Task 1 greps for them, and the test suite imports the new path.
- **The forbidden-edge gate.** `resolver-storage` is empty, and nothing in `cgis.resolver` imports `cgis.storage`, even under `TYPE_CHECKING`. The gate counts `IMPORTS` edges whatever their `type_only` flag says.
- **Self-graph FQNs.** `pipeline` keeps its id (`pipeline/__init__.py` strips `/__init__`); `resolver.uplift.*` becomes `pipeline.uplift.*` in god-object, calibration and receiver fixtures, and Task 1 greps for leftovers.

---

### Task 1: The pipeline package; uplift moves into it

**Files:**
- Move: `src/cgis/pipeline.py` → `src/cgis/pipeline/__init__.py`
- Move: `src/cgis/resolver/uplift.py` → `src/cgis/pipeline/uplift.py`
- Modify:
  - `src/cgis/pipeline/__init__.py:22` (the import)
  - `tests/unit/test_uplift.py:16`
  - `tests/self_parsing/forbidden_edges_allowlist.json` (`resolver-storage` → `{}`)
  - any self-graph test naming `resolver.uplift`
  - the docstring in `src/cgis/query/context/audit.py:17`, only if it names the module path
- Test: `tests/unit/test_pipeline.py` (append)

**Interfaces:**
- Produces: `cgis.pipeline.uplift.SemanticUpliftEngine` (unchanged API). `cgis.pipeline` keeps `IngestionPipeline`, `refresh_dispatch` and everything else it had.

- [ ] **Step 1: Write the failing gate expectation and the order test.**
  1. In `tests/self_parsing/forbidden_edges_allowlist.json`, set `"resolver-storage": {}`. Spec acceptance is the test.
  2. Append to `tests/unit/test_pipeline.py`:

```python
def test_post_write_passes_run_in_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dispatch is refreshed over the stored graph, then uplift runs — after the write (#545)."""
    (tmp_path / "a.py").write_text("class A:\n    def f(self):\n        pass\n", encoding="utf-8")
    order: list[str] = []
    real_refresh = pipeline_module.refresh_dispatch
    monkeypatch.setattr(
        pipeline_module, "refresh_dispatch", lambda store: (order.append("dispatch"), real_refresh(store))
    )
    monkeypatch.setattr(
        pipeline_module.SemanticUpliftEngine, "execute_uplift", lambda self: order.append("uplift")
    )
    with SQLiteStore(str(tmp_path / "g.db")) as store:
        IngestionPipeline({".py": PythonExtractor()}).run(str(tmp_path), store=store)
    assert order == ["dispatch", "uplift"]
```

     Add `import cgis.pipeline as pipeline_module` (and `pytest`, `SQLiteStore` and `PythonExtractor` if they are missing) to the file's imports.

- [ ] **Step 2: Run them.**
  - Run: `uv run pytest tests/self_parsing/test_architecture.py -q -k allowlist && uv run pytest tests/unit/test_pipeline.py -q -k post_write`
  - Expected:
    - the allowlist test FAILS, printing `"resolver-storage": {"cgis.resolver.uplift -> cgis.storage.sqlite_store": 8}`;
    - the order test PASSES. It is a guard for the move, mutation-checked in Step 5.

- [ ] **Step 3: Move with git, then fix the imports.**

```bash
cd /home/zaebee/projects/ownima/cgb-step5 && mkdir -p src/cgis/pipeline && git mv src/cgis/pipeline.py src/cgis/pipeline/__init__.py && git mv src/cgis/resolver/uplift.py src/cgis/pipeline/uplift.py && sed -i 's/^from cgis\.resolver\.uplift import SemanticUpliftEngine$/from cgis.pipeline.uplift import SemanticUpliftEngine/' src/cgis/pipeline/__init__.py && sed -i 's/^from cgis\.resolver\.uplift import/from cgis.pipeline.uplift import/' tests/unit/test_uplift.py && git grep -n "resolver\.uplift\|resolver/uplift" -- src tests scripts docs/ontology
```

  - Expected: the final grep lists only prose (docstrings and comments). Re-point those that describe where the code lives now; leave historical mentions alone.
  - `pipeline/__init__.py` importing its own submodule `cgis.pipeline.uplift` is fine. Check with `uv run python -c "import cgis.pipeline, cgis.pipeline.uplift"`.
  - If the self-graph tests name `resolver.uplift.*` FQNs (`git grep -n "resolver.uplift" -- tests/self_parsing`), re-point them to `pipeline.uplift.*`.

- [ ] **Step 4: Run the tests.**
  - Run: `uv run pytest tests/self_parsing tests/unit/test_pipeline.py tests/unit/test_uplift.py tests/unit/test_graph_identity.py -q`
  - Expected: all pass, including the allowlist with `resolver-storage` empty.

- [ ] **Step 5: Mutation-check the order test.**
  1. Swap the two lines in `IngestionPipeline.run` (`refresh_dispatch(store)` and the `SemanticUpliftEngine(...)` call) for one run.
  2. `test_post_write_passes_run_in_order` must fail.
  3. Restore the file and confirm `git diff --stat` shows only the intended files.

- [ ] **Step 6: Measure the two domains.**

```bash
cd /home/zaebee/projects/ownima/cgb-step5 && S=/tmp/claude-1001/-home-zaebee-projects-ownima-codegraph-brain/97c475b5-6bc7-4231-a643-ff81f3f651ec/scratchpad/step5 && mkdir -p $S && uv run cgis ingest src --output $S/self.db > /dev/null 2>&1 && uv run cgis drift --db $S/self.db --profile python --format json > $S/drift.json 2>/dev/null
python3 -c "import json; d=json.load(open('$S/drift.json')); rs=d if isinstance(d,list) else d.get('reports') or d.get('domains'); [print(r['domain'], round(r['drift_score'],4), round(r['actual']['unresolved_ratio'],4), r['actual']['node_count'], r['status']) for r in rs if r['domain'] in ('pipeline','resolution')]"
/bin/rm -f $S/self.db
```

  - Expected:
    - `pipeline`: drift ≤ 0.25 and unresolved ≤ 0.43;
    - `resolution`: drift ≤ 0.40 and unresolved ≤ 0.45.
  - If any value is over its bound, stop. First list the intra-domain import edges on both sides:

```
sqlite3 <db> "select source, target from edges where type='IMPORTS' and source like 'cgis.pipeline%' and target like 'cgis.pipeline%'"
```

    A cross-import triangle was the cause in step 4. Then report.

- [ ] **Step 7: Inventory.**
  - Regenerate `tests/unit/review_path_inventory.txt` as in step 2's plan (Task 2, Step 3).
  - Expected: `gone` holds at most `src/cgis/resolver/uplift.py` or `src/cgis/pipeline.py` if they were in the closure, and `new` holds their new paths. Anything else gone means the review walk lost a module: stop.

- [ ] **Step 8: Gate and commit.**
  - Message: `refactor(pipeline): pipeline.py becomes a package; semantic uplift moves into it`.
  - The body records:
    - the measured drift for both domains;
    - that `resolver-storage` is now empty;
    - ruling P1 (no importer changes).

---

### Task 2: Documentation

**Files:**
- `CLAUDE.md` (Architecture block and the pipeline line)
- `docs/specs/2026-10-14-language-packs-design.md` (implementation note under "Build plane")

- [ ] **Step 1: CLAUDE.md.**
  - The Architecture block reads `IngestionPipeline (pipeline.py)`. Change it to `IngestionPipeline (pipeline/__init__.py)`.
  - Add a line under the store: `├── post-write passes (pipeline/)   ← refresh_dispatch, SemanticUpliftEngine (pipeline/uplift.py)`. Match the block's existing indentation and arrow style.
  - Correct any other line that names `resolver/uplift.py`.

- [ ] **Step 2: Spec note.** Under "Build plane", add `> **Implementation note (step 5):** …`. It covers:
  - P1: the package keeps the orchestrator in `__init__.py`, so imports are unchanged;
  - P2: `refresh_dispatch` was already in the pipeline, and `dispatch_edges` stays pure in the resolver;
  - the empty `resolver-storage` allowlist;
  - the measured drift;
  - P3: the language residue left in the resolver's text, listed as follow-up work for a measured change.

- [ ] **Step 3: Gate and commit.**
  - Message: `docs: step 5 implementation notes`.

---

### Task 3: Identity report, review, PR

- [ ] **Step 1: Identity.**
  - Run the same commands as step 4's plan, Task 8 Step 1, using `step5` paths and `BR=/home/zaebee/projects/ownima/cgb-step5`.
  - Expected: `identical` three times.
  - Delete the databases.
- [ ] **Step 2: Independent review.**
  - Dispatch a reviewer with execution tools on the most capable model.
  - Give it: the branch diff, this plan, the spec, the ledger, the Review Focus and the disk warning.
  - Ask it to confirm, with a `--domains` ingest on old and new, that uplift output (`domains`, `ontology_class`, domain-dependency edges) is identical.
  - Fix Critical/Important findings test-first.
- [ ] **Step 3: PR.**
  - Push, then open `refactor(pipeline): step 5 — build-plane passes`.
  - The body carries:
    - the module map;
    - rulings P1–P3;
    - the identity results;
    - the drift numbers;
    - the emptied `resolver-storage` allowlist;
    - the residue list with the follow-up recommendation.
  - Check the Gemini and Guardian comments and Sonar, and reply in every thread.

<!-- START_CGIS_RELATED -->
**Related:** [language-packs-step6](2026-10-14-language-packs-step6.md) · [language-packs-step4](2026-10-14-language-packs-step4.md) · [language-packs-step3](2026-10-14-language-packs-step3.md) · [language-packs-step7](2026-10-14-language-packs-step7.md) · [language-packs](2026-10-14-language-packs.md)

**Code:** [`src/cgis/pipeline/`](../../src/cgis/pipeline/) · [`src/cgis/query/`](../../src/cgis/query/) · [`src/cgis/resolver/`](../../src/cgis/resolver/) · [`src/cgis/storage/`](../../src/cgis/storage/) · [`tests/fixtures/graph_identity/snapshot.json`](../../tests/fixtures/graph_identity/snapshot.json)

<!-- END_CGIS_RELATED -->
