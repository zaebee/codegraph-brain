import { render, screen } from '@testing-library/react';
import App from './App';

test('renders graph debugger', () => {
  render(<App />);
  expect(screen.getByText(/Depth/i)).toBeInTheDocument();
});
