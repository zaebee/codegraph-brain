# Backlog

## 🔴 Critical / Bugs

- [ ] `onViewportChanged` — невалидный ReactFlow v12 проп, `needsFit` never resets after fit-view — `src/App.jsx:369`
- [ ] Testing libs (`@testing-library/*`) в `dependencies` вместо `devDependencies` — `package.json:6-9`
- [ ] `@testing-library/user-event` не используется — удалить — `package.json:9`
- [ ] `build/` (CRA, 3MB + source maps) заtracked в git — `git rm -r --cached build/`

## 🟡 Quick Wins

- [ ] Переименовать `extractClass` → `extractGroupKey` — `src/App.jsx:478-482`
- [ ] Переименовать `groupByClass` → `groupByFile` — `src/grouping.js:1`
- [ ] Вынести `NODE_WIDTH = 160` в shared constant — дублируется в `src/layout.js:3` и `src/App.jsx:184,304`
- [ ] Убрать дублирующий CSS reset в `index.css` — `src/index.css:2` дублирует `src/App.css:1-5`
- [ ] Убрать `reportWebVitals.js` импорт если остался — `src/main.jsx`

## 🟢 Performance

- [ ] `graph.json` (135KB) инлайнится в bundle — загружать через `fetch()` или dynamic import — `src/App.jsx:7`
- [ ] Валидация рёбер O(n*m) → O(n+m) через Set — `src/App.jsx:191-193`
- [ ] Дедупикация рёдер `flowEdges.some()` O(n²) → O(n) через Set — `src/flow.js:82-85`
- [ ] Memoize результаты `buildExecutionFlow` для повторных кликов — `src/App.jsx:269-330`
- [ ] Debounce search input (150-300ms) — `src/App.jsx:419`
- [ ] Lazy loading ReactFlow + dagre — уменьшить initial bundle — `src/App.jsx:2`

## 🔵 UX

- [ ] Клавиатурные шорткаты (Esc = back, F = fit view, / = search focus)
- [ ] Cursor-following tooltip вместо фиксированного bottom-left — `src/App.jsx:450-459`
- [ ] "Zoom to Fit" кнопка в панели управления
- [ ] Loading indicator при layout computation (синхронный на main thread)
- [ ] Search highlighting — border/glow на matching nodes — `src/App.jsx:340-353`
- [ ] Depth presets ("Immediate callers" = 1, "Call tree" = 3)
- [ ] Back-to-root навигация в flow view — сейчас только "Back" кнопка
- [ ] ARIA labels на интерактивных элементах

## ⚪ Tooling

- [ ] CI/CD — GitHub Actions workflow для test + build
- [ ] ESLint — `eslint-plugin-react`, `eslint-plugin-react-hooks`
- [ ] Prettier — `.prettierrc` с консистентным форматированием
- [ ] Storybook для `GroupNode` и node configurations
- [ ] Environment variables — `.env` для graph data path, API URLs
- [ ] `html-to-image` pin 1.11.11 — periodic re-check (tech debt tracking)
- [ ] Bundle analyzer — `rollup-plugin-visualizer` для оптимизации

## 🟣 Code Quality

- [ ] ErrorBoundary → `react-error-boundary` пакет — `src/App.jsx:14-38`
- [ ] Убрать `!important` из CSS (10+ использований) — `src/App.css`
- [ ] `displayedNodes` вычисление в useMemo для оптимизации — `src/App.jsx:340-353`
- [ ] `extractGroupKey` вынести в отдельный модуль — `src/App.jsx:478-482`
- [ ] Edge validation в `buildFullGraph` вынести в утилиту — `src/App.jsx:191-193`
- [ ] Flow edge dedup вынести в утилиту — `src/flow.js:80-88`
- [ ] `NODE_WIDTH`, `NODE_HEIGHT`, `GROUP_PADDING` в shared constants — `src/layout.js:3-7`
- [ ] Типизация — добавить JSDoc или TypeScript для graph node/edge interfaces
- [ ] `onNodeMouseEnter`/`onNodeMouseLeave` — проверить валидность пропсов ReactFlow v12
- [ ] Убрать стёртые CSS классы `.react-flow__node` из App.css если не используются
