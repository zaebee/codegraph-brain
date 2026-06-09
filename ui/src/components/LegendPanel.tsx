import { Panel } from "@xyflow/react";
import styles from "./LegendPanel.module.css";
import { NODE_LEGEND_ITEMS, EDGE_LEGEND_ITEMS } from "../theme";
import type { LegendItem } from "../theme";

export default function LegendPanel({ visible = true }: { visible: boolean }) {
  if (!visible) return null;

  return (
    <Panel position="bottom-right" style={{ marginRight: '210px' }} className={styles["legend-panel"]}>
      <div className={styles["legend-header"]}>Legend</div>
      <div className={styles["legend-columns"]}>
        <div className={styles["legend-col"]}>
          {NODE_LEGEND_ITEMS.map((item: LegendItem) => (
            <div key={item.type} className={styles["legend-item"]}>
              <span className={styles["legend-dot"]} style={{ background: item.color }} />
              {item.label}
            </div>
          ))}
        </div>
        <div className={styles["legend-col"]}>
          {EDGE_LEGEND_ITEMS.map((item: LegendItem) => (
            <div key={item.type} className={styles["legend-item"]}>
              <span className={styles["legend-line"]} style={{ background: item.color }} />
              {item.label}
            </div>
          ))}
        </div>
      </div>
    </Panel>
  );
}
