export function buildExecutionFlow(graph, startNodeId, depth = 3, direction = "both") {
  const nodeMap = new Map(graph.nodes.map(n => [n.id, n]));

  const outgoing = new Map();
  const incoming = new Map();

  graph.edges.forEach(e => {
    if (!outgoing.has(e.source)) {
      outgoing.set(e.source, []);
    }
    outgoing.get(e.source).push(e);

    if (!incoming.has(e.target)) {
      incoming.set(e.target, []);
    }
    incoming.get(e.target).push(e);
  });

  const visited = new Set();
  const flowNodes = new Map();
  const flowEdges = [];

  function dfs(nodeId, currentDepth, edges) {
    if (visited.has(nodeId) || currentDepth > depth) return;

    visited.add(nodeId);

    const node = nodeMap.get(nodeId);
    if (!node) return;

    flowNodes.set(nodeId, node);

    const outEdges = edges.get(nodeId) || [];

    outEdges.forEach(e => {
      if (e.type !== "CALLS") return;

      flowEdges.push(e);
      dfs(e.target, currentDepth + 1, edges);
    });
  }

  if (direction === "outgoing" || direction === "both") {
    dfs(startNodeId, 0, outgoing);
  }

  if (direction === "incoming" || direction === "both") {
    const inVisited = new Set();
    const inFlowNodes = new Map();
    const inFlowEdges = [];

    function dfsIn(nodeId, currentDepth) {
      if (inVisited.has(nodeId) || currentDepth > depth) return;

      inVisited.add(nodeId);

      const node = nodeMap.get(nodeId);
      if (!node) return;

      inFlowNodes.set(nodeId, node);

      const inEdges = incoming.get(nodeId) || [];

      inEdges.forEach(e => {
        if (e.type !== "CALLS") return;

        inFlowEdges.push(e);
        dfsIn(e.source, currentDepth + 1);
      });
    }

    dfsIn(startNodeId, 0);

    inFlowNodes.forEach((node, id) => {
      if (!flowNodes.has(id)) {
        flowNodes.set(id, node);
      }
    });

    const edgeKeySet = new Set(flowEdges.map(e => `${e.source}->${e.target}`));

    inFlowEdges.forEach(e => {
      const key = `${e.source}->${e.target}`;
      if (!edgeKeySet.has(key)) {
        edgeKeySet.add(key);
        flowEdges.push(e);
      }
    });
  }

  return {
    nodes: Array.from(flowNodes.values()),
    edges: flowEdges
  };
}
