# Close the Gemini client's connection pools — design (#283)

**Status:** approved (2026-07-30)
**Issue:** #283
**Lane:** guardian / providers

## Goal

Make `GeminiProvider` close what it opens, matching the two sibling providers.

## What the measurement established

`genai.Client(...)` eagerly creates **two** httpx pools before any request is
sent — one sync, one async:

```
httpx clients before Client(): 0 | after: 2
  sync httpx.Client: 1 | async httpx.AsyncClient: 1
```

`gemini.py` constructs one per `_generate` call and drops it. Its siblings both
close theirs per call — `mistral.py:38` and `ollama.py:58` each use
`async with`. Gemini is the only one without that guarantee, and nothing in the
code claims it should differ.

Since #275, `BaseProvider._retry` calls `_generate` up to `MAX_ATTEMPTS = 3`
times on transient failures, so one logical call can leave **up to six**
unclosed pools.

### Both halves need closing, and neither call does both

The SDK is explicit about this, in both directions:

- `Client.close()` — "Closes the synchronous client explicitly. However, it
  doesn't close the async client, which can be closed using the
  `Client.aio.aclose()` method or using the async context manager."
- `AsyncClient.aclose()` — "Closes the async client explicitly. However, it
  doesn't close the sync client, which can be closed using the `Client.close()`
  method or using the context manager."

Verified that the pair does close both:

```
after aclose()+close(): both closed? True True
```

This corrects the fix sketched in #283, which named only the async half. Reaching
for `client.close()` alone — the obvious move — fixes the *other* half and leaves
the async pool open.

## Design

```python
client = genai.Client(api_key=..., http_options=...)
try:
    response = await client.aio.models.generate_content(...)
    ...
finally:
    await client.aio.aclose()
    client.close()
```

**Why an explicit `finally` rather than `async with client.aio`.** The context
manager closes only the async half, so the sync half would still need a `finally`
— two idioms for one job, in a function that is nine lines long. One `finally`
closing both reads as what it is: this call owns two pools and releases both.

It is also the shape the test can assert without teaching a `MagicMock` to be an
async context manager: `aclose.assert_awaited_once()` and
`close.assert_called_once()`.

`client.aio` is a cached property (`c.aio is c.aio` → True), so referencing it in
`finally` does not build a second AsyncClient.

**Mistral and Ollama are untouched.** They already close per call via
`async with`; only Gemini diverged.

## Testing

`google-genai` lives in the `guardian` dependency group, and CI runs
`uv sync --group dev` — so the SDK is absent in CI. `test_guardian_core.py:366`
already solves this by injecting a fake module tree:

```python
with patch.dict(
    "sys.modules",
    {"google": MagicMock(genai=mock_genai), "google.genai": mock_genai,
     "google.genai.types": MagicMock()},
):
```

The new test reuses that pattern and asserts both closes happen — appended to
`tests/unit/test_guardian_core.py`, beside the existing Gemini provider tests
rather than in a new file.

Two cases:

- A successful call closes both pools.
- **A call whose request raises still closes both.** This is the case the
  `finally` exists for, and the one a naive fix would miss.

## Error and edge handling

- If `generate_content` raises, the `finally` runs before the exception
  propagates, so `_retry` sees the same exception it would have seen before —
  retry behaviour is unchanged.
- If `aclose()` itself raises, that exception replaces the original. Accepted:
  closing a pool that failed to close is not a recoverable situation, and
  swallowing it would hide a genuine transport problem. The cleanup is not
  wrapped in `except`.
- **But the two closes are nested, not sequential.** Written as two flat
  statements, a throwing `aclose()` would skip `client.close()` and strand the
  sync pool — the exact class of leak this change removes. So `client.close()`
  sits in an inner `finally`, which releases it regardless while still letting
  the `aclose()` exception propagate. Found in review of this branch; the first
  draft had the flat form.

## Acceptance criteria

1. Both new tests pass, and every existing Gemini/provider test passes unchanged.
2. `make format && make lint && make type-check && make pytest && make doc-coverage`
   all pass, and `uv run mypy src` passes in a CI-shaped environment
   (`uv sync --group dev`, no guardian group).

## Out of scope

- Reusing one client across calls. That would break the lazy SDK import the
  providers deliberately use and diverge from Mistral and Ollama, which build and
  close per call.
- Any change to Mistral or Ollama.
- Retry, timeout or review behaviour.
