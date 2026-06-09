/* eslint-disable @typescript-eslint/no-explicit-any */

import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { ReactFlow, Background, Controls, MiniMap, useReactFlow, Panel } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { ErrorBoundary, type FallbackProps } from "react-error-boundary";
import appStyles from "./App.module.css";
import sharedStyles from "./shared.module.css";

import { filterValidEdges } from "./utils";
import { mapNodeToReactFlow } from "./utils/nodeMapper";
import { mapEdgeToReactFlow } from "./utils/edgeMapper";
import { applyContextHighlight } from "./utils/applyHighlight";

import ControlPanel from "./components/ControlPanel";
import StatsPanel from "./components/StatsPanel";
import NodeTooltip from "./components/NodeTooltip";
import LegendPanel from "./components/LegendPanel";
import LoadingOverlay from "./components/LoadingOverlay";
import FileContainerNode from "./components/FileContainerNode";

import { useGraphFetch } from "./hooks/useGraphFetch";
import { useGraphFilter } from "./hooks/useGraphFilter";
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
  const [hoveredNode, setHoveredNode] = useState<any>(null);
  const [stats, setStats] = useState<{ nodes: number; edges: number; external: number } | null>(
    null
  );
  const [allNodes, setAllNodes] = useState<any[]>([]);
  const [allEdges, setAllEdges] = useState<any[]>([]);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const [flowRootId, setFlowRootId] = useState<string | null>(null);
  const [graphLoading, setGraphLoading] = useState(true);

  const wrapperRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const parentChildrenRef = useRef<Map<string, string[]>>(new Map());
  const { fitView } = useReactFlow();

  const ALL_EDGE_TYPES = ["CALLS", "IMPORTS", "EXTENDS", "DECLARES"];

  const {
    showExternal,
    activeEdgeTypes,
    isLayouting,
    setShowExternal,
    setExpandedFiles,
    setActiveEdgeTypes,
  } = useGraphFilter({
    viewMode,
    allNodes,
    allEdges,
    parentChildrenRef,
    ALL_EDGE_TYPES,
    onFiltered: useCallback((filteredNodes: any[], filteredEdges: any[]) => {
      setNodes(filteredNodes);
      setEdges(filteredEdges);
    }, []),
  });

  // Hook: fetch graph
  const { graph: graphData, loading: loadingState } = useGraphFetch();

  useEffect(() => {
    setGraphLoading(loadingState);
  }, [loadingState]);

  const emptyGraph = useMemo<GraphData>(() => ({ nodes: [], edges: [] }), []);

  // Hook: keyboard shortcuts
  useKeyboardShortcuts({
    onEscape: () => {
      if (viewMode === "flow") buildFullGraph();
    },
    onFit: () => fitView({ padding: 0.15, duration: 250 }),
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
    graphData ?? emptyGraph,
    setNodes,
    setEdges,
    setViewMode,
    setFlowRootId,
    activeEdgeTypes
  );

  const buildFullGraph = useCallback(async () => {
    if (!graphData?.nodes || !graphData?.edges) return;

    const rawParentChildren = new Map<string, string[]>();
    for (const edge of graphData.edges) {
      if (edge.type === "CONTAINS") {
        const children = rawParentChildren.get(edge.source) || [];
        children.push(edge.target);
        rawParentChildren.set(edge.source, children);
      }
    }
    parentChildrenRef.current = rawParentChildren;

    const baseNodes = graphData.nodes.map((n) =>
      mapNodeToReactFlow(n, { groupKey: n.file_path || undefined })
    );
    const baseEdges = filterValidEdges(graphData.nodes, graphData.edges).map(
      (e: any, i: number) => mapEdgeToReactFlow(e, i)
    );

    setExpandedFiles(new Set());

    setAllNodes(baseNodes);
    setAllEdges(baseEdges);
    setViewMode("full");
    clearCache();
    setStats({
      nodes: graphData.nodes.length,
      edges: baseEdges.length,
      external: graphData.nodes.filter((n) => n.namespace && n.namespace !== "INTERNAL").length,
    });
  }, [graphData, clearCache, setExpandedFiles]);

  useEffect(() => {
    if (graphData) buildFullGraph();
  }, [graphData, buildFullGraph]);

  const onNodeClick = useCallback(
    (_event: any, node: any) => {
      if (viewMode === "full" && node.data?.nodeType === "FILE") {
        setExpandedFiles((prev: Set<string>) => {
          const next = new Set(prev);
          if (next.has(node.id)) {
            next.delete(node.id);
          } else {
            next.add(node.id);
          }
          return next;
        });
      } else {
        onNodeClickHandler(_event, node);
      }
    },
    [viewMode, onNodeClickHandler, setExpandedFiles]
  );

  const onNodeMouseEnter = useCallback((_event: any, node: any) => {
    setHoveredNode(node);
  }, []);

  const onNodeMouseLeave = useCallback(() => {
    setHoveredNode(null);
  }, []);

  const nodeTypes = useMemo(() => ({ fileContainer: FileContainerNode }), []);

  const { nodes: highlightedNodes, edges: highlightedEdges } = useMemo(
    () => applyContextHighlight(displayedNodes, edges, hoveredNode?.id ?? null),
    [displayedNodes, edges, hoveredNode]
  );

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
        nodes={highlightedNodes}
        edges={highlightedEdges}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        onNodeMouseEnter={onNodeMouseEnter}
        onNodeMouseLeave={onNodeMouseLeave}
        colorMode="dark"
        minZoom={0.1}
        maxZoom={2}
        nodesDraggable={false}
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
              FILE: "#5c6bc0",
              FUNCTION: "#4fc3f7",
              CLASS: "#66bb6a",
              METHOD: "#ce93d8",
            };
            return colorMap[n.data?.nodeType] || "#78909c";
          }}
          maskColor="rgba(0,0,0,0.7)"
          style={{ background: "#1a1a2e", border: "1px solid #333", borderRadius: 8 }}
          pannable
          zoomable
        />

        <ControlPanel
          searchQuery={searchQuery}
          setSearchQuery={setSearchQuery}
          searchInputRef={searchInputRef}
          onToggleExternal={setShowExternal}
          showExternal={showExternal}
          activeEdgeTypes={activeEdgeTypes}
          onToggleEdgeType={(t: string) => {
            setActiveEdgeTypes((prev) => {
              if (prev.includes(t)) return prev.filter((x) => x !== t);
              return [...prev, t];
            });
          }}
          ALL_EDGE_TYPES={ALL_EDGE_TYPES}
          onExportPng={exportPng}
          onExportSvg={exportSvg}
          onFit={() => fitView({ padding: 0.15, duration: 250 })}
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
