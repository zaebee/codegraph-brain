// ui/src/hooks/useLayoutComputation.ts
/* eslint-disable @typescript-eslint/no-explicit-any */
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
