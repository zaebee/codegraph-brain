import { describe, it, expect } from "vitest";
import { applyContextHighlight } from "./applyHighlight";

function makeNode(id: string, opts = {}) {
  return { id, data: { label: id }, style: { opacity: 1 }, ...opts };
}

function makeEdge(id: string, source: string, target: string) {
  return { id, source, target, style: { opacity: 0.8 } };
}

describe("applyContextHighlight", () => {
  it("returns same nodes/edges when no hovered node", () => {
    const nodes = [makeNode("a"), makeNode("b")];
    const edges = [makeEdge("e1", "a", "b")];
    const result = applyContextHighlight(nodes, edges, null);
    expect(result.nodes[0].style.opacity).toBe(1);
    expect(result.edges[0].style.opacity).toBe(0.8);
  });

  it("dims non-connected nodes", () => {
    const nodes = [makeNode("a"), makeNode("b"), makeNode("c")];
    const edges = [makeEdge("e1", "a", "b")];
    const result = applyContextHighlight(nodes, edges, "a");
    // a is hovered
    expect(result.nodes[0].style.opacity).toBe(1);
    // b is connected
    expect(result.nodes[1].style.opacity).toBe(1);
    // c is not connected
    expect(result.nodes[2].style.opacity).toBeCloseTo(0.15);
  });

  it("dims non-connected edges", () => {
    const nodes = [makeNode("a"), makeNode("b"), makeNode("c")];
    const edges = [
      makeEdge("e1", "a", "b"),
      makeEdge("e2", "b", "c"),
    ];
    const result = applyContextHighlight(nodes, edges, "a");
    // e1 connects a-b
    expect(result.edges[0].style.opacity).toBe(0.8);
    // e2 connects b-c — both not highlighted (c not connected to a)
    expect(result.edges[1].style.opacity).toBeCloseTo(0.8 * 0.05);
  });

  it("preserves existing node opacities when dimming", () => {
    const nodes = [
      makeNode("a", { style: { opacity: 0.5 } }),
      makeNode("b", { style: { opacity: 1 } }),
      makeNode("c", { style: { opacity: 0.3 } }),
    ];
    const edges = [makeEdge("e1", "a", "b")];
    const result = applyContextHighlight(nodes, edges, "a");
    // Already dimmed node (0.3) * not connected = 0.3 * 0.15
    expect(result.nodes[2].style.opacity).toBeCloseTo(0.3 * 0.15);
  });

  it("handles empty arrays", () => {
    const result = applyContextHighlight([], [], "a");
    expect(result.nodes).toEqual([]);
    expect(result.edges).toEqual([]);
  });

  it("handles hovered node not in nodes list", () => {
    const nodes = [makeNode("a"), makeNode("b")];
    const edges = [makeEdge("e1", "a", "b")];
    const result = applyContextHighlight(nodes, edges, "z");
    // z not in nodes, both a and b should be dimmed
    expect(result.nodes[0].style.opacity).toBeCloseTo(0.15);
    expect(result.nodes[1].style.opacity).toBeCloseTo(0.15);
  });

  it("keeps children visible when hovering a fileContainer", () => {
    const container = { id: "file-a", data: { label: "file-a" }, style: { opacity: 1 } };
    const child1 = { id: "child1", data: { label: "child1", groupId: "file-a" }, style: { opacity: 1 } };
    const child2 = { id: "child2", data: { label: "child2", groupId: "file-a" }, style: { opacity: 1 } };
    const unrelated = makeNode("other");
    const nodes = [container, child1, child2, unrelated];
    const result = applyContextHighlight(nodes, [], "file-a");
    expect(result.nodes[0].style.opacity).toBe(1); // container itself
    expect(result.nodes[1].style.opacity).toBe(1); // child1 stays visible
    expect(result.nodes[2].style.opacity).toBe(1); // child2 stays visible
    expect(result.nodes[3].style.opacity).toBeCloseTo(0.15); // unrelated dims
  });

  it("keeps container and siblings visible when hovering a child node", () => {
    const container = { id: "file-a", data: { label: "file-a" }, style: { opacity: 1 } };
    const child1 = { id: "child1", data: { label: "child1", groupId: "file-a" }, style: { opacity: 1 } };
    const child2 = { id: "child2", data: { label: "child2", groupId: "file-a" }, style: { opacity: 1 } };
    const unrelated = makeNode("other");
    const nodes = [container, child1, child2, unrelated];
    const result = applyContextHighlight(nodes, [], "child1");
    expect(result.nodes[0].style.opacity).toBe(1); // container stays visible
    expect(result.nodes[1].style.opacity).toBe(1); // hovered child
    expect(result.nodes[2].style.opacity).toBe(1); // sibling stays visible
    expect(result.nodes[3].style.opacity).toBeCloseTo(0.15); // unrelated dims
  });
});
