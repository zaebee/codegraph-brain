/* eslint-disable @typescript-eslint/no-explicit-any */

import { useState, useCallback, useRef } from "react";
import { useReactFlow } from "@xyflow/react";
import { buildExecutionFlow } from "../flow";
import { layoutGraph } from "../layout";
import { mapNodeToFlowView } from "../utils/nodeMapper";
import { mapEdgeToFlowView } from "../utils/edgeMapper";
import type { GraphData } from "../types";
import type { Node, Edge } from "@xyflow/react";

export function useFlowNavigation(
  graphData: GraphData,
  setNodes: (nodes: Node[]) => void,
  setEdges: (edges: Edge[]) => void,
  setViewMode: (mode: string) => void,
  setFlowRootId: (id: string | null) => void,
  allowedEdgeTypes: string[] = ["CALLS", "IMPORTS", "CONTAINS"]
) {
  const depth = 3;
  const [flowRootId, setFlowRootIdState] = useState<string | null>(null);
  const flowCacheRef = useRef<Map<string, { nodes: any[]; edges: any[] }>>(new Map());
  const { fitView } = useReactFlow();

  const onNodeClick = useCallback(
    async (_event: any, node: Node) => {
      if (node.type === "group") return;

      const cacheKey = `${node.id}:${depth}:${allowedEdgeTypes.join(",")}`;
      let flow: { nodes: any[]; edges: any[] };

      if (flowCacheRef.current.has(cacheKey)) {
        flow = flowCacheRef.current.get(cacheKey)!;
      } else {
        flow = buildExecutionFlow(graphData, node.id, depth, "both", allowedEdgeTypes);
        flowCacheRef.current.set(cacheKey, flow);
      }

      const flowNodes = flow.nodes.map((n) => mapNodeToFlowView(n, { isRoot: n.id === node.id }));
      const flowEdges = flow.edges.map((e, i) => mapEdgeToFlowView(e, i));

      const { nodes: layoutedNodes } = await layoutGraph(flowNodes, flowEdges);

      setNodes(layoutedNodes);
      setEdges(flowEdges);
      setViewMode("flow");
      setFlowRootIdState(node.id);
      setFlowRootId(node.id);

      fitView({ padding: 0.15, duration: 250 });
    },
    [graphData, depth, allowedEdgeTypes, setNodes, setEdges, setViewMode, setFlowRootId, fitView]
  );

  const clearCache = useCallback(() => {
    flowCacheRef.current.clear();
  }, []);

  return { onNodeClick, flowRootId, clearCache };
}
