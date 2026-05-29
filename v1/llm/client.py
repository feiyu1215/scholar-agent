"""
llm/client.py — Shared LLM client with native function calling support.

Supports any OpenAI-compatible API provider.
Configure via environment variables or .env file.

Key Design Decisions:
- Fully async (no asyncio.run() anywhere — caller manages event loop)
- Native tool_use via OpenAI function calling protocol
- Streaming support for real-time output
- Structured output parsing with automatic retry on format errors
"""

from __future__ import annotations

import os
import sys
import asyncio
from typing import Optional, List, Dict, Any

# ============================================================
# Provider Configuration (read at runtime, not import time)
# ============================================================


def get_provider_config(provider: str | None = None) -> dict:
    """Dynamically read provider config from environment variables.
    
    This ensures .env is loaded before we read the values.
    """
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    default_model = os.environ.get("LLM_MODEL", "gpt-4.1-mini")
    default_provider = os.environ.get("LLM_PROVIDER", "openai")

    providers = {
        "openai": {
            "base_url": base_url,
            "api_key": api_key,
            "default_model": default_model,
            "description": "OpenAI API (or any compatible endpoint)",
        },
    }

    effective_provider = provider or default_provider
    if effective_provider not in providers:
        return providers["openai"]
    return providers[effective_provider]


# ============================================================
# LLM Client
# ============================================================

class LLMClient:
    """Async LLM client with native function calling, streaming, and retry."""

    def __init__(self, model: str | None = None, max_concurrent: int = 5,
                 provider: str | None = None):
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("pip install openai")

        config = get_provider_config(provider)
        self.provider_name = provider or os.environ.get("LLM_PROVIDER", "openai")
        # Priority: explicit model arg > session_model (runtime override) > env/config default
        if not model:
            try:
                from core.state import session_model as _sm
                model = _sm
            except (ImportError, AttributeError):
                pass
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
        # Python 3.9 兼容: asyncio.Semaphore() 调用 get_event_loop()，
        # 如果之前 asyncio.run() 关闭了默认 loop 会抛 RuntimeError
        try:
            self.semaphore = asyncio.Semaphore(max_concurrent)
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
            self.semaphore = asyncio.Semaphore(max_concurrent)
        # Rate limit: minimum interval between requests (seconds)
        self._min_interval = float(os.environ.get("SCHOLAR_MIN_INTERVAL", "0"))
        self._last_call_time = 0.0
        self.total_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    async def _rate_limit_wait(self):
        """Enforce minimum interval between requests."""
        if self._min_interval > 0:
            import time
            elapsed = time.time() - self._last_call_time
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_call_time = time.time()

    async def chat(self, system: str, user: str, temperature: float = 0.0,
                   max_tokens: int = 2000, retries: int = 5,
                   model: str = None) -> str:
        """Simple call (no tools). Returns content text."""
        effective_model = model or self.model
        for attempt in range(retries):
            try:
                async with self.semaphore:
                    await self._rate_limit_wait()
                    resp = await self.client.chat.completions.create(
                        model=effective_model,
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
                # Rate limit errors need longer backoff
                is_rate_limit = "429" in str(e) or "RateLimit" in type(e).__name__
                base_wait = 30 if is_rate_limit else 2 ** (attempt + 1)
                wait = base_wait + (attempt * 10 if is_rate_limit else 0)
                print(f"  [retry {attempt+1}] {type(e).__name__}: {e}, wait {wait}s",
                      file=sys.stderr)
                await asyncio.sleep(wait)
        return ""

    async def chat_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        temperature: float = 0.1,
        max_tokens: int = 4096,
        retries: int = 5,
        model: str = None,
        tool_choice: str = "auto",
    ) -> Dict[str, Any]:
        """
        Native function calling via OpenAI tools API.
        
        Returns:
            {
                "content": str | None,        # Text response (if any)
                "tool_calls": [               # Tool calls (if any)
                    {"id": str, "name": str, "arguments": dict}
                ],
                "finish_reason": str,         # "stop" | "tool_calls" | "length"
                "usage": {"prompt_tokens": int, "completion_tokens": int}
            }
        """
        effective_model = model or self.model

        # Convert our tool format to OpenAI function calling format
        openai_tools = self._convert_tools(tools)

        for attempt in range(retries):
            try:
                async with self.semaphore:
                    await self._rate_limit_wait()
                    kwargs = {
                        "model": effective_model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    }
                    if openai_tools:
                        kwargs["tools"] = openai_tools
                        kwargs["tool_choice"] = tool_choice

                    resp = await self.client.chat.completions.create(**kwargs)

                self.total_calls += 1
                usage = {}
                if resp.usage:
                    self.total_input_tokens += resp.usage.prompt_tokens
                    self.total_output_tokens += resp.usage.completion_tokens
                    usage = {
                        "prompt_tokens": resp.usage.prompt_tokens,
                        "completion_tokens": resp.usage.completion_tokens,
                    }

                choice = resp.choices[0]
                message = choice.message

                # Parse tool calls — 容错: 解析失败时保留原始字符串用于诊断
                tool_calls = []
                if message.tool_calls:
                    import json
                    for tc in message.tool_calls:
                        try:
                            args = json.loads(tc.function.arguments)
                        except (json.JSONDecodeError, TypeError) as parse_err:
                            # 不静默丢弃：记录原始内容，传递错误信息给 harness 层
                            raw_args = tc.function.arguments if tc.function.arguments else ""
                            print(
                                f"  [LLM 容错] tool_call '{tc.function.name}' 参数解析失败: "
                                f"{parse_err}. 原始内容: {raw_args[:200]}",
                                file=sys.stderr,
                            )
                            args = {"__parse_error__": str(parse_err), "__raw__": raw_args}
                        tool_calls.append({
                            "id": tc.id,
                            "name": tc.function.name,
                            "arguments": args,
                        })

                return {
                    "content": message.content,
                    "tool_calls": tool_calls,
                    "finish_reason": choice.finish_reason,
                    "usage": usage,
                }

            except Exception as e:
                if attempt == retries - 1:
                    raise
                is_rate_limit = "429" in str(e) or "RateLimit" in type(e).__name__
                base_wait = 30 if is_rate_limit else 2 ** (attempt + 1)
                wait = base_wait + (attempt * 10 if is_rate_limit else 0)
                print(f"  [retry {attempt+1}] {type(e).__name__}: {e}, wait {wait}s",
                      file=sys.stderr)
                await asyncio.sleep(wait)

        return {"content": None, "tool_calls": [], "finish_reason": "error", "usage": {}}

    async def chat_messages(self, messages: List[Dict], temperature: float = 0.0,
                            max_tokens: int = 2000, retries: int = 5,
                            model: str = None) -> str:
        """Call with full message list (no tools). Returns content text."""
        effective_model = model or self.model
        for attempt in range(retries):
            try:
                async with self.semaphore:
                    await self._rate_limit_wait()
                    resp = await self.client.chat.completions.create(
                        model=effective_model,
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
                is_rate_limit = "429" in str(e) or "RateLimit" in type(e).__name__
                base_wait = 30 if is_rate_limit else 2 ** (attempt + 1)
                wait = base_wait + (attempt * 10 if is_rate_limit else 0)
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

    async def chat_with_tools_stream(
        self,
        messages: List[Dict],
        tools: List[Dict],
        temperature: float = 0.1,
        max_tokens: int = 4096,
        retries: int = 5,
        model: str = None,
        tool_choice: str = "auto",
    ):
        """
        Streaming version of chat_with_tools.
        
        Yields chunks as dicts:
            {"type": "content_delta", "text": str}   — incremental text
            {"type": "tool_calls", "tool_calls": [...]}  — complete tool calls at end
            {"type": "finish", "finish_reason": str, "usage": dict}
        
        Falls back to non-streaming on provider error.
        """
        effective_model = model or self.model
        openai_tools = self._convert_tools(tools)

        for attempt in range(retries):
            try:
                async with self.semaphore:
                    await self._rate_limit_wait()
                    kwargs = {
                        "model": effective_model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "stream": True,
                        "stream_options": {"include_usage": True},
                    }
                    if openai_tools:
                        kwargs["tools"] = openai_tools
                        kwargs["tool_choice"] = tool_choice

                    stream = await self.client.chat.completions.create(**kwargs)

                self.total_calls += 1

                # Accumulate streaming response
                collected_content = ""
                tool_call_chunks = {}  # index -> {id, name, arguments_parts}
                finish_reason = "stop"
                usage = {}

                async for chunk in stream:
                    if not chunk.choices and hasattr(chunk, 'usage') and chunk.usage:
                        # Final usage-only chunk
                        usage = {
                            "prompt_tokens": chunk.usage.prompt_tokens,
                            "completion_tokens": chunk.usage.completion_tokens,
                        }
                        self.total_input_tokens += chunk.usage.prompt_tokens
                        self.total_output_tokens += chunk.usage.completion_tokens
                        continue

                    if not chunk.choices:
                        continue

                    delta = chunk.choices[0].delta
                    chunk_finish = chunk.choices[0].finish_reason

                    if chunk_finish:
                        finish_reason = chunk_finish

                    # Content streaming
                    if delta.content:
                        collected_content += delta.content
                        yield {"type": "content_delta", "text": delta.content}

                    # Tool call streaming (accumulated across chunks)
                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_call_chunks:
                                tool_call_chunks[idx] = {
                                    "id": tc_delta.id or "",
                                    "name": "",
                                    "arguments_parts": [],
                                }
                            entry = tool_call_chunks[idx]
                            if tc_delta.id:
                                entry["id"] = tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    entry["name"] = tc_delta.function.name
                                if tc_delta.function.arguments:
                                    entry["arguments_parts"].append(
                                        tc_delta.function.arguments
                                    )

                # Assemble final tool_calls
                import json as _json
                final_tool_calls = []
                for idx in sorted(tool_call_chunks.keys()):
                    entry = tool_call_chunks[idx]
                    args_str = "".join(entry["arguments_parts"])
                    try:
                        args = _json.loads(args_str) if args_str else {}
                    except (_json.JSONDecodeError, TypeError):
                        args = {}
                    final_tool_calls.append({
                        "id": entry["id"],
                        "name": entry["name"],
                        "arguments": args,
                    })

                if final_tool_calls:
                    yield {"type": "tool_calls", "tool_calls": final_tool_calls}

                yield {
                    "type": "finish",
                    "finish_reason": finish_reason,
                    "usage": usage,
                    "content": collected_content if collected_content else None,
                    "tool_calls": final_tool_calls,
                }
                return

            except Exception as e:
                if attempt == retries - 1:
                    raise
                is_rate_limit = "429" in str(e) or "RateLimit" in type(e).__name__
                base_wait = 30 if is_rate_limit else 2 ** (attempt + 1)
                wait = base_wait + (attempt * 10 if is_rate_limit else 0)
                print(f"  [stream retry {attempt+1}] {type(e).__name__}: {e}, wait {wait}s",
                      file=sys.stderr)
                await asyncio.sleep(wait)

        # Final fallback: yield error
        yield {
            "type": "finish",
            "finish_reason": "error",
            "usage": {},
            "content": None,
            "tool_calls": [],
        }

    @staticmethod
    def _convert_tools(tools: List[Dict]) -> List[Dict]:
        """Convert ScholarAgent tool format → OpenAI function calling format.
        
        ScholarAgent format:
            {"name": "x", "description": "...", "input_schema": {...}}
        
        OpenAI format:
            {"type": "function", "function": {"name": "x", "description": "...", "parameters": {...}}}
        """
        openai_tools = []
        for tool in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                },
            })
        return openai_tools
