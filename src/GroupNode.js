import React from "react";

export default function GroupNode({ data }) {
  return (
    <div className="group-node">
      <div className="group-node-label">{data.label}</div>
    </div>
  );
}
