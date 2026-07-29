# Guardian Skeptic Scoring Implementation Plan (#246)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the guardian's single batch skeptic call with one call per finding that returns two orthogonal axes — `verdict` (is it true) and `impact_score` 0–10 (does it matter) — and give the benchmark the data to choose a threshold.

**Architecture:** `skeptic.py` changes shape from "one function over the whole list" to "one function per finding, run concurrently under a semaphore". The truth axis keeps its existing prompt and merge semantics byte-for-byte; the importance axis is new. `core.py` and `chunked.py` switch to the new API; `render.py`, `github_poster.py`, `runner.py` and the bench learn about the score. The batch API is deleted last, once nothing calls it.

**Tech Stack:** Python 3.12, pydantic v2 (frozen models), asyncio, structlog, pytest. No new dependencies.

## Global Constraints

- Spec: `docs/specs/2026-07-29-guardian-skeptic-scoring-design.md`. Read it before Task 1.
- `mypy --strict` must pass on `src/` after every task (`make type-check`). All functions fully annotated including return types.
- `ruff check` and `ruff format` clean (`make lint`, `make format`). Line length 100.
- Docstring coverage ≥ 90% (`make doc-coverage`) — every new public function and class needs a docstring.
- Full suite green after every task (`make pytest`). Baseline at plan time: **1080 passed, 2 skipped**.
- No live LLM calls in unit tests. Use the fake-provider pattern from `tests/unit/test_guardian_core.py:82`.
- The truth-axis system prompt (`SKEPTIC_SYSTEM_PROMPT`, `skeptic.py:38-48`) is **not edited**. Its wording was bought by reverting a refute-by-default version that killed 7/7 findings including 2 ground-truth matches.
- A missing judgement is **never** a refutation. Findings hidden by the impact threshold are **never** dropped from `ReviewResult.findings` — only from what is rendered.
- `cgis.guardian` has no `project_domain` binding, so `cgis drift` skips it: moving code inside the guardian package cannot move a drift ratchet. No re-baseline is expected in this plan.

---

### Task 1: Contract fields and the finder sanitizer

**Files:**
- Modify: `src/cgis/guardian/findings.py:13-40`
- Modify: `src/cgis/guardian/core.py:26-38`
- Test: `tests/unit/test_guardian_core.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `Finding.impact_score: int | None`, `ReviewResult.skeptic_status` extended with `"partial"`, `ReviewResult.skeptic_judged: int`, `ReviewResult.skeptic_total: int`. `_sanitize_finder_result` wipes all skeptic-owned fields.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_guardian_core.py` (near the other sanitizer tests):

```python
def test_sanitizer_wipes_finder_hallucinated_impact_score() -> None:
    """The finder shares ReviewResult as its schema and can invent skeptic-owned fields.

    A hallucinated impact_score would hide a real finding behind the threshold —
    quieter than the Plan-2 verdict="refuted" bug, same class.
    """
    hallucinated = ReviewResult(
        findings=[_FINDING.model_copy(update={"impact_score": 0, "verdict": "refuted"})],
        summary="s",
        skeptic_status="ok",
        skeptic_judged=1,
        skeptic_total=1,
    )

    clean = _sanitize_finder_result(hallucinated)

    assert clean.findings[0].impact_score is None
    assert clean.findings[0].verdict is None
    assert clean.skeptic_status == "off"
    assert clean.skeptic_judged == 0
    assert clean.skeptic_total == 0
```

`_FINDING` already exists in this module; if the import of `_sanitize_finder_result` is missing, add it to the existing `from cgis.guardian.core import ...` line.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_guardian_core.py::test_sanitizer_wipes_finder_hallucinated_impact_score -v`
Expected: FAIL — `ValidationError` on the unknown `impact_score` / `skeptic_judged` fields.

- [ ] **Step 3: Write minimal implementation**

In `src/cgis/guardian/findings.py`, add to `Finding` (after `verdict`/`skeptic_note`):

```python
    # 0-10 importance from the skeptic (spec §3.1). None = not judged. Kept
    # separate from `verdict`: a finding can be true (confirmed) and worthless
    # (score 1), which one enum value cannot express.
    impact_score: int | None = Field(default=None, ge=0, le=10)
```

Replace `ReviewResult.skeptic_status` and add the counters:

```python
    # "off" = skeptic not configured; "ok" = every finding judged; "partial" =
    # some judgement calls failed (see skeptic_judged/skeptic_total); "failed" =
    # no finding was judged (spec §3.4 — never silent).
    skeptic_status: Literal["off", "ok", "partial", "failed"] = "off"
    skeptic_judged: int = 0
    skeptic_total: int = 0
```

In `src/cgis/guardian/core.py`, extend the sanitizer:

```python
    findings = [
        f.model_copy(update={"verdict": None, "skeptic_note": None, "impact_score": None})
        for f in result.findings
    ]
    return result.model_copy(
        update={
            "findings": findings,
            "skeptic_status": "off",
            "skeptic_judged": 0,
            "skeptic_total": 0,
        }
    )
```

Update the sanitizer docstring to name `impact_score` alongside the other skeptic-owned fields.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_core.py -v` then `make pytest`
Expected: the new test passes; 1081 passed, 2 skipped.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/findings.py src/cgis/guardian/core.py tests/unit/test_guardian_core.py
git commit -m "feat(guardian): impact_score field, partial skeptic status, sanitizer coverage"
```

---

### Task 2: Judgement model, pure merge, and the impact threshold

**Files:**
- Modify: `src/cgis/guardian/skeptic.py`
- Test: `tests/unit/test_guardian_skeptic.py`

**Interfaces:**
- Consumes: `Finding.impact_score` (Task 1).
- Produces:
  - `class FindingJudgement(BaseModel, frozen=True)` with `verdict: Literal["confirmed","refuted","uncertain"]`, `impact_score: int` (0–10), `rationale: str`
  - `apply_judgements(findings: list[Finding], judgements: list[FindingJudgement | None]) -> list[Finding]`
  - `visible_findings(findings: Iterable[Finding], threshold: int = 0) -> list[Finding]`

The batch API (`SkepticVerdict`, `SkepticResult`, `build_skeptic_prompt`, `apply_verdicts`) stays untouched in this task so existing call sites keep working; Task 10 deletes it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_guardian_skeptic.py`:

```python
def _judgement(verdict: str, score: int, rationale: str = "because") -> FindingJudgement:
    return FindingJudgement(verdict=verdict, impact_score=score, rationale=rationale)  # type: ignore[arg-type]


def test_judgement_merges_verdict_note_and_score() -> None:
    """A judgement writes verdict, note and impact_score onto a new frozen copy."""
    merged = apply_judgements([_FINDING], [_judgement("confirmed", 7)])

    assert merged[0].verdict == "confirmed"
    assert merged[0].skeptic_note == "because"
    assert merged[0].impact_score == 7
    assert _FINDING.impact_score is None  # original untouched (frozen)


def test_judgement_uncertain_discounts_confidence_and_keeps_finding() -> None:
    """uncertain keeps the finding and discounts confidence x0.9, exactly as before."""
    f = _FINDING.model_copy(update={"confidence": 30})

    merged = apply_judgements([f], [_judgement("uncertain", 4)])

    assert merged[0].verdict == "uncertain"
    assert merged[0].confidence == 27
    assert merged[0].impact_score == 4
    assert visible_findings(merged) == merged


def test_missing_judgement_is_not_a_refutation() -> None:
    """None = the judgement call failed; the finding survives unruled and visible."""
    merged = apply_judgements([_FINDING], [None])

    assert merged[0].verdict is None
    assert merged[0].impact_score is None
    assert visible_findings(merged) == merged


def test_threshold_hides_low_impact_but_keeps_it_in_the_list() -> None:
    """Below-threshold findings are hidden from the report, never dropped from the result."""
    low = _FINDING.model_copy(update={"verdict": "confirmed", "impact_score": 1})
    high = _FINDING.model_copy(update={"verdict": "confirmed", "impact_score": 8})

    assert visible_findings([low, high], threshold=3) == [high]
    assert visible_findings([low, high]) == [low, high]  # default 0 hides nothing


def test_unjudged_finding_survives_a_threshold() -> None:
    """An unjudged finding has no score to compare; a threshold must not hide it."""
    assert visible_findings([_FINDING], threshold=5) == [_FINDING]
```

Extend the module's import block with `FindingJudgement` and `apply_judgements`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_skeptic.py -v`
Expected: FAIL at import — `cannot import name 'FindingJudgement'`.

- [ ] **Step 3: Write minimal implementation**

In `src/cgis/guardian/skeptic.py`, add after `SkepticResult`:

```python
class FindingJudgement(BaseModel, frozen=True):
    """One skeptic ruling on one finding, on two orthogonal axes (spec §3.1).

    ``verdict`` answers "is this claim true" and only an explicit 'refuted'
    drops the finding. ``impact_score`` answers "does it matter" and only
    hides below a threshold. No index: a judgement belongs to the call that
    produced it, which is why the batch API's index-mapping failure modes
    (out-of-range, duplicate) cannot occur here.
    """

    verdict: Literal["confirmed", "refuted", "uncertain"]
    impact_score: int = Field(ge=0, le=10)
    rationale: str
```

Add `Field` to the pydantic import line.

```python
def apply_judgements(
    findings: list[Finding], judgements: list[FindingJudgement | None]
) -> list[Finding]:
    """Merge per-finding judgements into new frozen copies, positionally (spec §3.2).

    ``judgements[i]`` rules on ``findings[i]``; a ``None`` means that call
    failed and the finding stays unruled — absence of a judgement is not a
    refutation. 'uncertain' discounts confidence x0.9 as a ranking signal only,
    identical to the batch merge it replaces.
    """
    merged: list[Finding] = []
    for finding, judgement in zip(findings, judgements, strict=True):
        if judgement is None:
            merged.append(finding)
            continue
        update: dict[str, object] = {
            "verdict": judgement.verdict,
            "skeptic_note": judgement.rationale,
            "impact_score": judgement.impact_score,
        }
        if judgement.verdict == "uncertain":
            update["confidence"] = round(finding.confidence * _UNCERTAIN_MULTIPLIER)
        merged.append(finding.model_copy(update=update))
    return merged
```

Replace `visible_findings` with:

```python
def visible_findings(findings: Iterable[Finding], threshold: int = 0) -> list[Finding]:
    """Findings that appear in the rendered report (spec §3.2).

    Hidden: anything refuted, and anything the skeptic scored below
    ``threshold``. An unjudged finding (``impact_score is None``) has no score
    to compare and is always shown — a failed judgement call must not silence a
    finding. Hidden findings remain in ``ReviewResult.findings`` so metrics and
    the benchmark still see them.
    """
    return [
        f
        for f in findings
        if f.verdict != "refuted" and not (f.impact_score is not None and f.impact_score < threshold)
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_skeptic.py -v` then `make pytest`
Expected: all pass; existing `visible_findings` callers unaffected (default threshold 0).

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/skeptic.py tests/unit/test_guardian_skeptic.py
git commit -m "feat(guardian): FindingJudgement + per-finding merge + impact threshold"
```

---

### Task 3: Move the per-file diff splitter to the diff leaf

**Files:**
- Modify: `src/cgis/guardian/diff_index.py`
- Modify: `src/cgis/guardian/chunker.py:58-98` (the whole `split_diff_by_file` body, up to but not including `build_chunks` at line 100)
- Test: `tests/unit/test_guardian_diff_index.py` (create if absent), `tests/unit/test_guardian_chunker.py` (unchanged, must stay green)

**Interfaces:**
- Consumes: nothing new.
- Produces: `diff_index.split_diff_by_file(diff_text: str) -> dict[str, str]`, re-exported from `chunker` so `chunker.split_diff_by_file` keeps working.

Rationale: Task 4 needs per-file diff blocks, and `diff_index.py` is the module whose stated purpose is pure unified-diff parsing. Importing it from `chunker` (a #154 concept, dormant behind a flag) would point the skeptic at the wrong layer.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_guardian_diff_index.py` if it does not exist, otherwise append:

```python
def test_split_diff_by_file_is_available_from_the_diff_leaf() -> None:
    """The per-file splitter lives with the other pure diff parsers."""
    diff = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n+++ b/src/a.py\n"
        "@@ -1,2 +1,2 @@\n-old\n+new\n"
        "diff --git a/src/b.py b/src/b.py\n"
        "--- a/src/b.py\n+++ b/src/b.py\n"
        "@@ -1,1 +1,1 @@\n-x\n+y\n"
    )

    blocks = split_diff_by_file(diff)

    assert set(blocks) == {"src/a.py", "src/b.py"}
    assert "+new" in blocks["src/a.py"]
    assert "+new" not in blocks["src/b.py"]
```

Import it as `from cgis.guardian.diff_index import split_diff_by_file`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_guardian_diff_index.py -v`
Expected: FAIL — `cannot import name 'split_diff_by_file' from 'cgis.guardian.diff_index'`.

- [ ] **Step 3: Move the function**

Cut `split_diff_by_file` and any private helper it uses exclusively (`_flush` is nested, so it moves with the body) from `chunker.py` into `diff_index.py`, verbatim — no behaviour edits. In `chunker.py`, replace the definition with a re-export at the top of the file:

```python
from cgis.guardian.diff_index import split_diff_by_file as split_diff_by_file  # noqa: PLC0414
```

The redundant alias plus `noqa` is this repo's established re-export idiom (see `resolver/symbols.py`) and keeps `mypy --strict`'s `no_implicit_reexport` happy.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_diff_index.py tests/unit/test_guardian_chunker.py -v` then `make pytest` and `make type-check`
Expected: the new test passes and all 24 existing chunker tests still pass — the move is behaviour-preserving.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/diff_index.py src/cgis/guardian/chunker.py tests/unit/test_guardian_diff_index.py
git commit -m "refactor(guardian): move split_diff_by_file to the pure diff leaf"
```

---

### Task 4: Judgement prompt and the single-finding call

**Files:**
- Modify: `src/cgis/guardian/skeptic.py`
- Test: `tests/unit/test_guardian_skeptic.py`

**Interfaces:**
- Consumes: `FindingJudgement` (Task 2).
- Produces:
  - `IMPACT_RUBRIC: str`
  - `build_judgement_prompt(finding: Finding, hunks: str) -> str`
  - `async judge_finding(provider: BaseProvider, finding: Finding, hunks: str) -> FindingJudgement | None`

- [ ] **Step 1: Write the failing tests**

```python
def test_judgement_prompt_hides_the_finders_self_assessment() -> None:
    """confidence and severity are the finder's guess at what we are re-deriving."""
    prompt = build_judgement_prompt(_FINDING.model_copy(update={"confidence": 85}), "@@ -1 +1 @@\n+x")

    assert "off-by-one" in prompt          # the claim itself is shown
    assert "range(n + 1)" in prompt        # and its evidence
    assert "85" not in prompt              # but not the finder's confidence
    assert "major" not in prompt           # nor its severity


def test_judgement_prompt_states_out_of_hunk_claims_are_uncertain() -> None:
    """Narrow context must not become a false-refutation generator (spec §3.3)."""
    prompt = build_judgement_prompt(_FINDING, "@@ -1 +1 @@\n+x")

    assert "uncertain" in prompt.lower()


async def test_judge_finding_parses_a_judgement() -> None:
    """A well-formed provider response becomes a FindingJudgement."""
    provider = _FakeProvider(
        '{"verdict": "confirmed", "impact_score": 7, "rationale": "real off-by-one"}'
    )

    judgement = await judge_finding(provider, _FINDING, "@@ -1 +1 @@\n+x")

    assert judgement is not None
    assert judgement.verdict == "confirmed"
    assert judgement.impact_score == 7


async def test_judge_finding_returns_none_on_unparseable_response() -> None:
    """A failed call yields None — the caller keeps the finding unruled, never drops it."""
    assert await judge_finding(_FakeProvider("not json"), _FINDING, "") is None


async def test_judge_finding_returns_none_when_the_provider_raises() -> None:
    """Provider errors are contained per finding (spec §3.4)."""

    class _BoomProvider(BaseProvider):
        async def generate_content(self, system_prompt: str, user_prompt: str) -> str:  # noqa: ARG002
            raise RuntimeError("429")

        async def generate_structured(
            self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
        ) -> str:
            return await self.generate_content(system_prompt, user_prompt)

    assert await judge_finding(_BoomProvider(), _FINDING, "") is None
```

Copy `_FakeProvider` from `tests/unit/test_guardian_core.py:82` into this module (or import it if the repo already shares fixtures via `tests/unit/conftest.py` — check first and prefer the shared location, since a Sonar duplication gate has fired on copied fixtures in this repo before). Async tests need no decorator: `asyncio_mode=auto` is configured.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_skeptic.py -v`
Expected: FAIL — `cannot import name 'build_judgement_prompt'`.

- [ ] **Step 3: Write minimal implementation**

In `skeptic.py`:

```python
IMPACT_RUBRIC = """
Also rate how much this finding MATTERS, independent of whether it is true,
as impact_score 0-10:

- 0-2  true but not actionable: style, taste, "consider X for explicitness",
       or restating something the project's tooling already enforces.
       If `ruff`, `ruff format` or `mypy --strict` would catch it, score <= 2:
       those run as mandatory gates in this repo, so such issues are already
       covered.
- 3-5  a minor real issue: local clarity or robustness, no behaviour change.
- 6-8  a real defect with a concrete failure path in this diff.
- 9-10 a broken contract, a security hole, or data loss.

A true finding can score 0. Scoring is NOT a second chance to refute: judge
truth and importance independently.
"""


def build_judgement_prompt(finding: Finding, hunks: str) -> str:
    """Assemble the user prompt judging ONE finding against its own file's hunks.

    The finder's ``confidence`` and ``severity`` are deliberately omitted: both
    are its own guess at what this pass re-derives independently, and showing
    them anchors the judge on the claim it is meant to check (spec §3.3).
    """
    location = f"{finding.file}:{finding.line}" if finding.line is not None else finding.file
    return f"""Another reviewer claims this diff contains a defect.

### THE CLAIM
Location: {location}
Title: {finding.title}
Quoted code: {finding.evidence}
Problem: {finding.problem}
Proposed fix: {finding.fix}

### THE DIFF HUNKS FOR THAT FILE
{hunks or "(no hunks available for this file)"}

### HOW TO JUDGE
Verify the quoted code against the hunks above. If the claim depends on code
that is NOT in these hunks, you cannot check it: that is grounds for
'uncertain', never for 'refuted'.
{IMPACT_RUBRIC}

### OUTPUT FORMAT
Return ONLY a JSON object:
{{"verdict": "confirmed|refuted|uncertain", "impact_score": 0, "rationale": "one sentence"}}"""


async def judge_finding(
    provider: BaseProvider, finding: Finding, hunks: str
) -> FindingJudgement | None:
    """Judge one finding; None means this call failed (spec §3.2).

    Every failure mode — transport error, rate limit, unparseable output —
    collapses to None so the caller keeps the finding unruled and visible. A
    skeptic that cannot answer must never be able to silence a finding.
    """
    try:
        raw = await provider.generate_structured(
            SKEPTIC_SYSTEM_PROMPT, build_judgement_prompt(finding, hunks), FindingJudgement
        )
        return FindingJudgement.model_validate_json(extract_json(raw))
    except Exception:
        log.warning("Skeptic judgement failed; finding stays unruled.", file=finding.file)
        return None
```

Add imports: `from cgis.guardian.findings import Finding, extract_json` and
`from cgis.guardian.providers.base import BaseProvider`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_skeptic.py -v` then `make pytest`, `make type-check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/skeptic.py tests/unit/test_guardian_skeptic.py
git commit -m "feat(guardian): per-finding judgement prompt with impact rubric"
```

---

### Task 5: Concurrent judgement over all findings

**Files:**
- Modify: `src/cgis/guardian/skeptic.py`
- Test: `tests/unit/test_guardian_skeptic.py`

**Interfaces:**
- Consumes: `judge_finding` (Task 4), `split_diff_by_file` (Task 3).
- Produces: `async judge_all(provider: BaseProvider, findings: list[Finding], diff: str, concurrency: int = DEFAULT_SKEPTIC_CONCURRENCY) -> list[FindingJudgement | None]` and `DEFAULT_SKEPTIC_CONCURRENCY: int = 3`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_judge_all_returns_one_result_per_finding_in_order() -> None:
    """Positional contract: judgements[i] rules on findings[i]."""
    provider = _SequenceProvider(
        [
            '{"verdict": "confirmed", "impact_score": 8, "rationale": "a"}',
            '{"verdict": "refuted", "impact_score": 0, "rationale": "b"}',
        ]
    )
    findings = [_FINDING, _FINDING.model_copy(update={"title": "second"})]

    judgements = await judge_all(provider, findings, "", concurrency=1)

    assert [j.verdict for j in judgements if j] == ["confirmed", "refuted"]


async def test_judge_all_isolates_a_failing_call() -> None:
    """One bad response costs one verdict, not the whole pass."""
    provider = _SequenceProvider(
        ["not json", '{"verdict": "confirmed", "impact_score": 6, "rationale": "ok"}']
    )
    findings = [_FINDING, _FINDING.model_copy(update={"title": "second"})]

    judgements = await judge_all(provider, findings, "", concurrency=1)

    assert judgements[0] is None
    assert judgements[1] is not None


async def test_judge_all_feeds_each_finding_only_its_own_file_hunks() -> None:
    """Per-finding context is the point of the isolation (spec §3.3)."""
    provider = _SequenceProvider(['{"verdict": "confirmed", "impact_score": 5, "rationale": "x"}'])
    diff = (
        "diff --git a/src/cgis/cli.py b/src/cgis/cli.py\n"
        "--- a/src/cgis/cli.py\n+++ b/src/cgis/cli.py\n"
        "@@ -1,1 +1,1 @@\n-old\n+cli_line\n"
        "diff --git a/other.py b/other.py\n"
        "--- a/other.py\n+++ b/other.py\n"
        "@@ -1,1 +1,1 @@\n-x\n+other_line\n"
    )

    await judge_all(provider, [_FINDING], diff, concurrency=1)

    assert "cli_line" in provider.structured_prompts[0]
    assert "other_line" not in provider.structured_prompts[0]


async def test_judge_all_never_exceeds_the_concurrency_limit() -> None:
    """The semaphore is what keeps mistral's per-minute token cap out of reach."""

    class _ConcurrencyProbe(BaseProvider):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.peak = 0

        async def generate_content(self, system_prompt: str, user_prompt: str) -> str:  # noqa: ARG002
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0)  # yield so overlapping calls can pile up
            self.active -= 1
            return '{"verdict": "confirmed", "impact_score": 5, "rationale": "x"}'

        async def generate_structured(
            self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
        ) -> str:
            return await self.generate_content(system_prompt, user_prompt)

    probe = _ConcurrencyProbe()

    await judge_all(probe, [_FINDING] * 10, "", concurrency=3)

    assert probe.peak <= 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_skeptic.py -v`
Expected: FAIL — `cannot import name 'judge_all'`.

- [ ] **Step 3: Write minimal implementation**

```python
DEFAULT_SKEPTIC_CONCURRENCY = 3  # mistral's free tier caps tokens per MINUTE; a
# local ollama skeptic serialises on one model instance anyway.


async def judge_all(
    provider: BaseProvider,
    findings: list[Finding],
    diff: str,
    concurrency: int = DEFAULT_SKEPTIC_CONCURRENCY,
) -> list[FindingJudgement | None]:
    """Judge every finding concurrently, each against its own file's hunks.

    Returns one entry per finding, positionally aligned, with None where that
    finding's call failed. Concurrency is bounded because provider rate limits
    are the binding constraint, not local CPU.
    """
    blocks = split_diff_by_file(diff)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _one(finding: Finding) -> FindingJudgement | None:
        async with semaphore:
            return await judge_finding(provider, finding, blocks.get(finding.file, ""))

    return list(await asyncio.gather(*(_one(f) for f in findings)))
```

Add `import asyncio` and `from cgis.guardian.diff_index import split_diff_by_file`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_skeptic.py -v` then `make pytest`, `make type-check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/skeptic.py tests/unit/test_guardian_skeptic.py
git commit -m "feat(guardian): bounded-concurrency judge_all with per-call isolation"
```

---

### Task 6: Wire the reviewer to per-finding judgement

**Files:**
- Modify: `src/cgis/guardian/core.py:86-103`
- Test: `tests/unit/test_guardian_core.py`

**Interfaces:**
- Consumes: `judge_all`, `apply_judgements` (Tasks 2, 5).
- Produces: `GuardianReviewer.run_review` sets `skeptic_status` ∈ {`off`,`ok`,`partial`,`failed`} plus `skeptic_judged`/`skeptic_total`.

- [ ] **Step 1: Write the failing tests**

Add these module-level constants first:

```python
def _finding_json(title: str) -> str:
    return (
        '{"file": "src/a.py", "line": 3, "severity": "major", "category": "logic",'
        f' "title": "{title}", "evidence": "e", "problem": "p", "fix": "f",'
        ' "confidence": 90}'
    )


_ONE_FINDING = '{"findings": [' + _finding_json("first") + '], "summary": "s"}'
_TWO_FINDINGS = (
    '{"findings": [' + _finding_json("first") + "," + _finding_json("second") + '], "summary": "s"}'
)
_JUDGE_OK = '{"verdict": "confirmed", "impact_score": 7, "rationale": "ok"}'
```

```python
async def test_run_review_reports_partial_when_some_judgements_fail() -> None:
    """Partial is its own status: neither a lie ('ok') nor a discard ('failed')."""
    skeptic = _SequenceProvider(["not json", _JUDGE_OK])
    reviewer = GuardianReviewer(
        _FakeProvider(_TWO_FINDINGS), _stub_collector(), skeptic_provider=skeptic
    )

    result = await reviewer.run_review()

    assert result.skeptic_status == "partial"
    assert result.skeptic_judged == 1
    assert result.skeptic_total == 2
    assert len(visible_findings(result.findings)) == 2  # the unruled one survives


async def test_run_review_reports_failed_when_no_judgement_lands() -> None:
    """Every call failing is 'failed', and findings are returned single-pass."""
    reviewer = GuardianReviewer(
        _FakeProvider(_ONE_FINDING), _stub_collector(), skeptic_provider=_FakeProvider("not json")
    )

    result = await reviewer.run_review()

    assert result.skeptic_status == "failed"
    assert result.skeptic_judged == 0
    assert len(result.findings) == 1
```

`_stub_collector()` stands for whatever collector fixture this module already
uses to drive `GuardianReviewer` (the existing skeptic tests in
`tests/unit/test_guardian_core.py` construct one) — reuse it verbatim rather
than adding a second collector stub.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_core.py -k "partial or failed_when_no_judgement" -v`
Expected: FAIL — status is `ok` or `failed` from the old batch path, counters are 0.

- [ ] **Step 3: Write minimal implementation**

Replace `run_review`'s skeptic block:

```python
    async def run_review(self) -> ReviewResult:
        """Run the review; optionally judge each finding with the skeptic pass (spec §3.4)."""
        context = self.context_collector.collect_all()
        result = await self._finder_pass(context)
        if self.skeptic_provider is None or not result.findings or result.parse_failed:
            return result
        judgements = await judge_all(
            self.skeptic_provider, result.findings, context.get("diff", ""), self.concurrency
        )
        judged = sum(1 for j in judgements if j is not None)
        if judged == 0:
            log.warning("Every skeptic judgement failed; returning single-pass results.")
        merged = apply_judgements(result.findings, judgements)
        return result.model_copy(
            update={
                "findings": merged,
                "skeptic_status": skeptic_status_for(judged, len(judgements)),
                "skeptic_judged": judged,
                "skeptic_total": len(judgements),
            }
        )
```

Add the status helper to **`skeptic.py`** (public, because Task 7 needs the same
mapping and both call sites must agree):

```python
def skeptic_status_for(judged: int, total: int) -> Literal["ok", "partial", "failed"]:
    """Map judged/total onto the reported skeptic status (spec §3.4).

    Public and shared: the chunked orchestrator reports the same statuses, and
    two copies of this mapping would be free to drift apart.
    """
    if judged == 0:
        return "failed"
    return "ok" if judged == total else "partial"
```

Add a `concurrency` parameter to `GuardianReviewer.__init__`, defaulting to
`DEFAULT_SKEPTIC_CONCURRENCY`, stored as `self.concurrency`. Update `core.py`'s
imports: drop `SKEPTIC_SYSTEM_PROMPT`, `SkepticResult`, `apply_verdicts`,
`build_skeptic_prompt`; add `DEFAULT_SKEPTIC_CONCURRENCY`, `apply_judgements`,
`judge_all`, `skeptic_status_for`. `Literal` is imported in `skeptic.py`
already.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_core.py -v` then `make pytest`, `make type-check`
Expected: PASS. Existing skeptic tests in this module that asserted `skeptic_status == "ok"` on a full success still pass; any that fed a batch-shaped `{"verdicts": [...]}` response need their canned response rewritten to the per-finding shape — that is an expected, intended test change.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/core.py tests/unit/test_guardian_core.py
git commit -m "feat(guardian): reviewer judges findings one by one, reports partial"
```

---

### Task 7: Port the chunked orchestrator to the same API

**Files:**
- Modify: `src/cgis/guardian/chunked.py:170-195`
- Test: `tests/unit/test_guardian_chunked.py`

**Interfaces:**
- Consumes: `judge_all`, `apply_judgements`, `_skeptic_status` semantics (Task 6).
- Produces: no new public API — removes the second batch call site.

`chunked.py` stays behind `GUARDIAN_FEATURES=chunked` (off in production after its benchmark failed). It is ported so a diverging second skeptic call site does not survive Task 11; it is **not** re-benchmarked here.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_chunked_review_uses_per_finding_judgement(tmp_path: Path) -> None:
    """The chunked path must not keep its own batch skeptic call alive."""
    diff = fdiff("src/a.py")
    provider = StubProvider([_finder_json("src/a.py")])
    skeptic = StubProvider(['{"verdict": "confirmed", "impact_score": 9, "rationale": "real"}'])

    routed = await run_chunked_review(
        provider=provider,
        collector=_collector(tmp_path, diff),
        skeptic_provider=skeptic,
    )

    assert routed.result.findings[0].impact_score == 9
    assert routed.result.skeptic_status == "ok"
    assert routed.result.skeptic_judged == 1
    assert routed.result.skeptic_total == 1
    # The prompt is the per-finding shape, not the indexed batch list.
    assert "finding_index" not in skeptic.prompts[0]
```

`fdiff`, `_finder_json`, `StubProvider` and `_collector` already exist in
`tests/unit/test_guardian_chunked.py` — use them, do not invent a new fixture
shape. Also delete the now-unused `_CONFIRM_ALL` batch constant
(`tests/unit/test_guardian_chunked.py:112`) and update any test that fed it to
the per-finding response shape used above.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_guardian_chunked.py -k per_finding_judgement -v`
Expected: FAIL — `impact_score` is None because the batch path ran.

- [ ] **Step 3: Write minimal implementation**

Replace the skeptic block in `chunked.py` with the same shape as Task 6: call
`judge_all(skeptic_provider, merged.findings, skeptic_context.get("diff", ""), concurrency)`,
count `judged`, merge with `apply_judgements`, set `skeptic_status` via the same
mapping, and populate `skeptic_judged`/`skeptic_total`. Import `_skeptic_status`
from `core` (make it public as `skeptic_status_for(judged, total)` if the
implementer prefers not to import a private name — pick one and use it in both
call sites).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_chunked.py -v` then `make pytest`, `make type-check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/chunked.py tests/unit/test_guardian_chunked.py
git commit -m "refactor(guardian): chunked path judges findings one by one too"
```

---

### Task 8: Report ordering, score display, and the threshold knob

**Files:**
- Modify: `src/cgis/guardian/render.py`
- Modify: `src/cgis/guardian/github_poster.py:81`
- Modify: `src/cgis/guardian/runner.py` (env parsing near `_ollama_num_ctx:35`, footer at `build_footer:151`)
- Modify: `src/cgis/guardian/metrics.py:21,42`
- Test: `tests/unit/test_guardian_render.py`, `tests/unit/test_guardian_runner.py`

**Interfaces:**
- Consumes: `visible_findings(findings, threshold)` (Task 2), `ReviewResult.skeptic_judged/skeptic_total` (Task 1).
- Produces: `runner.impact_threshold(env: Mapping[str, str]) -> int` reading `GUARDIAN_IMPACT_THRESHOLD` (default 0); `render_report(result, *, threshold: int = 0)`; metrics JSONL gains `skeptic_judged`, `skeptic_total`, `impact_threshold`.

- [ ] **Step 1: Write the failing tests**

```python
def test_report_orders_by_impact_then_severity() -> None:
    """A scored report ranks by what matters, not by the finder's severity guess."""
    low = _finding(severity="critical", title="loud but trivial", impact_score=1)
    high = _finding(severity="minor", title="quiet but real", impact_score=9)

    body = render_report(ReviewResult(findings=[low, high], summary="s"))

    assert body.index("quiet but real") < body.index("loud but trivial")


def test_report_shows_the_impact_score() -> None:
    """A human must see the ranking signal, not just its effect."""
    body = render_report(ReviewResult(findings=[_finding(impact_score=7)], summary="s"))

    assert "Impact: 7/10" in body


def test_report_notes_hidden_low_impact_findings() -> None:
    """Hiding is never silent — same rule the refuted counter already follows."""
    body = render_report(
        ReviewResult(findings=[_finding(impact_score=1), _finding(impact_score=9)], summary="s"),
        threshold=5,
    )

    assert "1 finding was below the impact threshold" in body


def test_report_notes_a_partial_skeptic_pass() -> None:
    """partial must read as partial, not as a clean pass."""
    body = render_report(
        ReviewResult(
            findings=[_finding(impact_score=8)],
            summary="s",
            skeptic_status="partial",
            skeptic_judged=1,
            skeptic_total=3,
        )
    )

    assert "1 of 3" in body


def test_impact_threshold_defaults_to_zero_and_reads_env() -> None:
    """Ships inert: the knob only moves once the benchmark shows the distribution."""
    assert impact_threshold({}) == 0
    assert impact_threshold({"GUARDIAN_IMPACT_THRESHOLD": "4"}) == 4
    assert impact_threshold({"GUARDIAN_IMPACT_THRESHOLD": "nonsense"}) == 0
```

Add this helper at module level in the render test file:

```python
def _finding(
    *, severity: str = "major", title: str = "t", impact_score: int | None = None
) -> Finding:
    return Finding(
        file="src/a.py",
        line=3,
        severity=severity,  # type: ignore[arg-type]
        category="logic",
        title=title,
        evidence="e",
        problem="p",
        fix="f",
        confidence=90,
        verdict="confirmed",
        impact_score=impact_score,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_render.py tests/unit/test_guardian_runner.py -v`
Expected: FAIL — ordering is severity-only, no score in the body, `impact_threshold` missing.

- [ ] **Step 3: Write minimal implementation**

In `render.py`:

- `render_finding` appends `f" · Impact: {finding.impact_score}/10"` to the severity line when `impact_score is not None`.
- `render_report(result: ReviewResult, *, threshold: int = 0)`: pass `threshold` into `visible_findings`; sort with
  `key=lambda f: (-(f.impact_score if f.impact_score is not None else -1), _SEVERITY_ORDER[f.severity])`
  so scored findings rank by score descending, and unscored ones fall to the end
  by severity rather than pretending to be a 0.
- Add a note line when findings were hidden by the threshold (count them as
  `len(non-refuted) - len(visible)`), mirroring the existing refuted note, and a
  note for `skeptic_status == "partial"` reading `_Skeptic judged {judged} of {total} findings._`
- `render_review_body` takes and forwards the same `threshold`.

In `github_poster.py:81`, thread the threshold into `visible_findings`.

In `runner.py`, add next to the other env readers:

```python
def impact_threshold(env: Mapping[str, str]) -> int:
    """Read GUARDIAN_IMPACT_THRESHOLD; 0 (nothing hidden) on absence or garbage.

    Ships inert on purpose (spec §3.5): the threshold is chosen from the
    benchmark's score distribution, not guessed.
    """
    raw = env.get("GUARDIAN_IMPACT_THRESHOLD", "").strip()
    try:
        return max(0, min(10, int(raw)))
    except ValueError:
        return 0
```

Thread it into the render/post call sites, and extend `build_footer` to append
`· skeptic {judged}/{total}` when `skeptic_total` is non-zero.

In `metrics.py`, add `skeptic_judged`, `skeptic_total` and `impact_threshold`
parameters (defaults 0) and write them into the JSONL record.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_render.py tests/unit/test_guardian_runner.py -v` then `make pytest`, `make type-check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/render.py src/cgis/guardian/github_poster.py src/cgis/guardian/runner.py src/cgis/guardian/metrics.py tests/unit/
git commit -m "feat(guardian): rank findings by impact, expose threshold and judged counts"
```

---

### Task 9: Benchmark records per-finding scores and GT match

**Files:**
- Modify: `scripts/guardian_bench.py:115-140`
- Modify: `src/cgis/guardian/bench.py`
- Test: `tests/unit/test_guardian_bench.py`

**Interfaces:**
- Consumes: `Finding.impact_score`, `MatchResult` from `bench.py:89`.
- Produces: JSONL gains a `findings` array of `{file, line, title, verdict, impact_score, matched_gt}` and `score_separation` computed by `bench.score_separation(...)`.

- [ ] **Step 1: Write the failing test**

```python
def test_score_separation_is_the_gap_between_gt_and_noise_medians() -> None:
    """The gate metric (spec §4.3): does the skeptic rank real findings above noise?"""
    gt_scores = [8, 9, 7]
    noise_scores = [1, 2, 0, 3]

    assert score_separation(gt_scores, noise_scores) == 6.0  # median 8 - median 2


def test_score_separation_is_none_without_both_populations() -> None:
    """A run with no GT match (or no noise) cannot answer the question."""
    assert score_separation([], [1, 2]) is None
    assert score_separation([8], []) is None


def test_score_separation_ignores_unscored_findings() -> None:
    """Unjudged findings carry no ranking signal and must not drag a median."""
    assert score_separation([8, 9], [2, 2]) == 6.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_guardian_bench.py -k score_separation -v`
Expected: FAIL — `cannot import name 'score_separation'`.

- [ ] **Step 3: Write minimal implementation**

In `src/cgis/guardian/bench.py`:

```python
def score_separation(gt_scores: Sequence[int], noise_scores: Sequence[int]) -> float | None:
    """Median impact_score of GT-matching findings minus that of the rest (spec §4.3).

    None when either population is empty — with nothing to compare, the run
    cannot answer whether the skeptic ranks. Callers pool across benchmark PRs
    before calling: individual PRs carry 1-6 GT matches, too few for a median
    to mean anything.
    """
    if not gt_scores or not noise_scores:
        return None
    return statistics.median(gt_scores) - statistics.median(noise_scores)
```

In `scripts/guardian_bench.py`, after scoring, emit per-finding records into the
JSONL run entry:

```python
        "findings": [
            {
                "file": f.file,
                "line": f.line,
                "title": f.title,
                "verdict": f.verdict,
                "impact_score": f.impact_score,
                "matched_gt": i in matched_indices,
            }
            for i, f in enumerate(result.findings)
        ],
```

`MatchResult.matched` (`bench.py:89`) is a `dict[str, int]` mapping each
ground-truth id to the index of the prediction that matched it, so the matched
prediction indices are exactly `set(match.matched.values())`. Compute
`matched_indices = set(match.matched.values())` once and use `i in
matched_indices` while enumerating `result.findings` — do not re-implement
matching. Run the bench with `threshold=0` so nothing is hidden.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_bench.py -v` then `make pytest`, `make type-check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/bench.py scripts/guardian_bench.py tests/unit/test_guardian_bench.py
git commit -m "feat(bench): record per-finding impact scores and GT match, add score separation"
```

---

### Task 10: Replay mode — freeze finder output

**Files:**
- Modify: `scripts/guardian_bench.py`
- Test: `tests/unit/test_guardian_bench.py`

**Interfaces:**
- Consumes: Task 9's JSONL fields.
- Produces: `--record-finder <path>` and `--replay-finder <path>` CLI flags; a replay run makes **zero** finder LLM calls.

- [ ] **Step 1: Write the failing test**

```python
async def test_replay_loads_recorded_findings_from_disk(tmp_path: Path) -> None:
    """The skeptic is measured against a frozen finder set, so the finder's own
    stochasticity stops leaking into the comparison (spec §4.1)."""
    recorded = tmp_path / "finder.json"
    recorded.write_text(_RECORDED_FINDER_JSON, encoding="utf-8")

    result = await replay_finder_result(recorded)

    assert len(result.findings) == 1
    assert result.findings[0].file == "src/a.py"
    assert result.parse_failed is False
```

with, at module level:

```python
_RECORDED_FINDER_JSON = (
    '{"findings": [{"file": "src/a.py", "line": 3, "severity": "major",'
    ' "category": "logic", "title": "t", "evidence": "e", "problem": "p",'
    ' "fix": "f", "confidence": 90}], "summary": "s"}'
)
```

That the finder provider is never constructed in replay mode is enforced by the
wiring in Step 3 (the `--replay-finder` branch never reaches `finder_pass`), and
covered end-to-end by the phase-1 run rather than by a mock that asserts on its
own absence.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_guardian_bench.py -k replay -v`
Expected: FAIL — `cannot import name 'replay_finder_result'`.

- [ ] **Step 3: Write minimal implementation**

Add to `scripts/guardian_bench.py`:

```python
async def replay_finder_result(path: Path) -> ReviewResult:
    """Load a previously recorded finder ReviewResult (spec §4.1).

    Skeptic variants must judge the SAME findings: re-running the stochastic
    finder per variant measures the sum of two noise sources, which is how a
    single lucky finding moved a PR's median in earlier n=3 runs.
    """
    return ReviewResult.model_validate_json(path.read_text(encoding="utf-8"))
```

Wire the two flags: `--record-finder PATH` writes `result.model_dump_json()` of
the finder pass before the skeptic runs; `--replay-finder PATH` skips
`finder_pass` entirely and feeds the loaded result into the skeptic stage. The
two flags are mutually exclusive — error out if both are given.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_bench.py -v` then `make pytest`, `make type-check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/guardian_bench.py tests/unit/test_guardian_bench.py
git commit -m "feat(bench): record/replay finder output to isolate the skeptic variable"
```

---

### Task 11: Delete the batch API and amend the sprint spec

**Files:**
- Modify: `src/cgis/guardian/skeptic.py` (remove `SkepticVerdict`, `SkepticResult`, `build_skeptic_prompt`, `apply_verdicts`)
- Modify: `tests/unit/test_guardian_skeptic.py` (remove the batch tests)
- Modify: `docs/specs/2026-06-10-guardian-sprint-design.md` §5.2

**Interfaces:**
- Consumes: nothing — this task only removes what Tasks 6 and 7 orphaned.
- Produces: no public API.

- [ ] **Step 1: Verify nothing still calls the batch API**

Run: `grep -rn "apply_verdicts\|SkepticResult\|build_skeptic_prompt\|SkepticVerdict" src tests scripts`
Expected: hits only in `skeptic.py` itself and in the batch tests about to be deleted. **If any other call site appears, stop** — a previous task left the port incomplete.

- [ ] **Step 2: Delete the batch API and its tests**

Remove the four symbols from `skeptic.py` and the tests that exercise index
mapping (`test_out_of_range_and_duplicate_indices_discarded` and siblings). The
behaviours those tests protected — refuted drops, uncertain keeps and discounts,
absence is not refutation — are already covered by Task 2's tests against
`apply_judgements`; confirm each one has an equivalent before deleting.

- [ ] **Step 3: Run the full suite**

Run: `make pytest`, `make type-check`, `make lint`, `make doc-coverage`
Expected: all green, no unused-import warnings.

- [ ] **Step 4: Amend the sprint spec**

In `docs/specs/2026-06-10-guardian-sprint-design.md` §5.2, append:

```markdown
> **Amendment (2026-07-29, #246):** the "one call, not N" decision is superseded
> by per-finding judgement — see `2026-07-29-guardian-skeptic-scoring-design.md`.
> One call was both cheap and sufficient for a finder capped at 5 findings; the
> recall-lean finder (#249) emits 10-26, and the single call became the
> bottleneck: on PR #263 it confirmed all 8 findings at one severity, leaving a
> real defect indistinguishable from two factually wrong claims.
```

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/skeptic.py tests/unit/test_guardian_skeptic.py docs/specs/2026-06-10-guardian-sprint-design.md
git commit -m "refactor(guardian): drop the batch skeptic API, amend sprint spec §5.2"
```

---

## After the plan: phase 1 benchmark (not a code task)

With the code merged, run the A/B from spec §4.3 — same frozen finder output,
same skeptic model (codestral), batch arm versus per-finding arm:

```bash
set -a; . ./.env; set +a
uv run python scripts/guardian_bench.py --pr 143 --runs 1 --record-finder /tmp/pr143-finder.json
uv run python scripts/guardian_bench.py --pr 143 --replay-finder /tmp/pr143-finder.json
```

Gate: noise down, zero lost GT versus the batch arm, and pooled
`median(score | GT) − median(score | non-GT) ≥ 3`. Phase 2 (the paid model
matrix — gemini, local ollama) needs an explicit budget decision first: the
prior gemini benchmark hit the 30 PLN cap.
