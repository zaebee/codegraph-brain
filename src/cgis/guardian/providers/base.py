import abc


class BaseProvider(abc.ABC):
    """Abstract base class for LLM providers."""

    @abc.abstractmethod
    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Sends a prompt to the LLM and returns the text response."""


class GeminiProvider(BaseProvider):
    """Implementation of BaseProvider using Google Gemini API."""

    def __init__(self, api_key: str, model_name: str = "gemini-1.5-pro"):
        self.api_key = api_key
        self.model_name = model_name

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        # Mocking the actual API call for now to allow testing without a key
        # In real usage:
        # import google.generativeai as genai
        # genai.configure(api_key=self.api_key)
        # self.model = genai.GenerativeModel(self.model_name)
        # response = self.model.generate_content(f"{system_prompt}\\n\\n{user_prompt}")
        # return response.text
        return (
            f"[MOCK GEMINI RESPONSE for model {self.model_name}]\nReviewing: {user_prompt[:50]}..."
        )
