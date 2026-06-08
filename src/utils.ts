/**
 * Filter edges to only include those where both source and target exist in nodes.
 * @param {{ id: string }[]} nodes
 * @param {{ source: string, target: string }[]} edges
 * @returns {{ source: string, target: string }[]}
 */
export function filterValidEdges(
  nodes: { id: string }[],
  edges: { source: string; target: string }[]
): { source: string; target: string }[] {
  const nodeIds = new Set(nodes.map((n) => n.id));
  return edges.filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target));
}

/**
 * Deduplicate edges by source->target key, keeping first occurrence.
 */
export function deduplicateEdges<T extends { source: string; target: string }>(edges: T[]): T[] {
  const seen = new Set();
  return edges.filter((e) => {
    const key = `${e.source}->${e.target}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
