# 🏗️ TDD: Resolution Layer Architecture

## 1. 🎯 The Ambiguity Problem
Simple name-based searching (`name_matching`) fails in three primary scenarios:
1.  **Shadowing:** A `save()` method in the `User` class versus a `save()` method in the `Order` class.
2.  **Aliasing:** `from module import func as f`. The code shows `f()`, but the graph must link to `module.func`.
3.  **Namespace Scoping:** Distinguishing between `self.method()` (current instance) and `utils.helper()` (imported module).

## 2. 🔑 Key Concept: Fully Qualified Name (FQN)
To eliminate ambiguity, every node in the graph must possess a unique **FQN**.
*   **Incorrect:** `id: "save"`
*   **Correct:** `id: "project.auth.services.UserService.save"`

**FQN Formula:** `[Package/Module Path] . [Class Hierarchy] . [Symbol Name]`

---

## 3. 🔄 Architecture: Three-Pass Resolution
Symbol relationships cannot be resolved in a single AST traversal. A three-phase approach is required.

### Phase 1: Declaration Indexing (Global Symbol Table)
**Goal:** Construct a "world map" — a registry of all existing entities.

1.  **Traversal:** Scan all repository files.
2.  **Action:** Identify all `ClassDef`, `FunctionDef`, and `VariableDef` nodes.
3.  **FQN Calculation:**
    *   For file `src/auth/manager.py` and function `login`:
    *   `FQN = src.auth.manager.login`
4.  **Outcome:** A Global `SymbolTable`:
    *   `Map<FQN, NodeMetadata>`
    *   *Example:* `{"src.auth.manager.login": {type: FUNCTION, file: "..."}}`

### Phase 2: Import & Namespace Mapping (Local Context)
**Goal:** Build a "translation dictionary" from local identifiers to FQNs for each file.

1.  **Traversal:** Process files individually.
2.  **Action:** Analyze `import` and `from ... import ...` statements.
3.  **Local Namespace Map (LNM) Construction:**
    *   Given `from src.auth.manager import login as auth_login` in `src/api/handler.py`.
    *   **LNM for this file:** `{"auth_login": "src.auth.manager.login", "manager": "src.auth.manager"}`.
4.  **Outcome:** A collection of `FileContext` objects.

### Phase 3: The Linking Pass (Edge Creation)
**Goal:** Bind symbol "Usage" (calls/references) to "Declaration" (definitions).

1.  **Traversal:** Traverse the AST of each file.
2.  **Action:** Locate `Call` nodes (invocations).
3.  **Resolution Algorithm:**
    When a call to `target_name` is detected:
    *   **Step 1 (Local):** Check the current file's `LNM`. Is `auth_login` present? $\to$ `FQN = src.auth.manager.login`.
    *   **Step 2 (Scope):** If not in `LNM`, check the current `Scope` (is the call within a class method? If so, search for `self.target_name` within that class).
    *   **Step 3 (Global):** If not resolved locally, search the `Global Symbol Table` for global functions.
    *   **Step 4 (Match):** If an `FQN` is found in the `SymbolTable` $\to$ **Create Edge**. If not $\to$ **Create "Unresolved Call"** (critical for debugging and agent awareness).

---

## 4. 🛠️ Data Structures (Pseudo-code)

```python
class SymbolTable:
    # Key: FQN, Value: Node ID in Graph
    symbols: Dict[str, str] 

class LocalNamespace:
    # Key: Local Name (e.g., 'f'), Value: FQN (e.g., 'mod.func')
    mapping: Dict[str, str]
    current_file: str

class Resolver:
    def resolve_call(self, call_node, current_file_context, current_scope):
        name = call_node.name
        
        # 1. Try Local Namespace (Imports)
        fqn = current_file_context.mapping.get(name)
        
        # 2. Try Class Scope (self.method)
        if not fqn and current_scope.is_inside_class:
            fqn = current_scope.resolve_member(name)
            
        # 3. Try Global
        if not fqn:
            fqn = global_symbol_table.lookup(name)
            
        return fqn
```

---

## 5. ⚠️ Edge Cases & Complexity (The "Real World" Part)

### A. Python Dynamic Behavior
*   **Problem:** `getattr(obj, "method_name")()` or `func = get_func(); func()`.
*   **MVP Solution:** Deterministic resolution is not attempted for these cases. We tag such relationships as `Edge(type=DYNAMIC_CALL, confidence=0.1)`, alerting agents to the presence of "dynamic magic."

### B. TypeScript/Go Modules
*   **TypeScript:** Requires accounting for `tsconfig.json` configuration (`paths`/`baseUrl`).
*   **Go:** Requires observing `package` names and visibility rules (Exported/Capitalized vs. internal/lowercase).
*   **Solution:** Introduce a "Language Specific Resolver" in `Phase 2` to load build configurations.

### C. Circular Dependencies
*   Scenario: `A.py` imports `B.py`, and `B.py` imports `A.py`.
*   **Solution:** The Three-Pass architecture inherently solves this, as `Phase 1` (Indexing) is completed across all files before any linking in `Phase 3` begins.

---

## 6. 📈 Success Metrics for Resolution Layer
How do we measure the efficacy of the Resolver?

1.  **Unresolved Rate:** The percentage of calls that fail to bind to an FQN (should approach zero in well-structured projects).
2.  **Collision Rate:** The frequency of distinct calls to the same name incorrectly resolving to the same `target_id` (should be zero).
3.  **FQN Completeness:** Ensuring all graph nodes possess a fully qualified path rather than just a local identifier.
