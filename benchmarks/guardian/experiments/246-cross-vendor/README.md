# Cross-vendor skeptic: still blocked, and now on measured limits (#246)

Two attempts at the third arm, both stopped by the validity gate before any
number was reported. The gate is the deliverable here; the comparison is not.

## Why a gate at all

`judge_finding` contains provider errors per finding and returns None, so a
spent quota, an HTTP 429, a 402 and unparseable JSON all arrive as **the same
row shape as a skeptic that refused to refute anything** — which is exactly what
#246 predicts for a same-vendor skeptic. A broken arm would confirm the
hypothesis. `MAX_UNRULED_RATE` refuses to print a comparison above 5% unruled,
keeps the rows, and exits 2.

It fired on both attempts. Neither produced a number that could be read.

## Attempt 1 — free tier. The quota is 50 requests per day.

`nvidia/nemotron-3-super-120b-a12b:free`, the whole corpus: **96 of 135 findings
unruled**, and the pattern is a cutoff in time rather than a property of the
input.

| PR | evidence | unruled |
|---|---|---:|
| 122 (first) | no | **0 / 29** |
| 140 | yes | 29 / 39 |
| 141, 142, 143, 144 | — | **100%** |

39 answered, then silence. With the day's earlier probes (4 candidate calls, 6
in a smoke run) that is 49 successful requests before the wall — the documented
free allowance is 50/day, and this is that number arrived at from the data.

Rows: `free-nemotron-quota-cutoff.jsonl`.

## Attempt 2 — paid. Two separate faults, one after the other.

`qwen/qwen3.7-plus`, chosen because capability has to match: comparing
`gemini-2.5-flash` against a small free model measures strong-versus-weak and
calls it same-versus-cross.

**First fault — thinking.** 118 of 135 unruled. `qwen3.7-plus` puts its chain of
thought in a separate `reasoning` field and fills `content` only at the end, so
on the larger prompts it spent the entire 8,000-token budget thinking and
returned `content: null`. Raising the budget would have fixed the symptom and
broken the experiment: the arm it is compared against is not doing extended
thinking, so the two arms would differ in a dimension nobody chose. The provider
now sends `reasoning: {"enabled": …}` explicitly in both directions, off by
default, so a run can state what it did.

**Second fault — credits.** The re-run failed on HTTP **402**: the account has
`total_credits: $0` against `total_usage: $0.159`. The trial allowance is spent;
paid models are unavailable until it is topped up.

The second fault took half an hour to identify because the warning read only
"Skeptic judgement failed; finding stays unruled" — 118 identical lines, no
type, no message. It now carries both. A quota, a truncated reasoning model and
a 402 have three different remedies and all three land in that one `except`.

## To unblock

1. **Top up OpenRouter.** The measured cost of the run with reasoning off is
   ~$0.15: 135 findings × ~2,700 prompt tokens at \$0.32/M, plus short
   completions at \$1.28/M. A dollar covers it several times over.
2. **Free tier across three days.** The input is a frozen recording, so the run
   is deterministic and can be split — 50 requests a day, 135 needed. Fragile,
   and it needs resume support the tool does not have.
3. A local ollama, which this issue already names as the no-cost option.

Everything else is in place: the corpus with both finder orientations, the
recordings, per-arm models, the scoring, and the gate that stopped two wrong
answers from being published.
