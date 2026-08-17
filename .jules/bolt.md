## 2026-08-11 - Grouping AST node checks in recursive walker
**Learning:** Grouping node-type specific checks under one `node.type == "..."` test in a hot recursive walker (`_walk`) keeps the branching readable and stops a predicate being called on node types it can never match.

**What was measured (2026-08-17):** the graph is byte-identical before and after — 751 nodes, 1891 edges over a frozen 32-file corpus. The speed-up is not demonstrated: six alternating paired runs gave the new version faster in 3 of 6, median +1.9%, range -1.6% to +13.5%. The gains sit in the early pairs and vanish once warm, which is the shape of noise. That fits the mechanism — the avoided work is one call doing one string comparison, on nodes that had already failed four earlier branch tests.

**Action:** group node-type checks for clarity, and do not record a speed-up without a paired measurement. An unmeasured rule gets applied again; this note originally read "massive CPU cycle savings" and had no measurement behind it.
