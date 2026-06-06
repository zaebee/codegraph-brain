# 1. 📄 PRD (Product Requirements Document) — Draft
**Project Name:** CGIS (Code Graph Intelligence System)  
**Status:** Draft v0.1  

#### 🎯 1.1 Problem Statement
Modern LLM agents and RAG systems suffer from "context fragmentation." When asked "how does the payment process work?", vector search retrieves disconnected code snippets but fails to reconstruct the **call chain** or perform **impact analysis**. Developers and agents are forced to "guess" based on text fragments instead of navigating a deterministic graph.

#### 💎 1.2 Value Proposition
Establishing a **deterministic knowledge layer** for code that transforms source text into a computable architectural model.
*   **For Agents:** Transition from "keyword-based search" to "graph-based navigation."
*   **For DeepWiki:** Automated documentation generation based on actual symbol relationships rather than potentially outdated comments.
*   **For Developers:** Real-time impact analysis tools for safer refactoring.

#### 🚀 1.3 Target Audience
1.  **AI Agents (Primary):** Autonomous coders requiring precise "upward and downward" context of a given function.
2.  **Knowledge Engineers:** Architects building knowledge graphs for complex monorepositories.
3.  **DevOps/SRE:** Engineers analyzing call chains from API endpoints down to database schemas.

#### 🛠️ 1.4 Key High-Level Features
*   **Multi-layer Extraction:** From raw syntax (AST) to high-level semantics (DomainConcepts).
*   **Deterministic Resolution:** `CALLS` and `IMPORTS` relations must be verified through symbol resolution, not probabilistic estimation.
*   **Hybrid Retrieval:** Combining vector search (for "fuzzy" intent) with graph traversal (for "structural" accuracy).

---

# 2. 📋 FRD (Functional Requirements Document) — Draft
**Focus:** User Interaction & System Capabilities

#### 🔄 2.1 Functional Capabilities
*   **FC-1: Ingestion Pipeline:** The system must autonomously scan repositories and construct the graph without human intervention.
*   **FC-2: Structural Querying:** Ability to answer structural questions: *"Who calls this function?"*, *"Which modules are imported by this class?"*.
*   **FC-3: Flow Reconstruction:** Capability to trace execution paths from `API Endpoint -> Service -> Repository -> DB Table`.
*   **FC-4: Semantic Uplift:** Automated assignment of `DomainConcepts` (e.g., `Auth`, `Billing`) based on naming conventions and relationship patterns.
*   **FC-5: Impact Analysis:** Answering the critical question: *"If I change this function signature, what parts of the system will break?"*.

#### 👤 2.2 User Stories (Agent Perspective)
*   *As an AI Agent, I want to see the full call stack of a function so that I don't introduce breaking changes.*
*   *As an AI Agent, I want to know which domain a piece of code belongs to, so I can fetch relevant architectural rules.*

#### 📊 2.3 Accuracy Requirements
*   **Structural Integrity:** `CALLS` and `DECLARES` relations must achieve $>95\%$ accuracy (validated against AST).
*   **Semantic Confidence:** Semantic layer (L2) relations must include a `confidence_score` to differentiate between facts and heuristic inferences.

---

# 3. ⚙️ TDD (Technical Design Document) — Draft
**Focus:** Implementation & Architecture

#### 🏗️ 3.1 High-Level Architecture
1.  **Extraction Layer:** Tree-sitter integration (Python/TS/Go) $\to$ Raw AST extraction.
2.  **Resolution Layer (Core):** Symbol Table management $\to$ Symbol Linkage (binding calls to their respective declarations).
3.  **Storage Layer:** 
    *   *L1 (Structural):* SQLite/DuckDB for high-performance relational queries.
    *   *L2 (Graph):* Neo4j or NetworkX for complex graph traversals.
4.  **Semantic Layer:** LLM-based enrichment for domain mapping (background process).
5.  **Access Layer:** FastAPI-powered Graph Query Engine.

#### 🧬 3.2 Data Schema (Ontology)
*   **Nodes (<code>Node</code>):** <code>{id (FQN), type (NodeType), name, file_path, start_line, end_line, language, metadata, ontology_class, domains, confidence_score}</code>
*   **Edges:** `{source, target, type (EdgeType), weight, confidence, context}`

#### ⚠️ 3.3 Critical Technical Risks
*   **Risk 1: Symbol Resolution in Dynamic Languages:** Python allows runtime function resolution (e.g., `getattr`).
    *   *Mitigation:* Use heuristics combined with tagging these edges as `uncertain`.
*   **Risk 2: Scalability:** Graphs for large monorepos can grow exponentially.
    *   *Mitigation:* Implementing graph indexing and partitioning by module/domain boundaries.

---
