/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Apply context highlighting to nodes and edges based on hovered node.
 * Non-connected elements get dimmed to visually emphasize the hovered node's neighborhood.
 * Preserves object references for unchanged nodes/edges to avoid ReactFlow re-renders.
 */
export function applyContextHighlight(
  nodes: any[],
  edges: any[],
  hoveredNodeId: string | null
): { nodes: any[]; edges: any[] } {
  if (!hoveredNodeId || nodes.length === 0) return { nodes, edges };

  const connected = new Set<string>([hoveredNodeId]);
  for (const edge of edges) {
    if (edge.source === hoveredNodeId) connected.add(edge.target);
    if (edge.target === hoveredNodeId) connected.add(edge.source);
  }

  // If hovering a child node, also keep its file container visible.
  // Then add all nodes whose groupId is already in `connected` (handles
  // both hovering the container → show children, and hovering a child → show siblings).
  const hoveredNode = nodes.find((n) => n.id === hoveredNodeId);
  const hoveredGroupId = hoveredNode?.data?.groupId as string | undefined;
  if (hoveredGroupId) connected.add(hoveredGroupId);
  for (const n of nodes) {
    const groupId = n.data?.groupId as string | undefined;
    if (groupId && connected.has(groupId)) connected.add(n.id);
  }

  const highlightedEdgeIds = new Set<string>();
  for (const edge of edges) {
    if (connected.has(edge.source) && connected.has(edge.target)) {
      highlightedEdgeIds.add(edge.id);
    }
  }

  let hasChanges = false;
  const resultNodes = nodes.map((n) => {
    if (connected.has(n.id)) return n;
    hasChanges = true;
    return { ...n, style: { ...n.style, opacity: (n.style?.opacity ?? 1) * 0.15 } };
  });
  const resultEdges = edges.map((e) => {
    if (highlightedEdgeIds.has(e.id)) return e;
    hasChanges = true;
    return { ...e, style: { ...e.style, opacity: (e.style?.opacity ?? 1) * 0.05 } };
  });

  if (!hasChanges) return { nodes, edges };

  return { nodes: resultNodes, edges: resultEdges };
}
