"""Abstract base class for LLM provider implementations."""

import abc
import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import ClassVar

import httpx
import structlog
from pydantic import BaseModel, computed_field

log = structlog.getLogger(__name__)

#: Request timeout in SECONDS. Mistral and Gemini take milliseconds and Ollama
#: takes seconds, so each provider converts at its own call site — the unit is
#: visible where the conversion happens rather than buried in a constant's name.
DEFAULT_REQUEST_TIMEOUT = 180.0

#: Total attempts per call, including the first.
MAX_ATTEMPTS = 3

#: Backoff base: sleeps are BACKOFF_BASE ** attempt — 2 s, then 4 s.
BACKOFF_BASE = 2.0

#: Retried transport failures. Deliberately the same set the Mistral SDK itself
#: retries (mistralai/client/utils/retries.py) rather than a list invented here.
#: google-genai reraises httpx exceptions too, and httpx.NetworkError is the
#: parent of ConnectError, so this covers every provider.
RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.NetworkError,
)


class ProviderUsage(BaseModel, frozen=True):
    """Token usage reported by the LLM after a single generate_content call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_tokens(self) -> int:
        """Total tokens consumed by this LLM request, used for cost tracking."""
        return self.prompt_tokens + self.completion_tokens


class BaseProvider(abc.ABC):
    """Abstract base class for LLM providers."""

    #: The GUARDIAN_PROVIDER value that selects this provider. A bare
    #: annotation (no default) so a provider is not handed one it never
    #: claimed; `__init_subclass__` below is what actually stops a concrete
    #: provider from omitting it — a plain ClassVar annotation is not enforced
    #: by mypy across subclasses (verified: a subclass that omits it type-checks
    #: clean under `mypy --strict`). Without that runtime check the omission
    #: would surface only as an AttributeError wherever `.name` is first
    #: read — which, in the work this exists for, is inside the review
    #: fingerprint's digest scoping (#375 §3.3), silently computing the digest
    #: over the wrong module. Previously three call sites sniffed the type
    #: with isinstance instead of asking; one of them reported Ollama as
    #: gemini.
    name: ClassVar[str]

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Refuse a concrete provider that has not declared its own `name`.

        Raised at class-definition time, not at first use — see the `name`
        docstring above for why that matters.

        An abstract intermediate (a shared base for two concrete providers
        that itself still has unimplemented abstract methods) is exempt: it
        has no business declaring a name of its own. `cls.__abstractmethods__`
        cannot answer that here, though — ABCMeta computes it only after
        `type.__new__` returns, and `__init_subclass__` runs *during*
        `type.__new__`, before that attribute exists on `cls` at all
        (confirmed by reproducing an AttributeError on it from inside this
        hook). `inspect.isabstract` is used instead: it special-cases exactly
        this ordering, falling back to deriving abstractness by hand when
        `__abstractmethods__` is not yet set, which is precisely the situation
        here.
        """
        super().__init_subclass__(**kwargs)
        if inspect.isabstract(cls) or hasattr(cls, "name"):
            return
        _msg = (
            f"{cls.__name__} must declare `name: ClassVar[str]` — one of the "
            'GUARDIAN_PROVIDER values: "gemini", "mistral", "ollama", "openrouter". Add '
            f'`name: ClassVar[str] = "..."` as the first line of '
            f"{cls.__name__}'s class body."
        )
        raise TypeError(_msg)

    def __init__(self) -> None:
        """Initialise usage counters to zero.

        last_usage reflects the most recent LLM call; cumulative_usage sums
        every call this provider instance has made (a chunked review makes
        N finder calls — and even a single-pass review makes 2 on a parse
        retry, whose first call last_usage used to silently drop).
        """
        self.last_usage: ProviderUsage = ProviderUsage()
        self.cumulative_usage: ProviderUsage = ProviderUsage()

    def _record_usage(self, usage: ProviderUsage) -> None:
        """Record one call's token usage: set last_usage, add to cumulative."""
        self.last_usage = usage
        self.cumulative_usage = ProviderUsage(
            prompt_tokens=self.cumulative_usage.prompt_tokens + usage.prompt_tokens,
            completion_tokens=self.cumulative_usage.completion_tokens + usage.completion_tokens,
        )

    async def _sleep(self, seconds: float) -> None:
        """Wait between retries. Overridden in tests so they do not actually wait."""
        await asyncio.sleep(seconds)

    async def _retry(self, call: Callable[[], Awaitable[str]]) -> str:
        """Run call, retrying transient transport failures with exponential backoff.

        Retries only RETRYABLE_EXCEPTIONS: an auth or validation failure must
        fail on the first attempt rather than burn MAX_ATTEMPTS calls. The final
        exception propagates unchanged so callers' existing degradation paths
        still see a real error (#275).
        """
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return await call()
            except RETRYABLE_EXCEPTIONS as exc:
                if attempt == MAX_ATTEMPTS:
                    log.warning(
                        "Provider call failed; retries exhausted.",
                        attempts=MAX_ATTEMPTS,
                        error=repr(exc),
                    )
                    raise
                delay = BACKOFF_BASE**attempt
                log.warning(
                    "Provider call failed; retrying.",
                    attempt=attempt,
                    of=MAX_ATTEMPTS,
                    delay_s=delay,
                    error=repr(exc),
                )
                await self._sleep(delay)
        # Unreachable: the loop either returns or raises on the last attempt.
        raise AssertionError

    @abc.abstractmethod
    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Send a prompt to the LLM and return the text response."""

    @abc.abstractmethod
    async def generate_structured(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        """Send a prompt requesting JSON conforming to schema; return raw JSON text."""
