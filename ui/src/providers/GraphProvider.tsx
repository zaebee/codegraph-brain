import { useEffect, type ReactNode } from 'react'
import { useGraphStore } from '../store/useGraphStore'
import { mapNodeToReactFlow } from '../utils/nodeMapper'
import { mapEdgeToReactFlow } from '../utils/edgeMapper'
import type { GraphData } from '../types'

const GRAPH_URL =
  (import.meta.env.VITE_GRAPH_DATA_URL as string | undefined) ?? '/graph.json'

export function GraphProvider({ children }: { children: ReactNode }) {
  const setGraphData = useGraphStore((s) => s.setGraphData)

  useEffect(() => {
    fetch(GRAPH_URL)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status} loading graph`)
        return r.json() as Promise<GraphData>
      })
      .then((data) =>
        setGraphData(
          data.nodes.map((n) => mapNodeToReactFlow(n)),
          data.edges.map((e, i) => mapEdgeToReactFlow(e, i))
        )
      )
      .catch((err) => console.error('GraphProvider: failed to load graph', err))
  }, [setGraphData])

  return <>{children}</>
}
