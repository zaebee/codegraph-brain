/* eslint-disable @typescript-eslint/no-explicit-any */

import { renderHook, act } from "@testing-library/react";
import { useFlowNavigation } from "./useFlowNavigation";
import type { GraphData } from "../types";

const mockFitView = vi.fn();

vi.mock("@xyflow/react", () => ({
  useReactFlow: () => ({ fitView: mockFitView }),
}));

vi.mock("../layout", () => ({
  layoutGraph: (nodes: unknown[]) => Promise.resolve(nodes),
}));

const graphData: GraphData = {
  nodes: [
    { id: "a", type: "FUNCTION", name: "funcA", file_path: "a.py", start_line: 1, end_line: 10, language: "py", namespace: "INTERNAL", ontology_class: null, domains: [], confidence_score: 1, metadata: {} },
    { id: "b", type: "FUNCTION", name: "funcB", file_path: "b.py", start_line: 1, end_line: 5, language: "py", namespace: "INTERNAL", ontology_class: null, domains: [], confidence_score: 1, metadata: {} },
    { id: "g1", type: "CLASS", name: "GroupNode", file_path: "g.py", start_line: 1, end_line: 20, language: "py", namespace: "INTERNAL", ontology_class: null, domains: [], confidence_score: 1, metadata: {} },
  ],
  edges: [
    { id: "e1", source: "a", target: "b", type: "CALLS" },
  ],
};

function setup() {
  const setNodes = vi.fn();
  const setEdges = vi.fn();
  const setViewMode = vi.fn();
  const setFlowRootId = vi.fn();
  const { result } = renderHook(() =>
    useFlowNavigation(graphData, setNodes, setEdges, setViewMode, setFlowRootId, 3)
  );
  return { result, setNodes, setEdges, setViewMode, setFlowRootId };
}

describe("useFlowNavigation", () => {
  beforeEach(() => {
    mockFitView.mockClear();
  });

  it("returns onNodeClick, flowRootId, and clearCache", () => {
    const { result } = setup();
    expect(result.current).toHaveProperty("onNodeClick");
    expect(result.current).toHaveProperty("flowRootId");
    expect(result.current).toHaveProperty("clearCache");
  });

  it("flowRootId starts as null", () => {
    const { result } = setup();
    expect(result.current.flowRootId).toBeNull();
  });

  it("onNodeClick on a group node returns early", async () => {
    const { result, setNodes, setEdges, setViewMode } = setup();
    await act(async () => {
      await result.current.onNodeClick({}, { id: "g1", type: "group" } as any);
    });
    expect(setNodes).not.toHaveBeenCalled();
    expect(setEdges).not.toHaveBeenCalled();
    expect(setViewMode).not.toHaveBeenCalled();
    expect(mockFitView).not.toHaveBeenCalled();
  });

  it("onNodeClick calls setNodes, setEdges, setViewMode, and fitView", async () => {
    const { result, setNodes, setEdges, setViewMode, setFlowRootId } = setup();
    await act(async () => {
      await result.current.onNodeClick({}, { id: "a", type: "FUNCTION" } as any);
    });
    expect(setNodes).toHaveBeenCalled();
    expect(setEdges).toHaveBeenCalled();
    expect(setViewMode).toHaveBeenCalledWith("flow");
    expect(setFlowRootId).toHaveBeenCalledWith("a");
    expect(mockFitView).toHaveBeenCalledWith({ padding: 0.15 });
  });

  it("clearCache does not throw", () => {
    const { result } = setup();
    expect(() => result.current.clearCache()).not.toThrow();
  });
});
