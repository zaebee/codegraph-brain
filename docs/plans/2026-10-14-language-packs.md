# Language Packs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a language one package behind a `LanguagePack` contract, and enforce cgis's topology through forbidden edges, without changing any Python or TS graph along the way.

**Architecture:** Step 0, detailed below, lands the safety nets the refactor relies on:
- a checked-in multi-language corpus whose stored graph is snapshotted;
- a forbidden-edge gate with exact allowlists;
- observe-only plane quotients.

Steps 1–7 move code under those nets, one PR each. Each step gets its own detailed plan when it starts; this document outlines them.

**Tech Stack:** Python 3.12, tree-sitter, SQLite, pytest, ruff, mypy strict, interrogate.

**Spec:** `docs/specs/2026-10-14-language-packs-design.md` (PR #593, revision 3).

## Global Constraints

- `make format && make lint && make type-check && make pytest && make doc-coverage` passes before every commit; gate with `&&`, never `;` or a pipe on the gating command.
- mypy strict on `src/`; docstring coverage ≥ 90% (interrogate); Sonar cognitive complexity ≤ 15 per function.
- Steps 1–5 leave every stored Python/TS graph's node and edge sets identical. The step-0 snapshot never changes before step 7, and its Python/TS part never changes at all.
- Drift tolerances only go down (`docs/ontology/tolerances.lock`, `test_tolerances_never_exceed_the_lock`). The god-object baseline (`test_god_object_baseline_not_exceeded`) is not raised to make a step pass.
- Test-count floor: raise `.github/test-count-baseline` in the same PR when the suite grows past it by 150 or more.
- Every PR gets an independent reviewer with execution tools before merge.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; PR bodies end with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

## Review Focus

- A snapshot that embeds the temp directory the corpus was copied to must still compare equal on another machine. Every absolute path is normalised to `<ROOT>` (Task 1 test `test_paths_are_normalised`).
- Two runs of the same ingest must give byte-identical dumps, whatever the hash seed and file mtimes. `ingested_at` is dropped, and keys and rows are sorted (Task 3 runs the snapshot under two `PYTHONHASHSEED` values).
- An allowlist entry for an edge that has disappeared must fail, not linger (Task 4 `test_forbidden_edges_match_the_allowlist_exactly` compares for equality, not "≤").
- Edges to virtual or unresolved nodes must not count as forbidden edges. Only INTERNAL targets whose FILE module is known are attributed (Task 4 `test_external_targets_are_never_attributed`).
- The configs-above-root case needs a `.git` marker that cannot be checked in. The snapshot command creates it in its temp copy (Task 2 corpus contract, Task 3).

---

## Step 0 — Measure and guard (one PR)

### Task 1: Canonical graph dump and diff

**Files:**
- Create: `scripts/graph_identity.py`
- Test: `tests/unit/test_graph_identity_dump.py`

**Interfaces:**
- Produces:
  - `dump(db_path: Path, root: Path) -> dict[str, object]`: the canonical, normalised content of one stored graph;
  - `diff(before: Mapping[str, object], after: Mapping[str, object]) -> list[str]`: human-readable differences, empty when equal;
  - CLI `python scripts/graph_identity.py diff <db-before> <root-before> <db-after> <root-after>`, which exits 1 and prints the diff when the graphs differ.

- [ ] **Step 1: Write the failing tests**

```python
"""The canonical graph dump the identity net compares (spec: Migration, safety net)."""

import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import graph_identity as gi  # noqa: E402

from cgis.extractors.registry import build_extractors  # noqa: E402
from cgis.pipeline import IngestionPipeline  # noqa: E402
from cgis.storage.sqlite_store import SQLiteStore  # noqa: E402


def _ingest(root: Path, db: Path) -> None:
    with SQLiteStore(str(db)) as store:
        IngestionPipeline(build_extractors([])).run(str(root), store=store)


def _repo(root: Path) -> Path:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("def f():\n    g()\n\ndef g():\n    pass\n")
    return root


def test_dump_carries_nodes_edges_and_ingest_state(tmp_path: Path) -> None:
    root = _repo(tmp_path / "r")
    _ingest(root, tmp_path / "g.db")
    out = gi.dump(tmp_path / "g.db", root)

    assert {n["id"] for n in out["nodes"]} >= {"pkg.a", "pkg.a.f", "pkg.a.g"}
    call = next(e for e in out["edges"] if e["type"] == "CALLS")
    assert (call["source"], call["target"]) == ("pkg.a.f", "pkg.a.g")
    assert "ingested_at" not in out["ingest_state"]
    assert set(out["nodes"][0]) >= {"metadata", "language", "is_test", "is_generated"}


def test_paths_are_normalised(tmp_path: Path) -> None:
    root = _repo(tmp_path / "r")
    _ingest(root, tmp_path / "g.db")
    text = json.dumps(gi.dump(tmp_path / "g.db", root))

    assert str(root) not in text
    assert "<ROOT>" in text


def test_the_same_ingest_twice_dumps_identically(tmp_path: Path) -> None:
    a, b = _repo(tmp_path / "a"), _repo(tmp_path / "b")
    _ingest(a, tmp_path / "a.db")
    _ingest(b, tmp_path / "b.db")

    assert gi.diff(gi.dump(tmp_path / "a.db", a), gi.dump(tmp_path / "b.db", b)) == []


def test_diff_names_a_changed_edge(tmp_path: Path) -> None:
    root = _repo(tmp_path / "r")
    _ingest(root, tmp_path / "g.db")
    before = gi.dump(tmp_path / "g.db", root)
    with sqlite3.connect(tmp_path / "g.db") as conn:
        conn.execute("UPDATE edges SET confidence = 0.5 WHERE type = 'CALLS'")
    after = gi.dump(tmp_path / "g.db", root)

    lines = gi.diff(before, after)
    assert lines, "a changed confidence must show"
    assert all(line.startswith("edges ") for line in lines), "only edge rows changed"
    assert any("pkg.a.f" in line and "0.5" in line for line in lines)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/test_graph_identity_dump.py -q`
Expected: FAIL. `ModuleNotFoundError: No module named 'graph_identity'`.

- [ ] **Step 3: Implement `scripts/graph_identity.py`**

```python
"""Canonical dump and diff of a stored cgis graph — the language-packs identity net.

Steps 1–5 of docs/specs/2026-10-14-language-packs-design.md move code without
changing behaviour. This is how that is checked: the same input ingested by two
cgis versions must dump identically. Absolute paths become `<ROOT>` and the
mtime-derived `ingested_at` is dropped, so a dump compares across machines and
temp directories.
"""

import argparse
import json
import sqlite3
import sys
from collections.abc import Mapping
from pathlib import Path

_NODE_COLUMNS = (
    "id", "type", "name", "file_path", "start_line", "end_line", "language",
    "namespace", "is_test", "is_generated", "metadata",
)  # fmt: skip
_EDGE_COLUMNS = (
    "id", "source", "target", "type", "weight", "confidence", "context",
    "file_path", "line_number", "type_only",
)  # fmt: skip
_VOLATILE_STATE = frozenset({"ingested_at"})


def _normalise(value: object, root: str) -> object:
    """`value` with the corpus root replaced by `<ROOT>`, JSON text parsed to data."""
    if isinstance(value, str):
        text = value.replace(root, "<ROOT>")
        if text[:1] in "[{":
            try:
                return _normalise(json.loads(text), "\0")
            except ValueError:
                return text
        return text
    if isinstance(value, list):
        return [_normalise(v, root) for v in value]
    if isinstance(value, dict):
        return {k: _normalise(v, root) for k, v in sorted(value.items())}
    return value


def _rows(conn: sqlite3.Connection, table: str, columns: tuple[str, ...], root: str) -> list[dict[str, object]]:
    """Every row of `table` as a normalised dict, sorted by id."""
    query = f"SELECT {', '.join(columns)} FROM {table} ORDER BY id"  # table/columns are constants
    return [
        {col: _normalise(val, root) for col, val in zip(columns, row, strict=True)}
        for row in conn.execute(query)
    ]


def dump(db_path: Path, root: Path) -> dict[str, object]:
    """The canonical content of one stored graph, with `root` normalised away."""
    prefix = str(root.resolve())
    with sqlite3.connect(db_path) as conn:
        state = {
            key: _normalise(value, prefix)
            for key, value in conn.execute("SELECT key, value FROM ingest_state ORDER BY key")
            if key not in _VOLATILE_STATE
        }
        return {
            "nodes": _rows(conn, "nodes", _NODE_COLUMNS, prefix),
            "edges": _rows(conn, "edges", _EDGE_COLUMNS, prefix),
            "ingest_state": state,
        }


def diff(before: Mapping[str, object], after: Mapping[str, object]) -> list[str]:
    """One line per row that differs between two dumps, keyed by table and id."""
    lines: list[str] = []
    for table in ("nodes", "edges"):
        old = {row["id"]: row for row in before[table]}  # type: ignore[index, union-attr]
        new = {row["id"]: row for row in after[table]}  # type: ignore[index, union-attr]
        for key in sorted(old.keys() | new.keys()):
            if old.get(key) != new.get(key):
                lines.append(f"{table} {key}: {json.dumps(old.get(key))} → {json.dumps(new.get(key))}")
    if before["ingest_state"] != after["ingest_state"]:
        lines.append(f"ingest_state: {before['ingest_state']} → {after['ingest_state']}")
    return lines


def main(argv: list[str] | None = None) -> int:
    """`diff <db-a> <root-a> <db-b> <root-b>`: exit 1 and print when the graphs differ."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("diff")
    for name in ("db_a", "root_a", "db_b", "root_b"):
        d.add_argument(name, type=Path)
    args = parser.parse_args(argv)
    lines = diff(dump(args.db_a, args.root_a), dump(args.db_b, args.root_b))
    print("\n".join(lines) if lines else "identical")
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_graph_identity_dump.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/graph_identity.py tests/unit/test_graph_identity_dump.py
git commit -m "feat(bench): canonical dump and diff of a stored graph — the language-packs identity net

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2: The identity corpus

**Files:**
- Create: the tree under `tests/fixtures/graph_identity/corpus/`, listed below
- Test: none of its own; Task 3 ingests it.

**Interfaces:**
- Produces: a corpus with two ingest roots that Task 3 snapshots:
  - `corpus/` (whole);
  - `corpus/ts/apps/web/src` (a sub-directory root, which exercises configs above the root).

  The snapshot command adds `.git` at the copy's root.

- [ ] **Step 1: Write the Python half**

`corpus/py/pyproject.toml`:
```toml
[project]
name = "shop"
dependencies = ["fastapi>=0.110", "alembic>=1.13"]
```

`corpus/py/src/shop/__init__.py`:
```python
from .service import *  # noqa: F403 — star-import chain under test
```

`corpus/py/src/shop/repo.py`:
```python
from abc import ABC, abstractmethod


class Repo(ABC):
    @abstractmethod
    def save(self, item: str) -> None: ...


class SqlRepo(Repo):
    def save(self, item: str) -> None:
        print(item)
```

`corpus/py/src/shop/deps.py`:
```python
from typing import Annotated

from fastapi import Depends

from .repo import Repo, SqlRepo


def get_repo() -> Repo:
    return SqlRepo()


RepoDep = Annotated[Repo, Depends(get_repo)]
```

`corpus/py/src/shop/service.py`:
```python
from .deps import RepoDep
from .repo import Repo

__all__ = ["OrderService", "place"]


class OrderService:
    def __init__(self, repo: RepoDep) -> None:
        self.repo = repo

    def place(self, item: str) -> None:
        self.repo.save(item)


def place(repo: Repo, item: str) -> None:
    repo.save(item)
```

`corpus/py/src/alembic/env.py` (a local package named like a declared dependency, the collision case):
```python
def run_migrations() -> None:
    pass
```

`corpus/py/lib/legacy/tool.py`:
```python
from shop import place


def main() -> None:
    place(None, "x")
```

`corpus/py/tests/test_service.py`:
```python
from shop.service import place


def test_place() -> None:
    place(None, "x")
```

- [ ] **Step 2: Write the TypeScript half**

`corpus/ts/package.json`:
```json
{"name": "mono", "private": true, "workspaces": ["packages/*"], "dependencies": {"react": "^19.0.0"}}
```

`corpus/ts/pnpm-workspace.yaml`:
```yaml
packages:
  - "apps/*"
  - "!**/fixtures/**"
```

`corpus/ts/tsconfig.json`:
```json
{"compilerOptions": {"baseUrl": ".", "paths": {"@app/*": ["apps/web/src/*"]}}}
```

`corpus/ts/packages/lib/package.json`:
```json
{"name": "@x/lib", "version": "1.0.0"}
```

`corpus/ts/packages/lib/index.ts`:
```ts
export function libFn(): number {
  return 1;
}
```

`corpus/ts/apps/web/package.json`:
```json
{"name": "web", "dependencies": {"@x/lib": "^1.0.0", "react": "^19.0.0", "lodash.debounce": "^4.0.8"}}
```

`corpus/ts/apps/web/src/util.ts`:
```ts
export function helper(n: number): number {
  return n + 1;
}
```

`corpus/ts/apps/web/src/types.d.ts`:
```ts
export interface Shape {
  area(): number;
}
```

`corpus/ts/apps/web/src/main.ts`:
```ts
import { randomUUID } from "node:crypto";
import debounce from "lodash.debounce";
import { useState } from "react";
import { libFn } from "@x/lib";
import { helper } from "@app/util";

function setTimeout(fn: () => void): void {
  fn();
}

export function main(): number {
  useState(0);
  randomUUID();
  setTimeout(() => debounce(() => helper(libFn()), 10));
  return libFn();
}
```

- [ ] **Step 3: Check the corpus ingests cleanly under today's cgis**

Run: `uv run cgis ingest tests/fixtures/graph_identity/corpus -o /tmp/claude-1001/corpus-check.db`
Expected: "✅ Success!" and no traceback. Then check for each case:

- `@x.lib` is UNKNOWN;
- `react` is EXTERNAL;
- `node:crypto` is STDLIB;
- `shop.repo.Repo.save → shop.repo.SqlRepo.save` is a DISPATCHES edge;
- `shop.service.OrderService.place` CALLS `shop.repo.Repo.save` through the `RepoDep` alias.

```bash
sqlite3 /tmp/claude-1001/corpus-check.db "select id, namespace from nodes where id in ('@x.lib','react','node:crypto')"
sqlite3 /tmp/claude-1001/corpus-check.db "select source, target, type from edges where type in ('DISPATCHES','CALLS') and source like 'shop.%'"
```

If a case does not appear, adjust that file in the corpus until it does, so that every case listed in the spec is present in the graph. Record which ones needed adjusting in the commit message.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/graph_identity/corpus
git commit -m "test(fixtures): the language-packs identity corpus — Python DI/ABC/star/layouts, TS monorepo/pnpm/tsconfig/node:

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 3: Snapshot test under two hash seeds

**Files:**
- Modify: `scripts/graph_identity.py` (add the `snapshot` subcommand)
- Create: `tests/fixtures/graph_identity/snapshot.json` (generated)
- Test: `tests/unit/test_graph_identity.py`

**Interfaces:**
- Consumes: `dump`, `diff` (Task 1); the corpus (Task 2).
- Produces: CLI `python scripts/graph_identity.py snapshot <corpus> [--out FILE]`. It copies the corpus to a temp directory, adds `.git`, and ingests two roots: `whole` = the copy, `sub` = `ts/apps/web/src`. It writes `{"whole": dump, "sub": dump}` as sorted, indented JSON to FILE, or to stdout.

- [ ] **Step 1: Write the failing test**

```python
"""The Python/TS graph a fixed corpus produces must not change (spec: Migration).

Regenerate only in a step meant to change behaviour, with the reason in the
commit message:
  uv run python scripts/graph_identity.py snapshot tests/fixtures/graph_identity/corpus \
      --out tests/fixtures/graph_identity/snapshot.json
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
CORPUS = REPO / "tests" / "fixtures" / "graph_identity" / "corpus"
SNAPSHOT = REPO / "tests" / "fixtures" / "graph_identity" / "snapshot.json"


def _snapshot(seed: str) -> dict[str, object]:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    out = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "graph_identity.py"), "snapshot", str(CORPUS)],
        env=env, capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


@pytest.mark.parametrize("seed", ["0", "1"])
def test_the_corpus_graph_matches_the_snapshot(seed: str) -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    import graph_identity as gi

    recorded = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    actual = _snapshot(seed)
    for root in ("whole", "sub"):
        lines = gi.diff(recorded[root], actual[root])
        assert not lines, f"{root} graph changed (PYTHONHASHSEED={seed}):\n" + "\n".join(lines[:40])
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest tests/unit/test_graph_identity.py -q`
Expected: FAIL. Either `snapshot` is an invalid choice for the argparse subcommand, or `snapshot.json` is not found.

- [ ] **Step 3: Add the `snapshot` subcommand**

In `scripts/graph_identity.py`, add:

```python
import shutil
import tempfile

from cgis.extractors.registry import build_extractors
from cgis.pipeline import IngestionPipeline
from cgis.storage.sqlite_store import SQLiteStore

_SUB_ROOT = Path("ts/apps/web/src")


def _ingest(root: Path, db: Path) -> None:
    """One full ingest of `root` into `db`, as `cgis ingest` does it."""
    with SQLiteStore(str(db)) as store:
        IngestionPipeline(build_extractors([])).run(str(root), store=store)


def snapshot(corpus: Path) -> dict[str, object]:
    """Both corpus roots, ingested from a temp copy that has a `.git` marker."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "corpus"
        shutil.copytree(corpus, copy)
        (copy / ".git").mkdir()
        dumps: dict[str, object] = {}
        for name, root in (("whole", copy), ("sub", copy / _SUB_ROOT)):
            db = Path(tmp) / f"{name}.db"
            _ingest(root, db)
            dumps[name] = dump(db, copy)
        return dumps
```

In `main`, register the subcommand and handle it:

```python
    s = sub.add_parser("snapshot")
    s.add_argument("corpus", type=Path)
    s.add_argument("--out", type=Path)
    ...
    if args.command == "snapshot":
        text = json.dumps(snapshot(args.corpus), indent=1, sort_keys=True)
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
        return 0
```

`dump(db, copy)` normalises against the copy root for both roots, so the `sub` dump's paths read `<ROOT>/ts/apps/web/src/...` as well.

- [ ] **Step 4: Generate the snapshot and run the test**

```bash
uv run python scripts/graph_identity.py snapshot tests/fixtures/graph_identity/corpus --out tests/fixtures/graph_identity/snapshot.json
uv run pytest tests/unit/test_graph_identity.py -q
```

Expected: 2 passed. Then make sure the test bites. Change one corpus line (rename `helper` to `helper2` in `util.ts`), run the test and see it FAIL with a diff naming `helper2`, then revert the corpus line and confirm with `git diff --stat tests/fixtures` that only `snapshot.json` is new.

- [ ] **Step 5: Commit**

```bash
git add scripts/graph_identity.py tests/unit/test_graph_identity.py tests/fixtures/graph_identity/snapshot.json
git commit -m "test: the Python/TS identity snapshot, compared under two hash seeds

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 4: Forbidden-edge gate with exact allowlists

**Files:**
- Modify: `tests/self_parsing/test_architecture.py` (append "Invariant 7")
- Create: `tests/self_parsing/forbidden_edges_allowlist.json` (generated, then reviewed)

**Interfaces:**
- Consumes: the `root_graph_data` fixture (`tests/self_parsing/conftest.py`), the self-graph of `src/` with `cgis.`-prefixed ids.
- Produces: the `RULES` table and `forbidden_edges(nodes, edges) -> dict[str, dict[str, int]]` (rule → `"src module -> dst module"` → count). Later steps add rules or shrink the allowlist; they never add entries.

- [ ] **Step 1: Write the failing tests**

Add to the import block at the top of `tests/self_parsing/test_architecture.py` (ruff E402 forbids imports further down), and replace the existing `from cgis.core.models import Edge, EdgeType, Node`:

```python
import json
from dataclasses import dataclass, field
from pathlib import Path

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
```

Then append to the end of the file:

```python
# ---------------------------------------------------------------------------
# Invariant 7 — Forbidden edges (language-packs spec, Drift gates)
# ---------------------------------------------------------------------------

_ALLOWLIST = Path(__file__).parent / "forbidden_edges_allowlist.json"
_RULE_EDGES = frozenset({EdgeType.IMPORTS, EdgeType.IMPORTS_SYMBOL, EdgeType.CALLS})


@dataclass(frozen=True)
class _Rule:
    """No edge from a module under `sources` to one under `targets`, bar the exemptions."""

    name: str
    sources: tuple[str, ...]
    targets: tuple[str, ...]
    exempt_modules: frozenset[str] = field(default_factory=frozenset)  # exact module ids
    exempt_prefixes: tuple[str, ...] = ()
    cross_pack: bool = False  # fire only when the `cgis.languages.<pack>` segments differ


RULES: tuple[_Rule, ...] = (
    _Rule("cross-pack", ("cgis.languages",), ("cgis.languages",),
          exempt_modules=frozenset({"cgis.languages"}), cross_pack=True),
    _Rule("pack-isolation", ("cgis.languages", "cgis.extractors"),
          ("cgis.storage", "cgis.query", "cgis.api", "cgis.guardian", "cgis.pipeline")),
    _Rule("resolver-storage", ("cgis.resolver",), ("cgis.storage",)),
    _Rule("surface-packs", ("cgis.query", "cgis.guardian", "cgis.cli", "cgis.api"),
          ("cgis.languages", "cgis.extractors"),
          exempt_modules=frozenset({"cgis.languages", "cgis.extractors.registry"})),
    _Rule("surface-storage", ("cgis.cli", "cgis.api", "cgis.guardian"), ("cgis.storage",)),
    _Rule("core-isolation", ("cgis.core",), ("cgis",), exempt_prefixes=("cgis.core",)),
)  # fmt: skip


def _under(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _pack(module: str) -> str | None:
    parts = module.split(".")
    return parts[2] if len(parts) > 2 and parts[:2] == ["cgis", "languages"] else None


def _fires(rule: _Rule, src: str, dst: str) -> bool:
    if not any(_under(src, p) for p in rule.sources) or not any(_under(dst, p) for p in rule.targets):
        return False
    if dst in rule.exempt_modules or any(_under(dst, p) for p in rule.exempt_prefixes):
        return False
    return not rule.cross_pack or (_pack(src) is not None and _pack(src) != _pack(dst))


def forbidden_edges(nodes: list[Node], edges: list[Edge]) -> dict[str, dict[str, int]]:
    """Rule → "src module -> dst module" → edge count, over INTERNAL targets only."""
    module_of_file = {n.file_path: n.id for n in nodes if n.type == NodeType.FILE}
    by_id = {n.id: n for n in nodes}
    found: dict[str, dict[str, int]] = {rule.name: {} for rule in RULES}
    for e in edges:
        src, dst = by_id.get(e.source), by_id.get(e.target)
        if e.type not in _RULE_EDGES or src is None or dst is None:
            continue
        if dst.namespace != NodeNamespace.INTERNAL:
            continue
        a, b = module_of_file.get(src.file_path), module_of_file.get(dst.file_path)
        if a is None or b is None or a == b:
            continue
        for rule in RULES:
            if _fires(rule, a, b):
                key = f"{a} -> {b}"
                found[rule.name][key] = found[rule.name].get(key, 0) + 1
    return found


def test_forbidden_edges_match_the_allowlist_exactly(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Exact equality: a new edge fails, a grown count fails, a vanished entry fails.

    When a refactor removes violations, lower or delete the entries in the same PR.
    Never add one: a new forbidden edge is the regression this gate exists for.
    """
    _, nodes, edges = root_graph_data
    actual = forbidden_edges(nodes, edges)
    allowed = json.loads(_ALLOWLIST.read_text(encoding="utf-8"))
    assert actual == allowed, (
        "Forbidden edges differ from forbidden_edges_allowlist.json. Actual:\n"
        + json.dumps(actual, indent=1, sort_keys=True)
    )


def test_external_targets_are_never_attributed() -> None:
    """A call into a stdlib or unresolved node is not an edge between our modules."""
    file_a = Node(id="cgis.resolver.x", type=NodeType.FILE, name="x", file_path="cgis/resolver/x.py",
                  start_line=1, end_line=1)
    fn = Node(id="cgis.resolver.x.f", type=NodeType.FUNCTION, name="f", file_path="cgis/resolver/x.py",
              start_line=1, end_line=1)
    ext = Node(id="cgis.storage.fake", type=NodeType.FUNCTION, name="fake", file_path="<virtual>",
               start_line=0, end_line=0, namespace=NodeNamespace.UNKNOWN)
    edge = Edge(id="e", source=fn.id, target=ext.id, type=EdgeType.CALLS)
    assert forbidden_edges([file_a, fn, ext], [edge])["resolver-storage"] == {}


def test_cross_pack_fires_only_between_different_packs() -> None:
    rule = RULES[0]
    assert _fires(rule, "cgis.languages.go.extractor", "cgis.languages.python.indices")
    assert not _fires(rule, "cgis.languages.go.extractor", "cgis.languages.go.rules")
    assert not _fires(rule, "cgis.languages.go.extractor", "cgis.languages")
```

- [ ] **Step 2: Run them to see the gate fail**

Run: `uv run pytest tests/self_parsing/test_architecture.py -q -k "forbidden or external or cross_pack"`
Expected: `test_forbidden_edges_match_the_allowlist_exactly` FAILS with `FileNotFoundError` for `forbidden_edges_allowlist.json`. The two synthetic tests pass.

- [ ] **Step 3: Generate the allowlist and review it by hand**

```bash
uv run python - <<'EOF'
import json, sys
sys.path.insert(0, "tests/self_parsing")
from test_architecture import forbidden_edges
from cgis.pipeline import IngestionPipeline
from cgis.extractors.python_extractor import PythonExtractor
from cgis.storage.sqlite_store import SQLiteStore
with SQLiteStore("/tmp/claude-1001/fe.db") as s:
    nodes, _, edges = IngestionPipeline({".py": PythonExtractor()}).run("src", store=s)
    with open("tests/self_parsing/forbidden_edges_allowlist.json", "w") as fh:
        fh.write(json.dumps(forbidden_edges(nodes, edges), indent=1, sort_keys=True) + "\n")
EOF
```

Review the file against the spec's table. Expected contents:
- `resolver-storage`: only `cgis.resolver.uplift -> cgis.storage.sqlite_store` (8 edges at b0d67f8);
- `surface-packs`: `cgis.query.render.mermaid` must not appear, because it goes through the exempt registry. Direct extractor-class imports from `cgis.cli`, `cgis.api.mcp_server` and `cgis.guardian.*` may appear;
- `surface-storage`: `cgis.cli`, `cgis.api.*` and `cgis.guardian.*` → `cgis.storage.*`, about 65 edges in total;
- `cross-pack`, `pack-isolation` and `core-isolation`: empty.

If any other pair appears, it is either a real finding or a rule bug. Stop and decide before committing.

The script builds the graph exactly as the `root_graph_data` fixture does: `pipeline.run` on `src/`, and the resolved edges it returns. The allowlist then matches the test's own input.

- [ ] **Step 4: Run the gate**

Run: `uv run pytest tests/self_parsing/test_architecture.py -q`
Expected: all pass. Then make sure it bites: append `from cgis.storage.sqlite_store import SQLiteStore` to `src/cgis/core/paths.py` and run again. It must FAIL, naming `cgis.core.paths -> cgis.storage.sqlite_store` under `core-isolation`. Revert, and confirm with `git diff src/` showing nothing.

- [ ] **Step 5: Commit**

```bash
git add tests/self_parsing/test_architecture.py tests/self_parsing/forbidden_edges_allowlist.json
git commit -m "test(architecture): forbidden-edge gate with exact allowlists (language-packs spec)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 5: Plane quotients, observe-only

**Files:**
- Modify: `tests/self_parsing/test_drift.py`

**Interfaces:**
- Consumes: `build_quotient`, `quotient_call_tally` (`cgis/query/drift/quotient.py`), `FingerprintExtractor.from_graph`, `DriftScorer.score`, `DriftScorer.load_project_level`, `_selected_domains` (already in `test_drift.py`).
- Produces: a printed report per plane; nothing enforces it.

- [ ] **Step 1: Write the test**

Append to `tests/self_parsing/test_drift.py`:

```python
import dataclasses

_PLANES = {
    "build": (("pipeline", "extraction", "resolution", "storage"), "orchestrator"),
    "read": (("storage", "query", "guardian", "cli", "api"), "layered_dag"),
}


def test_plane_quotients_observed_not_enforced(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Score each plane's quotient against its template; print, never fail (spec: Drift gates).

    The enforced topology gate is the forbidden-edge test in test_architecture.py.
    These numbers are kept for the record and for #554.
    """
    store, _, _ = root_graph_data
    scorer = DriftScorer(_PATTERNS)
    binding = scorer.load_project_level()[0]
    by_name = {d.name: d for d in _selected_domains(scorer, graph="python")}
    nodes, edges = store.get_all_nodes(), store.get_all_edges()
    for plane, (names, template) in _PLANES.items():
        domains = [by_name[n] for n in names]
        qnodes, qedges = build_quotient(nodes, edges, domains)
        extractor = FingerprintExtractor.from_graph(
            qnodes, qedges, quotient_call_tally(nodes, edges, domains)
        )
        fingerprint = extractor.extract(binding.fqn_prefix)
        assert fingerprint.node_count == len(domains), f"{plane}: a domain matched nothing"
        report = scorer.score(fingerprint, dataclasses.replace(binding, expected_pattern=template))
        print(f"\n[plane observe-only] {plane} vs {template}: drift={report.drift_score:.3f}")
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/self_parsing/test_drift.py -q -s -k plane`
Expected: 1 passed. It prints `build vs orchestrator: drift=0.238` and `read vs layered_dag: drift=0.676`, ±0.01 of the spec's b0d67f8 figures. If either number is far off, the plane membership is wrong: check the domain names in `patterns.yaml` before going on.

`node_count` is the quotient's node count on the fingerprint (`cgis/query/drift/fingerprint.py`). The assertion exists so a misspelled domain fails loudly instead of scoring an empty plane.

- [ ] **Step 3: Full gate and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage && \
git add tests/self_parsing/test_drift.py && \
git commit -m "test(drift): plane quotients reported observe-only (language-packs spec)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Step 0 PR

- [ ] Push the branch, open the PR ("test: language-packs step 0 — identity net, forbidden-edge gate, plane reports"), and run the independent reviewer. Ask it to mutation-check the gate and the snapshot. Merge on approval.

---

## Steps 1–7 — outline (each gets its own detailed plan when started)

Each step below is one PR. Its plan is written when the previous step has merged, against the code as it then is. Every step's PR description carries the `graph_identity.py diff` result for cgis `src/` at 715cd9a and owner-api @ 8de2245, with `main`'s cgis and the branch's cgis over the same input.

**Step 1 — Skeleton and registry.**
- `cgis/languages/__init__.py` (lazy registry, per-pack manifests), `languages/python/`, `languages/typescript/`.
- Extractor modules move under their pack.
- `scope_of` and `is_test` per pack; the pipeline's `_TEST_FILE_PATTERN` is replaced.
- CLI, MCP server, `scripts/generate_health.py`, guardian and `mermaid.py` go through the registry.
- Test imports are updated (36 files), with no shims.
- The `extraction` domain in `patterns.yaml` is re-pointed, and its tolerance re-measured.
- CLAUDE.md is updated.

Acceptance:
- the snapshot is unchanged;
- the `surface-packs` allowlist is empty;
- an ingest of a Python-only repo imports no TS grammar (test by checking `sys.modules`).

**Step 2 — Project readers.**
- `ProjectReader`/`ProjectFacts` per pack.
- `import_names.py` collects them; the store keys are unchanged.

Acceptance: snapshot unchanged, including `ingest_state`; the `test_configs_above_root.py` tests are green, unmodified.

**Step 3 — Index and classification.**
- `index_facts` and `classify(fqn, ctx)` per pack.
- The no-source → Python rule stays in the registry.
- `resolve_layout`/`map_to_node_fqn` are gated by pack.

Acceptance: snapshot unchanged.

**Step 4 — Resolution steps.**
- Named generic steps, plus per-pack type normalizers, positioned steps, fallback (JS builtins at 0.8) and import reconciliation.
- `cross_file_metadata` per pack manifest.
- One language branch is extracted per commit, with the snapshot test run after each.

Acceptance:
- snapshot unchanged;
- owner-api ingest no more than 5% slower than `main`.

**Step 5 — Build-plane passes.**
- `pipeline.py` becomes `cgis/pipeline/`.
- `resolver/uplift.py` and `refresh_dispatch` move into it.
- The `resolution` domain is re-measured.

Acceptance: the `resolver-storage` allowlist is empty, and the snapshot is unchanged.

**Step 6 — Gates.**
- The `project_level` binding is deleted from `patterns.yaml`, along with its test in `test_drift.py`.
- `languages.<pack>` domains with one shared tolerance; `core` gets `pure_utility`.
- #146 is closed with the forbidden-edge gate as its answer.

**Step 7 — Go pilot.**
- `languages/go/` and its registry manifest.
- The `tree-sitter-go` dependency.
- A Go corpus with its own snapshot.

Acceptance:
- `git diff --stat main` touches only `src/cgis/languages/go/`, `src/cgis/languages/__init__.py` (one manifest), `pyproject.toml`/`uv.lock` and Go tests and fixtures;
- the Python/TS snapshot is unchanged;
- the ontology-compliance tests pass on the Go corpus.
