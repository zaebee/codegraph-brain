import { useState, useMemo, useEffect } from "react";
import type { Node } from "@xyflow/react";

interface SearchNodeData {
  label?: string;
  subtitle?: string;
  file?: string;
}

/**
 * Хук для обработки поиска с debounce и подсветкой совпадений.
 * @param {Node[]} nodes - массив узлов для фильтрации
 * @param {string} initialQuery - начальный поисковый запрос
 * @param {number} [debounceDelay=200] - задержка debounce в ms
 * @returns {{ searchQuery: string, setSearchQuery: (q: string) => void, debouncedQuery: string, displayedNodes: Node[] }}
 */
export function useSearch(nodes: Node[], initialQuery: string = "", debounceDelay: number = 200) {
  const [searchQuery, setSearchQuery] = useState(initialQuery);
  const [debouncedQuery, setDebouncedQuery] = useState(initialQuery);

  // Debounce
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQuery(searchQuery);
    }, debounceDelay);
    return () => clearTimeout(timer);
  }, [searchQuery, debounceDelay]);

  // Filter nodes with highlighting
  const displayedNodes = useMemo(() => {
    if (!debouncedQuery) return nodes;

    return nodes.map((n) => {
      if (n.type === "group") return n;

      const q = debouncedQuery.toLowerCase();
      const data = n.data as SearchNodeData | undefined;
      const match =
        (data?.label || "").toLowerCase().includes(q) ||
        (data?.subtitle || "").toLowerCase().includes(q) ||
        (data?.file || "").toLowerCase().includes(q);

      return {
        ...n,
        style: {
          ...n.style,
          opacity: match ? 1 : 0.15,
          boxShadow:
            match && debouncedQuery
              ? "0 0 12px rgba(79, 195, 247, 0.6)"
              : n.style?.boxShadow || "0 2px 8px rgba(0,0,0,0.3)",
        },
      };
    });
  }, [nodes, debouncedQuery]);

  return { searchQuery, setSearchQuery, debouncedQuery, displayedNodes };
}
