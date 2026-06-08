
import { mapEdgeToReactFlow, mapEdgeToFlowView } from "./edgeMapper";
import type { GraphEdge } from "../types";

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

  it("colors CALLS edge blue", () => {
    const result = mapEdgeToReactFlow(mockEdge, 0);
    expect(result.style!.stroke).toBe("#4fc3f7");
  });

  it("colors IMPORTS edge green", () => {
    const result = mapEdgeToReactFlow({ ...mockEdge, type: "IMPORTS" }, 0);
    expect(result.style!.stroke).toBe("#66bb6a");
  });

  it("colors EXTENDS edge purple", () => {
    const result = mapEdgeToReactFlow({ ...mockEdge, type: "EXTENDS" }, 0);
    expect(result.style!.stroke).toBe("#ce93d8");
  });

  it("colors CONTAINS edge amber", () => {
    const result = mapEdgeToReactFlow({ ...mockEdge, type: "CONTAINS" }, 0);
    expect(result.style!.stroke).toBe("#ff9800");
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
