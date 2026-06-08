/* eslint-disable @typescript-eslint/no-explicit-any */

import { render, screen, fireEvent } from "@testing-library/react";
import ControlPanel from "./ControlPanel";
import sharedStyles from "../shared.module.css";

const mockFitView = vi.fn();

vi.mock("@xyflow/react", () => ({
  Panel: ({ children, className }: any) => <div className={className}>{children}</div>,
  useReactFlow: () => ({ fitView: mockFitView }),
}));

function renderControlPanel(props: Partial<Parameters<typeof ControlPanel>[0]> = {}) {
  const defaults = {
    onDepthChange: vi.fn(),
    depth: 3,
    onToggleExternal: vi.fn(),
    showExternal: false,
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

  it("renders Controls title", () => {
    renderControlPanel();
    expect(screen.getByText("Controls")).toBeInTheDocument();
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
    expect(screen.getByText("Controls")).toBeInTheDocument();
  });

  it("shows depth slider in full mode", () => {
    renderControlPanel();
    const slider = screen.getByLabelText("Flow depth");
    expect(slider).toBeInTheDocument();
    expect(slider).toHaveValue("3");
  });

  it("calls onDepthChange when depth slider changes", () => {
    const onDepthChange = vi.fn();
    renderControlPanel({ onDepthChange });
    const slider = screen.getByLabelText("Flow depth");
    fireEvent.change(slider, { target: { value: "5" } });
    expect(onDepthChange).toHaveBeenCalledWith(5);
  });

  it("calls onDepthChange via preset buttons", () => {
    const onDepthChange = vi.fn();
    renderControlPanel({ onDepthChange, depth: 1 });
    fireEvent.click(screen.getByText("2: Near"));
    expect(onDepthChange).toHaveBeenCalledWith(2);
  });

  it("shows active class on current depth preset", () => {
    renderControlPanel({ depth: 3 });
    const presets = screen.getAllByText(/^\d+:/);
    expect(presets[2]).toHaveClass(sharedStyles.active);
  });

  it("shows search input in full mode", () => {
    renderControlPanel();
    expect(screen.getByPlaceholderText(/Search nodes/)).toBeInTheDocument();
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
    expect(mockFitView).toHaveBeenCalledWith({ padding: 0.15 });
  });
});
