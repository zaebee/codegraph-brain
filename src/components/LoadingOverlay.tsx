/**
 * Overlay со спиннером "Computing layout...".
 * @param {Object} props
 * @param {boolean} props.visible - показывать ли overlay
 */
import "./LoadingOverlay.css";

export default function LoadingOverlay({ visible = false }: { visible: boolean }) {
  if (!visible) return null;

  return (
    <div className="loading-overlay">
      <div className="loading-indicator">
        <div className="loading-spinner" />
        <span>Computing layout...</span>
      </div>
    </div>
  );
}
