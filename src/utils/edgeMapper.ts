import { MarkerType, type Edge } from "@xyflow/react";
import type { GraphEdge } from "../types";
import { EDGE_COLORS } from "../theme";

const FULL_EDGE_STYLES: Record<string, { stroke: string; strokeWidth: number; opacity: number }> = {};
for (const [type, stroke] of Object.entries(EDGE_COLORS)) {
  FULL_EDGE_STYLES[type] = { stroke, strokeWidth: 1.5, opacity: 0.8 };
}
FULL_EDGE_STYLES.DECLARES = { stroke: EDGE_COLORS.DECLARES, strokeWidth: 1, opacity: 0.6 };

/**
 * Map a raw edge to a ReactFlow edge for the full graph view.
 */
export function mapEdgeToReactFlow(edge: GraphEdge, index: number): Edge {
  const style = FULL_EDGE_STYLES[edge.type] || FULL_EDGE_STYLES.DECLARES;
  return {
    id: `e-${index}`,
    source: edge.source,
    target: edge.target,
    style,
    animated: false,
    type: "default",
    markerEnd: { type: MarkerType.ArrowClosed },
    data: { edgeType: edge.type },
  } as Edge;
}

/**
 * Map a raw edge to a ReactFlow edge for the flow (execution) view.
 */
export function mapEdgeToFlowView(edge: GraphEdge, index: number): Edge {
  return {
    id: `flow-${index}`,
    source: edge.source,
    target: edge.target,
    animated: true,
    style: {
      stroke: "#ff9800",
      strokeWidth: 2,
    },
  } as Edge;
}
