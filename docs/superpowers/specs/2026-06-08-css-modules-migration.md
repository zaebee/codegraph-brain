# CSS Modules Migration

**Date:** 2026-06-08
**Status:** Approved design
**Phase:** 4 — CSS Architecture

## Summary

Migrate from plain global CSS to CSS Modules with CSS custom properties. Resolves 2 class name conflicts, eliminates global scope leakage, and introduces a shared design token system.

## Scope

7 CSS files → 7 CSS Module files + 2 new files (`tokens.css`, `GroupNode.module.css`) + 1 extracted shared module (`shared.module.css`).

## Files

### New files

| File | Purpose |
|------|---------|
| `src/tokens.css` | Global CSS custom properties (colors, spacing, radii, font sizes) — loaded once in `main.tsx` |
| `src/shared.module.css` | Shared button classes (`.btn`, `.btn-icon`, `.btn-toggle`, `.btn-preset`, `.btn.active`) — imported by `ControlPanel.tsx` and `App.tsx` (ErrorFallback) |
| `src/GroupNode.module.css` | `.group-node` and `.group-node-label` — extracted from `App.css` |

### Renamed files (`*.css` → `*.module.css`)

| Before | After |
|--------|-------|
| `App.css` | `App.module.css` |
| `components/ControlPanel.css` | `components/ControlPanel.module.css` |
| `components/LoadingOverlay.css` | `components/LoadingOverlay.module.css` |
| `components/StatsPanel.css` | `components/StatsPanel.module.css` |
| `components/LegendPanel.css` | `components/LegendPanel.module.css` |
| `components/NodeTooltip.css` | `components/NodeTooltip.module.css` |

### Unchanged files

| File | Reason |
|------|--------|
| `index.css` | Global body/font styles — stays plain CSS |
| `theme.ts` | Node colors for dynamic inline styles — stays as-is |

## CSS Custom Properties (`tokens.css`)

```css
:root {
  --color-bg-primary: #0d1117;
  --color-bg-surface: #1a1a2e;
  --color-bg-elevated: #21262d;
  --color-bg-control: #161b22;
  --color-border: #30363d;
  --color-border-hover: #8b949e;
  --color-border-focus: #1f6feb;
  --color-text-primary: #cfd8dc;
  --color-text-secondary: #8b949e;
  --color-text-muted: #78909c;
  --color-text-accent: #58a6ff;
  --color-danger: #f44336;
  --spacing-xs: 4px;
  --spacing-sm: 8px;
  --spacing-md: 12px;
  --spacing-lg: 16px;
  --spacing-xl: 24px;
  --radius-sm: 6px;
  --radius-md: 8px;
  --radius-lg: 12px;
  --font-size-xs: 10px;
  --font-size-sm: 11px;
  --font-size-md: 12px;
  --font-size-lg: 13px;
  --font-size-xl: 14px;
  --font-size-2xl: 18px;
}
```

All hardcoded values in CSS files are replaced with `var(--token)` references.

## Shared Module (`shared.module.css`)

Contains only the `.btn` family — used by `ControlPanel.tsx` and `App.tsx` (ErrorFallback):

- `.btn` — base button
- `.btn:hover`, `.btn:active`
- `.btn.active` — active/toggled state
- `.btn-icon` — icon-only button (collapsed state)
- `.btn-toggle` — toggle button (External)
- `.btn-preset` — depth preset
- `.btn-back` — back navigation (no extra rules, inherits `.btn`)
- `.btn-export` — export button (no extra rules, inherits `.btn`)

## Conflicts Resolved

| Conflict | Resolution |
|----------|------------|
| `.loading-indicator` in App.css vs LoadingOverlay.css | Automatic — each module scopes its own `.loading-indicator` |
| `.loading-spinner` in App.css vs LoadingOverlay.css | Automatic — each module scopes its own `.loading-spinner` |
| `@keyframes spin` duplicated | Automatic — each module scopes its `@keyframes` |
| `.active` generic name | Automatic — scoped to ControlPanel.module.css |

## Global Selectors in App.module.css

ReactFlow library classes (`.react-flow__*`) must remain global. These selectors are wrapped in `:global()`:

```css
:global(.react-flow__node[data-id]) { ... }
:global(.react-flow__controls) { ... }
:global(.react-flow__minimap) { ... }
:global(.react-flow__background) { ... }
:global(.react-flow__edge-text) { ... }
```

## className Updates per File

### `App.tsx` (imports: `appStyles`, `sharedStyles`)
- `className="app-root"` → `className={appStyles['app-root']}`
- `className="btn btn-back"` → `className={\`${sharedStyles.btn} ${sharedStyles['btn-back']}\`}`
- `className="error-boundary"` → `className={appStyles['error-boundary']}`
- `className="loading-indicator"` → `className={appStyles['loading-indicator']}`
- `className="loading-spinner"` → `className={appStyles['loading-spinner']}`

### `ControlPanel.tsx` (imports: `panelStyles`, `sharedStyles`)
- `className="control-panel"` → `className={panelStyles['control-panel']}`
- `className={`btn btn-preset ${depth === 1 ? "active" : ""}`}` → `` className={`${sharedStyles.btn} ${sharedStyles['btn-preset']} ${depth === 1 ? sharedStyles.active : ''}`} ``
- All other panel classes use `panelStyles.xxx`

### `GroupNode.tsx` (imports: `styles` from `GroupNode.module.css`)
- `className="group-node"` → `className={styles['group-node']}`
- `className="group-node-label"` → `className={styles['group-node-label']}`

### Remaining components (each imports own `styles`)
- `LoadingOverlay.tsx` — `.loading-overlay`, `.loading-indicator`, `.loading-spinner`
- `StatsPanel.tsx` — `.stats-panel`, `.stats`, `.stats-sep`, `.stats-ext`
- `LegendPanel.tsx` — `.legend-panel`, `.legend`, `.legend-item`, `.legend-dot`, `.legend-label`
- `NodeTooltip.tsx` — `.tooltip-follow`, `.node-tooltip`, `.tooltip-name`, `.tooltip-type`, `.tooltip-file`, `.tooltip-lines`

## Migration Order

1. Create `tokens.css` with all CSS custom properties
2. Import `tokens.css` in `main.tsx`
3. Create `shared.module.css` with button classes (using `var(--token)`)
4. Create `GroupNode.module.css` with group node styles (using `var(--token)`)
5. Rename each `*.css` → `*.module.css`, replace hardcoded values with `var(--token)`
6. Update imports + `className` in each component
7. Verify: `npx tsc --noEmit`, `bun lint`, `bun run test:run`

## Testing

No functional changes — only CSS scoping and tokenization. Existing tests (74 tests) should continue to pass without modification. Verify visually by running `bun run dev`.

## Risks

- **Global selectors:** `:global()` wrappers needed for `react-flow__*` class selectors; missing one breaks ReactFlow styling
- **className typos:** Bracket notation for kebab-case names (e.g., `styles['control-panel']`) is easy to mistype; verify with `bun run dev`
- **Button styles:** `ErrorFallback` in `App.tsx` must import `shared.module.css` for `.btn` / `.btn-back` classes
