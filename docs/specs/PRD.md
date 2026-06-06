# 1. 📄 PRD (Product Requirements Document) — Draft
**Project Name:** CGIS (Code Graph Intelligence System)  
**Status:** Draft v0.1  

#### 🎯 1.1 Problem Statement
Современные LLM-агенты и RAG-системы страдают от "фрагментации контекста". При запросе "как работает процесс оплаты?", векторный поиск выдает куски кода, но не может восстановить **цепочку вызовов (call chain)** или **влияние изменений (impact analysis)**. Разработчику или агенту приходится "гадать" по кускам текста, вместо того чтобы следовать по графу.

#### 💎 1.2 Value Proposition
Создание **детерминированного слоя знаний** о коде, который превращает текст в вычислимую модель архитектуры. 
*   **Для агентов:** Переход от "поиска по тексту" к "навигации по графу".
*   **Для DeepWiki:** Автоматическая генерация документации, основанной на реальных связях, а не на комментариях.
*   **Для разработчика:** Инструмент мгновенного анализа влияния (Impact Analysis).

#### 🚀 1.3 Target Audience
1.  **AI Agents (Primary):** Автономные кодеры, которым нужен контекст "выше и ниже" текущей функции.
2.  **Knowledge Engineers:** Создатели графов знаний для сложных монорепозиториев.
3.  **DevOps/SRE:** Анализ цепочек вызовов от API-эндпоинта до БД.

#### 🛠️ 1.4 Key High-Level Features
*   **Multi-layer Extraction:** От синтаксиса (AST) до семантики (Domain).
*   **Deterministic Resolution:** Связи `CALLS` и `IMPORTS` должны быть подтвержденными, а не вероятностными.
*   **Hybrid Retrieval:** Сочетание векторного поиска (для "fuzzy" запросов) и графового обхода (для "structural" запросов).

---

# 2. 📋 FRD (Functional Requirements Document) — Draft
**Focus:** User Interaction & System Capabilities

#### 🔄 2.1 Functional Capabilities
*   **FC-1: Ingestion Pipeline:** Система должна уметь сканировать репозиторий и строить граф без участия человека.
*   **FC-2: Structural Querying:** Возможность ответить на вопросы: *"Кто вызывает эту функцию?"*, *"Какие модули импортирует этот класс?"*.
*   **FC-3: Flow Reconstruction:** Возможность построить путь от `API Endpoint -> Service -> Repository -> DB Table`.
*   **FC-4: Semantic Uplift:** Автоматическое присвоение "доменов" (например, `Auth`, `Billing`) на основе анализа имен и связей.
*   **FC-5: Impact Analysis:** Ответ на вопрос: *"Если я изменю сигнатуру этой функции, что сломается в системе?"*.

#### 👤 2.2 User Stories (Agent Perspective)
*   *As an AI Agent, I want to see the full call stack of a function so that I don't introduce breaking changes.*
*   *As an AI Agent, I want to know which domain a piece of code belongs to, so I can fetch relevant architectural rules.*

#### 📊 2.3 Accuracy Requirements
*   **Structural Integrity:** Связи `CALLS` и `DECLARES` должны иметь точность $>95\%$ (на базе AST).
*   **Semantic Confidence:** Связи семантического слоя (L2) должны сопровождаться `confidence_score` (для фильтрации галлюцинаций).

---

# 3. ⚙️ TDD (Technical Design Document) — Draft
**Focus:** Implementation & Architecture

#### 🏗️ 3.1 High-Level Architecture
*(Здесь мы опираемся на твои наброски)*
1.  **Extraction Layer:** Tree-sitter (Python/TS/Go) $\to$ Raw AST.
2.  **Resolution Layer (The Hard Part):** Symbol Table $\to$ Linkage (склейка вызовов с определениями).
3.  **Storage Layer:** 
    *   *L1 (Structural):* SQLite/DuckDB (для быстрых табличных запросов).
    *   *L2 (Graph):* Neo4j или NetworkX (для обходов графа).
4.  **Semantic Layer:** LLM-based enrichment (offline job).
5.  **Access Layer:** FastAPI (Graph Query Engine).

#### 🧬 3.2 Data Schema (Ontology)
*   **Nodes:** `{id, type, name, file, range, metadata, ontology_class}`
*   **Edges:** `{source, target, type, weight, confidence, context}`

#### ⚠️ 3.3 Critical Technical Risks
*   **Risk 1: Symbol Resolution (Dynamic languages):** В Python легко вызвать функцию, имя которой определяется в рантайме. 
    *   *Mitigation:* Использование эвристик + маркировка связей как `uncertain`.
*   **Risk 2: Scalability:** Граф огромного монорепозитория может раздуться.
    *   *Mitigation:* Использование графовых индексов и партиционирование по модулям/доменам.

---

