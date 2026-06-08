import { FallbackProps } from "react-error-boundary";

export default function ErrorFallback({ error, resetErrorBoundary }: FallbackProps) {
  return (
    <div className="error-boundary">
      <h2>Something went wrong</h2>
      <p>{(error as Error)?.message}</p>
      <button className="btn btn-back" onClick={resetErrorBoundary}>
        Try again
      </button>
    </div>
  );
}
