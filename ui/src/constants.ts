export const NODE_WIDTH = 160;
export const NODE_HEIGHT = 50;
export const FILE_CONTAINER_PADDING = 12;
export const FILE_HEADER_HEIGHT = 32;
export const FILE_HEADER_GAP = 4;
export const LAYOUT_DIRECTION: "TB" | "LR" = "LR";
export const NODE_SEP = 60;
export const RANK_SEP = 100;

// All edge type names — module-level constant, never re-created on render
export const ALL_EDGE_TYPES = ['CALLS', 'IMPORTS', 'EXTENDS', 'DECLARES'] as const
export type EdgeTypeName = (typeof ALL_EDGE_TYPES)[number]
