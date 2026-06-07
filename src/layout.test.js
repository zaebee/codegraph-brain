import { layoutGraph } from "./layout";

describe("layoutGraph", () => {
  const makeLeaf = (id, type = "FUNCTION") => ({
    id,
    type,
    data: { label: id },
    position: { x: 0, y: 0 }
  });

  const makeEdge = (source, target) => ({ id: `e-${source}-${target}`, source, target });

  it("positions all leaf nodes", () => {
    const nodes = [makeLeaf("a"), makeLeaf("b"), makeLeaf("c")];
    const edges = [makeEdge("a", "b"), makeEdge("b", "c")];

    const result = layoutGraph(nodes, edges);

    result.forEach(n => {
      expect(typeof n.position.x).toBe("number");
      expect(typeof n.position.y).toBe("number");
    });
  });

  it("creates group nodes from groupId", () => {
    const nodes = [
      { id: "g1", type: "group", data: {}, position: { x: 0, y: 0 }, style: {} },
      makeLeaf("a"),
      { ...makeLeaf("b"), groupId: "g1" }
    ];

    const result = layoutGraph(nodes, []);
    const group = result.find(n => n.id === "g1");

    expect(group.style.width).toBeGreaterThan(0);
    expect(group.style.height).toBeGreaterThan(0);
  });

  it("positions children within group bounds", () => {
    const nodes = [
      { id: "g1", type: "group", data: {}, position: { x: 0, y: 0 }, style: {} },
      { ...makeLeaf("a"), groupId: "g1" },
      { ...makeLeaf("b"), groupId: "g1" }
    ];

    const result = layoutGraph(nodes, [makeEdge("a", "b")]);
    const group = result.find(n => n.id === "g1");
    const childA = result.find(n => n.id === "a");
    const childB = result.find(n => n.id === "b");

    const gx = group.position.x;
    const gy = group.position.y;
    const gw = group.style.width;
    const gh = group.style.height;

    expect(childA.position.x).toBeGreaterThanOrEqual(gx);
    expect(childA.position.x + 160).toBeLessThanOrEqual(gx + gw);
    expect(childB.position.x).toBeGreaterThanOrEqual(gx);
    expect(childB.position.x + 160).toBeLessThanOrEqual(gx + gw);
  });

  it("does not overlap groups", () => {
    const nodes = [
      { id: "g1", type: "group", data: {}, position: { x: 0, y: 0 }, style: {} },
      { id: "g2", type: "group", data: {}, position: { x: 0, y: 0 }, style: {} },
      { ...makeLeaf("a"), groupId: "g1" },
      { ...makeLeaf("b"), groupId: "g2" }
    ];

    const result = layoutGraph(nodes, []);
    const g1 = result.find(n => n.id === "g1");
    const g2 = result.find(n => n.id === "g2");

    const g1Bottom = g1.position.y + g1.style.height;
    const g1Right = g1.position.x + g1.style.width;
    const g2Bottom = g2.position.y + g2.style.height;
    const g2Right = g2.position.x + g2.style.width;

    const horizOverlap = g1.position.x < g2Right && g1Right > g2.position.x;
    const vertOverlap = g1.position.y < g2Bottom && g1Bottom > g2.position.y;

    expect(horizOverlap && vertOverlap).toBe(false);
  });

  it("positions CLASS nodes centered in group", () => {
    const nodes = [
      { id: "g1", type: "group", data: {}, position: { x: 0, y: 0 }, style: {} },
      { id: "cls", type: "CLASS", data: {}, position: { x: 0, y: 0 }, groupId: "g1" },
      { ...makeLeaf("a"), groupId: "g1" }
    ];

    const result = layoutGraph(nodes, []);
    const cls = result.find(n => n.id === "cls");
    const group = result.find(n => n.id === "g1");

    expect(cls.position.x).toBeGreaterThan(group.position.x - 50);
    expect(cls.position.x).toBeLessThan(group.position.x + group.style.width);
  });

  it("handles empty input", () => {
    const result = layoutGraph([], []);
    expect(result).toEqual([]);
  });

  it("handles nodes with no edges", () => {
    const nodes = [makeLeaf("a"), makeLeaf("b")];
    const result = layoutGraph(nodes, []);

    expect(result).toHaveLength(2);
    result.forEach(n => {
      expect(typeof n.position.x).toBe("number");
    });
  });
});
