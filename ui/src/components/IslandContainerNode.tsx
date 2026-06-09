import type { NodeProps, Node } from '@xyflow/react'
import type { CSSProperties } from 'react'
import { NAMESPACE_COLORS } from '../theme'

const NAMESPACE_LABEL: Record<string, string> = {
  'owner-api': '🐍 owner-api',
  'owner-web': '⚛️ owner-web',
  'ownima-admin': '⚛️ admin',
  'rider-web': '🌐 rider-web',
  _default: '📦 default',
}

type IslandContainerData = { namespace: string; style?: CSSProperties }

export default function IslandContainerNode({ data }: NodeProps<Node<IslandContainerData>>) {
  const ns = data.namespace
  const color = NAMESPACE_COLORS[ns] ?? '#546e7a'
  const label = NAMESPACE_LABEL[ns] ?? ns

  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        border: `2px solid ${color}`,
        borderRadius: 10,
        background: `${color}11`,
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          position: 'absolute',
          top: -1,
          left: 10,
          background: color,
          color: '#fff',
          fontSize: 10,
          fontWeight: 'bold',
          padding: '2px 8px',
          borderRadius: '0 0 4px 4px',
        }}
      >
        {label}
      </div>
    </div>
  )
}
