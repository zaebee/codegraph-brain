import type { Node, Edge } from "@xyflow/react";

/**
 * Extract the group key from a node for file-based grouping.
 * @param {{ type: string, file_path?: string }} node
 * @returns {string|null} The file path to group by, or null for external nodes.
 */
export function extractGroupKey(node: { file_path?: string }): string | null {
  if (node.file_path) return node.file_path;
  return null;
}

/**
 * Group nodes by their file path, creating group container nodes.
 * @param {{ id: string, class?: string }[]} nodes
 * @param {{ source: string, target: string }[]} edges
 * @returns {{ nodes: Array, edges: Array }} Grouped nodes with group containers.
 */
export function groupByFile(
  nodes: Array<{ id: string; class?: string }>,
  edges: { source: string; target: string }[]
): { nodes: Node[]; edges: Edge[] } {
  const classMap = new Map();

  nodes.forEach((n) => {
    if (n.class) {
      if (!classMap.has(n.class)) {
        const parts = n.class.split("/");
        const fileName = parts[parts.length - 1];
        const dirPath = parts.slice(0, -1).join("/");

        classMap.set(n.class, {
          id: `file:${n.class}`,
          type: "group",
          selectable: false,
          data: {
            label: fileName,
            fullPath: n.class,
            dir: dirPath,
          },
          position: { x: 0, y: 0 },
          style: {
            background: "rgba(30, 40, 55, 0.6)",
            border: "2px dashed #546e7a",
            borderRadius: 12,
            padding: 10,
            zIndex: -1,
          },
        });
      }
    }
  });

  const updatedNodes = nodes.map((n) => {
    if (n.class) {
      return {
        ...n,
        groupId: `file:${n.class}`,
      };
    }
    return n;
  });

  return {
    nodes: [...Array.from(classMap.values()), ...updatedNodes],
    edges: edges as Edge[],
  };
}
