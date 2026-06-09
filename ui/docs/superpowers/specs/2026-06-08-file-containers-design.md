# File Containers (A)

**Date:** 2026-06-08
**Status:** Approved — ready for implementation
**Designer:** brainstorming session

## Problem

Expanded FILE nodes currently show children (FUNCTION, METHOD, CLASS) as separate nodes connected by `CONTAINS` edges. This creates visual noise — dashed orange lines cross the graph and the containment relationship is not immediately obvious.

## Solution

FILE nodes become **visual containers**. When expanded, children render *inside* the FILE node's rectangle instead of as separate connected nodes. The containment is expressed visually (position + background + border) rather than through edges.

## Key Design Decisions

1. **No ReactFlow `parentId`** — dagre v12 fights parent-child positioning. Children are regular nodes positioned by dagre at their absolute coordinates. The FILE container wraps around them in a post-processing pass.

2. **No CONTAINS edges when expanded** — the visual container IS the containment. CONTAINS edges are only shown when the FILE is collapsed (children not visible).

3. **Custom FILE node component** (`FileContainerNode`) — renders the container background, header bar, and manages the child area visual.

4. **FILE node uses `type: "fileContainer"`** — registered as a custom `nodeType` in ReactFlow.

## Visual Design

### Collapsed state
Same as current: small rectangle with file name, colored by file type. `▶` prefix.

```
┌──────────────────────┐
│ ▶ pipeline.py        │
└──────────────────────┘
```

### Expanded state
Container with semi-transparent background, header bar. Children are standard ReactFlow nodes positioned by dagre and rendered *inside* the container area by ReactFlow (the container is transparent to pointer events in the child area, only the header is interactive).

```
┌─────────────────────────────────┐
│ 📄 pipeline.py            ▼    │  ← header bar (solid)
├─────────────────────────────────┤
│ ┌──────────┐  ┌──────────┐     │  ← children (semi-transparent bg)
│ │ run()    │  │ parse()  │     │
│ └──────────┘  └──────────┘     │
│ ┌──────────────────────┐       │
│ │ ConfigLoader         │       │
│ └──────────────────────┘       │
└─────────────────────────────────┘
```

### Styling
- Container background: FILE node color (`#1a237e`) at 20% opacity
- Container border: FILE node border color (`#5c6bc0`), dashed, 2px
- Header: solid FILE background at 60% opacity, bold file name
- Header height: 32px
- Children area padding: 12px from each edge (left, right, bottom); 4px from header bottom
- Corner radius: 8px (matches existing node style)

## Pipeline Changes

### Current pipeline
```
getCollapsedView → type filter → aggregate → external filter → labels → dagre layout
```

### New pipeline
```
getCollapsedView → type filter → aggregate → external filter → labels → dagre layout → CONTAINER WRAP
```

The new **container wrap** step is a pure function in `layout.ts`:
1. For each expanded FILE, find its children via `parentChildren` map
2. Compute children bounding box (minX, maxX, minY, maxY)
3. Expand by padding (12px left/right/bottom, 32px top for header + 4px gap)
4. Set FILE node position to `(minX - paddingLeft, minY - headerHeight - gap)`
5. Set FILE node size to `(width, height)` via `style.width` / `style.height`
6. Resolve overlapping containers: greedy vertical push-down (existing algorithm from old `layout.ts`)
7. Remove CONTAINS edges where source is an expanded FILE and target is its child
8. Return `{ nodes, edges }` (not just nodes) — edges are also filtered

This makes `layoutGraph` return both nodes AND edges, not just nodes.

### ReactFlow integration
- `nodeTypes.fileContainer = FileContainerNode` — registered in App.tsx
- FILE nodes in expanded state get `type: "fileContainer"` instead of default
- FILE nodes in collapsed state keep default type (simple styled node)

## FileContainerNode Component

```
FileContainerNode (custom component)
├── Receives: data (label, nodeType, children info), selected, id
├── Renders: <div className="file-container">
│   ├── <div className="file-container-header"> (file name, toggle indicator)
│   └── children AREA — transparent, children rendered by ReactFlow
└── Style: background, border, borderRadius from theme
```

The container itself is transparent to mouse events for its children area. Only the header is interactive (click to collapse/expand).

## Files to Change

| File | Change |
|------|--------|
| `src/layout.ts` | Add container wrap pass after dagre (compute bounds, overlap resolution, CONTAINS removal). Return `{nodes, edges}` instead of just nodes |
| `src/components/FileContainerNode.tsx` | **NEW** — custom node component for FILE containers |
| `src/components/FileContainerNode.module.css` | **NEW** — container styles |
| `src/App.tsx` | Register `nodeTypes`, pass expanded FILE type as `fileContainer`. Update `onFiltered` to receive `{nodes, edges}` from layout |
| `src/App.module.css` | Remove `.react-flow__node[data-type="group"]` rules (no longer relevant) |
| `src/hooks/useGraphFilter.ts` | Update to pass edges through layout and use returned `{nodes, edges}` |
| `src/collapse.ts` | No change needed (already filters correctly) |
| `src/constants.ts` | Add `FILE_CONTAINER_PADDING`, `FILE_HEADER_HEIGHT` |
| `src/theme.ts` | No change (colors already defined) |

## Edge Cases

- **FILE with no children**: renders as collapsed (small node) — cannot be expanded
- **FILE with one child**: container wraps snugly around single child
- **Overlapping containers**: resolved via greedy vertical push (proven from old layout.ts)
- **Flow view**: unaffected (containers only in full view, flow view uses different layout)
- **Search highlighting**: container opacity is dimmed if non-matching, children inside also dim
- **Export**: containers render correctly in PNG/SVG export (they're just styled divs)

## Testing

- Unit: `FileContainerNode` renders correct structure in collapsed/expanded states
- Unit: layout container wrap computes bounds correctly
- Integration: expanded FILE shows children inside container, not as separate nodes with CONTAINS
- Integration: collapsed FILE reverts to small node
- Integration: overlap resolution prevents container overlap
