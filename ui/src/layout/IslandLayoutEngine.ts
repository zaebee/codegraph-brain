import dagre from 'dagre'
import type { Node, Edge } from '@xyflow/react'
import {
  NODE_WIDTH,
  NODE_HEIGHT,
  NODE_SEP,
  RANK_SEP,
  LAYOUT_DIRECTION,
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

  constructor(nodes: Node[], edges: Edge[]) {
    this.nodes = nodes
    this.edges = edges
  }

  private getNamespace(node: Node): string {
    return ((node.data as Record<string, unknown>)?.namespace as string) || '_default'
  }

  partition(): Map<string, IslandData> {
    const islands = new Map<string, IslandData>()
    const nodeIndex = new Map(this.nodes.map((n) => [n.id, n]))

    for (const node of this.nodes) {
      const ns = this.getNamespace(node)
      if (!islands.has(ns)) islands.set(ns, { nodes: [], edges: [] })
      islands.get(ns)!.nodes.push(node)
    }

    this.crossEdges = []
    for (const edge of this.edges) {
      const srcNode = nodeIndex.get(edge.source)
      const tgtNode = nodeIndex.get(edge.target)
      if (!srcNode || !tgtNode) continue

      const srcNs = this.getNamespace(srcNode)
      const tgtNs = this.getNamespace(tgtNode)
      if (srcNs === tgtNs) {
        islands.get(srcNs)?.edges.push(edge)
      } else {
        this.crossEdges.push(edge)
      }
    }

    return islands
  }

  private layoutIsland(island: IslandData): { nodes: Node[]; bbox: { width: number; height: number } } {
    const g = new dagre.graphlib.Graph()
    g.setDefaultEdgeLabel(() => ({}))
    g.setGraph({ rankdir: LAYOUT_DIRECTION, nodesep: NODE_SEP, ranksep: RANK_SEP })

    for (const node of island.nodes) {
      g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
    }
    for (const edge of island.edges) {
      g.setEdge(edge.source, edge.target)
    }

    dagre.layout(g)

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
    const positioned = island.nodes.map((node) => {
      const pos = g.node(node.id)
      const x = pos ? pos.x - NODE_WIDTH / 2 : (node.position?.x ?? 0)
      const y = pos ? pos.y - NODE_HEIGHT / 2 : (node.position?.y ?? 0)
      minX = Math.min(minX, x)
      minY = Math.min(minY, y)
      maxX = Math.max(maxX, x + NODE_WIDTH)
      maxY = Math.max(maxY, y + NODE_HEIGHT)
      return { ...node, position: { x, y } }
    })

    // Offset nodes so they sit inside the container border + below the header
    const normalised = positioned.map((n) => ({
      ...n,
      position: {
        x: n.position.x - minX + FILE_CONTAINER_PADDING,
        y: n.position.y - minY + FILE_CONTAINER_PADDING + FILE_HEADER_HEIGHT + FILE_HEADER_GAP,
      },
    }))

    const bbox = {
      width: maxX - minX + FILE_CONTAINER_PADDING * 2,
      height: maxY - minY + FILE_CONTAINER_PADDING * 2 + FILE_HEADER_HEIGHT + FILE_HEADER_GAP,
    }

    return { nodes: normalised, bbox }
  }

  static computeOffsets(
    bboxes: IslandBBox[],
    canvasMaxWidth: number = CANVAS_MAX_WIDTH
  ): Map<string, { x: number; y: number }> {
    const sorted = [...bboxes].sort((a, b) => a.namespace.localeCompare(b.namespace))

    const offsets = new Map<string, { x: number; y: number }>()
    let rowX = 0
    let rowY = 0
    let rowMaxHeight = 0

    for (const island of sorted) {
      if (rowX > 0 && rowX + island.width > canvasMaxWidth) {
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

  // Transforms expanded FILE nodes into fileContainer background wrappers sized to their children.
  // Uses the background-node pattern (same as islandContainer): the container is a non-interactive
  // node z-ordered before children; all positions remain absolute. We intentionally skip React Flow's
  // parentNode/extent:'parent' approach because our containers are non-draggable visual-only elements
  // and absolute coordinates are simpler and sufficient here.
  private applyFileContainers(nodes: Node[], expandedFiles: Set<string>): Node[] {
    // Build fileId → child node indices
    const fileChildIndices = new Map<string, number[]>()
    for (let i = 0; i < nodes.length; i++) {
      const groupId = (nodes[i].data as Record<string, unknown>)?.groupId as string | undefined
      if (groupId) {
        const list = fileChildIndices.get(groupId) ?? []
        list.push(i)
        fileChildIndices.set(groupId, list)
      }
    }

    const result = [...nodes]

    for (let i = 0; i < result.length; i++) {
      const node = result[i]
      if ((node.data as Record<string, unknown>)?.nodeType !== 'FILE') continue
      if (!expandedFiles.has(node.id)) continue

      const childIndices = fileChildIndices.get(node.id) ?? []
      if (childIndices.length === 0) continue

      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
      for (const ci of childIndices) {
        const p = nodes[ci].position
        minX = Math.min(minX, p.x)
        maxX = Math.max(maxX, p.x + NODE_WIDTH)
        minY = Math.min(minY, p.y)
        maxY = Math.max(maxY, p.y + NODE_HEIGHT)
      }

      result[i] = {
        ...node,
        type: 'fileContainer',
        position: {
          x: minX - FILE_CONTAINER_PADDING,
          y: minY - FILE_CONTAINER_PADDING - FILE_HEADER_HEIGHT - FILE_HEADER_GAP,
        },
        style: {
          ...node.style,
          width: maxX - minX + FILE_CONTAINER_PADDING * 2,
          height: maxY - minY + FILE_CONTAINER_PADDING * 2 + FILE_HEADER_HEIGHT + FILE_HEADER_GAP,
        },
        data: { ...node.data, isExpanded: true },
        draggable: false,
      }
    }

    // Containers (island + file) must precede content nodes for correct z-order
    const containers = result.filter((n) => n.type === 'islandContainer' || n.type === 'fileContainer')
    const content = result.filter((n) => n.type !== 'islandContainer' && n.type !== 'fileContainer')
    return [...containers, ...content]
  }

  // TODO(PR4): use expandedFiles to highlight the ego-subgraph overlay (issue #30)
  run(expandedFiles: Set<string> = new Set()): { nodes: Node[]; edges: Edge[] } {
    const islands = this.partition()
    const bboxes: IslandBBox[] = []
    const islandResults = new Map<string, { nodes: Node[]; bbox: { width: number; height: number } }>()

    for (const [ns, island] of islands) {
      const result = this.layoutIsland(island)
      islandResults.set(ns, result)
      bboxes.push({ namespace: ns, width: result.bbox.width, height: result.bbox.height })
    }

    const offsets = IslandLayoutEngine.computeOffsets(bboxes)

    const allNodes: Node[] = []
    for (const [ns, result] of islandResults) {
      const offset = offsets.get(ns) ?? { x: 0, y: 0 }
      const bbox = result.bbox

      allNodes.push({
        id: `island-container-${ns}`,
        type: 'islandContainer',
        position: { x: offset.x, y: offset.y },
        style: {
          width: bbox.width,
          height: bbox.height,
          pointerEvents: 'none' as const,
        },
        data: { namespace: ns },
        selectable: false,
        draggable: false,
      })

      for (const node of result.nodes) {
        allNodes.push({
          ...node,
          position: {
            x: node.position.x + offset.x,
            y: node.position.y + offset.y,
          },
          data: { ...node.data, islandNamespace: ns },
        })
      }
    }

    const allEdges = [...this.crossEdges]
    for (const [, island] of islands) {
      allEdges.push(...island.edges)
    }

    if (expandedFiles.size === 0) {
      return { nodes: allNodes, edges: allEdges }
    }

    // Filter CONTAINS edges from expanded files — they're implicit via the container
    const filteredEdges = allEdges.filter((e) => {
      if ((e.data as Record<string, unknown>)?.edgeType !== 'CONTAINS') return true
      return !expandedFiles.has(e.source)
    })

    return { nodes: this.applyFileContainers(allNodes, expandedFiles), edges: filteredEdges }
  }
}
