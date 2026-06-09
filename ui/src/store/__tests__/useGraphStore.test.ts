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
    colorMode: 'type',
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

  it('setGraphData extracts namespaces from graphNodes', () => {
    const graphNodes = [
      { id: 'a', type: 'FILE', name: 'a', file_path: 'a.py', start_line: 1, end_line: 1, language: 'python', namespace: 'owner-api', ontology_class: null, domains: [], confidence_score: 1, metadata: {} },
      { id: 'b', type: 'FILE', name: 'b', file_path: 'b.py', start_line: 1, end_line: 1, language: 'python', namespace: 'owner-web', ontology_class: null, domains: [], confidence_score: 1, metadata: {} },
      { id: 'c', type: 'FILE', name: 'c', file_path: 'c.py', start_line: 1, end_line: 1, language: 'python', namespace: 'owner-api', ontology_class: null, domains: [], confidence_score: 1, metadata: {} },
    ] as any[] // eslint-disable-line @typescript-eslint/no-explicit-any
    useGraphStore.getState().setGraphData([], [], graphNodes, [])
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

  it('setColorMode updates colorMode', () => {
    useGraphStore.getState().setColorMode('health')
    expect(useGraphStore.getState().colorMode).toBe('health')
    useGraphStore.getState().setColorMode('type')
    expect(useGraphStore.getState().colorMode).toBe('type')
  })
})
