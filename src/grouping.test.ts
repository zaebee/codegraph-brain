/* eslint-disable @typescript-eslint/no-explicit-any */

import { groupByFile } from "./grouping";

const makeNode = (id: string, type: string, filePath: string) => ({
  id,
  type,
  file_path: filePath,
  class: filePath,
  data: { label: id },
  position: { x: 0, y: 0 },
});

describe("groupByFile", () => {
  it("creates group nodes for each unique file path", () => {
    const nodes = [
      makeNode("a", "FUNCTION", "src/app.py"),
      makeNode("b", "FUNCTION", "src/app.py"),
      makeNode("c", "FUNCTION", "src/utils.py"),
    ];

    const result = groupByFile(nodes, []);
    const groups = result.nodes.filter((n) => n.type === "group");

    expect(groups).toHaveLength(2);
    expect(groups.map((g) => g.id).sort()).toEqual(["file:src/app.py", "file:src/utils.py"]);
  });

  it("uses filename as label", () => {
    const nodes = [makeNode("a", "FUNCTION", "src/cgis/pipeline.py")];
    const result = groupByFile(nodes, []);
    const group = result.nodes.find((n) => n.type === "group")!;

    expect(group.data.label).toBe("pipeline.py");
    expect((group.data as any).fullPath).toBe("src/cgis/pipeline.py");
    expect((group.data as any).dir).toBe("src/cgis");
  });

  it("sets groupId on child nodes", () => {
    const nodes = [makeNode("a", "FUNCTION", "src/app.py"), makeNode("b", "METHOD", "src/app.py")];

    const result = groupByFile(nodes, []);
    const childA = result.nodes.find((n) => n.id === "a")!;
    const childB = result.nodes.find((n) => n.id === "b")!;

    expect((childA as any).groupId).toBe("file:src/app.py");
    expect((childB as any).groupId).toBe("file:src/app.py");
  });

  it("does not set groupId on nodes without class", () => {
    const nodes: any[] = [{ id: "orphan", type: "FILE", data: {}, position: { x: 0, y: 0 } }];
    const result = groupByFile(nodes, []);

    expect((result.nodes[0] as any).groupId).toBeUndefined();
  });

  it("preserves edges unchanged", () => {
    const edges = [{ id: "e1", source: "a", target: "b" }];
    const nodes = [makeNode("a", "FUNCTION", "f.py"), makeNode("b", "FUNCTION", "f.py")];

    const result = groupByFile(nodes, edges);
    expect(result.edges).toEqual(edges);
  });

  it("creates one group per file even with many nodes", () => {
    const nodes = Array.from({ length: 10 }, (_, i) => makeNode(`n${i}`, "METHOD", "big.py"));

    const result = groupByFile(nodes, []);
    const groups = result.nodes.filter((n) => n.type === "group");
    expect(groups).toHaveLength(1);
  });
});
