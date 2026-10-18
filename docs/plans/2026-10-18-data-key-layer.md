# Data-key layer and diff check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Given a git range, cgis lists what else depends on what the diff changed —
subclasses, callers, and the other readers/writers/declarers of the data keys it
touched — and the result is measured on held-out owner-api breakages.

**Architecture:** Extractors emit raw key edges (`raw_key:<namespace>|<path>`) the
resolver turns into virtual `DATA_KEY` nodes, as it does for unresolved calls. Python
finds keys in dict access, query-DSL literals and model fields; a new `data` pack reads
JSON/YAML files as key declarations. A `diffcheck` query package compares the head
graph with the re-parsed old text of changed files and ranks what it lists.

**Tech Stack:** Python 3.12, tree-sitter-python, pyyaml (already a dependency),
SQLite store, typer CLI, FastMCP, pytest, strict mypy.

**Spec:** `docs/specs/2026-10-18-data-key-layer-design.md` (rev 2 + d120ff5).

## Global Constraints

- No data flow across functions (variant A): a namespace comes only from the key's own site; otherwise the key is unscoped.
- No owner-api names, indices or fields anywhere in `src/`. Ecosystem knowledge is data in pack profiles. An optional project config may list wrappers; no measured run uses one.
- New node type `DATA_KEY` and edge types `READS_KEY`, `WRITES_KEY`, `DECLARES_KEY`, defined by an ontology clause (DK-1..DK-3) with conformance cases. Do not reuse `PERSISTS`, `CONSUMES`, `DECLARES`, `REFERENCES`.
- Python is the only key extractor; JSON and YAML are the only declaring files.
- Diff check: at most 5 items shown by default plus a count of the rest; test sites counted ("+N in tests"), never listed; under 10 s on an owner-api diff, graph build excluded.
- Two keys match when their paths are equal and their namespaces are equal or either is unscoped.
- The held-out set is mined and sealed (Task 0) before any product code; its diffs are not read until Task 6; a held-out miss is reported, not fixed against the same set.
- Pass thresholds (Task 6): held-out in-zone recall ≥ 3 of 4; noise median ≤ 5 listed items per PR and ≥ 50% "worth a look"; same on the public repository; < 10 s.
- Ingest cost: full owner-api ingest ≤ 1.15× main (median of 5 clean interleaved pairs, fresh databases, never reusing a db between runs); checked after Task 3 and after Task 4, not only at the end. Over budget → profile and fix before the next task.
- Worktree `/home/zaebee/projects/ownima/cgb-keys`; `unset VIRTUAL_ENV` before `uv run`; run touched test files plus `tests/self_parsing` when `src/` changes, never the full suite.
- Commits end `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; chain `&&`.

## Review Focus

1. A JSON/YAML file that is not data — a 2 MB lockfile, minified JSON, a malformed YAML — must not slow or break ingest; it is skipped or capped. → Task 4 `test_large_and_lock_files_declare_nothing`, `test_malformed_yaml_is_skipped_not_raised`.
2. Very common keys (`id`, `name`, `type`, `data`) must never be listed as unscoped matches. → Task 5 `test_common_unscoped_keys_are_not_listed`.
3. A diff that deletes, adds or renames a file must still be checked (old path re-parsed, no crash). → Task 1 `test_deleted_and_renamed_files_are_checked`.
4. A path that is not a git repository, or a range git rejects, gives a clear error and a non-zero exit, not a traceback. → Task 1 `test_bad_range_is_a_clear_error`.
5. Dynamic keys (`d[f"x_{i}"]`, `d[name]`, `d[0]`) produce no key edge. → Task 3 `test_dynamic_keys_make_no_edge`.

---

### Task 0: Evaluation sets, sealed before building

**Files:**
- Create: `scripts/data_keys/mine_heldout.py`, `scripts/data_keys/draw_noise.py`
- Create: `benchmarks/data_keys/dev_cases.yaml`, `benchmarks/data_keys/labelling.md`
- Create (outputs, committed): `benchmarks/data_keys/heldout.json`, `benchmarks/data_keys/noise.json`
- Test: `tests/unit/test_data_keys_sets.py`

**Interfaces:**
- Produces: `repairs(fix: PR, earlier: Sequence[PR]) -> PR | None` in `mine_heldout.py`
  (the mining rule, pure); `PR` is a frozen dataclass `(number: int, merged_at: datetime,
  merge_sha: str, title: str, body: str)`; JSON outputs with
  `{"breaking": {"pr", "base_sha", "merge_sha"}, "fix": {"pr", "merge_sha"}}` records
  (heldout) and `{"pr", "base_sha", "merge_sha"}` (noise).

- [ ] **Step 1: Failing tests** for the rule: `test_a_fix_naming_an_earlier_pr_within_30_days_repairs_it`
  (fix title `fix(x): ...` whose body says `#2066` merged 5 days earlier → returns 2066);
  `test_a_reference_older_than_30_days_is_not_a_repair`; `test_a_non_fix_commit_repairs_nothing`
  (`feat:`/`chore:` titles); `test_dev_cases_are_excluded` (2093, 2088, 2066, 2071, 2075,
  2079, 2085, 2057, 1832, 2082, 2096, 2098, 1815, 2040 never appear as breaking or fix);
  `test_noise_draw_is_seeded_and_excludes_breaking_prs`.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_data_keys_sets.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** both scripts. Mining reads merged PRs of `Ownima/owner-api`
  via `gh pr list --state merged --json number,mergedAt,mergeCommit,title,body --limit 1000`;
  rule: title starts `fix` and body or title references `#N` of a PR merged 0–30 days
  before; keep up to 15, newest first. The script prints only counts and PR numbers,
  never diffs. Noise: 30 merged PRs from the last 60 days that are neither side of any
  repair pair, `random.Random(2026).sample`. `base_sha` = first parent of `merge_sha`.
  `dev_cases.yaml`: the eleven spec cases, each with `breaking_pr` (or cgis commit),
  the base and merge sha, and `expected_site` (file + symbol or key the check must list)
  from the spec table; cgis #701's `expected_site` is the `cross_file_metadata`
  declaration in `src/cgis/languages/__init__.py`. `labelling.md`: the in-zone rule
  verbatim from the spec (Evaluation → Sets) and the instruction to name the broken site.
- [ ] **Step 4: Run** the tests (PASS), then the scripts once; commit their outputs.
  Expected: 10–15 held-out pairs, 30 noise PRs. Also pin the public repository for the
  noise check in `benchmarks/data_keys/noise.json` (`public_repo`, sha, 15 PRs by the
  same seeded rule): a Python repository using opensearch-py/elasticsearch-py or redis.
- [ ] **Step 5: Commit** `test(data-keys): seal the held-out breakages and draw the noise sets`.

### Task 1: Diff check over subclasses and callers

**Files:**
- Create: `src/cgis/query/diffcheck/__init__.py`, `src/cgis/query/diffcheck/hunks.py`,
  `src/cgis/query/diffcheck/check.py`
- Modify: `src/cgis/cli.py` (command `diff-check`), `src/cgis/api/mcp_server.py` (tool `cgis_diff_check`, `@refreshes_graph`)
- Create: `scripts/data_keys/evaluate.py`
- Test: `tests/unit/test_diffcheck.py`

**Interfaces:**
- Produces in `hunks.py`: `FileChange` frozen dataclass `(old_path: str | None, new_path: str | None,
  old_lines: frozenset[int], new_lines: frozenset[int])`; `changed_files(repo: Path, base: str, head: str) -> list[FileChange]`
  (raises `DiffError` with git's message on a bad repo or range).
- Produces in `check.py`: `DiffItem` frozen dataclass `(kind: str, changed: str, site: str,
  file: str, line: int | None, reason: str, score: float)` with `kind` one of
  `subclass`, `caller` (Task 5 adds `key-reader`, `key-writer`, `key-declarer`,
  `undeclared-read`, `removed-declaration`); `DiffReport` `(items: list[DiffItem],
  hidden: int, in_tests: int, changed_symbols: list[str], changed_keys: list[str])`;
  `run_diff_check(store: SQLiteStore, repo: Path, base: str, head: str = "HEAD", cap: int = 5) -> DiffReport`.
- Head side: graph nodes whose `[start_line, end_line]` meets `new_lines` of their file.
  Base side: the old text (`git show base:old_path`) parsed with `cgis.languages.extractors`
  for its extension; a base node whose id is absent from the graph is a removed symbol.
  Changed classes → transitive incoming `EXTENDS`; changed functions/methods → incoming
  `CALLS`, depth 1. Test-file sites (`Node.is_test`) go to `in_tests`.
- `evaluate.py`: for each case in a sets file, ingest the tree at `merge_sha` into a
  case db, run the check for `base_sha..merge_sha`, record whether `expected_site`
  is among *all* items (shown or hidden) and whether it is shown; write a JSON result.

- [ ] **Step 1: Failing tests** on a tiny git repo built in `tmp_path`:
  `test_a_changed_base_class_lists_its_subclasses` (B extends A in another file; edit A → item `subclass` for B);
  `test_a_changed_function_lists_its_callers`; `test_a_removed_function_still_lists_its_callers`
  (base re-parse); `test_test_sites_are_counted_not_listed`; `test_cap_shows_five_and_counts_the_rest`;
  `test_deleted_and_renamed_files_are_checked`; `test_bad_range_is_a_clear_error`
  (CLI exit code 2, message names the range).
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_diffcheck.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** hunks, check, CLI (`cgis diff-check --db --repo --base origin/main --head HEAD --cap 5 --format text|json`), MCP tool, `evaluate.py`.
- [ ] **Step 4: Run** the tests and `tests/self_parsing`; then `evaluate.py` on `dev_cases.yaml`.
  Expected: tests pass; the #2093 case's `expected_site` (`models.owner.UserAdmin`) is listed.
- [ ] **Step 5: Commit** `feat(query): cgis diff-check lists subclasses and callers of what a diff changed`.

### Task 2: Key nodes in the core

**Files:**
- Modify: `src/cgis/core/models.py` (`NodeType.DATA_KEY`, `EdgeType.READS_KEY/WRITES_KEY/DECLARES_KEY`, `RAW_KEY_PREFIX = "raw_key:"`)
- Modify: `src/cgis/resolver/engine.py` (raw key edges → `key:<namespace>|<path>` targets and virtual `DATA_KEY` nodes)
- Modify: `docs/specs/core/ontology.yml` (clauses DK-1 writes, DK-2 reads, DK-3 declares; text from the spec's Concepts section)
- Create: `src/cgis/core/keys.py` (`key_id(namespace: str, path: str) -> str`, `parse_key_id(id: str) -> tuple[str, str]`, `keys_match(a: str, b: str) -> bool`)
- Test: `tests/unit/test_keys_core.py`; conformance cases `tests/fixtures/conformance/python/dk1-*`…`dk3-*` (`xfail` until Task 3), `go/` and `typescript/` DK cases `xfail` (coverage test)

**Interfaces:**
- Raw form: edge target `raw_key:<namespace>|<path>`, namespace empty for unscoped, or
  `@<NAME>` for a module-level constant to resolve. The resolver resolves `@NAME` through
  global symbols to a `VARIABLE` node and reads its `metadata["str_value"]` (Task 3 writes
  it); unresolved → unscoped. Virtual node: `id=key_id(ns, path)`, `type=DATA_KEY`,
  `name=path`, `metadata={"namespace": ns, "path": path}`, `file_path=VIRTUAL_FILE_PATH`.
- `keys_match`: equal paths and (equal namespaces or either empty).

- [ ] **Step 1: Failing tests:** `test_key_ids_round_trip`, `test_keys_match_rule` (scoped/scoped equal, scoped/unscoped, different namespaces don't);
  `test_raw_key_edges_become_key_nodes`; `test_a_constant_namespace_resolves_through_str_value`;
  `test_an_unresolvable_namespace_is_unscoped`; `test_key_nodes_do_not_count_as_unresolved`
  (`cgis validate` ratio unchanged by key edges).
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement** types, `keys.py`, resolver branch, ontology clauses, conformance stubs.
- [ ] **Step 4: Run** `tests/unit/test_keys_core.py tests/unit/test_resolver.py tests/integration/test_conformance.py tests/self_parsing`. Expected: PASS (DK cases XFAIL).
- [ ] **Step 5: Commit** `feat(core): data keys — node, three edges, ontology DK-1..DK-3`.

### Task 3: Python finds keys

**Files:**
- Create: `src/cgis/languages/python/_keys.py`
- Modify: `src/cgis/languages/python/profile.py` (`KEY_PROFILE`), `src/cgis/languages/python/extractor.py` (call `key_edges` after the walk; record `str_value` on module-level `NAME = "literal"` variables)
- Test: `tests/unit/test_python_keys.py`; promote the Python DK conformance cases

**Interfaces:**
- Produces: `key_edges(root: BaseNode, code: bytes, file_path: str, owners: Callable[[BaseNode], str | None]) -> list[Edge]`
  — `owners` maps a node to its enclosing function id, else class, else module (the walk's existing owner rule).
- `KEY_PROFILE` (data): `query_operators` (`term`, `terms`, `match`, `match_phrase`,
  `range`, `prefix`, `wildcard`, `exists`, `sort`) — a dict literal keyed by an operator
  reads its child's keys as field paths, or the string under a child `"field"` key;
  `storage_calls` method names with their namespace keyword (`search`, `index`, `update`,
  `delete_by_query`, `count` → `index`); `write_arguments` (`body`, `document`, `doc`);
  `model_bases` (`BaseModel`, `SQLModel`); `redis_methods` (`get`, `set`, `hget`, `hset`,
  `delete`, `exists`) → namespace `redis`; `task_methods` (`enqueue`) → namespace `task`.
- Forms → relation: `d["k"]` load READS, store or `del` WRITES; `.get("k")` READS,
  `.pop("k")` READS and WRITES, `.setdefault("k")` WRITES; `"k" in d` READS; a dict
  literal passed as a `write_arguments` value of a `storage_calls` call WRITES each key
  (nested as dotted paths), namespace from that call's `index=` literal or constant;
  annotated attributes of a class whose direct base's last segment is in `model_bases`
  DECLARES, namespace = the class FQN. Only plain string literals are keys.

- [ ] **Step 1: Failing tests:** `test_subscript_reads_and_writes`, `test_get_pop_setdefault`,
  `test_membership_reads`, `test_query_operator_children_are_field_reads`
  (`{"terms": {"event_type.keyword": x}}` → READS `event_type.keyword`, unscoped),
  `test_aggregation_field_value_is_read` (`{"terms": {"field": "brand"}}` → READS `brand`),
  `test_storage_write_literal_writes_nested_paths` (`client.index(index="vehicles", document={"details": {"brand": b}})` → WRITES `vehicles|details.brand`),
  `test_constant_index_namespace` (`index=VEHICLES` → `@VEHICLES`), `test_model_fields_declare`,
  `test_dynamic_keys_make_no_edge`, `test_key_edges_belong_to_the_enclosing_function`.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_python_keys.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `_keys.py` (one tree-sitter query per file, as `_values._bodies` does), profile, wiring, `str_value`.
- [ ] **Step 4: Run** the unit file, conformance, graph identity (re-snapshot if key nodes change it, every change a key edge or node), `tests/self_parsing` (calibration bands: re-measure, and ask the maintainer before moving any).
  Expected: PASS. Then the ingest-cost check (Global Constraints) on owner-api at the pinned sha: ≤ 1.15× main; record node/edge counts and key-edge count.
- [ ] **Step 5: Commit** `feat(python): dict access, query literals and model fields are data keys`.

### Task 4: JSON and YAML declare keys

**Files:**
- Create: `src/cgis/languages/data/__init__.py` (`PACK`), `src/cgis/languages/data/extractor.py`, `src/cgis/languages/data/profile.py`
- Modify: `src/cgis/languages/__init__.py` (one `Manifest`: name `data`, title `JSON and YAML`, extensions `.json`, `.yaml`, `.yml`)
- Modify: coverage claims in `src/cgis/api/server_instructions.py`, `plugin/skills/cgis/SKILL.md`, README (they must name every manifest title; say these files contribute key declarations only)
- Test: `tests/unit/test_data_pack.py`

**Interfaces:**
- The extractor emits one FILE node and `DECLARES_KEY` raw edges for every key path
  (dotted, lists skipped), unscoped by default. OpenSearch mapping shape (profile): a
  document with `mappings.properties` or top-level `properties` declares each property
  path and `<path>.<subfield>` for each entry under `fields`; namespace from the file-name
  rule `mappings/<index>.json` (profile pattern), else unscoped.
- Skips (declares nothing, FILE node kept): files over 256 KB, names matching
  `*lock*` / `*.min.json`, `package.json`, `tsconfig*.json`, paths under `node_modules`
  or a dot-directory; at most 2 000 keys per file; malformed content logs one warning.

- [ ] **Step 1: Failing tests:** `test_mapping_declares_property_paths_and_subfields`
  (`event_type: {type: keyword}` declares `event_type`, not `event_type.keyword`; with
  `fields: {keyword: ...}` it declares both), `test_mapping_namespace_from_file_name`,
  `test_plain_yaml_declares_unscoped_paths`, `test_large_and_lock_files_declare_nothing`,
  `test_malformed_yaml_is_skipped_not_raised`, `test_coverage_claims_name_the_data_pack`.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement** the pack (minimal `LanguagePack`: `is_test` via `is_test_path`, `scope_of` = path rule, empty `index_facts`, `classify` → UNKNOWN, default `ResolutionRules`), manifest, claims.
- [ ] **Step 4: Run** the unit file, `tests/unit/test_coverage_claims.py tests/unit/test_languages.py tests/integration/test_conformance.py tests/self_parsing`. Expected: PASS. Then the ingest-cost check again with the data pack on, plus the number of JSON/YAML files read versus skipped on owner-api.
- [ ] **Step 5: Commit** `feat(languages): JSON and YAML files declare data keys`.

### Task 5: The diff check reports keys

**Files:**
- Modify: `src/cgis/query/diffcheck/check.py`
- Test: `tests/unit/test_diffcheck_keys.py`

**Interfaces:**
- Consumes: Task 2 `keys_match`, `parse_key_id`; Task 1 `DiffItem`, `DiffReport`.
- Changed keys: key edges whose `line_number` falls in `new_lines` (head graph) or in
  `old_lines` (re-parsed base). For each, items for the other sites of every matching
  key node, by relation (`key-reader`, `key-writer`, `key-declarer`). `undeclared-read`:
  a read of a dotted path whose parent path is declared somewhere and the path nowhere
  (narrowed to the namespace's declarations when scoped). `removed-declaration`: a
  declaration only on the base side whose path is still read.
- Specificity (initial values, tune on the dev set only): score = segments in path +
  1 if scoped − log2(files referencing the key); an unscoped single-segment key is
  dropped when it is in `{"id", "name", "type", "data", "value", "key", "items", "result"}`
  or referenced from more than 8 files. Items sort by score, then kind.

- [ ] **Step 1: Failing tests:** `test_a_changed_write_lists_other_readers` (the #2066 shape:
  `doc["picture"] = None` changed → reader `data_source["picture"].pop(...)` in another file listed);
  `test_undeclared_read_is_reported` (the #2071 shape: mapping declares `event_type` without
  `fields`, code reads `event_type.keyword`); `test_removed_declaration_still_read_is_reported`;
  `test_common_unscoped_keys_are_not_listed`; `test_scoped_keys_in_other_namespaces_do_not_match`.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the file and `tests/unit/test_diffcheck.py`, then `evaluate.py` on the dev set.
  Expected: PASS; #2066 and #2071 dev cases caught; the cgis #701 case is a known miss.
- [ ] **Step 5: Commit** `feat(query): diff-check lists the other sites of changed data keys`.

### Task 6: Final measurement and write-up

**Files:**
- Create: `docs/lab-notes/2026-10-xx-data-key-diff-check.md`, results under `benchmarks/data_keys/results/`

- [ ] **Step 1:** Have an independent agent label `heldout.json` per `labelling.md` (in/out of zone, broken site), without the check's output; commit the labels.
- [ ] **Step 2:** Run `evaluate.py` on the held-out set once. Record caught/in-zone per case.
- [ ] **Step 3:** Run the check on the 30 owner-api and 15 public noise PRs; have an independent agent judge each listed item "worth a look" or not with the PR in front of it; record medians.
- [ ] **Step 4:** Time the check on five owner-api diffs with a fresh graph (median), and the final ingest-cost pair run (threshold 5 in the spec).
- [ ] **Step 5:** Write the lab note: thresholds, results, misses with causes, and what the misses argue for (variant B data flow, registry completeness, fakes). Commit and open the PR.
