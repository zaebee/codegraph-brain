import { MarkerType, type Edge } from "@xyflow/react";
import type { GraphEdge } from "../types";
import { EDGE_COLORS } from "../theme";

const FULL_EDGE_STYLES: Record<string, { stroke: string; strokeWidth: number; opacity: number }> = {};
for (const [type, stroke] of Object.entries(EDGE_COLORS)) {
  FULL_EDGE_STYLES[type] = { stroke, strokeWidth: 1.5, opacity: 0.8 };
}
FULL_EDGE_STYLES.DECLARES = { stroke: EDGE_COLORS.DECLARES, strokeWidth: 1, opacity: 0.6 };

function stableEdgeId(source: string, target: string, type: string): string {
  return `${source}→${target}:${type}`;
}

/**
 * Map a raw edge to a ReactFlow edge for the full graph view.
 */
export function mapEdgeToReactFlow(edge: GraphEdge, _index: number): Edge {
  const style = FULL_EDGE_STYLES[edge.type] || FULL_EDGE_STYLES.DECLARES;
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
