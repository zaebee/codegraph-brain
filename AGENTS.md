# Graph Debugger

Interactive call-graph visualizer for Python codebases (`src/cgis/`). Vite + React 19 + @xyflow/react v12.

## Quick commands

```bash
bun run dev        # dev server (localhost:3000)
bun run test       # vitest watch mode
bun run test:run   # single run
bun run build      # production build
```

## Architecture

| File | Purpose |
|------|---------|
| `src/App.jsx` | Main component: state, UI panels, export, search, flow view |
| `src/layout.js` | Dagre TB layout + greedy vertical group overlap resolution |
| `src/grouping.js` | File-path based grouping via `groupId` metadata |
| `src/flow.js` | Bidirectional DFS execution flow (`direction`: "outgoing"/"incoming"/"both") |
| `src/GroupNode.jsx` | Custom ReactFlow group node (visual container only) |
| `src/graph.json` | 68 nodes, 297 edges — the input data |

## Key design decisions

- **No ReactFlow `parentNode`**: Groups use metadata-only `groupId`. ReactFlow v12 parent-child fights dagre layout.
- **CLASS nodes excluded from dagre**: Positioned at group center after layout to avoid bounding-box distortion.
- **Groups are visual containers**: Positioned from children bounding boxes; children keep absolute dagre positions.
- **`layoutGraph` for both views**: Full graph AND flow view go through dagre layout.
- **`addExternalNodes` cached in ref**: Computed once on first render, not per click.

## Gotchas

- `html-to-image` pinned to `1.11.11` — newer versions broken per xyflow docs.
- Search dims nodes via opacity — triggers ResizeObserver loop error (benign, ignore it).
- `extractClass(n)` returns `n.file_path` — used as `class` prop for grouping, not class-based grouping.
- Export targets `.react-flow__viewport`, filters out minimap/controls by class name.
