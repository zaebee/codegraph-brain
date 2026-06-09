import { useCallback, useMemo, useRef, useState, type MouseEvent } from 'react'
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
import { ALL_EDGE_TYPES } from '../constants'

import ControlPanel from './ControlPanel'
import StatsPanel from './StatsPanel'
import NodeTooltip from './NodeTooltip'
import LegendPanel from './LegendPanel'
import LoadingOverlay from './LoadingOverlay'
import FileContainerNode from './FileContainerNode'
import IslandContainerNode from './IslandContainerNode'

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

  // Layout computation hook — subscribes to filter state, runs dagre
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

  // Fix 2: Extract handleFit with useCallback
  const handleFit = useCallback(
    () => fitView({ padding: 0.15, duration: 250 }),
    [fitView]
  )

  // Fix 3: Memoize handleBack with useCallback
  const handleBack = useCallback(() => setViewMode('full'), [setViewMode])

  // Keyboard shortcuts
  useKeyboardShortcuts({
    onEscape: useCallback(() => {
      if (viewMode === 'flow') setViewMode('full')
    }, [viewMode, setViewMode]),
    onFit: handleFit,
    onFocusSearch: useCallback(() => searchInputRef.current?.focus(), []),
  })

  // Highlight connected nodes/edges on hover
  const activeEdges = viewMode === 'flow' ? flowEdges : layoutedEdges
  const { nodes: highlightedNodes, edges: highlightedEdges } = useMemo(
    () => applyContextHighlight(displayedNodes, activeEdges, hoveredNodeId),
    [displayedNodes, activeEdges, hoveredNodeId]
  )

  const nodeTypes = useMemo(
    () => ({ fileContainer: FileContainerNode, islandContainer: IslandContainerNode }),
    []
  )

  // Fix 1: Memoize ALL_EDGE_TYPES spread
  const allEdgeTypesArray = useMemo(() => [...ALL_EDGE_TYPES], [])

  // Fix 4: Memoize handleMouseMove with useCallback
  const handleMouseMove = useCallback(
    (e: MouseEvent) => setMousePos({ x: e.clientX, y: e.clientY }),
    []
  )

  const handleToggleEdgeType = useCallback(
    (type: string) => toggleEdgeType(type as Parameters<typeof toggleEdgeType>[0]),
    [toggleEdgeType]
  )

  const onNodeClick = useCallback(
    async (event: MouseEvent, node: Parameters<typeof onFlowClick>[1]) => {
      if (viewMode === 'full' && (node.data as Record<string, unknown>)?.nodeType === 'FILE') {
        toggleExpandedFile(node.id)
      } else {
        await onFlowClick(event, node)
      }
    },
    [viewMode, toggleExpandedFile, onFlowClick]
  )

  const graphLoading = graphVersion === 0

  // Adapt store Set to ControlPanel's string[] expectation
  const activeEdgeTypesArray = useMemo(() => [...activeEdgeTypes], [activeEdgeTypes])

  // Compute stats for StatsPanel
  const stats = useMemo(() => {
    const externalCount = highlightedNodes.filter(
      (n) => (n.data as Record<string, unknown>)?.isExternal === true
    ).length
    return {
      nodes: highlightedNodes.length,
      edges: highlightedEdges.length,
      external: externalCount,
    }
  }, [highlightedNodes, highlightedEdges])

  // Find the hovered node for NodeTooltip
  const hoveredNode = useMemo(
    () => highlightedNodes.find((n) => n.id === hoveredNodeId) ?? null,
    [highlightedNodes, hoveredNodeId]
  )

  return (
    <div style={{ width: '100vw', height: '100vh' }}>
      <LoadingOverlay visible={graphLoading} />
      <ReactFlow
        nodes={highlightedNodes}
        edges={highlightedEdges}
        onNodeClick={onNodeClick}
        onNodeMouseEnter={(_, node) => setHoveredNodeId(node.id)}
        onNodeMouseLeave={() => setHoveredNodeId(null)}
        onMouseMove={handleMouseMove}
        nodeTypes={nodeTypes}
        colorMode="dark"
        fitView
      >
        <Background />
        <Controls />
        <MiniMap />
        <Panel position="top-left">
          <ControlPanel
            searchQuery={searchQuery}
            setSearchQuery={setSearchQuery}
            searchInputRef={searchInputRef}
            onExportPng={exportPng}
            onExportSvg={exportSvg}
            onFit={handleFit}
            onBack={handleBack}
            viewMode={viewMode}
            activeEdgeTypes={activeEdgeTypesArray}
            onToggleEdgeType={handleToggleEdgeType}
            ALL_EDGE_TYPES={allEdgeTypesArray}
            showExternal={showExternal}
            onToggleExternal={(_show: boolean) => toggleExternal()}
            flowRootId={null}
          />
        </Panel>
        {/* LegendPanel renders its own <Panel position="bottom-right"> internally */}
        <LegendPanel visible={true} />
        {/* StatsPanel renders its own <Panel position="top-right"> internally */}
        <StatsPanel stats={stats} visible={!graphLoading} />
      </ReactFlow>
      {hoveredNodeId && (
        <NodeTooltip
          node={hoveredNode}
          mousePos={mousePos}
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
