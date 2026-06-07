import React, { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { ReactFlow, Background, Controls, MiniMap, Panel, useReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { toPng, toSvg } from "html-to-image";
import "./App.css";

import graph from "./graph.json";

import { layoutGraph } from "./layout";
import { groupByClass } from "./grouping";
import { buildExecutionFlow } from "./flow";
import GroupNode from "./GroupNode";

const NODE_COLORS = {
  FUNCTION: { bg: "#1e3a5f", border: "#4fc3f7", text: "#b3e5fc" },
  CLASS:    { bg: "#1b5e20", border: "#66bb6a", text: "#c8e6c9" },
  METHOD:   { bg: "#4a148c", border: "#ce93d8", text: "#e1bee7" },
  EXTERNAL: { bg: "#1a1a2e", border: "#546e7a", text: "#78909c" },
  GROUP:    { bg: "#263238", border: "#546e7a", text: "#b0bec5" },
  DEFAULT:  { bg: "#37474f", border: "#78909c", text: "#cfd8dc" }
};

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

export default function App() {
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [viewMode, setViewMode] = useState("full");
  const [depth, setDepth] = useState(3);
  const [hoveredNode, setHoveredNode] = useState(null);
  const [stats, setStats] = useState(null);
  const [showExternal, setShowExternal] = useState(true);
  const [allNodes, setAllNodes] = useState([]);
  const [allEdges, setAllEdges] = useState([]);
  const graphRef = useRef(graph);
  const wrapperRef = useRef(null);

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
    }).then((dataUrl) => downloadImage(dataUrl, "graph.png"));
  }, [downloadImage]);

  const exportSvg = useCallback(() => {
    const el = wrapperRef.current?.querySelector(".react-flow__viewport");
    if (!el) return;
    toSvg(el, {
      backgroundColor: "#0d1117",
      filter: (node) =>
        !node?.classList?.contains("react-flow__minimap") &&
        !node?.classList?.contains("react-flow__controls")
    }).then((dataUrl) => downloadImage(dataUrl, "graph.svg"));
  }, [downloadImage]);

  const buildFullGraph = useCallback(() => {
    const enriched = addExternalNodes(graphRef.current);
    const g = enriched;

    const baseNodes = g.nodes.map((n) => {
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
          width: 160,
          minHeight: 40,
          opacity: n.type === "EXTERNAL" ? 0.6 : 1
        },
        class: extractClass(n)
      };
    });

    const validEdges = g.edges.filter(e => {
      return g.nodes.some(n => n.id === e.source) && g.nodes.some(n => n.id === e.target);
    });

    const baseEdges = validEdges.map((e, i) => {
      const targetNode = g.nodes.find(n => n.id === e.target);
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

    const grouped = groupByClass(baseNodes, baseEdges);
    const layoutedNodes = layoutGraph(grouped.nodes, grouped.edges);

    setAllNodes(layoutedNodes);
    setAllEdges(grouped.edges);
    setNodes(layoutedNodes);
    setEdges(grouped.edges);
    setViewMode("full");
    setStats({
      nodes: g.nodes.length,
      edges: validEdges.length,
      external: g.nodes.filter(n => n.type === "EXTERNAL").length
    });
  }, []);

  useEffect(() => {
    buildFullGraph();
  }, [buildFullGraph]);

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
  }, [showExternal, allNodes, allEdges, viewMode]);

  const onNodeClick = useCallback(
    (event, node) => {
      if (node.type === "group") return;
      const enriched = addExternalNodes(graphRef.current);
      const flow = buildExecutionFlow(enriched, node.id, depth, "both");

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
            width: 160,
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
    },
    [depth]
  );

  const onNodeMouseEnter = useCallback((event, node) => {
    setHoveredNode(node);
  }, []);

  const onNodeMouseLeave = useCallback(() => {
    setHoveredNode(null);
  }, []);

  return (
    <div className="app-root" ref={wrapperRef}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        onNodeMouseEnter={onNodeMouseEnter}
        onNodeMouseLeave={onNodeMouseLeave}
        colorMode="dark"
        fitView
        fitViewOptions={{ padding: 0.15 }}
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
              <button className="btn btn-back" onClick={buildFullGraph}>
                ← Back
              </button>
            ) : (
              <div className="depth-control">
                <span className="depth-label">Depth</span>
                <input
                  type="range"
                  min="1"
                  max="5"
                  value={depth}
                  onChange={(e) => setDepth(Number(e.target.value))}
                  className="depth-slider"
                />
                <span className="depth-value">{depth}</span>
              </div>
            )}

            {viewMode === "full" && (
              <>
                <button
                  className={`btn btn-toggle ${showExternal ? "active" : ""}`}
                  onClick={() => setShowExternal(prev => !prev)}
                >
                  {showExternal ? "● External" : "○ External"}
                </button>
                <button className="btn btn-export" onClick={exportPng}>
                  ⬇ PNG
                </button>
                <button className="btn btn-export" onClick={exportSvg}>
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
          <Panel position="bottom-left" className="tooltip-panel">
            <div className="node-tooltip">
              <div className="tooltip-name">{hoveredNode.data?.label}</div>
              <div className="tooltip-type">{hoveredNode.data?.subtitle}</div>
              <div className="tooltip-file">{hoveredNode.data?.file}</div>
              <div className="tooltip-lines">lines {hoveredNode.data?.lines}</div>
            </div>
          </Panel>
        )}
      </ReactFlow>
    </div>
  );
}

function extractClass(node) {
  if (node.type === "EXTERNAL") return null;
  if (node.file_path) return node.file_path;
  return null;
}
