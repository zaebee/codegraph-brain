# Guardian Precision Baseline + Finder Recordings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pin Guardian's current false-positive rate as a benchmark number, and make every production review re-scorable offline by persisting what the finder said.

**Architecture:** The recording model moves out of `bench.py` into its own module so production code does not depend on the benchmark. `run_guardian` gains an optional `record_finder` path and writes `routed.result` plus the diff — safe post-skeptic because `load_finder_recording` already strips verdicts on read. The workflow passes the flag unconditionally and uploads the result beside the metrics artifact. Finally a `pr-278.yaml` ground-truth entry with no findings turns "10 false positives" into a number the bench reports.

**Tech Stack:** Python 3.12+, Pydantic frozen models, pytest (`asyncio_mode = auto` — async tests need no decorator), GitHub Actions.

**Spec:** `docs/specs/2026-07-30-guardian-precision-bench-design.md`
**Issue:** #279

## Global Constraints

- **MyPy strict** (`make type-check` runs `mypy src`). Full annotations including return types.
- **Ruff** full rule set, line length **100**, double quotes. `SLF001` is on and tests have no per-file ignore — use inline `# noqa: SLF001  # white-box: <reason>`, matching `test_guardian_runner.py:177`.
- **Docstring coverage ≥ 90%** (`uv run interrogate src`; `scripts/` and `tests/` are excluded).
- **No review-behaviour change.** Findings, prompts, context assembly and rendering stay identical. This adds persistence only.
- **Do not fix precision here.** No prompt or skeptic edits. The baseline exists so a later fix can be measured.
- **Do not touch `chunked.py`.** Recording happens in `run_guardian`, above the routing decision, so both paths are covered without editing either. Chunk routing is #277.
- **Verify against the CI environment, not the local one.** CI runs `uv sync --group dev` — no guardian group. `uv run mypy src` must pass there. (This is the exact trap that turned #275's CI red.)
- **Full verification before every commit:** `make format && make lint && make type-check && make pytest && make doc-coverage`.
- Branch `fix/279-precision-bench`, worktree `.claude/worktrees/guardian-precision`.

## File Structure

| File | Responsibility |
|------|----------------|
| `src/cgis/guardian/recording.py` (create) | `FinderRecording`, `save_finder_recording`, `load_finder_recording`, `_validated_recording_path` |
| `src/cgis/guardian/bench.py` (modify) | Drops those definitions; re-exports them so existing importers keep working |
| `src/cgis/guardian/runner.py` (modify) | `run_guardian(record_finder=...)` writes the recording |
| `scripts/guardian_review.py` (modify) | `--record-finder PATH` |
| `.github/workflows/guardian.yml` (modify) | Pass the flag; upload `guardian-finder-<pr>` |
| `tests/unit/test_guardian_recording.py` (create) | The moved module's own tests |
| `tests/unit/test_guardian_runner.py` (modify) | Recording written / not written / refuted survives |
| `benchmarks/guardian/pr-278.yaml` (create) | The precision baseline entry |
| `benchmarks/guardian/CURATION.md` (modify) | The fourth curation rule + the PR 278 section |

Task 1 is a pure move (no behaviour). Task 2 adds the write path. Task 3 wires CI. Task 4 is data plus the offline proof that the whole thing scores.

---

### Task 1: Extract the recording module

**Files:**
- Create: `src/cgis/guardian/recording.py`
- Modify: `src/cgis/guardian/bench.py`
- Test: `tests/unit/test_guardian_recording.py` (create)

**Interfaces:**
- Produces (all moved verbatim from `bench.py`, signatures unchanged):
  - `FinderRecording` — frozen model, fields `result: ReviewResult`, `diff: str`
  - `save_finder_recording(path: Path, result: ReviewResult, diff: str) -> None`
  - `load_finder_recording(path: Path) -> FinderRecording`
  - `_validated_recording_path(path: Path, *, must_exist: bool) -> Path`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_guardian_recording.py`:

```python
"""Unit tests for the finder-recording module extracted from bench (#279)."""

from pathlib import Path

import pytest

from cgis.guardian.findings import Finding, ReviewResult
from cgis.guardian.recording import (
    FinderRecording,
    load_finder_recording,
    save_finder_recording,
)

_FINDING = Finding(
    file="a.py",
    line=1,
    severity="major",
    category="logic",
    title="t",
    evidence="e",
    problem="p",
    fix="f",
    confidence=90,
)


def test_recording_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "rec.json"
    result = ReviewResult(findings=[_FINDING], summary="s")

    save_finder_recording(path, result, "the-diff")
    loaded = load_finder_recording(path)

    assert loaded.diff == "the-diff"
    assert [f.title for f in loaded.result.findings] == ["t"]


def test_load_strips_skeptic_verdicts(tmp_path: Path) -> None:
    """Every replay must start from unjudged findings, or arms are not comparable."""
    path = tmp_path / "rec.json"
    judged = _FINDING.model_copy(update={"verdict": "refuted", "impact_score": 0})
    save_finder_recording(path, ReviewResult(findings=[judged], summary="s"), "d")

    loaded = load_finder_recording(path)

    assert loaded.result.findings[0].verdict is None
    assert loaded.result.findings[0].impact_score is None
    assert loaded.result.skeptic_status == "off"


def test_a_non_json_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be a .json file"):
        save_finder_recording(tmp_path / "rec.txt", ReviewResult(findings=[], summary=""), "d")


def test_loading_a_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a file"):
        load_finder_recording(tmp_path / "nope.json")


def test_bench_still_re_exports_the_moved_names() -> None:
    """bench.py is the historical import site; existing callers must keep working."""
    from cgis.guardian import bench

    assert bench.FinderRecording is FinderRecording
    assert bench.save_finder_recording is save_finder_recording
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_guardian_recording.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cgis.guardian.recording'`

- [ ] **Step 3: Create the module**

Create `src/cgis/guardian/recording.py` and move the four definitions from
`bench.py` **verbatim** — same bodies, same docstrings. Only the module docstring
and imports are new:

```python
"""Persisted finder passes: the recording format shared by bench and production.

Extracted from bench.py (#279) once run_guardian became a second consumer —
production code must not import the benchmark module. Same reason diff_index.py
was split out of chunker.py when the skeptic pass needed the per-file split.
"""

from pathlib import Path

from pydantic import BaseModel

from cgis.guardian.findings import ReviewResult
```

Then cut `FinderRecording`, `_validated_recording_path`, `save_finder_recording`
and `load_finder_recording` out of `bench.py` and paste them here unchanged.

Extend `save_finder_recording`'s docstring with the fidelity note the spec calls
for:

```python
def save_finder_recording(path: Path, result: ReviewResult, diff: str) -> None:
    """Write a finder pass to disk for later replay.

    A production recording is taken AFTER the skeptic ran, which is safe because
    load_finder_recording strips verdicts on read. One caveat travels with it:
    apply_judgements rewrites confidence to round(confidence * 0.9) for
    'uncertain' verdicts and the load path does not restore it, so a recorded
    confidence on that subset is not the finder's own number (#279).
    """
```

- [ ] **Step 4: Re-export from `bench.py`**

In `src/cgis/guardian/bench.py`, replace the removed block with re-exports,
using the redundant-alias form the package already uses for this
(`chunker.py` does exactly this for `split_diff_by_file`):

```python
# Re-exported: the recording format moved to its own module when production
# became a second consumer (#279); bench remains the historical import site.
from cgis.guardian.recording import (  # noqa: PLC0414
    FinderRecording as FinderRecording,
    load_finder_recording as load_finder_recording,
    save_finder_recording as save_finder_recording,
)
```

Leave `scripts/guardian_bench.py` importing from `cgis.guardian.bench` — the
re-export keeps it working, and this task changes no behaviour.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_recording.py tests/unit/test_guardian_bench.py -v`
Expected: PASS — the new file plus every pre-existing bench test, untouched.

- [ ] **Step 6: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/guardian/recording.py src/cgis/guardian/bench.py tests/unit/test_guardian_recording.py
git commit -m "refactor(guardian): extract the recording format from bench (#279)"
```

---

### Task 2: Record the finder pass from `run_guardian`

**Files:**
- Modify: `src/cgis/guardian/runner.py`
- Modify: `scripts/guardian_review.py`
- Test: `tests/unit/test_guardian_runner.py` (append)

**Interfaces:**
- Consumes: `save_finder_recording` from Task 1
- Produces: `run_guardian(..., record_finder: Path | None = None)` — writes the recording when set; `scripts/guardian_review.py --record-finder PATH`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_guardian_runner.py`. It already imports `Path`,
`StubProvider`, `FINDING_JSON`, `ContextCollector`, `run_guardian` and `patch`:

```python
async def test_run_guardian_records_the_finder_pass(tmp_path: Path) -> None:
    """The artifact that makes a review re-scorable offline (#279)."""
    recording_path = tmp_path / "finder.json"
    provider = StubProvider([FINDING_JSON])
    collector = ContextCollector(project_root=tmp_path)
    with (
        patch.object(collector, "collect_all", return_value={"diff": "d"}),
        patch.object(collector, "get_git_diff", return_value="the-diff"),
    ):
        await run_guardian(
            provider=provider,
            model="m",
            collector=collector,
            pr=1,
            metrics_path=tmp_path / "m.jsonl",
            record_finder=recording_path,
        )

    loaded = load_finder_recording(recording_path)
    assert loaded.diff == "the-diff"
    assert [f.file for f in loaded.result.findings] == ["a.py"]


async def test_run_guardian_records_nothing_without_the_flag(tmp_path: Path) -> None:
    provider = StubProvider([FINDING_JSON])
    collector = ContextCollector(project_root=tmp_path)
    with (
        patch.object(collector, "collect_all", return_value={"diff": "d"}),
        patch.object(collector, "get_git_diff", return_value="the-diff"),
    ):
        await run_guardian(
            provider=provider,
            model="m",
            collector=collector,
            pr=1,
            metrics_path=tmp_path / "m.jsonl",
        )

    assert list(tmp_path.glob("*.json")) == []


async def test_refuted_findings_survive_into_the_recording(tmp_path: Path) -> None:
    """The regression test for the whole 'post-skeptic recording is safe' argument.

    apply_judgements annotates rather than drops (tests/unit/test_guardian_skeptic.py
    guards that directly); this asserts the end-to-end consequence — a refuted
    finding still reaches the file, and loads back unjudged.
    """
    recording_path = tmp_path / "finder.json"
    provider = StubProvider([FINDING_JSON])
    skeptic = StubProvider(['{"verdict": "refuted", "impact_score": 0, "rationale": "b"}'])
    collector = ContextCollector(project_root=tmp_path)
    with (
        patch.object(collector, "collect_all", return_value={"diff": "d"}),
        patch.object(collector, "get_git_diff", return_value="the-diff"),
    ):
        await run_guardian(
            provider=provider,
            model="m",
            collector=collector,
            pr=1,
            metrics_path=tmp_path / "m.jsonl",
            skeptic=(skeptic, "stub-skeptic"),
            record_finder=recording_path,
        )

    loaded = load_finder_recording(recording_path)
    assert len(loaded.result.findings) == 1
    assert loaded.result.findings[0].verdict is None
```

Add `from cgis.guardian.recording import load_finder_recording` to the test
module's imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_runner.py -v -k record`
Expected: FAIL — `TypeError: run_guardian() got an unexpected keyword argument 'record_finder'`

- [ ] **Step 3: Implement in `runner.py`**

Add the import:

```python
from cgis.guardian.recording import save_finder_recording
```

Add the parameter to `run_guardian`, after `threshold`:

```python
    threshold: int = 0,
    record_finder: Path | None = None,
) -> tuple[str, bool]:
```

Then, immediately after `duration_s` is computed and `result = routed.result` is
taken, write the recording:

```python
    result = routed.result
    if record_finder is not None:
        # Recorded AFTER the skeptic on purpose: load_finder_recording strips
        # verdicts on read, and refuted findings stay in result.findings — so a
        # replay still starts clean, with no second API call to pay for (#279).
        save_finder_recording(record_finder, result, collector.get_git_diff())
        log.info("Finder pass recorded.", path=str(record_finder))
```

Document the parameter in the existing docstring by appending one line:

```
    ``record_finder`` writes the finder pass (findings + diff) to that path so
    the review can be re-scored offline against a benchmark entry (#279).
```

- [ ] **Step 4: Implement the CLI flag in `scripts/guardian_review.py`**

Add the argument next to the existing ones:

```python
    parser.add_argument(
        "--record-finder",
        type=Path,
        default=None,
        help="Write the finder pass (findings + diff) to this .json for offline re-scoring.",
    )
```

and pass it through:

```python
        threshold=impact_threshold(os.environ),
        record_finder=args.record_finder,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_runner.py -v`
Expected: PASS — the three new tests plus every pre-existing runner test.

- [ ] **Step 6: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/guardian/runner.py scripts/guardian_review.py tests/unit/test_guardian_runner.py
git commit -m "feat(guardian): record the finder pass for offline re-scoring (#279)"
```

---

### Task 3: Upload the recording from CI

**Files:**
- Modify: `.github/workflows/guardian.yml`

**Interfaces:**
- Consumes: `--record-finder` from Task 2

- [ ] **Step 1: Pass the flag**

In the `Run Guardian Review` step, add the flag to the existing invocation:

```yaml
          uv run python scripts/guardian_review.py \
            --output guardian_report.md \
            --db graph.db \
            --pr "$PR_NUMBER" \
            --metrics guardian_metrics.jsonl \
            --record-finder guardian_finder.json \
            --inline \
            --base-branch "$BASE_BRANCH"
```

- [ ] **Step 2: Upload the artifact**

Add a step immediately after `Upload metrics artifact`, mirroring it exactly —
same action pin, same retention, and deliberately **no** `if:` condition, so a
recording exists for exactly the runs that completed, matching the metrics
artifact's semantics:

```yaml
      - name: Upload finder recording artifact
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02  # v4
        with:
          name: guardian-finder-${{ env.PR_NUMBER }}
          path: guardian_finder.json
          retention-days: 7
```

- [ ] **Step 3: Validate the workflow file parses**

Run:
```bash
uv run python -c "
import yaml, pathlib
wf = yaml.safe_load(pathlib.Path('.github/workflows/guardian.yml').read_text())
steps = wf['jobs']['review']['steps']
names = [s.get('name') for s in steps]
assert 'Upload finder recording artifact' in names, names
run = next(s['run'] for s in steps if s.get('name') == 'Run Guardian Review')
assert '--record-finder guardian_finder.json' in run
print('workflow ok:', len(steps), 'steps')
"
```
Expected: `workflow ok: <n> steps`

- [ ] **Step 4: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add .github/workflows/guardian.yml
git commit -m "ci(guardian): upload the finder recording artifact (#279)"
```

---

### Task 4: The precision baseline entry

**Files:**
- Create: `benchmarks/guardian/pr-278.yaml`
- Modify: `benchmarks/guardian/CURATION.md`

**Interfaces:**
- Consumes: `load_ground_truth`, `match_findings`, `score` from `cgis.guardian.bench`; `FinderRecording` from Task 1

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_guardian_bench.py`:

```python
def test_pr_278_entry_scores_every_prediction_as_noise() -> None:
    """The precision baseline: an entry with no ground truth measures noise only (#279)."""
    truth = load_ground_truth(Path("benchmarks/guardian/pr-278.yaml"))
    predictions = [
        _pred("src/cgis/guardian/providers/base.py", 102),
        _pred("src/cgis/guardian/providers/mistral.py", 26),
    ]

    result = score(match_findings(predictions, truth), truth)

    assert result.precision == 0.0
    assert result.noise == 2
    assert result.recall == 1.0  # vacuous: no ground truth to miss


def test_pr_278_entry_files_nothing_as_ambiguous() -> None:
    """Ambiguous exempts per FILE, so one entry would blind the whole baseline."""
    truth = load_ground_truth(Path("benchmarks/guardian/pr-278.yaml"))

    assert truth.findings == []
    assert truth.ambiguous == []
```

Use the module's existing `_pred(file: str, line: int | None, confidence: int = 90)`
factory rather than defining a new one — it is already there at the top of the file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_bench.py -v -k pr_278`
Expected: FAIL — `FileNotFoundError` / validation error, the YAML does not exist

- [ ] **Step 3: Create the benchmark entry**

Create `benchmarks/guardian/pr-278.yaml`:

```yaml
# PR 278 — fix(guardian): explicit request timeout + bounded retry. See CURATION.md.
# Guardian produced 10 findings, skeptic-confirmed 10/10, ALL refuted (#279).
# head = review head (the snapshot guardian actually ran on); the two later
# commits on that branch postdate the review.
# GT findings: NONE → recall vacuously 1.0; this PR measures precision only.
pr: 278
base: 2e768cef585f10481970697657b0a9b40a411f47
head: 6ee175e553c784a363c190e00021be5d365fb8dd
findings: []
ambiguous: []
```

- [ ] **Step 4: Add the curation rule and the PR section**

In `benchmarks/guardian/CURATION.md`, add a fourth bullet to the
`## Curation policy` list, after the "Declined-with-reason" one:

```markdown
- Refuted claims → omitted from GT entirely, NOT `ambiguous`. A finding
  disproved by execution (the code cannot behave as claimed) is noise, and
  `ambiguous` exempts hits per FILE — filing errors there would make the suite
  structurally unable to observe a precision failure.
```

Then append a section documenting the entry:

```markdown
## PR 278 — fix(guardian): request timeout + bounded retry

- merge-base: 2e768cef585f10481970697657b0a9b40a411f47
- **review_head = 6ee175e553c784a363c190e00021be5d365fb8dd** (later commits on
  the branch postdate the review)

GT findings: NONE → recall vacuously 1.0; this PR measures precision only.
Guardian returned 10 findings, all skeptic-confirmed, all refuted (#279):

- `base.py:102` remove the "unreachable" `raise AssertionError` — **would break
  the build**; deleting it fails mypy with `Missing return statement`
- `base.py:7` `RETRYABLE_EXCEPTIONS` lacks type annotations — it is annotated
  `tuple[type[Exception], ...]`, and mypy strict passes
- `base.py:84` the tuple may miss httpx subclasses — every relevant transport
  exception is covered; the finding names ones that ARE caught
- `base.py:69` `_sleep` may receive a negative value — no caller can produce one
- `mistral.py:26`, `gemini.py:25` integer overflow in `timeout * 1000` — Python
  ints are arbitrary precision (counted twice, same non-issue)
- `runner.py:217` 2-dp rounding loses sub-ms precision — 10 ms on a 65 s value
- `metrics.py:68` no runtime validation of `duration_s` — no field in that
  module has any; it is a plain dict → JSON
- two test findings on brittle call counts — one's premise is false, the test
  already derives its expectation from `MAX_ATTEMPTS`
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_bench.py -v`
Expected: PASS

- [ ] **Step 6: Prove the whole path scores offline**

This is acceptance criterion 1 — a recording scored against the entry with no
API key anywhere:

```bash
uv run python -c "
from pathlib import Path
from cgis.guardian.findings import Finding, ReviewResult
from cgis.guardian.recording import save_finder_recording, load_finder_recording
from cgis.guardian.bench import load_ground_truth, match_findings, score

f = Finding(file='src/cgis/guardian/providers/base.py', line=102, severity='minor',
            category='logic', title='t', evidence='e', problem='p', fix='f', confidence=80)
p = Path('/tmp/rec279.json')
save_finder_recording(p, ReviewResult(findings=[f], summary='s'), 'diff')
rec = load_finder_recording(p)
truth = load_ground_truth(Path('benchmarks/guardian/pr-278.yaml'))
s = score(match_findings(rec.result.findings, truth), truth)
print(f'precision={s.precision} recall={s.recall} noise={s.noise}')
"
```
Expected: `precision=0.0 recall=1.0 noise=1`

- [ ] **Step 7: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add benchmarks/guardian/pr-278.yaml benchmarks/guardian/CURATION.md tests/unit/test_guardian_bench.py
git commit -m "test(bench): precision baseline entry for PR 278 (#279)"
```

---

## Definition of done

- `make format && make lint && make type-check && make pytest && make doc-coverage` all pass.
- `uv run mypy src` passes in a CI-shaped environment (`uv sync --group dev`, no guardian group).
- Every pre-existing guardian test passes unchanged — this adds persistence, not behaviour.
- The Step 6 one-liner prints `precision=0.0` with no provider key set.
- `chunked.py`, prompts, and the finder/skeptic logic are untouched.

## Post-merge verification (needs CI, not the branch)

Comment `/guardian review` on any PR and confirm a `guardian-finder-<pr>`
artifact appears alongside `guardian-metrics-<pr>`, and that
`load_finder_recording` parses it. This is acceptance criterion 3 and cannot be
checked locally — no provider key is available here.
