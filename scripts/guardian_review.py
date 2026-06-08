import asyncio
import os
from pathlib import Path

from cgis.guardian.collector import ContextCollector
from cgis.guardian.core import GuardianReviewer
from cgis.guardian.providers.gemini import GeminiProvider


async def main():
    # In a real usage, API key would come from an environment variable
    api_key = os.getenv("GEMINI_API_KEY", "mock_key")
    project_root = Path(__file__).parent.parent.parent.absolute()

    provider = GeminiProvider(api_key=api_key)
    collector = ContextCollector(project_root=project_root)
    reviewer = GuardianReviewer(provider=provider, context_collector=collector)

    review_result = await reviewer.run_review()

    print("\n" + "=" * 30)
    print("🛡️  GUARDIAN REVIEW RESULT")
    print("=" * 30 + "\n")
    print(review_result)


if __name__ == "__main__":
    asyncio.run(main())
