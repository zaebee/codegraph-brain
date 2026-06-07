import dagre from "dagre";

const NODE_WIDTH = 160;
const NODE_HEIGHT = 50;
const GROUP_PADDING = 40;
const GROUP_HEADER = 50;
const GROUP_SPACING = 40;

export function layoutGraph(nodes, edges) {
  const leafNodes = nodes.filter(n => n.type !== "group");

  const childrenByParent = new Map();
  leafNodes.forEach((node) => {
    if (node.parentNode) {
      if (!childrenByParent.has(node.parentNode)) {
        childrenByParent.set(node.parentNode, []);
      }
      childrenByParent.get(node.parentNode).push(node.id);
    }
  });

  const g = new dagre.graphlib.Graph();
  g.setGraph({
    rankdir: "TB",
    nodesep: 60,
    ranksep: 100
  });
  g.setDefaultEdgeLabel(() => ({}));

  leafNodes.forEach((node) => {
    g.setNode(node.id, {
      width: NODE_WIDTH,
      height: NODE_HEIGHT
    });
  });

  edges.forEach((edge) => {
    if (g.hasNode(edge.source) && g.hasNode(edge.target)) {
      g.setEdge(edge.source, edge.target);
    }
  });

  dagre.layout(g);

  const positioned = new Map();
  leafNodes.forEach((node) => {
    const pos = g.node(node.id);
    if (pos) {
      positioned.set(node.id, {
        x: pos.x - NODE_WIDTH / 2,
        y: pos.y - NODE_HEIGHT / 2
      });
    }
  });

  const groupBounds = new Map();

  childrenByParent.forEach((childIds, groupId) => {
    if (childIds.length === 0) return;

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;

    childIds.forEach((childId) => {
      const pos = positioned.get(childId);
      if (!pos) return;
      minX = Math.min(minX, pos.x);
      maxX = Math.max(maxX, pos.x + NODE_WIDTH);
      minY = Math.min(minY, pos.y);
      maxY = Math.max(maxY, pos.y + NODE_HEIGHT);
    });

    if (minX === Infinity) return;

    groupBounds.set(groupId, { minX, maxX, minY, maxY, childIds });
  });

  const sortedGroups = [...groupBounds.entries()].sort(
    (a, b) => a[1].minY - b[1].minY
  );

  const groupPositions = new Map();
  let currentY = 0;

  sortedGroups.forEach(([groupId, bounds]) => {
    const groupWidth = bounds.maxX - bounds.minX + GROUP_PADDING * 2;
    const childHeight = bounds.maxY - bounds.minY;
    const groupHeight = childHeight + GROUP_PADDING * 2 + GROUP_HEADER;

    const groupX = bounds.minX - GROUP_PADDING;
    const groupY = currentY;

    groupPositions.set(groupId, {
      x: groupX,
      y: groupY,
      width: groupWidth,
      height: groupHeight
    });

    positioned.set(groupId, { x: groupX, y: groupY });

    const contentStartY = groupY + GROUP_HEADER + GROUP_PADDING;
    const childOffsetY = contentStartY - bounds.minY;

    bounds.childIds.forEach((childId) => {
      const pos = positioned.get(childId);
      if (!pos) return;
      positioned.set(childId, {
        x: pos.x - groupX,
        y: pos.y + childOffsetY
      });
    });

    currentY += groupHeight + GROUP_SPACING;
  });

  return nodes.map((node) => {
    const pos = positioned.get(node.id) || { x: 0, y: 0 };

    const result = {
      ...node,
      position: pos
    };

    if (node.type === "group" && groupPositions.has(node.id)) {
      const gp = groupPositions.get(node.id);
      result.style = {
        ...result.style,
        width: gp.width,
        height: gp.height
      };
    }

    return result;
  });
}
