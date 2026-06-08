import type { Edge } from "@xyflow/react";
import type { GraphEdge, GraphNode } from "../types";

/**
 * Map a raw edge to a ReactFlow edge for the full graph view.
 */
export function mapEdgeToReactFlow(
  edge: GraphEdge,
  index: number,
  { enrichedNodes }: { enrichedNodes?: GraphNode[] } = {}
): Edge {
  const sourceIsExternal = enrichedNodes?.find((n) => n.id === edge.source)?.type === "EXTERNAL";
  const targetIsExternal = enrichedNodes?.find((n) => n.id === edge.target)?.type === "EXTERNAL";
  const isExternal = sourceIsExternal || targetIsExternal;
  return {
    id: `e-${index}`,
    source: edge.source,
    target: edge.target,
    style: {
      stroke: isExternal ? "#546e7a" : "#4fc3f7",
      strokeWidth: isExternal ? 1 : 1.5,
      opacity: isExternal ? 0.4 : 0.8,
    },
    animated: false,
    type: "default",
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
