/* eslint-disable @typescript-eslint/no-explicit-any */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReactFlowProvider } from "@xyflow/react";
import FileContainerNode from "./FileContainerNode";

function renderNode(props: any = {}) {
  const defaultProps = {
    id: "f1",
    data: { label: "pipeline.py", nodeType: "FILE", isExpanded: true },
    selected: false,
    ...props,
  };
  return render(
    <ReactFlowProvider>
      <FileContainerNode {...defaultProps} />
    </ReactFlowProvider>,
  );
}

describe("FileContainerNode", () => {
  it("renders file name", () => {
    renderNode({ data: { label: "pipeline.py", isExpanded: false } });
    expect(screen.getByText(/pipeline.py/)).toBeInTheDocument();
  });

  it("shows collapse indicator when expanded", () => {
    renderNode({ data: { isExpanded: true } });
    expect(screen.getByText(/▼/)).toBeInTheDocument();
  });

  it("shows expand indicator when collapsed", () => {
    renderNode({ data: { isExpanded: false } });
    expect(screen.getByText(/▶/)).toBeInTheDocument();
  });
});
