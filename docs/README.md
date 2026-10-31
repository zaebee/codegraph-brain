# Documentation

Where a document lives says what it is for. Two rules keep the tree usable:
**a design record is dated**, and **a finished record moves to the archive
unchanged**.

## Living documents

| Location | What belongs there |
|---|---|
| `docs/architecture/` | Generated: `HOW_IT_WORKS.md` (the pipeline graph CGIS parses out of itself), the schema section, `ONTOLOGY.md`, `PATTERNS_AND_TRIADS.md`, `SELF_PORTRAIT.md`, the health badge. Do not hand-edit the generated sections. |
| `docs/how-to/` | Task guides. `MCP_REFERENCE.md` is generated from the MCP tool schemas; `CLI_USAGE.md` is hand-written. |
| `docs/specs/` | Current design records: `BLUEPRINT.md`, `PRD.md`, `TDD.md`, `core/ontology.yml` (machine-read clauses), and dated design specs from 2026-09 on. |
| `docs/plans/` | Active plans, including multi-step `*-stepN.md` series. |
| `docs/lab-notes/` | Measurements and experiments — the evidence behind a decision, dated. |
| `docs/ontology/` | Machine-read inputs (`patterns.yaml`, `domains.yaml`, `core.yaml`). Not prose: change them with the code that reads them. |

## Archive

`docs/archive/` holds completed design records and plans, kept for the "why"
behind shipped code. The documents are frozen: no edits, no generated
cross-references, no place in `docs/INDEX.md`. Move a record there — unchanged —
once its plan has shipped and the rationale is no longer needed while working.

## Conventions

- **New design record:** `docs/specs/YYYY-MM-DD-<slug>-design.md`. Its plan is
  `docs/plans/YYYY-MM-DD-<slug>.md`, or a `*-stepN.md` series for multi-step work.
- **Status line:** say what the document is, near the top, as a blockquote:
  `> **Status:** approved (2026-07-30)`. Past tense for what shipped.
- **Generated links:** after editing a living document, run `make docs-links`.
  It writes the `Related:` / `Code:` block and `docs/INDEX.md`; CI refreshes the
  same output on every push to `main`, so a stale block never lands.
- **Archived documents are not regenerated.** Strip nothing, edit nothing: if a
  record needs correcting, write a living note that points at it.
