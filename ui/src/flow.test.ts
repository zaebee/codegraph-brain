import { buildExecutionFlow } from "./flow";
import type { GraphData } from "./types";

describe("buildExecutionFlow", () => {
  const makeGraph = (nodes: string[], edges: [string, string, string?][]): GraphData => ({
    nodes: nodes.map((id) => ({
      id,
      name: id,
      type: "FUNCTION",
      file_path: "f.py",
      start_line: 1,
      end_line: 10,
      language: "python",
      namespace: "INTERNAL",
      ontology_class: null,
      domains: [],
      confidence_score: 1,
      metadata: {},
    })),
    edges: edges.map(([source, target, type = "CALLS"]) => ({
      id: `e-${source}-${target}`,
      source,
      target,
      type,
    })),
  });

  it("returns start node with depth 0", () => {
    const graph = makeGraph(["a", "b"], [["a", "b"]]);
    const flow = buildExecutionFlow(graph, "a", 3, "outgoing");

    expect(flow.nodes.map((n) => n.id)).toContain("a");
  });

  it("follows outgoing edges within depth", () => {
    const graph = makeGraph(
      ["a", "b", "c"],
      [
        ["a", "b"],
        ["b", "c"],
      ]
    );
    const flow = buildExecutionFlow(graph, "a", 2, "outgoing");

    expect(flow.nodes.map((n) => n.id)).toEqual(expect.arrayContaining(["a", "b", "c"]));
  });

  it("respects depth limit", () => {
    const graph = makeGraph(
      ["a", "b", "c", "d"],
      [
        ["a", "b"],
        ["b", "c"],
        ["c", "d"],
      ]
    );
    const flow = buildExecutionFlow(graph, "a", 1, "outgoing");

    expect(flow.nodes.map((n) => n.id)).toContain("a");
    expect(flow.nodes.map((n) => n.id)).toContain("b");
    expect(flow.nodes.map((n) => n.id)).not.toContain("c");
  });

  it("follows incoming edges", () => {
    const graph = makeGraph(
      ["a", "b", "c"],
      [
        ["c", "b"],
        ["b", "a"],
      ]
    );
    const flow = buildExecutionFlow(graph, "a", 2, "incoming");

    expect(flow.nodes.map((n) => n.id)).toEqual(expect.arrayContaining(["a", "b", "c"]));
  });

  it("follows both directions", () => {
    const graph = makeGraph(
      ["a", "b", "c"],
      [
        ["a", "b"],
        ["c", "b"],
      ]
    );
    const flow = buildExecutionFlow(graph, "b", 2, "both");

    expect(flow.nodes.map((n) => n.id)).toEqual(expect.arrayContaining(["a", "b", "c"]));
  });

  it("skips non-traversal edge types", () => {
    const graph: GraphData = {
      nodes: [
        {
          id: "a",
          name: "a",
          type: "FUNCTION",
          file_path: "",
          start_line: 1,
          end_line: 1,
          language: "python",
          namespace: "INTERNAL",
          ontology_class: null,
          domains: [],
          confidence_score: 1,
          metadata: {},
        },
        {
          id: "b",
          name: "b",
          type: "FUNCTION",
          file_path: "",
          start_line: 1,
          end_line: 1,
          language: "python",
          namespace: "INTERNAL",
          ontology_class: null,
          domains: [],
          confidence_score: 1,
          metadata: {},
        },
      ],
      edges: [{ id: "e-a-b", source: "a", target: "b", type: "DECLARES" }],
    };

    const flow = buildExecutionFlow(graph, "a", 3, "outgoing");
    expect(flow.nodes.map((n) => n.id)).toEqual(["a"]);
  });

  it("traverses IMPORTS edges", () => {
    const graph = makeGraph(["a", "b"], [["a", "b", "IMPORTS"]]);
    const flow = buildExecutionFlow(graph, "a", 3, "outgoing");
    expect(flow.nodes.map((n) => n.id)).toEqual(expect.arrayContaining(["a", "b"]));
  });

  it("traverses CONTAINS edges", () => {
    const graph = makeGraph(["a", "b"], [["a", "b", "CONTAINS"]]);
    const flow = buildExecutionFlow(graph, "a", 3, "outgoing");
    expect(flow.nodes.map((n) => n.id)).toEqual(expect.arrayContaining(["a", "b"]));
  });

  it("handles cycles without infinite loop", () => {
    const graph = makeGraph(
      ["a", "b"],
      [
        ["a", "b"],
        ["b", "a"],
      ]
    );
    const flow = buildExecutionFlow(graph, "a", 5, "outgoing");

    expect(flow.nodes.length).toBeLessThanOrEqual(2);
  });

  it("returns empty for unknown node", () => {
    const graph = makeGraph(["a"], []);
    const flow = buildExecutionFlow(graph, "unknown", 3, "outgoing");

    expect(flow.nodes).toHaveLength(0);
  });

  it("deduplicates edges in both directions", () => {
    const graph = makeGraph(["a", "b"], [["a", "b"]]);
    const flow = buildExecutionFlow(graph, "a", 3, "both");

    const edgeKeys = flow.edges.map((e) => `${e.source}->${e.target}`);
    expect(new Set(edgeKeys).size).toBe(edgeKeys.length);
  });
});
