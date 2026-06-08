from cgis.guardian.collector import ContextCollector
from cgis.guardian.prompts import PromptBuilder
from cgis.guardian.providers.base import BaseProvider


class GuardianReviewer:
    """Orchestrates the entire review process."""

    def __init__(self, provider: BaseProvider, context_collector: ContextCollector) -> None:
        self.provider = provider
        self.context_collector = context_collector
        self.prompt_builder = PromptBuilder()

    async def run_review(self) -> str:
        """Executes the full review workflow."""
        print("🔍 Collecting context...")
        context = self.context_collector.collect_all()

        print("🏗️ Building prompts...")
        system_prompt = self.prompt_builder.build_system_prompt()
        user_prompt = self.prompt_builder.build_user_prompt(context)

        print("🤖 Invoking LLM...")
        return await self.provider.generate_content(system_prompt, user_prompt)
