"""
llm/client.py — Shared LLM client with native function calling support.

Supports any OpenAI-compatible API provider.
Configure via environment variables or .env file.

Key Design Decisions:
- Fully async (no asyncio.run() anywhere — caller manages event loop)
- Native tool_use via OpenAI function calling protocol
- Streaming support for real-time output
- Structured output parsing with automatic retry on format errors

V3 A2 Enhancements:
- Per-request timeout (configurable via SCHOLAR_LLM_TIMEOUT, default 120s)
- Error classification: transient (retry) vs permanent (fast-fail)
- Unified retry logic via _retry_call() helper
- Exponential backoff with jitter and cap (max 60s)
"""

from __future__ import annotations

import logging
import os
import asyncio
import random
import time
from typing import Optional, List, Dict, Any

# Module-level logger (replaces print to stderr)
logger = logging.getLogger(__name__)

# Default retry count for LLM API calls
DEFAULT_MAX_RETRIES = 5

# ============================================================
# Error Classification
# ============================================================

# Errors that should NOT be retried (permanent failures)
_PERMANENT_ERROR_CODES = {400, 401, 403, 404, 422}


def _is_transient_error(e: Exception) -> bool:
    """Determine if an error is transient (should retry) or permanent (fast-fail).
    
    Transient: rate limit (429), server error (5xx), timeout, connection errors.
    Permanent: auth errors (401/403), bad request (400), not found (404).
    
    Priority: structured status_code > error type name > string matching (fallback).
    """
    err_type = type(e).__name__

    # 1. Structured status code (most reliable — OpenAI SDK errors have this)
    if hasattr(e, 'status_code'):
        code = getattr(e, 'status_code', 0)
        if code == 429:
            return True  # rate limit — transient
        if code in _PERMANENT_ERROR_CODES:
            return False  # permanent
        if code >= 500:
            return True  # server error — transient
        # Other 4xx not in permanent set — treat as permanent
        if 400 <= code < 500:
            return False

    # 2. Error type name matching (no string content parsing needed)
    if "RateLimit" in err_type:
        return True
    if "Timeout" in err_type or "TimeoutError" in err_type:
        return True
    if "Connection" in err_type:
        return True

    # 3. String-based fallback (only for errors without status_code)
    err_str = str(e)
    if "429" in err_str:
        return True
    if "timed out" in err_str.lower() or "timeout" in err_str.lower():
        return True
    if "connect" in err_str.lower():
        return True

    # Default: treat as transient (retry is safer than giving up)
    return True


def _is_rate_limit(e: Exception) -> bool:
    """Check if error is specifically a rate limit (for longer backoff)."""
    if hasattr(e, 'status_code') and getattr(e, 'status_code', 0) == 429:
        return True
    if "RateLimit" in type(e).__name__:
        return True
    # String fallback for errors without status_code
    return "429" in str(e)


def _extract_retry_after(e: Exception) -> float | None:
    """Extract Retry-After value from a rate limit error's response headers.

    Returns seconds to wait, or None if header not present/parseable.
    OpenAI SDK attaches response.headers on APIStatusError subclasses.
    """
    # Try response.headers (openai SDK >= 1.x)
    response = getattr(e, 'response', None)
    if response is not None:
        headers = getattr(response, 'headers', None) or {}
        retry_after = headers.get('retry-after') or headers.get('Retry-After')
        if retry_after is not None:
            try:
                return float(retry_after)
            except (ValueError, TypeError):
                pass
    # Try direct header dict attribute (some providers)
    headers = getattr(e, 'headers', None) or {}
    retry_after = headers.get('retry-after') or headers.get('Retry-After')
    if retry_after is not None:
        try:
            return float(retry_after)
        except (ValueError, TypeError):
            pass
    return None


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
    """Async LLM client with native function calling, streaming, and robust retry."""

    # Configurable timeout per request (seconds)
    DEFAULT_TIMEOUT = float(os.environ.get("SCHOLAR_LLM_TIMEOUT", "120"))
    # Max backoff cap (seconds)
    MAX_BACKOFF = 60.0

    def __init__(self, model: str | None = None, max_concurrent: int = 5,
                 provider: str | None = None, timeout: float | None = None,
                 total_timeout: float | None = None):
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

        self.timeout = timeout or self.DEFAULT_TIMEOUT
        # Total timeout: max wall-clock time for a single chat() call including all retries
        # Default: 3x per-request timeout (covers 5 retries with backoff)
        self.total_timeout = total_timeout or (self.timeout * 3)
        self.client = AsyncOpenAI(
            api_key=config["api_key"],
            base_url=config["base_url"],
            timeout=self.timeout,
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
        # Error stats for observability
        self.total_retries = 0
        self.total_permanent_failures = 0

    async def _rate_limit_wait(self):
        """Enforce minimum interval between requests."""
        if self._min_interval > 0:
            elapsed = time.time() - self._last_call_time
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_call_time = time.time()

    def _compute_backoff(self, attempt: int, is_rate_limit: bool,
                         retry_after: float | None = None) -> float:
        """Compute backoff with exponential growth, jitter, and cap.
        
        Priority: Retry-After header > rate-limit heuristic > exponential.
        Rate limit (no header): base 30s + 10s per attempt (aggressive backoff).
        Other: 2^attempt with jitter, capped at MAX_BACKOFF.
        """
        # If server told us exactly how long to wait, respect it (with cap)
        if retry_after is not None and retry_after > 0:
            return min(retry_after, self.MAX_BACKOFF * 2)  # Allow up to 2x cap for server directive

        if is_rate_limit:
            base_wait = 30 + (attempt * 10)
        else:
            base_wait = min(2 ** (attempt + 1), self.MAX_BACKOFF)
        # Add jitter (±25%) to avoid thundering herd
        jitter = base_wait * 0.25 * (2 * random.random() - 1)
        return max(0.5, base_wait + jitter)

    async def chat(self, system: str, user: str, temperature: float = 0.0,
                   max_tokens: int = 2000, retries: int = DEFAULT_MAX_RETRIES,
                   model: str = None) -> str:
        """Simple call (no tools). Returns content text."""
        effective_model = model or self.model
        start_time = time.time()
        for attempt in range(retries):
            # Total timeout guard: abort if wall-clock budget exhausted
            elapsed_total = time.time() - start_time
            if elapsed_total >= self.total_timeout:
                raise TimeoutError(
                    f"LLM chat() total timeout ({self.total_timeout:.0f}s) exceeded "
                    f"after {attempt} attempts and {elapsed_total:.1f}s"
                )
            try:
                async with self.semaphore:
                    await self._rate_limit_wait()
                    resp = await asyncio.wait_for(
                        self.client.chat.completions.create(
                            model=effective_model,
                            messages=[
                                {"role": "system", "content": system},
                                {"role": "user", "content": user},
                            ],
                            temperature=temperature,
                            max_tokens=max_tokens,
                        ),
                        timeout=self.timeout,
                    )
                self.total_calls += 1
                if resp.usage:
                    self.total_input_tokens += resp.usage.prompt_tokens
                    self.total_output_tokens += resp.usage.completion_tokens
                return resp.choices[0].message.content or ""
            except Exception as e:
                if attempt == retries - 1:
                    raise
                if not _is_transient_error(e):
                    self.total_permanent_failures += 1
                    raise  # Don't retry permanent errors
                self.total_retries += 1
                rate_limited = _is_rate_limit(e)
                retry_after = _extract_retry_after(e) if rate_limited else None
                wait = self._compute_backoff(attempt, rate_limited, retry_after)
                logger.warning(
                    "[retry %d/%d] %s: %s, wait %.1fs %s",
                    attempt + 1, retries, type(e).__name__, e, wait,
                    f"(rate-limit, Retry-After={retry_after})" if rate_limited else "(transient)"
                )
                await asyncio.sleep(wait)
        return ""

    async def chat_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        temperature: float = 0.1,
        max_tokens: int = 4096,
        retries: int = DEFAULT_MAX_RETRIES,
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

        start_time = time.time()
        for attempt in range(retries):
            # Total timeout guard
            elapsed_total = time.time() - start_time
            if elapsed_total >= self.total_timeout:
                raise TimeoutError(
                    f"LLM chat_with_tools() total timeout ({self.total_timeout:.0f}s) exceeded "
                    f"after {attempt} attempts and {elapsed_total:.1f}s"
                )
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

                    resp = await asyncio.wait_for(
                        self.client.chat.completions.create(**kwargs),
                        timeout=self.timeout,
                    )

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
                            logger.warning(
                                "[LLM 容错] tool_call '%s' 参数解析失败: %s. 原始内容: %s",
                                tc.function.name, parse_err, raw_args[:200]
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
                if not _is_transient_error(e):
                    self.total_permanent_failures += 1
                    raise
                self.total_retries += 1
                rate_limited = _is_rate_limit(e)
                retry_after = _extract_retry_after(e) if rate_limited else None
                wait = self._compute_backoff(attempt, rate_limited, retry_after)
                logger.warning(
                    "[retry %d/%d] %s: %s, wait %.1fs %s",
                    attempt + 1, retries, type(e).__name__, e, wait,
                    f"(rate-limit, Retry-After={retry_after})" if rate_limited else "(transient)"
                )
                await asyncio.sleep(wait)

        return {"content": None, "tool_calls": [], "finish_reason": "error", "usage": {}}

    async def chat_messages(self, messages: List[Dict], temperature: float = 0.0,
                            max_tokens: int = 2000, retries: int = DEFAULT_MAX_RETRIES,
                            model: str = None) -> str:
        """Call with full message list (no tools). Returns content text."""
        effective_model = model or self.model
        start_time = time.time()
        for attempt in range(retries):
            # Total timeout guard
            elapsed_total = time.time() - start_time
            if elapsed_total >= self.total_timeout:
                raise TimeoutError(
                    f"LLM chat_messages() total timeout ({self.total_timeout:.0f}s) exceeded "
                    f"after {attempt} attempts and {elapsed_total:.1f}s"
                )
            try:
                async with self.semaphore:
                    await self._rate_limit_wait()
                    resp = await asyncio.wait_for(
                        self.client.chat.completions.create(
                            model=effective_model,
                            messages=messages,
                            temperature=temperature,
                            max_tokens=max_tokens,
                        ),
                        timeout=self.timeout,
                    )
                self.total_calls += 1
                if resp.usage:
                    self.total_input_tokens += resp.usage.prompt_tokens
                    self.total_output_tokens += resp.usage.completion_tokens
                return resp.choices[0].message.content or ""
            except Exception as e:
                if attempt == retries - 1:
                    raise
                if not _is_transient_error(e):
                    self.total_permanent_failures += 1
                    raise
                self.total_retries += 1
                rate_limited = _is_rate_limit(e)
                retry_after = _extract_retry_after(e) if rate_limited else None
                wait = self._compute_backoff(attempt, rate_limited, retry_after)
                logger.warning(
                    "[retry %d/%d] %s: %s, wait %.1fs %s",
                    attempt + 1, retries, type(e).__name__, e, wait,
                    f"(rate-limit, Retry-After={retry_after})" if rate_limited else "(transient)"
                )
                await asyncio.sleep(wait)
        return ""

    def with_model_override(self, model: str) -> "LLMClient":
        """创建使用不同模型的 client（共享连接池/session）。

        设计：浅拷贝，只改 model 字段。如果 model 相同则返回 self（零成本）。
        连接池（AsyncOpenAI client）、semaphore、统计计数器均共享，
        避免创建新的 HTTP session。

        Args:
            model: 目标模型名称

        Returns:
            使用指定模型的 LLMClient（可能是 self）
        """
        if model == self.model:
            return self
        import copy
        clone = copy.copy(self)
        clone.model = model
        return clone

    def stats(self) -> dict:
        return {
            "provider": self.provider_name,
            "model": self.model,
            "total_calls": self.total_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_retries": self.total_retries,
            "total_permanent_failures": self.total_permanent_failures,
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
        retries: int = DEFAULT_MAX_RETRIES,
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

                    stream = await asyncio.wait_for(
                        self.client.chat.completions.create(**kwargs),
                        timeout=self.timeout,
                    )

                self.total_calls += 1

                # Accumulate streaming response
                collected_content = ""
                tool_call_chunks: dict[int, dict] = {}  # index -> {id, name, arguments_parts}
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
                if not _is_transient_error(e):
                    self.total_permanent_failures += 1
                    raise
                self.total_retries += 1
                rate_limited = _is_rate_limit(e)
                retry_after = _extract_retry_after(e) if rate_limited else None
                wait = self._compute_backoff(attempt, rate_limited, retry_after)
                logger.warning(
                    "[stream retry %d/%d] %s: %s, wait %.1fs %s",
                    attempt + 1, retries, type(e).__name__, e, wait,
                    f"(rate-limit, Retry-After={retry_after})" if rate_limited else "(transient)"
                )
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
