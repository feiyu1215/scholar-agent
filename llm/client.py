"""
llm/client.py — Shared LLM client with concurrency control and retry.

Supports any OpenAI-compatible API provider.
Configure via environment variables or .env file.
"""

from __future__ import annotations

import os
import sys
import asyncio
from typing import Optional, List, Dict

# ============================================================
# Provider Configuration
# ============================================================

# Default: OpenAI API. Override with any OpenAI-compatible endpoint.
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

DEFAULT_MODEL = os.environ.get("LLM_MODEL", "gpt-4.1-mini")

PROVIDERS = {
    "openai": {
        "base_url": OPENAI_BASE_URL,
        "api_key": OPENAI_API_KEY,
        "default_model": DEFAULT_MODEL,
        "description": "OpenAI API (or any compatible endpoint)",
    },
}

DEFAULT_PROVIDER = os.environ.get("LLM_PROVIDER", "openai")


def get_provider_config(provider: str | None = None) -> dict:
    provider = provider or DEFAULT_PROVIDER
    if provider not in PROVIDERS:
        # Treat unknown providers as custom OpenAI-compatible with env overrides
        return PROVIDERS["openai"]
    return PROVIDERS[provider]


# ============================================================
# LLM Client
# ============================================================

class LLMClient:
    """Async LLM client with concurrency control, exponential backoff retry, and token tracking."""

    def __init__(self, model: str | None = None, max_concurrent: int = 5,
                 provider: str | None = None):
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("pip install openai")

        config = get_provider_config(provider)
        self.provider_name = provider or DEFAULT_PROVIDER
        self.model = model or config["default_model"]

        if not config["api_key"]:
            raise ValueError(
                "No API key configured. Set OPENAI_API_KEY in your environment or .env file.\n"
                "See .env.example for configuration options."
            )

        self.client = AsyncOpenAI(
            api_key=config["api_key"],
            base_url=config["base_url"],
        )
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.total_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    async def chat(self, system: str, user: str, temperature: float = 0.0,
                   max_tokens: int = 2000, retries: int = 5) -> str:
        """Single call with exponential backoff. Returns content text."""
        for attempt in range(retries):
            try:
                async with self.semaphore:
                    resp = await self.client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                self.total_calls += 1
                if resp.usage:
                    self.total_input_tokens += resp.usage.prompt_tokens
                    self.total_output_tokens += resp.usage.completion_tokens
                return resp.choices[0].message.content or ""
            except Exception as e:
                if attempt == retries - 1:
                    raise
                wait = 2 ** (attempt + 1)
                print(f"  [retry {attempt+1}] {type(e).__name__}: {e}, wait {wait}s",
                      file=sys.stderr)
                await asyncio.sleep(wait)
        return ""

    async def chat_messages(self, messages: List[Dict], temperature: float = 0.0,
                            max_tokens: int = 2000, retries: int = 5) -> str:
        """Call with full message list. Returns content text."""
        for attempt in range(retries):
            try:
                async with self.semaphore:
                    resp = await self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                self.total_calls += 1
                if resp.usage:
                    self.total_input_tokens += resp.usage.prompt_tokens
                    self.total_output_tokens += resp.usage.completion_tokens
                return resp.choices[0].message.content or ""
            except Exception as e:
                if attempt == retries - 1:
                    raise
                wait = 2 ** (attempt + 1)
                print(f"  [retry {attempt+1}] {type(e).__name__}: {e}, wait {wait}s",
                      file=sys.stderr)
                await asyncio.sleep(wait)
        return ""

    def stats(self) -> dict:
        return {
            "provider": self.provider_name,
            "model": self.model,
            "total_calls": self.total_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "estimated_cost_usd": round(
                self.total_input_tokens * 0.00015 / 1000 +
                self.total_output_tokens * 0.0006 / 1000, 4
            ),
        }
