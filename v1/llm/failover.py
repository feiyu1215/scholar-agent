"""
llm/failover.py - Failover client wrapping multiple providers.

Provides automatic failover between LLM providers when errors occur.
Triggers failover on: HTTP 429 (rate limit), 500+ (server errors), timeouts.

Usage:
    from llm.failover import FailoverClient
    
    client = FailoverClient()
    result = await client.chat(system="...", user="...")
    # Automatically tries next provider on failure
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any, Dict, List, Optional

from .provider import ProviderConfig, get_registry


# Errors that trigger failover (provider-level, not user-level)
_FAILOVER_STATUS_CODES = {429, 500, 502, 503, 504}
_FAILOVER_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    OSError,
)


class ProviderHealth:
    """Tracks health state for a single provider."""

    def __init__(self, config: ProviderConfig):
        self.config = config
        self.consecutive_failures = 0
        self.last_failure_time: float = 0
        self.total_failures = 0
        self.total_successes = 0
        self.is_circuit_open = False
        self._circuit_open_until: float = 0

    def record_success(self):
        self.consecutive_failures = 0
        self.total_successes += 1
        self.is_circuit_open = False

    def record_failure(self):
        self.consecutive_failures += 1
        self.total_failures += 1
        self.last_failure_time = time.time()
        # Circuit breaker: open after 3 consecutive failures
        if self.consecutive_failures >= 3:
            backoff = min(60 * (2 ** (self.consecutive_failures - 3)), 300)
            self._circuit_open_until = time.time() + backoff
            self.is_circuit_open = True

    def is_available(self) -> bool:
        if not self.is_circuit_open:
            return True
        # Check if circuit cooldown has passed
        if time.time() >= self._circuit_open_until:
            self.is_circuit_open = False
            return True
        return False

    @property
    def health_score(self) -> float:
        """0.0 (worst) to 1.0 (best)."""
        total = self.total_successes + self.total_failures
        if total == 0:
            return 1.0
        return self.total_successes / total


class FailoverClient:
    """
    Multi-provider LLM client with automatic failover.
    
    Wraps the provider registry and attempts each provider in priority order.
    On transient failures, transparently retries with the next provider.
    """

    def __init__(self, max_concurrent: int = 5):
        self._registry = get_registry()
        self._max_concurrent = max_concurrent
        self._health: Dict[str, ProviderHealth] = {}
        self._clients: Dict[str, Any] = {}  # Lazy-initialized AsyncOpenAI instances
        self._semaphore = asyncio.Semaphore(max_concurrent)

        # Initialize health tracking
        for config in self._registry.get_by_priority():
            self._health[config.name] = ProviderHealth(config)

    def _get_client(self, config: ProviderConfig):
        """Lazily initialize an AsyncOpenAI client for a provider."""
        if config.name not in self._clients:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise ImportError("pip install openai")
            self._clients[config.name] = AsyncOpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=config.timeout,
            )
        return self._clients[config.name]

    def _get_ordered_providers(self) -> List[ProviderConfig]:
        """Get providers ordered by priority, skipping circuit-broken ones."""
        providers = []
        for config in self._registry.get_by_priority():
            health = self._health.get(config.name)
            if health and health.is_available():
                providers.append(config)
        return providers

    def _should_failover(self, error: Exception) -> bool:
        """Determine if an error should trigger failover."""
        # Check known exception types
        if isinstance(error, _FAILOVER_EXCEPTIONS):
            return True

        # Check HTTP status codes (openai library wraps these)
        status = getattr(error, "status_code", None) or getattr(error, "status", None)
        if status and int(status) in _FAILOVER_STATUS_CODES:
            return True

        # Rate limit errors from openai library
        error_type = type(error).__name__
        if error_type in ("RateLimitError", "APIStatusError", "APITimeoutError",
                          "InternalServerError", "ServiceUnavailableError"):
            return True

        return False

    async def chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int = 2000,
        model: str | None = None,
        preferred_provider: str | None = None,
    ) -> str:
        """
        Simple chat with automatic failover.
        
        Args:
            preferred_provider: If set, try this provider first
            model: Override model (uses provider's default if None)
        """
        providers = self._get_ordered_providers()
        if not providers:
            raise RuntimeError("No LLM providers available. Check your API keys.")

        # Move preferred provider to front
        if preferred_provider:
            providers = sorted(
                providers,
                key=lambda p: 0 if p.name == preferred_provider else 1
            )

        last_error = None
        for config in providers:
            client = self._get_client(config)
            effective_model = model or config.default_model
            health = self._health[config.name]

            try:
                async with self._semaphore:
                    resp = await client.chat.completions.create(
                        model=effective_model,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                health.record_success()
                return resp.choices[0].message.content or ""

            except Exception as e:
                health.record_failure()
                last_error = e
                if self._should_failover(e):
                    print(
                        f"  [failover] {config.name} failed ({type(e).__name__}), "
                        f"trying next provider...",
                        file=sys.stderr,
                    )
                    continue
                else:
                    # Non-transient error, don't failover
                    raise

        # All providers failed
        raise RuntimeError(
            f"All providers exhausted. Last error: {last_error}"
        ) from last_error

    async def chat_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        temperature: float = 0.1,
        max_tokens: int = 4096,
        model: str | None = None,
        tool_choice: str = "auto",
        preferred_provider: str | None = None,
    ) -> Dict[str, Any]:
        """
        Function calling with automatic failover.
        Returns same format as LLMClient.chat_with_tools().
        """
        import json
        from .client import LLMClient

        providers = self._get_ordered_providers()
        if not providers:
            raise RuntimeError("No LLM providers available.")

        if preferred_provider:
            providers = sorted(
                providers,
                key=lambda p: 0 if p.name == preferred_provider else 1
            )

        openai_tools = LLMClient._convert_tools(tools)
        last_error = None

        for config in providers:
            client = self._get_client(config)
            effective_model = model or config.default_model
            health = self._health[config.name]

            try:
                async with self._semaphore:
                    kwargs = {
                        "model": effective_model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    }
                    if openai_tools:
                        kwargs["tools"] = openai_tools
                        kwargs["tool_choice"] = tool_choice

                    resp = await client.chat.completions.create(**kwargs)

                health.record_success()
                choice = resp.choices[0]
                message = choice.message

                tool_calls = []
                if message.tool_calls:
                    for tc in message.tool_calls:
                        try:
                            args = json.loads(tc.function.arguments)
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                        tool_calls.append({
                            "id": tc.id,
                            "name": tc.function.name,
                            "arguments": args,
                        })

                usage = {}
                if resp.usage:
                    usage = {
                        "prompt_tokens": resp.usage.prompt_tokens,
                        "completion_tokens": resp.usage.completion_tokens,
                    }

                return {
                    "content": message.content,
                    "tool_calls": tool_calls,
                    "finish_reason": choice.finish_reason,
                    "usage": usage,
                    "provider_used": config.name,
                }

            except Exception as e:
                health.record_failure()
                last_error = e
                if self._should_failover(e):
                    print(
                        f"  [failover] {config.name} failed ({type(e).__name__}), "
                        f"trying next...",
                        file=sys.stderr,
                    )
                    continue
                else:
                    raise

        return {
            "content": None,
            "tool_calls": [],
            "finish_reason": "error",
            "usage": {},
            "provider_used": None,
            "error": str(last_error),
        }

    def health_summary(self) -> Dict[str, dict]:
        """Return health status of all providers."""
        return {
            name: {
                "available": h.is_available(),
                "health_score": round(h.health_score, 2),
                "consecutive_failures": h.consecutive_failures,
                "total_calls": h.total_successes + h.total_failures,
                "circuit_open": h.is_circuit_open,
            }
            for name, h in self._health.items()
        }

    @property
    def available_count(self) -> int:
        return sum(1 for h in self._health.values() if h.is_available())
