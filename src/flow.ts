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
  direction: "outgoing" | "incoming" | "both" = "both"
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

  const visited = new Set<string>();
  const flowNodes = new Map<string, GraphNode>();
  const flowEdges: GraphEdge[] = [];

  function dfs(nodeId: string, currentDepth: number, edges: Map<string, GraphEdge[]>) {
    if (visited.has(nodeId) || currentDepth > depth) return;

    visited.add(nodeId);

    const node = nodeMap.get(nodeId);
    if (!node) return;

    flowNodes.set(nodeId, node);

    const outEdges = edges.get(nodeId) || [];

    outEdges.forEach((e) => {
      if (e.type !== "CALLS") return;

      flowEdges.push(e);
      dfs(e.target, currentDepth + 1, edges);
    });
  }

  if (direction === "outgoing" || direction === "both") {
    dfs(startNodeId, 0, outgoing);
  }

  if (direction === "incoming" || direction === "both") {
    const inVisited = new Set<string>();
    const inFlowNodes = new Map<string, GraphNode>();
    const inFlowEdges: GraphEdge[] = [];

    function dfsIn(nodeId: string, currentDepth: number) {
      if (inVisited.has(nodeId) || currentDepth > depth) return;

      inVisited.add(nodeId);

      const node = nodeMap.get(nodeId);
      if (!node) return;

      inFlowNodes.set(nodeId, node);

      const inEdges = incoming.get(nodeId) || [];

      inEdges.forEach((e) => {
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

    const allEdges: GraphEdge[] = [...flowEdges, ...inFlowEdges];
    flowEdges.length = 0;
    flowEdges.push(...deduplicateEdges(allEdges));
  }

  return {
    nodes: Array.from(flowNodes.values()),
    edges: flowEdges,
  };
}
