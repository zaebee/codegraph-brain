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
