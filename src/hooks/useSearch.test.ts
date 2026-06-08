/* eslint-disable @typescript-eslint/no-explicit-any */

import { renderHook, act, waitFor } from "@testing-library/react";
import { useSearch } from "./useSearch";
import type { Node } from "@xyflow/react";

const makeNode = (id: string, label: string, subtitle: string, file: string): Node =>
  ({
    id,
    type: "default",
    data: { label, subtitle, file },
    position: { x: 0, y: 0 },
    style: {},
  }) as Node;

const nodes: Node[] = [
  makeNode("1", "processData", "FUNCTION", "app.py"),
  makeNode("2", "handleError", "METHOD", "utils.py"),
  makeNode("3", "DEFAULT_VALUE", "CONSTANT", "config.py"),
];

describe("useSearch", () => {
  it("returns all nodes when query is empty", () => {
    const { result } = renderHook(() => useSearch(nodes, "", 5));
    expect(result.current.displayedNodes).toHaveLength(3);
  });

  it("filters by label (case-insensitive)", async () => {
    const { result } = renderHook(() => useSearch(nodes, "", 5));
    act(() => result.current.setSearchQuery("process"));
    await waitFor(() => {
      const visible = result.current.displayedNodes.filter(
        (n) => (n.style as any).opacity !== 0.15
      );
      expect(visible).toHaveLength(1);
    });
  });

  it("filters by subtitle (type)", async () => {
    const { result } = renderHook(() => useSearch(nodes, "", 5));
    act(() => result.current.setSearchQuery("METHOD"));
    await waitFor(() => {
      const visible = result.current.displayedNodes.filter(
        (n) => (n.style as any).opacity !== 0.15
      );
      expect(visible).toHaveLength(1);
    });
  });

  it("filters by file path", async () => {
    const { result } = renderHook(() => useSearch(nodes, "", 5));
    act(() => result.current.setSearchQuery("config"));
    await waitFor(() => {
      const visible = result.current.displayedNodes.filter(
        (n) => (n.style as any).opacity !== 0.15
      );
      expect(visible).toHaveLength(1);
    });
  });

  it("dims non-matching nodes", async () => {
    const { result } = renderHook(() => useSearch(nodes, "", 5));
    act(() => result.current.setSearchQuery("process"));
    await waitFor(() => {
      const dimmed = result.current.displayedNodes.filter((n) => (n.style as any).opacity === 0.15);
      expect(dimmed).toHaveLength(2);
    });
  });

  it("passes through group nodes unmodified", async () => {
    const groupNode: Node = {
      id: "g1",
      type: "group",
      data: {},
      position: { x: 0, y: 0 },
      style: {},
    };
    const { result } = renderHook(() => useSearch([...nodes, groupNode], "", 5));
    act(() => result.current.setSearchQuery("process"));
    await waitFor(() => {
      const group = result.current.displayedNodes.find((n) => n.id === "g1");
      expect((group!.style as any).opacity).toBeUndefined();
    });
  });

  it("debounces query updates", async () => {
    const { result } = renderHook(() => useSearch(nodes, "", 200));
    act(() => result.current.setSearchQuery("process"));
    // Before debounce, all nodes still visible (no opacity change yet)
    expect(
      result.current.displayedNodes.filter((n) => (n.style as any).opacity !== 0.15)
    ).toHaveLength(3);
    // After debounce delay, only matching node is visible
    await waitFor(() => {
      expect(
        result.current.displayedNodes.filter((n) => (n.style as any).opacity !== 0.15)
      ).toHaveLength(1);
    });
  });
});
