# Backlog

## 🔴 Critical / Bugs

- [x] `onViewportChanged` — невалидный ReactFlow v12 проп, `needsFit` never resets after fit-view
- [x] Testing libs (`@testing-library/*`) в `dependencies` вместо `devDependencies`
- [x] `@testing-library/user-event` не используется — удалить
- [x] `build/` (CRA, 3MB + source maps) заtracked в git — `git rm -r --cached build/`

## 🟡 Quick Wins

- [x] Переименовать `extractClass` → `extractGroupKey`
- [x] Переименовать `groupByClass` → `groupByFile`
- [x] Вынести `NODE_WIDTH = 160` в shared constant
- [x] Убрать дублирующий `margin: 0` из `index.css`
- [x] Убрать `reportWebVitals.js`

## 🟢 Performance

- [x] `graph.json` (135KB) инлайнится в bundle — загружать через `fetch()`
- [x] Валидация рёбер O(n*m) → O(n+m) через Set
- [x] Дедупикация рёдер `flowEdges.some()` O(n²) → O(n) через Set
- [x] Memoize результаты `buildExecutionFlow` для повторных кликов
- [x] Debounce search input (150-300ms)
- [x] Lazy loading dagre — динамический `import()` в `layout.js`

## 🔵 UX

- [x] Клавиатурные шорткаты (Esc = back, F = fit view, / = search focus)
- [x] Cursor-following tooltip вместо фиксированного bottom-left
- [x] "Zoom to Fit" кнопка в панели управления
- [x] Loading indicator при layout computation
- [x] Search highlighting — border/glow на matching nodes
- [x] Depth presets ("Immediate callers" = 1, "Call tree" = 3)
- [x] Back-to-root навигация в flow view
- [x] ARIA labels на интерактивных элементах

## ⚪ Tooling

- [x] CI/CD — GitHub Actions workflow для test + build
- [x] ESLint + Prettier — flat config, React + typescript-eslint plugins
- [x] Environment variables — `.env` для graph data path, API URLs
- [x] `html-to-image` pin 1.11.11 — bumped to `^1.11.13`, verified with export tests
- [x] Bundle analyzer — `rollup-plugin-visualizer` через `ANALYZE=true vite build`

## 🟣 Code Quality

- [x] ErrorBoundary → `react-error-boundary` пакет
- [x] Убрать `!important` из CSS (10+ использований)
- [x] `displayedNodes` вычисление в `useMemo` для оптимизации
- [x] `extractGroupKey` вынести в отдельный модуль
- [x] Edge validation в `buildFullGraph` вынести в утилиту
- [x] Flow edge dedup вынести в утилиту
- [x] `NODE_WIDTH`, `NODE_HEIGHT`, `GROUP_PADDING` в shared constants
- [x] Типизация — TypeScript + JSDoc

## 🚀 Refactoring Plan

### Phase 1: Decompose App.jsx → компоненты + хуки ✅

- ✅ `components/ControlPanel.tsx` — панель управления
- ✅ `components/StatsPanel.tsx` — счётчики
- ✅ `components/NodeTooltip.tsx` — tooltip за мышкой
- ✅ `components/LegendPanel.tsx` — легенда
- ✅ `components/LoadingOverlay.tsx` — спиннер
- ✅ `hooks/useGraphFetch.ts` — fetch /graph.json
- ✅ `hooks/useSearch.ts` — search + debounce + highlight
- ✅ `hooks/useExport.ts` — export PNG/SVG
- ✅ `hooks/useFlowNavigation.ts` — навигация по flow view
- ✅ `hooks/useKeyboardShortcuts.ts` — клавиатурные шорткаты
- ✅ `utils/nodeMapper.ts` — mapNodeToReactFlow, mapNodeToFlowView
- ✅ `utils/edgeMapper.ts` — mapEdgeToReactFlow, mapEdgeToFlowView

**Исход:** App.tsx: ~300 строк (сокращение с 633 на ~50%)

### Phase 2: TypeScript миграция ✅

- ✅ `constants.ts`
- ✅ `types.ts` (GraphNode, GraphEdge, GraphData)
- ✅ `utils.ts`
- ✅ `theme.ts`
- ✅ `grouping.ts`
- ✅ `flow.ts`
- ✅ `layout.ts`
- ✅ `GroupNode.tsx`
- ✅ All hooks → `.ts`
- ✅ All components → `.tsx`
- ✅ `App.tsx`
- ✅ ESLint + typescript-eslint flat config
- ✅ `tsconfig.json` strict mode, `allowJs: true`
- ✅ 6 post-merge TS issues resolved (duplicate effect, mapper integration, isLayouting state, / search focus, enriched data to useFlowNavigation, noUnusedLocals)

### Phase 3: Тесты ✅

- [x] App smoke test → extended to integration (legend + stats panel)
- [x] flow.test.ts (execution flow)
- [x] grouping.test.ts (file-based grouping)
- [x] layout.test.ts (dagre layout + group boundaries)
- [x] `utils/nodeMapper.ts` — Unit
- [x] `utils/edgeMapper.ts` — Unit
- [x] `hooks/useSearch` — Unit (renderHook)
- [x] `hooks/useExport` — Unit (mock html-to-image)
- [x] `hooks/useFlowNavigation` — Unit
- [x] `components/ControlPanel` — Render test
- [x] Integration: fetch → layout → render (App.test.tsx)
- **Итог:** 10 test files, 74 tests, lint + tsc clean

### Phase 4: CSS Architecture ✅

- ✅ `tokens.css` — CSS custom properties (colors, spacing, radii, font sizes)
- ✅ `shared.module.css` — shared button classes (`.btn`, `.btn-icon`, `.btn-toggle`, `.btn-preset`, `.btn-back`, `.btn-export`, `.btn.active`)
- ✅ `GroupNode.module.css` — group node styles
- ✅ `ControlPanel` → CSS Modules
- ✅ `LoadingOverlay` → CSS Modules
- ✅ `StatsPanel` → CSS Modules
- ✅ `LegendPanel` → CSS Modules
- ✅ `NodeTooltip` → CSS Modules
- ✅ `App.tsx` → CSS Modules (+ `:global()` for react-flow classes)
- ✅ `GroupNode.tsx` → CSS Modules
- ✅ Old plain `.css` files deleted
- **Итог:** 8 CSS Modules, only `index.css` remains as plain CSS (global body/font imports), all tokens via `var(--token)`

### Phase 5: Progressive Disclosure (GitHub #30, #31) — блокировано backend-ом

Оба issue описывают: вместо загрузки всего `graph.json` → динамический API `/api/graph/explore`.

**Backend (FastAPI):**
- `GET /api/graph/explore?node_id=X&depth=1&direction=both` → ego-subgraph
- Использует существующий `QueryEngine.get_flow_graph()` и `get_impact_graph()`

**Frontend (React):**
- `useGraphFetch()` → загружает только корневые узлы при старте
- `expandNode(nodeId)` → `fetch(/api/graph/explore?node_id=...)` → merge в state
- `onNodeDoubleClick` → вызывает `expandNode`
- Дедупликация при merge (Set по `node.id`)

**Deprecation:**
- `graph.json` → заменяется на API
- `addExternalNodes()` → больше не нужен (API возвращает External)

## 🎯 Phase 6: Visual Refinements (2026-06-08)

### Edge Aggregation (B) — approved, prototype OK

- [ ] Агрегировать множественные CALLS между двумя нодами в одно ребро
- [ ] Толщина линии = количество вызовов (log-scale или sqrt)
- [ ] Лейбл "×N" на ребре
- [ ] При наведении на ребро — тултип с деталями вызовов
- [ ] Context highlighting: при наведении на ноду — dim не-связанных нод/рёбер (opacity 0.15)
- [ ] CSS transition для opacity при hover

### File Containers (A)

- [ ] Вместо CONTAINS рёбер при раскрытии FILE — ReactFlow group nodes
- [ ] Дети (FUNCTION/METHOD) отрисовываются внутри прямоугольника файла
- [ ] Полупрозрачный фон, padding, заголовок с именем файла
- [ ] Layout: dagre + group overlap resolution (было в старой версии layout.ts)
- [ ] Требует чинить конфликт dagre ↔ groups (file grouping)

### Module-level Hierarchy

- [ ] Добавить MODULE-ноды для директорий
- [ ] Три уровня: MODULE → FILE → FUNCTION/METHOD
- [ ] Drill-down навигация: модули → файлы → функции
- [ ] По умолчанию показаны только MODULE с IMPORTS

## 🧹 Code Quality (Phase 6)

- [x] Вынести `getCollapsedView` в чистую функцию (`collapse.ts`)
- [x] Обобщить DFS в `flow.ts` (один `dfs()` для outgoing/incoming)
- [x] Удалить мёртвый код: `ErrorFallback.tsx`, `types.js`, неиспользуемые CSS-классы
- [ ] Вынести filter pipeline из App.tsx в хук `useGraphFilter`
