import { describe, it, expect } from 'vitest'
import { MarkerType } from '@xyflow/react'
import { mapEdgeToReactFlow, mapEdgeToFlowView } from '../edgeMapper'
import type { GraphEdge } from '../../types'

const edge: GraphEdge = {
  id: 'test-edge-1',
  source: 'src.pipeline.IngestionPipeline.run',
  target: 'src.resolver.engine.ResolverEngine.resolve',
  type: 'CALLS',
  weight: 1,
  confidence: 1,
  context: '',
}

describe('mapEdgeToReactFlow', () => {
  it('generates stable content-hash ID', () => {
    const e1 = mapEdgeToReactFlow(edge, 0)
    const e2 = mapEdgeToReactFlow(edge, 99)  // index doesn't matter
    expect(e1.id).toBe(e2.id)
    expect(e1.id).toBe(
      'src.pipeline.IngestionPipeline.run→src.resolver.engine.ResolverEngine.resolve:CALLS'
    )
  })

  it('includes markerEnd with ArrowClosed', () => {
    const e = mapEdgeToReactFlow(edge, 0)
    expect(e.markerEnd).toBeDefined()
    expect((e.markerEnd as { type: string }).type).toBe(MarkerType.ArrowClosed)
  })
})

describe('mapEdgeToFlowView', () => {
  it('generates same stable ID as mapEdgeToReactFlow', () => {
    const full = mapEdgeToReactFlow(edge, 0)
    const flow = mapEdgeToFlowView(edge, 0)
    expect(flow.id).toBe(full.id)
  })

  it('includes markerEnd with ArrowClosed', () => {
    const e = mapEdgeToFlowView(edge, 0)
    expect(e.markerEnd).toBeDefined()
    expect((e.markerEnd as { type: string }).type).toBe(MarkerType.ArrowClosed)
  })

  it('is animated', () => {
    const e = mapEdgeToFlowView(edge, 0)
    expect(e.animated).toBe(true)
  })
})
