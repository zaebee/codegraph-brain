## 2026-08-11 - Grouping AST node checks in recursive walker
**Learning:** Calling helpers like `is_module_level_assignment` on every single AST node inside recursive walker (`_walk`) creates massive unnecessary function calls. Grouping them inside `elif node.type == "assignment":` prevents checking thousands of nodes that are not assignments, yielding massive CPU cycle savings on hot paths.
**Action:** Always group node-type specific checks inside an initial `node.type == "..."` block in hot recursive AST loops before checking detailed properties.
