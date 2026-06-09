/* eslint-disable @typescript-eslint/no-explicit-any */

import { NODE_WIDTH, NODE_HEIGHT, FILE_CONTAINER_PADDING, FILE_HEADER_HEIGHT, FILE_HEADER_GAP } from "./constants";
import type { Node, Edge } from "@xyflow/react";

let dagreInstance: any = null;

async function getDagre() {
  if (!dagreInstance) {
    const mod = await import("dagre");
    dagreInstance = mod.default;
  }
  return dagreInstance;
}

export async function layoutGraph(
  nodes: Node[],
  edges: Edge[],
  expandedFiles: Set<string> = new Set(),
  parentChildren: Map<string, string[]> = new Map()
): Promise<{ nodes: Node[]; edges: Edge[] }> {
  const dagre = await getDagre();

  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 60, ranksep: 100 });
  g.setDefaultEdgeLabel(() => ({}));

  nodes.forEach((node) => {
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  });

  edges.forEach((edge) => {
    if (g.hasNode(edge.source) && g.hasNode(edge.target)) {
      g.setEdge(edge.source, edge.target);
    }
  });

  dagre.layout(g);

  const positioned = new Map<string, { x: number; y: number }>();
  nodes.forEach((node) => {
    const pos = g.node(node.id);
    if (pos) {
      positioned.set(node.id, { x: pos.x - NODE_WIDTH / 2, y: pos.y - NODE_HEIGHT / 2 });
    }
  });

  let resultEdges = edges;
  let resultNodes = nodes.map((n) => ({ ...n, position: positioned.get(n.id) || n.position }));

  if (expandedFiles.size > 0) {
    resultNodes.forEach((node) => {
      if (node.data?.nodeType === "FILE" && expandedFiles.has(node.id)) {
        const children = parentChildren.get(node.id) || [];
        if (children.length === 0) return;

        const childPositions = children
          .map((cid) => {
            const child = resultNodes.find((n: any) => n.id === cid);
            const pos = child?.position;
            if (!pos) return null;
            return {
              x: pos.x,
              y: pos.y,
              width: (child.style as any)?.width || NODE_WIDTH,
              height: (child.style as any)?.height || NODE_HEIGHT,
            };
          })
          .filter(Boolean) as Array<{ x: number; y: number; width: number; height: number }>;

        if (childPositions.length === 0) return;

        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        for (const cp of childPositions) {
          minX = Math.min(minX, cp.x);
          maxX = Math.max(maxX, cp.x + cp.width);
          minY = Math.min(minY, cp.y);
          maxY = Math.max(maxY, cp.y + cp.height);
        }

        const containerW = maxX - minX + FILE_CONTAINER_PADDING * 2;
        const containerH = maxY - minY + FILE_CONTAINER_PADDING * 2 + FILE_HEADER_HEIGHT + FILE_HEADER_GAP;

        node.type = "fileContainer";
        node.position = {
          x: minX - FILE_CONTAINER_PADDING,
          y: minY - FILE_CONTAINER_PADDING - FILE_HEADER_HEIGHT - FILE_HEADER_GAP,
        };
        (node as any).style = { width: containerW, height: containerH };
      }
    });

    const containers = resultNodes
      .filter((n: any) => n.type === "fileContainer")
      .map((n: any) => ({
        id: n.id,
        x: n.position.x,
        y: n.position.y,
        width: n.style.width as number || NODE_WIDTH,
        height: n.style.height as number || NODE_HEIGHT,
      }))
      .sort((a, b) => a.y - b.y);

    for (let i = 0; i < containers.length; i++) {
      for (let j = 0; j < i; j++) {
        const a = containers[i];
        const b = containers[j];
        const horizOverlap = a.x < b.x + b.width && a.x + a.width > b.x;
        const vertOverlap = a.y < b.y + b.height && a.y + a.height > b.y;
        if (horizOverlap && vertOverlap) {
          const shiftY = b.y + b.height + 20 - a.y;
          a.y += shiftY;
          const node = resultNodes.find((n: any) => n.id === a.id) as any;
          if (node) node.position.y += shiftY;
        }
      }
    }

    resultEdges = resultEdges.filter((e: any) => {
      if (e.data?.edgeType !== "CONTAINS") return true;
      if (expandedFiles.has(e.source)) return false;
      return true;
    });
  }

  return { nodes: resultNodes, edges: resultEdges };
}
