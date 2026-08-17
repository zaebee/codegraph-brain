"""Each provider states its own name, so nothing has to sniff its type."""

import abc
from typing import ClassVar

import pytest
from pydantic import BaseModel

from cgis.guardian.providers.base import BaseProvider
from cgis.guardian.providers.gemini import GeminiProvider
from cgis.guardian.providers.mistral import MistralProvider
from cgis.guardian.providers.ollama import OllamaProvider


def test_every_provider_names_itself() -> None:
    assert GeminiProvider.name == "gemini"
    assert MistralProvider.name == "mistral"
    assert OllamaProvider.name == "ollama"


def test_names_match_the_runner_vocabulary() -> None:
    """The names are the GUARDIAN_PROVIDER values, not free-form labels.

    `build_provider` dispatches on exactly these three strings, so a provider
    whose name does not appear there cannot be selected and its module would be
    pruned out of every fingerprint.
    """
    assert {GeminiProvider.name, MistralProvider.name, OllamaProvider.name} == {
        "gemini",
        "mistral",
        "ollama",
    }


def test_base_declares_the_attribute() -> None:
    """Declared on the base so a new provider that forgets it fails type-check.

    `name` is a bare `ClassVar[str]` on BaseProvider (no default) — deliberately,
    so a subclass must supply its own. That means it never becomes a real
    attribute on BaseProvider itself, only an entry in its `__annotations__`;
    `hasattr(BaseProvider, "name")` is therefore False even though the
    declaration is present, so the annotation is what this test checks.
    """
    assert "name" in BaseProvider.__annotations__


def test_ollama_is_not_reported_as_gemini() -> None:
    """The bug the isinstance sniff had: only Mistral was checked for.

    An Ollama provider fell into the else branch and was announced as gemini,
    which then selected the wrong default skeptic.
    """
    provider = OllamaProvider(model_name="codellama:13b")
    assert provider.name == "ollama"


def test_concrete_subclass_without_name_raises_at_class_definition() -> None:
    """A provider that forgets `name` is refused when the class is defined.

    Without this, the omission would surface only as an AttributeError
    wherever `.name` is first read — which, in the review-fingerprint work
    this exists for (#375 Task 1), is inside the digest scoping.
    """
    with pytest.raises(TypeError, match="must declare `name"):

        class _Nameless(BaseProvider):
            async def generate_content(
                self,
                system_prompt: str,  # noqa: ARG002
                user_prompt: str,  # noqa: ARG002
            ) -> str:
                """Unreachable: class definition raises before instantiation."""
                return ""

            async def generate_structured(
                self,
                system_prompt: str,  # noqa: ARG002
                user_prompt: str,  # noqa: ARG002
                schema: type[BaseModel],  # noqa: ARG002
            ) -> str:
                """Unreachable: class definition raises before instantiation."""
                return ""


def test_concrete_subclass_with_name_does_not_raise() -> None:
    """A provider that declares `name` defines cleanly, same as the real three."""

    class _Named(BaseProvider):
        name: ClassVar[str] = "gemini"

        async def generate_content(
            self,
            system_prompt: str,  # noqa: ARG002
            user_prompt: str,  # noqa: ARG002
        ) -> str:
            """Unreachable: never called by this test."""
            return ""

        async def generate_structured(
            self,
            system_prompt: str,  # noqa: ARG002
            user_prompt: str,  # noqa: ARG002
            schema: type[BaseModel],  # noqa: ARG002
        ) -> str:
            """Unreachable: never called by this test."""
            return ""

    assert _Named.name == "gemini"


def test_abstract_intermediate_without_name_does_not_raise() -> None:
    """A shared base for two concrete providers has no business naming itself.

    It is still abstract (it does not implement both of BaseProvider's
    abstract methods), so the guard must not demand a `name` from it — only
    from the concrete leaf that finally implements everything.
    """

    class _SharedBase(BaseProvider, abc.ABC):
        async def generate_content(
            self,
            system_prompt: str,  # noqa: ARG002
            user_prompt: str,  # noqa: ARG002
        ) -> str:
            """Still abstract: generate_structured is not implemented here."""
            return ""

    assert "name" not in _SharedBase.__dict__
