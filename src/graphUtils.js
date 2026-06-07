export function analyzeGraph(graph) {
  const nodeMap = new Map();
  graph.nodes.forEach(n => nodeMap.set(n.id, n));

  const brokenEdges = [];
  const unresolvedEdges = [];

  graph.edges.forEach(e => {
    if (!nodeMap.has(e.target)) {
      brokenEdges.push(e);
    }

    if (e.type === "CALLS" && !nodeMap.has(e.target)) {
      unresolvedEdges.push(e);
    }
  });

  return {
    nodeMap,
    brokenEdges,
    unresolvedEdges
  };
}
