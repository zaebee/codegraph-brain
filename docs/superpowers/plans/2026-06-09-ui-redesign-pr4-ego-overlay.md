# UI Redesign PR 4: Ego-Subgraph Overlay

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Double-clicking any node triggers an ego-subgraph overlay fetched from `GET /api/graph/explore`. Adds `GraphDataService`, `EgoGraph` overlay component, depth slider, and direction toggle — completing issue #30.

**Architecture:** `GraphDataService` is a plain class (singleton) that owns both data sources: static JSON load (already handled by GraphProvider) and ego-subgraph REST queries. Ego overlay nodes are merged into the canvas on top of the full graph. `viewMode = 'ego'` triggers the overlay; Escape clears it.

**Tech Stack:** React 19, @xyflow/react v12, vitest, pytest, bun

**Prerequisite:** PR 3 merged (`cgis serve` running).

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `ui/src/services/GraphDataService.ts` | Ego-subgraph REST queries, cache keyed by `graphVersion` |
| Create | `ui/src/components/EgoOverlay.tsx` | Ego overlay panel: focused node name, depth slider, direction toggle, Back button |
| Modify | `ui/src/components/GraphShell.tsx` | Double-click → set egoNodeId; merge ego nodes; render EgoOverlay |
| Modify | `ui/src/store/useGraphStore.ts` | Add `egoNodes`, `egoEdges`, `setEgoGraph`, `clearEgoGraph` to uiSlice |
| Modify | `ui/src/hooks/useLayoutComputation.ts` | Skip re-layout when `viewMode === 'ego'` |
| Test | `ui/src/services/__tests__/GraphDataService.test.ts` | Cache invalidation on graphVersion change |
| Test | `ui/src/components/__tests__/EgoOverlay.test.tsx` | Renders focus node, depth slider, Back clears ego |

---

## Task 1: Extend store with ego graph state

**Files:** `ui/src/store/useGraphStore.ts`

- [ ] **Step 1: Add ego fields to the interface and initial state**

Add to the `GraphStore` interface (in the ui slice section):

```typescript
// in GraphStore interface — ui slice:
egoNodes: Node[]
egoEdges: Edge[]
setEgoGraph: (nodes: Node[], edges: Edge[]) => void
clearEgoGraph: () => void
```

Add to the `create()` call (in the ui slice section):

```typescript
egoNodes: [],
egoEdges: [],
setEgoGraph: (nodes, edges) => set({ egoNodes: nodes, egoEdges: edges, viewMode: 'ego' }),
clearEgoGraph: () => set({ egoNodes: [], egoEdges: [], egoNodeId: null, viewMode: 'full' }),
```

- [ ] **Step 2: Add tests for new actions**

Append to `ui/src/store/__tests__/useGraphStore.test.ts`:

```typescript
describe('uiSlice — ego graph', () => {
  it('setEgoGraph sets nodes, edges, and viewMode to ego', () => {
    const node = { id: 'n1', position: { x: 0, y: 0 }, data: {} } as Node
    useGraphStore.getState().setEgoGraph([node], [])
    const state = useGraphStore.getState()
    expect(state.egoNodes).toHaveLength(1)
    expect(state.viewMode).toBe('ego')
  })

  it('clearEgoGraph resets to full view', () => {
    useGraphStore.setState({ egoNodes: [{ id: 'x' } as Node], viewMode: 'ego', egoNodeId: 'x' })
    useGraphStore.getState().clearEgoGraph()
    const state = useGraphStore.getState()
    expect(state.egoNodes).toHaveLength(0)
    expect(state.viewMode).toBe('full')
    expect(state.egoNodeId).toBeNull()
  })
})
```

- [ ] **Step 3: Run tests — expect PASS**

```bash
cd ui && bunx vitest run src/store/__tests__/useGraphStore.test.ts
```

Expected: all tests pass (including the 2 new ones).

- [ ] **Step 4: Commit**

```bash
git add ui/src/store/useGraphStore.ts ui/src/store/__tests__/useGraphStore.test.ts
git commit -m "feat(ui): add egoNodes/egoEdges/setEgoGraph/clearEgoGraph to store"
```

---

## Task 2: Create GraphDataService

**Files:**
- Create: `ui/src/services/GraphDataService.ts`
- Create: `ui/src/services/__tests__/GraphDataService.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// ui/src/services/__tests__/GraphDataService.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { GraphDataService } from '../GraphDataService'

const mockFetch = vi.fn()
global.fetch = mockFetch

beforeEach(() => {
  mockFetch.mockReset()
})

describe('GraphDataService.exploreNode', () => {
  it('fetches from /api/graph/explore with correct params', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ focus_node_id: 'fn', nodes: [], edges: [] }),
    })
    const svc = new GraphDataService()
    const result = await svc.exploreNode('fn', 1, 'both', 1)
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/graph/explore?node_id=fn&depth=1&direction=both'
    )
    expect(result.focus_node_id).toBe('fn')
  })

  it('returns cached result for same graphVersion', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ focus_node_id: 'fn', nodes: [], edges: [] }),
    })
    const svc = new GraphDataService()
    await svc.exploreNode('fn', 1, 'both', 1)
    await svc.exploreNode('fn', 1, 'both', 1)
    expect(mockFetch).toHaveBeenCalledTimes(1)  // cached
  })

  it('invalidates cache when graphVersion changes', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ focus_node_id: 'fn', nodes: [], edges: [] }),
    })
    const svc = new GraphDataService()
    await svc.exploreNode('fn', 1, 'both', 1)
    await svc.exploreNode('fn', 1, 'both', 2)  // new graphVersion
    expect(mockFetch).toHaveBeenCalledTimes(2)
  })
})
```

- [ ] **Step 2: Run — expect FAIL**

```bash
cd ui && bunx vitest run src/services/__tests__/GraphDataService.test.ts
```

- [ ] **Step 3: Create GraphDataService.ts**

```typescript
// ui/src/services/GraphDataService.ts
const API_BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? ''

export interface EgoSubgraph {
  focus_node_id: string
  nodes: Array<{
    id: string
    label: string
    subtext: string
    type: string
    namespace: string
    file_path: string
  }>
  edges: Array<{
    id: string
    source: string
    target: string
    relation_type: string
    confidence: number
  }>
}

export class GraphDataService {
  private cache = new Map<string, EgoSubgraph>()

  async exploreNode(
    nodeId: string,
    depth: number,
    direction: string,
    graphVersion: number
  ): Promise<EgoSubgraph> {
    const key = `${nodeId}:${depth}:${direction}:${graphVersion}`
    if (this.cache.has(key)) return this.cache.get(key)!

    const url = `${API_BASE}/api/graph/explore?node_id=${encodeURIComponent(nodeId)}&depth=${depth}&direction=${direction}`
    const response = await fetch(url)
    if (!response.ok) throw new Error(`Explore API error: ${response.status}`)
    const data = (await response.json()) as EgoSubgraph
    this.cache.set(key, data)
    return data
  }

  clearCache(): void {
    this.cache.clear()
  }
}

// Singleton instance shared across the app
export const graphDataService = new GraphDataService()
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd ui && bunx vitest run src/services/__tests__/GraphDataService.test.ts
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add ui/src/services/GraphDataService.ts ui/src/services/__tests__/GraphDataService.test.ts
git commit -m "feat(ui): add GraphDataService with cached ego-subgraph fetching"
```

---

## Task 3: Create EgoOverlay component

**Files:**
- Create: `ui/src/components/EgoOverlay.tsx`
- Create: `ui/src/components/__tests__/EgoOverlay.test.tsx`

- [ ] **Step 1: Write failing test**

```typescript
// ui/src/components/__tests__/EgoOverlay.test.tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import EgoOverlay from '../EgoOverlay'
import { useGraphStore } from '../../store/useGraphStore'

describe('EgoOverlay', () => {
  it('renders focused node id', () => {
    useGraphStore.setState({ egoNodeId: 'src.pipeline.run', viewMode: 'ego' })
    render(<EgoOverlay depth={1} onDepthChange={vi.fn()} direction="both" onDirectionChange={vi.fn()} />)
    expect(screen.getByText(/src.pipeline.run/)).toBeTruthy()
  })

  it('Back button calls clearEgoGraph', () => {
    const clearEgoGraph = vi.fn()
    useGraphStore.setState({ egoNodeId: 'x', viewMode: 'ego' })
    vi.spyOn(useGraphStore.getState(), 'clearEgoGraph').mockImplementation(clearEgoGraph)
    render(<EgoOverlay depth={1} onDepthChange={vi.fn()} direction="both" onDirectionChange={vi.fn()} />)
    fireEvent.click(screen.getByText('← Back'))
    expect(clearEgoGraph).toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run — expect FAIL**

```bash
cd ui && bunx vitest run src/components/__tests__/EgoOverlay.test.tsx
```

- [ ] **Step 3: Create EgoOverlay.tsx**

```tsx
// ui/src/components/EgoOverlay.tsx
import { useGraphStore } from '../store/useGraphStore'
import sharedStyles from '../shared.module.css'

interface Props {
  depth: number
  onDepthChange: (d: number) => void
  direction: string
  onDirectionChange: (d: string) => void
}

export default function EgoOverlay({ depth, onDepthChange, direction, onDirectionChange }: Props) {
  const egoNodeId = useGraphStore((s) => s.egoNodeId)
  const clearEgoGraph = useGraphStore((s) => s.clearEgoGraph)

  if (!egoNodeId) return null

  const shortId = egoNodeId.split('.').slice(-2).join('.')

  return (
    <div
      style={{
        position: 'absolute',
        top: 12,
        right: 12,
        background: '#1a1a2e',
        border: '1px solid #2d2d6a',
        borderRadius: 8,
        padding: '12px 16px',
        minWidth: 220,
        zIndex: 10,
        color: '#e2e8f0',
        fontSize: 12,
      }}
    >
      <div style={{ fontWeight: 'bold', color: '#60a5fa', marginBottom: 8 }}>
        Ego graph: {shortId}
      </div>

      <label style={{ display: 'block', marginBottom: 6 }}>
        Depth: {depth}
        <input
          type="range"
          min={1}
          max={5}
          value={depth}
          onChange={(e) => onDepthChange(Number(e.target.value))}
          style={{ width: '100%', marginTop: 4 }}
        />
      </label>

      <label style={{ display: 'block', marginBottom: 10 }}>
        Direction:
        <select
          value={direction}
          onChange={(e) => onDirectionChange(e.target.value)}
          style={{ marginLeft: 6, background: '#0d1117', color: '#e2e8f0', border: '1px solid #2d2d6a', borderRadius: 4 }}
        >
          <option value="both">Both</option>
          <option value="downstream">Downstream</option>
          <option value="upstream">Upstream</option>
        </select>
      </label>

      <button className={sharedStyles.btn} onClick={clearEgoGraph}>
        ← Back
      </button>
    </div>
  )
}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd ui && bunx vitest run src/components/__tests__/EgoOverlay.test.tsx
```

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/EgoOverlay.tsx ui/src/components/__tests__/EgoOverlay.test.tsx
git commit -m "feat(ui): add EgoOverlay — depth slider, direction toggle, back button"
```

---

## Task 4: Wire ego overlay into GraphShell

**Files:** `ui/src/components/GraphShell.tsx`, `ui/src/hooks/useLayoutComputation.ts`

- [ ] **Step 1: Add double-click handler and ego node merge in GraphShell**

In `GraphShell.tsx`, add:

```typescript
import { useState, useCallback } from 'react'  // already imported
import EgoOverlay from './EgoOverlay'
import { graphDataService } from '../services/GraphDataService'
import { mapNodeToReactFlow } from '../utils/nodeMapper'
import { mapEdgeToReactFlow } from '../utils/edgeMapper'

// In GraphCanvas():
const egoNodes = useGraphStore((s) => s.egoNodes)
const egoEdges = useGraphStore((s) => s.egoEdges)
const egoNodeId = useGraphStore((s) => s.egoNodeId)
const setEgoNodeId = useGraphStore((s) => s.setEgoNodeId)
const setEgoGraph = useGraphStore((s) => s.setEgoGraph)
const clearEgoGraph = useGraphStore((s) => s.clearEgoGraph)
const graphVersion = useGraphStore((s) => s.graphVersion)

const [egoDepth, setEgoDepth] = useState(1)
const [egoDirection, setEgoDirection] = useState('both')

const onNodeDoubleClick = useCallback(
  async (_event: React.MouseEvent, node: any) => {
    const nodeId = node.id as string
    setEgoNodeId(nodeId)
    try {
      const result = await graphDataService.exploreNode(nodeId, egoDepth, egoDirection, graphVersion)
      // Map API response to ReactFlow format
      const rfNodes = result.nodes.map((n, i) =>
        mapNodeToReactFlow(
          { id: n.id, name: n.label, type: n.type as any, file_path: n.file_path, namespace: n.namespace,
            start_line: 0, end_line: 0, confidence_score: 1, domains: [], ontology_class: '',
            metadata: { namespace: n.namespace } } as any,
          i
        )
      )
      const rfEdges = result.edges.map((e, i) =>
        mapEdgeToReactFlow(
          { source: e.source, target: e.target, type: e.relation_type as any,
            weight: 1, confidence: e.confidence, context: '' } as any,
          i
        )
      )
      setEgoGraph(rfNodes, rfEdges)
    } catch (err) {
      console.error('Failed to load ego-subgraph:', err)
      clearEgoGraph()
    }
  },
  [egoDepth, egoDirection, graphVersion, setEgoNodeId, setEgoGraph, clearEgoGraph]
)
```

Re-fetch when depth/direction changes:

```typescript
useEffect(() => {
  if (!egoNodeId || viewMode !== 'ego') return
  void onNodeDoubleClick({} as any, { id: egoNodeId } as any)
}, [egoDepth, egoDirection])  // eslint-disable-line react-hooks/exhaustive-deps
```

Merge ego nodes into the displayed set:

```typescript
// Replace:
//   const activeNodes = viewMode === 'flow' ? flowNodes : layoutedNodes
// With:
const baseNodes = viewMode === 'flow' ? flowNodes : layoutedNodes
const activeNodes = viewMode === 'ego'
  ? [...baseNodes.map(n => ({ ...n, style: { ...n.style, opacity: 0.3 } })), ...egoNodes]
  : baseNodes

const activeEdges = viewMode === 'flow'
  ? flowEdges
  : viewMode === 'ego'
    ? egoEdges
    : layoutedEdges
```

Add `onNodeDoubleClick` to ReactFlow:

```tsx
<ReactFlow
  nodes={highlightedNodes}
  edges={highlightedEdges}
  onNodeClick={onNodeClick}
  onNodeDoubleClick={onNodeDoubleClick}  // ← add this
  ...
>
```

Add EgoOverlay to JSX (inside the outer div, outside ReactFlow):

```tsx
{viewMode === 'ego' && (
  <EgoOverlay
    depth={egoDepth}
    onDepthChange={setEgoDepth}
    direction={egoDirection}
    onDirectionChange={setEgoDirection}
  />
)}
```

- [ ] **Step 2: Skip layout in ego mode**

In `ui/src/hooks/useLayoutComputation.ts`, update the guard:

```typescript
// Replace:
//   if (viewMode !== 'full' || rawNodes.length === 0) return
// With:
if (viewMode === 'ego' || viewMode === 'flow' || rawNodes.length === 0) return
```

- [ ] **Step 3: Wire Escape key to clearEgoGraph**

In `GraphShell.tsx`, update the keyboard shortcut:

```typescript
onEscape: useCallback(() => {
  if (viewMode === 'ego') clearEgoGraph()
  else if (viewMode === 'flow') setViewMode('full')
}, [viewMode, clearEgoGraph, setViewMode]),
```

- [ ] **Step 4: Build and smoke test**

```bash
cd ui && bun run build 2>&1 | tail -5
```

Start `cgis serve` in background, then start UI dev server:

```bash
# Terminal 1:
uv run cgis ingest ./src --namespace cgis --output graph.db && uv run cgis serve --db graph.db

# Terminal 2:
cd ui && bun run dev
```

In browser: open http://localhost:3000, double-click a node, verify:
- Ego overlay panel appears top-right with node ID
- Other nodes dim to 30% opacity
- Ego edges show (with arrows)
- Depth slider triggers refetch
- Escape clears overlay and restores full graph
- Back button does the same

- [ ] **Step 5: Run all tests**

```bash
cd ui && bun run test:run
make pytest
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(ui): ego-subgraph overlay on double-click — depth slider, direction, Escape"
```

---

## Task 5: Push + PR

- [ ] **Step 1: Push**

```bash
git checkout -b feat/ui-pr4-ego-overlay
git push -u origin feat/ui-pr4-ego-overlay
```

- [ ] **Step 2: Open PR**

```bash
gh pr create \
  --title "feat(ui+cgis): ego-subgraph overlay on double-click (PR 4/4)" \
  --body "$(cat <<'EOF'
## Summary
- Double-click any node → ego-subgraph overlay fetched from GET /api/graph/explore
- GraphDataService caches by graphVersion — stale data impossible
- EgoOverlay panel: focused node ID, depth slider (1-5), direction toggle (both/up/down)
- Escape / Back → restores full graph view
- Completes issue #30 (React-to-Graph API Contract)

## Test plan
- [ ] `bun run test:run` passes
- [ ] `make pytest` passes
- [ ] Dev: double-click node → overlay appears, other nodes dim
- [ ] Depth slider 1→3 → more nodes appear
- [ ] Direction: upstream shows callers only, downstream shows callees only
- [ ] Escape clears overlay
- [ ] Reload graph → ego cache invalidated (no stale results)

Closes: #30
Prerequisite: PR 3
EOF
)"
```
