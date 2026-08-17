# Benchmarking Guardian on a local model, inside a notebook

Free measurement of a local model as Guardian's finder or skeptic, on a borrowed
GPU, with nothing exposed to the network.

## Why this shape

Two facts make it simple, and both were checked rather than assumed.

**Scoring needs no LLM judge.** `bench.score` compares findings against the
curated ground truth in `benchmarks/guardian/pr-*.yaml` deterministically. That
is what separates these fixtures from the Martian corpus, where every comparison
costs judge calls — here a run is free end to end, even with both API budgets
exhausted.

**Nothing has to be exported.** The repository is public and small, so the
notebook clones it and gets the fixtures, the harness, and the git history the
replay needs. An earlier plan to design a bundle format was wasted motion.

There is no tunnel. Colab terminates a runtime that opens one — the symptom is a
runtime that dies *only* while the tunnel is up — and the work does not need one:
the model, the harness and the fixtures all sit in the notebook together.

## Running it

```
!git clone https://github.com/zaebee/codegraph-brain
%cd codegraph-brain
!bash scripts/colab_bench.sh qwen3.5:9b
```

Arguments after the model name pass through to `guardian_bench.py`, so
`... qwen3.5:9b --pr 143 --runs 3` works.

Download `benchmarks/guardian/results.jsonl` afterwards and score it locally.

### Install from the clone, not from PyPI

`pip install codegraph-brain` would install **0.11.0**, which predates the
truncation guard and the salvage path. On that version a prompt too large for the
context window is cut silently — so a low recall cannot be told apart from a
truncated input, which is precisely the ambiguity that made the June round of
local-model results unreadable. The script installs `-e '.[guardian]'` from the
checkout for that reason.

The wheel also carries only `cgis/`: no `scripts/`, no `benchmarks/`, and no git
history. The harness is not in it in any case.

## Context window: what fits

Measured prompt sizes per fixture (characters ÷ 3.6):

| fixture | with graph | diff-only |
|---|---|---|
| pr-313 | 6.8k | 6.5k |
| pr-141 | 8.3k | 7.9k |
| pr-142 | 8.8k | 8.8k |
| pr-122 | 13.1k | 13.1k |
| pr-278 | 22.4k | 20.4k |
| pr-143 | 32.4k | 31.8k |
| pr-140 | 34.2k | 33.4k |
| **pr-144** | **46.7k** | **43.8k** |

At the script's default `GUARDIAN_OLLAMA_NUM_CTX=40960`, seven of eight fixtures
fit and **pr-144 does not**. It will raise `PromptTruncatedError` naming the
model and the knob. That is the guard working, not a bug: before it existed, the
same prompt was cut silently and the missing findings looked like a weak model.

**`GUARDIAN_NO_GRAPH` is not a lever for fitting a small window.** Measured, it
saves 2–6%: the diff dominates the prompt and the graph section is small beside
it. Earlier notes in this project described it as the way to fit a rich diff into
a smaller context; the measurement above retires that.

On a 16GB T4, an 8–9B model at Q4 holds roughly 32–48k, so 40960 is already near
the ceiling. Raising it far enough for pr-144 risks an out-of-memory that leaves
the Ollama server unhealthy until restarted.

## How long it takes

A T4 processes the prompt at roughly 1000 tok/s — 14k tokens in 15 seconds — and
then generates at about **24 tok/s**. Generation is the cost: the recall-lean
finder writes 3–4k tokens per review (measured on real PRs: median 3100, mean
3629), so budget **2–4 minutes per fixture** and around half an hour for all
eight. That is the hardware, not a misconfiguration.

Pass `--pr N` to run one fixture at a time if the notebook cell is short-lived.
Start with the small ones: pr-313, pr-141, pr-142.

Sampling is stated rather than inherited. Left alone, Ollama takes it from the
model's chat template — qwen3.5:9b ships `temperature 1.0`, `presence_penalty
1.5`, `repeat_penalty 1.1`. Those are conversation defaults; the structured path
now sends neutral repetition penalties, because the finder's JSON repeats its
keys in every object by construction and nobody chose to penalise that.

## When the model will not stop

Observed on qwen3.5:9b: **9,358 generated tokens on a 6,495-token prompt with no
stop token**, ending in the 600-second client timeout, which returns nothing at
all. Not slowness — non-termination.

The likely cause is ours. `ReviewResult.findings` is an array with no `maxItems`,
so under grammar-constrained decoding another element is *always* permitted and
closing the array stays a choice the model can decline. Mistral's `json_object`
mode is not grammar-constrained the same way, which is why it stops on its own
between 1.4k and 6.8k tokens.

`GUARDIAN_OLLAMA_NUM_PREDICT` (default **8192**) bounds it. Hitting the budget
produces a truncated response, whose valid prefix is salvaged and whose row is
flagged `parse_failed` — so the bench **excludes** it rather than scoring a
finder that was cut off. That is the point: the budget bounds cost without
quietly shortening a measurement.

It is not the finding cap #249 removed. That limited how many claims the model
was allowed to make and depressed recall by construction; a token budget does not
choose which findings get made.

**No `maxItems` was added to the schema.** The schema is shared with Mistral and
Gemini, so bounding it there would reimpose on them the cap #249 deliberately
removed.

### An open question, deliberately left as a switch

Neutralising the model's repetition penalties was argued from the shape of the
task — the finder's JSON repeats its keys in every object — **not from evidence**.
The run immediately after that change did not terminate. It used a different
fixture, so it neither confirms nor refutes the change.

`GUARDIAN_OLLAMA_PENALTIES=model` restores the model's own. The experiment is
one fixture run twice with the flag flipped, comparing `n_decoded` in the
llama-server log. Until someone runs it, neither setting has evidence behind it,
and this document should not pretend otherwise.

## What this can and cannot answer

**Finder.** Runs today, unblocked. The open question is real: local 8B finders
measured 0/6 recall against `mistral-medium`'s 0.8, but that was granite-code and
qwen3 in June, a generation ago, and under silent truncation. A 2026 9B is
untested here.

**Skeptic.** Needs a frozen finder set inside the clone so that every skeptic
variant judges the same findings — the method that finally isolated the skeptic
in #246. `--record-finder` produces one, but it takes a single path and rewrites
it per PR, so freeze one fixture at a time until that is changed.

**Neither result may be swapped into #342 Phase 3.** That phase is registered
with a `mistral-medium` finder and a same-family skeptic. A local model is a
different configuration, and a good result here means a new registration, not an
edit to the running one.

## When it breaks

- **`PromptTruncatedError`.** Expected on pr-144 at the default window. Use a
  longer-context model, or accept seven fixtures and say which.
- **The runtime dies for no reason.** Check that nothing is tunnelling. That is
  the one reliable trigger observed so far.
- **`Server disconnected` / HTTP 000 mid-run.** Usually the runtime went away.
  An out-of-memory says so explicitly instead.
