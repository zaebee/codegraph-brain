import { describe, it, expect, beforeEach } from 'vitest'
import { useGraphStore } from '../useGraphStore'

beforeEach(() => {
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
