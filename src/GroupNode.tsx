import styles from "./GroupNode.module.css";
import type { NodeProps } from "@xyflow/react";

export default function GroupNode({ data }: NodeProps) {
  const { label } = data as { label?: string };
  return (
    <div className={styles["group-node"]}>
      <div className={styles["group-node-label"]}>{label}</div>
    </div>
  );
}
