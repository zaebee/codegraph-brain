
import { mapEdgeToReactFlow, mapEdgeToFlowView } from "./edgeMapper";
import type { GraphEdge, GraphNode } from "../types";

const mockEdge: GraphEdge = {
  id: "e1",
  source: "a",
  target: "b",
  type: "CALLS",
};

describe("mapEdgeToReactFlow", () => {
  it("maps edge with default styling", () => {
    const result = mapEdgeToReactFlow(mockEdge, 0);
    expect(result.id).toBe("e-0");
    expect(result.source).toBe("a");
    expect(result.target).toBe("b");
    expect(result.style!.stroke).toBe("#4fc3f7");
    expect(result.animated).toBe(false);
  });

  it("uses dim styling when target is external", () => {
    const enrichedNodes: GraphNode[] = [
      {
        id: "a",
        type: "FUNCTION",
        name: "a",
        file_path: "f.py",
        start_line: 1,
        end_line: 2,
        language: "py",
        ontology_class: null,
        domains: [],
        confidence_score: 1,
        metadata: {},
      },
      {
        id: "b",
        type: "EXTERNAL",
        name: "b",
        file_path: "",
        start_line: 0,
        end_line: 0,
        language: "",
        ontology_class: null,
        domains: [],
        confidence_score: 0,
        metadata: {},
      },
    ];
    const result = mapEdgeToReactFlow(mockEdge, 0, { enrichedNodes });
    expect(result.style!.stroke).toBe("#546e7a");
    expect(result.style!.opacity).toBe(0.4);
  });

  it("uses dim styling when source is external", () => {
    const enrichedNodes: GraphNode[] = [
      {
        id: "a",
        type: "FUNCTION",
        name: "a",
        file_path: "f.py",
        start_line: 1,
        end_line: 2,
        language: "py",
        ontology_class: null,
        domains: [],
        confidence_score: 1,
        metadata: {},
      },
      {
        id: "b",
        type: "EXTERNAL",
        name: "b",
        file_path: "",
        start_line: 0,
        end_line: 0,
        language: "",
        ontology_class: null,
        domains: [],
        confidence_score: 0,
        metadata: {},
      },
    ];
    const result = mapEdgeToReactFlow({ ...mockEdge, source: "b", target: "a" }, 0, {
      enrichedNodes,
    });
    expect(result.style!.stroke).toBe("#546e7a");
  });
});

describe("mapEdgeToFlowView", () => {
  it("maps edge with flow styling", () => {
    const result = mapEdgeToFlowView(mockEdge, 0);
    expect(result.id).toBe("flow-0");
    expect(result.source).toBe("a");
    expect(result.target).toBe("b");
    expect(result.style!.stroke).toBe("#ff9800");
    expect(result.animated).toBe(true);
  });
});
