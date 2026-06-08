/* eslint-disable @typescript-eslint/no-explicit-any */

import { useState, useCallback, useRef } from "react";
import { useReactFlow } from "@xyflow/react";
import { buildExecutionFlow } from "../flow";
import { layoutGraph } from "../layout";
import { mapNodeToFlowView } from "../utils/nodeMapper";
import { mapEdgeToFlowView } from "../utils/edgeMapper";
import type { GraphData } from "../types";
import type { Node, Edge } from "@xyflow/react";

/**
 * Хук для навигации по flow view с кэшированием.
 * @param {GraphData} graphData - полные данные графа
 * @param {Function} setNodes - функция для обновления узлов
 * @param {Function} setEdges - функция для обновления рёбер
 * @param {Function} setViewMode - функция для переключения view mode
 * @param {Function} setFlowRootId - функция для установки root node
 * @param {number} depth - максимальная глубина трассировки
 * @returns {{ onNodeClick: Function, flowRootId: string|null }}
 */
export function useFlowNavigation(
  graphData: GraphData,
  setNodes: (nodes: Node[]) => void,
  setEdges: (edges: Edge[]) => void,
  setViewMode: (mode: string) => void,
  setFlowRootId: (id: string | null) => void,
  depth: number = 3
) {
  const [flowRootId, setFlowRootIdState] = useState<string | null>(null);
  const flowCacheRef = useRef<Map<string, { nodes: any[]; edges: any[] }>>(new Map());
  const { fitView } = useReactFlow();

  /**
   * Handle node click to navigate to flow view
   */
  const onNodeClick = useCallback(
    async (_event: any, node: Node) => {
      if (node.type === "group") return;

      const cacheKey = `${node.id}:${depth}`;
      let flow: { nodes: any[]; edges: any[] };

      // Check cache first
      if (flowCacheRef.current.has(cacheKey)) {
        flow = flowCacheRef.current.get(cacheKey)!;
      } else {
        // Build execution flow
        flow = buildExecutionFlow(graphData, node.id, depth, "both");
        flowCacheRef.current.set(cacheKey, flow);
      }

      // Map nodes and edges to ReactFlow format
      const flowNodes = flow.nodes.map((n) => mapNodeToFlowView(n, { isRoot: n.id === node.id }));
      const flowEdges = flow.edges.map((e, i) => mapEdgeToFlowView(e, i));

      // Apply layout
      const layoutedNodes = await layoutGraph(flowNodes, flowEdges);

      // Update state
      setNodes(layoutedNodes);
      setEdges(flowEdges);
      setViewMode("flow");
      setFlowRootIdState(node.id);
      setFlowRootId(node.id);

      // Fit view
      fitView({ padding: 0.15 });
    },
    [graphData, depth, setNodes, setEdges, setViewMode, setFlowRootId, fitView]
  );

  /**
   * Clear flow cache when depth changes
   */
  const clearCache = useCallback(() => {
    flowCacheRef.current.clear();
  }, []);

  return { onNodeClick, flowRootId, clearCache };
}
