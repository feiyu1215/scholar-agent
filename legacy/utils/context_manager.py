"""
Proactive Context Manager — Prevents context overflow by monitoring and compressing early.

Unlike reactive truncation (which loses information when the window fills),
this module:
1. Tracks approximate token usage per message
2. Triggers compression BEFORE the window fills (at ~70% capacity)
3. Uses retention policies to decide what to keep/compress/drop
4. Preserves semantic continuity through compression summaries

The compression is non-destructive: original messages are replaced with
summaries that retain key facts and decisions.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional


# Compression triggers
SOFT_LIMIT_RATIO = 0.65   # Start compressing at 65% of context window
HARD_LIMIT_RATIO = 0.80   # Aggressive compression at 80%
DEFAULT_CONTEXT_WINDOW = 128000  # tokens


@dataclass
class ContextBudget:
    """Tracks token usage and compression state."""
    max_tokens: int = DEFAULT_CONTEXT_WINDOW
    estimated_tokens: int = 0
    messages_count: int = 0
    compressions_applied: int = 0
    last_compression_at: int = 0  # message index where last compression happened

    @property
    def usage_ratio(self) -> float:
        return self.estimated_tokens / max(self.max_tokens, 1)

    @property
    def should_compress(self) -> bool:
        return self.usage_ratio >= SOFT_LIMIT_RATIO

    @property
    def must_compress(self) -> bool:
        return self.usage_ratio >= HARD_LIMIT_RATIO


def estimate_tokens(text: str) -> int:
    """CJK-aware token estimate for a text string.

    Matches the estimation logic in main.py:
    - ASCII text: ~4 chars per token
    - CJK characters: ~1.5 chars per token
    """
    if not text:
        return 0
    cjk_chars = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f')
    ascii_chars = len(text) - cjk_chars
    return max(1, int(ascii_chars / 4 + cjk_chars / 1.5))


def estimate_message_tokens(msg: dict) -> int:
    """Estimate tokens in a single message dict."""
    total = 4  # Base overhead per message (role, separators)
    content = msg.get("content", "")
    if content:
        total += estimate_tokens(content)
    # Tool name field (present in tool messages)
    name = msg.get("name", "")
    if name:
        total += estimate_tokens(name)
    # Tool calls add tokens
    tool_calls = msg.get("tool_calls", [])
    for tc in tool_calls:
        func = tc.get("function", {})
        total += estimate_tokens(func.get("name", ""))
        total += estimate_tokens(func.get("arguments", ""))
    return total


class ProactiveContextManager:
    """Monitors context budget and triggers compression before overflow.

    Usage:
        ctx_mgr = ProactiveContextManager(max_tokens=128000)
        ctx_mgr.update(messages)
        if ctx_mgr.should_compress():
            compressed = ctx_mgr.compress(messages, retention_policy)
    """

    def __init__(self, max_tokens: int = DEFAULT_CONTEXT_WINDOW):
        self._budget = ContextBudget(max_tokens=max_tokens)
        self._system_tokens = 0  # Tokens used by system prompt (fixed cost)

    def set_system_overhead(self, system_prompt: str):
        """Record the system prompt token cost (doesn't change per turn)."""
        self._system_tokens = estimate_tokens(system_prompt)

    def update(self, messages: list[dict]) -> ContextBudget:
        """Recalculate budget from current message list."""
        total = self._system_tokens
        for msg in messages:
            total += estimate_message_tokens(msg)

        self._budget.estimated_tokens = total
        self._budget.messages_count = len(messages)
        return self._budget

    def should_compress(self) -> bool:
        """Check if compression should be triggered."""
        return self._budget.should_compress

    def must_compress(self) -> bool:
        """Check if aggressive compression is needed."""
        return self._budget.must_compress

    def get_budget(self) -> ContextBudget:
        """Return current budget state."""
        return self._budget

    def compress(self, messages: list[dict], retention_policy: dict,
                 recent_window: int = 6) -> list[dict]:
        """Compress messages according to retention policy.

        Strategy:
        1. Never touch the most recent `recent_window` messages
        2. ALWAYS_KEEP messages are preserved verbatim
        3. KEEP_UNTIL_SUPERSEDED messages are kept if no newer version exists
        4. COMPRESS_TO_SUMMARY messages are replaced with brief summaries
        5. Tool results older than recent window get aggressively compressed

        Args:
            messages: Full message history
            retention_policy: Dict mapping tool_name → retention level
            recent_window: Number of recent messages to never compress

        Returns:
            Compressed message list (new list, doesn't modify input)
        """
        if len(messages) <= recent_window:
            return messages  # Nothing to compress

        # Split into compressible region and protected recent window
        compress_region = messages[:-recent_window]
        protected = messages[-recent_window:]

        compressed = []
        # Track which tools have been seen (for KEEP_UNTIL_SUPERSEDED)
        seen_tools: dict[str, int] = {}  # tool_name → index of latest occurrence

        # First pass: identify latest occurrence of each tool in compress_region
        for i, msg in enumerate(compress_region):
            if msg.get("role") == "tool":
                tool_name = msg.get("name", "")
                seen_tools[tool_name] = i

        # Second pass: apply retention policies
        # Key invariant: preserve assistant(tool_calls) → tool pairing
        for i, msg in enumerate(compress_region):
            role = msg.get("role", "")

            if role == "user":
                # User messages are always kept (they define the task)
                compressed.append(msg)

            elif role == "assistant":
                content = msg.get("content", "")
                if msg.get("tool_calls"):
                    # Keep tool call structure but trim arguments
                    trimmed_msg = {"role": "assistant", "content": content}
                    trimmed_calls = []
                    for tc in msg.get("tool_calls", []):
                        trimmed_calls.append({
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["function"]["name"],
                                "arguments": tc["function"]["arguments"][:100],
                            },
                        })
                    trimmed_msg["tool_calls"] = trimmed_calls
                    compressed.append(trimmed_msg)
                elif content and len(content) > 200:
                    # Compress long assistant text messages
                    compressed.append({
                        "role": "assistant",
                        "content": content[:200] + "...",
                    })
                else:
                    compressed.append(msg)

            elif role == "tool":
                tool_name = msg.get("name", "")
                tool_call_id = msg.get("tool_call_id", "")
                policy = retention_policy.get(tool_name, "COMPRESS_TO_SUMMARY")
                content = msg.get("content", "")

                if policy == "ALWAYS_KEEP":
                    # Keep verbatim
                    compressed.append(msg)
                elif policy == "KEEP_UNTIL_SUPERSEDED":
                    if seen_tools.get(tool_name) == i:
                        # Latest occurrence: keep
                        compressed.append(msg)
                    else:
                        # Superseded: keep structure but compress content
                        compressed.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "name": tool_name,
                            "content": f"(superseded by later {tool_name} call)",
                        })
                else:
                    # COMPRESS_TO_SUMMARY: keep as valid tool message with truncated content
                    summary = content[:100] + "..." if len(content) > 100 else content
                    compressed.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": tool_name,
                        "content": summary,
                    })

            elif role == "system":
                compressed.append(msg)
            else:
                compressed.append(msg)

        # Combine compressed + protected
        result = compressed + protected

        self._budget.compressions_applied += 1
        self._budget.last_compression_at = len(result) - recent_window

        return result

    def get_status_string(self) -> str:
        """Return status for session_status display."""
        b = self._budget
        return (
            f"Context: ~{b.estimated_tokens:,}/{b.max_tokens:,} tokens "
            f"({b.usage_ratio:.0%}) | "
            f"Messages: {b.messages_count} | "
            f"Compressions: {b.compressions_applied}"
        )


