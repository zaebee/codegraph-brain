import { MarkerType, type Edge } from "@xyflow/react";
import type { GraphEdge } from "../types";
import { EDGE_COLORS } from "../theme";

const FULL_EDGE_STYLES: Record<string, { stroke: string; strokeWidth: number; opacity: number }> = {};
for (const [type, stroke] of Object.entries(EDGE_COLORS)) {
  FULL_EDGE_STYLES[type] = { stroke, strokeWidth: 1.5, opacity: 0.8 };
}
FULL_EDGE_STYLES.DECLARES = { stroke: EDGE_COLORS.DECLARES, strokeWidth: 1, opacity: 0.6 };

// confidence=1.0 → fully resolved call (thick)
// confidence=0.8 → resolved to external/builtin (medium)
// confidence<0.5 → unresolved raw_call (thin)
function confidenceToWidth(confidence: number | undefined): number {
  if (confidence == null || confidence >= 0.9) return 2.2
  if (confidence >= 0.7) return 1.2
  return 0.6
}

function stableEdgeId(source: string, target: string, type: string): string {
  return `${source}→${target}:${type}`;
}

/**
 * Map a raw edge to a ReactFlow edge for the full graph view.
 */
export function mapEdgeToReactFlow(edge: GraphEdge, _index: number): Edge {
  const base = FULL_EDGE_STYLES[edge.type] || FULL_EDGE_STYLES.DECLARES;
  const style = { ...base, strokeWidth: confidenceToWidth(edge.confidence) };
  return {
    id: stableEdgeId(edge.source, edge.target, edge.type),
    source: edge.source,
    target: edge.target,
    style,
    animated: false,
    type: "default",
    markerEnd: { type: MarkerType.ArrowClosed, color: style.stroke },
    data: { edgeType: edge.type },
  } as Edge;
}

/**
 * Map a raw edge to a ReactFlow edge for the flow (execution) view.
 */
export function mapEdgeToFlowView(edge: GraphEdge, _index: number): Edge {
  const color = "#ff9800";
  return {
    id: stableEdgeId(edge.source, edge.target, edge.type),
    source: edge.source,
    target: edge.target,
    animated: true,
    style: {
      stroke: color,
      strokeWidth: 2,
    },
    markerEnd: { type: MarkerType.ArrowClosed, color },
    data: { edgeType: edge.type },
  } as Edge;
}
