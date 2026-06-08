import { render, screen, waitFor } from "@testing-library/react";
import { ReactFlowProvider } from "@xyflow/react";
import App from "./App";

const mockGraph = {
  nodes: [
    {
      id: "n1",
      type: "FUNCTION",
      name: "testFunc",
      file_path: "test.py",
      start_line: 1,
      end_line: 5,
      language: "python",
      namespace: "INTERNAL",
      ontology_class: null,
      domains: [],
      confidence_score: 1,
      metadata: {},
    },
  ],
  edges: [],
};

beforeEach(() => {
  globalThis.fetch = vi
    .fn()
    .mockResolvedValue({ ok: true, json: () => Promise.resolve(mockGraph) }) as unknown as typeof fetch;
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function renderApp() {
  render(
    <ReactFlowProvider>
      <App />
    </ReactFlowProvider>
  );
  await waitFor(() => {
    expect(screen.getByLabelText("Collapse panel")).toBeInTheDocument();
  });
}

it("renders control panel", async () => {
  await renderApp();
  expect(screen.getByLabelText("Toggle CALLS")).toBeInTheDocument();
});

it("shows legend with all node types", async () => {
  await renderApp();
  expect(screen.getByText("Function")).toBeInTheDocument();
  expect(screen.getByText("Class")).toBeInTheDocument();
  expect(screen.getByText("Method")).toBeInTheDocument();
  expect(screen.getByText("File")).toBeInTheDocument();
});

it("shows stats panel with correct counts", async () => {
  await renderApp();
  await waitFor(() => {
    expect(screen.getByText("1 nodes")).toBeInTheDocument();
    expect(screen.getByText("0 edges")).toBeInTheDocument();
    expect(screen.getByText("0 ext")).toBeInTheDocument();
  });
});
