/* eslint-disable @typescript-eslint/no-explicit-any */

import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { ReactFlow, Background, Controls, MiniMap, useReactFlow, Panel } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { ErrorBoundary, type FallbackProps } from "react-error-boundary";
import appStyles from "./App.module.css";
import sharedStyles from "./shared.module.css";

import { layoutGraph } from "./layout";
import { groupByFile, extractGroupKey } from "./grouping";
import GroupNode from "./GroupNode";
import { filterValidEdges } from "./utils";
import { mapNodeToReactFlow } from "./utils/nodeMapper";
import { mapEdgeToReactFlow } from "./utils/edgeMapper";

import ControlPanel from "./components/ControlPanel";
import StatsPanel from "./components/StatsPanel";
import NodeTooltip from "./components/NodeTooltip";
import LegendPanel from "./components/LegendPanel";
import LoadingOverlay from "./components/LoadingOverlay";

import { useGraphFetch } from "./hooks/useGraphFetch";
import { useSearch } from "./hooks/useSearch";
import { useExport } from "./hooks/useExport";
import { useFlowNavigation } from "./hooks/useFlowNavigation";
import { useKeyboardShortcuts } from "./hooks/useKeyboardShortcuts";
import type { GraphData } from "./types";

function ErrorFallback({ error, resetErrorBoundary }: FallbackProps) {
  return (
    <div className={appStyles["error-boundary"]}>
      <h2>Something went wrong</h2>
      <p>{error instanceof Error ? error.message : String(error)}</p>
      <button className={sharedStyles.btn + " " + sharedStyles["btn-back"]} onClick={resetErrorBoundary}>
        Try again
      </button>
    </div>
  );
}

function addExternalNodes(graphData: GraphData) {
  const nodeIds = new Set(graphData.nodes.map((n) => n.id));
  const externals: GraphData["nodes"] = [];

  graphData.edges.forEach((e) => {
    if (!nodeIds.has(e.target) && !externals.some((x) => x.id === e.target)) {
      const rawName = e.target.includes(":") ? e.target.split(":").pop()! : e.target;
      externals.push({
        id: e.target,
        type: "EXTERNAL",
        name: rawName,
        file_path: "(external)",
        start_line: 0,
        end_line: 0,
        language: "",
        ontology_class: null,
        domains: [],
        confidence_score: 0,
        metadata: {},
      });
    }
  });

  return {
    ...graphData,
    nodes: [...graphData.nodes, ...externals],
  };
}

const nodeTypes = { group: GroupNode };

export default function AppWrapper() {
  return (
    <ErrorBoundary FallbackComponent={ErrorFallback}>
      <App />
    </ErrorBoundary>
  );
}

function App() {
  const [nodes, setNodes] = useState<any[]>([]);
  const [edges, setEdges] = useState<any[]>([]);
  const [viewMode, setViewMode] = useState("full");
  const [depth, setDepth] = useState(3);
  const [hoveredNode, setHoveredNode] = useState<any>(null);
  const [stats, setStats] = useState<{ nodes: number; edges: number; external: number } | null>(
    null
  );
  const [showExternal, setShowExternal] = useState(false);
  const [allNodes, setAllNodes] = useState<any[]>([]);
  const [allEdges, setAllEdges] = useState<any[]>([]);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const [flowRootId, setFlowRootId] = useState<string | null>(null);
  const [graphLoading, setGraphLoading] = useState(true);
  const [isLayouting, setIsLayouting] = useState(false);

  const wrapperRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const { fitView } = useReactFlow();

  // Hook: fetch graph
  const { graph: graphData, loading: loadingState } = useGraphFetch();

  useEffect(() => {
    setGraphLoading(loadingState);
  }, [loadingState]);

  // Compute enriched graph data (adds external nodes) outside render phase
  const enrichedGraph = useMemo(() => {
    if (!graphData) return null;
    return addExternalNodes(graphData);
  }, [graphData]);

  const emptyGraph = useMemo<GraphData>(() => ({ nodes: [], edges: [] }), []);

  // Hook: keyboard shortcuts
  useKeyboardShortcuts({
    onEscape: () => {
      if (viewMode === "flow") buildFullGraph();
    },
    onFit: () => fitView({ padding: 0.15 }),
    onFocusSearch: () => searchInputRef.current?.focus(),
    disabled: false,
  });

  // Hook: search
  const { searchQuery, setSearchQuery, displayedNodes } = useSearch(nodes, "", 200);

  // Hook: export
  const { exportPng, exportSvg } = useExport((dataUrl: string, filename: string) => {
    const a = document.createElement("a");
    a.setAttribute("download", filename);
    a.setAttribute("href", dataUrl);
    a.click();
  });

  // Hook: flow navigation
  const { onNodeClick: onNodeClickHandler, clearCache } = useFlowNavigation(
    enrichedGraph ?? emptyGraph,
    setNodes,
    setEdges,
    setViewMode,
    setFlowRootId,
    depth
  );

  const buildFullGraph = useCallback(async () => {
    const enriched = enrichedGraph;
    if (!enriched?.nodes || !enriched?.edges) return;

    const validEdges = filterValidEdges(enriched.nodes, enriched.edges);
    const baseNodes = enriched.nodes.map((n) =>
      mapNodeToReactFlow(n, { groupKey: extractGroupKey(n) ?? undefined })
    );
    const baseEdges = validEdges.map((e: any, i: number) =>
      mapEdgeToReactFlow(e, i, { enrichedNodes: enriched.nodes })
    );

    setIsLayouting(true);
    const grouped = groupByFile(baseNodes, baseEdges);
    const layoutedNodes = await layoutGraph(grouped.nodes, grouped.edges);

    setAllNodes(layoutedNodes);
    setAllEdges(grouped.edges);
    setNodes(layoutedNodes);
    setEdges(grouped.edges);
    setViewMode("full");
    clearCache();
    fitView({ padding: 0.15 });
    setStats({
      nodes: enriched.nodes.length,
      edges: validEdges.length,
      external: enriched.nodes.filter((n) => n.type === "EXTERNAL").length,
    });
    setIsLayouting(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enrichedGraph, clearCache]);

  useEffect(() => {
    if (graphData) buildFullGraph();
  }, [graphData, buildFullGraph]);

  // Filter external nodes
  useEffect(() => {
    async function filterAndLayout() {
      if (viewMode !== "full") return;

      if (showExternal) {
        setNodes(allNodes);
        setEdges(allEdges);
      } else {
        const externalIds = new Set(
          allNodes.filter((n: any) => n.data?.nodeType === "EXTERNAL").map((n: any) => n.id)
        );
        const filteredNodes = allNodes.filter((n: any) => {
          if (n.data?.nodeType === "EXTERNAL") return false;
          if (n.type === "group")
            return allNodes.some((c: any) => c.groupId === n.id && !externalIds.has(c.id));
          return true;
        });
        const filteredNodeIds = new Set(filteredNodes.map((n: any) => n.id));
        const filteredEdges = allEdges.filter(
          (e: any) => filteredNodeIds.has(e.source) && filteredNodeIds.has(e.target)
        );

        setIsLayouting(true);
        const reLayouted = await layoutGraph(filteredNodes, filteredEdges);
        setNodes(reLayouted);
        setEdges(filteredEdges);
        setIsLayouting(false);
      }
      fitView({ padding: 0.15 });
    }
    filterAndLayout();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showExternal, allNodes, allEdges, viewMode]);

  const onNodeClick = useCallback(
    (_event: any, node: any) => {
      if (node.type === "group") return;
      onNodeClickHandler(_event, node);
    },
    [onNodeClickHandler]
  );

  const onNodeMouseEnter = useCallback((_event: any, node: any) => {
    setHoveredNode(node);
  }, []);

  const onNodeMouseLeave = useCallback(() => {
    setHoveredNode(null);
  }, []);

  if (graphLoading) {
    return (
      <div className={appStyles["app-root"] + " loading"}>
          <div className={appStyles["loading-indicator"]}>
            <div className={appStyles["loading-spinner"]} />
          <span>Loading graph data...</span>
        </div>
      </div>
    );
  }

  return (
    <div
      className={appStyles["app-root"]}
      ref={wrapperRef}
      onMouseMove={(e) => setMousePos({ x: e.clientX, y: e.clientY })}
    >
      <ReactFlow
        nodes={displayedNodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        onNodeMouseEnter={onNodeMouseEnter}
        onNodeMouseLeave={onNodeMouseLeave}
        colorMode="dark"
        minZoom={0.1}
        maxZoom={2}
      >
        <Background gap={20} size={1} style={{ backgroundColor: "#0d1117" }} />
        <Controls
          showInteractive={false}
          style={{ border: "1px solid #30363d", borderRadius: 8 }}
        />
        <MiniMap
          nodeColor={(n: any) => {
            if (viewMode === "flow") return n.id === hoveredNode?.id ? "#f44336" : "#ff9800";
            const colorMap: Record<string, string> = {
              FUNCTION: "#4fc3f7",
              CLASS: "#66bb6a",
              METHOD: "#ce93d8",
              EXTERNAL: "#546e7a",
            };
            return colorMap[n.data?.nodeType] || "#78909c";
          }}
          maskColor="rgba(0,0,0,0.7)"
          style={{ background: "#1a1a2e", border: "1px solid #333", borderRadius: 8 }}
          pannable
          zoomable
        />

        <ControlPanel
          onDepthChange={setDepth}
          depth={depth}
          searchQuery={searchQuery}
          setSearchQuery={setSearchQuery}
          searchInputRef={searchInputRef}
          onToggleExternal={setShowExternal}
          showExternal={showExternal}
          onExportPng={exportPng}
          onExportSvg={exportSvg}
          onFit={() => fitView({ padding: 0.15 })}
          viewMode={viewMode}
          flowRootId={flowRootId}
          onBack={() => buildFullGraph()}
          onBackToRoot={() => {
            if (flowRootId)
              onNodeClickHandler({} as any, { id: flowRootId, type: "FUNCTION" } as any);
          }}
        />

        <StatsPanel stats={stats} visible={viewMode === "full"} />
        {hoveredNode && <NodeTooltip node={hoveredNode} mousePos={mousePos} />}
        {viewMode === "full" && <LegendPanel visible={true} />}
        {(graphLoading || isLayouting) && (
          <Panel position="top-center">
            <LoadingOverlay visible={true} />
          </Panel>
        )}
      </ReactFlow>
    </div>
  );
}
