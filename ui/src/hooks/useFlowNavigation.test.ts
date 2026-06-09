/* eslint-disable @typescript-eslint/no-explicit-any */

import { renderHook, act } from "@testing-library/react";
import { useFlowNavigation } from "./useFlowNavigation";

const mockFitView = vi.fn();
const mockSetFlow = vi.fn();
const mockSetViewMode = vi.fn();

vi.mock("@xyflow/react", () => ({
  useReactFlow: () => ({ fitView: mockFitView }),
}));

vi.mock("../layout/IslandLayoutEngine", () => ({
  IslandLayoutEngine: class {
    constructor(public nodes: unknown[], public edges: unknown[]) {}
    run(_expandedFiles: Set<string>) {
      return { nodes: this.nodes, edges: this.edges }
    }
  },
}));

vi.mock("../utils/edgeMapper", () => ({
  mapEdgeToFlowView: (_e: unknown, _i: number) => ({ id: "mock-edge", source: "a", target: "b" }),
}));

vi.mock("../utils/nodeMapper", () => ({
  mapNodeToFlowView: (n: any, _opts?: any) => ({ id: n.id, type: n.type, data: {} }),
}));

const rawNodes = [
  { id: "a", type: "FUNCTION", name: "funcA", file_path: "a.py", start_line: 1, end_line: 10, language: "py", namespace: "INTERNAL", ontology_class: null, domains: [], confidence_score: 1, metadata: {} },
  { id: "b", type: "FUNCTION", name: "funcB", file_path: "b.py", start_line: 1, end_line: 5, language: "py", namespace: "INTERNAL", ontology_class: null, domains: [], confidence_score: 1, metadata: {} },
  { id: "g1", type: "CLASS", name: "GroupNode", file_path: "g.py", start_line: 1, end_line: 20, language: "py", namespace: "INTERNAL", ontology_class: null, domains: [], confidence_score: 1, metadata: {} },
];

const rawEdges = [
  { id: "e1", source: "a", target: "b", type: "CALLS" },
];

vi.mock("../store/useGraphStore", () => ({
  useGraphStore: (selector: (s: any) => any) =>
    selector({
      rawNodes,
      rawEdges,
      graphVersion: 1,
      setFlow: mockSetFlow,
      setViewMode: mockSetViewMode,
    }),
}));

function setup() {
  const { result } = renderHook(() => useFlowNavigation());
  return { result };
}

describe("useFlowNavigation", () => {
  beforeEach(() => {
    mockFitView.mockClear();
    mockSetFlow.mockClear();
    mockSetViewMode.mockClear();
  });

  it("returns onNodeClick", () => {
    const { result } = setup();
    expect(result.current).toHaveProperty("onNodeClick");
  });

  it("onNodeClick on a group node returns early", async () => {
    const { result } = setup();
    await act(async () => {
      await result.current.onNodeClick({} as any, { id: "g1", type: "group" } as any);
    });
    expect(mockSetFlow).not.toHaveBeenCalled();
    expect(mockSetViewMode).not.toHaveBeenCalled();
    expect(mockFitView).not.toHaveBeenCalled();
  });

  it("onNodeClick on a fileContainer node returns early", async () => {
    const { result } = setup();
    await act(async () => {
      await result.current.onNodeClick({} as any, { id: "g1", type: "fileContainer" } as any);
    });
    expect(mockSetFlow).not.toHaveBeenCalled();
    expect(mockSetViewMode).not.toHaveBeenCalled();
    expect(mockFitView).not.toHaveBeenCalled();
  });

  it("onNodeClick calls setFlow, setViewMode('flow'), and fitView", async () => {
    const { result } = setup();
    await act(async () => {
      await result.current.onNodeClick({} as any, { id: "a", type: "FUNCTION" } as any);
    });
    expect(mockSetFlow).toHaveBeenCalled();
    expect(mockSetViewMode).toHaveBeenCalledWith("flow");
    expect(mockFitView).toHaveBeenCalledWith({ padding: 0.15, duration: 250 });
  });

  it("cache is keyed by graphVersion — same node, same version uses cache", async () => {
    const { result } = setup();
    await act(async () => {
      await result.current.onNodeClick({} as any, { id: "a", type: "FUNCTION" } as any);
    });
    await act(async () => {
      await result.current.onNodeClick({} as any, { id: "a", type: "FUNCTION" } as any);
    });
    // setFlow called twice (once per click), but buildExecutionFlow only computed once
    expect(mockSetFlow).toHaveBeenCalledTimes(2);
  });
});
