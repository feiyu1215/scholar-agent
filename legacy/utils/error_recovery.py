"""
Error Recovery — Intelligent retry, fallback, and degradation strategies.

When a tool fails, the agent shouldn't just tell the user "Error occurred."
Instead, it should:
1. Classify the error (transient vs permanent, recoverable vs fatal)
2. Apply the appropriate recovery strategy
3. Degrade gracefully if recovery fails

This module provides:
- Error classification
- Retry logic with exponential backoff
- Fallback tool suggestions (alternative paths)
- Circuit breaker pattern (stop retrying after N failures)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ErrorClass(str, Enum):
    """Classification of tool errors."""
    TRANSIENT = "transient"         # Network timeout, rate limit — worth retrying
    INPUT_INVALID = "input_invalid"  # Bad arguments — don't retry with same args
    RESOURCE_MISSING = "resource_missing"  # File/section not found — need different input
    PROVIDER_ERROR = "provider_error"  # LLM provider error — retry or switch provider
    INTERNAL_BUG = "internal_bug"    # Code bug — don't retry, report
    UNKNOWN = "unknown"


# Patterns that indicate specific error classes
ERROR_PATTERNS = {
    ErrorClass.TRANSIENT: [
        "timeout", "timed out", "rate limit", "429", "503", "502",
        "connection reset", "connection refused", "temporary",
    ],
    ErrorClass.INPUT_INVALID: [
        "invalid", "required", "missing parameter", "type error",
        "validation", "not a valid",
    ],
    ErrorClass.RESOURCE_MISSING: [
        "not found", "no such file", "does not exist", "section_id",
        "FileNotFoundError", "KeyError",
    ],
    ErrorClass.PROVIDER_ERROR: [
        "openai", "anthropic", "api error", "model not available",
        "content filter", "safety", "insufficient_quota",
    ],
    ErrorClass.INTERNAL_BUG: [
        "AttributeError", "TypeError", "ImportError", "SyntaxError",
        "NameError", "IndexError", "RecursionError",
    ],
}

# Fallback suggestions: if tool X fails, suggest tool Y as an alternative.
# These are advisory — the LLM decides whether to use them.
# Entries should reference tools that exist in TOOL_HANDLERS.
FALLBACK_MAP: dict[str, list[str]] = {
    "search_literature": ["verify_doi"],  # If search fails, try direct DOI lookup
    "review_paper": ["run_single_reviewer"],  # If full review fails, try single role
    "parallel_rewrite": ["rewrite_section"],  # If parallel fails, try sequential
    "deai_closed_loop": ["deai_audit"],  # If closed loop fails, try lighter audit
    "verify_and_enrich_citations": ["verify_citations"],  # Lighter citation check
    "generate_rewrite": ["edit_section"],  # If generation fails, try edit
}

# Max retries per error class
MAX_RETRIES = {
    ErrorClass.TRANSIENT: 3,
    ErrorClass.INPUT_INVALID: 0,
    ErrorClass.RESOURCE_MISSING: 0,
    ErrorClass.PROVIDER_ERROR: 2,
    ErrorClass.INTERNAL_BUG: 0,
    ErrorClass.UNKNOWN: 1,
}


@dataclass
class ErrorEvent:
    """A recorded error occurrence."""
    tool_name: str
    error_class: ErrorClass
    error_message: str
    timestamp: float = field(default_factory=time.time)
    retry_count: int = 0
    recovered: bool = False


@dataclass
class CircuitState:
    """Circuit breaker state for a specific tool."""
    failures: int = 0
    last_failure: float = 0.0
    is_open: bool = False  # True = circuit broken, don't call this tool
    cooldown_until: float = 0.0  # When the circuit can be re-tested

    FAILURE_THRESHOLD = 3
    COOLDOWN_SECONDS = 60.0

    def record_failure(self):
        self.failures += 1
        self.last_failure = time.time()
        if self.failures >= self.FAILURE_THRESHOLD:
            self.is_open = True
            self.cooldown_until = time.time() + self.COOLDOWN_SECONDS

    def record_success(self):
        self.failures = 0
        self.is_open = False

    def can_attempt(self) -> bool:
        if not self.is_open:
            return True
        # Check if cooldown has elapsed
        if time.time() >= self.cooldown_until:
            self.is_open = False  # Half-open: allow one attempt
            return True
        return False


class ErrorRecoveryEngine:
    """Classifies errors and manages recovery strategies.

    Usage:
        recovery = ErrorRecoveryEngine()
        # Before calling a tool:
        if not recovery.can_call(tool_name):
            # Circuit is open, use fallback
            fallbacks = recovery.get_fallbacks(tool_name)

        # After an error:
        action = recovery.handle_error(tool_name, error_message)
        # action tells you: retry | fallback | report | abort
    """

    def __init__(self):
        self._circuits: dict[str, CircuitState] = {}
        self._error_history: list[ErrorEvent] = []
        self._retry_counts: dict[str, int] = {}  # tool_name → consecutive retries

    def classify_error(self, error_message: str) -> ErrorClass:
        """Classify an error message into an ErrorClass."""
        lower = error_message.lower()
        for error_class, patterns in ERROR_PATTERNS.items():
            for pattern in patterns:
                if pattern.lower() in lower:
                    return error_class
        return ErrorClass.UNKNOWN

    def can_call(self, tool_name: str) -> bool:
        """Check if a tool can be called (circuit breaker check)."""
        circuit = self._circuits.get(tool_name)
        if circuit is None:
            return True
        return circuit.can_attempt()

    def handle_error(self, tool_name: str, error_message: str) -> dict:
        """Process an error and return recovery recommendation.

        Returns a dict with:
            - action: "retry" | "fallback" | "report" | "abort"
            - reason: Human-readable explanation
            - fallbacks: List of alternative tools (if action is "fallback")
            - retry_delay: Seconds to wait before retry (if action is "retry")
            - error_class: The classified error type
        """
        error_class = self.classify_error(error_message)

        # Record event
        event = ErrorEvent(
            tool_name=tool_name,
            error_class=error_class,
            error_message=error_message[:500],
        )
        self._error_history.append(event)

        # Update circuit breaker
        circuit = self._circuits.setdefault(tool_name, CircuitState())
        circuit.record_failure()

        # Check retry budget
        current_retries = self._retry_counts.get(tool_name, 0)
        max_retries = MAX_RETRIES.get(error_class, 0)

        result = {
            "error_class": error_class.value,
            "tool_name": tool_name,
        }

        if current_retries < max_retries and not circuit.is_open:
            # Can retry
            self._retry_counts[tool_name] = current_retries + 1
            delay = min(2 ** current_retries, 8)  # Exponential backoff, max 8s
            result["action"] = "retry"
            result["reason"] = (
                f"{error_class.value} error (attempt {current_retries + 1}/{max_retries}). "
                f"Retrying in {delay}s."
            )
            result["retry_delay"] = delay
        elif tool_name in FALLBACK_MAP:
            # Has fallback options
            fallbacks = FALLBACK_MAP[tool_name]
            available = [f for f in fallbacks if self.can_call(f)]
            if available:
                result["action"] = "fallback"
                result["reason"] = (
                    f"{tool_name} failed ({error_class.value}). "
                    f"Suggesting alternative: {available[0]}"
                )
                result["fallbacks"] = available
            else:
                result["action"] = "report"
                result["reason"] = (
                    f"{tool_name} failed and all fallbacks are also unavailable. "
                    f"Report to user."
                )
        elif circuit.is_open:
            result["action"] = "abort"
            result["reason"] = (
                f"Circuit breaker open for {tool_name} "
                f"({circuit.failures} consecutive failures). "
                f"Tool disabled for {CircuitState.COOLDOWN_SECONDS}s."
            )
        else:
            result["action"] = "report"
            result["reason"] = (
                f"{tool_name} failed with {error_class.value} error. "
                f"No retry or fallback available."
            )

        return result

    def record_success(self, tool_name: str):
        """Record a successful tool call (resets circuit breaker)."""
        circuit = self._circuits.get(tool_name)
        if circuit:
            circuit.record_success()
        self._retry_counts.pop(tool_name, None)

    def get_fallbacks(self, tool_name: str) -> list[str]:
        """Get available fallback tools for a given tool."""
        fallbacks = FALLBACK_MAP.get(tool_name, [])
        return [f for f in fallbacks if self.can_call(f)]

    def get_error_summary(self) -> str:
        """Generate error summary for session_status."""
        if not self._error_history:
            return "Errors: none"

        recent = self._error_history[-5:]
        lines = [f"Recent errors ({len(self._error_history)} total):"]
        for e in recent:
            status = "✓ recovered" if e.recovered else "✗ unrecovered"
            lines.append(
                f"  - {e.tool_name} [{e.error_class.value}] {status}"
            )

        open_circuits = [
            name for name, c in self._circuits.items() if c.is_open
        ]
        if open_circuits:
            lines.append(f"  Circuit breakers OPEN: {', '.join(open_circuits)}")

        return "\n".join(lines)

    def get_recovery_context(self) -> str:
        """Context injection for system prompt when errors are active."""
        open_circuits = [
            name for name, c in self._circuits.items() if c.is_open
        ]
        if not open_circuits:
            return ""

        return (
            "\n[WARNING] These tools are currently disabled due to repeated failures: "
            + ", ".join(open_circuits)
            + ". Use alternative approaches or inform the user."
        )
