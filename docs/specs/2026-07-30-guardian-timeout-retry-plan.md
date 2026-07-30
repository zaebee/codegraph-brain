# Guardian Timeout + Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every Guardian LLM provider an explicit request timeout and one bounded retry with backoff, and record how long a review took, so a single slow API window can no longer destroy a whole run.

**Architecture:** One shared retry helper on `BaseProvider` wraps each provider's public `generate_content` / `generate_structured`, retrying the same httpx exception set the Mistral SDK itself retries. Timeouts stay vendor-native because only the SDK's own knob changes the socket timeout — Mistral and Gemini take **milliseconds**, Ollama takes **seconds**. A `duration_s` field lands in the metrics record so the next timeout value can be measured instead of judged.

**Tech Stack:** Python 3.12+, `httpx` (already installed transitively via `mcp[cli]`), `mistralai`, `google-genai`, `ollama` (all in the `guardian` dependency group), pytest + `pytest-asyncio` (asyncio mode is AUTO — async tests need no decorator).

**Spec:** `docs/specs/2026-07-30-guardian-timeout-retry-design.md`
**Issue:** #275

## Global Constraints

- **MyPy strict** (`make type-check` runs `mypy src`). Full annotations including return types.
- **Ruff** full rule set, line length **100**, double quotes. Note `PLC0415` (no function-level imports) is ON — but the providers deliberately import their SDKs lazily inside `_generate` with `# noqa: PLC0415`; follow that existing pattern, do not "fix" it.
- **Docstring coverage ≥ 90%** (`uv run interrogate src`).
- **No behaviour change to review content.** This is a robustness fix. Findings, prompts, context assembly, and metrics semantics stay identical apart from the one added field.
- **Do not touch chunk routing** (`chunked.py`). That is #277 and explicitly out of scope.
- **Ollama's 600 s timeout stays as-is** — its comment documents cold-GPU weight loading, a different problem.
- **Vendor retry stays off.** Do not pass `RetryConfig` to Mistral or `retry_options` to Gemini. One retry mechanism only.
- **Full verification before every commit:** `make format && make lint && make type-check && make pytest && make doc-coverage`.
- Branch `fix/275-guardian-chunk-retry`, worktree `.claude/worktrees/guardian-chunk`.

## File Structure

| File | Responsibility |
|------|----------------|
| `src/cgis/guardian/providers/base.py` (modify) | `DEFAULT_REQUEST_TIMEOUT`, `MAX_ATTEMPTS`, `RETRYABLE_EXCEPTIONS`, `BaseProvider._retry` |
| `tests/unit/test_guardian_providers.py` (create) | Retry semantics + the milliseconds-vs-seconds regression test |
| `src/cgis/guardian/providers/mistral.py` (modify) | Accept `timeout`, pass `timeout_ms`, wrap public methods |
| `src/cgis/guardian/providers/gemini.py` (modify) | Accept `timeout`, pass `HttpOptions(timeout=...)`, wrap public methods |
| `src/cgis/guardian/providers/ollama.py` (modify) | Wrap public methods; timeout untouched |
| `src/cgis/guardian/metrics.py` (modify) | `duration_s` on `record_review` |
| `src/cgis/guardian/runner.py` (modify) | Measure elapsed around `run_review_routed` |
| `pyproject.toml` (modify) | Declare `httpx` in the `guardian` group |

Task 1 builds the shared machinery and its tests. Tasks 2–4 apply it per provider (each independently reviewable). Task 5 is the metrics field.

---

### Task 1: Shared retry helper on BaseProvider

**Files:**
- Modify: `src/cgis/guardian/providers/base.py`
- Modify: `pyproject.toml`
- Test: `tests/unit/test_guardian_providers.py` (create)

**Interfaces:**
- Produces:
  - `DEFAULT_REQUEST_TIMEOUT: float` — seconds, `180.0`
  - `MAX_ATTEMPTS: int` — `3`
  - `RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...]` — `(httpx.TimeoutException, httpx.NetworkError)`
  - `BaseProvider._retry(self, call: Callable[[], Awaitable[str]]) -> str`
  - `BaseProvider._sleep(self, seconds: float) -> None` — an overridable seam so tests do not wait

- [ ] **Step 1: Declare httpx in the guardian dependency group**

In `pyproject.toml`, find the `guardian = [` group and add `httpx`:

```toml
guardian = [
    "google-genai>=0.8.0",
    "httpx>=0.27",
    "mistralai>=1.0.0",
    "cohere>=5.0.0",
    "ollama>=0.3.0",
]
```

It is already present transitively through `mcp[cli]`, but `base.py` now imports it directly and only guardian code does — so that is where it belongs. Then run `uv sync --group guardian` to refresh the lock.

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/test_guardian_providers.py`:

```python
"""Unit tests for provider retry semantics and timeout wiring (#275)."""

import httpx
import pytest
from pydantic import BaseModel

from cgis.guardian.providers.base import DEFAULT_REQUEST_TIMEOUT, MAX_ATTEMPTS, BaseProvider


class _Recorder(BaseProvider):
    """A provider whose transport is scripted by the test.

    Distinct from guardian_stubs.StubProvider, which returns canned JSON: this
    one scripts per-call outcomes and spies on the backoff.
    """

    def __init__(self, outcomes: list[object]) -> None:
        """Store the scripted per-call outcomes: an exception to raise or a value."""
        super().__init__()
        self.outcomes = outcomes
        self.calls = 0
        self.slept: list[float] = []

    async def _transport(self) -> str:
        """Return or raise the next scripted outcome."""
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return str(outcome)

    async def _sleep(self, seconds: float) -> None:
        """Record the backoff instead of waiting."""
        self.slept.append(seconds)

    async def generate_content(
        self,
        system_prompt: str,  # noqa: ARG002
        user_prompt: str,  # noqa: ARG002
    ) -> str:
        """Route the scripted transport through the retry helper."""
        return await self._retry(self._transport)

    async def generate_structured(
        self,
        system_prompt: str,  # noqa: ARG002
        user_prompt: str,  # noqa: ARG002
        schema: type[BaseModel],  # noqa: ARG002
    ) -> str:
        """Route the scripted transport through the retry helper."""
        return await self._retry(self._transport)


async def test_retry_returns_the_value_after_a_transient_timeout() -> None:
    provider = _Recorder([httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"), "ok"])

    result = await provider.generate_content("sys", "usr")

    assert result == "ok"
    assert provider.calls == 3


async def test_retry_gives_up_after_max_attempts_and_reraises() -> None:
    provider = _Recorder([httpx.ReadTimeout("slow")] * MAX_ATTEMPTS)

    with pytest.raises(httpx.ReadTimeout):
        await provider.generate_content("sys", "usr")

    assert provider.calls == MAX_ATTEMPTS


async def test_backoff_grows_exponentially_between_attempts() -> None:
    provider = _Recorder([httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"), "ok"])

    await provider.generate_content("sys", "usr")

    assert provider.slept == [2.0, 4.0]


async def test_network_errors_are_retried_too() -> None:
    provider = _Recorder([httpx.ConnectError("refused"), "ok"])

    assert await provider.generate_content("sys", "usr") == "ok"
    assert provider.calls == 2


async def test_a_non_transient_error_is_not_retried() -> None:
    """An auth or validation failure must fail fast, not burn three calls."""
    provider = _Recorder([ValueError("bad api key")])

    with pytest.raises(ValueError, match="bad api key"):
        await provider.generate_content("sys", "usr")

    assert provider.calls == 1
    assert provider.slept == []
```

Note the tests drive the **public** `generate_content`, not `_retry` directly.
That exercises the real wiring and avoids `SLF001` (flake8-self is enabled and
there are no per-file ignores for tests — the repo's convention is an inline
`# noqa: SLF001` with a reason, which is worth not needing here).

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_providers.py -v`
Expected: FAIL — `ImportError: cannot import name 'MAX_ATTEMPTS'`

- [ ] **Step 4: Implement the helper**

In `src/cgis/guardian/providers/base.py`, add to the imports at the top:

```python
import asyncio
from collections.abc import Awaitable, Callable

import httpx
import structlog
```

and after the existing imports, before `class ProviderUsage`:

```python
log = structlog.getLogger(__name__)

#: Request timeout in SECONDS. Mistral and Gemini take milliseconds and Ollama
#: takes seconds, so each provider converts at its own call site — the unit is
#: visible where the conversion happens rather than buried in a constant's name.
DEFAULT_REQUEST_TIMEOUT = 180.0

#: Total attempts per call, including the first.
MAX_ATTEMPTS = 3

#: Backoff base: sleeps are BACKOFF_BASE ** attempt — 2 s, then 4 s.
BACKOFF_BASE = 2.0

#: Retried transport failures. Deliberately the same set the Mistral SDK itself
#: retries (mistralai/client/utils/retries.py) rather than a list invented here.
#: google-genai reraises httpx exceptions too, and httpx.NetworkError is the
#: parent of ConnectError, so this covers every provider.
RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.NetworkError,
)
```

Then add these two methods to `BaseProvider`, after `_record_usage`:

```python
    async def _sleep(self, seconds: float) -> None:
        """Wait between retries. Overridden in tests so they do not actually wait."""
        await asyncio.sleep(seconds)

    async def _retry(self, call: Callable[[], Awaitable[str]]) -> str:
        """Run call, retrying transient transport failures with exponential backoff.

        Retries only RETRYABLE_EXCEPTIONS: an auth or validation failure must
        fail on the first attempt rather than burn MAX_ATTEMPTS calls. The final
        exception propagates unchanged so callers' existing degradation paths
        still see a real error (#275).
        """
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return await call()
            except RETRYABLE_EXCEPTIONS as exc:
                if attempt == MAX_ATTEMPTS:
                    log.warning(
                        "Provider call failed; retries exhausted.",
                        attempts=MAX_ATTEMPTS,
                        error=repr(exc),
                    )
                    raise
                delay = BACKOFF_BASE**attempt
                log.warning(
                    "Provider call failed; retrying.",
                    attempt=attempt,
                    of=MAX_ATTEMPTS,
                    delay_s=delay,
                    error=repr(exc),
                )
                await self._sleep(delay)
        # Unreachable: the loop either returns or raises on the last attempt.
        raise AssertionError
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_providers.py -v`
Expected: PASS — 5 passed

- [ ] **Step 6: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/guardian/providers/base.py tests/unit/test_guardian_providers.py pyproject.toml uv.lock
git commit -m "feat(guardian): bounded retry with backoff on BaseProvider (#275)"
```

---

### Task 2: Mistral timeout + retry

**Files:**
- Modify: `src/cgis/guardian/providers/mistral.py`
- Test: `tests/unit/test_guardian_providers.py` (append)

**Interfaces:**
- Consumes: `DEFAULT_REQUEST_TIMEOUT`, `BaseProvider._retry` from Task 1
- Produces: `MistralProvider(api_key: str, model_name: str = "mistral-medium-latest", timeout: float = DEFAULT_REQUEST_TIMEOUT)`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_guardian_providers.py`:

Add `from cgis.guardian.providers.mistral import MistralProvider` to the test
module's imports — at module level, matching `test_guardian_runner.py`, which
imports `OllamaProvider` the same way. The provider modules import their SDKs
lazily inside `_generate`, so importing the module never requires `mistralai`
to be installed.

```python
def test_mistral_converts_the_timeout_to_milliseconds() -> None:
    """A seconds-vs-milliseconds slip is a factor-of-1000 error nothing else catches."""
    provider = MistralProvider(api_key="k", timeout=45.0)

    assert provider._timeout_ms == 45000  # noqa: SLF001  # white-box: timeout wiring


def test_mistral_defaults_to_the_shared_timeout() -> None:
    provider = MistralProvider(api_key="k")

    assert provider._timeout_ms == int(  # noqa: SLF001  # white-box: timeout wiring
        DEFAULT_REQUEST_TIMEOUT * 1000
    )
```

The `# noqa: SLF001` comments match the existing convention in
`test_guardian_runner.py:177` (`provider._host == ...  # noqa: SLF001  # white-box: host wiring`).

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_guardian_providers.py -v -k mistral`
Expected: FAIL — `AttributeError: 'MistralProvider' object has no attribute '_timeout_ms'`

- [ ] **Step 3: Implement**

In `src/cgis/guardian/providers/mistral.py`, change the import line to:

```python
from cgis.guardian.providers.base import DEFAULT_REQUEST_TIMEOUT, BaseProvider, ProviderUsage
```

Replace `__init__`:

```python
    def __init__(
        self,
        api_key: str,
        model_name: str = "mistral-medium-latest",
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        """Store credentials and the request timeout; mistralai is imported lazily.

        The SDK takes MILLISECONDS and hard-codes 60 000 when given nothing
        (mistralai/client/chat.py) — that default is what killed the review on
        #274, so it is always overridden here.
        """
        super().__init__()
        self._api_key = api_key
        self._model_name = model_name
        self._timeout_ms = int(timeout * 1000)
```

In `_generate`, pass the timeout to the client:

```python
        async with Mistral(api_key=self._api_key, timeout_ms=self._timeout_ms) as client:
```

And wrap both public methods:

```python
    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Send prompts to Mistral and return the text response."""
        return await self._retry(
            lambda: self._generate(system_prompt, user_prompt, json_mode=False)
        )

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> str:
        """Send prompts in json_object mode.

        Mistral's json_object mode takes no schema parameter — the schema is
        described in the user prompt (spec §2.4); the argument exists to
        satisfy the BaseProvider contract.
        """
        del schema
        return await self._retry(
            lambda: self._generate(system_prompt, user_prompt, json_mode=True)
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_providers.py -v`
Expected: PASS — 7 passed

- [ ] **Step 5: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/guardian/providers/mistral.py tests/unit/test_guardian_providers.py
git commit -m "fix(guardian): explicit Mistral timeout + retry (#275)"
```

---

### Task 3: Gemini timeout + retry

**Files:**
- Modify: `src/cgis/guardian/providers/gemini.py`
- Test: `tests/unit/test_guardian_providers.py` (append)

**Interfaces:**
- Consumes: `DEFAULT_REQUEST_TIMEOUT`, `BaseProvider._retry` from Task 1
- Produces: `GeminiProvider(api_key: str, model_name: str = "gemini-2.5-flash", timeout: float = DEFAULT_REQUEST_TIMEOUT)`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_guardian_providers.py`:

Add `from cgis.guardian.providers.gemini import GeminiProvider` to the test
module's imports, at module level for the same reason as Task 2.

```python
def test_gemini_converts_the_timeout_to_milliseconds() -> None:
    """HttpOptions.timeout is documented in milliseconds, same trap as Mistral."""
    provider = GeminiProvider(api_key="k", timeout=45.0)

    assert provider._timeout_ms == 45000  # noqa: SLF001  # white-box: timeout wiring


def test_gemini_defaults_to_the_shared_timeout() -> None:
    provider = GeminiProvider(api_key="k")

    assert provider._timeout_ms == int(  # noqa: SLF001  # white-box: timeout wiring
        DEFAULT_REQUEST_TIMEOUT * 1000
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_guardian_providers.py -v -k gemini`
Expected: FAIL — `AttributeError: 'GeminiProvider' object has no attribute '_timeout_ms'`

- [ ] **Step 3: Implement**

In `src/cgis/guardian/providers/gemini.py`, change the import line to:

```python
from cgis.guardian.providers.base import DEFAULT_REQUEST_TIMEOUT, BaseProvider, ProviderUsage
```

Replace `__init__`:

```python
    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.5-flash",
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        """Store credentials and the request timeout; google-genai is imported lazily.

        HttpOptions.timeout is documented in MILLISECONDS — the same unit trap
        as Mistral, and the opposite of Ollama's seconds.
        """
        super().__init__()
        self._api_key = api_key
        self._model_name = model_name
        self._timeout_ms = int(timeout * 1000)
```

In `_generate`, build the client with the timeout:

```python
        client = genai.Client(
            api_key=self._api_key,
            http_options=types.HttpOptions(timeout=self._timeout_ms),
        )
```

And wrap both public methods:

```python
    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Send prompts to Gemini and return the text response."""
        return await self._retry(lambda: self._generate(system_prompt, user_prompt, None))

    async def generate_structured(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        """Send prompts in native JSON mode constrained by schema."""
        return await self._retry(lambda: self._generate(system_prompt, user_prompt, schema))
```

Do **not** pass `retry_options`. google-genai defaults to `stop_after_attempt(1)`, so vendor retry is already off and must stay off — `BaseProvider._retry` is the single mechanism.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_providers.py -v`
Expected: PASS — 9 passed

- [ ] **Step 5: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/guardian/providers/gemini.py tests/unit/test_guardian_providers.py
git commit -m "fix(guardian): explicit Gemini timeout + retry (#275)"
```

---

### Task 4: Ollama retry

**Files:**
- Modify: `src/cgis/guardian/providers/ollama.py`

**Interfaces:**
- Consumes: `BaseProvider._retry` from Task 1
- Produces: no signature change — `OllamaProvider` keeps its existing `timeout: float = DEFAULT_OLLAMA_TIMEOUT`

- [ ] **Step 1: Wrap both public methods**

Ollama already passes an explicit timeout, so this task only adds retry. Replace the two public methods in `src/cgis/guardian/providers/ollama.py`:

```python
    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Send prompts to Ollama and return the text response."""
        return await self._retry(lambda: self._generate(system_prompt, user_prompt, fmt=""))

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> str:
        """Send prompts with Ollama's schema-constrained format (structured output).

        Passing the model's JSON Schema makes Ollama constrain decoding to a
        conformant object — small local models otherwise emit null in required
        fields under plain "json" mode, which fails strict validation downstream.
        """
        return await self._retry(
            lambda: self._generate(system_prompt, user_prompt, fmt=schema.model_json_schema())
        )
```

Leave `DEFAULT_OLLAMA_TIMEOUT = 600.0` and its comment untouched — cold-GPU weight loading is a real, different problem and that rationale still holds.

- [ ] **Step 2: Run the guardian tests**

Run: `uv run pytest tests/unit/ -v -k "guardian or ollama"`
Expected: PASS — no regressions. There is no new behaviour to assert here beyond what Task 1 already covers; Ollama's transport is unchanged and its retry path is the same helper.

- [ ] **Step 3: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/guardian/providers/ollama.py
git commit -m "fix(guardian): retry Ollama transport failures (#275)"
```

---

### Task 5: `duration_s` in the metrics record

**Files:**
- Modify: `src/cgis/guardian/metrics.py`
- Modify: `src/cgis/guardian/runner.py`
- Test: `tests/unit/test_guardian_metrics.py` (append)

**Interfaces:**
- Produces: `record_review(..., duration_s: float | None = None, ...)` — the entry gains a `"duration_s"` key

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_guardian_metrics.py` (read its existing helpers first and reuse them rather than inventing new fixtures):

```python
def test_record_review_writes_the_duration(tmp_path: Path) -> None:
    metrics_path = tmp_path / "metrics.jsonl"

    record_review(
        model="m",
        pr=1,
        prompt_tokens=10,
        completion_tokens=2,
        findings_total=0,
        lgtm=True,
        duration_s=12.5,
        metrics_path=metrics_path,
    )

    entry = json.loads(metrics_path.read_text(encoding="utf-8").strip())
    assert entry["duration_s"] == 12.5


def test_record_review_duration_defaults_to_none(tmp_path: Path) -> None:
    """Historical entries have no timing; the field must be optional, not required."""
    metrics_path = tmp_path / "metrics.jsonl"

    record_review(
        model="m",
        pr=1,
        prompt_tokens=10,
        completion_tokens=2,
        findings_total=0,
        lgtm=True,
        metrics_path=metrics_path,
    )

    entry = json.loads(metrics_path.read_text(encoding="utf-8").strip())
    assert entry["duration_s"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_metrics.py -v -k duration`
Expected: FAIL — `TypeError: record_review() got an unexpected keyword argument 'duration_s'`

- [ ] **Step 3: Implement**

In `src/cgis/guardian/metrics.py`, add the parameter to `record_review` (keyword-only, after `chunk_count`):

```python
    chunk_count: int | None = None,
    duration_s: float | None = None,
    metrics_path: Path = _DEFAULT_METRICS_FILE,
```

and add the key to the `entry` dict, after `"chunk_count"`:

```python
        "chunk_count": chunk_count,
        # Wall-clock of the whole LLM phase (finder + skeptic). None on entries
        # written before #275. Completed runs only — a failing review writes no
        # record at all, so this data is survivorship-biased by construction.
        "duration_s": duration_s,
```

In `src/cgis/guardian/runner.py`, add `import time` to the imports, then measure around the routed call:

```python
    started = time.monotonic()
    routed = await run_review_routed(
        provider=provider,
        collector=collector,
        skeptic_provider=skeptic[0] if skeptic else None,
    )
    duration_s = round(time.monotonic() - started, 2)
```

and pass it to `record_review`, next to `chunk_count`:

```python
        chunk_count=routed.chunk_count,
        duration_s=duration_s,
        metrics_path=metrics_path,
```

Use `time.monotonic()`, not `time.time()` — a wall-clock adjustment mid-review would otherwise produce a negative or nonsense duration.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_metrics.py -v`
Expected: PASS

- [ ] **Step 5: Confirm historical records still load**

Run:
```bash
uv run python -c "
from pathlib import Path
from cgis.guardian.metrics import load_reviews
import subprocess
raw = subprocess.run(['git','show','origin/data/guardian-metrics:guardian_metrics.jsonl'],
                     capture_output=True, text=True).stdout
p = Path('/tmp/hist_metrics.jsonl'); p.write_text(raw)
rows = load_reviews(p)
print('loaded', len(rows), 'historical rows; duration_s present in', sum('duration_s' in r for r in rows))
"
```
Expected: `loaded 41 historical rows; duration_s present in 0` — the 41 pre-existing entries must still parse. If `load_reviews` raises, the field was made required somewhere; it must stay optional.

- [ ] **Step 6: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/guardian/metrics.py src/cgis/guardian/runner.py tests/unit/test_guardian_metrics.py
git commit -m "feat(guardian): record review duration in metrics (#275)"
```

---

## Definition of done

- `make format && make lint && make type-check && make pytest && make doc-coverage` all pass.
- Every existing guardian test passes unchanged — review content, finding counts and metrics semantics are untouched apart from the added field.
- `MistralProvider` and `GeminiProvider` both convert seconds to milliseconds, each covered by its own regression test.
- All three providers route their public calls through `BaseProvider._retry`.
- The 41 historical metrics entries still load.
- No change to `chunked.py`, prompts, context assembly, or the finder/skeptic logic.

## Post-merge verification (needs a live API key — not part of the PR)

Comment `/guardian review` on a PR and confirm the run completes and its metrics
entry carries a plausible `duration_s`. This is acceptance criterion 2 in the
spec and cannot be checked from the branch, since no provider key is available
locally.
