"""
core/context_pipeline.py — Token management and context compression.

Contains:
- RETENTION_POLICY: per-tool retention rules
- estimate_tokens(): CJK-aware token estimator
- smart_compact(): Layer 1 rule-based compression
- auto_compact(): Layer 2 LLM-based conversation summary
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from core.state import WORKSPACE

# ============================================================
# Configuration
# ============================================================

TOKEN_THRESHOLD_SOFT = 30000   # Trigger smart_compact
TOKEN_THRESHOLD_HARD = 45000   # Trigger auto_compact (LLM summary)
KEEP_RECENT_TOOL_RESULTS = 5   # Minimum recent results to never compress

# Retention policies per tool — controls how aggressively results are compressed
# ALWAYS_KEEP: never compress (behavioral rules, user input)
# KEEP_UNTIL_SUPERSEDED: keep until a newer result from same tool replaces it
# COMPRESS_TO_SUMMARY: compress to first 100 chars after leaving recent window
RETENTION_POLICY = {
    # Always keep — these are behavioral/structural
    "read_agent_guidelines": "ALWAYS_KEEP",
    "ask_user": "ALWAYS_KEEP",
    "read_section": "KEEP_UNTIL_SUPERSEDED",
    "review_paper": "ALWAYS_KEEP",
    "architecture_diagnosis": "ALWAYS_KEEP",
    "consolidate_reviews": "ALWAYS_KEEP",
    # Keep until superseded — newer version replaces old
    "read_section_index": "KEEP_UNTIL_SUPERSEDED",
    "revision_progress": "KEEP_UNTIL_SUPERSEDED",
    "session_status": "KEEP_UNTIL_SUPERSEDED",
    "route_issues": "KEEP_UNTIL_SUPERSEDED",
    "read_issues": "KEEP_UNTIL_SUPERSEDED",
    "show_author_profile": "KEEP_UNTIL_SUPERSEDED",
    # Compress to summary — large outputs that lose value over time
    "rewrite_section": "COMPRESS_TO_SUMMARY",
    "generate_rewrite": "KEEP_UNTIL_SUPERSEDED",  # Must survive until commit_rewrite consumes it
    "commit_rewrite": "COMPRESS_TO_SUMMARY",
    "verify_rewrite_quality": "KEEP_UNTIL_SUPERSEDED",
    "edit_section": "COMPRESS_TO_SUMMARY",
    "parallel_rewrite": "COMPRESS_TO_SUMMARY",
    "deai_closed_loop": "COMPRESS_TO_SUMMARY",
    "deai_detect": "COMPRESS_TO_SUMMARY",
    "deai_diagnose": "COMPRESS_TO_SUMMARY",
    "deai_rewrite": "COMPRESS_TO_SUMMARY",
    "deai_verify": "COMPRESS_TO_SUMMARY",
    "diff_section": "COMPRESS_TO_SUMMARY",
    "verify_citations": "COMPRESS_TO_SUMMARY",
    "search_literature": "COMPRESS_TO_SUMMARY",
    "presubmission_check": "COMPRESS_TO_SUMMARY",
    "dry_run_estimate": "COMPRESS_TO_SUMMARY",
    # De-AI audit & voice profile — inform subsequent decisions
    "deai_audit": "KEEP_UNTIL_SUPERSEDED",
    "build_voice_profile": "KEEP_UNTIL_SUPERSEDED",
    # Goal & Plan tools (Wave 2)
    "set_goal": "ALWAYS_KEEP",
    "complete_goal": "KEEP_UNTIL_SUPERSEDED",
    "save_plan": "KEEP_UNTIL_SUPERSEDED",
    "load_plan": "KEEP_UNTIL_SUPERSEDED",
    "advance_plan": "KEEP_UNTIL_SUPERSEDED",
    "self_critique": "COMPRESS_TO_SUMMARY",
    # Learning tools (Wave 4)
    "record_lesson": "COMPRESS_TO_SUMMARY",
    "observe_edit": "COMPRESS_TO_SUMMARY",
}

# Default policy for tools not explicitly listed
_DEFAULT_RETENTION = "COMPRESS_TO_SUMMARY"


# ============================================================
# Token Estimation
# ============================================================

def estimate_tokens(messages: list) -> int:
    """Estimate token count with CJK awareness.

    Rules:
    - ASCII text: ~4 chars per token
    - CJK characters: ~1.5 chars per token
    """
    total_chars = 0
    cjk_chars = 0
    for msg in messages:
        content = ""
        if isinstance(msg.get("content"), str):
            content = msg["content"]
        elif isinstance(msg.get("content"), list):
            for part in msg["content"]:
                if isinstance(part, dict):
                    content += str(part.get("text", "")) + str(part.get("content", ""))
        # Also count tool_calls arguments
        for tc in msg.get("tool_calls", []):
            if isinstance(tc, dict) and "function" in tc:
                content += tc["function"].get("arguments", "")
        total_chars += len(content)
        for ch in content:
            if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f':
                cjk_chars += 1

    ascii_chars = total_chars - cjk_chars
    return int(ascii_chars / 4 + cjk_chars / 1.5)


# ============================================================
# Layer 1: Smart Compact (rule-based)
# ============================================================

def smart_compact(messages: list) -> list:
    """Smart context compaction based on per-tool retention policies.

    Strategy:
    1. Identify all tool-result messages
    2. For recent N results: never touch (regardless of policy)
    3. For older results: apply policy
       - ALWAYS_KEEP: never compress
       - KEEP_UNTIL_SUPERSEDED: keep only the latest result per tool
       - COMPRESS_TO_SUMMARY: truncate to preview
    """
    tool_indices = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "tool":
            tool_indices.append(i)

    if len(tool_indices) <= KEEP_RECENT_TOOL_RESULTS:
        return messages

    # Track latest occurrence of KEEP_UNTIL_SUPERSEDED tools by (tool_name, key_args)
    latest_by_key = {}  # (tool_name, args_key) -> highest index

    def _supersede_key(idx: int) -> tuple:
        """Build a tracking key from tool name + distinguishing arguments."""
        msg = messages[idx]
        tool_name = msg.get("name", "")
        tool_call_id = msg.get("tool_call_id", "")
        key_arg = ""
        for m in messages[:idx]:
            for tc in m.get("tool_calls", []):
                if isinstance(tc, dict) and tc.get("id") == tool_call_id:
                    try:
                        args = json.loads(tc.get("function", {}).get("arguments", "{}"))
                        key_arg = args.get("section_id", args.get("skill_name", args.get("topic", "")))
                    except (json.JSONDecodeError, AttributeError):
                        pass
                    break
            if key_arg:
                break
        return (tool_name, key_arg)

    for idx in tool_indices:
        tool_name = messages[idx].get("name", "")
        if RETENTION_POLICY.get(tool_name, _DEFAULT_RETENTION) == "KEEP_UNTIL_SUPERSEDED":
            key = _supersede_key(idx)
            latest_by_key[key] = idx

    # Process older results (not in recent window)
    to_process = tool_indices[:-KEEP_RECENT_TOOL_RESULTS]
    for idx in to_process:
        msg = messages[idx]
        content = msg.get("content", "")
        tool_name = msg.get("name", "")

        if not isinstance(content, str) or len(content) <= 150:
            continue

        policy = RETENTION_POLICY.get(tool_name, _DEFAULT_RETENTION)

        if policy == "ALWAYS_KEEP":
            continue
        elif policy == "KEEP_UNTIL_SUPERSEDED":
            key = _supersede_key(idx)
            if latest_by_key.get(key, -1) > idx:
                preview = content[:80].rstrip()
                messages[idx]["content"] = (
                    "[Superseded: " + str(len(content)) + " chars] " + preview + "..."
                )
        elif policy == "COMPRESS_TO_SUMMARY":
            preview = content[:100].rstrip()
            messages[idx]["content"] = (
                "[Compressed: " + str(len(content)) + " chars] " + preview + "..."
            )

    return messages


# ============================================================
# Layer 2: Auto Compact (LLM-based)
# ============================================================

async def auto_compact(messages: list, client) -> list:
    """Layer 2: LLM-based conversation summary when context too large.

    Improvements over naive version:
    - Saves full transcript for recovery
    - Preserves ALWAYS_KEEP tool results in the compressed context
    - Handles LLM failure gracefully (falls back to mechanical truncation)
    """
    transcript_dir = WORKSPACE / ".transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_dir / ("transcript_" + str(int(time.time())) + ".jsonl")
    with open(transcript_path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str) + "\n")

    print("\n[Context compressed. Transcript saved: " + str(transcript_path) + "]")

    # Collect ALWAYS_KEEP results to preserve
    preserved_results = []
    for msg in messages:
        if msg.get("role") == "tool":
            tool_name = msg.get("name", "")
            if RETENTION_POLICY.get(tool_name) == "ALWAYS_KEEP":
                content = msg.get("content", "")
                if content and not content.startswith("[Compressed:"):
                    preserved_results.append({"tool": tool_name, "content": content})

    # Summarize using LLM (with error handling)
    conversation_text = json.dumps(messages[-10:], default=str)[:20000]
    try:
        summary = await client.chat(
            system="Summarize this agent conversation for continuity. Include: what was accomplished, current state, pending tasks, key decisions made. Be specific about tool results and findings.",
            user=conversation_text,
            max_tokens=2000,
        )
    except Exception as e:
        # Fallback: mechanical summary from last few messages
        print(f"[Warning: auto_compact LLM call failed ({e}), using mechanical fallback]")
        summary = "Mechanical summary (LLM unavailable):\n"
        for msg in messages[-5:]:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if isinstance(content, str):
                summary += f"- {role}: {content[:200]}\n"

    # Build compressed context
    compressed = [
        {"role": "user", "content": "[Session compressed. Transcript: " + str(transcript_path) + "]\n\nSummary:\n" + summary},
    ]

    # Re-inject preserved results as context
    if preserved_results:
        preserved_text = "\n\n---\nPreserved tool results (ALWAYS_KEEP):\n"
        for pr in preserved_results[-5:]:  # Keep at most 5 preserved results
            preserved_text += f"\n[{pr['tool']}]: {pr['content'][:3000]}\n"
        compressed[0]["content"] += preserved_text

    return compressed
