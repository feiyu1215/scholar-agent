#!/usr/bin/env python3
"""
ScholarAgent — Academic Paper Review & Revision Agent

Architecture: Harness pattern (learn-claude-code style)
    - One agent loop
    - Domain-specific tools (paper parsing, section-level review/edit)
    - Human-in-the-loop via ask_user tool
    - Subagent pattern for parallel multi-role review
    - File-system as external memory (paper never lives in context)
    - Context compression for long sessions

The model decides. The harness executes. The model is the driver, the harness is the vehicle.

Usage:
    python main.py                          # Interactive REPL
    python main.py --paper paper.pdf        # Start with a paper
    python main.py --model gpt-4o           # Use specific model
    python main.py --budget minimal         # Review + guidance only, no rewrite
"""

from __future__ import annotations

import os
import sys
import json
import asyncio
import argparse
import time
from pathlib import Path
from typing import List, Dict

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from llm.client import LLMClient

# ============================================================
# Configuration
# ============================================================

WORKDIR = Path.cwd()
WORKSPACE = WORKDIR / ".workspace"

SYSTEM_PROMPT = """You are ScholarAgent, an expert academic paper review and revision assistant.

You operate in a workspace where papers are stored as section-level files, NOT in your conversation context.
This keeps your attention focused and token usage efficient.

## Budget Mode: {budget}

- full: auto_fix executes directly, confirm_fix asks user, guidance outputs instructions
- medium: auto_fix executes, confirm_fix outputs as guidance only, guidance outputs instructions
- minimal: ALL issues become guidance only — zero rewrite, zero token cost beyond review

## Your Workflow

Phase 1 — REVIEW:
1. Parse the paper into sections (parse_paper)
2. Read the section index to understand structure (read_section_index)
3. Run multi-role review with 5 parallel reviewers (review_paper)
4. Route issues: run route_issues to classify each issue into auto_fix / confirm_fix / guidance
5. Present routing report + review findings to user and WAIT for their response (ask_user)

Phase 2 — REVISE (only if user agrees AND budget allows):
1. Read revision state (revision_progress) to see what's pending
2. For each issue by priority:
   - auto_fix: rewrite_section directly, then run deai_audit
   - confirm_fix: generate_fix_proposal to show user the change, wait for approval, then execute if approved
   - guidance: generate_fix_proposal in guidance mode, output instructions only
3. After each fix, update revision_progress
4. After all revisions, run consistency_check
5. Present final diff summary to user (ask_user)

## Red Lines (NEVER violate)
1. Never modify the author's core thesis or causal direction
2. Never invent citations, data, or factual claims not in the original
3. De-AI fixes must not degrade expression quality

## Key Principles
- NEVER load the full paper into context. Always work section-by-section.
- Pause at these moments: after review + routing complete, after each confirm_fix proposal, after all revisions.
- Every edit must have a logged reason. Use edit_section for surgical edits, rewrite_section for full rewrites.
- Be a rigorous but fair reviewer. Anchor findings to specific text.

## Current workspace: {workspace}
"""

# ============================================================
# Tool Definitions (the harness)
# ============================================================

TOOLS = [
    {
        "name": "parse_paper",
        "description": "Parse a PDF/tex/md paper into section-level files. The paper is stored in .workspace/paper/, NOT in context. Returns a section index summary.",
        "input_schema": {
            "type": "object",
            "properties": {
                "paper_path": {"type": "string", "description": "Path to the paper file (PDF, tex, md, txt)"},
            },
            "required": ["paper_path"],
        },
    },
    {
        "name": "read_section_index",
        "description": "Read the lightweight section index — shows all sections with titles and word counts. Use this to understand paper structure without loading content.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_section",
        "description": "Read a single section by ID. Returns ONLY that section's content. If a revised version exists, returns the revised version.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string", "description": "Section ID from the index (e.g., '01_abstract', '02_introduction')"},
            },
            "required": ["section_id"],
        },
    },
    {
        "name": "review_paper",
        "description": "Run full multi-role academic review with 5 parallel reviewers (editor, theory, methodology, logic, literature). Each reviewer only sees relevant sections. Returns consolidated findings with score and revision roadmap.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "rewrite_section",
        "description": "Rewrite a single section to address review issues. Loads issues automatically. Saves revised version and logs changes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string", "description": "Which section to rewrite"},
                "custom_instructions": {"type": "string", "description": "Optional additional instructions from the user"},
            },
            "required": ["section_id"],
        },
    },
    {
        "name": "edit_section",
        "description": "Make a surgical edit to a section (string replacement). Use for small, targeted fixes rather than full rewrites.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string"},
                "old_text": {"type": "string", "description": "Exact text to replace"},
                "new_text": {"type": "string", "description": "Replacement text"},
                "reason": {"type": "string", "description": "Why this edit is needed"},
            },
            "required": ["section_id", "old_text", "new_text", "reason"],
        },
    },
    {
        "name": "diff_section",
        "description": "Show unified diff between original and revised version of a section.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string"},
            },
            "required": ["section_id"],
        },
    },
    {
        "name": "read_revision_log",
        "description": "Read the revision log — shows all changes made, when, and why. Optionally filter by section.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string", "description": "Optional: filter by section ID"},
            },
        },
    },
    {
        "name": "consistency_check",
        "description": "Quick consistency scan across all sections. Reads first/last lines of each section to detect logical flow issues without loading full content.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_issues",
        "description": "Read the review issues list from .workspace/review/issues.json",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ask_user",
        "description": "Pause execution and present information to the user. Wait for their response. Use when: review is complete, you need clarification on author intent, revision is complete, or any decision requires user input.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "What to show/ask the user"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: suggested response options",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "load_skill",
        "description": "Load domain knowledge from skills/ directory on demand. Returns relevant writing/review guidelines.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Skill to load: 'review_criteria', 'econ_writing', 'nature_style'"},
            },
            "required": ["skill_name"],
        },
    },
    # ── v2: Action Routing & Guidance Mode ──────────────────────────────────
    {
        "name": "route_issues",
        "description": "Route consolidated review issues through Red Line checks and budget ceiling. Each issue gets an effective_action (auto_fix/confirm_fix/guidance). Returns routing report with stats.",
        "input_schema": {
            "type": "object",
            "properties": {
                "budget": {"type": "string", "enum": ["full", "medium", "minimal"],
                           "description": "Budget mode (default: session budget)"},
            },
        },
    },
    {
        "name": "generate_fix_proposal",
        "description": "Dry-Run mode: generate a fix proposal for a specific issue WITHOUT executing it. For confirm_fix issues (shows exact before/after). For guidance issues (gives actionable instructions).",
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string", "description": "Issue ID (e.g., 'ISS-001')"},
                "section_id": {"type": "string", "description": "Optional: section to look at (auto-detected from issue if omitted)"},
            },
            "required": ["issue_id"],
        },
    },
    {
        "name": "approve_fix",
        "description": "Approve a confirm_fix proposal. Executes the proposed change and marks the issue as done. Also marks the category as 'seen' (future same-category issues auto_fix without confirmation).",
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string", "description": "Issue ID to approve"},
            },
            "required": ["issue_id"],
        },
    },
    {
        "name": "revision_progress",
        "description": "Show current revision progress: how many issues are done, pending, failed. Also shows de-AI audit results and Stata verification status.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "stata_verify",
        "description": "Run Stata statistical verification for a methodology issue flagged with needs_statistical_verification. Generates .do code, attempts execution via MCP, interprets results. Graceful degradation: if Stata unavailable, outputs .do code as guidance.",
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string", "description": "Issue ID to verify statistically"},
            },
            "required": ["issue_id"],
        },
    },
    {
        "name": "deai_audit",
        "description": "Run de-AI detection audit on a specific section. Returns naturalness score and detected AI signals. Typically called automatically after rewrite, but can be invoked manually.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string", "description": "Section to audit"},
                "scene": {"type": "string", "enum": ["S1", "S3"], "description": "S1=CS academic, S3=economics (default: S1)"},
            },
            "required": ["section_id"],
        },
    },
]

# ============================================================
# Tool Handlers
# ============================================================

def _handle_parse_paper(paper_path: str) -> str:
    from tools.paper_parser import parse_paper
    return parse_paper(paper_path, str(WORKSPACE))


def _handle_read_section_index() -> str:
    from tools.section_ops import read_section_index
    return read_section_index()


def _handle_read_section(section_id: str) -> str:
    from tools.section_ops import read_section
    return read_section(section_id)


def _handle_review_paper(provider: str = None, model: str = None) -> str:
    from tools.review_engine import review_paper
    return asyncio.run(review_paper(provider=provider, model=model))


def _handle_rewrite_section(section_id: str, custom_instructions: str = "",
                            provider: str = None, model: str = None) -> str:
    from tools.write_engine import rewrite_section
    return asyncio.run(rewrite_section(section_id, provider=provider, model=model,
                                       custom_instructions=custom_instructions))


def _handle_edit_section(section_id: str, old_text: str, new_text: str, reason: str) -> str:
    from tools.section_ops import edit_section
    return edit_section(section_id, old_text, new_text, reason)


def _handle_diff_section(section_id: str) -> str:
    from tools.section_ops import diff_section
    return diff_section(section_id)


def _handle_read_revision_log(section_id: str = None) -> str:
    from tools.section_ops import read_revision_log
    return read_revision_log(section_id)


def _handle_consistency_check() -> str:
    from tools.section_ops import consistency_check
    return consistency_check()


def _handle_read_issues() -> str:
    issues_path = WORKSPACE / "review" / "issues.json"
    if not issues_path.exists():
        return "No review issues found. Run review_paper first."
    return issues_path.read_text(encoding="utf-8")


def _handle_load_skill(skill_name: str) -> str:
    skills_dir = Path("skills")
    skill_file = skills_dir / f"{skill_name}.md"
    if not skill_file.exists():
        available = [f.stem for f in skills_dir.glob("*.md")] if skills_dir.exists() else []
        return f"Skill '{skill_name}' not found. Available: {available}"
    content = skill_file.read_text(encoding="utf-8")
    # Truncate for context efficiency — load key sections only
    if len(content) > 4000:
        content = content[:4000] + f"\n\n[... truncated, {len(content)} total chars]"
    return content


# ── v2: Action Routing & Guidance Mode Handlers ──────────────────────────

# Session-level state
_session_budget = "full"


def _handle_route_issues(budget: str = None) -> str:
    from tools.action_router import route_issues, format_routing_report
    from tools.revision_state import load_state, register_issues, get_seen_categories

    effective_budget = budget or _session_budget

    # Load issues from consolidated review
    issues_path = WORKSPACE / "review" / "consolidated.json"
    if not issues_path.exists():
        return "Error: No consolidated review found. Run review_paper first."

    consolidated = json.loads(issues_path.read_text(encoding="utf-8"))
    issues = consolidated.get("issues", [])
    if not issues:
        return "No issues found in consolidated review."

    # Load state and route
    state = load_state()
    seen = get_seen_categories(state)
    routed, stats = route_issues(issues, budget=effective_budget, seen_categories=seen)

    # Register into revision state
    routed_dicts = [r.to_dict() for r in routed]
    register_issues(state, routed_dicts)

    # Save routed issues
    routed_path = WORKSPACE / "review" / "routed_issues.json"
    routed_path.write_text(
        json.dumps(routed_dicts, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return format_routing_report(routed, stats)


def _handle_generate_fix_proposal(issue_id: str, section_id: str = None,
                                   provider: str = None, model: str = None) -> str:
    from tools.write_engine import generate_fix_proposal, format_proposal_for_user

    # Load the routed issue
    routed_path = WORKSPACE / "review" / "routed_issues.json"
    if not routed_path.exists():
        return "Error: No routed issues. Run route_issues first."

    routed = json.loads(routed_path.read_text(encoding="utf-8"))
    issue = next((i for i in routed if i.get("id") == issue_id), None)
    if not issue:
        return f"Error: Issue '{issue_id}' not found in routed issues."

    proposal = asyncio.run(generate_fix_proposal(
        issue, section_id=section_id, provider=provider, model=model
    ))
    return format_proposal_for_user(proposal)


def _handle_approve_fix(issue_id: str, provider: str = None, model: str = None) -> str:
    from tools.revision_state import (
        load_state, update_issue_status, mark_category_confirmed
    )
    from tools.write_engine import rewrite_section

    # Load the proposal
    proposal_path = WORKSPACE / "proposals" / f"{issue_id}.json"
    if not proposal_path.exists():
        return f"Error: No proposal found for {issue_id}. Run generate_fix_proposal first."

    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    section_id = proposal.get("section_id", "")

    if not section_id:
        return f"Error: Proposal for {issue_id} has no section_id."

    # Execute the fix via rewrite_section with the proposal as custom instruction
    custom = (
        f"Apply this APPROVED fix for {issue_id}:\n"
        f"Current: {proposal.get('current_text', '')}\n"
        f"Change to: {proposal.get('proposed_text', '')}\n"
        f"Rationale: {proposal.get('rationale', '')}"
    )
    result = asyncio.run(rewrite_section(
        section_id, provider=provider, model=model, custom_instructions=custom
    ))

    # Update state
    state = load_state()
    update_issue_status(state, issue_id, "done", note="approved by user")

    # Mark category as confirmed (future same-category → auto_fix)
    routed_path = WORKSPACE / "review" / "routed_issues.json"
    if routed_path.exists():
        routed = json.loads(routed_path.read_text(encoding="utf-8"))
        issue = next((i for i in routed if i.get("id") == issue_id), None)
        if issue:
            mark_category_confirmed(state, issue.get("category", ""))

    return f"✅ Fix approved and applied for {issue_id}.\n{result}"


def _handle_revision_progress() -> str:
    from tools.revision_state import load_state, format_progress
    state = load_state()
    return format_progress(state)


def _handle_stata_verify(issue_id: str, provider: str = None, model: str = None) -> str:
    from tools.stata_verify import stata_verify, format_stata_result
    from tools.revision_state import load_state, record_stata_result
    from tools.section_ops import read_section

    # Load the routed issue
    routed_path = WORKSPACE / "review" / "routed_issues.json"
    if not routed_path.exists():
        return "Error: No routed issues. Run route_issues first."

    routed = json.loads(routed_path.read_text(encoding="utf-8"))
    issue = next((i for i in routed if i.get("id") == issue_id), None)
    if not issue:
        return f"Error: Issue '{issue_id}' not found."

    # Get methods context from paper
    methods_context = ""
    index_path = WORKSPACE / "paper" / "section_index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        for entry in index:
            if any(kw in entry.get("slug", "").lower() for kw in ["method", "data", "empiric"]):
                sec_path = Path(entry["file"])
                if sec_path.exists():
                    methods_context += sec_path.read_text(encoding="utf-8")[:2000]
                break

    result = asyncio.run(stata_verify(
        issue, methods_context=methods_context, provider=provider, model=model
    ))

    # Record in state
    state = load_state()
    record_stata_result(state, issue_id, result)

    return format_stata_result(result)


def _handle_deai_audit(section_id: str, scene: str = "S1",
                        provider: str = None, model: str = None) -> str:
    from tools.deai_engine import deai_audit, format_deai_result
    from tools.section_ops import read_section

    # Read the section (prefer revised version)
    rev_path = WORKSPACE / "revisions" / f"{section_id}_v2.md"
    if rev_path.exists():
        text = rev_path.read_text(encoding="utf-8")
    else:
        # Try original
        index_path = WORKSPACE / "paper" / "section_index.json"
        if not index_path.exists():
            return "Error: No paper parsed."
        index = json.loads(index_path.read_text(encoding="utf-8"))
        entry = next((e for e in index if section_id in e.get("id", "")), None)
        if not entry:
            return f"Error: Section '{section_id}' not found."
        sec_path = Path(entry["file"])
        if not sec_path.exists():
            return f"Error: Section file not found: {entry['file']}"
        text = sec_path.read_text(encoding="utf-8")

    verdict = asyncio.run(deai_audit(text, scene=scene, provider=provider, model=model))
    return format_deai_result(verdict)


# Global state for ask_user
_user_response = None


def _handle_ask_user(message: str, options: list = None) -> str:
    """This is special: it pauses the agent loop and waits for user input."""
    global _user_response
    print("\n" + "=" * 60)
    print("🔔 AGENT PAUSED — Waiting for your input")
    print("=" * 60)
    print(f"\n{message}\n")
    if options:
        for i, opt in enumerate(options, 1):
            print(f"  {i}. {opt}")
        print()
    try:
        response = input("\033[36mYour response >> \033[0m")
    except (EOFError, KeyboardInterrupt):
        response = "continue"
    _user_response = response
    return f"User responded: {response}"


# Tool dispatch map
TOOL_HANDLERS = {
    "parse_paper": lambda **kw: _handle_parse_paper(kw["paper_path"]),
    "read_section_index": lambda **kw: _handle_read_section_index(),
    "read_section": lambda **kw: _handle_read_section(kw["section_id"]),
    "review_paper": lambda **kw: _handle_review_paper(),
    "rewrite_section": lambda **kw: _handle_rewrite_section(
        kw["section_id"], kw.get("custom_instructions", "")),
    "edit_section": lambda **kw: _handle_edit_section(
        kw["section_id"], kw["old_text"], kw["new_text"], kw["reason"]),
    "diff_section": lambda **kw: _handle_diff_section(kw["section_id"]),
    "read_revision_log": lambda **kw: _handle_read_revision_log(kw.get("section_id")),
    "consistency_check": lambda **kw: _handle_consistency_check(),
    "read_issues": lambda **kw: _handle_read_issues(),
    "ask_user": lambda **kw: _handle_ask_user(kw["message"], kw.get("options")),
    "load_skill": lambda **kw: _handle_load_skill(kw["skill_name"]),
    # v2: Action Routing & Guidance
    "route_issues": lambda **kw: _handle_route_issues(kw.get("budget")),
    "generate_fix_proposal": lambda **kw: _handle_generate_fix_proposal(
        kw["issue_id"], kw.get("section_id")),
    "approve_fix": lambda **kw: _handle_approve_fix(kw["issue_id"]),
    "revision_progress": lambda **kw: _handle_revision_progress(),
    # v2: Statistical Verification & De-AI
    "stata_verify": lambda **kw: _handle_stata_verify(kw["issue_id"]),
    "deai_audit": lambda **kw: _handle_deai_audit(
        kw["section_id"], kw.get("scene", "S1")),
}

# ============================================================
# Context Compression (s06 pattern)
# ============================================================

KEEP_RECENT = 3
TOKEN_THRESHOLD = 40000


def estimate_tokens(messages: list) -> int:
    """Rough estimate: ~4 chars per token."""
    return len(json.dumps(messages, default=str)) // 4


def micro_compact(messages: list) -> list:
    """Layer 1: Replace old tool results with placeholders (except read_section results)."""
    tool_results = []
    for msg_idx, msg in enumerate(messages):
        if msg["role"] == "user" and isinstance(msg.get("content"), list):
            for part_idx, part in enumerate(msg["content"]):
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    tool_results.append((msg_idx, part_idx, part))

    if len(tool_results) <= KEEP_RECENT:
        return messages

    # Keep recent results and read_section/ask_user results
    preserve_tools = {"read_section", "ask_user", "review_paper"}
    to_clear = tool_results[:-KEEP_RECENT]

    for _, _, result in to_clear:
        content = result.get("content", "")
        if not isinstance(content, str) or len(content) <= 100:
            continue
        # Don't compress important tool results
        tool_id = result.get("tool_use_id", "")
        # Simple heuristic: if it's a long result and not in preserve set, compress
        result["content"] = f"[Previous tool result compressed — {len(content)} chars]"

    return messages


def auto_compact(messages: list, client) -> list:
    """Layer 2: Summarize entire conversation when too long."""
    # Save transcript
    transcript_dir = WORKSPACE / ".transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_dir / f"transcript_{int(time.time())}.jsonl"
    with open(transcript_path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str) + "\n")

    print(f"\n[Context compressed. Transcript saved: {transcript_path}]")

    # Summarize using LLM
    conversation_text = json.dumps(messages[-10:], default=str)[:20000]  # Last 10 messages
    summary = asyncio.run(client.chat(
        system="Summarize this agent conversation for continuity. Include: what was accomplished, current state, pending tasks, key decisions.",
        user=conversation_text,
        max_tokens=1500,
    ))

    return [
        {"role": "user", "content": f"[Session compressed. Transcript: {transcript_path}]\n\nSummary:\n{summary}"},
    ]


# ============================================================
# The Agent Loop (the core pattern — same as learn-claude-code s01)
# ============================================================

def agent_loop(messages: list, client: LLMClient):
    """The agent loop. Model decides, harness executes."""
    while True:
        # Layer 1: micro compress before each call
        micro_compact(messages)

        # Layer 2: auto compress if too long
        if estimate_tokens(messages) > TOKEN_THRESHOLD:
            print("[Auto-compressing context...]")
            messages[:] = auto_compact(messages, client)

        # Call LLM
        system = SYSTEM_PROMPT.format(workspace=str(WORKSPACE), budget=_session_budget)
        response = asyncio.run(client.chat_messages(
            messages=[{"role": "system", "content": system}] + messages,
            max_tokens=4000,
            temperature=0.1,
        ))

        # The response is text (not tool_use format since we're using OpenAI API)
        # We need to parse tool calls from the response
        tool_calls = _parse_tool_calls(response)

        if not tool_calls:
            # No tool calls — model is responding to user
            messages.append({"role": "assistant", "content": response})
            print(f"\n\033[32m{response}\033[0m\n")
            return

        # Execute tool calls
        messages.append({"role": "assistant", "content": response})
        results = []
        for call in tool_calls:
            tool_name = call["name"]
            args = call["args"]
            handler = TOOL_HANDLERS.get(tool_name)
            if handler:
                try:
                    output = handler(**args)
                except Exception as e:
                    output = f"Error: {type(e).__name__}: {e}"
                print(f"\033[33m> {tool_name}({', '.join(f'{k}={repr(v)[:50]}' for k,v in args.items())})\033[0m")
                print(f"  {str(output)[:300]}")
            else:
                output = f"Unknown tool: {tool_name}"
            results.append({"tool": tool_name, "result": output})

        # Append tool results as user message (simulating tool_result)
        results_text = "\n\n".join(
            f"[Tool: {r['tool']}]\n{r['result']}" for r in results
        )
        messages.append({"role": "user", "content": f"Tool results:\n\n{results_text}"})


def _parse_tool_calls(response: str) -> List[Dict]:
    """Parse tool calls from LLM response.
    
    The model should output tool calls in this format:
    <tool_call>{"name": "tool_name", "args": {"key": "value"}}</tool_call>
    
    Or we detect function-call-like patterns.
    """
    import re

    calls = []

    # Pattern 1: <tool_call> JSON </tool_call>
    pattern = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
    matches = pattern.findall(response)
    for match in matches:
        try:
            call = json.loads(match.strip())
            if "name" in call:
                calls.append({"name": call["name"], "args": call.get("args", {})})
        except json.JSONDecodeError:
            continue

    # Pattern 2: ```tool\n{JSON}\n```
    pattern2 = re.compile(r"```tool\s*\n(.*?)\n```", re.DOTALL)
    matches2 = pattern2.findall(response)
    for match in matches2:
        try:
            call = json.loads(match.strip())
            if "name" in call:
                calls.append({"name": call["name"], "args": call.get("args", {})})
        except json.JSONDecodeError:
            continue

    return calls


# ============================================================
# Entry Point
# ============================================================

def main():
    global _session_budget

    parser = argparse.ArgumentParser(description="ScholarAgent — Academic Paper Review & Revision")
    parser.add_argument("--paper", help="Path to paper file to review")
    parser.add_argument("--provider", default=None, help="LLM provider name (default: openai)")
    parser.add_argument("--model", default=None, help="Model name")
    parser.add_argument("--budget", default="full", choices=["full", "medium", "minimal"],
                        help="Budget mode: full (auto-fix+confirm+guidance), "
                             "medium (auto-fix+guidance only), minimal (guidance only)")
    args = parser.parse_args()

    provider = args.provider or os.environ.get("LLM_PROVIDER", "openai")
    _session_budget = args.budget
    client = LLMClient(model=args.model, provider=provider)

    # Initialize revision state with budget
    from tools.revision_state import init_state
    init_state(budget=_session_budget)

    print("=" * 60)
    print("  ScholarAgent v2 — Academic Paper Review & Revision")
    print(f"  Provider: {client.provider_name} | Model: {client.model}")
    print(f"  Budget: {_session_budget} | Workspace: {WORKSPACE}")
    print("=" * 60)
    budget_desc = {
        "full": "Auto-fix + Confirm + Guidance (full revision)",
        "medium": "Auto-fix + Guidance only (no user confirmation needed)",
        "minimal": "Guidance only (zero rewrite, review + advice)",
    }
    print(f"  Mode: {budget_desc[_session_budget]}")
    print("\nCommands: type your request, or 'q' to quit, '/stats' for token usage\n")

    # Create workspace
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    history = []

    # If paper provided, start with it
    if args.paper:
        history.append({"role": "user",
                        "content": f"Please review this paper: {args.paper}"})
        agent_loop(history, client)

    # Interactive REPL
    while True:
        try:
            query = input("\033[36mscholar >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break

        if query.strip().lower() in ("q", "quit", "exit"):
            break
        if query.strip() == "/stats":
            print(json.dumps(client.stats(), indent=2))
            continue
        if query.strip() == "/history":
            print(f"Messages: {len(history)}, Tokens: ~{estimate_tokens(history)}")
            continue
        if not query.strip():
            continue

        history.append({"role": "user", "content": query})
        agent_loop(history, client)

    # Final stats
    print(f"\n{'=' * 60}")
    print("Session Stats:")
    print(json.dumps(client.stats(), indent=2))
    print("=" * 60)


if __name__ == "__main__":
    main()
