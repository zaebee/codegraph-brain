/* eslint-disable @typescript-eslint/no-explicit-any */

import { Node } from "@xyflow/react";
import type { GraphNode } from "../types";
import { NODE_COLORS, FLOW_NODE_COLORS } from "../theme";
import { NODE_WIDTH } from "../constants";

interface MapNodeOptions {
  groupKey?: string;
}

interface MapFlowViewOptions {
  isRoot?: boolean;
}

/**
 * Map a raw graph node to a ReactFlow node for the full graph view.
 */
export function mapNodeToReactFlow(n: GraphNode, { groupKey }: MapNodeOptions = {}): Node {
  const colors = (NODE_COLORS as any)[n.type] || NODE_COLORS.DEFAULT;
  return {
    id: n.id,
    data: {
      label: n.name,
      subtitle: n.type,
      file: n.file_path,
      lines: `${n.start_line}-${n.end_line}`,
      nodeType: n.type,
    },
    position: { x: 0, y: 0 },
    style: {
      background: colors.bg,
      border: `2px solid ${colors.border}`,
      borderRadius: 8,
      color: colors.text,
      padding: "8px 12px",
      fontSize: 12,
      fontWeight: 500,
      boxShadow: "0 2px 8px rgba(0,0,0,0.3)",
      width: NODE_WIDTH,
      minHeight: 40,
      opacity: n.type === "EXTERNAL" ? 0.6 : 1,
    },
    ...(groupKey != null ? { class: groupKey } : {}),
  } as Node;
}

/**
 * Map a raw graph node to a ReactFlow node for the flow (execution) view.
 */
export function mapNodeToFlowView(n: GraphNode, { isRoot = false }: MapFlowViewOptions = {}): Node {
  const isExternal = n.type === "EXTERNAL";
  const colors = isRoot
    ? FLOW_NODE_COLORS.ROOT
    : isExternal
      ? NODE_COLORS.EXTERNAL
      : FLOW_NODE_COLORS.DEFAULT;
  return {
    id: n.id,
    data: {
      label: n.name,
      subtitle: n.type,
      file: n.file_path,
      lines: `${n.start_line}-${n.end_line}`,
      nodeType: n.type,
    },
    position: { x: 0, y: 0 },
    style: {
      background: colors.bg,
      border: `2px solid ${colors.border}`,
      borderRadius: 8,
      color: colors.text,
      padding: "8px 12px",
      fontSize: 12,
      fontWeight: isRoot ? 700 : 500,
      boxShadow: isRoot ? "0 0 16px rgba(244,67,54,0.5)" : "0 2px 8px rgba(0,0,0,0.3)",
      width: NODE_WIDTH,
      minHeight: 40,
      opacity: isExternal ? 0.5 : 1,
    },
  } as Node;
}
