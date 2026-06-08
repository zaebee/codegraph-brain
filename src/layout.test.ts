import { layoutGraph } from "./layout";

describe("layoutGraph", () => {
  const makeLeaf = (id: string, type = "FUNCTION") => ({
    id,
    type,
    data: { label: id },
    position: { x: 0, y: 0 },
  });

  const makeEdge = (source: string, target: string) => ({ id: `e-${source}-${target}`, source, target });

  it("positions all leaf nodes", async () => {
    const nodes = [makeLeaf("a"), makeLeaf("b"), makeLeaf("c")];
    const edges = [makeEdge("a", "b"), makeEdge("b", "c")];

    const result = await layoutGraph(nodes, edges);

    result.forEach((n) => {
      expect(typeof n.position.x).toBe("number");
      expect(typeof n.position.y).toBe("number");
    });
  });

  it("handles empty input", async () => {
    const result = await layoutGraph([], []);
    expect(result).toEqual([]);
  });

  it("handles nodes with no edges", async () => {
    const nodes = [makeLeaf("a"), makeLeaf("b")];
    const result = await layoutGraph(nodes, []);

    expect(result).toHaveLength(2);
    result.forEach((n) => {
      expect(typeof n.position.x).toBe("number");
    });
  });
});
