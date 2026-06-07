import { groupByFile } from "./grouping";

describe("groupByFile", () => {
  const makeNode = (id, type, filePath) => ({
    id,
    type,
    file_path: filePath,
    class: filePath,
    data: { label: id },
    position: { x: 0, y: 0 }
  });

  it("creates group nodes for each unique file path", () => {
    const nodes = [
      makeNode("a", "FUNCTION", "src/app.py"),
      makeNode("b", "FUNCTION", "src/app.py"),
      makeNode("c", "FUNCTION", "src/utils.py")
    ];

    const result = groupByFile(nodes, []);
    const groups = result.nodes.filter(n => n.type === "group");

    expect(groups).toHaveLength(2);
    expect(groups.map(g => g.id).sort()).toEqual([
      "file:src/app.py",
      "file:src/utils.py"
    ]);
  });

  it("uses filename as label", () => {
    const nodes = [makeNode("a", "FUNCTION", "src/cgis/pipeline.py")];
    const result = groupByFile(nodes, []);
    const group = result.nodes.find(n => n.type === "group");

    expect(group.data.label).toBe("pipeline.py");
    expect(group.data.fullPath).toBe("src/cgis/pipeline.py");
    expect(group.data.dir).toBe("src/cgis");
  });

  it("sets groupId on child nodes", () => {
    const nodes = [
      makeNode("a", "FUNCTION", "src/app.py"),
      makeNode("b", "METHOD", "src/app.py")
    ];

    const result = groupByFile(nodes, []);
    const childA = result.nodes.find(n => n.id === "a");
    const childB = result.nodes.find(n => n.id === "b");

    expect(childA.groupId).toBe("file:src/app.py");
    expect(childB.groupId).toBe("file:src/app.py");
  });

  it("does not set groupId on nodes without class", () => {
    const nodes = [{ id: "ext1", type: "EXTERNAL", data: {}, position: { x: 0, y: 0 } }];
    const result = groupByFile(nodes, []);

    expect(result.nodes[0].groupId).toBeUndefined();
  });

  it("preserves edges unchanged", () => {
    const edges = [{ id: "e1", source: "a", target: "b" }];
    const nodes = [makeNode("a", "FUNCTION", "f.py"), makeNode("b", "FUNCTION", "f.py")];

    const result = groupByFile(nodes, edges);
    expect(result.edges).toEqual(edges);
  });

  it("creates one group per file even with many nodes", () => {
    const nodes = Array.from({ length: 10 }, (_, i) =>
      makeNode(`n${i}`, "METHOD", "big.py")
    );

    const result = groupByFile(nodes, []);
    const groups = result.nodes.filter(n => n.type === "group");
    expect(groups).toHaveLength(1);
  });
});
