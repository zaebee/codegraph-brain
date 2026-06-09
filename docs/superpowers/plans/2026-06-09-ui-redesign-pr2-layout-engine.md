# UI Redesign PR 2: IslandLayoutEngine

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `layout.ts` dagre calls with `IslandLayoutEngine` — a 3-phase pipeline that groups nodes into per-namespace islands and places them with greedy row bin-packing.

**Architecture:** `IslandLayoutEngine` is a pure class (no React, no store). `useLayoutComputation` from PR 1 replaces its call to `layoutGraph()` with a call to `IslandLayoutEngine.run()`. A new `IslandContainerNode` React component renders the per-namespace cluster border.

**Tech Stack:** React 19, @xyflow/react v12, dagre, vitest 4, bun

**Prerequisite:** PR 1 merged.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `ui/src/layout/IslandLayoutEngine.ts` | 3-phase layout: partition → layoutIsland → placeIslands |
| Create | `ui/src/layout/index.ts` | Re-exports (keeps `layoutGraph` import path working for callers) |
| Create | `ui/src/components/IslandContainerNode.tsx` | ReactFlow custom node — renders namespace cluster border |
| Modify | `ui/src/hooks/useLayoutComputation.ts` | Replace `layoutGraph()` call with `IslandLayoutEngine.run()` |
| Modify | `ui/src/components/GraphShell.tsx` | Add `islandContainer` to nodeTypes map |
| Modify | `ui/src/constants.ts` | Add `CANVAS_MAX_WIDTH = 3200` and `ISLAND_GAP = 80` |
| Delete | `ui/src/layout.ts` | Replaced by IslandLayoutEngine |
| Test | `ui/src/layout/__tests__/IslandLayoutEngine.test.ts` | Phase unit tests + bin-packing edge cases |

---

## Task 1: Add CANVAS_MAX_WIDTH and ISLAND_GAP constants

**Files:** `ui/src/constants.ts`

- [ ] **Step 1: Add constants**

Append to `ui/src/constants.ts`:

```typescript
export const CANVAS_MAX_WIDTH = 3200
export const ISLAND_GAP = 80  // padding between islands in bin-packing
```

- [ ] **Step 2: Commit**

```bash
git add ui/src/constants.ts
git commit -m "chore(ui): add CANVAS_MAX_WIDTH and ISLAND_GAP layout constants"
```

---

## Task 2: Create IslandLayoutEngine

**Files:**
- Create: `ui/src/layout/IslandLayoutEngine.ts`
- Create: `ui/src/layout/__tests__/IslandLayoutEngine.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// ui/src/layout/__tests__/IslandLayoutEngine.test.ts
import { describe, it, expect } from 'vitest'
import { IslandLayoutEngine } from '../IslandLayoutEngine'
import type { Node, Edge } from '@xyflow/react'

function makeNode(id: string, namespace: string): Node {
  return {
    id,
    position: { x: 0, y: 0 },
    data: { namespace, nodeType: 'FUNCTION', label: id },
  }
}

function makeEdge(source: string, target: string, type = 'CALLS'): Edge {
  return {
    id: `${source}→${target}:${type}`,
    source,
    target,
    data: { edgeType: type },
  } as Edge
}

describe('IslandLayoutEngine.partition', () => {
  it('groups nodes by namespace', () => {
    const nodes = [
      makeNode('a', 'py'),
      makeNode('b', 'py'),
      makeNode('c', 'ts'),
    ]
    const engine = new IslandLayoutEngine(nodes, [])
    const islands = engine.partition()
    expect(islands.size).toBe(2)
    expect(islands.get('py')?.nodes).toHaveLength(2)
    expect(islands.get('ts')?.nodes).toHaveLength(1)
  })

  it('sets aside cross-namespace edges', () => {
    const nodes = [makeNode('a', 'py'), makeNode('b', 'ts')]
    const edges = [makeEdge('a', 'b'), makeEdge('a', 'a')]
    const engine = new IslandLayoutEngine(nodes, edges)
    engine.partition()
    expect(engine.crossEdges).toHaveLength(1)
    expect(engine.crossEdges[0].source).toBe('a')
  })

  it('nodes with no namespace go into _default island', () => {
    const nodes = [{ id: 'x', position: { x: 0, y: 0 }, data: { label: 'x' } } as Node]
    const engine = new IslandLayoutEngine(nodes, [])
    const islands = engine.partition()
    expect(islands.has('_default')).toBe(true)
  })
})

describe('IslandLayoutEngine.placeIslands — bin-packing', () => {
  it('single island placed at origin', () => {
    const offsets = IslandLayoutEngine.computeOffsets(
      [{ namespace: 'py', width: 500, height: 300 }],
      3200
    )
    expect(offsets.get('py')).toEqual({ x: 0, y: 0 })
  })

  it('two islands that fit in one row placed side by side', () => {
    const offsets = IslandLayoutEngine.computeOffsets(
      [
        { namespace: 'py', width: 500, height: 300 },
        { namespace: 'ts', width: 400, height: 300 },
      ],
      3200
    )
    expect(offsets.get('py')?.x).toBe(0)
    expect(offsets.get('ts')?.x).toBeGreaterThan(0)
    expect(offsets.get('ts')?.y).toBe(0)  // same row
  })

  it('island wider than CANVAS_MAX_WIDTH wraps to new row', () => {
    const offsets = IslandLayoutEngine.computeOffsets(
      [
        { namespace: 'large', width: 3000, height: 400 },
        { namespace: 'small', width: 200, height: 200 },
      ],
      3200
    )
    expect(offsets.get('small')?.y).toBeGreaterThan(0)  // pushed to next row
  })
})
```

- [ ] **Step 2: Run — expect FAIL**

```bash
cd ui && bunx vitest run src/layout/__tests__/IslandLayoutEngine.test.ts
```

Expected: `Cannot find module '../IslandLayoutEngine'`

- [ ] **Step 3: Create IslandLayoutEngine.ts**

```typescript
// ui/src/layout/IslandLayoutEngine.ts
import dagre from 'dagre'
import type { Node, Edge } from '@xyflow/react'
import {
  NODE_WIDTH,
  NODE_HEIGHT,
  NODE_SEP,
  RANK_SEP,
  CANVAS_MAX_WIDTH,
  ISLAND_GAP,
  FILE_CONTAINER_PADDING,
  FILE_HEADER_HEIGHT,
  FILE_HEADER_GAP,
} from '../constants'

interface IslandData {
  nodes: Node[]
  edges: Edge[]
}

interface IslandBBox {
  namespace: string
  width: number
  height: number
}

export class IslandLayoutEngine {
  private nodes: Node[]
  private edges: Edge[]
  crossEdges: Edge[] = []
  private islands: Map<string, IslandData> = new Map()

  constructor(nodes: Node[], edges: Edge[]) {
    this.nodes = nodes
    this.edges = edges
  }

  // Phase 1 — group nodes by namespace
  partition(): Map<string, IslandData> {
    const islands = new Map<string, IslandData>()
    const nodeIndex = new Map(this.nodes.map((n) => [n.id, n]))

    for (const node of this.nodes) {
      const ns = ((node.data as Record<string, unknown>)?.namespace as string) || '_default'
      if (!islands.has(ns)) islands.set(ns, { nodes: [], edges: [] })
      islands.get(ns)!.nodes.push(node)
    }

    // Split edges: intra-island vs cross-namespace
    this.crossEdges = []
    for (const edge of this.edges) {
      const srcNs =
        ((nodeIndex.get(edge.source)?.data as Record<string, unknown>)?.namespace as string) ||
        '_default'
      const tgtNs =
        ((nodeIndex.get(edge.target)?.data as Record<string, unknown>)?.namespace as string) ||
        '_default'
      if (srcNs === tgtNs) {
        islands.get(srcNs)?.edges.push(edge)
      } else {
        this.crossEdges.push(edge)
      }
    }

    this.islands = islands
    return islands
  }

  // Phase 2 — run dagre independently for each island, return relative positions + bbox
  private layoutIsland(island: IslandData): { nodes: Node[]; bbox: { width: number; height: number } } {
    const g = new dagre.graphlib.Graph()
    g.setDefaultEdgeLabel(() => ({}))
    g.setGraph({ rankdir: 'TB', nodesep: NODE_SEP, ranksep: RANK_SEP })

    for (const node of island.nodes) {
      g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
    }
    for (const edge of island.edges) {
      g.setEdge(edge.source, edge.target)
    }

    dagre.layout(g)

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
    const positioned = island.nodes.map((node) => {
      const { x, y } = g.node(node.id)
      minX = Math.min(minX, x)
      minY = Math.min(minY, y)
      maxX = Math.max(maxX, x + NODE_WIDTH)
      maxY = Math.max(maxY, y + NODE_HEIGHT)
      return { ...node, position: { x, y } }
    })

    // Normalize to [0,0] origin
    const normalised = positioned.map((n) => ({
      ...n,
      position: { x: n.position.x - minX, y: n.position.y - minY },
    }))

    const containerPad = FILE_CONTAINER_PADDING * 2 + FILE_HEADER_HEIGHT + FILE_HEADER_GAP
    const bbox = {
      width: maxX - minX + containerPad,
      height: maxY - minY + containerPad,
    }

    return { nodes: normalised, bbox }
  }

  // Phase 3 — compute island offsets via greedy row bin-packing (O(n log n))
  static computeOffsets(
    bboxes: IslandBBox[],
    canvasMaxWidth: number = CANVAS_MAX_WIDTH
  ): Map<string, { x: number; y: number }> {
    // Sort largest first
    const sorted = [...bboxes].sort((a, b) => b.width * b.height - a.width * a.height)

    const offsets = new Map<string, { x: number; y: number }>()
    let rowX = 0
    let rowY = 0
    let rowMaxHeight = 0

    for (const island of sorted) {
      if (rowX > 0 && rowX + island.width > canvasMaxWidth) {
        // Wrap to next row
        rowY += rowMaxHeight + ISLAND_GAP
        rowX = 0
        rowMaxHeight = 0
      }
      offsets.set(island.namespace, { x: rowX, y: rowY })
      rowX += island.width + ISLAND_GAP
      rowMaxHeight = Math.max(rowMaxHeight, island.height)
    }

    return offsets
  }

  // Full pipeline: partition → layout each island → place islands → restore cross-edges
  async run(expandedFiles: Set<string>): Promise<{ nodes: Node[]; edges: Edge[] }> {
    const islands = this.partition()
    const bboxes: IslandBBox[] = []
    const islandResults = new Map<string, { nodes: Node[]; bbox: { width: number; height: number } }>()

    for (const [ns, island] of islands) {
      const result = this.layoutIsland(island)
      islandResults.set(ns, result)
      bboxes.push({ namespace: ns, width: result.bbox.width, height: result.bbox.height })
    }

    const offsets = IslandLayoutEngine.computeOffsets(bboxes)

    // Translate all nodes by their island offset
    const allNodes: Node[] = []
    for (const [ns, result] of islandResults) {
      const offset = offsets.get(ns) ?? { x: 0, y: 0 }
      for (const node of result.nodes) {
        allNodes.push({
          ...node,
          position: {
            x: node.position.x + offset.x,
            y: node.position.y + offset.y,
          },
          // Mark island membership for IslandContainerNode
          data: { ...node.data as object, islandNamespace: ns },
        })
      }

      // Add IslandContainerNode for each namespace
      const containerPad = FILE_CONTAINER_PADDING
      allNodes.push({
        id: `island-container-${ns}`,
        type: 'islandContainer',
        position: { x: offset.x - containerPad, y: offset.y - containerPad },
        style: {
          width: bboxes.find((b) => b.namespace === ns)!.width + containerPad * 2,
          height: bboxes.find((b) => b.namespace === ns)!.height + containerPad * 2,
          pointerEvents: 'none' as const,
        },
        data: { namespace: ns },
        selectable: false,
        draggable: false,
      })
    }

    const allEdges = [...this.crossEdges]
    for (const [, island] of islands) {
      allEdges.push(...island.edges)
    }

    return { nodes: allNodes, edges: allEdges }
  }
}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd ui && bunx vitest run src/layout/__tests__/IslandLayoutEngine.test.ts
```

Expected: `✓ 6 tests passed`

- [ ] **Step 5: Commit**

```bash
git add ui/src/layout/IslandLayoutEngine.ts ui/src/layout/__tests__/IslandLayoutEngine.test.ts
git commit -m "feat(ui): add IslandLayoutEngine — 3-phase bin-packing island layout"
```

---

## Task 3: Create IslandContainerNode component

**Files:**
- Create: `ui/src/components/IslandContainerNode.tsx`

- [ ] **Step 1: Create component**

```tsx
// ui/src/components/IslandContainerNode.tsx
import type { NodeProps } from '@xyflow/react'
import { NAMESPACE_COLORS } from '../theme'

const NAMESPACE_LABEL: Record<string, string> = {
  'owner-api': '🐍 owner-api',
  'owner-web': '⚛️ owner-web',
  'ownima-admin': '⚛️ admin',
  'rider-web': '🌐 rider-web',
  _default: '📦 default',
}

export default function IslandContainerNode({ data, style }: NodeProps) {
  const ns = (data as { namespace: string }).namespace
  const color = (NAMESPACE_COLORS as Record<string, string>)[ns] ?? '#546e7a'
  const label = NAMESPACE_LABEL[ns] ?? ns

  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        border: `2px solid ${color}`,
        borderRadius: 10,
        background: `${color}11`,
        pointerEvents: 'none',
        ...style,
      }}
    >
      <div
        style={{
          position: 'absolute',
          top: -1,
          left: 10,
          background: color,
          color: '#fff',
          fontSize: 10,
          fontWeight: 'bold',
          padding: '2px 8px',
          borderRadius: '0 0 4px 4px',
        }}
      >
        {label}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Add NAMESPACE_COLORS to theme.ts**

Append to `ui/src/theme.ts`:

```typescript
export const NAMESPACE_COLORS: Record<string, string> = {
  'owner-api': '#2d6a2d',
  'owner-web': '#2d2d6a',
  'ownima-admin': '#5a2d6a',
  'rider-web': '#1a5a5a',
  _default: '#546e7a',
}
```

- [ ] **Step 3: Commit**

```bash
git add ui/src/components/IslandContainerNode.tsx ui/src/theme.ts
git commit -m "feat(ui): add IslandContainerNode and NAMESPACE_COLORS"
```

---

## Task 4: Wire IslandLayoutEngine into useLayoutComputation

**Files:**
- Modify: `ui/src/hooks/useLayoutComputation.ts`
- Modify: `ui/src/components/GraphShell.tsx`

- [ ] **Step 1: Replace layoutGraph call with IslandLayoutEngine.run()**

In `ui/src/hooks/useLayoutComputation.ts`, replace the `layoutGraph()` call:

```typescript
// Replace the bottom of the async function — replace:
//   const { nodes: layoutedNodes, edges: layoutedEdges } = await layoutGraph(...)
// With:
import { IslandLayoutEngine } from '../layout/IslandLayoutEngine'

// ...inside the async function:
const engine = new IslandLayoutEngine(labeledNodes as Node[], visibleEdges as Edge[])
const { nodes: layoutedNodes, edges: layoutedEdges } = await engine.run(expandedFiles)
```

Also remove the `layoutGraph` import from the top of useLayoutComputation.ts.

- [ ] **Step 2: Add islandContainer to GraphShell nodeTypes**

In `ui/src/components/GraphShell.tsx`, modify the `nodeTypes` memo:

```typescript
import IslandContainerNode from './IslandContainerNode'

// Replace:
const nodeTypes = useMemo(() => ({ fileContainer: FileContainerNode }), [])
// With:
const nodeTypes = useMemo(
  () => ({ fileContainer: FileContainerNode, islandContainer: IslandContainerNode }),
  []
)
```

- [ ] **Step 3: Full build**

```bash
cd ui && bun run build 2>&1 | tail -5
```

Expected: build succeeds.

- [ ] **Step 4: Run dev server and verify islands render**

```bash
cd ui && bun run dev
```

Expected: namespace cluster borders visible around node groups, cross-namespace edges connect between islands, no console errors.

- [ ] **Step 5: Run all tests**

```bash
cd ui && bun run test:run
```

Expected: all tests pass.

- [ ] **Step 6: Delete layout.ts**

```bash
git rm ui/src/layout.ts
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(ui): wire IslandLayoutEngine, remove layout.ts"
```

---

## Task 5: Push + PR

- [ ] **Step 1: Create branch and push**

```bash
git checkout -b feat/ui-pr2-layout-engine
git push -u origin feat/ui-pr2-layout-engine
```

- [ ] **Step 2: Open PR**

```bash
gh pr create \
  --title "feat(ui): IslandLayoutEngine — per-namespace cluster layout (PR 2/4)" \
  --body "$(cat <<'EOF'
## Summary
- Replaces layout.ts with IslandLayoutEngine: partition → layoutIsland → placeIslands
- Greedy row bin-packing replaces O(n²) overlap resolution
- Handles both axes (horizontal + vertical)
- IslandContainerNode renders per-namespace cluster border
- Cross-namespace edges visible across islands

## Test plan
- [ ] `bun run test:run` passes
- [ ] Dev server: each namespace renders in its own bordered island
- [ ] Cross-namespace edges visible
- [ ] Drag test: islands stay visually separated

Prerequisite: PR 1
See spec: `docs/superpowers/specs/2026-06-09-ui-redesign-design.md`
EOF
)"
```
