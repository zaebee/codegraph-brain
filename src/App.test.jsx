import { render, screen } from '@testing-library/react';
import { ReactFlowProvider } from '@xyflow/react';
import App from './App';

test('renders graph debugger', () => {
  render(
    <ReactFlowProvider>
      <App />
    </ReactFlowProvider>
  );
  expect(screen.getByText(/Depth/i)).toBeInTheDocument();
});
