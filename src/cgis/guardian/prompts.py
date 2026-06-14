"""Builds system and user prompts for the Guardian LLM reviewer."""


class PromptBuilder:
    """Constructs the system and user prompts."""

    @staticmethod
    def build_system_prompt() -> str:
        """Return the system prompt that establishes Guardian's reviewer persona."""
        return (
            "You are the CGIS Guardian finder — a Senior Software Architect hunting for "
            "defects in a code review. You are the FIRST of two stages: a separate skeptic "
            "verifier runs after you and removes false positives. Because of that division "
            "of labour, your job is RECALL — surface every plausible real defect. A missed "
            "bug is the expensive error here; a borderline finding is cheap, because the "
            "skeptic filters it. Surface a finding whenever you can name a CONCRETE failure "
            "scenario — a specific input, state, timing, or platform that makes the code "
            "wrong. Do not self-censor because you are unsure: hand the uncertainty to the "
            "skeptic with your reasoning, do not suppress it. "
            "You prioritise: (1) Logic correctness — wrong output, crashes, data corruption; "
            "(2) Library boundary contracts — convention mismatches at third-party API calls; "
            "(3) Missing test coverage for real edge cases in the diff; "
            "(4) Type safety violations that mypy strict would catch; "
            "(5) Ontology compliance — wrong NodeType/EdgeType mappings corrupt the graph. "
            "You still do NOT flag style preferences, naming conventions, or design "
            "disagreements that have no concrete failure scenario. If the code is correct "
            "and tested, say so."
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

        contributing_section = ""
        if contributing:
            contributing_section = f"""### 1. ENGINEERING STANDARDS (from CONTRIBUTING.md)
{contributing}

"""

        ontology_section = ""
        if ontology:
            ontology_section = f"""### 2. PROJECT ONTOLOGY (from docs/ontology/)
{ontology}

"""

        return f"""Review the following Pull Request diff for real defects.

{contributing_section}{ontology_section}### 3. CHANGES TO REVIEW (git diff)
{diff}
{graph_section}{full_files_section}{drift_section}
---
### HUNTING RULES — read before writing a single finding:

1. **Evidence first.** Quote the exact line(s) from the diff that the finding sits on.
   The quoted text MUST appear verbatim in section 3 (it positions the inline comment).
   If you cannot find the line, do not raise it.

2. **Surface on a nameable failure scenario.** Before writing a finding, ask yourself:
   "Can I describe one concrete input, state, timing, or platform where this code
   misbehaves?" If yes — REPORT it, and put your honest `confidence` in the field.
   Confidence does NOT gate inclusion: a 40%-confident finding with a real failure
   scenario is worth surfacing, because the skeptic verifier decides what to keep.
   Only drop a candidate when you cannot construct ANY failure scenario for it.

3. **No ghost issues.** If the code already handles a case, acknowledge it and move on.
   Do not flag something as missing when it is present (this is a wrong finding, not
   an uncertain one — the skeptic cannot rescue precision from a fabricated claim).

4. **No invented rules.** Only cite standards explicitly written in CONTRIBUTING.md or the
   ontology files provided above. Do not apply rules from outside this context.

5. **No finding cap.** Report every real candidate you find — more genuine findings is
   strictly better here, since recall is your job and the skeptic trims the list. Order
   them most-severe first. Zero is still a valid answer when the diff is clean.

6. **Reason per changed function before deciding.** For each function the diff touches,
   briefly walk these before you write findings for it:
   - What are its inputs and where do they come from (caller, config/YAML/JSON, env,
     request)? Is any of them external/untrusted?
   - What happens on the awkward inputs: empty / None / zero / a non-dict where a dict
     is assumed / a scalar where an iterable is assumed / unsorted / duplicate / oversized?
   - Did a deleted or changed line hold an invariant (a guard, a validation, an error
     path)? Is it re-established?
   Only after that walk do you decide what to surface. Skipping this walk is the main
   reason subtle defects (unvalidated data, exact-equality, dropped guards) go unseen.

---
### WHAT TO LOOK FOR (focus areas):

**Logic bugs** — inputs that produce wrong output, division by zero, off-by-one errors,
incorrect algorithm behaviour. Think: empty collections, None values, boundary conditions.

**Unvalidated external data** — a value read from config/YAML/JSON, env, or a request is
used as a `dict`/`list`/`set`/iterable (subscripted, iterated, passed to `set()`/`dict()`,
`.items()`, `for x in value`) without first checking its type or presence. A YAML key the
author expects to be a mapping can legally be a scalar or a list; a `value or {{}}` idiom
catches `None` but lets a non-dict truthy value through to operations that then misbehave
silently rather than erroring. Flag each such use that lacks a type/shape guard.

**Exact-equality on floats / money** — bare `==` or `!=` comparing floating-point or
Decimal values, *including in test assertions* (`assert x == 0.3`). Floating-point
rounding makes these flaky or wrong; they should use a tolerance compare. Check changed
test files for this too.

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
### WORKED EXAMPLES (how the per-function walk turns into a finding):

These show the kind of subtle, borderline defect that is easy to skip but worth surfacing.
Do not look for these exact lines — learn the *pattern* and apply it to the diff above.

Example A — unvalidated config value used as a mapping:
```
def layers_for(self, name: str) -> set[str]:
    cfg = self._patterns.get(name)        # cfg comes from a YAML file
    return set(cfg)                        # <-- assumes cfg is iterable-of-str
```
Walk: input `cfg` is external (YAML); the author assumes a list/mapping, but YAML lets
that key be a scalar (`name: layered`) → `set("layered")` silently yields `{{'l','a',...}}`,
not an error. → FINDING: `set(cfg)` lacks a type/shape guard on external config data.
(confidence ~50 — borderline, but it has a concrete failure scenario, so surface it.)

Example B — exact-equality on floats in a test:
```
def test_drift_score():
    assert scorer.score(fp) == 0.3        # <-- bare == on a float result
```
Walk: `score()` returns a computed float; `== 0.3` is exact float equality → rounding can
make this assert flaky/false. → FINDING: float compared with bare `==` in a test; use a
tolerance (`pytest.approx` / `math.isclose`). (confidence ~55.)

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
- "confidence" is your honest 0-100 estimate; it does NOT gate inclusion — the skeptic
  verifier uses it to prioritise, so report findings below 80 too.
- no finding cap; report every real candidate, most-severe first; an empty list means LGTM.
- "summary" is mandatory; for an LGTM it lists what you checked and found correct.

Example LGTM response: {{"findings": [], "summary": "Checked diff for logic and types; ok."}}"""
