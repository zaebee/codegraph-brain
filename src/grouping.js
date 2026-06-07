export function groupByClass(nodes, edges) {
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
            dir: dirPath
          },
          position: { x: 0, y: 0 },
          style: {
            background: "rgba(30, 40, 55, 0.6)",
            border: "2px dashed #546e7a",
            borderRadius: 12,
            padding: 10,
            zIndex: -1
          }
        });
      }
    }
  });

  const updatedNodes = nodes.map((n) => {
    if (n.class) {
      return {
        ...n,
        groupId: `file:${n.class}`
      };
    }
    return n;
  });

  return {
    nodes: [...Array.from(classMap.values()), ...updatedNodes],
    edges
  };
}
