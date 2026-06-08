import { useState } from "react";
import { Panel } from "@xyflow/react";
import { useReactFlow } from "@xyflow/react";
import type { RefObject } from "react";
import panelStyles from "./ControlPanel.module.css";
import sharedStyles from "../shared.module.css";

export default function ControlPanel({
  onDepthChange,
  depth,
  onToggleExternal,
  showExternal,
  onExportPng,
  onExportSvg,
  onFit,
  viewMode,
  flowRootId,
  onBack,
  onBackToRoot,
  searchQuery,
  setSearchQuery,
  searchInputRef,
}: {
  onDepthChange: (depth: number) => void;
  depth: number;
  onToggleExternal?: (show: boolean) => void;
  showExternal: boolean;
  onExportPng?: () => void;
  onExportSvg?: () => void;
  onFit?: () => void;
  viewMode: string;
  flowRootId: string | null;
  onBack?: () => void;
  onBackToRoot?: () => void;
  searchQuery: string;
  setSearchQuery: (q: string) => void;
  searchInputRef?: RefObject<HTMLInputElement | null>;
}) {
  const { fitView } = useReactFlow();
  const [collapsed, setCollapsed] = useState(false);

  const handleFit = () => {
    fitView({ padding: 0.15 });
    onFit?.();
  };

  if (collapsed) {
    return (
      <Panel position="top-left" className={panelStyles["control-panel"]}>
        <div className={panelStyles["panel-collapsed"]}>
          <button
            className={sharedStyles.btn + " " + sharedStyles["btn-icon"]}
            onClick={() => setCollapsed(false)}
            aria-label="Expand panel"
            title="Expand panel"
          >
            ☰
          </button>
        </div>
      </Panel>
    );
  }

  return (
    <Panel position="top-left" className={panelStyles["control-panel"]}>
      <div className={panelStyles["panel-row"]}>
        <div className={panelStyles["panel-header"]}>
          <span className={panelStyles["panel-title"]}>Controls</span>
          <button
            className={sharedStyles.btn + " " + sharedStyles["btn-icon"]}
            onClick={() => setCollapsed(true)}
            aria-label="Collapse panel"
            title="Collapse panel"
          >
            ✕
          </button>
        </div>

        {viewMode === "flow" ? (
          <div className={panelStyles["panel-content"]}>
            <button className={sharedStyles.btn + " " + sharedStyles["btn-back"]} onClick={onBack} aria-label="Back to full graph">
              ← Back
            </button>
            {flowRootId && (
              <button
                className={sharedStyles.btn + " " + sharedStyles["btn-back"]}
                onClick={() => onBackToRoot?.()}
                aria-label="Back to root node"
              >
                ↑ Root
              </button>
            )}
            <button className={sharedStyles.btn + " " + sharedStyles["btn-export"]} onClick={handleFit} aria-label="Zoom to fit">
              ⊞ Fit
            </button>
          </div>
        ) : (
          <div className={panelStyles["panel-content"]}>
            <div className={panelStyles["depth-control"]}>
              <span className={panelStyles["depth-label"]}>Depth</span>
              <input
                type="range"
                min="1"
                max="5"
                value={depth}
                onChange={(e) => onDepthChange(Number(e.target.value))}
                className={panelStyles["depth-slider"]}
                aria-label="Flow depth"
                aria-valuemin={1}
                aria-valuemax={5}
                aria-valuenow={depth}
              />
              <span className={panelStyles["depth-value"]}>{depth}</span>
              <div className={panelStyles["depth-presets"]}>
                <button
                  className={`${sharedStyles.btn} ${sharedStyles["btn-preset"]} ${depth === 1 ? sharedStyles.active : ""}`}
                  onClick={() => onDepthChange(1)}
                >
                  1: Immediate
                </button>
                <button
                  className={`${sharedStyles.btn} ${sharedStyles["btn-preset"]} ${depth === 2 ? sharedStyles.active : ""}`}
                  onClick={() => onDepthChange(2)}
                >
                  2: Near
                </button>
                <button
                  className={`${sharedStyles.btn} ${sharedStyles["btn-preset"]} ${depth === 3 ? sharedStyles.active : ""}`}
                  onClick={() => onDepthChange(3)}
                >
                  3: Call tree
                </button>
              </div>
            </div>
            <button className={sharedStyles.btn + " " + sharedStyles["btn-export"]} onClick={handleFit} aria-label="Zoom to fit">
              ⊞ Fit
            </button>
          </div>
        )}

        {viewMode === "full" && (
          <div className={panelStyles["panel-content"]}>
            <input
              type="text"
              placeholder="Search nodes... (press / to focus)"
              className={panelStyles["search-input"]}
              aria-label="Search nodes"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              ref={searchInputRef}
            />
            <button
              className={`${sharedStyles.btn} ${sharedStyles["btn-toggle"]} ${showExternal ? sharedStyles.active : ""}`}
              onClick={() => onToggleExternal?.(!showExternal)}
              aria-label="Toggle external nodes"
              aria-pressed={showExternal}
            >
              {showExternal ? "● External" : "○ External"}
            </button>
            <button className={sharedStyles.btn + " " + sharedStyles["btn-export"]} onClick={onExportPng} aria-label="Export as PNG">
              ⬇ PNG
            </button>
            <button className={sharedStyles.btn + " " + sharedStyles["btn-export"]} onClick={onExportSvg} aria-label="Export as SVG">
              ⬇ SVG
            </button>
          </div>
        )}
      </div>
    </Panel>
  );
}
