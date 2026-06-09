import type { Node } from '@xyflow/react'
import { getHeatmapColor } from '../theme'

export function applyHeatmapColors(nodes: Node[], enabled: boolean): Node[] {
  if (!enabled) return nodes
  return nodes.map((node) => {
    const meta = (node.data as Record<string, unknown>)?.metadata as Record<string, unknown> | undefined
    const fanOut = typeof meta?.fan_out === 'number' ? meta.fan_out : 0
    const inCycle = meta?.in_cycle === true
    const colors = getHeatmapColor(fanOut, inCycle)
    return {
      ...node,
      style: {
        ...node.style,
        background: colors.bg,
        borderColor: colors.border,
        color: colors.text,
      },
    }
  })
}
