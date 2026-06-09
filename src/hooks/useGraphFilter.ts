/* eslint-disable @typescript-eslint/no-explicit-any */

import { useEffect, useRef, useState, type RefObject, type Dispatch, type SetStateAction } from "react";
import { useReactFlow } from "@xyflow/react";
import { getCollapsedView } from "../collapse";
import { layoutGraph } from "../layout";
import { aggregateEdges } from "../utils/aggregateEdges";

interface UseGraphFilterParams {
  viewMode: string;
  allNodes: any[];
  allEdges: any[];
  parentChildrenRef: RefObject<Map<string, string[]>>;
  ALL_EDGE_TYPES: string[];
  onFiltered: (nodes: any[], edges: any[]) => void;
}

interface UseGraphFilterResult {
  showExternal: boolean;
  expandedFiles: Set<string>;
  activeEdgeTypes: string[];
  isLayouting: boolean;
  setShowExternal: Dispatch<SetStateAction<boolean>>;
  setExpandedFiles: Dispatch<SetStateAction<Set<string>>>;
  setActiveEdgeTypes: Dispatch<SetStateAction<string[]>>;
}

export function useGraphFilter({
  viewMode,
  allNodes,
  allEdges,
  parentChildrenRef,
  ALL_EDGE_TYPES,
  onFiltered,
}: UseGraphFilterParams): UseGraphFilterResult {
  const { fitView } = useReactFlow();
  const [showExternal, setShowExternal] = useState(false);
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set());
  const [activeEdgeTypes, setActiveEdgeTypes] = useState<string[]>([...ALL_EDGE_TYPES]);
  const [isLayouting, setIsLayouting] = useState(false);
  const generationRef = useRef(0);

  useEffect(() => {
    const gen = ++generationRef.current;
    async function applyFilters() {
      if (viewMode !== "full") return;

      const collapsed = getCollapsedView(allNodes, allEdges, expandedFiles, parentChildrenRef.current);

      const typeFilteredEdges = collapsed.edges.filter((e: any) => {
        if (!e.data?.edgeType) return false;
        if (e.data.edgeType === "CONTAINS") return true;
        return activeEdgeTypes.includes(e.data.edgeType);
      });

      const aggregatedEdges = aggregateEdges(typeFilteredEdges);

      const externalFilteredNodes = showExternal
        ? collapsed.nodes
        : collapsed.nodes.filter(
            (n: any) => !n.data?.namespace || n.data?.namespace === "INTERNAL"
          );

      const filteredNodeIds = new Set(externalFilteredNodes.map((n: any) => n.id));
      const externalFilteredEdges = aggregatedEdges.filter(
        (e: any) => filteredNodeIds.has(e.source) && filteredNodeIds.has(e.target)
      );

      const labeledNodes = externalFilteredNodes.map((n: any) => {
        if (n.data?.nodeType === "FILE") {
          const indicator = expandedFiles.has(n.id) ? "▼ " : "▶ ";
          return {
            ...n,
            data: {
              ...n.data,
              label: indicator + n.data.label.replace(/^[▶▼]\s/, ""),
            },
          };
        }
        return n;
      });

      setIsLayouting(true);
      const { nodes: reLayouted, edges: layoutedEdges } = await layoutGraph(
        labeledNodes, externalFilteredEdges, expandedFiles, parentChildrenRef.current
      );
      if (generationRef.current !== gen) return;
      onFiltered(reLayouted, layoutedEdges);
      setIsLayouting(false);
      fitView({ padding: 0.15, duration: 250 });
    }
    applyFilters();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showExternal, allNodes, allEdges, viewMode, expandedFiles, activeEdgeTypes]);

  return {
    showExternal,
    expandedFiles,
    activeEdgeTypes,
    isLayouting,
    setShowExternal,
    setExpandedFiles,
    setActiveEdgeTypes,
  };
}
