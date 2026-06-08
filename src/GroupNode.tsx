import type { NodeProps } from "@xyflow/react";

export default function GroupNode({ data }: NodeProps) {
  const { label } = data as { label?: string };
  return (
    <div className="group-node">
      <div className="group-node-label">{label}</div>
    </div>
  );
}
