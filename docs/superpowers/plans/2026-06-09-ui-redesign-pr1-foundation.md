# UI Redesign PR 1: Zustand Foundation + Bug Fixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace App.tsx monolith and callback-chain hooks with a zustand store + GraphShell; fix all known state and rendering bugs.

**Architecture:** Single zustand store with 5 slices (graph/layout/filter/flow/ui). GraphProvider handles data loading. GraphShell is the sole orchestrator. Existing `layout.ts` is untouched — that's PR 2's job.

**Tech Stack:** React 19, @xyflow/react v12, zustand 5, vitest 4, bun

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `ui/src/store/useGraphStore.ts` | Single zustand store — all 5 slices |
| Create | `ui/src/providers/GraphProvider.tsx` | Fetches graph.json, maps to ReactFlow format, calls setGraphData |
| Create | `ui/src/hooks/useLayoutComputation.ts` | Subscribes to store, runs filter pipeline + layoutGraph, calls setLayout |
| Create | `ui/src/components/GraphShell.tsx` | Replaces App.tsx — orchestrates Canvas + Sidebar, no business logic |
| Modify | `ui/src/hooks/useFlowNavigation.ts` | Reads from store instead of props; adds graphVersion to cache key |
| Modify | `ui/src/utils/edgeMapper.ts` | Stable content-hash IDs; markerEnd in both view functions |
| Modify | `ui/src/constants.ts` | Add ALL_EDGE_TYPES module constant |
| Modify | `ui/src/main.tsx` | Wrap with GraphProvider |
| Delete | `ui/src/App.tsx` | Replaced by GraphShell + GraphProvider |
| Delete | `ui/src/hooks/useGraphFetch.ts` | Replaced by GraphProvider |
| Delete | `ui/src/hooks/useGraphFilter.ts` | Replaced by useLayoutComputation + filterSlice |
| Test | `ui/src/store/__tests__/useGraphStore.test.ts` | Store slice unit tests |
| Test | `ui/src/utils/__tests__/edgeMapper.test.ts` | Stable ID + markerEnd tests |

---

## Task 1: Install zustand

**Files:** `ui/package.json`, `ui/bun.lock`

- [ ] **Step 1: Install**

```bash
cd ui && bun add zustand
```

Expected output: `bun add v1.x ... + zustand@5.x.x`

- [ ] **Step 2: Verify**

```bash
cd ui && bun run build 2>&1 | tail -5
```

Expected: build succeeds (no zustand import errors yet).

- [ ] **Step 3: Commit**

```bash
git add ui/package.json ui/bun.lock
git commit -m "chore(ui): add zustand dependency"
```

---

## Task 2: Create zustand store

**Files:**
- Create: `ui/src/store/useGraphStore.ts`
- Create: `ui/src/store/__tests__/useGraphStore.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// ui/src/store/__tests__/useGraphStore.test.ts
import { describe, it, expect, beforeEach } from 'vitest'
import { useGraphStore } from '../useGraphStore'

beforeEach(() => {
  // Reset store between tests
  useGraphStore.setState({
    rawNodes: [],
    rawEdges: [],
    namespaces: [],
    graphVersion: 0,
    layoutedNodes: [],
    layoutedEdges: [],
    expandedFiles: new Set(),
    islandPositions: new Map(),
    activeEdgeTypes: new Set(['CALLS', 'IMPORTS', 'EXTENDS', 'CONTAINS', 'DECLARES']),
    showExternal: false,
    activeNamespaces: new Set(),
    flowNodes: [],
    flowEdges: [],
    flowCache: new Map(),
    viewMode: 'full',
    hoveredNodeId: null,
    egoNodeId: null,
  })
})

describe('graphSlice', () => {
  it('setGraphData bumps graphVersion', () => {
    const { setGraphData, graphVersion } = useGraphStore.getState()
    expect(graphVersion).toBe(0)
    setGraphData([], [])
    expect(useGraphStore.getState().graphVersion).toBe(1)
    setGraphData([], [])
    expect(useGraphStore.getState().graphVersion).toBe(2)
  })

  it('setGraphData extracts namespaces from node data', () => {
    const nodes = [
      { id: 'a', data: { namespace: 'owner-api' }, position: { x: 0, y: 0 } },
      { id: 'b', data: { namespace: 'owner-web' }, position: { x: 0, y: 0 } },
      { id: 'c', data: { namespace: 'owner-api' }, position: { x: 0, y: 0 } },
    ] as any[]
    useGraphStore.getState().setGraphData(nodes, [])
    expect(useGraphStore.getState().namespaces).toEqual(
      expect.arrayContaining(['owner-api', 'owner-web'])
    )
    expect(useGraphStore.getState().namespaces).toHaveLength(2)
  })

  it('setGraphData invalidates flow cache', () => {
    useGraphStore.setState({ flowCache: new Map([['key', { nodes: [], edges: [] }]]) })
    useGraphStore.getState().setGraphData([], [])
    expect(useGraphStore.getState().flowCache.size).toBe(0)
  })
})

describe('filterSlice', () => {
  it('toggleEdgeType removes then restores', () => {
    const { toggleEdgeType } = useGraphStore.getState()
    toggleEdgeType('CALLS')
    expect(useGraphStore.getState().activeEdgeTypes.has('CALLS')).toBe(false)
    toggleEdgeType('CALLS')
    expect(useGraphStore.getState().activeEdgeTypes.has('CALLS')).toBe(true)
  })

  it('toggleExternal flips showExternal', () => {
    expect(useGraphStore.getState().showExternal).toBe(false)
    useGraphStore.getState().toggleExternal()
    expect(useGraphStore.getState().showExternal).toBe(true)
  })
})

describe('layoutSlice', () => {
  it('toggleExpandedFile adds then removes fileId', () => {
    const { toggleExpandedFile } = useGraphStore.getState()
    toggleExpandedFile('file-a')
    expect(useGraphStore.getState().expandedFiles.has('file-a')).toBe(true)
    toggleExpandedFile('file-a')
    expect(useGraphStore.getState().expandedFiles.has('file-a')).toBe(false)
  })

  it('setIslandPosition stores offset', () => {
    useGraphStore.getState().setIslandPosition('owner-api', { x: 100, y: 200 })
    expect(useGraphStore.getState().islandPositions.get('owner-api')).toEqual({ x: 100, y: 200 })
  })
})

describe('uiSlice', () => {
  it('setViewMode updates viewMode', () => {
    useGraphStore.getState().setViewMode('flow')
    expect(useGraphStore.getState().viewMode).toBe('flow')
  })
})
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
cd ui && bunx vitest run src/store/__tests__/useGraphStore.test.ts
```

Expected: `Cannot find module '../useGraphStore'`

- [ ] **Step 3: Create the store**

```typescript
// ui/src/store/useGraphStore.ts
import { create } from 'zustand'
import type { Node, Edge } from '@xyflow/react'

type EdgeTypeName = 'CALLS' | 'IMPORTS' | 'EXTENDS' | 'CONTAINS' | 'DECLARES'
type ViewMode = 'full' | 'flow' | 'ego'

interface FlowCacheEntry { nodes: Node[]; edges: Edge[] }

const ALL_EDGE_TYPE_NAMES: EdgeTypeName[] = ['CALLS', 'IMPORTS', 'EXTENDS', 'CONTAINS', 'DECLARES']

interface GraphStore {
  // graph slice
  rawNodes: Node[]
  rawEdges: Edge[]
  namespaces: string[]
  graphVersion: number
  setGraphData: (nodes: Node[], edges: Edge[]) => void

  // layout slice
  layoutedNodes: Node[]
  layoutedEdges: Edge[]
  expandedFiles: Set<string>
  islandPositions: Map<string, { x: number; y: number }>
  setLayout: (nodes: Node[], edges: Edge[]) => void
  toggleExpandedFile: (fileId: string) => void
  setIslandPosition: (namespace: string, pos: { x: number; y: number }) => void

  // filter slice
  activeEdgeTypes: Set<EdgeTypeName>
  showExternal: boolean
  activeNamespaces: Set<string>
  toggleEdgeType: (type: EdgeTypeName) => void
  toggleExternal: () => void
  toggleNamespace: (ns: string) => void

  // flow slice
  flowNodes: Node[]
  flowEdges: Edge[]
  flowCache: Map<string, FlowCacheEntry>
  setFlow: (nodes: Node[], edges: Edge[]) => void
  cacheFlow: (key: string, entry: FlowCacheEntry) => void

  // ui slice
  viewMode: ViewMode
  hoveredNodeId: string | null
  egoNodeId: string | null
  setViewMode: (mode: ViewMode) => void
  setHoveredNodeId: (id: string | null) => void
  setEgoNodeId: (id: string | null) => void
}

export const useGraphStore = create<GraphStore>((set) => ({
  // graph slice
  rawNodes: [],
  rawEdges: [],
  namespaces: [],
  graphVersion: 0,
  setGraphData: (nodes, edges) => {
    const namespaces = [
      ...new Set(
        nodes
          .map((n) => (n.data as Record<string, unknown>)?.namespace as string | undefined)
          .filter((ns): ns is string => typeof ns === 'string' && ns.length > 0)
      ),
    ]
    set((state) => ({
      rawNodes: nodes,
      rawEdges: edges,
      namespaces,
      graphVersion: state.graphVersion + 1,
      activeNamespaces: new Set(namespaces),
      flowCache: new Map(),
    }))
  },

  // layout slice
  layoutedNodes: [],
  layoutedEdges: [],
  expandedFiles: new Set(),
  islandPositions: new Map(),
  setLayout: (nodes, edges) => set({ layoutedNodes: nodes, layoutedEdges: edges }),
  toggleExpandedFile: (fileId) =>
    set((state) => {
      const next = new Set(state.expandedFiles)
      if (next.has(fileId)) next.delete(fileId)
      else next.add(fileId)
      return { expandedFiles: next }
    }),
  setIslandPosition: (namespace, pos) =>
    set((state) => {
      const next = new Map(state.islandPositions)
      next.set(namespace, pos)
      return { islandPositions: next }
    }),

  // filter slice
  activeEdgeTypes: new Set(ALL_EDGE_TYPE_NAMES),
  showExternal: false,
  activeNamespaces: new Set(),
  toggleEdgeType: (type) =>
    set((state) => {
      const next = new Set(state.activeEdgeTypes)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return { activeEdgeTypes: next }
    }),
  toggleExternal: () => set((state) => ({ showExternal: !state.showExternal })),
  toggleNamespace: (ns) =>
    set((state) => {
      const next = new Set(state.activeNamespaces)
      if (next.has(ns)) next.delete(ns)
      else next.add(ns)
      return { activeNamespaces: next }
    }),

  // flow slice
  flowNodes: [],
  flowEdges: [],
  flowCache: new Map(),
  setFlow: (nodes, edges) => set({ flowNodes: nodes, flowEdges: edges }),
  cacheFlow: (key, entry) =>
    set((state) => {
      const next = new Map(state.flowCache)
      next.set(key, entry)
      return { flowCache: next }
    }),

  // ui slice
  viewMode: 'full',
  hoveredNodeId: null,
  egoNodeId: null,
  setViewMode: (mode) => set({ viewMode: mode }),
  setHoveredNodeId: (id) => set({ hoveredNodeId: id }),
  setEgoNodeId: (id) => set({ egoNodeId: id }),
}))
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd ui && bunx vitest run src/store/__tests__/useGraphStore.test.ts
```

Expected: `✓ 9 tests passed`

- [ ] **Step 5: Commit**

```bash
git add ui/src/store/useGraphStore.ts ui/src/store/__tests__/useGraphStore.test.ts
git commit -m "feat(ui): add zustand store with graph/layout/filter/flow/ui slices"
```

---

## Task 3: Create GraphProvider

**Files:**
- Create: `ui/src/providers/GraphProvider.tsx`

- [ ] **Step 1: Create the file**

```tsx
// ui/src/providers/GraphProvider.tsx
import { useEffect } from 'react'
import { useGraphStore } from '../store/useGraphStore'
import { mapNodeToReactFlow } from '../utils/nodeMapper'
import { mapEdgeToReactFlow } from '../utils/edgeMapper'
import type { GraphData } from '../types'

const GRAPH_URL =
  (import.meta.env.VITE_GRAPH_DATA_URL as string | undefined) ?? '/graph.json'

export function GraphProvider({ children }: { children: React.ReactNode }) {
  const setGraphData = useGraphStore((s) => s.setGraphData)

  useEffect(() => {
    fetch(GRAPH_URL)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status} loading graph`)
        return r.json() as Promise<GraphData>
      })
      .then((data) =>
        setGraphData(
          data.nodes.map((n, i) => mapNodeToReactFlow(n, i)),
          data.edges.map((e, i) => mapEdgeToReactFlow(e, i))
        )
      )
      .catch((err) => console.error('GraphProvider: failed to load graph', err))
  }, [setGraphData])

  return <>{children}</>
}
```

- [ ] **Step 2: Verify TypeScript**

```bash
cd ui && bunx tsc --noEmit 2>&1 | grep -i "graphprovider\|error" | head -10
```

Expected: no errors for GraphProvider.tsx.

- [ ] **Step 3: Commit**

```bash
git add ui/src/providers/GraphProvider.tsx
git commit -m "feat(ui): add GraphProvider — loads graph.json into zustand store"
```

---

## Task 4: Fix edgeMapper — stable IDs + markerEnd

**Files:**
- Modify: `ui/src/utils/edgeMapper.ts`
- Create: `ui/src/utils/__tests__/edgeMapper.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// ui/src/utils/__tests__/edgeMapper.test.ts
import { describe, it, expect } from 'vitest'
import { MarkerType } from '@xyflow/react'
import { mapEdgeToReactFlow, mapEdgeToFlowView } from '../edgeMapper'
import type { GraphEdge } from '../../types'

const edge: GraphEdge = {
  source: 'src.pipeline.IngestionPipeline.run',
  target: 'src.resolver.engine.ResolverEngine.resolve',
  type: 'CALLS',
  weight: 1,
  confidence: 1.0,
  context: '',
}

describe('mapEdgeToReactFlow', () => {
  it('generates stable content-hash ID', () => {
    const e1 = mapEdgeToReactFlow(edge, 0)
    const e2 = mapEdgeToReactFlow(edge, 99)  // index doesn't matter
    expect(e1.id).toBe(e2.id)
    expect(e1.id).toBe(
      'src.pipeline.IngestionPipeline.run→src.resolver.engine.ResolverEngine.resolve:CALLS'
    )
  })

  it('includes markerEnd with ArrowClosed', () => {
    const e = mapEdgeToReactFlow(edge, 0)
    expect(e.markerEnd).toBeDefined()
    expect((e.markerEnd as { type: string }).type).toBe(MarkerType.ArrowClosed)
  })
})

describe('mapEdgeToFlowView', () => {
  it('generates same stable ID as mapEdgeToReactFlow', () => {
    const full = mapEdgeToReactFlow(edge, 0)
    const flow = mapEdgeToFlowView(edge, 0)
    expect(flow.id).toBe(full.id)
  })

  it('includes markerEnd with ArrowClosed', () => {
    const e = mapEdgeToFlowView(edge, 0)
    expect(e.markerEnd).toBeDefined()
    expect((e.markerEnd as { type: string }).type).toBe(MarkerType.ArrowClosed)
  })

  it('is animated', () => {
    const e = mapEdgeToFlowView(edge, 0)
    expect(e.animated).toBe(true)
  })
})
```

- [ ] **Step 2: Run — expect FAIL**

```bash
cd ui && bunx vitest run src/utils/__tests__/edgeMapper.test.ts
```

Expected: `AssertionError: expected 'e-0' to be 'src.pipeline…'`

- [ ] **Step 3: Replace edgeMapper.ts**

```typescript
// ui/src/utils/edgeMapper.ts
import { MarkerType, type Edge } from '@xyflow/react'
import type { GraphEdge } from '../types'
import { EDGE_COLORS } from '../theme'

const FULL_EDGE_STYLES: Record<string, { stroke: string; strokeWidth: number; opacity: number }> =
  {}
for (const [type, stroke] of Object.entries(EDGE_COLORS)) {
  FULL_EDGE_STYLES[type] = { stroke, strokeWidth: 1.5, opacity: 0.8 }
}
FULL_EDGE_STYLES.DECLARES = { stroke: EDGE_COLORS.DECLARES, strokeWidth: 1, opacity: 0.6 }

function stableEdgeId(source: string, target: string, type: string): string {
  return `${source}→${target}:${type}`
}

export function mapEdgeToReactFlow(edge: GraphEdge, _index: number): Edge {
  const style = FULL_EDGE_STYLES[edge.type] ?? FULL_EDGE_STYLES.DECLARES
  return {
    id: stableEdgeId(edge.source, edge.target, edge.type),
    source: edge.source,
    target: edge.target,
    style,
    animated: false,
    type: 'default',
    markerEnd: { type: MarkerType.ArrowClosed, color: style.stroke },
    data: { edgeType: edge.type },
  } as Edge
}

export function mapEdgeToFlowView(edge: GraphEdge, _index: number): Edge {
  const color = '#ff9800'
  return {
    id: stableEdgeId(edge.source, edge.target, edge.type),
    source: edge.source,
    target: edge.target,
    animated: true,
    style: { stroke: color, strokeWidth: 2 },
    markerEnd: { type: MarkerType.ArrowClosed, color },
    data: { edgeType: edge.type },
  } as Edge
}
```

- [ ] **Step 4: Run — expect PASS**

```bash
cd ui && bunx vitest run src/utils/__tests__/edgeMapper.test.ts
```

Expected: `✓ 5 tests passed`

- [ ] **Step 5: Commit**

```bash
git add ui/src/utils/edgeMapper.ts ui/src/utils/__tests__/edgeMapper.test.ts
git commit -m "fix(ui): stable edge IDs and markerEnd in both view mappers"
```

---

## Task 5: Fix constants.ts — module-level ALL_EDGE_TYPES

**Files:**
- Modify: `ui/src/constants.ts`

- [ ] **Step 1: Add constant**

Add at the end of `ui/src/constants.ts`:

```typescript
// All edge type names — module-level constant, never re-created on render
export const ALL_EDGE_TYPES = ['CALLS', 'IMPORTS', 'EXTENDS', 'DECLARES'] as const
export type EdgeTypeName = (typeof ALL_EDGE_TYPES)[number]
```

- [ ] **Step 2: Commit**

```bash
git add ui/src/constants.ts
git commit -m "fix(ui): move ALL_EDGE_TYPES to module constant, add EdgeTypeName type"
```

---

## Task 6: Create useLayoutComputation

**Files:**
- Create: `ui/src/hooks/useLayoutComputation.ts`

This hook subscribes to the store's filter/expand state and triggers `layout.ts` when `viewMode === 'full'`. It replaces `useGraphFilter`'s layout side-effect.

- [ ] **Step 1: Create the hook**

```typescript
// ui/src/hooks/useLayoutComputation.ts
import { useEffect, useRef } from 'react'
import { useReactFlow } from '@xyflow/react'
import { useGraphStore } from '../store/useGraphStore'
import { getCollapsedView } from '../collapse'
import { layoutGraph } from '../layout'
import { aggregateEdges } from '../utils/aggregateEdges'

export function useLayoutComputation(): void {
  const { fitView } = useReactFlow()
  const generationRef = useRef(0)

  const rawNodes = useGraphStore((s) => s.rawNodes)
  const rawEdges = useGraphStore((s) => s.rawEdges)
  const expandedFiles = useGraphStore((s) => s.expandedFiles)
  const activeEdgeTypes = useGraphStore((s) => s.activeEdgeTypes)
  const showExternal = useGraphStore((s) => s.showExternal)
  const viewMode = useGraphStore((s) => s.viewMode)
  const setLayout = useGraphStore((s) => s.setLayout)

  useEffect(() => {
    if (viewMode !== 'full' || rawNodes.length === 0) return

    const gen = ++generationRef.current

    void (async () => {
      // Build parentChildren map from groupId metadata (FILE container logic)
      const parentChildren = new Map<string, string[]>()
      for (const node of rawNodes) {
        const groupId = (node.data as Record<string, unknown>)?.groupId as string | undefined
        if (groupId) {
          const children = parentChildren.get(groupId) ?? []
          children.push(node.id)
          parentChildren.set(groupId, children)
        }
      }

      // Step 1: collapse FILE nodes based on expandedFiles
      const collapsed = getCollapsedView(
        rawNodes as any[],
        rawEdges as any[],
        expandedFiles,
        parentChildren
      )

      // Step 2: filter edges by active types (always keep CONTAINS for structure)
      const typeFiltered = (collapsed.edges as any[]).filter((e) => {
        const et: string = e.data?.edgeType ?? ''
        return et === 'CONTAINS' || activeEdgeTypes.has(et as any)
      })

      // Step 3: aggregate parallel edges (reduces clutter)
      const aggregated = aggregateEdges(typeFiltered)

      // Step 4: filter external nodes if showExternal is false
      const visibleNodes = showExternal
        ? (collapsed.nodes as any[])
        : (collapsed.nodes as any[]).filter(
            (n) => !n.data?.namespace || n.data?.namespace === 'INTERNAL'
          )
      const visibleIds = new Set(visibleNodes.map((n) => n.id as string))
      const visibleEdges = aggregated.filter(
        (e) => visibleIds.has(e.source as string) && visibleIds.has(e.target as string)
      )

      // Step 5: add ▶/▼ indicator labels to FILE nodes
      const labeledNodes = visibleNodes.map((n) => {
        if (n.data?.nodeType !== 'FILE') return n
        const indicator = expandedFiles.has(n.id as string) ? '▼ ' : '▶ '
        return {
          ...n,
          data: {
            ...n.data,
            label: indicator + (n.data.label as string).replace(/^[▶▼]\s/, ''),
            isExpanded: expandedFiles.has(n.id as string),
          },
        }
      })

      // Step 6: run dagre layout
      const { nodes: layoutedNodes, edges: layoutedEdges } = await layoutGraph(
        labeledNodes,
        visibleEdges,
        expandedFiles,
        parentChildren
      )

      if (generationRef.current !== gen) return  // stale async, discard

      setLayout(layoutedNodes, layoutedEdges)
      fitView({ padding: 0.15, duration: 250 })
    })()
  }, [rawNodes, rawEdges, expandedFiles, activeEdgeTypes, showExternal, viewMode, setLayout, fitView])
}
```

- [ ] **Step 2: Type-check**

```bash
cd ui && bunx tsc --noEmit 2>&1 | grep "useLayoutComputation" | head -5
```

Expected: no errors for useLayoutComputation.ts

- [ ] **Step 3: Commit**

```bash
git add ui/src/hooks/useLayoutComputation.ts
git commit -m "feat(ui): add useLayoutComputation — replaces useGraphFilter side-effect"
```

---

## Task 7: Rewrite useFlowNavigation — read from store + graphVersion cache key

**Files:**
- Modify: `ui/src/hooks/useFlowNavigation.ts`

- [ ] **Step 1: Replace file content**

```typescript
// ui/src/hooks/useFlowNavigation.ts
import { useCallback, useRef } from 'react'
import { useReactFlow, type Node } from '@xyflow/react'
import { buildExecutionFlow } from '../flow'
import { layoutGraph } from '../layout'
import { mapNodeToFlowView } from '../utils/nodeMapper'
import { mapEdgeToFlowView } from '../utils/edgeMapper'
import { useGraphStore } from '../store/useGraphStore'

const FLOW_DEPTH = 3
const DEFAULT_ALLOWED = ['CALLS', 'IMPORTS', 'CONTAINS']

export function useFlowNavigation(allowedEdgeTypes: string[] = DEFAULT_ALLOWED) {
  const rawNodes = useGraphStore((s) => s.rawNodes)
  const rawEdges = useGraphStore((s) => s.rawEdges)
  const graphVersion = useGraphStore((s) => s.graphVersion)
  const setFlow = useGraphStore((s) => s.setFlow)
  const setViewMode = useGraphStore((s) => s.setViewMode)
  const { fitView } = useReactFlow()

  // Local ref cache — keyed by graphVersion so stale data is never served
  const cacheRef = useRef<Map<string, { nodes: any[]; edges: any[] }>>(new Map())

  const onNodeClick = useCallback(
    async (_event: React.MouseEvent, node: Node) => {
      if (node.type === 'group' || node.type === 'fileContainer') return

      const cacheKey = `${node.id}:${FLOW_DEPTH}:${allowedEdgeTypes.join(',')}:${graphVersion}`

      let flow: { nodes: any[]; edges: any[] }
      if (cacheRef.current.has(cacheKey)) {
        flow = cacheRef.current.get(cacheKey)!
      } else {
        const graphData = { nodes: rawNodes as any[], edges: rawEdges as any[] }
        flow = buildExecutionFlow(graphData, node.id, FLOW_DEPTH, 'both', allowedEdgeTypes)
        cacheRef.current.set(cacheKey, flow)
      }

      const flowNodes = flow.nodes.map((n) =>
        mapNodeToFlowView(n, { isRoot: n.id === node.id })
      )
      const flowEdges = flow.edges.map((e, i) => mapEdgeToFlowView(e, i))
      const { nodes: layoutedNodes } = await layoutGraph(flowNodes, flowEdges)

      setFlow(layoutedNodes, flowEdges)
      setViewMode('flow')
      fitView({ padding: 0.15, duration: 250 })
    },
    [rawNodes, rawEdges, graphVersion, allowedEdgeTypes, setFlow, setViewMode, fitView]
  )

  return { onNodeClick }
}
```

- [ ] **Step 2: Type-check**

```bash
cd ui && bunx tsc --noEmit 2>&1 | grep "useFlowNavigation" | head -5
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add ui/src/hooks/useFlowNavigation.ts
git commit -m "fix(ui): useFlowNavigation reads from store, graphVersion invalidates cache"
```

---

## Task 8: Create GraphShell — replaces App.tsx

**Files:**
- Create: `ui/src/components/GraphShell.tsx`

GraphShell is a thin orchestrator. No useState for data. All state lives in the store.

- [ ] **Step 1: Create GraphShell.tsx**

```tsx
// ui/src/components/GraphShell.tsx
import { useCallback, useMemo, useRef, useState } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useReactFlow,
  Panel,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { ErrorBoundary, type FallbackProps } from 'react-error-boundary'

import { useGraphStore } from '../store/useGraphStore'
import { useLayoutComputation } from '../hooks/useLayoutComputation'
import { useFlowNavigation } from '../hooks/useFlowNavigation'
import { useSearch } from '../hooks/useSearch'
import { useExport } from '../hooks/useExport'
import { useKeyboardShortcuts } from '../hooks/useKeyboardShortcuts'
import { applyContextHighlight } from '../utils/applyHighlight'

import ControlPanel from './ControlPanel'
import StatsPanel from './StatsPanel'
import NodeTooltip from './NodeTooltip'
import LegendPanel from './LegendPanel'
import LoadingOverlay from './LoadingOverlay'
import FileContainerNode from './FileContainerNode'

import sharedStyles from '../shared.module.css'

function ErrorFallback({ error, resetErrorBoundary }: FallbackProps) {
  return (
    <div style={{ padding: 24, color: '#f87171' }}>
      <h2>Something went wrong</h2>
      <p>{error instanceof Error ? error.message : String(error)}</p>
      <button className={sharedStyles.btn} onClick={resetErrorBoundary}>
        Try again
      </button>
    </div>
  )
}

function GraphCanvas() {
  const { fitView } = useReactFlow()
  const searchInputRef = useRef<HTMLInputElement>(null)
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 })

  // Store subscriptions — granular to avoid unnecessary re-renders
  const layoutedNodes = useGraphStore((s) => s.layoutedNodes)
  const layoutedEdges = useGraphStore((s) => s.layoutedEdges)
  const flowNodes = useGraphStore((s) => s.flowNodes)
  const flowEdges = useGraphStore((s) => s.flowEdges)
  const viewMode = useGraphStore((s) => s.viewMode)
  const hoveredNodeId = useGraphStore((s) => s.hoveredNodeId)
  const graphVersion = useGraphStore((s) => s.graphVersion)
  const setViewMode = useGraphStore((s) => s.setViewMode)
  const setHoveredNodeId = useGraphStore((s) => s.setHoveredNodeId)
  const toggleExpandedFile = useGraphStore((s) => s.toggleExpandedFile)
  const activeEdgeTypes = useGraphStore((s) => s.activeEdgeTypes)
  const showExternal = useGraphStore((s) => s.showExternal)
  const toggleEdgeType = useGraphStore((s) => s.toggleEdgeType)
  const toggleExternal = useGraphStore((s) => s.toggleExternal)

  // Hooks — run layout computation; subscribe to filter state changes
  useLayoutComputation()

  // Flow navigation on node click
  const { onNodeClick: onFlowClick } = useFlowNavigation()

  // Search filters displayed nodes
  const activeNodes = viewMode === 'flow' ? flowNodes : layoutedNodes
  const { searchQuery, setSearchQuery, displayedNodes } = useSearch(activeNodes)

  // Export to PNG / SVG
  const { exportPng, exportSvg } = useExport(
    useCallback((dataUrl: string, filename: string) => {
      const a = document.createElement('a')
      a.setAttribute('download', filename)
      a.setAttribute('href', dataUrl)
      a.click()
    }, [])
  )

  // Keyboard shortcuts
  useKeyboardShortcuts({
    onEscape: useCallback(() => {
      if (viewMode === 'flow') setViewMode('full')
    }, [viewMode, setViewMode]),
    onFit: useCallback(() => fitView({ padding: 0.15, duration: 250 }), [fitView]),
    onFocusSearch: useCallback(() => searchInputRef.current?.focus(), []),
  })

  // Highlight connected nodes/edges on hover
  const activeEdges = viewMode === 'flow' ? flowEdges : layoutedEdges
  const { nodes: highlightedNodes, edges: highlightedEdges } = useMemo(
    () => applyContextHighlight(displayedNodes, activeEdges, hoveredNodeId),
    [displayedNodes, activeEdges, hoveredNodeId]
  )

  const nodeTypes = useMemo(() => ({ fileContainer: FileContainerNode }), [])

  const onNodeClick = useCallback(
    (event: React.MouseEvent, node: any) => {
      if (viewMode === 'full' && node.data?.nodeType === 'FILE') {
        toggleExpandedFile(node.id as string)
      } else {
        void onFlowClick(event, node)
      }
    },
    [viewMode, toggleExpandedFile, onFlowClick]
  )

  const graphLoading = graphVersion === 0

  return (
    <div style={{ width: '100vw', height: '100vh' }}>
      {graphLoading && <LoadingOverlay />}
      <ReactFlow
        nodes={highlightedNodes}
        edges={highlightedEdges}
        onNodeClick={onNodeClick}
        onNodeMouseEnter={(_, node) => setHoveredNodeId(node.id)}
        onNodeMouseLeave={() => setHoveredNodeId(null)}
        onMouseMove={(e) => setMousePos({ x: e.clientX, y: e.clientY })}
        nodeTypes={nodeTypes}
        fitView
      >
        <Background />
        <Controls />
        <MiniMap />
        <Panel position="top-left">
          <ControlPanel
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            searchInputRef={searchInputRef}
            onExportPng={exportPng}
            onExportSvg={exportSvg}
            onFitView={() => fitView({ padding: 0.15, duration: 250 })}
            onBack={() => setViewMode('full')}
            viewMode={viewMode}
            activeEdgeTypes={activeEdgeTypes}
            onToggleEdgeType={toggleEdgeType}
            showExternal={showExternal}
            onToggleExternal={toggleExternal}
          />
        </Panel>
        <Panel position="bottom-right">
          <StatsPanel nodes={layoutedNodes} edges={layoutedEdges} />
        </Panel>
        <Panel position="bottom-left">
          <LegendPanel />
        </Panel>
      </ReactFlow>
      {hoveredNodeId && (
        <NodeTooltip
          node={highlightedNodes.find((n) => n.id === hoveredNodeId) ?? null}
          mouseX={mousePos.x}
          mouseY={mousePos.y}
        />
      )}
    </div>
  )
}

export default function GraphShell() {
  return (
    <ErrorBoundary FallbackComponent={ErrorFallback}>
      <GraphCanvas />
    </ErrorBoundary>
  )
}
```

- [ ] **Step 2: Verify TypeScript — expect errors about ControlPanel props**

```bash
cd ui && bunx tsc --noEmit 2>&1 | grep "GraphShell\|ControlPanel" | head -10
```

If ControlPanel doesn't accept `activeEdgeTypes`/`onToggleEdgeType`/`showExternal`/`onToggleExternal`, its interface needs updating. Check `ui/src/components/ControlPanel.tsx` and add the missing props to its interface. The new props replace the old `useGraphFilter` setters that were passed via App.tsx.

- [ ] **Step 3: Commit**

```bash
git add ui/src/components/GraphShell.tsx
git commit -m "feat(ui): add GraphShell — thin orchestrator replacing App.tsx"
```

---

## Task 9: Update main.tsx — wire GraphProvider

**Files:**
- Modify: `ui/src/main.tsx`

- [ ] **Step 1: Replace main.tsx**

```tsx
// ui/src/main.tsx
import { StrictMode } from 'react'
import ReactDOM from 'react-dom/client'
import { ReactFlowProvider } from '@xyflow/react'
import './tokens.css'
import './index.css'
import { GraphProvider } from './providers/GraphProvider'
import GraphShell from './components/GraphShell'

const rootElement = document.getElementById('root')
if (!rootElement) throw new Error('Failed to find the root element')

ReactDOM.createRoot(rootElement).render(
  <StrictMode>
    <ReactFlowProvider>
      <GraphProvider>
        <GraphShell />
      </GraphProvider>
    </ReactFlowProvider>
  </StrictMode>
)
```

- [ ] **Step 2: Full type-check**

```bash
cd ui && bunx tsc --noEmit 2>&1
```

Fix any remaining type errors. Common issues:
- `ControlPanel` props mismatch — add missing props to its interface
- `applyContextHighlight` third arg type — may need `string | null`

- [ ] **Step 3: Start dev server and verify graph renders**

```bash
cd ui && bun run dev
```

Open http://localhost:3000. Expected: graph loads, nodes visible, no console errors.

- [ ] **Step 4: Commit**

```bash
git add ui/src/main.tsx
git commit -m "feat(ui): wire GraphProvider into app entry point"
```

---

## Task 10: Delete dead code

**Files to delete:** `ui/src/App.tsx`, `ui/src/hooks/useGraphFetch.ts`, `ui/src/hooks/useGraphFilter.ts`

- [ ] **Step 1: Delete files**

```bash
git rm ui/src/App.tsx ui/src/hooks/useGraphFetch.ts ui/src/hooks/useGraphFilter.ts
```

- [ ] **Step 2: Check no remaining imports**

```bash
cd ui && grep -r "useGraphFetch\|useGraphFilter\|from.*App" src/ --include="*.ts" --include="*.tsx"
```

Expected: no output.

- [ ] **Step 3: Full build**

```bash
cd ui && bun run build 2>&1 | tail -10
```

Expected: build succeeds with no errors.

- [ ] **Step 4: Run all tests**

```bash
cd ui && bun run test:run
```

Expected: all existing + new tests pass.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore(ui): remove App.tsx, useGraphFetch, useGraphFilter — replaced by store+GraphShell"
```

---

## Task 11: Push branch + open PR

- [ ] **Step 1: Ensure on a feature branch**

```bash
git checkout -b feat/ui-pr1-foundation 2>/dev/null || git checkout feat/ui-pr1-foundation
```

- [ ] **Step 2: Push**

```bash
git push -u origin feat/ui-pr1-foundation
```

- [ ] **Step 3: Open PR**

```bash
gh pr create \
  --title "feat(ui): zustand foundation + bug fixes (PR 1/4)" \
  --body "$(cat <<'EOF'
## Summary
- Replaces App.tsx (232 lines) with zustand store + GraphShell + GraphProvider
- Fixes stable edge IDs (`src→tgt:type` instead of index-based)
- Fixes missing `markerEnd` in flow view edges
- Fixes stale flow cache (graphVersion in cache key)
- Moves ALL_EDGE_TYPES to module constant (was re-created every render)

## Test plan
- [ ] `bun run test:run` passes
- [ ] Dev server: graph renders, filter toggles work, flow view has arrows
- [ ] Flow view: click a node → arrows visible on edges
- [ ] Filter edge types: toggle CALLS → CALLS edges disappear
- [ ] Reload page → flow cache cleared, fresh layout

Closes: part of #31
See spec: `docs/superpowers/specs/2026-06-09-ui-redesign-design.md`
EOF
)"
```
