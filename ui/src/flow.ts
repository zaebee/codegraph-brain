import type { GraphNode, GraphEdge } from "./types";
import { deduplicateEdges } from "./utils";

/**
 * Build an execution flow from a start node using bidirectional DFS.
 * @param {{ nodes: GraphNode[], edges: GraphEdge[] }} graph - The full graph data.
 * @param {string} startNodeId - The node to start the flow from.
 * @param {number} [depth=3] - Maximum traversal depth.
 * @param {'outgoing' | 'incoming' | 'both'} [direction='both'] - Traversal direction.
 * @returns {{ nodes: GraphNode[], edges: GraphEdge[] }} The execution flow subgraph.
 */
export function buildExecutionFlow(
  graph: { nodes: GraphNode[]; edges: GraphEdge[] },
  startNodeId: string,
  depth: number = 3,
  direction: "outgoing" | "incoming" | "both" = "both",
  allowedEdgeTypes: string[] = ["CALLS", "IMPORTS", "CONTAINS"]
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodeMap = new Map(graph.nodes.map((n) => [n.id, n]));

  const outgoing = new Map<string, GraphEdge[]>();
  const incoming = new Map<string, GraphEdge[]>();

  graph.edges.forEach((e) => {
    if (!outgoing.has(e.source)) {
      outgoing.set(e.source, []);
    }
    outgoing.get(e.source)!.push(e);

    if (!incoming.has(e.target)) {
      incoming.set(e.target, []);
    }
    incoming.get(e.target)!.push(e);
  });

  const flowNodes = new Map<string, GraphNode>();
  const flowEdges: GraphEdge[] = [];

  function dfs(
    nodeId: string,
    currentDepth: number,
    edgesMap: Map<string, GraphEdge[]>,
    resolveNext: (edge: GraphEdge) => string,
    visited: Set<string>,
    nodeAcc: Map<string, GraphNode>,
    edgeAcc: GraphEdge[]
  ) {
    if (visited.has(nodeId) || currentDepth > depth) return;
    visited.add(nodeId);

    const node = nodeMap.get(nodeId);
    if (!node) return;

    nodeAcc.set(nodeId, node);

    const nodeEdges = edgesMap.get(nodeId) || [];
    nodeEdges.forEach((e) => {
      if (!allowedEdgeTypes.includes(e.type)) return;
      edgeAcc.push(e);
      dfs(resolveNext(e), currentDepth + 1, edgesMap, resolveNext, visited, nodeAcc, edgeAcc);
    });
  }

  if (direction === "outgoing" || direction === "both") {
    const outVisited = new Set<string>();
    const outNodes = new Map<string, GraphNode>();
    const outEdges: GraphEdge[] = [];
    dfs(startNodeId, 0, outgoing, (e) => e.target, outVisited, outNodes, outEdges);
    outNodes.forEach((node, id) => flowNodes.set(id, node));
    flowEdges.push(...outEdges);
  }

  if (direction === "incoming" || direction === "both") {
    const inVisited = new Set<string>();
    const inNodes = new Map<string, GraphNode>();
    const inEdges: GraphEdge[] = [];
    dfs(startNodeId, 0, incoming, (e) => e.source, inVisited, inNodes, inEdges);
    inNodes.forEach((node, id) => {
      if (!flowNodes.has(id)) flowNodes.set(id, node);
    });
    const merged = [...flowEdges, ...inEdges];
    flowEdges.length = 0;
    flowEdges.push(...deduplicateEdges(merged));
  }

  return {
    nodes: Array.from(flowNodes.values()),
    edges: flowEdges,
  };
}
