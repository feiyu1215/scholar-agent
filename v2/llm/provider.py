"""
llm/provider.py - Multi-provider registry with auto-discovery.

Discovers available LLM providers from environment variables and provides
a unified ProviderConfig interface. Supports OpenAI, Anthropic, DeepSeek,
and any local OpenAI-compatible endpoint.

Auto-discovery rules:
    - OPENAI_API_KEY present -> registers "openai"
    - ANTHROPIC_API_KEY present -> registers "anthropic"
    - DEEPSEEK_API_KEY present -> registers "deepseek"
    - LOCAL_MODEL_URL present -> registers "local"
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider."""
    name: str
    api_key: str
    base_url: str
    default_model: str
    max_rpm: int = 60          # Rate limit (requests per minute)
    max_concurrent: int = 5
    timeout: float = 120.0     # Request timeout in seconds
    priority: int = 0          # Lower = preferred (for failover ordering)
    supports_tools: bool = True
    supports_streaming: bool = True
    cost_per_1k_input: float = 0.0    # USD per 1K input tokens
    cost_per_1k_output: float = 0.0   # USD per 1K output tokens
    extra: Dict[str, str] = field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        return bool(self.api_key and self.base_url)


# ============================================================
# Provider Definitions
# ============================================================

_PROVIDER_SPECS = {
    "openai": {
        "env_key": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",
        "base_url_default": "https://api.openai.com/v1",
        "model_env": "OPENAI_MODEL",
        "model_default": "gpt-4.1-mini",
        "priority": 0,
        "cost_per_1k_input": 0.00015,
        "cost_per_1k_output": 0.0006,
    },
    "anthropic": {
        "env_key": "ANTHROPIC_API_KEY",
        "base_url_env": "ANTHROPIC_BASE_URL",
        "base_url_default": "https://api.anthropic.com/v1",
        "model_env": "ANTHROPIC_MODEL",
        "model_default": "claude-sonnet-4-20250514",
        "priority": 1,
        "cost_per_1k_input": 0.003,
        "cost_per_1k_output": 0.015,
    },
    "deepseek": {
        "env_key": "DEEPSEEK_API_KEY",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "base_url_default": "https://api.deepseek.com/v1",
        "model_env": "DEEPSEEK_MODEL",
        "model_default": "deepseek-chat",
        "priority": 2,
        "cost_per_1k_input": 0.00014,
        "cost_per_1k_output": 0.00028,
    },
    "local": {
        "env_key": "LOCAL_API_KEY",
        "base_url_env": "LOCAL_MODEL_URL",
        "base_url_default": "http://localhost:11434/v1",
        "model_env": "LOCAL_MODEL",
        "model_default": "llama3",
        "priority": 10,
        "cost_per_1k_input": 0.0,
        "cost_per_1k_output": 0.0,
    },
}


class ProviderRegistry:
    """
    Auto-discovers and manages LLM provider configurations.
    
    Usage:
        registry = ProviderRegistry()
        providers = registry.available_providers()
        config = registry.get("openai")
    """

    def __init__(self):
        self._providers: Dict[str, ProviderConfig] = {}
        self._discover()

    def _discover(self):
        """Auto-discover providers from environment variables."""
        for name, spec in _PROVIDER_SPECS.items():
            api_key = os.environ.get(spec["env_key"], "")
            base_url = os.environ.get(spec["base_url_env"], spec["base_url_default"])

            # Special case: LOCAL_MODEL_URL presence alone is enough
            if name == "local":
                local_url = os.environ.get("LOCAL_MODEL_URL", "")
                if not local_url:
                    continue
                base_url = local_url
                api_key = api_key or "local"  # Local models often don't need a real key

            if not api_key:
                continue

            model = os.environ.get(spec["model_env"], spec["model_default"])

            self._providers[name] = ProviderConfig(
                name=name,
                api_key=api_key,
                base_url=base_url,
                default_model=model,
                priority=spec["priority"],
                cost_per_1k_input=spec["cost_per_1k_input"],
                cost_per_1k_output=spec["cost_per_1k_output"],
            )

    def get(self, name: str) -> Optional[ProviderConfig]:
        """Get a specific provider config by name."""
        return self._providers.get(name)

    def available_providers(self) -> List[str]:
        """List names of all discovered providers, sorted by priority."""
        return sorted(
            self._providers.keys(),
            key=lambda n: self._providers[n].priority
        )

    def get_by_priority(self) -> List[ProviderConfig]:
        """Get all available providers sorted by priority (lowest first)."""
        return sorted(self._providers.values(), key=lambda p: p.priority)

    def primary(self) -> Optional[ProviderConfig]:
        """Get the highest-priority (lowest number) available provider."""
        providers = self.get_by_priority()
        return providers[0] if providers else None

    def register(self, config: ProviderConfig):
        """Manually register a provider (for testing or custom endpoints)."""
        self._providers[config.name] = config

    def unregister(self, name: str):
        """Remove a provider from the registry."""
        self._providers.pop(name, None)

    @property
    def count(self) -> int:
        return len(self._providers)

    def summary(self) -> Dict[str, dict]:
        """Return a summary suitable for logging/display."""
        return {
            name: {
                "model": p.default_model,
                "base_url": p.base_url[:50] + "..." if len(p.base_url) > 50 else p.base_url,
                "priority": p.priority,
                "supports_tools": p.supports_tools,
            }
            for name, p in self._providers.items()
        }


# Module-level singleton (lazy initialization pattern)
_registry: Optional[ProviderRegistry] = None


def get_registry() -> ProviderRegistry:
    """Get or create the global provider registry singleton."""
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry
