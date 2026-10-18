# A diff check that knows data keys — design

Date: 2026-10-18. Status: proposed (revision 2, after a builder check). Research direction 2 of the October review
("how the graph becomes useful when an agent edits code").

## Why

cgis answers "who calls X". Agents rarely need that answer: on read-only impact
questions Sonnet 5.5 is near the recall ceiling without cgis (exp1–exp3), and with it
the gain is cost (×0.63, exp3), not correctness. The question that matters is whether
the graph stops an agent from breaking someone else's code when it edits.

Two owner-api agent sessions listed ten real breakages from their own edits
(2026-10-18, cross-session interviews; case ids are owner-api PRs):

| Case | What broke | Found by | Call graph would help |
|---|---|---|---|
| #2093 after #2088 | `UserPublic` validators made lenient; `UserAdmin` inherits it and silently changed | local test | yes — subclasses |
| #2066 | `create_vehicle` stored `picture: null`; finance does `data_source["picture"].pop(...)` | review | partly — readers of key `picture` |
| #2071–#2085 | mapping `events.json` declares `event_type` as `keyword` with no `.keyword` subfield; code queries `event_type.keyword`; empty audit on stage, `mapper_parsing_exception` on prod | stage check, then a user | no — string contract code ↔ mapping |
| moved module | a structural test names a module by path string and silently stopped checking | — | yes, if string references are edges (out of zone this round) |
| #2057 | hook switched to `item.path`; a test fake `SimpleNamespace(fspath=...)` lacks it | CI | partly — fake shape |
| #1832 | query passed a fake OpenSearch, real cluster rejected it (500) | stage, prod | no — fake vs real service |
| #2082 | SAQ task span made a child of the request span; tail sampler dropped traces | collector metric | no — timing across systems |
| #1815 | `asyncio.gather` over one AsyncSession | prod | no — race |
| ruff G004 | autofix would break structlog formatting | manual | no — library semantics |
| #2040 | stale lockfile on a branch | CI on main | no |

The call graph covers two or three of ten. The most frequent class the graph cannot
see is **a dependency through a string key**: a document field, an OpenSearch field
path, a mapping declaration, a task name, a Redis key. This design adds that layer and
a diff check that reports it, and measures both against held-out breakages.

## Goals

1. **A data-key layer in the graph, language- and repository-neutral.** A key is a
   string the code uses to address data. The graph records who writes, reads and
   declares each key.
2. **A diff check.** Given a git range, list what else depends on what changed:
   subclasses of changed classes (EXTENDS, already in the graph — verified: owner-api's
   graph holds `models.owner.UserAdmin EXTENDS models.owner.UserPublic`), callers of
   changed functions (existing), and the other readers/writers/declarers of keys the
   diff touches. It also reports a read with no matching declaration where the
   namespace has a declaring file (the #2071 shape).
3. **A measured answer** on held-out breakages, with noise measured on ordinary PRs
   (thresholds below), before any work on an edit-time hook.

## Non-goals

- Fakes that drift from the real object (#2057, #1832). Next candidate layer, not this one.
- Timing, races, external-system behaviour, library semantics, lockfiles.
- The edit-time hook (#588). It reuses this check once the check passes; it is not built here.
- Cross-repository contracts (four frontends generated from OpenAPI, RFC #224).
- OpenAPI as a declaring file. JSON mappings only in this round.
- Go and TypeScript key extraction. The core is language-neutral; Python is the only
  extractor built and measured here.
- **Data flow across functions** (decided, variant A). A namespace is taken only where
  the site itself shows it; a query dict returned by a helper, a `doc["_source"]` read
  after a search, or a project wrapper's parameter does not carry a namespace to the
  key. Such keys are unscoped, and specificity is the only filter for them. If the
  noise threshold fails because of unscoped keys, that is the measured case for a
  data-flow round (variant B), not something to add mid-round.
- Module paths named as strings (the "moved module" case). No key kind for them here;
  that case is out of zone.

## Anti-goals (a failure even if it works)

- **A noisy list.** If the median PR gets more than 5 items, or fewer than half are
  worth a look, agents and reviewers will ignore it. A short precise list beats a
  complete one.
- **Repository-specific code.** Nothing in `src/` may name owner-api, its indices or
  its fields. Ecosystem knowledge lives in data profiles per library.
- **Tuning to the cases that will measure it.** The ten known cases are the
  development set; the verdict comes only from the held-out set.

## Concepts

**Key.** A string literal in a key position, normalised to a dotted path
(`details.brand`, `event_type.keyword`, `picture`). Keys are compared within a
**namespace**; a key with no known namespace is *unscoped*.

**Namespace.** What the key belongs to: an OpenSearch index or alias, a Redis key
prefix, a task registry, a model class. A library profile says how a call names it
(the `index=` argument of an opensearch-py search, say). Where it is known, a
namespace separates keys: `brand` in the events index is not `brand` in the vehicles
index. In real code it is usually not known at the key's own site (owner-api builds
query bodies in helpers and reads `doc["_source"]` after the search), and this round
does not follow data flow to find it, so most keys are **unscoped**: one key node per
path across the whole repository, kept useful by specificity.

**Key node and edges.** One node per (namespace, key), the namespace empty for an
unscoped key. Three relations from code (or a declaring file) to the key: **writes**,
**reads**, **declares**. They are **new** edge types, not reuses of `PERSISTS`,
`CONSUMES`, `DECLARES` or `REFERENCES`: those already feed metrics, drift and impact
traversals, which a reuse would silently change. The node type, the three edge types
and their meaning go into a new core clause in `docs/specs/core/ontology.yml` (as
CV-1..CV-4 did) with conformance cases in `tests/fixtures/conformance/`; approving this
document approves that clause's content (#341). The exact names are the builder's
call.

**Specificity.** How informative a key is: a compound path or a key used at few sites
is specific; `id` or `name` used everywhere is not. The diff check ranks by it and
drops unscoped keys below a threshold.

## How keys are found (Python)

Key positions, each a reads/writes/declares by form:

| Form | Relation |
|---|---|
| `d["k"]`, `d.get("k")`, `d.pop("k")`, `"k" in d` | reads (`pop` also writes) |
| `d["k"] = v`, `d.setdefault("k", ...)`, `{"k": v}` passed to a write call | writes |
| a dict literal inside a query body (`{"term": {"event_type.keyword": x}}`) | reads, path from the inner key |
| a field of a pydantic model / dataclass | declares, namespace = the model |
| `enqueue("task_name", ...)` and similar per the profile | reads/writes of a task-name key |

A dict literal written to storage (an index call's `body=`/`document=`) writes each of
its keys, nested keys as dotted paths. Which calls are reads, writes and queries, and
which argument names the namespace, come from the **library profile** — data only, in
the pack, the same shape as the CV invoker profile:

- opensearch-py: `index`, `search`, `update`, `delete_by_query`, `bulk` — namespace from `index=`;
- redis: `get`, `set`, `hget`, `hset`, `delete` — namespace from the key's literal prefix;
- SAQ (and Celery as a second profile): enqueue by task name;
- pydantic: model fields declare keys of the model's namespace.

A namespace given at the site by a module-level constant (`EVENTS_INDEX = "events"`)
resolves through the existing variable resolution. One that cannot be resolved, or that
lives in another function, leaves the key unscoped.

**Project wrappers.** A project may route storage through its own functions
(owner-api's `doc_index=` helpers, `enqueue_task(...)`). An optional project config
file may list such wrappers and their namespace argument, the same file that may link
mappings to indices. Nothing in this round depends on it: the measured runs use no
project config, on owner-api and on the public repository alike, so both are measured
on the same terms.

**Declaring files.** A JSON mapping is read by a small data pack: each path under
`properties` declares a key, and each subfield under `fields` declares
`<path>.<subfield>` (this is what makes `event_type.keyword` declared or not). The
mapping's namespace is the index it belongs to. The profile says how to link a mapping
file to an index name (a file-name rule such as `mappings/<index>.json`, overridable in
a project config file); an unlinked mapping still declares keys, unscoped.

## The diff check

Input: a git range (default `origin/main...HEAD`). Output: text and JSON, grouped by
what changed:

1. a changed class → its subclasses (transitive);
2. a changed function → its callers (existing impact, capped);
3. a key whose read, write or declaration the diff adds, removes or changes → the
   other sites of that key in its namespace, by relation;
4. "read but not declared": a read of a dotted path whose parent is declared in some
   declaring file while the path itself is declared in none (`event_type` is declared
   in `events.json`, `event_type.keyword` is not). This works on unscoped keys; when a
   namespace is known, the check is narrowed to that namespace's declaring file. The
   reverse is reported too: a declaration the diff removes whose path is still read.

Each item carries file:line and why it is listed. Items are ranked by specificity and
capped (5 shown by default, with a count of the rest). Sites in test code are left out
of the listed items, as impact already does, and shown only as a count ("+N in
tests").

**What it compares.** The head side is the existing graph (fresh-checked via the
freshness machinery). The base side is the old text of the changed files only,
re-parsed with the same extractor, so keys and classes the diff removed are known
without a second ingest. The 10 s budget covers this comparison, not building the
graph; the evaluation harness ingests each case's tree once before timing.
Exposed as a CLI command and an MCP tool; the name is the builder's call (#544
`cgis affected` is a neighbouring idea for tests and may share the entry point).

## Evaluation (pre-registered before building)

**Sets.**
- *Development set:* the ten cases above. Used freely while building.
- *Held-out set:* 10–15 breakages mined from owner-api history by a rule fixed before
  mining: a `fix` commit whose message or PR references an earlier PR it repairs, or
  whose diff reverts lines that PR introduced, within 30 days, excluding the ten known
  cases. A script mines and stores the list (commit pairs) without anyone reading the
  diffs; each pair is labelled in-zone/out-of-zone by an independent agent who sees
  the pair but not the check's output. **In zone** means the breakage travelled through
  a data key or an inheritance link *as a person reading the code would see it*,
  whether or not this design can see it (a key whose namespace only data flow would
  reveal is still in zone). The label does not depend on the design, so a narrow
  design can fail by construction; that is the point. The labelling instructions are
  written and committed in step 0, before mining. The check is run on
  the held-out set once, at the end.
- *Noise set:* 30 merged owner-api PRs from the last 60 days with no follow-up fix,
  drawn by a seeded rule; plus 15 PRs from one public Python repository that uses
  OpenSearch/Elasticsearch or Redis, chosen and pinned before the first run.

**Caught** means the check, run on the breaking PR's diff against its own base, lists
the site that later broke (the reader, the subclass, the undeclared path).

**Pass thresholds.**
1. Held-out recall: at least 3 in 4 of the in-zone cases caught; report the overall
   fraction of all held-out cases too.
2. Noise: median at most 5 listed items per PR on the noise set, and at least half of
   all listed items judged "worth a look" by an independent agent reviewer with the PR
   in front of it.
3. Generality: no owner-api-specific code; the public repository meets threshold 2.
4. Speed: the check finishes in under 10 s on an owner-api diff with a fresh graph.

A miss in the held-out set is reported, not fixed against the same set (a fix makes a
re-run "known misses closed", not recall).

## Order

0. Mine and seal the held-out set and draw the noise set (no building yet).
1. The diff check over what the graph already has: subclasses and callers. Measure on
   the development set — this alone should catch #2093.
2. Key extraction for Python (forms above) with the opensearch-py, redis and pydantic
   profiles; the core clause and conformance cases.
3. JSON mapping pack and "read but not declared".
4. Specificity ranking and caps.
5. Final measurement: held-out recall, noise, public repository, speed. Lab note with
   the result whichever way it goes.

## Calls made in this document (not asked of the maintainer)

Variant A (no data flow) is the maintainer's decision. The rest are the author's calls
after the builder check, open to the maintainer's review: an optional project config
for wrappers that no measured run uses; base side = re-parsed old text of changed
files, 10 s excluding ingest; "in zone" defined independently of the design; new edge
types, not reuses; test sites counted, not listed; module-path strings out of this
round.

## Left to the builder

Node and edge type names (through the ontology clause), the command and tool names,
the profile data format, the specificity formula and threshold, the JSON output schema,
how the mining script recognises "repairs an earlier PR" beyond the rule above, and
which public repository serves the noise check (pinned before the first run).
