import type { Edge } from "@xyflow/react";

export function aggregateEdges(
  edges: Edge[],
  aggregateTypes: string[] = ["CALLS"]
): Edge[] {
  const singles: Edge[] = [];
  const groups = new Map<string, Edge[]>();

  for (const edge of edges) {
    const edgeType = edge.data?.edgeType as string | undefined;
    if (!edgeType || !aggregateTypes.includes(edgeType)) {
      singles.push(edge);
      continue;
    }
    const key = `${edge.source}|${edge.target}|${edgeType}`;
    const existing = groups.get(key);
    if (existing) {
      existing.push(edge);
    } else {
      groups.set(key, [edge]);
    }
  }

  const aggregated: Edge[] = [];
  for (const groupEdges of groups.values()) {
    if (groupEdges.length === 1) {
      aggregated.push(groupEdges[0]);
    } else {
      const first = groupEdges[0];
      const strokeWidth = Math.max(1.5, Math.sqrt(groupEdges.length) * 1.5);
      aggregated.push({
        ...first,
        id: `agg-${first.source}-${first.target}-${first.data?.edgeType}`,
        label: `×${groupEdges.length}`,
        data: {
          ...first.data,
          isAggregated: true,
          aggregatedCount: groupEdges.length,
          aggregatedEdgeIds: groupEdges.map((e) => e.id),
          label: `×${groupEdges.length}`,
        },
        style: {
          ...(first.style || {}),
          strokeWidth,
        },
      });
    }
  }

  return [...singles, ...aggregated];
}
