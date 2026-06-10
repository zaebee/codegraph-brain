# Guardian Structured Findings + Benchmark Harness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement build-order steps 1–2 of `docs/specs/2026-06-10-guardian-sprint-design.md`: structured findings JSON as the guardian's core contract, plus the benchmark harness and a committed baseline measurement.

**Architecture:** `Finding`/`ReviewResult` frozen Pydantic models become the provider→reviewer contract (`generate_structured` on both providers, parse+retry+fallback in `GuardianReviewer`); a pure `render_report` keeps the PR-comment markdown back-compatible. The benchmark replays guardian on 6 merged PRs against hand-curated ground-truth YAML; matching/scoring are pure functions in `cgis.guardian.bench`.

**Tech Stack:** Python 3.12, Pydantic v2 (frozen models), structlog, pytest (asyncio via existing config), PyYAML, google-genai + mistralai (lazy imports, guardian dep-group), git worktrees for replay.

**Context for the implementer (read first):**

- Spec: `docs/specs/2026-06-10-guardian-sprint-design.md` §2 (structured findings) and §3 (benchmark). Sections 4–6 (context upgrades, skeptic, inline) are a LATER plan — do not implement them; `Finding.verdict`/`skeptic_note` fields exist now but stay `None`.
- MyPy is strict; every function needs full annotations. Pre-commit runs ruff format / ruff check / mypy automatically on `git commit`.
- Verification gate before every commit: `make format && make lint && make type-check && make pytest` (doc-coverage `make doc-coverage` must stay ≥90% — every new module/function needs a docstring).
- Existing test conventions: see `tests/unit/test_guardian_core.py` — provider SDKs are mocked via `patch.dict("sys.modules", ...)` because google-genai/mistralai live in the `guardian` dep-group and are absent in CI.

**File structure (locked):**

| File | Responsibility |
| --- | --- |
| Create `src/cgis/guardian/findings.py` | `Finding`, `ReviewResult` models + `extract_json` fence-stripper |
| Create `src/cgis/guardian/render.py` | `render_finding`, `render_report` (pure ReviewResult→markdown) |
| Create `src/cgis/guardian/runner.py` | provider selection + script orchestration (testable, replaces logic in scripts/guardian_review.py) |
| Create `src/cgis/guardian/bench.py` | ground-truth models/loader, `match_findings`, `score` |
| Create `scripts/guardian_bench.py` | replay runner (git worktree + ingest + review + score → results.jsonl) |
| Modify `src/cgis/guardian/providers/base.py` | add abstract `generate_structured` |
| Modify `src/cgis/guardian/providers/gemini.py` | JSON-mode implementation |
| Modify `src/cgis/guardian/providers/mistral.py` | json_object-mode implementation |
| Modify `src/cgis/guardian/core.py` | `run_review() -> ReviewResult`, parse/retry/fallback |
| Modify `src/cgis/guardian/prompts.py` | OUTPUT FORMAT section → JSON schema |
| Modify `src/cgis/guardian/metrics.py` | structured params, drop regex counting, add `parse_failed` |
| Modify `src/cgis/guardian/collector.py` | optional `base_ref` override for bench replay |
| Modify `scripts/guardian_review.py` | thin shim over `runner.py` |
| Create `benchmarks/guardian/pr-*.yaml` | ground truth (Task 10, controller-curated) |

Branch: create `feat/guardian-structured-findings` from up-to-date `main` before Task 1.

---

### Task 1: Findings models + JSON fence stripper

**Files:**
- Create: `src/cgis/guardian/findings.py`
- Test: `tests/unit/test_guardian_findings.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the structured findings contract (spec §2.1)."""

import pytest
from pydantic import ValidationError

from cgis.guardian.findings import Finding, ReviewResult, extract_json


def _finding(**overrides: object) -> Finding:
    base: dict[str, object] = {
        "file": "src/cgis/cli.py",
        "line": 42,
        "severity": "major",
        "category": "logic",
        "title": "off-by-one in pagination",
        "evidence": "for i in range(n + 1):",
        "problem": "iterates one element past the end.",
        "fix": "use range(n).",
        "confidence": 85,
    }
    base.update(overrides)
    return Finding.model_validate(base)


def test_finding_minimal_valid() -> None:
    """A fully-populated finding validates; skeptic fields default to None."""
    f = _finding()
    assert f.verdict is None
    assert f.skeptic_note is None


def test_finding_is_frozen() -> None:
    """Finding is immutable — updates go through model_copy."""
    f = _finding()
    with pytest.raises(ValidationError):
        f.confidence = 90  # type: ignore[misc]
    assert f.model_copy(update={"verdict": "confirmed"}).verdict == "confirmed"


def test_finding_line_must_be_positive() -> None:
    """line=0 violates gt=0 (gemini round-2 constraint)."""
    with pytest.raises(ValidationError):
        _finding(line=0)


def test_finding_line_none_means_file_level() -> None:
    """line=None is valid (file-level finding)."""
    assert _finding(line=None).line is None


def test_finding_confidence_bounds() -> None:
    """confidence outside [0, 100] is rejected."""
    with pytest.raises(ValidationError):
        _finding(confidence=101)
    with pytest.raises(ValidationError):
        _finding(confidence=-1)


def test_finding_rejects_unknown_severity_and_category() -> None:
    """Literal fields reject values outside the closed sets."""
    with pytest.raises(ValidationError):
        _finding(severity="blocker")
    with pytest.raises(ValidationError):
        _finding(category="style")


def test_review_result_empty_findings_is_lgtm() -> None:
    """Empty findings list with a summary is the LGTM shape."""
    r = ReviewResult(findings=[], summary="checked X and Y")
    assert r.findings == []
    assert r.parse_failed is False


def test_review_result_round_trips_json() -> None:
    """model_validate_json(model_dump_json()) is the identity."""
    r = ReviewResult(findings=[_finding()], summary="s")
    assert ReviewResult.model_validate_json(r.model_dump_json()) == r


def test_extract_json_plain() -> None:
    """Plain JSON passes through untouched."""
    assert extract_json('{"a": 1}') == '{"a": 1}'


def test_extract_json_strips_fences() -> None:
    """```json fences (LLM habit) are stripped."""
    fenced = '```json\n{"a": 1}\n```'
    assert extract_json(fenced) == '{"a": 1}'


def test_extract_json_strips_bare_fences() -> None:
    """``` fences without a language tag are stripped too."""
    fenced = '```\n{"a": 1}\n```'
    assert extract_json(fenced) == '{"a": 1}'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_findings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cgis.guardian.findings'`

- [ ] **Step 3: Write the implementation**

```python
"""Structured findings contract for the Guardian reviewer (spec §2.1)."""

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "major", "minor"]
Category = Literal["logic", "contract", "tests", "types", "ontology"]
Verdict = Literal["confirmed", "refuted", "uncertain"]


class Finding(BaseModel, frozen=True):
    """One reviewed defect, anchored to a file (and optionally a line)."""

    file: str
    line: int | None = Field(default=None, gt=0)
    severity: Severity
    category: Category
    title: str
    evidence: str
    problem: str
    fix: str
    confidence: int = Field(ge=0, le=100)
    verdict: Verdict | None = None
    skeptic_note: str | None = None


class ReviewResult(BaseModel, frozen=True):
    """The full review: findings (empty = LGTM) plus a checked-aspects summary."""

    findings: list[Finding]
    summary: str
    parse_failed: bool = False


def extract_json(text: str) -> str:
    """Return the JSON payload from an LLM response, stripping markdown fences."""
    stripped = text.strip()
    if stripped.startswith("```"):
        newline = stripped.find("\n")
        stripped = stripped[newline + 1 :] if newline != -1 else ""
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_findings.py -v`
Expected: 11 passed

- [ ] **Step 5: Full gate + commit**

```bash
make format && make lint && make type-check && make pytest
git add src/cgis/guardian/findings.py tests/unit/test_guardian_findings.py
git commit -m "feat(guardian): structured findings contract — Finding/ReviewResult + extract_json"
```

---

### Task 2: Provider contract — `generate_structured` (base + gemini)

**Files:**
- Modify: `src/cgis/guardian/providers/base.py`
- Modify: `src/cgis/guardian/providers/gemini.py`
- Modify: `tests/unit/test_guardian_core.py` (fake providers gain the new method)
- Test: `tests/unit/test_guardian_core.py`

- [ ] **Step 1: Write the failing test** (append to the GeminiProvider section of `tests/unit/test_guardian_core.py`)

```python
async def test_gemini_generate_structured_sets_json_mode() -> None:
    """generate_structured passes response_mime_type + response_schema to the SDK."""
    from cgis.guardian.findings import ReviewResult

    mock_response = MagicMock()
    mock_response.text = '{"findings": [], "summary": "ok"}'
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    mock_genai = MagicMock()
    mock_genai.Client.return_value = mock_client
    mock_types = MagicMock()

    provider = GeminiProvider(api_key="fake")
    with patch.dict(
        "sys.modules",
        {
            "google": MagicMock(genai=mock_genai),
            "google.genai": mock_genai,
            "google.genai.types": mock_types,
        },
    ):
        result = await provider.generate_structured("sys", "user", ReviewResult)

    assert result == '{"findings": [], "summary": "ok"}'
    config_kwargs = mock_types.GenerateContentConfig.call_args.kwargs
    assert config_kwargs["response_mime_type"] == "application/json"
    assert config_kwargs["response_schema"] is ReviewResult
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_guardian_core.py::test_gemini_generate_structured_sets_json_mode -v`
Expected: FAIL — `AttributeError: ... has no attribute 'generate_structured'` (or TypeError on abstract instantiation once base.py changes land first; either failure is acceptable at this step)

- [ ] **Step 3: Add the abstract method to `base.py`** (append to `BaseProvider`)

```python
    @abc.abstractmethod
    async def generate_structured(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        """Send a prompt requesting JSON conforming to schema; return raw JSON text."""
```

`BaseModel` is already imported in base.py.

- [ ] **Step 4: Implement in `gemini.py`** — refactor to share transport. Replace the class body with:

```python
class GeminiProvider(BaseProvider):
    """Google Gemini provider. Requires: uv sync --group guardian"""

    def __init__(self, api_key: str, model_name: str = "gemini-2.5-flash") -> None:
        """Store credentials; google-genai is imported lazily at call time."""
        super().__init__()
        self._api_key = api_key
        self._model_name = model_name

    async def _generate(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel] | None
    ) -> str:
        """Shared transport: one generate_content call, optional JSON mode."""
        _install_hint = "google-genai is required. Install with: uv sync --group guardian"
        try:
            from google import genai  # noqa: PLC0415
            from google.genai import types  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(_install_hint) from exc
        config_kwargs: dict[str, object] = {"system_instruction": system_prompt}
        if schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = schema
        client = genai.Client(api_key=self._api_key)
        response = await client.aio.models.generate_content(
            model=self._model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        meta = getattr(response, "usage_metadata", None)
        if meta is not None:
            self.last_usage = ProviderUsage(
                prompt_tokens=getattr(meta, "prompt_token_count", 0),
                completion_tokens=getattr(meta, "candidates_token_count", 0),
            )
        return str(response.text)

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Send prompts to Gemini and return the text response."""
        return await self._generate(system_prompt, user_prompt, None)

    async def generate_structured(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        """Send prompts in native JSON mode constrained by schema."""
        return await self._generate(system_prompt, user_prompt, schema)
```

Add to gemini.py imports: `from pydantic import BaseModel`.

- [ ] **Step 5: Fix the now-broken fakes in `tests/unit/test_guardian_core.py`** — `generate_structured` is abstract, so every `BaseProvider` subclass in the test file needs it. Add to `_FakeProvider`, `_CapturingProvider`, and `_UsageProvider` (each delegates to its existing behaviour):

```python
    async def generate_structured(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        """Structured variant — same canned behaviour as generate_content."""
        return await self.generate_content(system_prompt, user_prompt)
```

Add `from pydantic import BaseModel` to the test file imports.

- [ ] **Step 6: Run the guardian test file**

Run: `uv run pytest tests/unit/test_guardian_core.py -v`
Expected: all pass, including the new test

- [ ] **Step 7: Full gate + commit**

```bash
make format && make lint && make type-check && make pytest
git add src/cgis/guardian/providers/base.py src/cgis/guardian/providers/gemini.py tests/unit/test_guardian_core.py
git commit -m "feat(guardian): generate_structured provider contract + gemini JSON mode"
```

---

### Task 3: Mistral `generate_structured`

**Files:**
- Modify: `src/cgis/guardian/providers/mistral.py`
- Test: `tests/unit/test_guardian_core.py`

- [ ] **Step 1: Write the failing test** (append to the MistralProvider section)

```python
async def test_mistral_generate_structured_sets_json_object() -> None:
    """generate_structured passes response_format=json_object to the SDK."""
    from cgis.guardian.findings import ReviewResult

    mock_choice = MagicMock()
    mock_choice.message.content = '{"findings": [], "summary": "ok"}'
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    inst = _make_mistral_client(mock_response)
    provider = MistralProvider(api_key="fake")
    with patch.dict("sys.modules", _mistral_modules(inst)):
        result = await provider.generate_structured("sys", "user", ReviewResult)

    assert result == '{"findings": [], "summary": "ok"}'
    call_kwargs = inst.chat.complete_async.call_args.kwargs
    assert call_kwargs["response_format"] == {"type": "json_object"}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_guardian_core.py::test_mistral_generate_structured_sets_json_object -v`
Expected: FAIL — abstract-method TypeError (MistralProvider lacks generate_structured)

- [ ] **Step 3: Implement** — same shared-transport refactor as gemini. Replace the class body of `mistral.py` with:

```python
class MistralProvider(BaseProvider):
    """Mistral AI provider. Requires: uv sync --group guardian"""

    def __init__(self, api_key: str, model_name: str = "mistral-medium-latest") -> None:
        """Store credentials; mistralai is imported lazily at call time."""
        super().__init__()
        self._api_key = api_key
        self._model_name = model_name

    async def _generate(self, system_prompt: str, user_prompt: str, *, json_mode: bool) -> str:
        """Shared transport: one chat.complete_async call, optional json_object mode."""
        _install_hint = "mistralai is required. Install with: uv sync --group guardian"
        try:
            from mistralai.client import Mistral  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(_install_hint) from exc
        extra: dict[str, object] = {}
        if json_mode:
            extra["response_format"] = {"type": "json_object"}
        async with Mistral(api_key=self._api_key) as client:
            response = await client.chat.complete_async(
                model=self._model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                **extra,
            )
        if not response.choices:
            _msg = f"Mistral returned no choices for model {self._model_name}"
            raise ValueError(_msg)
        content = response.choices[0].message.content
        if content is None:
            _msg = f"Mistral returned null message content for model {self._model_name}"
            raise ValueError(_msg)
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.last_usage = ProviderUsage(
                prompt_tokens=getattr(usage, "prompt_tokens", 0),
                completion_tokens=getattr(usage, "completion_tokens", 0),
            )
        return str(content)

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Send prompts to Mistral and return the text response."""
        return await self._generate(system_prompt, user_prompt, json_mode=False)

    async def generate_structured(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        """Send prompts in json_object mode.

        Mistral's json_object mode takes no schema parameter — the schema is
        described in the user prompt (spec §2.4); the argument exists to
        satisfy the BaseProvider contract.
        """
        del schema
        return await self._generate(system_prompt, user_prompt, json_mode=True)
```

Add `from pydantic import BaseModel` to mistral.py imports.

- [ ] **Step 4: Run the file, full gate, commit**

```bash
uv run pytest tests/unit/test_guardian_core.py -v
make format && make lint && make type-check && make pytest
git add src/cgis/guardian/providers/mistral.py tests/unit/test_guardian_core.py
git commit -m "feat(guardian): mistral json_object structured mode"
```

---

### Task 4: Prompt OUTPUT FORMAT → JSON schema

**Files:**
- Modify: `src/cgis/guardian/prompts.py`
- Test: `tests/unit/test_guardian_core.py`

- [ ] **Step 1: Write the failing test** (append near the existing prompt tests)

```python
def test_build_user_prompt_demands_json_output() -> None:
    """OUTPUT FORMAT section requests raw JSON matching the findings schema."""
    prompt = PromptBuilder.build_user_prompt({"diff": "d"})
    assert '"findings"' in prompt
    assert '"summary"' in prompt
    assert "ONLY a JSON object" in prompt
    assert "**[Logic Bug" not in prompt  # old markdown template is gone
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_guardian_core.py::test_build_user_prompt_demands_json_output -v`
Expected: FAIL — old markdown OUTPUT FORMAT still present

- [ ] **Step 3: Replace ONLY the OUTPUT FORMAT block** in `build_user_prompt` (everything from `### OUTPUT FORMAT:` to the end of the returned f-string). PRECISION RULES and WHAT TO LOOK FOR stay verbatim. New tail:

```python
### OUTPUT FORMAT:

Return ONLY a JSON object — no prose, no markdown fences — with this exact shape:

{{"findings": [{{"file": "src/path/to/file.py", "line": 123, "severity": "critical|major|minor", "category": "logic|contract|tests|types|ontology", "title": "short headline", "evidence": "<verbatim quote from the diff>", "problem": "one sentence.", "fix": "concrete suggestion.", "confidence": 85}}], "summary": "2-3 most important things you checked and found correct."}}

Rules:
- "category" maps to the focus areas: logic = Logic Bug, contract = Library Contract, tests = Test Coverage, types = Type Safety, ontology = Ontology.
- "line" is the line number in the HEAD version of the file, or null for file-level findings.
- "confidence" must be >= 80 to include a finding (the gate above).
- max 5 findings; fewer is fine; an empty list means LGTM.
- "summary" is mandatory; for an LGTM it lists what you checked and found correct.

Example LGTM response: {{"findings": [], "summary": "Checked the BFS batching for off-by-one and the Pydantic model bounds; both correct."}}"""
```

Note the doubled `{{` / `}}` — the prompt is an f-string.

- [ ] **Step 4: Run guardian tests, fix any prompt-content assertions that referenced the old template**

Run: `uv run pytest tests/unit/test_guardian_core.py -v`
Expected: all pass (adjust only assertions about OUTPUT FORMAT text if any break; section-presence tests are untouched)

- [ ] **Step 5: Full gate + commit**

```bash
make format && make lint && make type-check && make pytest
git add src/cgis/guardian/prompts.py tests/unit/test_guardian_core.py
git commit -m "feat(guardian): prompt requests structured JSON output"
```

---

### Task 5: `GuardianReviewer.run_review() -> ReviewResult` with retry/fallback

**Files:**
- Modify: `src/cgis/guardian/core.py`
- Modify: `tests/unit/test_guardian_core.py` (run_review tests)

- [ ] **Step 1: Write the failing tests** (replace `test_run_review_returns_provider_response`; keep `test_run_review_passes_context_to_prompt` but adapt its assertion as shown)

```python
_VALID_JSON = '{"findings": [], "summary": "all good"}'
_VALID_FINDING_JSON = (
    '{"findings": [{"file": "a.py", "line": 1, "severity": "major", "category": "logic",'
    ' "title": "t", "evidence": "e", "problem": "p", "fix": "f", "confidence": 90}],'
    ' "summary": "s"}'
)


class _SequenceProvider(BaseProvider):
    """Returns queued responses; records structured-call prompts."""

    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self._responses = list(responses)
        self.structured_prompts: list[str] = []

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        return self._responses.pop(0)

    async def generate_structured(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        self.structured_prompts.append(user_prompt)
        return self._responses.pop(0)


async def test_run_review_parses_valid_json(collector: ContextCollector) -> None:
    """A valid JSON response becomes a ReviewResult on the first pass."""
    reviewer = GuardianReviewer(
        provider=_SequenceProvider([_VALID_FINDING_JSON]), context_collector=collector
    )
    result = await reviewer.run_review()
    assert result.parse_failed is False
    assert len(result.findings) == 1
    assert result.findings[0].file == "a.py"


async def test_run_review_strips_markdown_fences(collector: ContextCollector) -> None:
    """A fenced ```json response still parses."""
    fenced = f"```json\n{_VALID_JSON}\n```"
    reviewer = GuardianReviewer(
        provider=_SequenceProvider([fenced]), context_collector=collector
    )
    result = await reviewer.run_review()
    assert result.summary == "all good"


async def test_run_review_retries_once_on_invalid_json(collector: ContextCollector) -> None:
    """First invalid response triggers one retry whose prompt cites the error."""
    provider = _SequenceProvider(["not json at all", _VALID_JSON])
    reviewer = GuardianReviewer(provider=provider, context_collector=collector)
    result = await reviewer.run_review()
    assert result.parse_failed is False
    assert len(provider.structured_prompts) == 2
    assert "failed validation" in provider.structured_prompts[1].lower()


async def test_run_review_falls_back_after_two_failures(collector: ContextCollector) -> None:
    """Two invalid responses → raw text in summary, parse_failed=True."""
    provider = _SequenceProvider(["garbage one", "garbage two"])
    reviewer = GuardianReviewer(provider=provider, context_collector=collector)
    result = await reviewer.run_review()
    assert result.parse_failed is True
    assert result.findings == []
    assert result.summary == "garbage two"
```

In `test_run_review_passes_context_to_prompt`: change `_CapturingProvider.generate_structured` to record the prompt (it already delegates to `generate_content`, which records — verify the assertion still inspects `provider.user_prompt`), and have it return `_VALID_JSON` so parsing succeeds.

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/unit/test_guardian_core.py -k run_review -v`
Expected: FAIL — run_review returns str and never calls generate_structured

- [ ] **Step 3: Implement** — replace `core.py` content:

```python
"""Main orchestrator that wires together collector, prompts, and LLM provider."""

import structlog
from pydantic import ValidationError

from cgis.guardian.collector import ContextCollector
from cgis.guardian.findings import ReviewResult, extract_json
from cgis.guardian.prompts import PromptBuilder
from cgis.guardian.providers.base import BaseProvider

log = structlog.getLogger(__name__)

_RETRY_SUFFIX = (
    "\n\n---\nYour previous response failed validation against the required JSON schema:\n"
    "{error}\n"
    "Respond again with ONLY the JSON object — no prose, no markdown fences."
)


class GuardianReviewer:
    """Orchestrates the entire review process."""

    def __init__(self, provider: BaseProvider, context_collector: ContextCollector) -> None:
        """Wire up the LLM provider, context collector, and prompt builder."""
        self.provider = provider
        self.context_collector = context_collector
        self.prompt_builder = PromptBuilder()

    async def run_review(self) -> ReviewResult:
        """Run the review and return structured findings.

        Parse policy (spec §2.3): one retry with the validation error appended;
        on a second failure the raw text becomes the summary with parse_failed=True.
        """
        context = self.context_collector.collect_all()
        system_prompt = self.prompt_builder.build_system_prompt()
        user_prompt = self.prompt_builder.build_user_prompt(context)
        raw = await self.provider.generate_structured(system_prompt, user_prompt, ReviewResult)
        try:
            return ReviewResult.model_validate_json(extract_json(raw))
        except ValidationError as exc:
            log.warning("Structured output failed validation; retrying once.")
            retry_prompt = user_prompt + _RETRY_SUFFIX.format(error=exc)
            raw = await self.provider.generate_structured(
                system_prompt, retry_prompt, ReviewResult
            )
            try:
                return ReviewResult.model_validate_json(extract_json(raw))
            except ValidationError:
                log.error("Structured output failed twice; falling back to raw text.")
                return ReviewResult(findings=[], summary=raw, parse_failed=True)
```

- [ ] **Step 4: Run, gate, commit**

```bash
uv run pytest tests/unit/test_guardian_core.py -v
make format && make lint && make type-check && make pytest
git add src/cgis/guardian/core.py tests/unit/test_guardian_core.py
git commit -m "feat(guardian): run_review returns ReviewResult with parse retry + fallback"
```

(`make pytest` will fail here if `scripts/guardian_review.py` typing breaks under mypy — it concatenates `review_result + footer`. mypy covers only `src/`, so the script keeps working untouched until Task 7; if the full suite has a test importing it, defer that fix to Task 7 and note it in the commit body.)

---

### Task 6: Markdown renderer

**Files:**
- Create: `src/cgis/guardian/render.py`
- Test: `tests/unit/test_guardian_render.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Golden tests for ReviewResult → markdown rendering (spec §2.5)."""

from cgis.guardian.findings import Finding, ReviewResult
from cgis.guardian.render import render_finding, render_report

_FINDING = Finding(
    file="src/cgis/cli.py",
    line=42,
    severity="major",
    category="logic",
    title="off-by-one in pagination",
    evidence="for i in range(n + 1):",
    problem="iterates one element past the end.",
    fix="use range(n).",
    confidence=85,
)


def test_render_finding_contains_all_fields() -> None:
    """Header keeps the **[Category] — title** shape; all fields present."""
    text = render_finding(_FINDING)
    assert text.startswith("**[Logic Bug] — off-by-one in pagination**")
    assert "🟠" in text
    assert "`src/cgis/cli.py:42`" in text
    assert "for i in range(n + 1):" in text
    assert "Confidence: 85%" in text
    assert "Fix: use range(n)." in text


def test_render_finding_file_level_without_line() -> None:
    """line=None renders the bare file path."""
    text = render_finding(_FINDING.model_copy(update={"line": None}))
    assert "`src/cgis/cli.py`" in text
    assert ":None" not in text


def test_render_finding_with_skeptic_verdict() -> None:
    """A confirmed verdict adds the Verified line (used from the multi-pass step)."""
    f = _FINDING.model_copy(update={"verdict": "confirmed", "skeptic_note": "reproduced"})
    text = render_finding(f)
    assert "Skeptic: confirmed — reproduced" in text


def test_render_report_lgtm() -> None:
    """Empty findings render the canonical LGTM line plus the summary."""
    text = render_report(ReviewResult(findings=[], summary="Checked A and B."))
    assert text.startswith("LGTM — no defects found in this diff.")
    assert "Checked A and B." in text


def test_render_report_parse_failed() -> None:
    """parse_failed renders the raw text with an explicit warning header."""
    text = render_report(ReviewResult(findings=[], summary="raw blob", parse_failed=True))
    assert text.startswith("⚠️ Guardian could not produce structured output")
    assert "raw blob" in text


def test_render_report_findings_and_summary() -> None:
    """Findings are joined and the summary lands in a trailing section."""
    text = render_report(ReviewResult(findings=[_FINDING], summary="checked X"))
    assert "**[Logic Bug] — off-by-one in pagination**" in text
    assert "**Summary:** checked X" in text
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_guardian_render.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement `render.py`**

```python
"""Pure rendering of ReviewResult into the PR-comment markdown (spec §2.5)."""

from cgis.guardian.findings import Finding, ReviewResult

_SEVERITY_MARKER = {"critical": "🔴", "major": "🟠", "minor": "🟡"}
_CATEGORY_LABEL = {
    "logic": "Logic Bug",
    "contract": "Library Contract",
    "tests": "Test Coverage",
    "types": "Type Safety",
    "ontology": "Ontology",
}


def render_finding(finding: Finding) -> str:
    """Render one finding in the **[Category] — title** block format."""
    location = f"`{finding.file}:{finding.line}`" if finding.line else f"`{finding.file}`"
    lines = [
        f"**[{_CATEGORY_LABEL[finding.category]}] — {finding.title}**",
        f"{_SEVERITY_MARKER[finding.severity]} {finding.severity} at {location}"
        f" · Confidence: {finding.confidence}%",
        f"Lines: `{finding.evidence}`",
        f"Problem: {finding.problem}",
        f"Fix: {finding.fix}",
    ]
    if finding.verdict is not None:
        note = f" — {finding.skeptic_note}" if finding.skeptic_note else ""
        lines.append(f"Skeptic: {finding.verdict}{note}")
    return "\n".join(lines)


def render_report(result: ReviewResult) -> str:
    """Render the full review; visually matches the pre-structured format."""
    if result.parse_failed:
        return (
            "⚠️ Guardian could not produce structured output; raw response below.\n\n"
            + result.summary
        )
    if not result.findings:
        return f"LGTM — no defects found in this diff.\n\n{result.summary}"
    blocks = [render_finding(f) for f in result.findings]
    return "\n\n".join(blocks) + f"\n\n---\n**Summary:** {result.summary}"
```

- [ ] **Step 4: Run, gate, commit**

```bash
uv run pytest tests/unit/test_guardian_render.py -v
make format && make lint && make type-check && make pytest
git add src/cgis/guardian/render.py tests/unit/test_guardian_render.py
git commit -m "feat(guardian): markdown renderer for structured findings"
```

---

### Task 7: Metrics + runner module + thin script

**Files:**
- Modify: `src/cgis/guardian/metrics.py`
- Create: `src/cgis/guardian/runner.py`
- Modify: `scripts/guardian_review.py`
- Modify: `tests/unit/test_guardian_metrics.py`
- Test: `tests/unit/test_guardian_runner.py`

- [ ] **Step 1: Write the failing metrics tests** — in `tests/unit/test_guardian_metrics.py`, delete tests of `_count_findings` (the regex dies) and add:

```python
def test_record_review_structured_fields(tmp_path: Path) -> None:
    """record_review takes structured counts and writes parse_failed."""
    path = tmp_path / "m.jsonl"
    record_review(
        model="test-model",
        pr=152,
        prompt_tokens=10,
        completion_tokens=5,
        findings_total=2,
        lgtm=False,
        parse_failed=False,
        metrics_path=path,
    )
    entry = json.loads(path.read_text().splitlines()[0])
    assert entry["findings_total"] == 2
    assert entry["lgtm"] is False
    assert entry["parse_failed"] is False


def test_record_review_parse_failed_flag(tmp_path: Path) -> None:
    """parse_failed=True is recorded so the benchmark can see degraded runs."""
    path = tmp_path / "m.jsonl"
    record_review(
        model="m",
        pr=None,
        prompt_tokens=0,
        completion_tokens=0,
        findings_total=0,
        lgtm=False,
        parse_failed=True,
        metrics_path=path,
    )
    assert json.loads(path.read_text())["parse_failed"] is True
```

Existing `record_review` callers in that test file: replace `review_text="..."` arguments with `findings_total=N, lgtm=<bool>, parse_failed=False` keeping the entry counts the old assertions expect.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_guardian_metrics.py -v`
Expected: FAIL — unexpected keyword `findings_total`

- [ ] **Step 3: Modify `metrics.py`** — delete `_FINDING_RE`, `_count_findings`, and the `re` import. New signature:

```python
def record_review(
    *,
    model: str,
    pr: int | None,
    prompt_tokens: int,
    completion_tokens: int,
    findings_total: int,
    lgtm: bool,
    parse_failed: bool = False,
    metrics_path: Path = _DEFAULT_METRICS_FILE,
) -> Path:
    """Append one review entry to the metrics JSONL file and return the path.

    Counts come from the structured ReviewResult — no text parsing.
    """
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "pr": pr,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "findings_total": findings_total,
        "findings_applied": None,
        "lgtm": lgtm,
        "parse_failed": parse_failed,
    }
    with metrics_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return metrics_path
```

`rate_review` / `load_reviews` are untouched.

- [ ] **Step 4: Write the failing runner tests** — `tests/unit/test_guardian_runner.py`:

```python
"""Tests for the guardian script runner (provider selection + orchestration)."""

from pathlib import Path

import pytest
from pydantic import BaseModel

from cgis.guardian.collector import ContextCollector
from cgis.guardian.providers.base import BaseProvider
from cgis.guardian.runner import build_footer, build_provider, run_guardian

_VALID_JSON = '{"findings": [], "summary": "all good"}'


class _FakeProvider(BaseProvider):
    """Returns canned structured JSON."""

    def __init__(self, response: str = _VALID_JSON) -> None:
        super().__init__()
        self._response = response

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        return self._response

    async def generate_structured(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        return self._response


def test_build_provider_requires_a_key() -> None:
    """No API keys in env → RuntimeError with guidance."""
    with pytest.raises(RuntimeError, match="MISTRAL_API_KEY or GEMINI_API_KEY"):
        build_provider({})


def test_build_provider_prefers_explicit_provider() -> None:
    """GUARDIAN_PROVIDER=mistral wins even when both keys are present."""
    provider, model = build_provider(
        {"GUARDIAN_PROVIDER": "mistral", "MISTRAL_API_KEY": "k", "GEMINI_API_KEY": "g"}
    )
    assert model == "mistral-medium-latest"


def test_build_provider_model_override() -> None:
    """GUARDIAN_MODEL overrides the per-provider default."""
    _, model = build_provider({"GEMINI_API_KEY": "g", "GUARDIAN_MODEL": "gemini-x"})
    assert model == "gemini-x"


def test_build_footer_includes_model_and_tokens() -> None:
    """Footer lists model, token counts, and graph coverage."""
    from cgis.guardian.providers.base import ProviderUsage

    footer = build_footer(
        model="m1",
        usage=ProviderUsage(prompt_tokens=10, completion_tokens=2),
        stats={"total": 4, "with_graph": 2},
    )
    assert "m1" in footer
    assert "12" in footer
    assert "2/4" in footer


async def test_run_guardian_smoke(tmp_path: Path) -> None:
    """End-to-end with a fake provider: review → render → metrics line."""
    metrics = tmp_path / "m.jsonl"
    collector = ContextCollector(project_root=tmp_path)
    report = await run_guardian(
        provider=_FakeProvider(),
        model="fake-model",
        collector=collector,
        pr=152,
        metrics_path=metrics,
    )
    assert report.startswith("LGTM — no defects found in this diff.")
    assert metrics.exists()
```

- [ ] **Step 5: Run to verify failure**

Run: `uv run pytest tests/unit/test_guardian_runner.py -v`
Expected: FAIL — `cgis.guardian.runner` not found

- [ ] **Step 6: Create `src/cgis/guardian/runner.py`** (logic moves out of the script so it is importable and testable):

```python
"""Testable orchestration for the guardian review script."""

from collections.abc import Mapping
from pathlib import Path

import structlog

from cgis.guardian.collector import ContextCollector
from cgis.guardian.core import GuardianReviewer
from cgis.guardian.metrics import record_review
from cgis.guardian.providers.base import BaseProvider, ProviderUsage
from cgis.guardian.providers.gemini import GeminiProvider
from cgis.guardian.providers.mistral import MistralProvider
from cgis.guardian.render import render_report

log = structlog.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_MISTRAL_MODEL = "mistral-medium-latest"


def build_provider(env: Mapping[str, str]) -> tuple[BaseProvider, str]:
    """Return (provider, model_name) from GUARDIAN_PROVIDER / available API keys."""
    model_override = env.get("GUARDIAN_MODEL")
    provider_name = env.get("GUARDIAN_PROVIDER", "").lower()

    if provider_name == "mistral" or (not provider_name and env.get("MISTRAL_API_KEY")):
        mistral_key = env.get("MISTRAL_API_KEY")
        if not mistral_key:
            _msg = "MISTRAL_API_KEY must be set when GUARDIAN_PROVIDER=mistral"
            raise RuntimeError(_msg)
        model = model_override or DEFAULT_MISTRAL_MODEL
        return MistralProvider(api_key=mistral_key, model_name=model), model

    gemini_key = env.get("GEMINI_API_KEY")
    if gemini_key:
        model = model_override or DEFAULT_GEMINI_MODEL
        return GeminiProvider(api_key=gemini_key, model_name=model), model

    _msg = "Set MISTRAL_API_KEY or GEMINI_API_KEY to run Guardian."
    raise RuntimeError(_msg)


def build_footer(*, model: str, usage: ProviderUsage, stats: dict[str, int]) -> str:
    """Build the markdown footer with model, token usage, and graph coverage."""
    parts = [f"🤖 **{model}**"]
    if usage.total_tokens > 0:
        parts.append(
            f"{usage.prompt_tokens:,} prompt + {usage.completion_tokens:,} completion"
            f" = **{usage.total_tokens:,} tokens**"
        )
    if stats.get("total", 0) > 0:
        pct = round(stats["with_graph"] / stats["total"] * 100)
        parts.append(f"graph {stats['with_graph']}/{stats['total']} files ({pct}%)")
    return "\n\n---\n> " + " · ".join(parts)


async def run_guardian(
    *,
    provider: BaseProvider,
    model: str,
    collector: ContextCollector,
    pr: int | None,
    metrics_path: Path,
) -> str:
    """Run the review, record metrics, and return the rendered report + footer."""
    reviewer = GuardianReviewer(provider=provider, context_collector=collector)
    result = await reviewer.run_review()
    report = render_report(result)

    record_review(
        model=model,
        pr=pr,
        prompt_tokens=provider.last_usage.prompt_tokens,
        completion_tokens=provider.last_usage.completion_tokens,
        findings_total=len(result.findings),
        lgtm=not result.findings and not result.parse_failed,
        parse_failed=result.parse_failed,
        metrics_path=metrics_path,
    )
    return report + build_footer(
        model=model, usage=provider.last_usage, stats=collector.graph_stats
    )
```

- [ ] **Step 7: Slim down `scripts/guardian_review.py`** — keep argparse + I/O only:

```python
"""CLI entry point for running a Guardian AI code review."""

import argparse
import asyncio
import os
from pathlib import Path

import structlog

from cgis.guardian.collector import ContextCollector
from cgis.guardian.runner import build_provider, run_guardian

log = structlog.getLogger(__name__)


async def main() -> None:
    """Run Guardian review and write the result to stdout or a file."""
    parser = argparse.ArgumentParser(description="Run CGIS Guardian AI code review.")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--pr", type=int, default=None)
    parser.add_argument("--metrics", type=Path, default=Path("guardian_metrics.jsonl"))
    parser.add_argument("--base-branch", default="main")
    args = parser.parse_args()

    provider, model = build_provider(os.environ)
    project_root = Path(__file__).parent.parent.absolute()
    collector = ContextCollector(
        project_root=project_root, db_path=args.db, base_branch=args.base_branch
    )
    log.info("Running guardian review...", model=model)
    report = await run_guardian(
        provider=provider,
        model=model,
        collector=collector,
        pr=args.pr,
        metrics_path=args.metrics,
    )

    if args.output:
        safe_root = Path.cwd().resolve()
        output_path = (safe_root / args.output).resolve()
        if not output_path.is_relative_to(safe_root):
            _msg = f"--output must be within the working directory: {output_path}"
            raise ValueError(_msg)
        output_path.write_text(report)
        log.info("Review written to file.", path=str(output_path))
    else:
        print(report)


if __name__ == "__main__":
    asyncio.run(main())
```

(Keep the original `help=` strings on the arguments — abbreviated here for plan brevity ONLY in this listing; copy them from the current file.)

- [ ] **Step 8: Run everything, gate, commit**

```bash
uv run pytest tests/unit/test_guardian_metrics.py tests/unit/test_guardian_runner.py -v
make format && make lint && make type-check && make pytest
git add src/cgis/guardian/metrics.py src/cgis/guardian/runner.py scripts/guardian_review.py \
        tests/unit/test_guardian_metrics.py tests/unit/test_guardian_runner.py
git commit -m "feat(guardian): structured metrics + testable runner module, slim script"
```

---

### Task 8: Bench matching + scoring (pure logic)

**Files:**
- Create: `src/cgis/guardian/bench.py`
- Test: `tests/unit/test_guardian_bench.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for benchmark ground-truth matching and scoring (spec §3.2–3.3)."""

from pathlib import Path

from cgis.guardian.bench import (
    GroundTruth,
    load_ground_truth,
    match_findings,
    score,
)
from cgis.guardian.findings import Finding

_TRUTH = GroundTruth.model_validate(
    {
        "pr": 144,
        "base": "aaa",
        "head": "bbb",
        "findings": [
            {
                "id": "float-eq",
                "file": "tests/unit/test_quotient.py",
                "lines": [60, 75],
                "severity": "major",
                "category": "tests",
                "summary": "float ==",
                "source": "sonar",
            },
            {
                "id": "no-lines",
                "file": "src/cgis/query/drift.py",
                "severity": "minor",
                "category": "types",
                "summary": "anywhere in file",
                "source": "gemini",
            },
        ],
        "ambiguous": [{"file": "src/cgis/query/triads.py", "summary": "clip debate"}],
    }
)


def _pred(file: str, line: int | None, confidence: int = 90) -> Finding:
    return Finding(
        file=file,
        line=line,
        severity="major",
        category="logic",
        title="t",
        evidence="e",
        problem="p",
        fix="f",
        confidence=confidence,
    )


def test_match_in_line_range() -> None:
    """Same file + line inside [lo, hi] matches; category is NOT required."""
    m = match_findings([_pred("tests/unit/test_quotient.py", 68)], _TRUTH)
    assert m.matched == {"float-eq": 0}
    assert m.noise == []


def test_match_outside_line_range_is_noise() -> None:
    """Same file but line outside the range → noise."""
    m = match_findings([_pred("tests/unit/test_quotient.py", 10)], _TRUTH)
    assert m.matched == {}
    assert m.noise == [0]


def test_match_entry_without_lines_accepts_any_line() -> None:
    """A GT entry without lines matches any line (and None) in that file."""
    assert match_findings([_pred("src/cgis/query/drift.py", 999)], _TRUTH).matched
    assert match_findings([_pred("src/cgis/query/drift.py", None)], _TRUTH).matched


def test_ambiguous_is_neither_match_nor_noise() -> None:
    """Predictions on ambiguous files are tracked separately."""
    m = match_findings([_pred("src/cgis/query/triads.py", 5)], _TRUTH)
    assert m.matched == {}
    assert m.noise == []
    assert m.ambiguous_hits == [0]


def test_each_entry_matches_once_greedy_by_confidence() -> None:
    """Two predictions on one entry: the higher-confidence one wins, other is noise."""
    preds = [
        _pred("tests/unit/test_quotient.py", 61, confidence=80),
        _pred("tests/unit/test_quotient.py", 62, confidence=95),
    ]
    m = match_findings(preds, _TRUTH)
    assert m.matched == {"float-eq": 1}
    assert m.noise == [0]


def test_score_metrics() -> None:
    """recall = matched/GT, precision = matched/preds, noise = count."""
    preds = [
        _pred("tests/unit/test_quotient.py", 68),
        _pred("src/cgis/other.py", 1),
    ]
    m = match_findings(preds, _TRUTH)
    s = score(m, _TRUTH, total_predictions=len(preds))
    assert s.recall == 0.5
    assert s.precision == 0.5
    assert s.noise == 1
    assert s.missed == ["no-lines"]


def test_score_empty_ground_truth_perfect_recall() -> None:
    """No GT entries → recall 1.0 (a clean PR replayed with zero findings)."""
    truth = GroundTruth(pr=1, base="a", head="b", findings=[], ambiguous=[])
    s = score(match_findings([], truth), truth, total_predictions=0)
    assert s.recall == 1.0
    assert s.precision == 1.0


def test_load_ground_truth_yaml(tmp_path: Path) -> None:
    """YAML file loads into the GroundTruth model."""
    p = tmp_path / "pr-1.yaml"
    p.write_text(
        "pr: 1\nbase: aaa\nhead: bbb\n"
        "findings:\n"
        "  - id: x\n    file: f.py\n    lines: [1, 2]\n    severity: major\n"
        "    category: logic\n    summary: s\n    source: human\n"
    )
    gt = load_ground_truth(p)
    assert gt.pr == 1
    assert gt.findings[0].lines == (1, 2)
    assert gt.ambiguous == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_guardian_bench.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement `bench.py`**

```python
"""Benchmark ground truth, matching, and scoring (spec §3.1–3.3).

Matching is deterministic and pure so the scorer can be unit-tested without
any LLM: a prediction matches a ground-truth entry iff same file AND (line
within the entry's range, or the entry has no range). Greedy by descending
prediction confidence; each entry matches at most once.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from cgis.guardian.findings import Category, Finding, Severity


class GroundTruthEntry(BaseModel, frozen=True):
    """One curated real finding for a benchmark PR."""

    id: str
    file: str
    lines: tuple[int, int] | None = None
    severity: Severity
    category: Category
    summary: str
    source: Literal["gemini", "sonar", "fix-commit", "human"]


class AmbiguousEntry(BaseModel, frozen=True):
    """A debatable suggestion — neither a miss nor noise (spec §3.1)."""

    file: str
    summary: str


class GroundTruth(BaseModel, frozen=True):
    """Curated findings for one merged PR."""

    pr: int
    base: str
    head: str
    findings: list[GroundTruthEntry]
    ambiguous: list[AmbiguousEntry] = Field(default_factory=list)


class MatchResult(BaseModel, frozen=True):
    """Outcome of matching predictions against one PR's ground truth."""

    matched: dict[str, int]  # ground-truth id -> prediction index
    missed: list[str]  # ground-truth ids with no match
    noise: list[int]  # prediction indices matching nothing
    ambiguous_hits: list[int]  # prediction indices on ambiguous files


class BenchScore(BaseModel, frozen=True):
    """Per-PR metrics derived from a MatchResult (spec §3.3)."""

    recall: float
    precision: float
    noise: int
    missed: list[str]


def load_ground_truth(path: Path) -> GroundTruth:
    """Load and validate one benchmarks/guardian/pr-N.yaml file."""
    return GroundTruth.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _entry_accepts(entry: GroundTruthEntry, prediction: Finding) -> bool:
    """File must match; a lines range additionally requires the line within it."""
    if entry.file != prediction.file:
        return False
    if entry.lines is None:
        return True
    return prediction.line is not None and entry.lines[0] <= prediction.line <= entry.lines[1]


def match_findings(predictions: Sequence[Finding], truth: GroundTruth) -> MatchResult:
    """Match predictions to ground truth: greedy by descending confidence."""
    matched: dict[str, int] = {}
    noise: list[int] = []
    ambiguous_hits: list[int] = []
    ambiguous_files = {a.file for a in truth.ambiguous}
    order = sorted(range(len(predictions)), key=lambda i: -predictions[i].confidence)
    for i in order:
        prediction = predictions[i]
        hit = next(
            (e.id for e in truth.findings if e.id not in matched and _entry_accepts(e, prediction)),
            None,
        )
        if hit is not None:
            matched[hit] = i
        elif prediction.file in ambiguous_files:
            ambiguous_hits.append(i)
        else:
            noise.append(i)
    missed = [e.id for e in truth.findings if e.id not in matched]
    return MatchResult(
        matched=matched,
        missed=missed,
        noise=sorted(noise),
        ambiguous_hits=sorted(ambiguous_hits),
    )


def score(match: MatchResult, truth: GroundTruth, *, total_predictions: int) -> BenchScore:
    """Compute recall / precision / noise for one PR."""
    gt_total = len(truth.findings)
    recall = len(match.matched) / gt_total if gt_total else 1.0
    precision = len(match.matched) / total_predictions if total_predictions else 1.0
    return BenchScore(
        recall=recall, precision=precision, noise=len(match.noise), missed=match.missed
    )
```

- [ ] **Step 4: Run, gate, commit**

```bash
uv run pytest tests/unit/test_guardian_bench.py -v
make format && make lint && make type-check && make pytest
git add src/cgis/guardian/bench.py tests/unit/test_guardian_bench.py
git commit -m "feat(guardian): benchmark ground-truth models, matcher, scorer"
```

---

### Task 9: Collector `base_ref` + bench replay runner script

**Files:**
- Modify: `src/cgis/guardian/collector.py:19-58`
- Create: `scripts/guardian_bench.py`
- Test: `tests/unit/test_guardian_collector.py`

- [ ] **Step 1: Write the failing collector test** (append to `tests/unit/test_guardian_collector.py`; mirror the existing tmp-git-repo fixtures in that file — it already builds repos for diff tests)

```python
def test_base_ref_overrides_origin_prefix(tmp_path: Path) -> None:
    """base_ref diffs <ref>...HEAD instead of origin/<branch>...HEAD."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "one"], cwd=tmp_path, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout.strip()
    (tmp_path / "a.py").write_text("x = 2\n")
    subprocess.run(["git", "commit", "-aqm", "two"], cwd=tmp_path, check=True)

    collector = ContextCollector(project_root=tmp_path, base_ref=base_sha)
    diff = collector.get_git_diff()
    assert "x = 2" in diff
    assert collector.get_changed_py_files() == ["a.py"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_guardian_collector.py::test_base_ref_overrides_origin_prefix -v`
Expected: FAIL — unexpected keyword `base_ref`

- [ ] **Step 3: Modify `collector.py`** — add the parameter and a shared range helper:

```python
    def __init__(
        self,
        project_root: Path,
        base_branch: str = "main",
        db_path: Path | None = None,
        base_ref: str | None = None,
    ) -> None:
        """Set project root, diff base (branch or explicit ref), and optional graph DB.

        base_ref, when given, is used verbatim (e.g. a SHA for benchmark
        replays); otherwise the diff base is origin/<base_branch>.
        """
        self.project_root = project_root
        self.base_branch = base_branch
        self.db_path = db_path
        self.base_ref = base_ref
        self.graph_stats: dict[str, int] = {"total": 0, "with_graph": 0}

    def _diff_range(self) -> str:
        """Return the git range argument for diff commands."""
        base = self.base_ref or f"origin/{self.base_branch}"
        return f"{base}...HEAD"
```

In `get_git_diff` and `get_changed_py_files`, replace `f"origin/{self.base_branch}...HEAD"` with `self._diff_range()`.

- [ ] **Step 4: Create `scripts/guardian_bench.py`**

```python
"""Replay guardian on past PRs and score against curated ground truth (spec §3.4).

Usage:
    uv run python scripts/guardian_bench.py            # all benchmarks/guardian/pr-*.yaml
    uv run python scripts/guardian_bench.py --pr 144 --runs 3

Requires: full git history (refuses shallow clones), one provider API key.
Appends one JSON line per (pr, run) to benchmarks/guardian/results.jsonl.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import structlog

from cgis.guardian.bench import GroundTruth, load_ground_truth, match_findings, score
from cgis.guardian.collector import ContextCollector
from cgis.guardian.core import GuardianReviewer
from cgis.guardian.runner import build_provider

log = structlog.getLogger(__name__)

_REPO_ROOT = Path(__file__).parent.parent.absolute()
_BENCH_DIR = _REPO_ROOT / "benchmarks" / "guardian"


def _git(*args: str, cwd: Path = _REPO_ROOT) -> str:
    """Run a git command, return stdout, raise on failure."""
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _ensure_full_history() -> None:
    """Refuse to run in a shallow clone — the merge base would be missing."""
    if _git("rev-parse", "--is-shallow-repository") == "true":
        sys.exit("guardian_bench requires full git history; run: git fetch --unshallow")


async def _run_one(truth: GroundTruth, run_idx: int, results_path: Path) -> None:
    """Replay one PR once: worktree → ingest → review → score → JSONL line."""
    provider, model = build_provider(os.environ)
    _git("fetch", "origin", f"pull/{truth.pr}/head")
    with tempfile.TemporaryDirectory(prefix=f"bench-pr{truth.pr}-") as tmp:
        worktree = Path(tmp) / "wt"
        _git("worktree", "add", "--detach", str(worktree), truth.head)
        try:
            subprocess.run(
                ["uv", "run", "cgis", "ingest", "src", "--output", "graph.db"],
                cwd=worktree,
                check=True,
                capture_output=True,
            )
            collector = ContextCollector(
                project_root=worktree,
                db_path=worktree / "graph.db",
                base_ref=truth.base,
            )
            reviewer = GuardianReviewer(provider=provider, context_collector=collector)
            result = await reviewer.run_review()
        finally:
            _git("worktree", "remove", "--force", str(worktree))

    matches = match_findings(result.findings, truth)
    bench_score = score(matches, truth, total_predictions=len(result.findings))
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "pr": truth.pr,
        "run": run_idx,
        "model": model,
        "guardian_sha": _git("rev-parse", "HEAD"),
        "features": os.environ.get("GUARDIAN_FEATURES", ""),
        "parse_failed": result.parse_failed,
        "recall": bench_score.recall,
        "precision": bench_score.precision,
        "noise": bench_score.noise,
        "matched": matches.matched,
        "missed": matches.missed,
        "ambiguous_hits": matches.ambiguous_hits,
        "prompt_tokens": provider.last_usage.prompt_tokens,
        "completion_tokens": provider.last_usage.completion_tokens,
        "findings": [f.model_dump() for f in result.findings],
    }
    with results_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    log.info(
        "Scored.",
        pr=truth.pr,
        run=run_idx,
        recall=bench_score.recall,
        noise=bench_score.noise,
    )


async def main() -> None:
    """Run the benchmark over selected PRs, isolating per-PR failures."""
    parser = argparse.ArgumentParser(description="Replay guardian on past PRs and score.")
    parser.add_argument("--pr", type=int, action="append", default=None,
                        help="PR number(s) to run; default: every pr-*.yaml")
    parser.add_argument("--runs", type=int, default=1, help="Repetitions per PR (default 1).")
    parser.add_argument("--results", type=Path, default=_BENCH_DIR / "results.jsonl")
    args = parser.parse_args()

    _ensure_full_history()
    paths = sorted(_BENCH_DIR.glob("pr-*.yaml"))
    truths = [load_ground_truth(p) for p in paths]
    if args.pr:
        truths = [t for t in truths if t.pr in set(args.pr)]
    if not truths:
        sys.exit("No ground-truth files selected.")

    failures = 0
    for truth in truths:
        for run_idx in range(args.runs):
            try:
                await _run_one(truth, run_idx, args.results)
            except Exception as exc:  # noqa: BLE001 — isolate per-PR failures (spec §7)
                failures += 1
                log.error("PR replay failed.", pr=truth.pr, run=run_idx, error=str(exc))
                with args.results.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "timestamp": datetime.now(UTC).isoformat(),
                        "pr": truth.pr,
                        "run": run_idx,
                        "error": str(exc),
                    }) + "\n")
    if failures:
        log.warning("Some replays failed.", failures=failures)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: Sanity-check the script parses and the collector change passes**

```bash
uv run python -c "import ast; ast.parse(open('scripts/guardian_bench.py').read())"
uv run pytest tests/unit/test_guardian_collector.py -v
```
Expected: no output from the parse; collector tests pass

- [ ] **Step 6: Full gate + commit**

```bash
make format && make lint && make type-check && make pytest
git add src/cgis/guardian/collector.py scripts/guardian_bench.py tests/unit/test_guardian_collector.py
git commit -m "feat(guardian): bench replay runner + collector base_ref override"
```

---

### Task 10: Ground-truth curation (CONTROLLER TASK — not for implementation subagents)

**Files:**
- Create: `benchmarks/guardian/pr-122.yaml`, `pr-140.yaml`, `pr-141.yaml`, `pr-142.yaml`, `pr-143.yaml`, `pr-144.yaml`

This task is research/curation, not code — the session controller (with the user) executes it. For each PR:

- [ ] **Step 1: Record base/head SHAs**

```bash
N=144  # repeat per PR
git fetch origin pull/$N/head
gh pr view $N --json baseRefOid,headRefOid,mergeCommit --jq '{base: .baseRefOid, head: .headRefOid}'
git merge-base $(gh pr view $N --json baseRefOid --jq .baseRefOid) FETCH_HEAD
```

Use the merge-base output as `base`, `headRefOid` as `head`.

- [ ] **Step 2: Mine the three sources per PR**

```bash
# gemini inline threads (accepted ones become findings; declined-with-reasons → ambiguous)
gh api repos/zaebee/codegraph-brain/pulls/$N/comments --paginate \
  --jq '.[] | select(.user.login | startswith("gemini")) | {id, path, line: .original_line, body: .body[0:300]}'
# review-fix commits (every fix commit implies a real finding)
gh pr view $N --json commits --jq '.commits[] | select(.messageHeadline | test("fix|review")) | .messageHeadline'
# Sonar issues are in the PR checks / sonarcloud UI — cross-check the PR conversation
gh pr view $N --json comments --jq '.comments[] | select(.author.login | test("sonar")) | .body[0:300]'
```

- [ ] **Step 3: Write the YAML** following the spec §3.1 format exactly (id, file, lines range in HEAD coordinates, severity, category, summary, source; debatable items under `ambiguous`). Known seeds from project history: PR #144 → float-equality asserts (sonar, tests), cognitive-complexity split in triads (sonar), YAML mapping guards (gemini, types); PR #144 ambiguous → clip-to-[0,1] on drift. PR #143 → `_params_mapping` list guard (gemini), `_selected_domains` by profile field (gemini), CC extractions (sonar). PR #122 → the defect gemini caught that guardian missed (mine the thread for file/line).

- [ ] **Step 4: Validate every file loads**

```bash
uv run python -c "
from pathlib import Path
from cgis.guardian.bench import load_ground_truth
for p in sorted(Path('benchmarks/guardian').glob('pr-*.yaml')):
    gt = load_ground_truth(p)
    print(p.name, gt.pr, len(gt.findings), 'findings')
"
```
Expected: six lines, no validation errors

- [ ] **Step 5: Commit**

```bash
git add benchmarks/guardian/
git commit -m "feat(guardian): curated ground truth for 6 benchmark PRs"
```

---

### Task 11: Baseline measurement (CONTROLLER TASK — needs API keys, costs money)

- [ ] **Step 1: Gemini baseline, 3 runs per PR**

```bash
GEMINI_API_KEY=... uv run python scripts/guardian_bench.py --runs 3
```

- [ ] **Step 2: Mistral baseline, 3 runs per PR**

```bash
GUARDIAN_PROVIDER=mistral MISTRAL_API_KEY=... uv run python scripts/guardian_bench.py --runs 3
```

- [ ] **Step 3: Summarize** — aggregate recall / noise / precision per provider (mean over runs, then over PRs); note run-to-run variance; flag any `parse_failed` or `error` lines. Record the summary table in the PR description.

- [ ] **Step 4: Commit the results**

```bash
git add benchmarks/guardian/results.jsonl
git commit -m "feat(guardian): baseline benchmark results (gemini + mistral, N=3)"
```

- [ ] **Step 5: Finish the branch** — push, open PR titled `feat(guardian): structured findings + benchmark harness + baseline`, request review, report baseline numbers to the user. The NEXT plan (context upgrades, skeptic, inline) is written only after these numbers are in.

---

## Self-Review (completed)

- **Spec coverage:** §2.1 models → Task 1; §2.2 provider contract → Tasks 2–3; §2.3 parse policy → Task 5; §2.4 prompt → Task 4; §2.5 render + breaking change → Tasks 6–7; §3.1 ground truth → Tasks 8, 10; §3.2 matching → Task 8; §3.3 metrics → Task 8; §3.4 runner incl. shallow-clone guard and per-PR error isolation → Task 9; §3.5 baseline → Task 11. §§4–6 deliberately deferred to the next plan.
- **Placeholder scan:** one intentional abbreviation flagged inline (Task 7 Step 7 argparse `help=` strings — copy from the current file, explicitly instructed).
- **Type consistency:** `generate_structured(self, system_prompt, user_prompt, schema: type[BaseModel]) -> str` identical across base/gemini/mistral/fakes; `ReviewResult`/`Finding` field names consistent across findings/render/bench/runner; `score(..., *, total_predictions)` keyword-only everywhere it is called.
