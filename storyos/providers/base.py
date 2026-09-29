"""Provider-agnostic model interface.

One class per API family. The rest of Story OS never imports a vendor SDK
directly -- it asks for a tier ("cheap", "mid", "prose") and gets back a
Completion. Swapping Opus for GLM-5.2 or a local Ollama model is a config
edit, not a code edit.

Every call returns usage and a cost estimate, because a 40-chapter run makes
a hundred calls and you should be able to see where the money went.
"""

from __future__ import annotations

import dataclasses
import os
import time
from abc import ABC, abstractmethod
from typing import Any, Iterable


class ProviderError(RuntimeError):
    """A call failed in a way retrying will not fix (bad key, bad model name)."""


class TransientError(RuntimeError):
    """Rate limit, overload, or network blip. Worth retrying with backoff."""


@dataclasses.dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_read_tokens: int = 0
    cached_write_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cached_read_tokens + other.cached_read_tokens,
            self.cached_write_tokens + other.cached_write_tokens,
        )


@dataclasses.dataclass(slots=True)
class Completion:
    text: str
    model: str
    provider: str
    usage: Usage
    cost_usd: float
    latency_s: float
    stop_reason: str | None = None
    raw: Any = dataclasses.field(default=None, repr=False)

    @property
    def truncated(self) -> bool:
        """True if the model ran out of output budget mid-sentence.

        Worth checking on every prose call -- a chapter that stops at
        max_tokens looks superficially fine and is silently half-written.
        """
        return self.stop_reason in {"max_tokens", "length"}


@dataclasses.dataclass(frozen=True, slots=True)
class ModelSpec:
    """A model plus what it costs, so cost reporting needs no lookup table elsewhere."""

    provider: str
    model: str
    usd_per_m_input: float
    usd_per_m_output: float
    # Anthropic bills cache writes at 1.25x input and cache reads at 0.1x.
    usd_per_m_cache_write: float | None = None
    usd_per_m_cache_read: float | None = None
    max_output_tokens: int = 8192
    context_window: int = 200_000

    def cost(self, usage: Usage) -> float:
        write_rate = (
            self.usd_per_m_cache_write
            if self.usd_per_m_cache_write is not None
            else self.usd_per_m_input * 1.25
        )
        read_rate = (
            self.usd_per_m_cache_read
            if self.usd_per_m_cache_read is not None
            else self.usd_per_m_input * 0.1
        )
        return (
            usage.input_tokens * self.usd_per_m_input
            + usage.output_tokens * self.usd_per_m_output
            + usage.cached_write_tokens * write_rate
            + usage.cached_read_tokens * read_rate
        ) / 1_000_000


class Provider(ABC):
    """Implement `_call` and `name`; retry and cost accounting come free."""

    name: str = "abstract"
    max_retries: int = 5

    def __init__(self, spec: ModelSpec, api_key: str | None = None, base_url: str | None = None):
        self.spec = spec
        self.base_url = base_url
        self._api_key = api_key

    # -- subclass contract -------------------------------------------------

    @abstractmethod
    def _call(
        self,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        cache_system: bool,
    ) -> tuple[str, Usage, str | None, Any]:
        """Return (text, usage, stop_reason, raw_response)."""

    # -- shared behaviour --------------------------------------------------

    def complete(
        self,
        system: str,
        user: str | Iterable[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float = 1.0,
        cache_system: bool = True,
    ) -> Completion:
        messages = (
            [{"role": "user", "content": user}]
            if isinstance(user, str)
            else list(user)
        )
        budget = max_tokens or self.spec.max_output_tokens

        delay = 2.0
        last: Exception | None = None
        for attempt in range(self.max_retries):
            started = time.monotonic()
            try:
                text, usage, stop, raw = self._call(
                    system, messages, budget, temperature, cache_system
                )
            except TransientError as exc:
                last = exc
                if attempt == self.max_retries - 1:
                    break
                time.sleep(delay)
                delay *= 2
                continue

            return Completion(
                text=text,
                model=self.spec.model,
                provider=self.name,
                usage=usage,
                cost_usd=self.spec.cost(usage),
                latency_s=time.monotonic() - started,
                stop_reason=stop,
                raw=raw,
            )

        raise ProviderError(
            f"{self.name}/{self.spec.model} failed after {self.max_retries} attempts: {last}"
        ) from last

    def _require_key(self, env_var: str) -> str:
        key = self._api_key or os.environ.get(env_var)
        if not key:
            raise ProviderError(
                f"{env_var} is not set. Copy .env.example to .env and fill it in; "
                f"Story OS never reads keys from anywhere but the environment."
            )
        return key
