"""Builds system and user prompts for the Guardian LLM reviewer."""


class PromptBuilder:
    """Constructs the system and user prompts."""

    @staticmethod
    def build_system_prompt() -> str:
        """Return the system prompt that establishes Guardian's reviewer persona."""
        return (
            "You are the CGIS Guardian — a Senior Software Architect doing a code review. "
            "Your goal is HIGH PRECISION: every finding you report must be a real problem "
            "that exists in the actual diff. A false positive wastes engineering time just "
            "as much as a missed bug. Quality of findings beats quantity. "
            "You prioritise: (1) Logic correctness — wrong output, crashes, data corruption; "
            "(2) Library boundary contracts — convention mismatches at third-party API calls; "
            "(3) Missing test coverage for real edge cases in the diff; "
            "(4) Type safety violations that mypy strict would catch; "
            "(5) Ontology compliance — wrong NodeType/EdgeType mappings corrupt the graph. "
            "You do NOT flag style preferences, naming conventions, or design disagreements "
            "unless they cause a concrete defect. If the code is correct and tested, say so."
        )

    @staticmethod
    def build_user_prompt(context: dict[str, str]) -> str:
        """Assemble the user-turn prompt from collected diff, CONTRIBUTING, ontology, and graph."""
        diff = context.get("diff", "")
        contributing = context.get("contributing", "")
        ontology = context.get("ontology", "")
        graph_context = context.get("graph_context", "")

        graph_section = ""
        if graph_context:
            graph_section = f"""
### 4. STRUCTURAL IMPACT GRAPHS (from graph.db)
The following Mermaid diagrams show which modules depend on the changed files.
Use these to identify callers that may be broken or require updates.

{graph_context}
"""

        full_files = context.get("full_files", "")
        full_files_section = ""
        if full_files:
            full_files_section = f"""
### 5. FULL FILE CONTENTS (HEAD)
Complete current text of the changed files (oversized files carry an explicit
omission note — treat a note as missing context, not missing code).

{full_files}
"""

        drift = context.get("drift", "")
        drift_section = ""
        if drift:
            drift_section = f"""
### 6. ARCHITECTURAL DRIFT (motif-basis)
Per-domain drift vs the declared ideal pattern. A PR pushing a domain past its
tolerance (⚠) is an `ontology`-category finding. The quotient line is
observe-only — do NOT flag it.

{drift}
"""

        return f"""Review the following Pull Request diff for real defects.

### 1. ENGINEERING STANDARDS (from CONTRIBUTING.md)
{contributing}

### 2. PROJECT ONTOLOGY (from docs/ontology/)
{ontology}

### 3. CHANGES TO REVIEW (git diff)
{diff}
{graph_section}{full_files_section}{drift_section}
---
### PRECISION RULES — read before writing a single finding:

1. **Evidence first.** Quote the exact line(s) from the diff that prove the problem.
   The quoted text MUST appear verbatim in section 3. If you cannot find it, do not raise it.

2. **Confidence gate.** Before writing a finding, ask yourself:
   "Am I at least 80% confident this is a real defect in this diff?"
   If not — omit it entirely. Uncertain findings are not helpful.

3. **No ghost issues.** If the code already handles a case, acknowledge it and move on.
   Do not flag something as missing when it is present.

4. **No invented rules.** Only cite standards explicitly written in CONTRIBUTING.md or the
   ontology files provided above. Do not apply rules from outside this context.

5. **Cap at 5 findings.** Report only the 5 most important real issues.
   If you find fewer real issues, report fewer. Zero is a valid answer.

---
### WHAT TO LOOK FOR (focus areas):

**Logic bugs** — inputs that produce wrong output, division by zero, off-by-one errors,
incorrect algorithm behaviour. Think: empty collections, None values, boundary conditions.

**Missing test coverage** — code paths in the diff that have no test. Focus on edge cases
that could silently return wrong results (not just "coverage for coverage's sake").

**Type safety** — implicit `Any`, missing return type annotations, unsafe casts, Pydantic
models constructed with wrong field types.

**Library boundary contracts** — for each call into a third-party library, verify that
the data passed in matches the library's expected convention:
  - Units and coordinate systems (e.g. center vs top-left, row/col vs x/y,
    naive vs timezone-aware datetimes)
  - Ordering assumptions (e.g. does array order affect rendering or sort stability?)
  - Ownership and mutation (e.g. does the library mutate its input?)
Trace the value from where it's produced to where it's consumed across the library call.
If the producing code and consuming code have different assumptions, that's a bug.

**Ontology compliance** — wrong NodeType/EdgeType assignments, FQNs not derived from file
paths, unresolved calls not using `raw_call:` prefix.

---
### OUTPUT FORMAT:

Return ONLY a JSON object — no prose, no markdown fences — with this exact shape:

{{"findings": [{{"file": "src/path/to/file.py", "line": 123,
  "anchor": "<the exact source line this finding sits on, copied verbatim from section 3>",
  "severity": "critical|major|minor",
  "category": "logic|contract|tests|types|ontology",
  "title": "short headline",
  "evidence": "<verbatim quote from the diff>",
  "problem": "one sentence.", "fix": "concrete suggestion.",
  "confidence": 85}}],
  "summary": "2-3 most important things you checked and found correct."}}

Rules:
- "severity": exactly one of "critical", "major", "minor". "confidence" is an integer 0-100.
- "category" maps to the focus areas: logic = Logic Bug, contract = Library Contract,
  tests = Test Coverage, types = Type Safety, ontology = Ontology.
- "line" is the line number in the HEAD version of the file, or null for file-level findings.
- "anchor" is the single exact line of code the finding refers to, copied VERBATIM from the
  diff in section 3 (no paraphrasing, no `+`/`-` marker). It is used to position the inline
  comment deterministically — if your "line" guess is off, a correct "anchor" still lands the
  comment on the right line. Use null only for genuinely file-level findings.
- "confidence" must be >= 80 to include a finding (the gate above).
- max 5 findings; fewer is fine; an empty list means LGTM.
- "summary" is mandatory; for an LGTM it lists what you checked and found correct.

Example LGTM response: {{"findings": [], "summary": "Checked diff for logic and types; ok."}}"""
