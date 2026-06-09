/* eslint-disable @typescript-eslint/no-explicit-any */

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
  for (const n of nodes) {
    if (n.data?.nodeType === "FILE" && parentChildren.has(n.id)) {
      visibleIds.add(n.id);
      if (expandedFiles.has(n.id)) {
        const children = parentChildren.get(n.id) || [];
        for (const cid of children) {
          visibleIds.add(cid);
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
