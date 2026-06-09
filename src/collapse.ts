/* eslint-disable @typescript-eslint/no-explicit-any */

function collectAllDescendants(
  id: string,
  parentChildren: Map<string, string[]>
): string[] {
  const result: string[] = [];
  const direct = parentChildren.get(id) || [];
  for (const c of direct) {
    result.push(c);
    result.push(...collectAllDescendants(c, parentChildren));
  }
  return result;
}

export function getCollapsedView(
  nodes: any[],
  edges: any[],
  expandedFiles: Set<string>,
  parentChildren: Map<string, string[]>
): { nodes: any[]; edges: any[] } {
  if (expandedFiles.size === 0) {
    const fileIds = new Set(
      nodes.filter((n: any) => n.data?.nodeType === "FILE").map((n: any) => n.id)
    );
    const fileNodes = nodes.filter((n: any) => fileIds.has(n.id));
    const edgesBetweenFiles = edges.filter(
      (e: any) => fileIds.has(e.source) && fileIds.has(e.target)
    );
    return { nodes: fileNodes, edges: edgesBetweenFiles };
  }

  const visibleIds = new Set<string>();
  const allDescendantsCache = new Map<string, string[]>();

  function descendants(id: string): string[] {
    const cached = allDescendantsCache.get(id);
    if (cached) return cached;
    const all = collectAllDescendants(id, parentChildren);
    allDescendantsCache.set(id, all);
    return all;
  }

  for (const n of nodes) {
    if (n.data?.nodeType === "FILE") {
      visibleIds.add(n.id);
      if (expandedFiles.has(n.id)) {
        for (const descId of descendants(n.id)) {
          visibleIds.add(descId);
        }
      }
    }
  }

  const filteredNodes = nodes.filter((n: any) => visibleIds.has(n.id));
  const filteredEdges = edges.filter(
    (e: any) => visibleIds.has(e.source) && visibleIds.has(e.target)
  );
  return { nodes: filteredNodes, edges: filteredEdges };
}
