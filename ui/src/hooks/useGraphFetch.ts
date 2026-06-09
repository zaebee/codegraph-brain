import { useState, useEffect, useCallback } from "react";
import type { GraphData } from "../types";

/**
 * Hook для загрузки graph.json с обработкой ошибок и loading state.
 * @param {string} url - URL для загрузки графа (по умолчанию '/graph.json')
 * @returns {{ graph: GraphData | null, loading: boolean, error: Error | null, refetch: () => Promise<GraphData | null> }}
 */
export function useGraphFetch(url: string = import.meta.env.VITE_GRAPH_DATA_URL ?? "/graph.json") {
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchGraph = useCallback(
    async (fetchUrl: string = url): Promise<GraphData | null> => {
      setLoading(true);
      setError(null);
      try {
        const response = await fetch(fetchUrl);
        if (!response.ok) {
          throw new Error(`Failed to load graph: ${response.status} ${response.statusText}`);
        }
        const data = await response.json();
        setGraph(data);
        return data;
      } catch (err) {
        console.error("Failed to load graph:", err);
        setError(err as Error);
        return null;
      } finally {
        setLoading(false);
      }
    },
    [url]
  );

  useEffect(() => {
    fetchGraph();
  }, [fetchGraph]);

  return { graph, loading, error, refetch: fetchGraph };
}
