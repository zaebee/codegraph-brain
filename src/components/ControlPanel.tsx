import React, { useState } from "react";
import { Panel } from "@xyflow/react";
import { useReactFlow } from "@xyflow/react";
import type { RefObject } from "react";
import panelStyles from "./ControlPanel.module.css";
import sharedStyles from "../shared.module.css";
import { EDGE_COLORS } from "../theme";

const EDGE_COLOR_MAP: Record<string, string> = EDGE_COLORS;

export default function ControlPanel({
  onToggleExternal,
  showExternal,
  activeEdgeTypes,
  onToggleEdgeType,
  ALL_EDGE_TYPES,
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
  onToggleExternal?: (show: boolean) => void;
  showExternal: boolean;
  activeEdgeTypes: string[];
  onToggleEdgeType: (type: string) => void;
  ALL_EDGE_TYPES: string[];
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
    fitView({ padding: 0.15, duration: 250 });
    onFit?.();
  };

  if (collapsed) {
    return (
      <Panel position="top-left" className={panelStyles["control-panel"]}>
        <div className={panelStyles["panel-collapsed"]}>
          <button
            className={sharedStyles.btn + " " + sharedStyles["btn-icon-sm"]}
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
      <div className={panelStyles["panel-row-compact"]}>
        <div className={panelStyles["compact-header"]}>
          <button
            className={sharedStyles.btn + " " + sharedStyles["btn-icon-sm"]}
            onClick={() => setCollapsed(true)}
            aria-label="Collapse panel"
            title="Collapse panel"
          >
            ✕
          </button>
        </div>

        {/* Row 1: navigation / search + actions */}
        <div className={panelStyles["compact-row"]}>
          {viewMode === "flow" ? (
            <>
              <button className={sharedStyles.btn + " " + sharedStyles["btn-sm"]} onClick={onBack} aria-label="Back to full graph">
                ← Back
              </button>
              {flowRootId && (
                <button className={sharedStyles.btn + " " + sharedStyles["btn-sm"]} onClick={() => onBackToRoot?.()} aria-label="Back to root node">
                  ↑ Root
                </button>
              )}
              <button className={sharedStyles.btn + " " + sharedStyles["btn-sm"]} onClick={handleFit} aria-label="Zoom to fit" title="Fit">
                ⊞ Fit
              </button>
            </>
          ) : (
            <>
              <input
                type="text"
                placeholder="search..."
                className={panelStyles["search-input-compact"]}
                aria-label="Search nodes"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                ref={searchInputRef}
              />
              <button
                className={`${sharedStyles.btn} ${sharedStyles["btn-sm"]} ${showExternal ? sharedStyles.active : ""}`}
                onClick={() => onToggleExternal?.(!showExternal)}
                aria-label="Toggle external nodes"
                aria-pressed={showExternal}
              >
                {showExternal ? "●Ext" : "○Ext"}
              </button>
              <button className={sharedStyles.btn + " " + sharedStyles["btn-sm"]} onClick={handleFit} aria-label="Zoom to fit" title="Fit">
                ⊞
              </button>
              <button className={sharedStyles.btn + " " + sharedStyles["btn-sm"]} onClick={onExportPng} aria-label="Export as PNG" title="Export PNG">
                ⬇PNG
              </button>
              <button className={sharedStyles.btn + " " + sharedStyles["btn-sm"]} onClick={onExportSvg} aria-label="Export as SVG" title="Export SVG">
                ⬇SVG
              </button>
            </>
          )}
        </div>

        {/* Row 2: edge type toggles */}
        <div className={panelStyles["compact-row"]}>
          <div className={panelStyles["edge-type-buttons-compact"]}>
            {ALL_EDGE_TYPES.map((type) => {
              const isActive = activeEdgeTypes.includes(type);
              const color = EDGE_COLOR_MAP[type] || "#78909c";
              return (
                <button
                  key={type}
                  className={`${sharedStyles["btn-edge-type"]} ${isActive ? sharedStyles.active : ""}`}
                  onClick={() => onToggleEdgeType(type)}
                  aria-label={`Toggle ${type}`}
                  aria-pressed={isActive}
                  style={{ "--edge-color": color } as React.CSSProperties}
                  title={type}
                >
                  <span
                    className={panelStyles["edge-type-indicator"]}
                    style={{ background: isActive ? color : "transparent" }}
                  />
                  {type}
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </Panel>
  );
}
