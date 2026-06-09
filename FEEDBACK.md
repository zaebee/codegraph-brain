# cgis — Code Review Feedback

После полного аудита кода (extractors → resolver → storage → MCP → UI → self-parsing TS).

---

## 👍 Что реально хорошо

### Архитектура
Extract → Resolve → Store — чёткая модульная pipeline. Каждый этап можно
дёргать отдельно, тестировать изолированно. ResolverEngine как отдельный
проход — правильное решение, а не встраивание резолвинга в экстрактор.

### FQN Resolution
`raw_call:<name>` → resolved FQN — умный паттерн. Оставляет экстракторам
только одно: сказать "тут вызов". Resolver сам решает, куда он ведёт.
`self.method` → class method — корректно и покрывает самый частый кейс.

### MCP Integration
**Killer feature.** Не надо тащить зависимости в IDE. Не надо помнить CLI
флаги. `cgis_get_structure` / `cgis_trace_flow` / `cgis_analyze_impact`
через opencode — это immediate value для LLM-assisted разработки.
Формат Mermaid — идеальный баланс читаемости и machine parsability.

### TS Extractor
Боевой. 326 нод, 502 ребра с живого TS/TSX проекта — не демка.
Трансформеры (export unwrap, arrow functions, this→self) покрывают
реальные кейсы. JSX в TSX — тоже корректно.

### Self-parsing test
Гениально. Кормим себя собой → сразу видим регрессию. Это должно быть
must-have для всех экстракторов.

---

## ⚠️ Что надо чинить

### 1. IMPORTS → FILE node ID mismatch (critical)
**Проблема:** FILE node ID = raw file path (`hooks/useGraphFilter.ts`),
а IMPORTS edge target = module FQN (`hooks.useGraphFilter`). Resolver
их не связывает → IMPORTS падают в EXTERNAL/unresolved.

**Следствие:** в UI (дефолтное состояние — все FILE свёрнуты) IMPORTS
между FILE не видны, хотя технически должны быть. Выглядит как "файлы
ничего не импортят".

**Фикс:** либо изменить FILE node ID на module FQN, либо добавить
resolve pass, который маппит module FQN → file path.

### 2. Resolver coverage — только Python-семантика
TS resolver обрабатывает `self.method` (Python-паттерн). В JS/TS реально
используется `this.method()` — оно нормализуется в `self.method`, что
ок. Но JS standard library (`Array.map`, `Math.max`, `RegExp.test`)
остаются unresolved. Для TS нужен built-in resolve pass.

### 3. MCP server — extractors registry
MCP returns 0 nodes при первом запуске, потому что в `_EXTRACTORS` был
только Python. Нужно синхронизировать с `cli.py` или читать динамически.

### 4. Resolved edges — нет фильтрации по качеству
`resolved_edges` возвращает всё: CONTAINS, IMPORTS, CALLS (как resolved,
так и нет). Нет метрики "мы реально зарезолвили N из M звонков".
`confidence` в Node/Edge моделях есть, но не эксплуатируется.

---

## 💡 Feature requests

### 5. Test files filter
В pipeline или UI — фильтр `*.test.*`, `*.spec.*`. Сейчас тесты парсятся
наравне с продакшен-кодом и зашумляют граф.

### 6. Module-level view
Трёхуровневая навигация: MODULE → FILE → FUNCTION. Сейчас только FILE →
FUNCTION. Для больших проектов не хватает промежуточного уровня
(директории).

### 7. Semantic zoom
На большом удалении — кластеризация (только MODULE + IMPORTS).
При зуме — детали (FUNCTION + CALLS). Это для UI, не для бэка.

### 8. Edge aggregation в UI
Агрегация IMPORTS до FILE-уровня при свёрнутых FILE. Сейчас IMPORTS
между функциями разных файлов просто пропадают при коллапсе.

---

## 📊 Вердикт

**Не херота, а solid foundation.** Архитектура правильная, MCP — реальное
преимущество. Resolver нужно докручивать (модульные FQN, built-in JS),
UI стабилизировать. Ядро уже боевое — можно интегрировать и получать
пользу.
