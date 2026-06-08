/* eslint-disable @typescript-eslint/no-explicit-any */

import { describe, it, expect } from "vitest";
import { mapNodeToReactFlow, mapNodeToFlowView } from "./nodeMapper";
import type { GraphNode } from "../types";

const mockNode: GraphNode = {
  id: "test.func",
  type: "FUNCTION",
  name: "func",
  file_path: "test.py",
  start_line: 1,
  end_line: 10,
  language: "python",
  ontology_class: null,
  domains: [],
  confidence_score: 1,
  metadata: {},
};

describe("mapNodeToReactFlow", () => {
  it("maps id and label", () => {
    const result = mapNodeToReactFlow(mockNode);
    expect(result.id).toBe("test.func");
    expect(result.data.label).toBe("func");
  });

  it("sets nodeType in data", () => {
    const result = mapNodeToReactFlow(mockNode);
    expect(result.data.nodeType).toBe("FUNCTION");
  });

  it("uses NODE_WIDTH for style width", () => {
    const result = mapNodeToReactFlow(mockNode);
    expect(result.style!.width).toBe(160);
  });

  it("sets groupKey as class prop", () => {
    const result = mapNodeToReactFlow(mockNode, { groupKey: "test.py" });
    expect((result as any).class).toBe("test.py");
  });

  it("omits class prop when no groupKey", () => {
    const result = mapNodeToReactFlow(mockNode);
    expect((result as any).class).toBeUndefined();
  });

  it("reduces opacity for EXTERNAL type", () => {
    const ext: GraphNode = { ...mockNode, type: "EXTERNAL" };
    const result = mapNodeToReactFlow(ext);
    expect(result.style!.opacity).toBe(0.6);
  });
});

describe("mapNodeToFlowView", () => {
  it("maps id and label", () => {
    const result = mapNodeToFlowView(mockNode);
    expect(result.id).toBe("test.func");
    expect(result.data.label).toBe("func");
  });

  it("increases fontWeight for root node", () => {
    const root = mapNodeToFlowView(mockNode, { isRoot: true });
    expect(root.style!.fontWeight).toBe(700);

    const nonRoot = mapNodeToFlowView(mockNode);
    expect(nonRoot.style!.fontWeight).toBe(500);
  });

  it("reduces opacity for EXTERNAL type", () => {
    const ext: GraphNode = { ...mockNode, type: "EXTERNAL" };
    const result = mapNodeToFlowView(ext);
    expect(result.style!.opacity).toBe(0.5);
  });
});
