# Agent A/B: the same questions with and without cgis (#543)

Does an agent answer a code question better, or cheaper, when the cgis MCP
server is connected? This directory holds the questions, their answer keys and
the results. The runner is `scripts/agent_ab.py`; parsing and scoring live in
`src/cgis/bench/` and are unit-tested on recorded transcripts, so the harness
can change without spending anything.

Design notes and cost estimates: `research/543.md` in the project files.

## What is measured

**Primary, fixed before the first paid run:**

- `recall`: the share of the answer key the answer names (symbols, files and
  literal facts, pooled). `precision`: the share of what it names that is in
  the key or in its `allowed_*` lists.
- `cost_usd`: `total_cost_usd` from the session's closing event.

The hypothesis is that the `cgis` arm reaches at least the control arm's recall
for no more cost on `impact` and `flow` questions, and that `control`-type
questions show what cgis costs when it cannot help.

**Secondary:** tool calls by name, distinct files read and bytes read, turns,
wall time, token usage, and the context the session ends holding
(`residual_context`).

**For #220, not for ranking arms:** `sufficiency` (what the agent did right
after each cgis answer) and `allocation` (the share of files cgis named that
the final answer relied on). Definitions are in the docstring of
`src/cgis/bench/transcript.py`.

The answer key is written from the code and checked by hand, never taken from
the cgis graph: a key derived from the graph would agree with every edge the
resolver gets wrong. Each task's `notes` say how it was checked.

## Arms

| arm | MCP | plugin skills | graph files | cgis / uv on PATH |
|---|---|---|---|---|
| `control` | none (`--strict-mcp-config`, empty config) | no | all deleted | no |
| `cgis` | this checkout's `cgis-mcp` | yes (the plugin minus its `.mcp.json`) | `graph.db` built before the clock starts | no |
| `cgis-instructed` | as `cgis` | as `cgis` | as `cgis` | one line: query cgis first |

`cgis-instructed` exists because the first pilot's `cgis` arm made no cgis call in
12 of 12 sessions: with the server connected and the skill loaded, Sonnet still
went straight to Grep. It stands in for #542's MCP server instructions.

All arms run under `cgis.bench.guard` as a PreToolUse hook, which refuses the
cgis CLI, uv, sqlite3 and any read of `graph.db`/`graph.json` through Bash or
the file tools. Without it the control arm is not a control: codegraph's own
benchmark caught its control agent calling their CLI through Bash in 26 of 28
runs. The same predicate marks a finished run `contaminated` if a blocked call
ever returned output; such runs are counted in the report and left out of the
medians.

All arms allow `Read`, `Grep`, `Glob` and `Bash` (plus `mcp__cgis` in the
treatment arms, which has no server in control) and refuse edits, web access and
sub-agents, under `--permission-mode dontAsk`. Each session starts in a fresh
detached worktree at the task's pinned commit, with `--no-session-persistence`
and `--setting-sources project`, so user settings and earlier sessions do not
reach it.

## Running

```bash
uv run python scripts/agent_ab.py run --dry-run          # commands only, spends nothing
uv run python scripts/agent_ab.py run --task cgis-impact-language-for --runs 2
uv run python scripts/agent_ab.py run --repo owner-api=../ownima-backend
uv run python scripts/agent_ab.py report
```

`--model` defaults to `claude-sonnet-5-5`; `--max-budget-usd` (default 2.00)
caps each session. Every non-dry run is a paid session, so this never runs in
CI. Transcripts are written to `transcripts/` (git-ignored); `results.jsonl`
gets one line per (task, arm, run) and is meant to be committed with the write-up.

## Tasks

| id | type | repo |
|---|---|---|
| `cgis-impact-language-for` | impact | cgis |
| `cgis-flow-ingest-to-sqlite` | flow | cgis |
| `cgis-orientation-call-resolution` | orientation | cgis |
| `cgis-control-sqlite-pragmas` | control | cgis |

This repository's `CLAUDE.md` describes its architecture in some detail. It is
the same in both arms, but it lowers what a graph can add here, which is one
reason owner-api and httpx come next.
