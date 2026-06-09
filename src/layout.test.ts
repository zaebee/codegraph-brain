import { layoutGraph } from "./layout";
import type { Node, Edge } from "@xyflow/react";

function makeNode(id: string, nodeType?: string): Node {
  return { id, data: { nodeType, label: id }, position: { x: 0, y: 0 }, style: {} } as Node;
}

function makeEdge(id: string, source: string, target: string, type = "CALLS"): Edge {
  return { id, source, target, data: { edgeType: type } } as Edge;
}

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

    const { nodes: laidOutNodes } = await layoutGraph(nodes, edges);

    laidOutNodes.forEach((n) => {
      expect(typeof n.position.x).toBe("number");
      expect(typeof n.position.y).toBe("number");
    });
  });

  it("handles empty input", async () => {
    const result = await layoutGraph([], []);
    expect(result.nodes).toEqual([]);
    expect(result.edges).toEqual([]);
  });

  it("handles nodes with no edges", async () => {
    const nodes = [makeLeaf("a"), makeLeaf("b")];
    const { nodes: laidOutNodes } = await layoutGraph(nodes, []);

    expect(laidOutNodes).toHaveLength(2);
    laidOutNodes.forEach((n) => {
      expect(typeof n.position.x).toBe("number");
    });
  });
});

describe("layoutGraph container wrap", () => {
  it("returns {nodes, edges} with same nodes when no expanded files", async () => {
    const nodes = [makeNode("a"), makeNode("b")];
    const edges = [makeEdge("e1", "a", "b")];
    const result = await layoutGraph(nodes, edges, new Set(), new Map());
    expect(result).toHaveProperty("nodes");
    expect(result).toHaveProperty("edges");
    expect(result.nodes).toHaveLength(2);
    expect(result.edges).toHaveLength(1);
  });

  it("wraps expanded FILE around its children", async () => {
    const fileNode = { ...makeNode("f1", "FILE"), position: { x: 0, y: 200 } };
    const child1 = { ...makeNode("c1", "FUNCTION"), position: { x: 100, y: 0 } };
    const child2 = { ...makeNode("c2", "METHOD"), position: { x: 300, y: 0 } };
    const nodes = [fileNode, child1, child2];
    const edges = [
      makeEdge("e1", "f1", "c1", "CONTAINS"),
      makeEdge("e2", "f1", "c2", "CONTAINS"),
    ];
    const expandedFiles = new Set(["f1"]);
    const parentChildren = new Map([["f1", ["c1", "c2"]]]);

    const result = await layoutGraph(nodes, edges, expandedFiles, parentChildren);
    const wrappedFile = result.nodes.find((n: Node) => n.id === "f1")!;
    const child1Pos = result.nodes.find((n: Node) => n.id === "c1")!.position;
    const child2Pos = result.nodes.find((n: Node) => n.id === "c2")!.position;

    expect(wrappedFile.position.x).toBeLessThan(Math.min(child1Pos.x, child2Pos.x));
    expect(wrappedFile.position.y).toBeLessThan(Math.min(child1Pos.y, child2Pos.y));
    expect(wrappedFile.style?.width).toBeGreaterThan(0);
    expect(wrappedFile.style?.height).toBeGreaterThan(0);

    expect(result.edges.some((e: Edge) => e.id === "e1" || e.id === "e2")).toBe(false);
  });

  it("removes CONTAINS edges for expanded FILE children only", async () => {
    const expandedFile = { ...makeNode("f1", "FILE"), position: { x: 0, y: 100 } };
    const collapsedFile = { ...makeNode("f2", "FILE"), position: { x: 500, y: 100 } };
    const childOfF1 = { ...makeNode("c1", "FUNCTION"), position: { x: 50, y: 0 } };
    const childOfF2 = { ...makeNode("c2", "FUNCTION"), position: { x: 550, y: 0 } };
    const nodes = [expandedFile, collapsedFile, childOfF1, childOfF2];
    const edges = [
      makeEdge("e1", "f1", "c1", "CONTAINS"),
      makeEdge("e2", "f2", "c2", "CONTAINS"),
    ];
    const expandedFiles = new Set(["f1"]);
    const parentChildren = new Map([
      ["f1", ["c1"]],
      ["f2", ["c2"]],
    ]);

    const result = await layoutGraph(nodes, edges, expandedFiles, parentChildren);
    expect(result.edges.find((e: Edge) => e.id === "e1")).toBeUndefined();
    expect(result.edges.find((e: Edge) => e.id === "e2")).toBeDefined();
  });

  it("does not modify FILE without children", async () => {
    const fileNode = makeNode("f1", "FILE");
    const nodes = [fileNode];
    const edges: Edge[] = [];
    const result = await layoutGraph(nodes, edges, new Set(["f1"]), new Map());
    expect(result.nodes[0].id).toBe("f1");
  });

  it("sets type to fileContainer for expanded FILE nodes", async () => {
    const fileNode = { ...makeNode("f1", "FILE"), position: { x: 0, y: 100 } };
    const child = { ...makeNode("c1", "FUNCTION"), position: { x: 50, y: 0 } };
    const nodes = [fileNode, child];
    const edges = [makeEdge("e1", "f1", "c1", "CONTAINS")];
    const expandedFiles = new Set(["f1"]);
    const parentChildren = new Map([["f1", ["c1"]]]);

    const result = await layoutGraph(nodes, edges, expandedFiles, parentChildren);
    const wrappedFile = result.nodes.find((n: Node) => n.id === "f1")!;
    expect(wrappedFile.type).toBe("fileContainer");
  });
});
