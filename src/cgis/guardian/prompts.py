"""Builds system and user prompts for the Guardian LLM reviewer."""


class PromptBuilder:
    """Constructs the system and user prompts."""

    @staticmethod
    def build_system_prompt() -> str:
        """Return the system prompt that establishes Guardian's reviewer persona."""
        return (
            "You are the CGIS Guardian, a Senior Software Architect and Code Quality Specialist. "
            "Your mission is to review Pull Requests for the CodeGraph Brain (CGIS) project. "
            "You prioritize Correctness, Type Safety, and Determinism. "
            "You are not a syntax checker; you are a semantic and architectural reviewer. "
            "Your goal is to ensure all changes align with the project's Engineering Standards "
            "and Ontology."
        )

    @staticmethod
    def build_user_prompt(context: dict[str, str]) -> str:
        """Assemble the user-turn prompt from collected diff, CONTRIBUTING, and ontology."""
        diff = context.get("diff", "")
        contributing = context.get("contributing", "")
        ontology = context.get("ontology", "")

        return f"""Please review the following Pull Request based on the provided context.

### 1. ENGINEERING STANDARDS (from CONTRIBUTING.md)
{contributing}

### 2. PROJECT ONTOLOGY (from docs/ontology/)
{ontology}

### 3. CHANGES TO REVIEW (git diff)
{diff}

---
### INSTRUCTIONS:
1. Analyze the changes against the standards and ontology.
2. Identify any architectural violations, type safety issues (like prohibited use of 'Any'),
   or documentation gaps.
3. Provide your review in a clean Markdown format.
4. For each issue found, provide a clear reason and a suggested fix.
5. If everything looks perfect, provide a brief 'LGTM' with praise for the quality.
6. Be constructive, professional, and concise."""
