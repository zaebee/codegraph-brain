/* eslint-disable @typescript-eslint/no-explicit-any */

import { NODE_WIDTH, NODE_HEIGHT } from "./constants";
import type { Node, Edge } from "@xyflow/react";

let dagreInstance: any = null;

async function getDagre() {
  if (!dagreInstance) {
    const mod = await import("dagre");
    dagreInstance = mod.default;
  }
  return dagreInstance;
}

/**
 * Layout nodes using dagre with TB direction.
 * All nodes are treated as leaf nodes; group containers are handled upstream.
 */
export async function layoutGraph(nodes: Node[], edges: Edge[]): Promise<Node[]> {
  const dagre = await getDagre();

  const g = new dagre.graphlib.Graph();
  g.setGraph({
    rankdir: "TB",
    nodesep: 60,
    ranksep: 100,
  });
  g.setDefaultEdgeLabel(() => ({}));

  nodes.forEach((node) => {
    g.setNode(node.id, {
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    });
  });

  edges.forEach((edge) => {
    if (g.hasNode(edge.source) && g.hasNode(edge.target)) {
      g.setEdge(edge.source, edge.target);
    }
  });

  dagre.layout(g);

  return nodes.map((node) => {
    const pos = g.node(node.id);
    if (!pos) return node;
    return {
      ...node,
      position: {
        x: pos.x - NODE_WIDTH / 2,
        y: pos.y - NODE_HEIGHT / 2,
      },
    };
  });
}
