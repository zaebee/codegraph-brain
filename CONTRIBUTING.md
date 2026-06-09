# Contributing to CGIS

First off, thank you for considering contributing to CGIS! We are building a high-fidelity, mission-critical intelligence layer for AI agents. To maintain the integrity of the graph and the stability of the engine, we adhere to extremely strict engineering standards.

By contributing, you agree to follow these guidelines.

---

## 🛠️ Engineering Standards

We prioritize **Correctness, Type Safety, and Determinism** over speed of delivery.

### 1. Type Discipline (Mandatory)
We use `mypy` in **strict mode**.
*   **No `Any`:** The use of `Any` is prohibited unless it is technically impossible to avoid (and even then, it must be accompanied by a comment explaining why).
*   **Full Annotations:** Every function, method, and class attribute must have complete type annotations, including return types.
*   **Pydantic Models:** Use Pydantic for all shared data structures (anything that crosses module boundaries, is stored, or is returned from public APIs). Use `model_copy(update={...})` for immutable updates. Pure-internal accumulators (local variables, loop state) do not require Pydantic.

### 2. Linting & Formatting
We use `ruff` for both linting and formatting.
*   **Automated Fixes:** Run `make lint` to automatically fix most issues.
*   **Strict Linting:** All Ruff errors must be resolved. We do not suppress linting errors unless there is a documented technical reason.

### 3. Documentation & Ontology
CGIS is a semantic engine. Code without documentation is useless to an agent.
*   **Docstring Coverage:** We aim for >90% docstring coverage. Use `interrogate` to check your work.
*   **Ontology Compliance:** Any changes to `NodeType`, `EdgeType`, or the schema in `docs/ontology/` must be clearly documented in your PR. We treat the ontology as a formal contract.

---

## 🧪 Testing Mandate

**"A change is not complete until it is verified."**

1.  **Unit Tests:** Every new feature or logic change must include corresponding unit tests in `tests/unit/`.
2.  **Integration Tests:** Complex logic (e.g., resolver changes, pipeline updates) should be validated in `tests/integration/` (infrastructure is being built — see open issues).
3.  **Self-Parsing Tests:** If you modify the core engine, add self-parsing tests in `tests/self_parsing/` that verify the engine can correctly model itself (framework is planned — contribute to its design).
4.  **Coverage:** New code must be covered by tests. We monitor coverage trends in CI.

---

## 🚀 Workflow

1.  **Fork & Branch:** Create a descriptive branch name (e.g., `feat/add-go-extractor` or `fix/resolver-import-bug`).
2.  **Development Loop:**
    *   `make format`
    *   `make lint`
    *   `make type-check`
    *   `make pytest`
3.  **Pull Request:**
    *   Provide a clear, concise description of **WHY** the change is needed and **HOW** it was implemented.
    *   Link to relevant issues if applicable.
    *   Ensure your PR passes all CI checks (Linting, Type Checking, Testing).

### PR Checklist
- [ ] My code follows the project's coding style.
- [ ] I have added tests that prove my fix/feature is correct.
- [ ] I have updated documentation (docstrings, README, or docs/) where necessary.
- [ ] My changes do not break the ontology or existing type safety.
- [ ] I have run `make format && make lint && make type-check && make pytest` locally.

---

## 🤝 Code of Conduct
Be professional, respectful, and constructive. We are all working towards the same goal: making AI agents smarter and more reliable.

*Thank you for helping us build the foundation of the agentic era!*
