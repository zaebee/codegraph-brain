# Guardian request timeout + bounded retry — design (#275)

**Status:** approved (2026-07-30)
**Issue:** #275
**Lane:** guardian / providers

## Goal

Stop a single slow API window from destroying a whole Guardian run. Give every
provider an explicit request timeout and one bounded retry with backoff, and
record how long a review actually takes so the next timeout decision is a
measurement rather than a judgment call.

## What the measurement established

The failure on #274 was originally attributed to diff size taking the
single-pass path. That was wrong, and the correction is the reason this spec is
scoped the way it is.

#274's prompt was reconstructed locally (`ContextCollector` with
`base_ref=2e768ce^`, `GUARDIAN_FEATURES` unset, exactly as CI runs it) and
compared against the 41 runs in `guardian_metrics.jsonl` on
`data/guardian-metrics`:

| | prompt tokens |
|---|---|
| **#274 (failed twice)** | **32,795** |
| historical median | 16,220 |
| historical p90 | 36,682 |
| historical max, **succeeded** | **95,900** |

The failing prompt is below the p90 of prompts that passed, and about a third of
the largest one this setup has handled. **Size was not the cause**, and chunk
routing would not have prevented the failure. That gap is real but separate,
and moved to #277.

The actual cause, `mistralai/client/chat.py:189`:

```python
if timeout_ms is None:
    timeout_ms = 60000
```

A hard-coded 60 s ceiling on the chat endpoint, matching the observed 60 s wall
on both attempts. `providers/mistral.py` passes neither `timeout_ms` nor
`retry_config`, though the SDK accepts both. Responses are tiny — median 161
completion tokens, max 2227 — so that minute is spent on prompt processing and
queueing, not generation. There is no slack and no second attempt.

## Design

### 1. Timeout: vendor-native, per provider

Only the SDK's own knob changes the underlying socket timeout, so each provider
passes its own:

| provider | knob | unit |
|---|---|---|
| Mistral | `Mistral(timeout_ms=...)` | **milliseconds** |
| Gemini | `HttpOptions(timeout=...)` on `genai.Client` | **milliseconds** |
| Ollama | `AsyncClient(timeout=...)` — already present | **seconds** |

The unit mismatch is a live footgun: two of the three take milliseconds and one
takes seconds. The shared constant is defined in **seconds** and each provider
converts at its call site, so the conversion is visible where it happens rather
than encoded in a constant's name.

`DEFAULT_REQUEST_TIMEOUT = 180.0` seconds, overridable per provider through the
constructor (matching how `OllamaProvider` already takes `timeout`), so the
bench can tighten it without touching module state.

**Ollama keeps its existing 600 s.** Its comment documents a different problem —
cold GPU weight loading on the first call — and that rationale still holds.

### 2. Retry: one mechanism, in `BaseProvider`

```python
async def _retry(self, call: Callable[[], Awaitable[str]]) -> str:
```

Each provider's public `generate_content` / `generate_structured` wraps its
`_generate` call in `self._retry(...)`. The helper retries on
`(httpx.TimeoutException, httpx.NetworkError)` — deliberately the same set the
Mistral SDK itself retries (`mistralai/client/utils/retries.py:148`), so the
exception list is borrowed from the vendor rather than invented here. All three
providers sit on httpx, so the set applies uniformly.

That the set actually reaches us was verified per provider rather than assumed.
Mistral raises httpx exceptions directly — that is what the #274 traceback
shows. google-genai reraises them too: its own retry predicate is
`isinstance(e, (httpx.TimeoutException, httpx.ConnectError))` with
`reraise: True` (`google/genai/_api_client.py:539`). `httpx.NetworkError` is the
parent of `ConnectError`, so our two-entry set is a superset of both vendors'
choices.

`MAX_ATTEMPTS = 3`, exponential backoff `2 s` then `4 s`. Every retry logs the
attempt number and the exception; the final failure propagates unchanged so the
caller's existing degradation paths still see a real error.

**Vendor retry stays off**, which is also the SDKs' own default — Mistral
retries only when handed a `RetryConfig`, and google-genai falls back to
`stop_after_attempt(1)` when `retry_options` is None. Turning either on would
multiply attempts (the SDK's 3 times ours 3 is 9 calls for one review) and hide
the retries from our logs. One mechanism, in one place, visible and
unit-testable without a network.

### 3. `duration_s` in the metrics record

`record_review()` gains a `duration_s: float` field, measured in `run_guardian`
around the `run_review_routed` call with `time.monotonic()` — covering the whole
LLM phase, finder and skeptic together.

This exists because the 180 s above **is a judgment call, not a measurement**.
41 recorded runs carry token counts and no timing at all, so there is no way to
say how close the normal case runs to the ceiling. One field closes that gap:
after a few reviews the timeout can be set from the observed distribution.

**First measurement, taken on this branch (PR #278):** a successful review of
this very PR took **64.66 s** on a 22,936-token prompt — *above* the 60 s
default that was killing runs. So the ceiling was not merely tight for outsized
diffs; it sat below the normal operating time of an ordinary review. That single
number moves 180 s from "arbitrary" to "roughly 3× a measured normal run", and
it is direct evidence that the diagnosis was right.

The value chosen now rests on cost asymmetry rather than data. A timeout that is
too long costs CI minutes only in a case that is already broken; one that is too
short costs an entire review. Worst case with a dead API is roughly
`3 × 180 s + 6 s ≈ 9.1 min`, against a job with no `timeout-minutes` set and
GitHub's 6-hour default — comfortable, and not so long that a hung run goes
unnoticed.

## Where it lives

- `src/cgis/guardian/providers/base.py` — `DEFAULT_REQUEST_TIMEOUT`,
  `MAX_ATTEMPTS`, `RETRYABLE_EXCEPTIONS`, `BaseProvider._retry`.
- `src/cgis/guardian/providers/mistral.py` — accept `timeout`, pass
  `timeout_ms=int(timeout * 1000)`, wrap both public methods in `_retry`.
- `src/cgis/guardian/providers/gemini.py` — accept `timeout`, pass
  `HttpOptions(timeout=int(timeout * 1000))`, wrap both public methods.
- `src/cgis/guardian/providers/ollama.py` — wrap both public methods; timeout
  unchanged.
- `src/cgis/guardian/metrics.py` — `duration_s` on `record_review`.
- `src/cgis/guardian/runner.py` — measure around `run_review_routed`.
- `pyproject.toml` — add `httpx` to the **`guardian` dependency group**. It is
  already installed transitively via `mcp[cli]`, but `base.py` will now import
  it directly, and only guardian code does, so that is where it is declared.

## Error and edge handling

- A non-retryable exception (auth, validation, a 4xx surfaced as an SDK error)
  propagates on the first attempt — no waiting, no wasted calls.
- Retries exhausted: the last exception propagates unchanged. The chunked path
  still degrades per chunk and the single-pass path still fails the run, exactly
  as today.
- `duration_s` covers **completed runs only**. `run_guardian` has no `try`
  around `run_review_routed`; a failing review propagates out and writes no
  metrics record at all, which is exactly why #274 left no trace. Recording
  failures would mean adding error handling that swallows the exception, and
  the workflow depends on a non-zero exit to mark the job failed — out of scope
  here. The consequence is worth naming: the timing data this collects is
  **survivorship-biased**, the same bias that made all 41 existing records
  successes. It bounds how long successful reviews take, not how long the slow
  ones needed.
- Backoff sleeps are real `asyncio.sleep` calls; tests inject a zero-delay sleep
  rather than waiting.

## Testing

Unit — new file `tests/unit/test_guardian_providers.py`; the providers have no
test module today, which is why the missing timeout went unnoticed:

- A fake provider whose `_generate` raises `httpx.ReadTimeout` twice then
  succeeds: `_retry` returns the value, and exactly 3 calls were made.
- Raising `httpx.ReadTimeout` on every attempt: the exception propagates and
  exactly `MAX_ATTEMPTS` calls were made.
- A non-retryable exception (`ValueError`): propagates immediately, exactly 1
  call, no sleep.
- Backoff delays are `[2.0, 4.0]` — asserted against an injected sleep spy, not
  by wall-clock.
- `MistralProvider(timeout=45)` passes `timeout_ms=45000`; `GeminiProvider`
  likewise passes milliseconds. This is the regression test for the unit
  mismatch — a seconds-vs-milliseconds slip is a factor-of-1000 error that no
  other test would catch.
- `record_review(..., duration_s=12.5)` writes the field; a record without it
  still parses, so historical entries stay readable by `load_reviews`.

## Acceptance criteria

1. All existing guardian tests pass unchanged — this is a robustness fix and
   must not alter review content, finding counts, or metrics semantics beyond
   the added field.
2. A `/guardian review` run on a real PR completes and its metrics entry carries
   a plausible `duration_s`.
3. `make format && make lint && make type-check && make pytest && make doc-coverage`
   all pass.

## Out of scope

- **Chunk routing (#277).** The chunked path has never run in production (41/41
  recorded runs are single-pass). Enabling it changes the default review path
  and the finder's context, so it needs bench evidence against ground truth, not
  a threshold. Explicitly not touched here.
- Changing prompts, context assembly, or the finder/skeptic logic.
- Cohere, which is declared in the guardian dependency group but has no provider
  module.
