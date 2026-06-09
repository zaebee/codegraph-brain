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

  constructor(nodes: Node[], edges: Edge[]) {
    this.nodes = nodes
    this.edges = edges
  }

  partition(): Map<string, IslandData> {
    const islands = new Map<string, IslandData>()
    const nodeIndex = new Map(this.nodes.map((n) => [n.id, n]))

    for (const node of this.nodes) {
      const ns = ((node.data as Record<string, unknown>)?.namespace as string) || '_default'
      if (!islands.has(ns)) islands.set(ns, { nodes: [], edges: [] })
      islands.get(ns)!.nodes.push(node)
    }

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

    return islands
  }

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

  static computeOffsets(
    bboxes: IslandBBox[],
    canvasMaxWidth: number = CANVAS_MAX_WIDTH
  ): Map<string, { x: number; y: number }> {
    const sorted = [...bboxes].sort((a, b) => b.width * b.height - a.width * a.height)

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

  async run(_expandedFiles: Set<string>): Promise<{ nodes: Node[]; edges: Edge[] }> {
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
      for (const node of result.nodes) {
        allNodes.push({
          ...node,
          position: {
            x: node.position.x + offset.x,
            y: node.position.y + offset.y,
          },
          data: { ...node.data as object, islandNamespace: ns },
        })
      }

      const containerPad = FILE_CONTAINER_PADDING
      const bbox = bboxes.find((b) => b.namespace === ns) ?? { width: 0, height: 0 }
      allNodes.push({
        id: `island-container-${ns}`,
        type: 'islandContainer',
        position: { x: offset.x - containerPad, y: offset.y - containerPad },
        style: {
          width: bbox.width + containerPad * 2,
          height: bbox.height + containerPad * 2,
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
