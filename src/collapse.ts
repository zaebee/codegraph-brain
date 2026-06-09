/* eslint-disable @typescript-eslint/no-explicit-any */

export function getCollapsedView(
  nodes: any[],
  edges: any[],
  expandedFiles: Set<string>,
  expandedClasses: Set<string>,
  parentChildren: Map<string, string[]>
): { nodes: any[]; edges: any[] } {
  const hasAnyExpansion = expandedFiles.size > 0 || expandedClasses.size > 0;

  if (!hasAnyExpansion) {
    return { nodes, edges };
  }

  const hiddenChildren = new Set<string>();

  for (const [parentId, children] of parentChildren) {
    const parent = nodes.find((n: any) => n.id === parentId);
    if (!parent) continue;

    const isExpanded =
      (parent.data?.nodeType === "FILE" && expandedFiles.has(parentId)) ||
      (parent.data?.nodeType === "CLASS" && expandedClasses.has(parentId));

    if (!isExpanded) {
      for (const cid of children) {
        hiddenChildren.add(cid);
      }
    }
  }

  const filteredNodes = nodes.filter((n: any) => !hiddenChildren.has(n.id));
  const filteredEdges = edges.filter(
    (e: any) => !hiddenChildren.has(e.source) && !hiddenChildren.has(e.target)
  );
  return { nodes: filteredNodes, edges: filteredEdges };
}
