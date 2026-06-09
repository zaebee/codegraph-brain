import { create } from 'zustand'
import type { Node, Edge } from '@xyflow/react'
import type { GraphNode, GraphEdge } from '../types'

type EdgeTypeName = 'CALLS' | 'IMPORTS' | 'EXTENDS' | 'CONTAINS' | 'DECLARES'
type ViewMode = 'full' | 'flow' | 'ego'

interface FlowCacheEntry { nodes: Node[]; edges: Edge[] }

const ALL_EDGE_TYPE_NAMES: EdgeTypeName[] = ['CALLS', 'IMPORTS', 'EXTENDS', 'CONTAINS', 'DECLARES']

interface GraphStore {
  // graph slice
  rawNodes: Node[]
  rawEdges: Edge[]
  graphNodes: GraphNode[]
  graphEdges: GraphEdge[]
  namespaces: string[]
  graphVersion: number
  setGraphData: (nodes: Node[], edges: Edge[], graphNodes?: GraphNode[], graphEdges?: GraphEdge[]) => void

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
  colorMode: 'type' | 'health'
  setViewMode: (mode: ViewMode) => void
  setHoveredNodeId: (id: string | null) => void
  setEgoNodeId: (id: string | null) => void
  setColorMode: (mode: 'type' | 'health') => void
}

export const useGraphStore = create<GraphStore>((set) => ({
  // graph slice
  rawNodes: [],
  rawEdges: [],
  graphNodes: [],
  graphEdges: [],
  namespaces: [],
  graphVersion: 0,
  setGraphData: (nodes, edges, graphNodes = [], graphEdges = []) => {
    const namespaces = [
      ...new Set(
        graphNodes
          .map((n) => n.namespace)
          .filter((ns): ns is string => typeof ns === 'string' && ns.length > 0)
      ),
    ]
    set((state) => ({
      rawNodes: nodes,
      rawEdges: edges,
      graphNodes,
      graphEdges,
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
  colorMode: 'type',
  setViewMode: (mode) => set({ viewMode: mode }),
  setHoveredNodeId: (id) => set({ hoveredNodeId: id }),
  setEgoNodeId: (id) => set({ egoNodeId: id }),
  setColorMode: (mode) => set({ colorMode: mode }),
}))
