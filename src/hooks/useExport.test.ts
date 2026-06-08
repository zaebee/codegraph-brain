import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useExport } from "./useExport";

vi.mock("html-to-image", () => ({
  toPng: vi.fn().mockResolvedValue("data:image/png;base64,abc"),
  toSvg: vi.fn().mockResolvedValue("data:image/svg+xml;base64,xyz"),
}));

describe("useExport", () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div class="react-flow__viewport">
        <div class="react-flow__minimap" />
        <div class="react-flow__controls" />
        <div class="graph-content" />
      </div>
    `;
  });

  it("calls download callback with PNG data on exportPng", async () => {
    const download = vi.fn();
    const { result } = renderHook(() => useExport(download));

    await result.current.exportPng();

    expect(download).toHaveBeenCalledWith("data:image/png;base64,abc", "graph.png");
  });

  it("calls download callback with SVG data on exportSvg", async () => {
    const download = vi.fn();
    const { result } = renderHook(() => useExport(download));

    await result.current.exportSvg();

    expect(download).toHaveBeenCalledWith("data:image/svg+xml;base64,xyz", "graph.svg");
  });

  it("does nothing when viewport is missing", async () => {
    document.body.innerHTML = "";
    const download = vi.fn();
    const { result } = renderHook(() => useExport(download));

    await result.current.exportPng();
    expect(download).not.toHaveBeenCalled();
  });
});
