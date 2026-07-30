# Gemini Client Close Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `GeminiProvider` close both httpx pools its client opens, so a retried call cannot leak up to six of them.

**Architecture:** One `try/finally` around the request in `_generate`, closing the async pool with `await client.aio.aclose()` and the sync one with `client.close()`. Tests inject a fake `google.genai` module tree, following the pattern already in `test_guardian_core.py`, because the SDK is absent from CI.

**Tech Stack:** Python 3.12+, `unittest.mock` (`MagicMock`, `AsyncMock`, `patch.dict`), pytest (`asyncio_mode = auto` — async tests need no decorator in this file).

**Spec:** `docs/specs/2026-07-30-genai-client-close-design.md`
**Issue:** #283

## Global Constraints

- **MyPy strict** (`make type-check` runs `mypy src`). Full annotations including return types.
- **Ruff** full rule set, line length **100**, double quotes. The lazy SDK import inside `_generate` keeps its `# noqa: PLC0415` — that is the deliberate provider pattern, do not "fix" it.
- **Docstring coverage ≥ 90%** (`uv run interrogate src`; `tests/` is excluded).
- **Do not touch Mistral or Ollama.** They already close per call via `async with`.
- **Do not reuse a client across calls.** Building it per call is what keeps the SDK import lazy and matches the sibling providers.
- **No change to retry, timeout or review behaviour.**
- **Verify in a CI-shaped environment**: CI runs `uv sync --group dev` (no guardian group), so `google-genai` is absent there and `uv run mypy src` must still pass.
- **Full verification before the commit:** `make format && make lint && make type-check && make pytest && make doc-coverage`.
- Branch `fix/283-genai-client-close`, worktree `.claude/worktrees/genai-close`.

## File Structure

| File | Responsibility |
|------|----------------|
| `src/cgis/guardian/providers/gemini.py` (modify) | The `try/finally` closing both pools |
| `tests/unit/test_guardian_core.py` (modify) | A shared Gemini mock helper; two new close tests; the existing test adapted |

One task: the change is nine lines and its tests, and splitting it would leave a
commit where the existing Gemini test fails.

---

### Task 1: Close both pools in `_generate`

**Files:**
- Modify: `src/cgis/guardian/providers/gemini.py:41-58`
- Modify: `tests/unit/test_guardian_core.py` (the `GeminiProvider` section, from line 349)

**Interfaces:**
- Produces: no signature change. `GeminiProvider._generate` gains a `finally` block; the test module gains `_gemini_mocks(response: MagicMock) -> tuple[MagicMock, dict[str, MagicMock]]`.

**Read this before starting.** Adding `await client.aio.aclose()` **breaks the
existing test** `test_gemini_provider_returns_text`: its `mock_client` is a plain
`MagicMock`, so `mock_client.aio.aclose()` returns a `MagicMock`, and awaiting
that raises `TypeError: object MagicMock can't be used in 'await' expression`.
That is expected, not a mystery — Step 1 introduces a shared helper that fixes it
for all three tests at once.

- [ ] **Step 1: Add the shared mock helper and adapt the existing test**

In `tests/unit/test_guardian_core.py`, in the `GeminiProvider` section (it starts
with the `# ---- GeminiProvider ----` banner near line 349), add the helper above
`test_gemini_provider_returns_text`:

```python
def _gemini_mocks(response: MagicMock) -> tuple[MagicMock, dict[str, MagicMock]]:
    """A fake google.genai tree returning `response`, with both closes awaitable.

    google-genai is in the guardian dep-group, not dev — it is absent in CI test
    runs, so the module tree is injected rather than installed (same reason as
    _mistral_modules below). aclose is an AsyncMock because the provider awaits
    it; a plain MagicMock would not be awaitable.
    """
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=response)
    client.aio.aclose = AsyncMock()
    genai = MagicMock()
    genai.Client.return_value = client
    modules = {
        "google": MagicMock(genai=genai),
        "google.genai": genai,
        "google.genai.types": MagicMock(),
    }
    return client, modules
```

Then rewrite `test_gemini_provider_returns_text` to use it:

```python
async def test_gemini_provider_returns_text() -> None:
    """GeminiProvider.generate_content() returns response.text."""
    mock_response = MagicMock()
    mock_response.text = "gemini says LGTM"
    _client, modules = _gemini_mocks(mock_response)

    provider = GeminiProvider(api_key="fake")
    with patch.dict("sys.modules", modules):
        result = await provider.generate_content("sys", "user")

    assert result == "gemini says LGTM"
```

Leave `test_gemini_provider_import_error` exactly as it is — it patches the
modules to `None` and never reaches a client.

- [ ] **Step 2: Write the failing close tests**

Append them straight after `test_gemini_provider_returns_text`:

```python
async def test_gemini_closes_both_pools() -> None:
    """genai.Client opens a sync AND an async httpx pool; both must be released (#283)."""
    mock_response = MagicMock()
    mock_response.text = "ok"
    client, modules = _gemini_mocks(mock_response)

    provider = GeminiProvider(api_key="fake")
    with patch.dict("sys.modules", modules):
        await provider.generate_content("sys", "user")

    client.aio.aclose.assert_awaited_once()
    client.close.assert_called_once()


async def test_gemini_closes_both_pools_when_the_request_fails() -> None:
    """The case the finally exists for — a naive fix leaks exactly here (#283)."""
    client, modules = _gemini_mocks(MagicMock())
    client.aio.models.generate_content = AsyncMock(side_effect=RuntimeError("boom"))

    provider = GeminiProvider(api_key="fake")
    with patch.dict("sys.modules", modules), pytest.raises(RuntimeError, match="boom"):
        await provider.generate_content("sys", "user")

    client.aio.aclose.assert_awaited_once()
    client.close.assert_called_once()
```

`pytest` and `AsyncMock` are already imported at the top of this module.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_core.py -v -k gemini`
Expected: the two new tests FAIL on `Expected 'aclose' to have been awaited once. Awaited 0 times.`
`test_gemini_provider_returns_text` should PASS — Step 1 only changed how its
mocks are built, not what it asserts.

- [ ] **Step 4: Implement the close**

In `src/cgis/guardian/providers/gemini.py`, wrap the request. Replace from the
`response = await client.aio...` call through `return str(response.text)`:

```python
        client = genai.Client(
            api_key=self._api_key,
            http_options=types.HttpOptions(timeout=self._timeout_ms),
        )
        try:
            response = await client.aio.models.generate_content(
                model=self._model_name,
                contents=user_prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )
            meta = getattr(response, "usage_metadata", None)
            if meta is not None:
                self._record_usage(
                    ProviderUsage(
                        prompt_tokens=getattr(meta, "prompt_token_count", 0),
                        completion_tokens=getattr(meta, "candidates_token_count", 0),
                    )
                )
            return str(response.text)
        finally:
            # genai.Client opens BOTH a sync and an async httpx pool on
            # construction, and each close covers only its own half — the SDK
            # docstrings say so explicitly in both directions. Mistral and Ollama
            # get this from `async with`; Gemini's client is not an async context
            # manager, so it is spelled out here (#283). With the retry from #275
            # calling this up to MAX_ATTEMPTS times, leaking would compound.
            await client.aio.aclose()
            client.close()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_core.py -v -k gemini`
Expected: PASS — all four Gemini tests.

- [ ] **Step 6: Verify against the real SDK**

The tests use mocks, so confirm the calls exist on the actual client and that the
pair really closes both pools:

```bash
uv run --with google-genai python -c "
import asyncio
from google import genai
c = genai.Client(api_key='x')
asyncio.run(c.aio.aclose())
c.close()
print('sync closed:', c._api_client._httpx_client.is_closed)
print('async closed:', c._api_client._async_httpx_client.is_closed)
"
```

Expected: both `True`. This runs against the installed SDK, not a mock — if the
API ever changes shape, this is what catches it.

- [ ] **Step 7: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/guardian/providers/gemini.py tests/unit/test_guardian_core.py
git commit -m "fix(guardian): close both Gemini connection pools (#283)"
```

---

## Definition of done

- `make format && make lint && make type-check && make pytest && make doc-coverage` all pass.
- `uv run mypy src` passes in a CI-shaped environment (`uv sync --group dev`, no guardian group).
- Both new tests pass, and the two pre-existing Gemini tests still pass.
- The Step 6 check prints `True` twice against the real SDK.
- `mistral.py` and `ollama.py` are untouched.
