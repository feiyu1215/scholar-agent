"""
tests/mock_llm.py — Mock LLM Client for integration tests.

Provides a deterministic LLM client that returns pre-configured responses
in sequence, enabling reproducible integration tests of the cognitive loop.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


# ============================================================
# Mock Response Types
# ============================================================

@dataclass
class MockResponse:
    """A single pre-configured LLM response."""
    content: str | None = None
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"prompt_tokens": 100, "completion_tokens": 50})

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "tool_calls": self.tool_calls,
            "finish_reason": "stop" if not self.tool_calls else "tool_calls",
            "usage": self.usage,
        }


# ============================================================
# Helper functions for building tool calls
# ============================================================

_call_counter = 0


def _next_id() -> str:
    global _call_counter
    _call_counter += 1
    return f"call_{_call_counter:04d}"


def make_tool_call(name: str, args: dict) -> dict:
    """Create a tool_call dict as returned by LLM."""
    return {
        "id": _next_id(),
        "name": name,
        "arguments": args,
    }


def make_read_section_response(section: str, content: str | None = None) -> MockResponse:
    """Build a response that calls read_section."""
    return MockResponse(
        content=content,
        tool_calls=[make_tool_call("read_section", {"section": section})],
    )


def make_update_findings_response(findings: list[dict], content: str | None = None) -> MockResponse:
    """Build a response that calls update_findings (legacy batch format)."""
    return MockResponse(
        content=content,
        tool_calls=[make_tool_call("update_findings", {"findings": findings})],
    )


def make_single_finding_response(
    finding: str,
    section: str = "",
    priority: str = "medium",
    status: str = "suggestion",
    evidence: str = "",
    content: str | None = None,
) -> MockResponse:
    """Build a response that calls update_findings with the actual single-finding API format."""
    args = {
        "finding": finding,
        "section": section,
        "priority": priority,
        "status": status,
        "evidence": evidence,
    }
    return MockResponse(
        content=content,
        tool_calls=[make_tool_call("update_findings", args)],
    )


def make_done_response(summary: str, content: str | None = None) -> MockResponse:
    """Build a response that calls mark_complete (done)."""
    return MockResponse(
        content=content,
        tool_calls=[make_tool_call("mark_complete", {"summary": summary})],
    )


def make_phase_transition_response(target_phase: str, reason: str = "", content: str | None = None) -> MockResponse:
    """Build a response that calls request_phase_transition."""
    return MockResponse(
        content=content,
        tool_calls=[make_tool_call("request_phase_transition", {
            "target_phase": target_phase,
            "reason": reason,
        })],
    )


def make_text_only_response(text: str) -> MockResponse:
    """Build a response with text only (no tool calls)."""
    return MockResponse(content=text, tool_calls=[])


# ============================================================
# Mock LLM Client
# ============================================================

class MockLLMClient:
    """
    A mock LLM client that returns pre-configured responses in sequence.

    Usage:
        client = MockLLMClient(responses=[
            make_read_section_response("introduction"),
            make_read_section_response("methodology"),
            make_done_response("Review complete."),
        ])
    """

    def __init__(self, responses: list[MockResponse]) -> None:
        self._responses = responses
        self._call_index = 0

    @property
    def call_count(self) -> int:
        return self._call_index

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> dict:
        """Return the next pre-configured response."""
        if self._call_index >= len(self._responses):
            # Fallback: return a done call to prevent infinite loops in tests
            return MockResponse(
                content=None,
                tool_calls=[make_tool_call("mark_complete", {"summary": "[mock exhausted]"})],
            ).to_dict()
        response = self._responses[self._call_index]
        self._call_index += 1
        return response.to_dict()
