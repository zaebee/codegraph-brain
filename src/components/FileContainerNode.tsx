import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import styles from "./FileContainerNode.module.css";

function FileContainerNode({ data }: NodeProps) {
  const isExpanded = (data as Record<string, unknown>)?.isExpanded === true;
  const label = ((data as Record<string, unknown>)?.label as string) || "";
  const indicator = isExpanded ? "\u25BC" : "\u25B6";
  const cleanLabel = label.replace(/^[▶▼]\s/, "");

  return (
    <div className={`${styles.container} ${isExpanded ? styles.expanded : styles.collapsed}`}>
      <div className={styles.header}>
        <span className={styles.indicator}>{indicator}</span>
        <span className={styles.title}>{cleanLabel}</span>
      </div>
      {!isExpanded && (
        <Handle type="target" position={Position.Top} />
      )}
      {!isExpanded && (
        <Handle type="source" position={Position.Bottom} />
      )}
    </div>
  );
}

export default memo(FileContainerNode);
