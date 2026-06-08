import { Panel } from "@xyflow/react";
import "./LegendPanel.css";
import { LEGEND_ITEMS } from "../theme";
import type { LegendItem } from "../theme";

/**
 * Панель с легендой node types.
 * @param {Object} props
 * @param {boolean} props.visible - показывать ли панель
 */
export default function LegendPanel({ visible = true }: { visible: boolean }) {
  if (!visible) return null;

  return (
    <Panel position="bottom-right" className="legend-panel">
      <div className="legend">
        {LEGEND_ITEMS.map((item: LegendItem) => (
          <div key={item.type} className="legend-item">
            <span className="legend-dot" style={{ background: item.color }} />
            <span className="legend-label">{item.label}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}
