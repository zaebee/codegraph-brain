import { Panel } from "@xyflow/react";
import styles from "./LegendPanel.module.css";
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
    <Panel position="bottom-right" className={styles["legend-panel"]}>
      <div className={styles.legend}>
        {LEGEND_ITEMS.map((item: LegendItem) => (
          <div key={item.type} className={styles["legend-item"]}>
            <span className={styles["legend-dot"]} style={{ background: item.color }} />
            <span className={styles["legend-label"]}>{item.label}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}
