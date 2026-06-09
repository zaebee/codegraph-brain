import { Panel } from "@xyflow/react";
import styles from "./StatsPanel.module.css";

/**
 * Панель со статистикой (nodes, edges, external).
 * @param {Object} props
 * @param {Object} props.stats - объект со статистикой { nodes, edges, external }
 * @param {boolean} props.visible - показывать ли панель
 */
export default function StatsPanel({
  stats,
  visible = true,
}: {
  stats: { nodes: number; edges: number; external: number } | null;
  visible: boolean;
}) {
  if (!stats || !visible) return null;

  return (
    <Panel position="top-right" className={styles["stats-panel"]}>
      <div className={styles.stats}>
        <span>{stats.nodes} nodes</span>
        <span className={styles["stats-sep"]}>|</span>
        <span>{stats.edges} edges</span>
        <span className={styles["stats-sep"]}>|</span>
        <span className={styles["stats-ext"]}>{stats.external} ext</span>
      </div>
    </Panel>
  );
}
