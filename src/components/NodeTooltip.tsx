import { useEffect, useState } from "react";
import "./NodeTooltip.css";
import type { Node } from "@xyflow/react";

interface NodeTooltipData {
  label?: string;
  subtitle?: string;
  file?: string;
  lines?: string;
}

/**
 * Tooltip, следующий за мышью.
 * @param {Object} props
 * @param {Object} props.node - текущий hovered node
 * @param {Object} props.mousePos - позиция мыши { x, y }
 */
export default function NodeTooltip({
  node,
  mousePos,
}: {
  node: Node | null;
  mousePos: { x: number; y: number };
}) {
  const [position, setPosition] = useState({ x: 0, y: 0 });

  // Update position on mouse move
  useEffect(() => {
    setPosition({
      x: mousePos.x + 12,
      y: mousePos.y + 12,
    });
  }, [mousePos]);

  if (!node) return null;

  const data = node.data as NodeTooltipData | undefined;

  return (
    <div
      className="tooltip-follow"
      style={{
        position: "fixed",
        left: position.x,
        top: position.y,
        pointerEvents: "none",
      }}
    >
      <div className="node-tooltip">
        <div className="tooltip-name">{data?.label}</div>
        <div className="tooltip-type">{data?.subtitle}</div>
        <div className="tooltip-file">{data?.file}</div>
        <div className="tooltip-lines">lines {data?.lines}</div>
      </div>
    </div>
  );
}
