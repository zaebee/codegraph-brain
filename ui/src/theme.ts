export interface NodeColor {
  bg: string;
  border: string;
  text: string;
}

export interface LegendItem {
  type: string;
  label: string;
  color: string;
}

export const NODE_COLORS: Record<string, NodeColor> = {
  FILE: { bg: "#1a2535", border: "#4a6a8a", text: "#8ab4cc" },
  CLASS: { bg: "#1b5e20", border: "#66bb6a", text: "#c8e6c9" },
  FUNCTION: { bg: "#1e3a5f", border: "#4fc3f7", text: "#b3e5fc" },
  METHOD: { bg: "#4a148c", border: "#ce93d8", text: "#e1bee7" },
  EXTERNAL: { bg: "#1a1a2e", border: "#546e7a", text: "#78909c" },
  GROUP: { bg: "#263238", border: "#546e7a", text: "#b0bec5" },
  DEFAULT: { bg: "#37474f", border: "#78909c", text: "#cfd8dc" },
};

export const FLOW_NODE_COLORS: Record<string, NodeColor> = {
  ROOT: { bg: "#4a148c", border: "#f44336", text: "#ffcdd2" },
  DEFAULT: { bg: "#3e2723", border: "#ff9800", text: "#ffe0b2" },
};

export const EDGE_COLORS: Record<string, string> = {
  CALLS: "#4fc3f7",
  IMPORTS: "#66bb6a",
  EXTENDS: "#ce93d8",
  CONTAINS: "#ff9800",
  DECLARES: "#78909c",
};

export const NODE_LEGEND_ITEMS: LegendItem[] = [
  { type: "FILE", label: "File", color: NODE_COLORS.FILE.border },
  { type: "CLASS", label: "Class", color: NODE_COLORS.CLASS.border },
  { type: "FUNCTION", label: "Function", color: NODE_COLORS.FUNCTION.border },
  { type: "METHOD", label: "Method", color: NODE_COLORS.METHOD.border },
];

export const EDGE_LEGEND_ITEMS: LegendItem[] = [
  { type: "CALLS", label: "Calls", color: EDGE_COLORS.CALLS },
  { type: "IMPORTS", label: "Imports", color: EDGE_COLORS.IMPORTS },
  { type: "EXTENDS", label: "Extends", color: EDGE_COLORS.EXTENDS },
  { type: "CONTAINS", label: "Contains", color: EDGE_COLORS.CONTAINS },
  { type: "DECLARES", label: "Declares", color: EDGE_COLORS.DECLARES },
];

export const NAMESPACE_COLORS: Record<string, string> = {
  'owner-api': '#2d6a2d',
  'owner-web': '#2d2d6a',
  'ownima-admin': '#5a2d6a',
  'rider-web': '#1a5a5a',
  _default: '#546e7a',
};

// 5-stop color scale: healthy (low fan_out) → sick (high fan_out)
export const HEATMAP_COLORS: NodeColor[] = [
  { bg: "#1b3a1b", border: "#66bb6a", text: "#c8e6c9" }, // 0–2  healthy
  { bg: "#2e3b1b", border: "#aed581", text: "#dcedc8" }, // 3–5  ok
  { bg: "#3b2a00", border: "#ffb74d", text: "#ffe0b2" }, // 6–9  warning
  { bg: "#3b1500", border: "#ff8a65", text: "#ffccbc" }, // 10–14 danger
  { bg: "#3b0000", border: "#ef9a9a", text: "#ffcdd2" }, // 15+  critical
]

export function getHeatmapColor(fanOut: number, inCycle: boolean): NodeColor {
  if (inCycle) return HEATMAP_COLORS[4]
  if (fanOut <= 2) return HEATMAP_COLORS[0]
  if (fanOut <= 5) return HEATMAP_COLORS[1]
  if (fanOut <= 9) return HEATMAP_COLORS[2]
  if (fanOut <= 14) return HEATMAP_COLORS[3]
  return HEATMAP_COLORS[4]
}
