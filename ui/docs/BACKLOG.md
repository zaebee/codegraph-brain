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

### Edge Aggregation — done ✅

- [x] Агрегировать множественные CALLS между двумя нодами в одно ребро
- [x] Толщина линии = количество вызовов (log-scale или sqrt)
- [x] Лейбл "×N" на ребре
- [ ] При наведении на ребро — тултип с деталями вызовов (future)
- [x] Context highlighting: при наведении на ноду — dim не-связанных нод/рёбер (opacity 0.15)
- [x] CSS transition для opacity при hover

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
- [x] Вынести filter pipeline из App.tsx в хук `useGraphFilter`

## 🎯 Phase 7: UI Stabilization (feat/ui branch — June 2026)

### File Containers — done ✅

- [x] FILE-контейнеры через `fileContainer` type (FileContainerNode)
- [x] Dagre layout с wrapping children в container по bounding box
- [x] Greedy overlap resolution между контейнерами (pairwise, сдвиг по Y)
- [x] Рекурсивный сбор потомков (FILE → CLASS → METHOD)
- [x] Container сдвигает всех потомков при overlap resolution
- [x] Стрелки на рёбрах (`markerEnd: MarkerType.ArrowClosed`)
- [x] FILE свернуты по дефолту (FILE-level view)
- [x] Collapse/expand через `expandedFiles` Set + `getCollapsedView`

### Layout Direction — done ✅

- [x] `LAYOUT_DIRECTION` в `constants.ts` (TB / LR) — сейчас LR

### Edge Aggregation — done ✅

- [x] `aggregateEdges` — объединение параллельных рёбер
- [x] Толщина = sqrt(count), лейбл "×N"
- [x] Context highlighting (dim не-связанных при hover)

## 🔴 Known Bugs

### 1. IMPORTS пропадают при раскрытии FILE
- **Причина:** IMPORTS-рёбра между нодами внутри разных FILE. Когда FILE_A раскрыт, а FILE_B свёрнут — target (b_method) не попадает в `visibleIds`.
- **Нужно:** агрегация edge до FILE-уровня: если source видим, а target спрятан внутри свёрнутого FILE — показывать ребро source → parent-FILE.

### 2. Ноды выпадают из контейнера при overlap resolution
- **Причина:** в редких случаях overlap resolution сдвигает часть потомков, но не все (нерекурсивный обход или edge-case в позициях).
- **Статус:** частично починено (рекурсивные потомки для bounding box), возможны остаточные кейсы.
- **Воспроизведение:** раскрыть 3+ FILE с пересекающимися bounding box.

### 3. Стрелки на рёбрах — не сработало (user says)
- **Причина:** возможно, `markerEnd` конфликтует с кастомным рендерингом или стилями. Проверить SVG defs/marker.
- **Ещё:** для CONTAINS и DECLARES стрелки не нужны — только для CALLS, IMPORTS, EXTENDS.

### 4. Layout direction — TB vs LR
- Сейчас стоит LR. Нужно выбрать окончательно.
- TB лучше для иерархии вызовов (call tree), LR — для чтения потоков данных.

## 🔵 TODO / Feature Requests

### ts_extractor
- [ ] Влит PR #92 (`feat(extractor): TypeScript/TSX extractor parity`)
- [ ] Зарегистрировать .ts/.tsx в `cli.py` (dict `extensions → extractor`)
- [ ] Протестировать: `uv run cgis ingest <ts_project> --output graph.json`

### Перенос UI в основной репозиторий
- [ ] `cd ../ && git remote add ui ./ui && git fetch ui`
- [ ] `git merge --allow-unrelated-histories ui/feat/ui` или `git subtree add`
- [ ] Удалить вложенный `.git` из `ui/`
- [ ] Перенести CI (bun run build, lint, test:run) в основной workflow

### Edge Improvement
- [ ] Edge label: отображать `CALLS`, `IMPORTS` и т.д. вдоль ребра
- [ ] Агрегация IMPORTS до FILE-уровня (см. bug #1)
- [ ] Стрелки только для направленных рёбер (CALLS, IMPORTS, EXTENDS)

### UX
- [ ] Поиск по нодам (фильтр по имени/типу)
- [ ] Semantic zoom: кластеризация на большом удалении
- [ ] Показать количество свернутых детей на FILE-ноде (например, "▲ 5 functions")

### Performance
- [ ] Debounce layout при одновременном раскрытии нескольких FILE
- [ ] Web Worker для dagre (было в планах, отложено)
- [ ] Virtualisation для >1000 нод

## 🟣 Backlog (CLI / Backend)

### `--source-root` multi-root ingestion flag
**Проблема:** при ingest из корня репо Python-импорты `cgis.*` не совпадают с FQN `src.cgis.*` → 2853 phantom cross-island edges без разрешённых связей.
**Решение:** добавить `--source-root <path>` (можно несколько раз) в `cgis ingest`. `IngestionPipeline` принимает `source_roots: list[str]`; FQN файла строится относительно ближайшего source_root, а не от CWD.
- [ ] Добавить `source_roots: list[str] | None` в `IngestionPipeline.__init__`
- [ ] `file_path_to_module_fqn(path, source_root)` — вычислять относительно source_root
- [ ] CLI: `--source-root / -s` (Multiple=True) в `cgis ingest`
- [ ] Тесты: ingest `src/cgis` с `--source-root src` → FQN `cgis.*`, без него → `src.cgis.*`

### Ideal / Optimal Synthetic Graph
**Идея:** генерировать эталонный граф как «абстрактный класс для архитектур» — pure structural reference, как interface в Go или ABC в Python. Полезен для визуального сравнения «наш граф vs ideal» и как фикстура в тестах.
**Формат:** `graph.json` или `.db` с заданным набором паттернов:
- Hub node (high fan-in, low fan-out) — как utils/helpers
- Chain (linear CALLS) — как middleware pipeline
- Star (one class calls many)
- Cycle-free DAG
- [ ] `cgis generate ideal --pattern <hub|chain|star|dag>` → `ideal.json`
- [ ] Или Python-скрипт `scripts/gen_ideal_graph.py` (проще, не требует CLI команды)
- [ ] Использовать как эталон в UI для демо-режима
