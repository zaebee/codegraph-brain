/* eslint-disable @typescript-eslint/no-explicit-any */

import { renderHook, act, waitFor } from "@testing-library/react";
import { useGraphFilter } from "./useGraphFilter";

const mockFitView = vi.fn();

vi.mock("@xyflow/react", () => ({
  useReactFlow: () => ({ fitView: mockFitView }),
}));

vi.mock("../layout", () => ({
  layoutGraph: (nodes: unknown[], edges: unknown[]) => Promise.resolve({ nodes, edges }),
}));

const ALL_EDGE_TYPES = ["CALLS", "IMPORTS", "EXTENDS", "DECLARES"];

function makeNode(id: string, nodeType: string, label: string) {
  return {
    id,
    data: { nodeType, label, namespace: "INTERNAL" },
    style: {},
  };
}

describe("useGraphFilter", () => {
  beforeEach(() => {
    mockFitView.mockClear();
  });

  it("returns default state after initial render", async () => {
    const onFiltered = vi.fn();
    const { result } = renderHook(() =>
      useGraphFilter({
        viewMode: "full",
        allNodes: [],
        allEdges: [],
        parentChildrenRef: { current: new Map() } as any,
        ALL_EDGE_TYPES,
        onFiltered,
      })
    );
    await waitFor(() => expect(result.current.isLayouting).toBe(false));
    expect(result.current.showExternal).toBe(false);
    expect(result.current.activeEdgeTypes).toEqual(ALL_EDGE_TYPES);
  });

  it("does not call onFiltered when viewMode is not 'full'", async () => {
    const onFiltered = vi.fn();
    renderHook(() =>
      useGraphFilter({
        viewMode: "flow",
        allNodes: [],
        allEdges: [],
        parentChildrenRef: { current: new Map() } as any,
        ALL_EDGE_TYPES,
        onFiltered,
      })
    );
    await act(async () => {});
    expect(onFiltered).not.toHaveBeenCalled();
  });

  it("calls onFiltered with filtered data in full mode", async () => {
    const onFiltered = vi.fn();
    const nodes = [makeNode("n1", "FILE", "test.py")];
    const edges: any[] = [];
    renderHook(() =>
      useGraphFilter({
        viewMode: "full",
        allNodes: nodes,
        allEdges: edges,
        parentChildrenRef: { current: new Map() } as any,
        ALL_EDGE_TYPES,
        onFiltered,
      })
    );
    await waitFor(() => expect(onFiltered).toHaveBeenCalled());
    const [filteredNodes, filteredEdges] = onFiltered.mock.calls[0];
    expect(filteredNodes).toBeDefined();
    expect(filteredEdges).toBeDefined();
  });

  it("calls fitView after filtering", async () => {
    const onFiltered = vi.fn();
    renderHook(() =>
      useGraphFilter({
        viewMode: "full",
        allNodes: [],
        allEdges: [],
        parentChildrenRef: { current: new Map() } as any,
        ALL_EDGE_TYPES,
        onFiltered,
      })
    );
    await waitFor(() => expect(mockFitView).toHaveBeenCalled());
  });

  it("setExpandedFiles toggle works", () => {
    const onFiltered = vi.fn();
    const { result } = renderHook(() =>
      useGraphFilter({
        viewMode: "full",
        allNodes: [],
        allEdges: [],
        parentChildrenRef: { current: new Map() } as any,
        ALL_EDGE_TYPES,
        onFiltered,
      })
    );
    act(() => {
      result.current.setExpandedFiles((prev: Set<string>) => {
        const next = new Set(prev);
        next.add("file1");
        return next;
      });
    });
    expect(result.current.expandedFiles.has("file1")).toBe(true);
  });

  it("setActiveEdgeTypes toggle works", () => {
    const onFiltered = vi.fn();
    const { result } = renderHook(() =>
      useGraphFilter({
        viewMode: "full",
        allNodes: [],
        allEdges: [],
        parentChildrenRef: { current: new Map() } as any,
        ALL_EDGE_TYPES,
        onFiltered,
      })
    );
    act(() => {
      result.current.setActiveEdgeTypes((prev: string[]) =>
        prev.filter((t: string) => t !== "CALLS")
      );
    });
    expect(result.current.activeEdgeTypes).not.toContain("CALLS");
  });
});
