/* eslint-disable @typescript-eslint/no-explicit-any */

import { render, screen, fireEvent } from "@testing-library/react";
import ControlPanel from "./ControlPanel";

const mockFitView = vi.fn();

vi.mock("@xyflow/react", () => ({
  Panel: ({ children, className }: any) => <div className={className}>{children}</div>,
  useReactFlow: () => ({ fitView: mockFitView }),
}));

function renderControlPanel(props: Partial<Parameters<typeof ControlPanel>[0]> = {}) {
  const defaults = {
    onToggleExternal: vi.fn(),
    showExternal: false,
    activeEdgeTypes: ["CALLS", "IMPORTS", "EXTENDS", "DECLARES"],
    onToggleEdgeType: vi.fn(),
    ALL_EDGE_TYPES: ["CALLS", "IMPORTS", "EXTENDS", "DECLARES"],
    onExportPng: vi.fn(),
    onExportSvg: vi.fn(),
    onFit: vi.fn(),
    viewMode: "full",
    flowRootId: null,
    onBack: vi.fn(),
    onBackToRoot: vi.fn(),
    searchQuery: "",
    setSearchQuery: vi.fn(),
  };
  return render(<ControlPanel {...defaults} {...props} />);
}

describe("ControlPanel", () => {
  beforeEach(() => {
    mockFitView.mockClear();
  });

  it("renders collapse button", () => {
    renderControlPanel();
    expect(screen.getByLabelText("Collapse panel")).toBeInTheDocument();
  });

  it("toggles collapsed state", () => {
    renderControlPanel();
    fireEvent.click(screen.getByLabelText("Collapse panel"));
    expect(screen.getByLabelText("Expand panel")).toBeInTheDocument();
  });

  it("expands from collapsed state", () => {
    renderControlPanel();
    fireEvent.click(screen.getByLabelText("Collapse panel"));
    fireEvent.click(screen.getByLabelText("Expand panel"));
    expect(screen.getByLabelText("Collapse panel")).toBeInTheDocument();
  });

  it("shows search input in full mode", () => {
    renderControlPanel();
    expect(screen.getByPlaceholderText(/search/)).toBeInTheDocument();
  });

  it("calls setSearchQuery on input change", () => {
    const setSearchQuery = vi.fn();
    renderControlPanel({ setSearchQuery });
    fireEvent.change(screen.getByLabelText("Search nodes"), { target: { value: "test" } });
    expect(setSearchQuery).toHaveBeenCalledWith("test");
  });

  it("shows External toggle button", () => {
    renderControlPanel();
    expect(screen.getByLabelText("Toggle external nodes")).toBeInTheDocument();
  });

  it("calls onToggleExternal when External button clicked", () => {
    const onToggleExternal = vi.fn();
    renderControlPanel({ onToggleExternal });
    fireEvent.click(screen.getByLabelText("Toggle external nodes"));
    expect(onToggleExternal).toHaveBeenCalledWith(true);
  });

  it("shows export buttons", () => {
    renderControlPanel();
    expect(screen.getByLabelText("Export as PNG")).toBeInTheDocument();
    expect(screen.getByLabelText("Export as SVG")).toBeInTheDocument();
  });

  it("calls onExportPng when PNG button clicked", () => {
    const onExportPng = vi.fn();
    renderControlPanel({ onExportPng });
    fireEvent.click(screen.getByLabelText("Export as PNG"));
    expect(onExportPng).toHaveBeenCalled();
  });

  it("calls onExportSvg when SVG button clicked", () => {
    const onExportSvg = vi.fn();
    renderControlPanel({ onExportSvg });
    fireEvent.click(screen.getByLabelText("Export as SVG"));
    expect(onExportSvg).toHaveBeenCalled();
  });

  it("shows Back button in flow mode", () => {
    renderControlPanel({ viewMode: "flow" });
    expect(screen.getByLabelText("Back to full graph")).toBeInTheDocument();
  });

  it("calls onBack when Back button clicked in flow mode", () => {
    const onBack = vi.fn();
    renderControlPanel({ onBack, viewMode: "flow" });
    fireEvent.click(screen.getByLabelText("Back to full graph"));
    expect(onBack).toHaveBeenCalled();
  });

  it("shows Root button in flow mode when flowRootId is set", () => {
    renderControlPanel({ viewMode: "flow", flowRootId: "root-1" });
    expect(screen.getByLabelText("Back to root node")).toBeInTheDocument();
  });

  it("hides search input in flow mode", () => {
    renderControlPanel({ viewMode: "flow" });
    expect(screen.queryByPlaceholderText(/Search nodes/)).not.toBeInTheDocument();
  });

  it("calls fitView when Fit button clicked", () => {
    renderControlPanel();
    fireEvent.click(screen.getByLabelText("Zoom to fit"));
    expect(mockFitView).toHaveBeenCalledWith({ padding: 0.15, duration: 250 });
  });

  it("renders edge type toggle buttons", () => {
    renderControlPanel();
    expect(screen.getByLabelText("Toggle CALLS")).toBeInTheDocument();
    expect(screen.getByLabelText("Toggle IMPORTS")).toBeInTheDocument();
    expect(screen.getByLabelText("Toggle DECLARES")).toBeInTheDocument();
  });

  it("calls onToggleEdgeType when edge type button clicked", () => {
    const onToggleEdgeType = vi.fn();
    renderControlPanel({ onToggleEdgeType });
    fireEvent.click(screen.getByLabelText("Toggle IMPORTS"));
    expect(onToggleEdgeType).toHaveBeenCalledWith("IMPORTS");
  });
});
