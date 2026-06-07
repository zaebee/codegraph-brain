import { render, screen, waitFor } from '@testing-library/react';
import { ReactFlowProvider } from '@xyflow/react';
import App from './App';

const mockGraph = {
  nodes: [
    { id: "n1", type: "FUNCTION", name: "testFunc", file_path: "test.py", start_line: 1, end_line: 5, language: "python", ontology_class: null, domains: [], confidence_score: 1, metadata: {} }
  ],
  edges: []
};

beforeEach(() => {
  globalThis.fetch = vi.fn(() =>
    Promise.resolve({ json: () => Promise.resolve(mockGraph) })
  );
});

afterEach(() => {
  vi.restoreAllMocks();
});

test('renders graph debugger', async () => {
  render(
    <ReactFlowProvider>
      <App />
    </ReactFlowProvider>
  );
  await waitFor(() => {
    expect(screen.getByText(/Depth/i)).toBeInTheDocument();
  });
});
