/* eslint-disable @typescript-eslint/no-explicit-any */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
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
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns all nodes when query is empty", () => {
    const { result } = renderHook(() => useSearch(nodes, "", 200));
    expect(result.current.displayedNodes).toHaveLength(3);
  });

  it("filters by label (case-insensitive)", () => {
    const { result } = renderHook(() => useSearch(nodes, "", 200));
    act(() => result.current.setSearchQuery("process"));
    act(() => vi.advanceTimersByTime(200));
    const visible = result.current.displayedNodes.filter((n) => (n.style as any).opacity !== 0.15);
    expect(visible).toHaveLength(1);
    expect((visible[0].data as any).label).toBe("processData");
  });

  it("filters by subtitle (type)", () => {
    const { result } = renderHook(() => useSearch(nodes, "", 200));
    act(() => result.current.setSearchQuery("METHOD"));
    act(() => vi.advanceTimersByTime(200));
    const visible = result.current.displayedNodes.filter((n) => (n.style as any).opacity !== 0.15);
    expect(visible).toHaveLength(1);
  });

  it("filters by file path", () => {
    const { result } = renderHook(() => useSearch(nodes, "", 200));
    act(() => result.current.setSearchQuery("config"));
    act(() => vi.advanceTimersByTime(200));
    const visible = result.current.displayedNodes.filter((n) => (n.style as any).opacity !== 0.15);
    expect(visible).toHaveLength(1);
  });

  it("dims non-matching nodes", () => {
    const { result } = renderHook(() => useSearch(nodes, "", 200));
    act(() => result.current.setSearchQuery("process"));
    act(() => vi.advanceTimersByTime(200));
    const dimmed = result.current.displayedNodes.filter((n) => (n.style as any).opacity === 0.15);
    expect(dimmed).toHaveLength(2);
  });

  it("passes through group nodes unmodified", () => {
    const groupNode: Node = {
      id: "g1",
      type: "group",
      data: {},
      position: { x: 0, y: 0 },
      style: {},
    };
    const { result } = renderHook(() => useSearch([...nodes, groupNode], "", 200));
    act(() => result.current.setSearchQuery("process"));
    act(() => vi.advanceTimersByTime(200));
    const group = result.current.displayedNodes.find((n) => n.id === "g1");
    expect((group!.style as any).opacity).toBeUndefined();
  });

  it("debounces query updates", () => {
    const { result } = renderHook(() => useSearch(nodes, "", 200));
    act(() => result.current.setSearchQuery("process"));
    // Before debounce, all nodes still visible
    expect(result.current.displayedNodes.filter((n) => (n.style as any).opacity !== 0.15)).toHaveLength(3);
    act(() => vi.advanceTimersByTime(200));
    expect(result.current.displayedNodes.filter((n) => (n.style as any).opacity !== 0.15)).toHaveLength(1);
  });
});
