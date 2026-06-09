// ui/src/hooks/useFlowNavigation.ts
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useCallback, useRef, type MouseEvent } from 'react'
import { useReactFlow, type Node } from '@xyflow/react'
import { buildExecutionFlow } from '../flow'
import { IslandLayoutEngine } from '../layout/IslandLayoutEngine'
import { mapNodeToFlowView } from '../utils/nodeMapper'
import { mapEdgeToFlowView } from '../utils/edgeMapper'
import { useGraphStore } from '../store/useGraphStore'

const FLOW_DEPTH = 3
const DEFAULT_ALLOWED = ['CALLS', 'IMPORTS', 'CONTAINS']

export function useFlowNavigation(allowedEdgeTypes: string[] = DEFAULT_ALLOWED) {
  const rawNodes = useGraphStore((s) => s.graphNodes)
  const rawEdges = useGraphStore((s) => s.graphEdges)
  const graphVersion = useGraphStore((s) => s.graphVersion)
  const setFlow = useGraphStore((s) => s.setFlow)
  const setViewMode = useGraphStore((s) => s.setViewMode)
  const { fitView } = useReactFlow()

  // Local ref cache — keyed by graphVersion so stale data is never served
  const cacheRef = useRef<Map<string, { nodes: any[]; edges: any[] }>>(new Map())

  const onNodeClick = useCallback(
    async (_event: MouseEvent, node: Node) => {
      if (node.type === 'group' || node.type === 'fileContainer') return

      const cacheKey = `${node.id}:${FLOW_DEPTH}:${allowedEdgeTypes.join(',')}:${graphVersion}`

      let flow: { nodes: any[]; edges: any[] }
      if (cacheRef.current.has(cacheKey)) {
        flow = cacheRef.current.get(cacheKey)!
      } else {
        const graphData = { nodes: rawNodes, edges: rawEdges }
        flow = buildExecutionFlow(graphData, node.id, FLOW_DEPTH, 'both', allowedEdgeTypes)
        cacheRef.current.set(cacheKey, flow)
      }

      const flowNodes = flow.nodes.map((n) =>
        mapNodeToFlowView(n, { isRoot: n.id === node.id })
      )
      const flowEdges = flow.edges.map((e, i) => mapEdgeToFlowView(e, i))
      const engine = new IslandLayoutEngine(flowNodes, flowEdges)
      const { nodes: layoutedNodes } = engine.run(new Set<string>())

      setFlow(layoutedNodes, flowEdges)
      setViewMode('flow')
      fitView({ padding: 0.15, duration: 250 })
    },
    [rawNodes, rawEdges, graphVersion, allowedEdgeTypes, setFlow, setViewMode, fitView]
  )

  return { onNodeClick }
}
