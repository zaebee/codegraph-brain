# Graph freshness: telling a reader the graph is stale (#175 part 2)

Status: approved design
Issue: [#175](https://github.com/zaebee/codegraph-brain/issues/175)

## Problem

A query cannot ask "is this graph current?". Every tool answers from whatever
`graph.db` holds, and a graph ingested before the last edit answers confidently
and wrongly. The user — or the agent — is expected to remember.

The evidence that this needs a general mechanism is that the same need has
already been solved twice, locally, in two recently merged PRs. `OrphanReport`
carries two hand-rolled staleness signals, each documented as "zero here means
re-ingest":

| field | shipped in | what a zero is supposed to mean |
|---|---|---|
| `test_sources` | #415 | the graph predates the `is_test` column |
| `generated_excluded` | #432 / #441 | the graph predates the `is_generated` column |

Both exist only because there is no way to ask the question directly. A third
column would mean a third such counter. Worse, both overload one number with two
jobs — during #441's review `generated_excluded` was found to read zero both when
the graph was stale *and* when every generated class happened to be referenced,
so the documented rule was false in the common case. Per-report proxies for
freshness are the wrong shape, and this spec replaces the need for them.

`stale_files` already exists, but only *inside* the pipeline during an ingest
(`pipeline.py:201, 229`), where it drives cleanup. It never reaches a query.

## Measurement basis

Measured at `52e0de1` against `Ownima/owner-api` at `b7d02fe6` and cgis's own
`src/`.

| Measurement | Value |
|---|---|
| stat-only walk, owner-api `app/` | **5.6 ms** / 814 `.py` files |
| stat-only walk, cgis `src/` | **0.3 ms** / 77 files |
| CLI commands that read the graph | 9 |
| MCP tools that read the graph | 13 |
| Tables in `graph.db` today | `nodes`, `edges`, `files_state` |
| What records the ingested root | **nothing** |
| `files_state` rows after `cgis ingest` | **0** |
| `files_state` rows after `cgis ingest -i` | one per file |
| distinct `nodes.file_path` after either | one per file that produced a node |

The "what records the ingested root" row is the constraint the design turns on:
`files_state` holds a *relative* `file_path` and a content hash, so nothing in
the database says which directory those paths are relative to. A probe cannot
know what to compare against without either storing the root or being handed it.

The two `files_state` rows are why the probe does **not** read that table. A
plain `cgis ingest` populates it only in incremental mode, so on an ordinary
graph it is empty and a probe built on it would report "nothing missing" for
every repository — the silent-success failure this design is otherwise built to
avoid. `nodes.file_path` is populated by every ingest mode and is the honest
answer to "which files is this graph built from".

The first two rows settle the cost question: a stat-only walk is noise next to a
query, so freshness can be checked on every call rather than on request.

## Decisions

### D1 — `ingest_state` table, two rows

```sql
CREATE TABLE IF NOT EXISTS ingest_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

Holding `root` (the absolute path passed to `cgis ingest`) and `ingested_at`
(unix seconds). Written at the end of both full and incremental ingests.

`CREATE TABLE IF NOT EXISTS` is the whole migration; there is no backfill and
none is possible, since the root is not derivable from stored data. Unlike the
`is_generated` migration (#441), this one must **not** touch `files_state`: a
missing `ingest_state` row misreports nothing about the nodes themselves, so
forcing a re-parse would be cost without a reason.

`ingested_at` is written by cgis rather than read from the file system, so it is
comparable with `st_mtime` only to the extent that both come from the same clock.
That holds for the local case this serves; a graph built on one machine and
queried on another is `UNKNOWN` anyway, because its stored root will not exist.

### D2 — three states, not two

`SQLiteStore.freshness(root: str | None = None) -> Freshness`

| state | condition | reported as |
|---|---|---|
| `FRESH` | no file newer than `ingested_at`, none missing | nothing is printed |
| `STALE` | some file is newer, or a tracked file is gone | `N changed, M missing since ingest` |
| `UNKNOWN` | no `ingest_state` row, or the stored root does not exist | `freshness unknowable: <why>` |

The **tracked set** is `SELECT DISTINCT file_path FROM nodes WHERE file_path !=
VIRTUAL_FILE_PATH`, not `files_state` — see the measurement basis. The **on-disk
set** is the same walk `IngestionPipeline` performs, so the two are comparable by
construction; a file the extractors would skip is not counted as missing.

A file that is on disk but tracks no node (an empty `__init__.py`, say — 812
tracked against 814 on disk on owner-api) is never reported missing, and counts
as changed only if its mtime says so. Both are the over-reporting direction of
D3.

`UNKNOWN` is a separate state rather than a pessimistic `STALE` or an optimistic
`FRESH` because #441 established the cost of one value meaning two things: a
signal that looks identical when all is well and when it could not look is the
`generated_excluded == 0` failure again. The two `UNKNOWN` causes — a graph
predating the table, and a root that has moved — carry different remedies and are
reported distinctly.

An explicit `root` argument overrides the stored one. That covers the moved
checkout, a container path, and a CI runner, without making the common case ask
for anything.

### D3 — mtime, and the direction of its error

Staleness is decided by `st_mtime` against `ingested_at`, never by re-hashing.
`touch` on an unmodified file therefore reports `STALE` when nothing changed.

That is the chosen direction, not a defect, and the docstring says so: a
freshness signal that over-reports costs a re-ingest, while one that under-reports
returns a confident wrong answer — the failure this issue exists to remove. The
exact criterion remains available: incremental ingest still compares content
hashes, so acting on the warning is precise even though raising it is not.

### D4 — surfaced on every read, printed only when it matters

One helper, two calling layers:

- **CLI** — a line before the result, emitted only for `STALE` and `UNKNOWN`. A
  fresh graph adds nothing to the output. This matches `_render_orphans`, which
  already warns only when `test_sources == 0`.
- **MCP** — tools returning JSON gain a top-level `"freshness"` key, so the
  payload stays parseable; tools returning text gain a prefixed note, the idiom
  `cgis_find_symbol` already uses (`return note + payload`).

Nine CLI and thirteen MCP call sites, one line each. Adding the field to every
payload rather than exposing a separate `cgis_freshness` tool is the point: a
tool an agent must remember to call is the trap this issue describes, restated.

### D5 — the probe does not read file contents

Asserted by a test, not only by review. A future change that reaches for hashes
would move the per-query cost from milliseconds to seconds without failing
anything otherwise.

## Testing

| Case | Asserts |
|---|---|
| fresh tree | `FRESH`, and the CLI prints no freshness line |
| graph built by a plain (non-incremental) ingest | `FRESH`/`STALE` work — the probe does not depend on `files_state` |
| one file touched | `STALE` with `changed == 1` |
| one tracked file deleted | `STALE` with `missing == 1` |
| graph without `ingest_state` | `UNKNOWN`, distinctly from `STALE`, never `FRESH` |
| stored root no longer exists | `UNKNOWN`, with the moved-root reason |
| explicit `root` given | overrides the stored one |
| probe cost | no file contents are read (D5) |

## Out of scope

- **Lazy / auto ingest.** Making a read command write the graph is a behavioural
  change to every tool and belongs in its own decision. #175 keeps it.
- **Watch mode.**
- **Making a plain `cgis ingest` populate `files_state`.** Measured as empty
  above. It costs the first `-i` run after a plain ingest a full re-parse, which
  is a real if benign inefficiency, and it is not this design's to fix — the
  probe deliberately does not read that table.
- **Retiring `test_sources` and `generated_excluded`.** They can stop doubling as
  freshness probes once a general signal exists, but changing what two shipped
  fields mean is a separate, reviewable diff.
