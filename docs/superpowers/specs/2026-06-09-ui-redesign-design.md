# UI Redesign: Multi-Repo Graph Explorer

**Date:** 2026-06-09
**Status:** Approved
**Related issues:** #30 (ego-subgraph API), #31 (React UI upgrade)

---

## 1. Problem Statement

The current `feat/ui` branch has a working ReactFlow visualizer but several critical issues:

- **Layout bugs**: O(n²) overlap resolution, vertical-only shift, no horizontal correction
- **State bugs**: index-based edge IDs break React diffing on filter; `ALL_EDGE_TYPES` not memoized; stale flow cache after graph reload
- **Rendering bug**: `mapEdgeToFlowView()` missing `markerEnd` — no arrows in flow view
- **Architecture**: `App.tsx` (232 lines) mixes fetching, filtering, layout, hover, click — untestable monolith; implicit callback chains between hooks
- **Single-repo only**: hardcoded single `graph.json`; no namespace separation; doesn't scale to ownima's 4 repos

---

## 2. Goals

1. Fix all known bugs from `feat/ui`
2. Support multi-repo ingestion (Python + TypeScript namespaces) in one merged graph
3. Island layout: each repo rendered as an independent cluster; cross-repo edges visible
4. Progressive disclosure: double-click any node → ego-subgraph overlay via REST (issue #30)
5. Clean, testable architecture: zustand store, separated concerns, no callback chains

---

## 3. Architecture

### 3.1 Component Tree

```
GraphProvider (zustand store)
└── GraphShell
    ├── GraphCanvas
    │   └── ReactFlow
    │       ├── IslandContainerNode  ← new: per-namespace cluster
    │       ├── FileContainerNode    ← kept from feat/ui
    │       └── EgoGraph overlay     ← new: issue #30 on-demand subgraph
    └── Sidebar
        ├── ControlPanel            ← kept, adds RepoSelector
        ├── RepoSelector            ← new: toggle namespaces
        ├── StatsPanel              ← kept as-is
        └── LegendPanel             ← kept as-is
```

`GraphShell` is the sole orchestrator — renders `GraphCanvas` + `Sidebar`. No business logic in either leaf.

### 3.2 Zustand Store

Four slices, no prop-drilling, no callback chains. Any component subscribes directly via `useGraphStore(s => s.whatever)`.

```ts
// graphSlice
rawNodes: GraphNode[]
rawEdges: GraphEdge[]
namespaces: string[]          // ['owner-api', 'owner-web', …]
graphVersion: number          // bumped on reload — invalidates all caches

// layoutSlice
layoutedNodes: Node[]         // ReactFlow nodes with positions
layoutedEdges: Edge[]         // ReactFlow edges
expandedFiles: Set<string>
islandPositions: Map<string, {x: number, y: number}>  // drag overrides

// filterSlice
activeEdgeTypes: Set<EdgeType>
showExternal: boolean
activeNamespaces: Set<string>

// flowSlice
flowNodes: Node[]             // nodes for 'flow' view mode
flowEdges: Edge[]
flowCache: Map<string, {nodes: Node[], edges: Edge[]}>  // key: `${nodeId}:${depth}:${types}:${graphVersion}`

// uiSlice
viewMode: 'full' | 'flow' | 'ego'
hoveredNodeId: string | null
egoNodeId: string | null      // active ego-subgraph focus
```

**What's deleted:** The `onFiltered` callback chain between `useGraphFilter` → `App` → `useFlowNavigation`. Replaced by store selectors.

### 3.3 Files: keep vs rewrite

| File | Action |
|------|--------|
| `FileContainerNode.tsx` | Keep — minor isExpanded sync fix |
| `ControlPanel.tsx` | Keep — add `RepoSelector` inside |
| `NodeTooltip / StatsPanel / LegendPanel` | Keep — no changes |
| `useSearch / useExport / useKeyboardShortcuts` | Keep — wire to zustand |
| `theme.ts / constants.ts / types.ts` | Keep — add namespace color map |
| `collapse.ts / flow.ts` | Keep — pure functions, no changes |
| `App.tsx` | **Rewrite** → `GraphShell.tsx` + `GraphProvider.tsx` |
| `layout.ts` | **Rewrite** → `IslandLayoutEngine.ts` |
| `useGraphFilter.ts` | **Rewrite** → `filterSlice` in store |
| `useGraphFetch.ts` | **Rewrite** → `GraphDataService.ts` |
| `edgeMapper.ts` | **Rewrite** → unified with `markerEnd` + stable IDs |
| `useFlowNavigation.ts` | **Rewrite** → `flowSlice` in store |

---

## 4. IslandLayoutEngine

Replaces `layout.ts`. Three deterministic phases:

### Phase 1 — Partition
```
partition(nodes, edges) → Map<namespace, {nodes, edges}>
```
Group nodes by `node.namespace`. Cross-namespace edges are set aside — they don't participate in per-island layout.

### Phase 2 — Layout each island
```
layoutIsland(island) → {nodes: PositionedNode[], bbox: Rect}
```
Run dagre independently per island (direction TB, `nodesep=60`, `ranksep=100`). Then apply FILE container logic (kept from current `layout.ts`). Returns relative positions `[0..W] × [0..H]` and bounding box.

### Phase 3 — Place islands on canvas
```
placeIslands(islands) → Map<namespace, offset>
```
**Greedy row bin-packing** (O(n log n)):
1. Sort islands by node count descending (largest first)
2. Place each island left-to-right in current row; start new row when row width exceeds `CANVAS_MAX_WIDTH` (default 3200px, defined in `constants.ts`)
3. Translate all island nodes by their row offset
4. Restore cross-namespace edges (connect FQN endpoints across islands)

Island offsets stored in `islandPositions` store slice. Drag-and-drop overrides only the offset — no dagre recalculation.

**Why this fixes the current bugs:**
- No O(n²) loop — sort + linear scan
- Handles both axes (row packing moves islands horizontally and vertically)
- Cross-namespace edges re-routed after placement, not during

---

## 5. Data Layer

### 5.1 Multi-repo ingestion

New `--namespace` flag on `cgis ingest`, plus append mode:

```bash
cgis ingest ./owner-api/src --namespace owner-api --output graph.db
cgis ingest ./owner-web/src  --namespace owner-web  --db graph.db   # append
cgis ingest ./ownima-admin/src --namespace admin   --db graph.db
cgis ingest ./rider-web/src  --namespace rider-web --db graph.db
```

`Node.namespace` stores the repo tag. One `graph.db`, all islands.

**Append mode semantics:** `--db graph.db` (without `--output`) upserts by namespace — deletes all existing nodes/edges for that namespace first, then inserts the fresh parse. This prevents stale nodes from deleted files accumulating across runs.

### 5.2 cgis serve

```bash
cgis serve --db graph.db --port 8000
```

Thin FastAPI wrapper over the existing `SQLiteStore` + `QueryEngine`. Two routes:

```
GET /graph.json                         → full graph dump (static, for UI initial load)
GET /api/graph/explore                  → ego-subgraph (issue #30 contract)
    ?node_id=src.cgis.pipeline…
    &depth=1
    &direction=both|upstream|downstream
```

Response schema matches issue #30:
```json
{
  "focus_node_id": "…",
  "nodes": [{id, label, subtext, type, namespace, file_path}],
  "edges": [{id, source, target, relation_type, confidence}]
}
```

### 5.3 GraphDataService (UI)

Single data access point in the UI:

```ts
class GraphDataService {
  async loadFullGraph(): Promise<GraphData>
  // fetch VITE_GRAPH_URL || "/graph.json"
  // bumps store.graphVersion on success

  async exploreNode(nodeId: string, depth: number, direction: string): Promise<EgoSubgraph>
  // GET /api/graph/explore
  // cached by `${nodeId}:${depth}:${direction}:${graphVersion}`
  // graphVersion in cache key ensures invalidation on reload

  invalidateCache(): void
}
```

### 5.4 Ego-subgraph interaction flow

1. User double-clicks a node
2. Store: `egoNodeId = nodeId`, `viewMode = 'ego'`
3. `GraphDataService.exploreNode(nodeId, depth=1, 'both')` fires
4. Result nodes/edges merged into canvas, ego nodes highlighted
5. Depth slider → re-fetch with new depth
6. Escape / Back button → `viewMode = 'full'`, ego nodes removed

Ego overlay sits on top of the full graph — no route change, island positions preserved.

### 5.5 Bug fixes in data layer

| Bug | Fix |
|-----|-----|
| Index-based edge IDs (`e-${index}`) | Stable content-hash: `` `${source}→${target}:${type}` `` |
| Missing `markerEnd` in flow view | Unified `edgeMapper` with `arrowClosed` marker in both views |
| `ALL_EDGE_TYPES` not memoized | Module-level constant — never re-created |
| Stale flow cache after graph reload | Cache key includes `graphVersion` |
| `applyHighlight` creates new objects unconditionally | Only create new object when opacity changes |

---

## 6. Testing Strategy

### Unit (vitest)
- `IslandLayoutEngine` — each phase independently with fixture data
- Bin-packing algorithm — edge cases: single island, equal sizes, very wide island
- Zustand store slices — filterSlice selectors, graphVersion bumping
- Stable edge ID generation

### Component (vitest + @testing-library/react)
- `GraphShell` renders correct number of islands
- `RepoSelector` toggles `activeNamespaces` in store
- `FileContainerNode` expand/collapse
- `EgoGraph` overlay appears on double-click, disappears on Escape

### Integration
- `cgis ingest --namespace` → `graph.db` contains namespace-tagged nodes
- `cgis serve` → `/api/graph/explore` returns issue #30 contract
- Multi-namespace round-trip: 2 ingest runs → 2 islands on canvas

---

## 7. Implementation Plan (4 PRs)

| PR | Title | Scope | Est. |
|----|-------|-------|------|
| **PR 1** | `feat(ui): zustand foundation + bug fixes` | Store scaffold, GraphProvider, stable edge IDs, markerEnd, memoized constants, graphVersion | 2–3 days |
| **PR 2** | `feat(ui): IslandLayoutEngine` | 3-phase layout, bin-packing, IslandContainerNode, drag-to-reposition, cross-namespace edges | 3–4 days |
| **PR 3** | `feat(cgis+ui): multi-repo namespace support` | `--namespace` flag, append ingest, `cgis serve` FastAPI, RepoSelector UI, namespace colors | 2–3 days |
| **PR 4** | `feat(ui+cgis): ego-subgraph overlay (issue #30)` | GraphDataService, `/api/graph/explore` route, EgoGraph overlay, depth slider, direction toggle | 2–3 days |

Each PR leaves the graph fully functional. PR 1+2 can be merged to `main` independently — they close all known `feat/ui` bugs.

---

## 8. Open Questions (resolved)

| Question | Decision |
|----------|----------|
| Layout strategy | B — cluster islands, dagre per namespace |
| Data source | C — hybrid: static JSON + REST on-demand |
| Island visual style | B — free floating islands, drag-and-drop |
| Approach | 3 — full architecture redesign |
| State management | zustand (no boilerplate, works with ReactFlow) |
| Multi-graph model | 1 — single merged graph.db, namespace tags per node |
