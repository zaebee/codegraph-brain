#!/usr/bin/env bash
# Run the Guardian benchmark against a local Ollama, inside a Colab notebook.
#
#   !git clone https://github.com/zaebee/codegraph-brain && cd codegraph-brain
#   !bash scripts/colab_bench.sh qwen3.5:9b
#
# Nothing is tunnelled and no port is exposed: the model, the harness and the
# fixtures all live in the notebook. That is deliberate — Colab terminates a
# runtime that opens a tunnel, and the work does not need one.
#
# Scoring costs nothing either. `bench.score` compares findings against the
# curated ground truth in benchmarks/guardian/pr-*.yaml deterministically, so no
# LLM judge is involved and the result is reproducible offline.
#
# Install from this clone, never from PyPI. The published 0.11.0 predates the
# truncation guard and the salvage path, which are exactly what make a local run
# legible — on that version a prompt too large for the window is cut silently and
# a low recall cannot be told apart from a truncated input.

set -euo pipefail

MODEL="${1:-qwen3.5:9b}"

#: Fits every fixture except pr-144 (~47k tokens). On a 16GB T4 an 8-9B model at
#: Q4 holds roughly 32-48k, so this is close to the ceiling; raising it far
#: enough for pr-144 risks an OOM that leaves the server unhealthy.
#: GUARDIAN_NO_GRAPH is not the answer — measured, it saves only 2-6%, because
#: the diff dominates the prompt and the graph section is small beside it.
export GUARDIAN_OLLAMA_NUM_CTX="${GUARDIAN_OLLAMA_NUM_CTX:-40960}"
export GUARDIAN_PROVIDER=ollama
export GUARDIAN_MODEL="${MODEL}"
#: Off by default: this measures the finder alone, so recall and noise describe
#: the finder rather than a finder-skeptic pair. The skeptic gets its own run
#: over a frozen finder set.
export GUARDIAN_SKEPTIC="${GUARDIAN_SKEPTIC:-off}"

# --proto/--proto-redir pin the scheme across redirects; -L alone would follow
# one to plain HTTP. It does not remove the trust placed in ollama.com — this
# pipes a remote script into a shell, which is that project's documented install
# path and is stated here rather than papered over.
echo "==> installing ollama"
curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fsSL https://ollama.com/install.sh | sh

# Weights stay resident: a cold load costs about a minute, and a bench of many
# calls would otherwise spend most of its wall clock reloading them.
echo "==> starting ollama"
OLLAMA_KEEP_ALIVE=-1 nohup ollama serve >/tmp/ollama.log 2>&1 &
for _ in $(seq 1 60); do
  curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS http://127.0.0.1:11434/api/version >/dev/null

echo "==> pulling ${MODEL}"
ollama pull "${MODEL}"

# --only-binary :all: restricts *dependency* resolution to wheels, so nothing in
# the dependency tree runs a setup script. It does not block this project from
# building, which is both unavoidable for an editable install and not the risk:
# the whole point of the next line is to execute this checkout's code.
echo "==> installing the harness from this checkout"
pip install -q --only-binary :all: -e '.[guardian]'

# The bench builds a worktree per fixture at its recorded head SHA, so the clone
# needs the history those SHAs live in; `_ensure_full_history` unshallows if the
# clone was shallow.
echo "==> running the benchmark (free: no API keys, no judge)"
python scripts/guardian_bench.py "${@:2}"

echo
echo "Results appended to benchmarks/guardian/results.jsonl — download it and score locally."
echo "Expect pr-144 to raise PromptTruncatedError at this window: it needs ~47k."
