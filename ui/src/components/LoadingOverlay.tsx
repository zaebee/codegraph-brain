/**
 * Overlay со спиннером "Computing layout...".
 * @param {Object} props
 * @param {boolean} props.visible - показывать ли overlay
 */
import styles from "./LoadingOverlay.module.css";

export default function LoadingOverlay({ visible = false }: { visible: boolean }) {
  if (!visible) return null;

  return (
    <div className={styles["loading-overlay"]}>
      <div className={styles["loading-indicator"]}>
        <div className={styles["loading-spinner"]} />
        <span>Computing layout...</span>
      </div>
    </div>
  );
}
