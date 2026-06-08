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
  FUNCTION: { bg: "#1e3a5f", border: "#4fc3f7", text: "#b3e5fc" },
  CLASS: { bg: "#1b5e20", border: "#66bb6a", text: "#c8e6c9" },
  METHOD: { bg: "#4a148c", border: "#ce93d8", text: "#e1bee7" },
  EXTERNAL: { bg: "#1a1a2e", border: "#546e7a", text: "#78909c" },
  GROUP: { bg: "#263238", border: "#546e7a", text: "#b0bec5" },
  DEFAULT: { bg: "#37474f", border: "#78909c", text: "#cfd8dc" },
};

export const FLOW_NODE_COLORS: Record<string, NodeColor> = {
  ROOT: { bg: "#4a148c", border: "#f44336", text: "#ffcdd2" },
  DEFAULT: { bg: "#3e2723", border: "#ff9800", text: "#ffe0b2" },
};

export const LEGEND_ITEMS: LegendItem[] = [
  { type: "FUNCTION", label: "Function", color: NODE_COLORS.FUNCTION.border },
  { type: "CLASS", label: "Class", color: NODE_COLORS.CLASS.border },
  { type: "METHOD", label: "Method", color: NODE_COLORS.METHOD.border },
  { type: "EXTERNAL", label: "External", color: NODE_COLORS.EXTERNAL.border },
];
