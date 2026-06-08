import { Panel } from "@xyflow/react";
import "./StatsPanel.css";

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
    <Panel position="top-right" className="stats-panel">
      <div className="stats">
        <span>{stats.nodes} nodes</span>
        <span className="stats-sep">|</span>
        <span>{stats.edges} edges</span>
        <span className="stats-sep">|</span>
        <span className="stats-ext">{stats.external} ext</span>
      </div>
    </Panel>
  );
}
