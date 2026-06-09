import { describe, it, expect } from 'vitest'
import { applyHeatmapColors } from './applyHeatmap'

function makeNode(id: string, metadata: Record<string, unknown> = {}) {
  return {
    id,
    type: 'default',
    position: { x: 0, y: 0 },
    data: { label: id, metadata },
    style: { background: '#000', border: '1px solid #fff' },
  }
}

describe('applyHeatmapColors', () => {
  it('returns same array reference when disabled', () => {
    const nodes = [makeNode('a')]
    const result = applyHeatmapColors(nodes as any, false)
    expect(result).toBe(nodes)
  })

  it('overrides background/border for healthy node (fan_out=1)', () => {
    const nodes = [makeNode('a', { fan_out: 1, in_cycle: false })]
    const result = applyHeatmapColors(nodes as any, true)
    expect(result[0].style?.background).toBe('#1b3a1b')
    expect(result[0].style?.borderColor).toBe('#66bb6a')
  })

  it('uses critical color for in_cycle=true regardless of fan_out', () => {
    const nodes = [makeNode('a', { fan_out: 0, in_cycle: true })]
    const result = applyHeatmapColors(nodes as any, true)
    expect(result[0].style?.background).toBe('#3b0000')
  })

  it('uses warning color for fan_out=7', () => {
    const nodes = [makeNode('a', { fan_out: 7, in_cycle: false })]
    const result = applyHeatmapColors(nodes as any, true)
    expect(result[0].style?.background).toBe('#3b2a00')
  })

  it('preserves other style properties', () => {
    const nodes = [makeNode('a', { fan_out: 1, in_cycle: false })]
    const result = applyHeatmapColors(nodes as any, true)
    expect(result[0].style).toBeDefined()
  })

  it('nodes without health metadata get healthy color', () => {
    const nodes = [makeNode('a', {})]
    const result = applyHeatmapColors(nodes as any, true)
    expect(result[0].style?.background).toBe('#1b3a1b')
  })
})
