import { Component, useEffect, useState, useCallback, useRef } from "react";
import { ReactFlow, Background, Controls, MiniMap, Panel, useReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { toPng, toSvg } from "html-to-image";
import "./App.css";

// graph.json loaded via fetch()

import { layoutGraph } from "./layout";
import { groupByFile } from "./grouping";
import { buildExecutionFlow } from "./flow";
import GroupNode from "./GroupNode";
import { NODE_WIDTH } from "./constants";

class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-boundary">
          <h2>Something went wrong</h2>
          <p>{this.state.error?.message}</p>
          <button className="btn btn-back" onClick={() => this.setState({ hasError: false })}>
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

const NODE_COLORS = {
  FUNCTION: { bg: "#1e3a5f", border: "#4fc3f7", text: "#b3e5fc" },
  CLASS:    { bg: "#1b5e20", border: "#66bb6a", text: "#c8e6c9" },
  METHOD:   { bg: "#4a148c", border: "#ce93d8", text: "#e1bee7" },
  EXTERNAL: { bg: "#1a1a2e", border: "#546e7a", text: "#78909c" },
  GROUP:    { bg: "#263238", border: "#546e7a", text: "#b0bec5" },
  DEFAULT:  { bg: "#37474f", border: "#78909c", text: "#cfd8dc" }
};

const LEGEND_ITEMS = [
  { type: "FUNCTION", label: "Function", color: NODE_COLORS.FUNCTION.border },
  { type: "CLASS", label: "Class", color: NODE_COLORS.CLASS.border },
  { type: "METHOD", label: "Method", color: NODE_COLORS.METHOD.border },
  { type: "EXTERNAL", label: "External", color: NODE_COLORS.EXTERNAL.border }
];

const FLOW_NODE_COLORS = {
  ROOT:     { bg: "#4a148c", border: "#f44336", text: "#ffcdd2" },
  DEFAULT:  { bg: "#3e2723", border: "#ff9800", text: "#ffe0b2" }
};

const nodeTypes = { group: GroupNode };

function addExternalNodes(graphData) {
  const nodeIds = new Set(graphData.nodes.map(n => n.id));
  const externals = new Map();

  graphData.edges.forEach(e => {
    if (!nodeIds.has(e.target)) {
      if (!externals.has(e.target)) {
        const rawName = e.target.includes(":") ? e.target.split(":").pop() : e.target;
        externals.set(e.target, {
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
          metadata: {}
        });
      }
    }
  });

  return {
    ...graphData,
    nodes: [...graphData.nodes, ...Array.from(externals.values())]
  };
}

export default function AppWrapper() {
  return (
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  );
}

function App() {
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [viewMode, setViewMode] = useState("full");
  const [depth, setDepth] = useState(3);
  const [hoveredNode, setHoveredNode] = useState(null);
  const [stats, setStats] = useState(null);
  const [showExternal, setShowExternal] = useState(false);
  const [allNodes, setAllNodes] = useState([]);
  const [allEdges, setAllEdges] = useState([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const [flowRootId, setFlowRootId] = useState(null);
  const [graphData, setGraphData] = useState(null);
  const [graphLoading, setGraphLoading] = useState(true);
  const graphRef = useRef(null);
  const wrapperRef = useRef(null);
  const enrichedRef = useRef(null);
  const flowCacheRef = useRef(new Map());
  const searchInputRef = useRef(null);
  const { fitView } = useReactFlow();

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQuery(searchQuery);
    }, 200);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  useEffect(() => {
    flowCacheRef.current.clear();
  }, [depth]);

  const getEnriched = useCallback(() => {
    if (!enrichedRef.current && graphRef.current) {
      enrichedRef.current = addExternalNodes(graphRef.current);
    }
    return enrichedRef.current;
  }, []);

  useEffect(() => {
    fetch("/graph.json")
      .then(r => r.json())
      .then(data => {
        graphRef.current = data;
        setGraphData(data);
        setGraphLoading(false);
      })
      .catch(err => {
        console.error("Failed to load graph:", err);
        setGraphLoading(false);
      });
  }, []);

  const downloadImage = useCallback((dataUrl, filename) => {
    const a = document.createElement("a");
    a.setAttribute("download", filename);
    a.setAttribute("href", dataUrl);
    a.click();
  }, []);

  const exportPng = useCallback(() => {
    const el = wrapperRef.current?.querySelector(".react-flow__viewport");
    if (!el) return;
    toPng(el, {
      backgroundColor: "#0d1117",
      pixelRatio: 2,
      filter: (node) =>
        !node?.classList?.contains("react-flow__minimap") &&
        !node?.classList?.contains("react-flow__controls")
    })
      .then((dataUrl) => downloadImage(dataUrl, "graph.png"))
      .catch((err) => console.error("PNG export failed:", err));
  }, [downloadImage]);

  const exportSvg = useCallback(() => {
    const el = wrapperRef.current?.querySelector(".react-flow__viewport");
    if (!el) return;
    toSvg(el, {
      backgroundColor: "#0d1117",
      filter: (node) =>
        !node?.classList?.contains("react-flow__minimap") &&
        !node?.classList?.contains("react-flow__controls")
    })
      .then((dataUrl) => downloadImage(dataUrl, "graph.svg"))
      .catch((err) => console.error("SVG export failed:", err));
  }, [downloadImage]);

  const buildFullGraph = useCallback(() => {
    const enriched = getEnriched();

    const baseNodes = enriched.nodes.map((n) => {
      const colors = NODE_COLORS[n.type] || NODE_COLORS.DEFAULT;
      return {
        id: n.id,
        data: {
          label: n.name,
          subtitle: n.type,
          file: n.file_path,
          lines: `${n.start_line}-${n.end_line}`,
          nodeType: n.type
        },
        position: { x: 0, y: 0 },
        style: {
          background: colors.bg,
          border: `2px solid ${colors.border}`,
          borderRadius: 8,
          color: colors.text,
          padding: "8px 12px",
          fontSize: 12,
          fontWeight: 500,
          boxShadow: "0 2px 8px rgba(0,0,0,0.3)",
          width: NODE_WIDTH,
          minHeight: 40,
          opacity: n.type === "EXTERNAL" ? 0.6 : 1
        },
        class: extractGroupKey(n)
      };
    });

    const nodeIds = new Set(enriched.nodes.map(n => n.id));
    const validEdges = enriched.edges.filter(e => {
      return nodeIds.has(e.source) && nodeIds.has(e.target);
    });

    const baseEdges = validEdges.map((e, i) => {
      const targetNode = enriched.nodes.find(n => n.id === e.target);
      const isExternal = targetNode?.type === "EXTERNAL";
      return {
        id: `e-${i}`,
        source: e.source,
        target: e.target,
        label: e.type === "CALLS" ? "" : e.type,
        animated: e.type === "CALLS",
        style: {
          stroke: isExternal ? "#37474f" : "#546e7a",
          strokeWidth: isExternal ? 1 : 1.5,
          opacity: isExternal ? 0.4 : 1
        },
        labelStyle: {
          fill: "#78909c",
          fontSize: 10
        }
      };
    });

    setIsLoading(true);
    requestAnimationFrame(() => {
      const grouped = groupByFile(baseNodes, baseEdges);
      const layoutedNodes = layoutGraph(grouped.nodes, grouped.edges);

      setAllNodes(layoutedNodes);
      setAllEdges(grouped.edges);
      setNodes(layoutedNodes);
      setEdges(grouped.edges);
      setViewMode("full");
      fitView({ padding: 0.15 });
      setStats({
        nodes: enriched.nodes.length,
        edges: validEdges.length,
        external: enriched.nodes.filter(n => n.type === "EXTERNAL").length
      });
      setIsLoading(false);
    });
  }, [getEnriched]);

  useEffect(() => {
    if (graphData) {
      buildFullGraph();
    }
  }, [buildFullGraph, graphData]);

  useEffect(() => {
    if (viewMode !== "full") return;

    if (showExternal) {
      setNodes(allNodes);
      setEdges(allEdges);
    } else {
      const externalNodeIds = new Set(
        allNodes.filter(n => n.data?.nodeType === "EXTERNAL").map(n => n.id)
      );

      const filteredNodes = allNodes.filter(n => {
        if (n.data?.nodeType === "EXTERNAL") return false;
        if (n.type === "group") {
          return allNodes.some(
            c => c.groupId === n.id && !externalNodeIds.has(c.id)
          );
        }
        return true;
      });

      const filteredNodeIds = new Set(filteredNodes.map(n => n.id));
      const filteredEdges = allEdges.filter(e =>
        filteredNodeIds.has(e.source) && filteredNodeIds.has(e.target)
      );

      const reLayouted = layoutGraph(filteredNodes, filteredEdges);
      setNodes(reLayouted);
      setEdges(filteredEdges);
    }
    fitView({ padding: 0.15 });
  }, [showExternal, allNodes, allEdges, viewMode]);

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (document.activeElement.tagName === 'INPUT') return;
      
      switch (e.key) {
        case 'Escape':
          if (viewMode === 'flow') {
            buildFullGraph();
          }
          break;
        case 'f':
        case 'F':
          e.preventDefault();
          fitView({ padding: 0.15 });
          break;
        case '/':
          e.preventDefault();
          searchInputRef.current?.focus();
          break;
        default:
          break;
      }
    };
    
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [viewMode, fitView, buildFullGraph]);

  const onNodeClick = useCallback(
    (event, node) => {
      if (node.type === "group") return;
      const enriched = getEnriched();

      const cacheKey = `${node.id}:${depth}`;
      let flow;
      if (flowCacheRef.current.has(cacheKey)) {
        flow = flowCacheRef.current.get(cacheKey);
      } else {
        flow = buildExecutionFlow(enriched, node.id, depth, "both");
        flowCacheRef.current.set(cacheKey, flow);
      }

      const flowNodes = flow.nodes.map((n) => {
        const isRoot = n.id === node.id;
        const isExternal = n.type === "EXTERNAL";
        const colors = isRoot
          ? FLOW_NODE_COLORS.ROOT
          : isExternal
          ? NODE_COLORS.EXTERNAL
          : FLOW_NODE_COLORS.DEFAULT;
        return {
          id: n.id,
          data: {
            label: n.name,
            subtitle: n.type,
            file: n.file_path,
            lines: `${n.start_line}-${n.end_line}`,
            nodeType: n.type
          },
          position: { x: 0, y: 0 },
          style: {
            background: colors.bg,
            border: `2px solid ${colors.border}`,
            borderRadius: 8,
            color: colors.text,
            padding: "8px 12px",
            fontSize: 12,
            fontWeight: isRoot ? 700 : 500,
            boxShadow: isRoot
              ? "0 0 16px rgba(244,67,54,0.5)"
              : "0 2px 8px rgba(0,0,0,0.3)",
          width: NODE_WIDTH,
            minHeight: 40,
            opacity: isExternal ? 0.5 : 1
          }
        };
      });

      const flowEdges = flow.edges.map((e, i) => ({
        id: `flow-${i}`,
        source: e.source,
        target: e.target,
        animated: true,
        style: {
          stroke: "#ff9800",
          strokeWidth: 2
        }
      }));

      const layouted = layoutGraph(flowNodes, flowEdges);

      setNodes(layouted);
      setEdges(flowEdges);
      setViewMode("flow");
      setFlowRootId(node.id);
      fitView({ padding: 0.15 });
    },
    [depth, getEnriched]
  );

  const onNodeMouseEnter = useCallback((event, node) => {
    setHoveredNode(node);
  }, []);

  const onNodeMouseLeave = useCallback(() => {
    setHoveredNode(null);
  }, []);

  const displayedNodes = debouncedQuery
    ? nodes.map(n => {
        if (n.type === "group") return n;
        const q = debouncedQuery.toLowerCase();
        const match =
          (n.data?.label || "").toLowerCase().includes(q) ||
          (n.data?.subtitle || "").toLowerCase().includes(q) ||
          (n.data?.file || "").toLowerCase().includes(q);
        return {
          ...n,
          style: {
            ...n.style,
            opacity: match ? 1 : 0.15,
            boxShadow: match && debouncedQuery
              ? "0 0 12px rgba(79, 195, 247, 0.6)"
              : n.style?.boxShadow || "0 2px 8px rgba(0,0,0,0.3)"
          }
        };
      })
    : nodes;

  if (graphLoading) {
    return (
      <div className="app-root loading">
        <div className="loading-indicator">
          <div className="loading-spinner" />
          <span>Loading graph data...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="app-root" ref={wrapperRef} onMouseMove={(e) => setMousePos({ x: e.clientX, y: e.clientY })}>
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
        <Background gap={20} size={1} />
        <Controls showInteractive={false} />
        <MiniMap
          nodeColor={(n) => {
            if (viewMode === "flow") {
              return n.id === hoveredNode?.id ? "#f44336" : "#ff9800";
            }
            const colors = NODE_COLORS[n.data?.nodeType] || NODE_COLORS.DEFAULT;
            return colors.border;
          }}
          maskColor="rgba(0,0,0,0.7)"
          style={{
            background: "#1a1a2e",
            border: "1px solid #333",
            borderRadius: 8
          }}
          pannable
          zoomable
        />

        <Panel position="top-left" className="control-panel">
          <div className="panel-row">
            {viewMode === "flow" ? (
              <>
                <button className="btn btn-back" onClick={buildFullGraph} aria-label="Back to full graph">
                  ← Back
                </button>
                {flowRootId && (
                  <button
                    className="btn btn-back"
                    onClick={() => onNodeClick({}, { id: flowRootId, type: 'FUNCTION' })}
                    aria-label="Back to root node"
                  >
                    ↑ Root
                  </button>
                )}
                <button className="btn btn-export" onClick={() => fitView({ padding: 0.15 })} aria-label="Zoom to fit">
                  ⊞ Fit
                </button>
              </>
            ) : (
              <>
                <div className="depth-control">
                  <span className="depth-label">Depth</span>
                  <input
                    type="range"
                    min="1"
                    max="5"
                    value={depth}
                    onChange={(e) => setDepth(Number(e.target.value))}
                    className="depth-slider"
                    aria-label="Flow depth"
                    aria-valuemin={1}
                    aria-valuemax={5}
                    aria-valuenow={depth}
                  />
                  <span className="depth-value">{depth}</span>
                  <div className="depth-presets">
                    <button 
                      className={`btn btn-preset ${depth === 1 ? 'active' : ''}`}
                      onClick={() => setDepth(1)}
                    >
                      1: Immediate
                    </button>
                    <button 
                      className={`btn btn-preset ${depth === 2 ? 'active' : ''}`}
                      onClick={() => setDepth(2)}
                    >
                      2: Near
                    </button>
                    <button 
                      className={`btn btn-preset ${depth === 3 ? 'active' : ''}`}
                      onClick={() => setDepth(3)}
                    >
                      3: Call tree
                    </button>
                  </div>
                </div>
                <button className="btn btn-export" onClick={() => fitView({ padding: 0.15 })} aria-label="Zoom to fit">
                  ⊞ Fit
                </button>
              </>
            )}

            {viewMode === "full" && (
              <>
                <input
                  ref={searchInputRef}
                  type="text"
                  placeholder="Search nodes... (press / to focus)"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="search-input"
                  aria-label="Search nodes"
                />
                <button
                  className={`btn btn-toggle ${showExternal ? "active" : ""}`}
                  onClick={() => setShowExternal(prev => !prev)}
                  aria-label="Toggle external nodes"
                  aria-pressed={showExternal}
                >
                  {showExternal ? "● External" : "○ External"}
                </button>
                <button className="btn btn-export" onClick={exportPng} aria-label="Export as PNG">
                  ⬇ PNG
                </button>
                <button className="btn btn-export" onClick={exportSvg} aria-label="Export as SVG">
                  ⬇ SVG
                </button>
              </>
            )}
          </div>
        </Panel>

        {stats && viewMode === "full" && (
          <Panel position="top-right" className="stats-panel">
            <div className="stats">
              <span>{stats.nodes} nodes</span>
              <span className="stats-sep">|</span>
              <span>{stats.edges} edges</span>
              <span className="stats-sep">|</span>
              <span className="stats-ext">{stats.external} ext</span>
            </div>
          </Panel>
        )}

        {hoveredNode && (
          <div
            className="tooltip-follow"
            style={{
              position: 'fixed',
              left: mousePos.x + 12,
              top: mousePos.y + 12,
              pointerEvents: 'none'
            }}
          >
            <div className="node-tooltip">
              <div className="tooltip-name">{hoveredNode.data?.label}</div>
              <div className="tooltip-type">{hoveredNode.data?.subtitle}</div>
              <div className="tooltip-file">{hoveredNode.data?.file}</div>
              <div className="tooltip-lines">lines {hoveredNode.data?.lines}</div>
            </div>
          </div>
        )}

        {viewMode === "full" && (
          <Panel position="bottom-right" className="legend-panel">
            <div className="legend">
              {LEGEND_ITEMS.map(item => (
                <div key={item.type} className="legend-item">
                  <span className="legend-dot" style={{ background: item.color }} />
                  <span className="legend-label">{item.label}</span>
                </div>
              ))}
            </div>
          </Panel>
        )}
      {isLoading && (
        <Panel position="top-center">
          <div className="loading-indicator">
            <div className="loading-spinner" />
            <span>Computing layout...</span>
          </div>
        </Panel>
      )}
      </ReactFlow>
    </div>
  );
}

function extractGroupKey(node) {
  if (node.type === "EXTERNAL") return null;
  if (node.file_path) return node.file_path;
  return null;
}
