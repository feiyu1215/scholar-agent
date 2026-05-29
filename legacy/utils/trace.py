"""
utils/trace.py — Structured execution trace logging.

Every tool call is recorded to .workspace/trace.jsonl for:
- Post-session analysis of token consumption
- Debugging failed tool calls
- Full paper revision trace for reproducibility

Design: Logging happens at the agent_loop dispatch layer (zero intrusion on handlers).
"""

import json
import time
from pathlib import Path
from typing import Optional

WORKSPACE = Path(".workspace")


def log_trace(
    tool_name: str,
    args: dict,
    output: str,
    duration_ms: float,
    tokens_before: Optional[dict] = None,
    tokens_after: Optional[dict] = None,
    error: Optional[str] = None,
    note: Optional[str] = None,
):
    """
    Append a single trace entry to .workspace/trace.jsonl.

    Called from agent_loop after each tool execution — handlers are unaware of tracing.
    """
    entry = {
        "ts": time.time(),
        "tool": tool_name,
        "args_summary": {k: str(v)[:100] for k, v in args.items()},
        "output_length": len(output) if output else 0,
        "output_preview": (output[:150] + "...") if output and len(output) > 150 else output,
        "duration_ms": round(duration_ms, 1),
    }

    # Token delta (if LLMClient stats available before/after)
    if tokens_before and tokens_after:
        entry["tokens_delta"] = {
            "input": tokens_after.get("total_input_tokens", 0) - tokens_before.get("total_input_tokens", 0),
            "output": tokens_after.get("total_output_tokens", 0) - tokens_before.get("total_output_tokens", 0),
        }

    if error:
        entry["error"] = error

    if note:
        entry["note"] = note

    trace_path = WORKSPACE / "trace.jsonl"
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    with open(trace_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def get_trace_summary() -> dict:
    """Read trace.jsonl and return aggregate stats (for /stats command)."""
    trace_path = WORKSPACE / "trace.jsonl"
    if not trace_path.exists():
        return {"total_calls": 0, "by_tool": {}}

    by_tool: dict = {}
    total_calls = 0
    total_tokens_in = 0
    total_tokens_out = 0
    total_errors = 0

    with open(trace_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            total_calls += 1
            tool = entry.get("tool", "unknown")

            if tool not in by_tool:
                by_tool[tool] = {"calls": 0, "total_duration_ms": 0, "errors": 0,
                                 "tokens_in": 0, "tokens_out": 0}

            by_tool[tool]["calls"] += 1
            by_tool[tool]["total_duration_ms"] += entry.get("duration_ms", 0)

            if entry.get("error"):
                by_tool[tool]["errors"] += 1
                total_errors += 1

            delta = entry.get("tokens_delta", {})
            by_tool[tool]["tokens_in"] += delta.get("input", 0)
            by_tool[tool]["tokens_out"] += delta.get("output", 0)
            total_tokens_in += delta.get("input", 0)
            total_tokens_out += delta.get("output", 0)

    return {
        "total_calls": total_calls,
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "total_errors": total_errors,
        "by_tool": by_tool,
    }
