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
    expect(offsets.get('ts')?.y).toBe(0)
  })

  it('island wider than CANVAS_MAX_WIDTH wraps to new row', () => {
    const offsets = IslandLayoutEngine.computeOffsets(
      [
        { namespace: 'large', width: 3000, height: 400 },
        { namespace: 'small', width: 200, height: 200 },
      ],
      3200
    )
    expect(offsets.get('small')?.y).toBeGreaterThan(0)
  })
})
