# UI Redesign PR 3: Multi-Repo Namespace Support

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--namespace` flag to `cgis ingest` (append mode), implement `cgis serve` (FastAPI wrapper), and add `RepoSelector` UI component to toggle namespace islands.

**Architecture:** `cgis ingest --namespace owner-api` tags every node with `namespace="owner-api"`. Append mode (`--db` without `--output`) upserts by namespace — delete existing nodes for that namespace, then insert fresh parse. `cgis serve` is a thin FastAPI wrapper over SQLiteStore with two routes: `GET /graph.json` and `GET /api/graph/explore`.

**Tech Stack:** Python (FastAPI, uvicorn), React 19, TypeScript, vitest, pytest

**Prerequisite:** PR 2 merged.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `src/cgis/cli.py` | Add `--namespace` and `--db` (append) flags to `ingest`; add `serve` command |
| Create | `src/cgis/api/serve.py` | FastAPI app with `/graph.json` and `/api/graph/explore` routes |
| Modify | `src/cgis/pipeline.py` | Pass namespace through to all emitted Node objects |
| Modify | `src/cgis/storage/sqlite_store.py` | Add `delete_by_namespace()` for append mode |
| Create | `ui/src/components/RepoSelector.tsx` | Toggle buttons for each namespace island |
| Modify | `ui/src/components/ControlPanel.tsx` | Add RepoSelector below edge type toggles |
| Modify | `ui/src/components/GraphShell.tsx` | Pass activeNamespaces to IslandLayoutEngine filter |
| Modify | `ui/src/hooks/useLayoutComputation.ts` | Filter nodes by activeNamespaces before layout |
| Test | `tests/unit/test_namespace_ingest.py` | namespace tag on nodes, append mode upsert |
| Test | `tests/unit/test_serve.py` | FastAPI route contracts |
| Test | `ui/src/components/__tests__/RepoSelector.test.tsx` | Toggle behaviour |

---

## Task 1: Add --namespace flag to cgis ingest

**Files:** `src/cgis/cli.py`, `src/cgis/pipeline.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_namespace_ingest.py
import tempfile, os
from pathlib import Path
from cgis.pipeline import IngestionPipeline
from cgis.extractors.python_extractor import PythonExtractor
from cgis.storage.sqlite_store import SQLiteStore

def test_namespace_tags_all_nodes(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("def fn(): pass\n", encoding="utf-8")
    db = str(tmp_path / "graph.db")
    pipeline = IngestionPipeline({".py": PythonExtractor()}, namespace="owner-api")
    with SQLiteStore(db) as store:
        pipeline.run(str(tmp_path), store=store)
    with SQLiteStore(db) as store:
        nodes = store.get_all_nodes()
    assert all(n.metadata.get("namespace") == "owner-api" for n in nodes)

def test_append_mode_upserts_namespace(tmp_path: Path) -> None:
    db = str(tmp_path / "graph.db")
    (tmp_path / "a.py").write_text("def alpha(): pass\n", encoding="utf-8")
    pipeline = IngestionPipeline({".py": PythonExtractor()}, namespace="ns-a")
    with SQLiteStore(db) as store:
        pipeline.run(str(tmp_path), store=store)

    # Re-run same namespace — should replace, not duplicate
    with SQLiteStore(db) as store:
        pipeline.run(str(tmp_path), store=store)

    with SQLiteStore(db) as store:
        nodes = [n for n in store.get_all_nodes() if n.metadata.get("namespace") == "ns-a"]
    # Should have exactly the same count, not doubled
    assert len([n for n in nodes if n.name == "alpha"]) == 1
```

- [ ] **Step 2: Run — expect FAIL**

```bash
ENV=test uv run pytest tests/unit/test_namespace_ingest.py -v
```

Expected: `TypeError: IngestionPipeline.__init__() got an unexpected keyword argument 'namespace'`

- [ ] **Step 3: Add namespace to IngestionPipeline**

In `src/cgis/pipeline.py`, add `namespace: str | None = None` parameter to `__init__`, store as `self._namespace`. In `run()`, after extraction, update each node's metadata:

```python
def __init__(
    self,
    extractors: Mapping[str, BaseExtractor],
    domains_config: str | None = None,
    namespace: str | None = None,
) -> None:
    self._extractors = extractors
    self._domains_config = domains_config
    self._namespace = namespace
    self._excluded = {"venv", ".venv", "__pycache__", "node_modules", "build", "dist"}
```

In `_collect_nodes` or before `_persist_incremental`, tag nodes:

```python
if self._namespace:
    all_nodes = [
        n.model_copy(update={"metadata": {**n.metadata, "namespace": self._namespace}})
        for n in all_nodes
    ]
```

- [ ] **Step 4: Add delete_by_namespace to SQLiteStore**

In `src/cgis/storage/sqlite_store.py`:

```python
def delete_by_namespace(self, namespace: str) -> None:
    """Remove all nodes (and their edges) belonging to a namespace."""
    if not self._conn:
        raise RuntimeError(self._error_message)
    with self._conn:
        # Get node IDs for this namespace
        cursor = self._conn.execute(
            "SELECT id FROM nodes WHERE json_extract(metadata, '$.namespace') = ?",
            (namespace,),
        )
        ids = [row[0] for row in cursor.fetchall()]
        if not ids:
            return
        placeholders = ", ".join(["?"] * len(ids))
        self._conn.execute(f"DELETE FROM edges WHERE source IN ({placeholders})", ids)
        self._conn.execute(f"DELETE FROM edges WHERE target IN ({placeholders})", ids)
        self._conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", ids)
```

In `pipeline.py`, call `delete_by_namespace` before ingestion when namespace is set and store already has data:

```python
# At the start of run(), before _collect_nodes:
if self._namespace and store is not None:
    store.delete_by_namespace(self._namespace)
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
ENV=test uv run pytest tests/unit/test_namespace_ingest.py -v
```

Expected: `2 passed`

- [ ] **Step 6: Add --namespace and --db flags to CLI**

In `src/cgis/cli.py`, modify the `ingest` command signature:

```python
@app.command()
def ingest(
    path: str = typer.Argument(..., help="Path to repository root to ingest."),
    output: str | None = typer.Option(None, "--output", "-o", help="Output .db or .json file."),
    db: str | None = typer.Option(None, "--db", help="Existing .db file to append into (namespace required)."),
    namespace: str | None = typer.Option(None, "--namespace", "-n", help="Namespace tag for all nodes (e.g. 'owner-api')."),
    incremental: bool = typer.Option(False, "--incremental", "-i", help="Only re-ingest changed files."),
    domains: str | None = typer.Option(None, "--domains", "-d", help="Path to domains.yaml for semantic uplift."),
) -> None:
```

Validation: if `--db` is set, `--namespace` is required; if `--output` is set, use it as the db path.

- [ ] **Step 7: Run full make check**

```bash
make format && make lint && make type-check && make pytest
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/cgis/cli.py src/cgis/pipeline.py src/cgis/storage/sqlite_store.py tests/unit/test_namespace_ingest.py
git commit -m "feat(cgis): --namespace flag and append mode for multi-repo ingest"
```

---

## Task 2: Add cgis serve command

**Files:**
- Create: `src/cgis/api/serve.py`
- Modify: `src/cgis/cli.py`
- Create: `tests/unit/test_serve.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_serve.py
from fastapi.testclient import TestClient
from pathlib import Path
from cgis.api.serve import create_app
from cgis.storage.sqlite_store import SQLiteStore
from cgis.pipeline import IngestionPipeline
from cgis.extractors.python_extractor import PythonExtractor

def test_graph_json_endpoint(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("def fn(): pass\n", encoding="utf-8")
    db = str(tmp_path / "graph.db")
    with SQLiteStore(db) as store:
        IngestionPipeline({".py": PythonExtractor()}).run(str(tmp_path), store=store)

    app = create_app(db)
    client = TestClient(app)
    r = client.get("/graph.json")
    assert r.status_code == 200
    data = r.json()
    assert "nodes" in data and "edges" in data
    assert len(data["nodes"]) > 0

def test_explore_endpoint_returns_contract(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("def caller():\n    callee()\ndef callee(): pass\n", encoding="utf-8")
    db = str(tmp_path / "graph.db")
    with SQLiteStore(db) as store:
        IngestionPipeline({".py": PythonExtractor()}).run(str(tmp_path), store=store)

    with SQLiteStore(db) as store:
        nodes = store.get_all_nodes()
    caller = next(n for n in nodes if n.name == "caller")

    app = create_app(db)
    client = TestClient(app)
    r = client.get(f"/api/graph/explore?node_id={caller.id}&depth=1&direction=both")
    assert r.status_code == 200
    data = r.json()
    assert data["focus_node_id"] == caller.id
    assert "nodes" in data and "edges" in data
```

- [ ] **Step 2: Run — expect FAIL**

```bash
ENV=test uv run pytest tests/unit/test_serve.py -v
```

Expected: `ModuleNotFoundError: No module named 'cgis.api'`

- [ ] **Step 3: Create src/cgis/api/__init__.py**

```bash
mkdir -p src/cgis/api && touch src/cgis/api/__init__.py
```

- [ ] **Step 4: Create src/cgis/api/serve.py**

```python
# src/cgis/api/serve.py
"""Thin FastAPI wrapper over SQLiteStore for the graph explorer UI."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from cgis.storage.sqlite_store import SQLiteStore
from cgis.query.engine import QueryEngine


def create_app(db_path: str) -> FastAPI:
    """Create and return the FastAPI application."""
    app = FastAPI(title="CGIS Graph Explorer API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/graph.json")
    def full_graph() -> dict[str, Any]:
        with SQLiteStore(db_path) as store:
            nodes = store.get_all_nodes()
            edges = store.get_all_edges()
        return {
            "nodes": [
                {
                    "id": n.id,
                    "name": n.name,
                    "type": n.type.value,
                    "file_path": n.file_path,
                    "start_line": n.start_line,
                    "end_line": n.end_line,
                    "namespace": n.metadata.get("namespace", "INTERNAL"),
                    "metadata": n.metadata,
                }
                for n in nodes
            ],
            "edges": [
                {
                    "id": e.id,
                    "source": e.source,
                    "target": e.target,
                    "type": e.type.value,
                    "weight": e.weight,
                    "confidence": e.confidence,
                }
                for e in edges
            ],
        }

    @app.get("/api/graph/explore")
    def explore(
        node_id: str = Query(..., description="FQN of the focal node"),
        depth: int = Query(1, ge=1, le=5),
        direction: str = Query("both", pattern="^(upstream|downstream|both)$"),
    ) -> dict[str, Any]:
        with SQLiteStore(db_path) as store:
            engine = QueryEngine(store)
            nodes_acc: dict[str, Any] = {}
            edges_acc: dict[str, Any] = {}

            if direction in ("downstream", "both"):
                fn, fe = engine.get_flow_graph(node_id, max_depth=depth)
                for n in fn:
                    nodes_acc[n.id] = n
                for e in fe:
                    edges_acc[e.id] = e

            if direction in ("upstream", "both"):
                in_, ie = engine.get_impact_graph(node_id, max_depth=depth)
                for n in in_:
                    nodes_acc[n.id] = n
                for e in ie:
                    edges_acc[e.id] = e

        return {
            "focus_node_id": node_id,
            "nodes": [
                {
                    "id": n.id,
                    "label": n.name,
                    "subtext": f"{n.file_path}:{n.start_line}",
                    "type": n.type.value,
                    "namespace": n.metadata.get("namespace", "INTERNAL"),
                    "file_path": n.file_path,
                }
                for n in nodes_acc.values()
            ],
            "edges": [
                {
                    "id": e.id,
                    "source": e.source,
                    "target": e.target,
                    "relation_type": e.type.value,
                    "confidence": e.confidence,
                }
                for e in edges_acc.values()
            ],
        }

    return app
```

- [ ] **Step 5: Add serve command to cli.py**

```python
@app.command()
def serve(
    db: str = typer.Option("graph.db", "--db", help="Path to graph.db"),
    port: int = typer.Option(8000, "--port", "-p", help="HTTP port"),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
) -> None:
    """Serve the graph explorer API (GET /graph.json + GET /api/graph/explore)."""
    import uvicorn
    from cgis.api.serve import create_app
    uvicorn_app = create_app(db)
    console.print(f"[bold green]Serving graph API on http://{host}:{port}[/bold green]")
    uvicorn.run(uvicorn_app, host=host, port=port)
```

Add `fastapi` and `uvicorn` to `pyproject.toml` dependencies:

```toml
"fastapi>=0.115",
"uvicorn>=0.32",
```

Then `uv sync`.

- [ ] **Step 6: Run tests — expect PASS**

```bash
ENV=test uv run pytest tests/unit/test_serve.py -v
```

Expected: `2 passed`

- [ ] **Step 7: Full check**

```bash
make format && make lint && make type-check && make pytest
```

- [ ] **Step 8: Commit**

```bash
git add src/cgis/api/ tests/unit/test_serve.py src/cgis/cli.py pyproject.toml uv.lock
git commit -m "feat(cgis): add cgis serve command — FastAPI graph explorer API"
```

---

## Task 3: Add RepoSelector UI component

**Files:**
- Create: `ui/src/components/RepoSelector.tsx`
- Create: `ui/src/components/__tests__/RepoSelector.test.tsx`

- [ ] **Step 1: Write failing test**

```typescript
// ui/src/components/__tests__/RepoSelector.test.tsx
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useGraphStore } from '../../store/useGraphStore'
import RepoSelector from '../RepoSelector'

describe('RepoSelector', () => {
  it('renders one button per namespace', () => {
    useGraphStore.setState({ namespaces: ['owner-api', 'owner-web'], activeNamespaces: new Set(['owner-api', 'owner-web']) })
    render(<RepoSelector />)
    expect(screen.getByText('owner-api')).toBeTruthy()
    expect(screen.getByText('owner-web')).toBeTruthy()
  })

  it('clicking a namespace toggles it in the store', () => {
    useGraphStore.setState({ namespaces: ['owner-api'], activeNamespaces: new Set(['owner-api']) })
    render(<RepoSelector />)
    fireEvent.click(screen.getByText('owner-api'))
    expect(useGraphStore.getState().activeNamespaces.has('owner-api')).toBe(false)
    fireEvent.click(screen.getByText('owner-api'))
    expect(useGraphStore.getState().activeNamespaces.has('owner-api')).toBe(true)
  })

  it('renders nothing when namespaces is empty', () => {
    useGraphStore.setState({ namespaces: [], activeNamespaces: new Set() })
    const { container } = render(<RepoSelector />)
    expect(container.firstChild).toBeNull()
  })
})
```

- [ ] **Step 2: Run — expect FAIL**

```bash
cd ui && bunx vitest run src/components/__tests__/RepoSelector.test.tsx
```

- [ ] **Step 3: Create RepoSelector.tsx**

```tsx
// ui/src/components/RepoSelector.tsx
import { useGraphStore } from '../store/useGraphStore'
import { NAMESPACE_COLORS } from '../theme'
import sharedStyles from '../shared.module.css'

export default function RepoSelector() {
  const namespaces = useGraphStore((s) => s.namespaces)
  const activeNamespaces = useGraphStore((s) => s.activeNamespaces)
  const toggleNamespace = useGraphStore((s) => s.toggleNamespace)

  if (namespaces.length === 0) return null

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 8 }}>
      {namespaces.map((ns) => {
        const active = activeNamespaces.has(ns)
        const color = (NAMESPACE_COLORS as Record<string, string>)[ns] ?? '#546e7a'
        return (
          <button
            key={ns}
            className={sharedStyles.btn}
            onClick={() => toggleNamespace(ns)}
            style={{
              border: `1px solid ${color}`,
              background: active ? `${color}33` : 'transparent',
              color: active ? color : '#6b7280',
              fontSize: 10,
              padding: '2px 8px',
              cursor: 'pointer',
            }}
          >
            {ns}
          </button>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 4: Run — expect PASS**

```bash
cd ui && bunx vitest run src/components/__tests__/RepoSelector.test.tsx
```

Expected: `3 passed`

- [ ] **Step 5: Add RepoSelector to ControlPanel**

In `ui/src/components/ControlPanel.tsx`, import and render `<RepoSelector />` below the edge-type toggle buttons.

- [ ] **Step 6: Filter by activeNamespaces in useLayoutComputation**

In `ui/src/hooks/useLayoutComputation.ts`, add namespace filter before the layout call:

```typescript
const activeNamespaces = useGraphStore((s) => s.activeNamespaces)

// Add after external filter (Step 4 in the existing pipeline):
const nsFiltered = activeNamespaces.size === 0
  ? visibleNodes
  : visibleNodes.filter(
      (n) => activeNamespaces.has((n.data as any)?.namespace as string) ||
             !(n.data as any)?.namespace
    )
const nsFilteredIds = new Set(nsFiltered.map((n) => n.id))
const nsFilteredEdges = visibleEdges.filter(
  (e) => nsFilteredIds.has(e.source) && nsFilteredIds.has(e.target)
)
// Then pass nsFiltered / nsFilteredEdges to labeledNodes / engine
```

- [ ] **Step 7: Run all tests**

```bash
cd ui && bun run test:run && make pytest
```

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(ui): add RepoSelector — toggle namespace islands"
```

---

## Task 4: Push + PR

- [ ] **Step 1: Push**

```bash
git checkout -b feat/ui-pr3-multi-repo
git push -u origin feat/ui-pr3-multi-repo
```

- [ ] **Step 2: Open PR**

```bash
gh pr create \
  --title "feat(cgis+ui): multi-repo namespace support (PR 3/4)" \
  --body "$(cat <<'EOF'
## Summary
- `cgis ingest --namespace owner-api` tags all nodes with namespace
- Append mode (`--db graph.db --namespace ns`) upserts by namespace (no stale nodes)
- `cgis serve --db graph.db` serves GET /graph.json + GET /api/graph/explore (issue #30 contract)
- RepoSelector UI toggles namespace island visibility

## Test plan
- [ ] `make pytest` passes (namespace ingest + serve API)
- [ ] `bun run test:run` passes (RepoSelector)
- [ ] `cgis ingest ./src --namespace owner-api --output graph.db && cgis serve --db graph.db` → http://localhost:8000/graph.json returns namespace-tagged nodes
- [ ] Dev server: RepoSelector buttons appear, toggling hides/shows islands

Closes: #30 (API contract)
Prerequisite: PR 2
EOF
)"
```
