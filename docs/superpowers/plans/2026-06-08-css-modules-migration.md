# CSS Modules Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate all component CSS from plain global CSS to CSS Modules with CSS custom properties, resolving class name conflicts and introducing design tokens.

**Architecture:** 6 component CSS files renamed to `.module.css` + 3 new files (`tokens.css`, `shared.module.css`, `GroupNode.module.css`). `tokens.css` loaded once in `main.tsx`; `shared.module.css` provides cross-component button styles; each component imports its own scoped module. No functional changes — only scoping and tokenization.

**Tech Stack:** Vite CSS Modules (built-in, no config needed), CSS custom properties (`var(--token)`)

**Spec:** `docs/superpowers/specs/2026-06-08-css-modules-migration.md`

---

## File Structure

### Created
| File | Purpose |
|------|---------|
| `src/tokens.css` | CSS custom properties for colors, spacing, radii, font sizes |
| `src/shared.module.css` | `.btn` family classes — cross-component |
| `src/GroupNode.module.css` | `.group-node`, `.group-node-label` (extracted from App.css) |

### Renamed (content updated with `var(--token)` + removed btn classes)
| Old | New |
|-----|-----|
| `src/App.css` | `src/App.module.css` (+ `:global()` for react-flow classes) |
| `src/components/ControlPanel.css` | `src/components/ControlPanel.module.css` (- btn classes, moved to shared) |
| `src/components/LoadingOverlay.css` | `src/components/LoadingOverlay.module.css` |
| `src/components/StatsPanel.css` | `src/components/StatsPanel.module.css` |
| `src/components/LegendPanel.css` | `src/components/LegendPanel.module.css` |
| `src/components/NodeTooltip.css` | `src/components/NodeTooltip.module.css` |

### Modified
| File | Changes |
|------|---------|
| `src/main.tsx` | Add `import "./tokens.css"` |
| `src/App.tsx` | Import App.module.css + shared.module.css, update all className (6 occurrences) |
| `src/GroupNode.tsx` | Import GroupNode.module.css, update className (2 occurrences) |
| `src/components/ControlPanel.tsx` | Import ControlPanel.module.css + shared.module.css, update className (26 occurrences) |
| `src/components/LoadingOverlay.tsx` | Import LoadingOverlay.module.css, update className (3 occurrences) |
| `src/components/StatsPanel.tsx` | Import StatsPanel.module.css, update className (5 occurrences) |
| `src/components/LegendPanel.tsx` | Import LegendPanel.module.css, update className (5 occurrences) |
| `src/components/NodeTooltip.tsx` | Import NodeTooltip.module.css, update className (6 occurrences) |

### Deleted (after migration)
| File |
|------|
| `src/App.css` |
| `src/components/ControlPanel.css` |
| `src/components/LoadingOverlay.css` |
| `src/components/StatsPanel.css` |
| `src/components/LegendPanel.css` |
| `src/components/NodeTooltip.css` |

---

## Task 1: Create foundation files

**Files:**
- Create: `src/tokens.css`
- Create: `src/shared.module.css`
- Create: `src/GroupNode.module.css`
- Modify: `src/main.tsx`

### Step 1: Create `src/tokens.css`

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

### Step 2: Create `src/shared.module.css`

```css
.btn {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  color: var(--color-text-primary);
  padding: var(--spacing-sm) var(--spacing-md);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: var(--font-size-md);
  font-weight: 500;
  transition: all 0.2s;
}

.btn:hover {
  background: var(--color-border);
  border-color: var(--color-border-hover);
}

.btn:active {
  background: var(--color-bg-elevated);
}

.btn.active {
  background: var(--color-border-focus);
  border-color: var(--color-border-focus);
  color: white;
}

.btn-icon {
  padding: var(--spacing-xs) var(--spacing-sm);
  font-size: var(--font-size-xl);
  line-height: 1;
  min-width: auto;
}

.btn-toggle {
  font-size: var(--font-size-sm);
  padding: 8px 10px;
}

.btn-preset {
  font-size: var(--font-size-md);
  padding: var(--spacing-sm) 10px;
}

.btn-back {
  font-size: var(--font-size-sm);
}

.btn-export {
  font-size: var(--font-size-md);
}
```

### Step 3: Create `src/GroupNode.module.css`

Group node styles extracted from App.css, using CSS custom properties.

```css
.group-node {
  background: rgba(30, 40, 55, 0.6);
  border: 2px dashed var(--color-text-muted);
  border-radius: var(--radius-lg);
  padding: var(--spacing-md);
  pointer-events: none;
}

.group-node-label {
  font-size: var(--font-size-sm);
  font-weight: 700;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-bottom: var(--spacing-sm);
  padding-bottom: var(--spacing-xs);
  border-bottom: 1px solid var(--color-border);
}
```

### Step 4: Update `src/main.tsx`

Add `import "./tokens.css"` at the top (before `import "./index.css"`):

```tsx
import "./tokens.css";
import "./index.css";
```

### Step 5: Verify

```bash
cd ui && bun run test:run && bun lint && npx tsc --noEmit
```

Expected: all 74 tests pass, lint clean, tsc clean.

### Step 6: Commit

```bash
git add src/tokens.css src/shared.module.css src/GroupNode.module.css src/main.tsx
git commit -m "feat(css): add tokens.css, shared.module.css, GroupNode.module.css"
```

---

## Task 2: Migrate ControlPanel

**Files:**
- Create: `src/components/ControlPanel.module.css`
- Modify: `src/components/ControlPanel.tsx` (imports + className)
- Delete: `src/components/ControlPanel.css`

### Step 1: Create `src/components/ControlPanel.module.css`

Write the full CSS from `ControlPanel.css` BUT:
- Remove all `.btn` related classes (now in `shared.module.css`)
- Replace hardcoded values with `var(--token)`

```css
.control-panel {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.panel-collapsed {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  background: var(--color-bg-surface);
  padding: var(--spacing-sm);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
}

.panel-row {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  background: var(--color-bg-surface);
  padding: var(--spacing-md);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  min-width: 200px;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: var(--spacing-sm);
  border-bottom: 1px solid var(--color-border);
  margin-bottom: var(--spacing-xs);
}

.panel-title {
  font-size: var(--font-size-lg);
  font-weight: 600;
  color: var(--color-text-primary);
  letter-spacing: 0.5px;
}

.panel-content {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.depth-control {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.depth-label {
  font-size: var(--font-size-md);
  color: var(--color-text-secondary);
}

.depth-slider {
  width: 100%;
  cursor: pointer;
}

.depth-value {
  font-size: var(--font-size-md);
  color: var(--color-text-primary);
  text-align: right;
}

.depth-presets {
  display: flex;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

.search-input {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  color: var(--color-text-primary);
  padding: var(--spacing-sm) var(--spacing-md);
  border-radius: var(--radius-sm);
  font-size: var(--font-size-md);
  width: 100%;
  outline: none;
  box-sizing: border-box;
}

.search-input:focus {
  border-color: var(--color-border-focus);
  box-shadow: 0 0 0 2px rgba(31, 111, 235, 0.2);
}
```

### Step 2: Update `src/components/ControlPanel.tsx`

Change imports:

```tsx
import { useState } from "react";
import { Panel } from "@xyflow/react";
import { useReactFlow } from "@xyflow/react";
import type { RefObject } from "react";
import panelStyles from "./ControlPanel.module.css";
import sharedStyles from "../../shared.module.css";
```

Replace className (26 occurrences) — key changes:

Static panel classes:
- `className="control-panel"` → `className={panelStyles["control-panel"]}`
- `className="panel-collapsed"` → `className={panelStyles["panel-collapsed"]}`
- `className="panel-row"` → `className={panelStyles["panel-row"]}`
- `className="panel-header"` → `className={panelStyles["panel-header"]}`
- `className="panel-title"` → `className={panelStyles["panel-title"]}`
- `className="panel-content"` → `className={panelStyles["panel-content"]}`
- `className="depth-control"` → `className={panelStyles["depth-control"]}`
- `className="depth-label"` → `className={panelStyles["depth-label"]}`
- `className="depth-slider"` → `className={panelStyles["depth-slider"]}`
- `className="depth-value"` → `className={panelStyles["depth-value"]}`
- `className="depth-presets"` → `className={panelStyles["depth-presets"]}`
- `className="search-input"` → `className={panelStyles["search-input"]}`

Dynamic btn classes:
- `className="btn btn-icon"` → `className={sharedStyles.btn + " " + sharedStyles["btn-icon"]}`
- `className="btn btn-back"` → `className={sharedStyles.btn + " " + sharedStyles["btn-back"]}`
- `className="btn btn-export"` → `className={sharedStyles.btn + " " + sharedStyles["btn-export"]}`
- `` className={`btn btn-preset ${depth === 1 ? "active" : ""}`} `` → `` className={`${sharedStyles.btn} ${sharedStyles["btn-preset"]} ${depth === 1 ? sharedStyles.active : ""}`} ``
- `` className={`btn btn-toggle ${showExternal ? "active" : ""}`} `` → `` className={`${sharedStyles.btn} ${sharedStyles["btn-toggle"]} ${showExternal ? sharedStyles.active : ""}`} ``

### Step 3: Delete old CSS

```bash
rm src/components/ControlPanel.css
```

### Step 4: Verify

```bash
cd ui && bun run test:run && bun lint && npx tsc --noEmit
```

### Step 5: Commit

```bash
git add src/components/ControlPanel.module.css src/components/ControlPanel.tsx
git rm src/components/ControlPanel.css
git commit -m "feat(css): migrate ControlPanel to CSS Modules"
```

---

## Task 3: Migrate LoadingOverlay

**Files:**
- Create: `src/components/LoadingOverlay.module.css`
- Modify: `src/components/LoadingOverlay.tsx`
- Delete: `src/components/LoadingOverlay.css`

### Step 1: Create `src/components/LoadingOverlay.module.css`

Same as LoadingOverlay.css but with `var(--token)`:

```css
.loading-overlay {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 1000;
  pointer-events: none;
}

.loading-indicator {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--spacing-md);
  background: rgba(26, 26, 46, 0.95);
  padding: var(--spacing-xl);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
}

.loading-spinner {
  width: 32px;
  height: 32px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-text-accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.loading-indicator span {
  font-size: var(--font-size-xl);
  color: var(--color-text-primary);
  font-weight: 500;
}
```

### Step 2: Update `src/components/LoadingOverlay.tsx`

Change import:
```tsx
import styles from "./LoadingOverlay.module.css";
```

Replace className:
- `className="loading-overlay"` → `className={styles["loading-overlay"]}`
- `className="loading-indicator"` → `className={styles["loading-indicator"]}`
- `className="loading-spinner"` → `className={styles["loading-spinner"]}`

### Step 3: Delete old CSS

```bash
rm src/components/LoadingOverlay.css
```

### Step 4: Verify

```bash
cd ui && bun run test:run -- --reporter=verbose 2>&1 | grep -E "(PASS|FAIL|loadingOverlay)"
```

### Step 5: Commit

```bash
git add src/components/LoadingOverlay.module.css src/components/LoadingOverlay.tsx
git rm src/components/LoadingOverlay.css
git commit -m "feat(css): migrate LoadingOverlay to CSS Modules"
```

---

## Task 4: Migrate StatsPanel

**Files:**
- Create: `src/components/StatsPanel.module.css`
- Modify: `src/components/StatsPanel.tsx`
- Delete: `src/components/StatsPanel.css`

### Step 1: Create `src/components/StatsPanel.module.css`

```css
.stats-panel {
  background: var(--color-bg-surface);
  padding: var(--spacing-md);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
}

.stats {
  display: flex;
  gap: var(--spacing-sm);
  font-size: var(--font-size-md);
  color: var(--color-text-primary);
  flex-wrap: wrap;
}

.stats-sep {
  color: #484f58;
}

.stats-ext {
  color: var(--color-text-accent);
}
```

### Step 2: Update `src/components/StatsPanel.tsx`

Change import:
```tsx
import styles from "./StatsPanel.module.css";
```

Replace className:
- `className="stats-panel"` → `className={styles["stats-panel"]}`
- `className="stats"` → `className={styles.stats}`
- `className="stats-sep"` → `className={styles["stats-sep"]}`
- `className="stats-ext"` → `className={styles["stats-ext"]}`

### Step 3: Delete old CSS + verify + commit

```bash
rm src/components/StatsPanel.css
cd ui && bun run test:run -- --reporter=verbose 2>&1 | grep -E "(PASS|FAIL|statsPanel)"
git add src/components/StatsPanel.module.css src/components/StatsPanel.tsx
git rm src/components/StatsPanel.css
git commit -m "feat(css): migrate StatsPanel to CSS Modules"
```

---

## Task 5: Migrate LegendPanel

**Files:**
- Create: `src/components/LegendPanel.module.css`
- Modify: `src/components/LegendPanel.tsx`
- Delete: `src/components/LegendPanel.css`

### Step 1: Create `src/components/LegendPanel.module.css`

```css
.legend-panel {
  background: var(--color-bg-surface);
  padding: var(--spacing-md);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
}

.legend {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  font-size: var(--font-size-md);
  color: var(--color-text-primary);
}

.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.legend-label {
  color: var(--color-text-secondary);
}
```

### Step 2: Update `src/components/LegendPanel.tsx`

Change import:
```tsx
import styles from "./LegendPanel.module.css";
```

Replace className:
- `className="legend-panel"` → `className={styles["legend-panel"]}`
- `className="legend"` → `className={styles.legend}`
- `className="legend-item"` → `className={styles["legend-item"]}`
- `className="legend-dot"` → `className={styles["legend-dot"]}`
- `className="legend-label"` → `className={styles["legend-label"]}`

### Step 3: Delete + verify + commit

---

## Task 6: Migrate NodeTooltip

**Files:**
- Create: `src/components/NodeTooltip.module.css`
- Modify: `src/components/NodeTooltip.tsx`
- Delete: `src/components/NodeTooltip.css`

### Step 1: Create `src/components/NodeTooltip.module.css`

```css
.tooltip-follow {
  position: fixed;
  z-index: 1000;
  pointer-events: none;
}

.node-tooltip {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--spacing-md);
  min-width: 200px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
}

.tooltip-name {
  font-size: var(--font-size-xl);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--spacing-xs);
}

.tooltip-type {
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: var(--spacing-xs);
}

.tooltip-file {
  font-size: var(--font-size-sm);
  color: var(--color-text-accent);
  margin-bottom: 2px;
}

.tooltip-lines {
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
}
```

### Step 2: Update `src/components/NodeTooltip.tsx`

Change import:
```tsx
import styles from "./NodeTooltip.module.css";
```

Replace className:
- `className="tooltip-follow"` → `className={styles["tooltip-follow"]}`
- `className="node-tooltip"` → `className={styles["node-tooltip"]}`
- `className="tooltip-name"` → `className={styles["tooltip-name"]}`
- `className="tooltip-type"` → `className={styles["tooltip-type"]}`
- `className="tooltip-file"` → `className={styles["tooltip-file"]}`
- `className="tooltip-lines"` → `className={styles["tooltip-lines"]}`

### Step 3: Delete + verify + commit

---

## Task 7: Migrate App.css + App.tsx

**Files:**
- Create: `src/App.module.css`
- Modify: `src/App.tsx`
- Delete: `src/App.css`

### Step 1: Create `src/App.module.css`

Same content as `App.css` with:
1. Replace all hardcoded values with `var(--token)`
2. Wrap react-flow class selectors in `:global()`
3. Keep `.loading-indicator` and `.loading-spinner` (App's own version)

```css
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

.app-root {
  width: 100vw;
  height: 100vh;
  background: var(--color-bg-primary);
}

body {
  background: var(--color-bg-primary);
}

:global(.react-flow__node[data-id]) {
  transition: box-shadow 0.2s, transform 0.15s;
  cursor: pointer;
}

:global(.react-flow__node[data-id]:hover) {
  box-shadow: 0 4px 20px rgba(79, 195, 247, 0.3);
  transform: translateY(-1px);
}

:global(.react-flow__edge-text) {
  font-size: var(--font-size-xs);
  fill: var(--color-text-muted);
}

:global(.react-flow__node[data-type="group"]) {
  pointer-events: none;
  z-index: -1;
}

:global(.react-flow__node[data-type="group"] > div) {
  background: transparent;
  border: none;
  padding: 0;
  box-shadow: none;
}

:global(.react-flow__controls) {
  overflow: hidden;
}

:global(.react-flow__controls .react-flow__controls-button) {
  background: var(--color-bg-control);
  border-bottom: 1px solid var(--color-border);
  fill: var(--color-text-secondary);
}

:global(.react-flow__controls .react-flow__controls-button:hover) {
  background: var(--color-bg-elevated);
  fill: #e6edf3;
}

:global(.react-flow__minimap) {
  overflow: hidden;
}

:global(.react-flow__background) {
  background-color: var(--color-bg-primary);
}

.error-boundary {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100vh;
  background: var(--color-bg-primary);
  color: #c9d1d9;
  gap: var(--spacing-lg);
}

.error-boundary h2 {
  font-size: var(--font-size-2xl);
  color: var(--color-danger);
}

.error-boundary p {
  font-size: var(--font-size-lg);
  color: var(--color-text-secondary);
  max-width: 400px;
  text-align: center;
}

.loading-indicator {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  background: rgba(13, 17, 23, 0.9);
  border: 1px solid #333;
  border-radius: var(--radius-md);
  padding: var(--spacing-sm) var(--spacing-lg);
  color: #b0bec5;
  font-size: var(--font-size-lg);
}

.loading-spinner {
  width: 16px;
  height: 16px;
  border: 2px solid #333;
  border-top-color: #4fc3f7;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
```

Note: `#333`, `#b0bec5`, `#4fc3f7`, `#c9d1d9`, `#e6edf3`, `rgba(79, 195, 247, 0.3)`, `#484f58` are kept as hardcoded values in a few places where they're used only once (or the token would be over-specific). The CSS custom properties cover the high-frequency values.

### Step 2: Update `src/App.tsx`

Change imports:

```tsx
import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { ReactFlow, Background, Controls, MiniMap, useReactFlow, Panel } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { ErrorBoundary, type FallbackProps } from "react-error-boundary";
import appStyles from "./App.module.css";
import sharedStyles from "./shared.module.css";
```

Replace className:
- `className="app-root loading"` → `className={appStyles["app-root"] + " loading"}` (keeps "loading" as a global marker — no CSS targets it)
- `className="app-root"` → `className={appStyles["app-root"]}`
- `className="loading-indicator"` → `className={appStyles["loading-indicator"]}`
- `className="loading-spinner"` → `className={appStyles["loading-spinner"]}`
- `className="error-boundary"` → `className={appStyles["error-boundary"]}`
- `className="btn btn-back"` → `className={sharedStyles.btn + " " + sharedStyles["btn-back"]}`

### Step 3: Delete old CSS

```bash
rm src/App.css
```

### Step 4: Verify

```bash
cd ui && bun run test:run && bun lint && npx tsc --noEmit
```

### Step 5: Commit

```bash
git add src/App.module.css src/App.tsx
git rm src/App.css
git commit -m "feat(css): migrate App to CSS Modules"
```

---

## Task 8: Update GroupNode.tsx

**Files:**
- Modify: `src/GroupNode.tsx`

No old CSS file to delete (GroupNode had no separate CSS file — styles were in App.css and now in `GroupNode.module.css`).

### Step 1: Update `src/GroupNode.tsx`

Add import and replace className:

```tsx
import styles from "./GroupNode.module.css";

// ...
<div className={styles["group-node"]}>
  <div className={styles["group-node-label"]}>{label}</div>
</div>
```

### Step 2: Verify

```bash
cd ui && bun run test:run && bun lint && npx tsc --noEmit
```

### Step 3: Commit

```bash
git add src/GroupNode.tsx
git commit -m "feat(css): migrate GroupNode to CSS Modules"
```

---

## Task 9: Final verification

Run full quality gate:

```bash
cd ui && echo "=== LINT ===" && bun lint && echo "=== TSC ===" && npx tsc --noEmit && echo "=== TESTS ===" && bun run test:run
```

Expected: lint 0 errors, tsc 0 errors, 74/74 tests pass.

Run `bun run dev` and confirm the UI renders correctly (no broken styles, no console errors).

Delete old CSS files if any were missed:

```bash
ls src/components/*.css
# Should only show: (empty — or none)
```

If any `.css` files remain (besides index.css), they should be plain global CSS intentionally kept — verify against the plan.

```bash
git status
# Should show clean working tree (no stray .css files)
```

No commit needed — this is a verification step.
