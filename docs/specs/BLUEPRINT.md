# 🏗️ Blueprint: Code Graph Resolution Engine (CGRE)

## 1. 🧠 Memory Model (Data Structures)

To establish "who is who," the engine utilizes three types of memory structures.

### A. Global Symbol Table (GST) — "The Universe"
A global registry of all declared entities within the project.
*   **Key:** `FQN` (Fully Qualified Name), e.g., `project.core.auth.UserService`
*   **Value:** `NodeID` (Graph reference) + `Metadata` (Type, File Path, Source Coordinates).
*   *Purpose:* Provide immediate verification of an entity's existence project-wide.

### B. Local Namespace Map (LNM) — "The Translator"
A file-specific dictionary mapping local identifiers to global FQNs.
*   **Key:** `local_name` (e.g., `auth_user`)
*   **Value:** `FQN` (e.g., `project.core.auth.User`)
*   *Purpose:* Resolve alias complexity (e.g., `import x as y`) and standard imports.

### C. Scope Stack — "The Navigator"
Dynamic memory utilized during the traversal of a specific file. It tracks the current hierarchical "depth."
*   **Stack Item:** <code>{ type: (CLASS|FUNCTION|METHOD|MODULE), fqn: string, local_vars: List[string] }</code>
*   *Example stack within a method:*
    1. `[Module: project.app]`
    2. `[Class: AppService]`
    3. `[Method: run_task]` $\leftarrow$ *Current Scope*

---

## 2. 🔄 The Three-Pass Pipeline (Workflow)

The engine operates in three distinct phases. Relationship linking cannot occur until indexing is complete.

### Pass 1: Discovery (The Indexer)
**Goal:** Populate the `GST`.
1. Iterate through all source files.
2. For every `class_definition` and `function_definition`, compute their unique `FQN` based on file path and nesting hierarchy.
3. Register these in the `GST`.
4. *Outcome:* A comprehensive index of all available entities (`A`, `B`, `C`).

### Pass 2: Translation (The Mapper)
**Goal:** Populate the `LNM` for each individual file.
1. Iterate through source files.
2. Identify `import_statement` and `import_from` nodes.
3. For each import, record the mapping in the file's `LNM`: `local_name` $\to$ `target_FQN`.
4. *Outcome:* In `main.py`, the engine knows that the name `auth` refers to `project.core.auth`.

### Pass 3: Linking (The Resolver) — "The Core"
**Goal:** Identify `Call` nodes and establish `Edge` relations.
1. Iterate through source files.
2. Initialize an empty `Scope Stack` for each file.
3. Traverse the AST; `push` to the stack upon entering a `class` or `def`, and `pop` upon exiting.
4. When a `Call(name)` node is encountered, execute the **Resolution Logic**.

---

## 3. ⚡ Resolution Logic (The Decision Tree)

Upon encountering a call to `target_name`, the engine executes the following decision process.

**Inputs:** `target_name` (e.g., `get_user`), `current_file_context`, `current_scope_stack`.

### Step 1: Qualified Call Check
*Applies if the code uses `math.sqrt()` or `self.do_work()`:*
1. Split into `prefix` (`math` or `self`) and `name` (`sqrt` or `do_work`).
2. **If `prefix == 'self'`:**
   - Retrieve `current_scope` from stack. If inside class `X`, then `Target_FQN = X.do_work`.
3. **If `prefix` is any other identifier (e.g., `math`):**
   - Lookup `prefix` in the current file's `LNM`.
   - If found: `prefix_fqn = LNM[prefix]`.
   - `Target_FQN = prefix_fqn + "." + name`.
4. **If not found in `LNM`:**
   - Tag as `Unresolved_Qualified_Call`.

### Step 2: Unqualified Call Check (Local/Global)
*Applies if the code uses a simple `get_user()` call:*
1. **LNM Check:** Is `get_user` defined in the file's `LNM`? (Indicates an import).
   - If yes $\to$ `Target_FQN = LNM['get_user']`.
2. **Scope Check:** Is `get_user` present in `local_vars` at the current stack level? (Indicates a local variable or nested function).
   - If yes $\to$ `Target_FQN = current_scope.fqn + ".get_user"`.
3. **Class Member Check:** If inside class `X`, does `X` have a method named `get_user`?
   - If yes $\to$ `Target_FQN = X.get_user`.
4. **Global Check:** Is `get_user` registered in the `GST` as a global function?
   - If yes $\to$ `Target_FQN = GST['get_user']`.

### Step 3: Finalization
*   If `Target_FQN` is successfully resolved in the `GST` $\to$ **Create Edge (CALLS) with confidence=1.0**.
*   If not found $\to$ **Create Edge (UNKNOWN_CALL) with confidence=0.1**.

---

## 4. 🧪 The "Self-Parsing" Test (The Ultimate Validation)

To validate the engine, we feed it its own source code as input.

**Expected Outcome:**
1. `GST` must contain FQNs for all resolution engine modules.
2. `LNM` must correctly resolve `import tree_sitter`.
3. The `Linking Pass` must identify the relationship: `ResolutionEngine.resolve_call` $\to$ `CALLS` $\to$ `AST_Node.name`.

**If the generated self-graph matches the actual directory and file structure, the engine is functioning perfectly.**
