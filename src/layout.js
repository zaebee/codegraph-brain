import dagre from "dagre";

const NODE_WIDTH = 160;
const NODE_HEIGHT = 50;
const GROUP_PADDING = 40;
const GROUP_HEADER = 50;
const GROUP_SPACING = 30;

export function layoutGraph(nodes, edges) {
  const groupNodes = nodes.filter(n => n.type === "group");
  const classNodes = nodes.filter(n => n.type === "CLASS");
  const leafNodes = nodes.filter(n =>
    n.type !== "group" && n.type !== "CLASS"
  );

  const childrenByGroup = new Map();
  nodes.forEach((node) => {
    if (node.groupId) {
      if (!childrenByGroup.has(node.groupId)) {
        childrenByGroup.set(node.groupId, []);
      }
      childrenByGroup.get(node.groupId).push(node.id);
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

  const groupBoxes = [];

  childrenByGroup.forEach((childIds, groupId) => {
    const leafChildIds = childIds.filter(id =>
      !classNodes.some(cn => cn.id === id)
    );

    if (leafChildIds.length === 0) return;

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;

    leafChildIds.forEach((childId) => {
      const pos = positioned.get(childId);
      if (!pos) return;
      minX = Math.min(minX, pos.x);
      maxX = Math.max(maxX, pos.x + NODE_WIDTH);
      minY = Math.min(minY, pos.y);
      maxY = Math.max(maxY, pos.y + NODE_HEIGHT);
    });

    if (minX === Infinity) return;

    const width = maxX - minX + GROUP_PADDING * 2;
    const height = maxY - minY + GROUP_PADDING * 2 + GROUP_HEADER;

    groupBoxes.push({
      groupId,
      childIds,
      leafChildIds,
      origX: minX - GROUP_PADDING,
      origY: minY - GROUP_PADDING - GROUP_HEADER,
      width,
      height
    });
  });

  groupBoxes.sort((a, b) => a.origY - b.origY);

  const placed = [];
  const groupOffsets = new Map();

  for (const box of groupBoxes) {
    let y = box.origY;

    let hasOverlap = true;
    while (hasOverlap) {
      hasOverlap = false;
      for (const p of placed) {
        const horizOverlap =
          box.origX < p.x + p.width && box.origX + box.width > p.x;
        const vertOverlap = y < p.y + p.height && y + box.height > p.y;

        if (horizOverlap && vertOverlap) {
          y = p.y + p.height + GROUP_SPACING;
          hasOverlap = true;
          break;
        }
      }
    }

    const dy = y - box.origY;
    groupOffsets.set(box.groupId, { dx: 0, dy });

    placed.push({
      x: box.origX,
      y,
      width: box.width,
      height: box.height
    });
  }

  const groupPositions = new Map();

  groupBoxes.forEach((box) => {
    const offset = groupOffsets.get(box.groupId) || { dx: 0, dy: 0 };
    const groupX = box.origX + offset.dx;
    const groupY = box.origY + offset.dy;

    groupPositions.set(box.groupId, {
      x: groupX,
      y: groupY,
      width: box.width,
      height: box.height
    });

    box.leafChildIds.forEach((childId) => {
      const pos = positioned.get(childId);
      if (!pos) return;
      positioned.set(childId, {
        x: pos.x,
        y: pos.y + offset.dy
      });
    });

    const classChildIds = box.childIds.filter(id =>
      classNodes.some(cn => cn.id === id)
    );

    classChildIds.forEach((classId) => {
      positioned.set(classId, {
        x: groupX + box.width / 2 - NODE_WIDTH / 2,
        y: groupY + GROUP_HEADER / 2 - NODE_HEIGHT / 2
      });
    });
  });

  return nodes.map((node) => {
    let pos = positioned.get(node.id) || { x: 0, y: 0 };

    if (node.type === "group" && groupPositions.has(node.id)) {
      const gp = groupPositions.get(node.id);
      pos = { x: gp.x, y: gp.y };
      return {
        ...node,
        position: pos,
        style: {
          ...node.style,
          width: gp.width,
          height: gp.height
        }
      };
    }

    return {
      ...node,
      position: pos
    };
  });
}
