import type { NodeProps } from "@xyflow/react";

export default function GroupNode({ data }: NodeProps) {
  return (
    <div className="group-node">
      <div className="group-node-label">{data.label}</div>
    </div>
  );
}
