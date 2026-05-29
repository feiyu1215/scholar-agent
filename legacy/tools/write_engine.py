"""
tools/write_engine.py — Section-level rewriting guided by review issues.

Design principle: rewrite ONE section at a time. Never load the full paper.
Each rewrite call receives:
  - The section content (from filesystem)
  - The specific issues flagged for that section
  - Domain knowledge (loaded on demand from skills/)

The agent decides WHICH section to rewrite and in what order.
This tool just executes a single rewrite operation.
"""

from __future__ import annotations

import json
import asyncio
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.token_budget import calculate_max_tokens

from llm.client import LLMClient
from llm.router import get_model_for_task

WORKSPACE = Path(".workspace")

REWRITE_SYSTEM_PROMPT = """You are an expert academic writing assistant. 
You are rewriting a specific section of a research paper to address reviewer feedback.

Rules:
1. Fix ONLY the issues listed. Do not rewrite parts that are fine.
2. Preserve the author's voice and core arguments.
3. Do not invent data, references, or claims not in the original.
4. If an issue requires information you don't have (e.g., new data, author's intent), 
   mark it with [AUTHOR_INPUT_NEEDED: description] instead of guessing.
5. Return the COMPLETE revised section (not just the changed parts).
6. After the revised text, add a brief "Changes Made" summary listing what you changed and why.

{domain_knowledge}"""


async def rewrite_section(section_id: str, provider: str = None, model: str = None,
                          custom_instructions: str = "",
                          fix_complexity: str = "paragraph_level") -> str:
    """Rewrite a single section based on review issues.
    
    Returns: summary of changes made (the full text is saved to filesystem).
    """
    # Load section content
    index = _load_index()
    entry = _find_section(section_id, index)
    if not entry:
        return f"Error: Section '{section_id}' not found."

    # Read current version
    rev_path = WORKSPACE / "revisions" / f"{section_id}_v2.md"
    if rev_path.exists():
        content = rev_path.read_text(encoding="utf-8")
    else:
        content = Path(entry["file"]).read_text(encoding="utf-8")

    # Load issues for this section
    issues = _get_issues_for_section(section_id)
    if not issues and not custom_instructions:
        return f"No issues found for {section_id} and no custom instructions provided."

    # Load domain knowledge if available
    domain_knowledge = _load_domain_knowledge(entry.get("title", ""))

    # Build prompt
    client = LLMClient(model=model, max_concurrent=3, provider=provider)

    system = REWRITE_SYSTEM_PROMPT.format(
        domain_knowledge=f"\nDomain guidance:\n{domain_knowledge}" if domain_knowledge else ""
    )

    user_parts = [f"## Section to revise: {entry['title']}\n\n{content}"]

    if issues:
        user_parts.append(f"\n\n## Issues to address:\n{json.dumps(issues, indent=2, ensure_ascii=False)}")

    if custom_instructions:
        user_parts.append(f"\n\n## Additional instructions from author:\n{custom_instructions}")

    # Dynamic token budget based on section length and fix complexity
    max_tokens = calculate_max_tokens(content, fix_complexity)

    response = await client.chat(
        system=system,
        user="\n".join(user_parts),
        max_tokens=max_tokens,
        temperature=0.1,
        model=get_model_for_task("rewrite_section"),
    )

    # Parse response: extract revised text and changes summary
    revised_text, changes_summary = _parse_rewrite_response(response)

    if not revised_text:
        return f"Error: Could not parse rewrite output for {section_id}. Raw response saved."

    # Save revised version
    rev_dir = WORKSPACE / "revisions"
    rev_dir.mkdir(parents=True, exist_ok=True)
    rev_path = rev_dir / f"{section_id}_v2.md"
    rev_path.write_text(revised_text, encoding="utf-8")

    # ── v2: De-AI Audit (PEV Loop) ──────────────────────────────────────
    # Only run if text actually changed substantively
    deai_summary = ""
    if _text_substantially_changed(content, revised_text):
        deai_summary = await _run_deai_audit(
            section_id, revised_text, content, rev_path, provider, model
        )
    else:
        deai_summary = "[De-AI: skipped — no substantial change from original]"

    # ── v3: Post-Edit Verification ──────────────────────────────────────
    # Three-layer check: consistency + voice drift + AI regression
    # Note: De-AI audit may have updated rev_path, so re-read the final text
    final_text = rev_path.read_text(encoding="utf-8") if rev_path.exists() else revised_text
    verify_summary = ""
    try:
        from tools.post_edit_verify import verify_edit, format_verification_result
        verify_result = verify_edit(
            section_id=section_id,
            old_text=content,
            new_text=final_text,
        )
        if not verify_result.passed:
            verify_summary = (
                f"\n⚠️ Post-Edit Verification FAILED:\n"
                f"{format_verification_result(verify_result, section_id)}"
            )
        else:
            verify_summary = "\n✅ Post-Edit Verification passed."
    except Exception as e:
        verify_summary = f"\n[Post-Edit Verify: skipped — {e}]"

    # ── Score tracking for rewrite ──
    try:
        import time as _time
        from utils.score_tracker import ScoreSnapshot, record_score
        snapshot = ScoreSnapshot(
            timestamp=_time.strftime("%Y-%m-%dT%H:%M:%S"),
            overall_score=0.0,  # Will be updated after re-review
            dimension_scores={},
            issues_remaining=0,
            must_fix_remaining=0,
            trigger=f"rewrite_{section_id}",
            sections_modified=[section_id],
        )
        record_score(snapshot)
    except Exception:
        pass  # Non-fatal

    # ── Memory persistence for rewrite ──
    try:
        from utils.memory.integration import remember_rewrite, get_paper_id
        _meta = json.loads((WORKSPACE / "paper" / "metadata.json").read_text(encoding="utf-8"))
        _paper_id = get_paper_id(_meta.get("source_file", ""))
        _verify_passed = "✅" in verify_summary
        remember_rewrite(
            paper_id=_paper_id,
            section_id=section_id,
            changes_summary=changes_summary,
            verify_passed=_verify_passed,
        )
    except Exception:
        pass  # Non-fatal

    # Log
    _log_revision(section_id, changes_summary)

    return (f"Revised {section_id} ({len(content)} → {len(final_text)} chars)\n"
            f"Changes: {changes_summary}\n"
            f"{deai_summary}\n"
            f"{verify_summary}\n"
            f"[Stats: {client.stats()['total_input_tokens']} in / {client.stats()['total_output_tokens']} out]")


def _get_issues_for_section(section_id: str) -> list:
    """Get review issues relevant to a specific section."""
    issues_path = WORKSPACE / "review" / "issues.json"
    if not issues_path.exists():
        return []

    all_issues = json.loads(issues_path.read_text(encoding="utf-8"))
    relevant = []
    for issue in all_issues:
        location = issue.get("location", "").lower()
        if section_id.lower() in location or any(
            slug in location for slug in section_id.lower().split("_")
        ):
            relevant.append(issue)

    return relevant


def _load_domain_knowledge(section_title: str) -> str:
    """Load relevant domain knowledge from skills/ directory on demand (s05 pattern)."""
    skills_dir = Path("skills")
    if not skills_dir.exists():
        return ""

    # Simple keyword matching to find relevant skill files
    title_lower = section_title.lower()
    relevant_content = []

    for skill_file in skills_dir.glob("*.md"):
        skill_name = skill_file.stem.lower()
        # Load if the skill seems relevant to the section
        if any(kw in title_lower for kw in ["introduction", "abstract", "conclusion", "result"]):
            if "writing" in skill_name or "econ" in skill_name:
                content = skill_file.read_text(encoding="utf-8")
                # Only load relevant portions (first 2000 chars as guidance)
                relevant_content.append(content[:2000])
        elif "method" in title_lower:
            if "method" in skill_name or "review" in skill_name:
                content = skill_file.read_text(encoding="utf-8")
                relevant_content.append(content[:2000])

    return "\n---\n".join(relevant_content[:2])  # Max 2 skill files loaded


def _parse_rewrite_response(response: str) -> tuple[str, str]:
    """Parse the LLM rewrite response into revised text and changes summary."""
    # Look for "Changes Made" marker
    markers = ["## Changes Made", "### Changes Made", "**Changes Made**", "Changes Made:"]
    for marker in markers:
        if marker in response:
            parts = response.split(marker, 1)
            revised_text = parts[0].strip()
            changes_summary = parts[1].strip()[:500]
            return revised_text, changes_summary

    # If no marker found, treat everything as revised text
    return response.strip(), "Changes made (no explicit summary provided)"


def _log_revision(section_id: str, changes_summary: str):
    """Append to revision log."""
    import time
    log_path = WORKSPACE / "revisions" / "revision_log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "section_id": section_id,
        "reason": changes_summary[:300],
        "type": "rewrite",
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _load_index() -> list:
    index_path = WORKSPACE / "paper" / "section_index.json"
    if not index_path.exists():
        return []
    return json.loads(index_path.read_text(encoding="utf-8"))


def _find_section(section_id: str, index: list) -> Optional[dict]:
    for entry in index:
        if entry["id"] == section_id or section_id in entry["id"]:
            return entry
    return None


def _load_paper_metadata() -> Optional[dict]:
    """Load paper metadata from .workspace/paper/metadata.json if available.
    
    Supports 'discipline' field for explicit scene routing:
      - "economics", "finance", "business" → S3
      - "chinese" → S2
      - Any other value → S1
    """
    meta_path = WORKSPACE / "paper" / "metadata.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# v2: De-AI Integration Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _text_substantially_changed(original: str, revised: str) -> bool:
    """Check if the rewrite made meaningful changes (not just whitespace/formatting)."""
    # Normalize for comparison
    orig_normalized = " ".join(original.split())
    rev_normalized = " ".join(revised.split())
    
    if orig_normalized == rev_normalized:
        return False
    
    # Simple heuristic: if less than 5% of characters changed, not substantial
    shorter = min(len(orig_normalized), len(rev_normalized))
    if shorter == 0:
        return True
    
    # Count differing characters (rough approximation)
    diff_count = abs(len(orig_normalized) - len(rev_normalized))
    # Also sample first 1000 chars for character-level diff
    sample_len = min(1000, shorter)
    char_diffs = sum(1 for a, b in zip(orig_normalized[:sample_len], rev_normalized[:sample_len]) if a != b)
    diff_count += char_diffs
    
    return (diff_count / shorter) > 0.05


async def _run_deai_audit(
    section_id: str,
    revised_text: str,
    original_text: str,
    rev_path: Path,
    provider: str = None,
    model: str = None,
) -> str:
    """Run de-AI audit on revised text. Fixes in place if needed.
    
    Integration: loads review hints for this section so DeAI is aware
    of expression issues the reviewer already flagged.
    """
    from tools.deai_engine import deai_audit_and_fix, format_deai_result, detect_scene
    from tools.revision_state import load_state, record_deai_result
    from tools.review_deai_bridge import load_hints_for_section, format_hints_for_prompt
    
    try:
        # Auto-detect scene from text content and paper metadata
        paper_metadata = _load_paper_metadata()
        scene = detect_scene(revised_text, metadata=paper_metadata)
        
        # Load reviewer context for this section (Review→DeAI integration)
        hints = load_hints_for_section(section_id)
        hints_prompt = format_hints_for_prompt(hints)
        
        final_text, verdict, fixes = await deai_audit_and_fix(
            revised_text, 
            original_text=original_text,
            scene=scene,
            provider=provider, 
            model=model,
            review_hints=hints_prompt,
            review_hints_structured=hints,  # structured hints for dimension bias
        )
        
        # If fixes were applied, update the saved revision
        if fixes and final_text != revised_text:
            rev_path.write_text(final_text, encoding="utf-8")
        
        # Record in revision state
        state = load_state()
        record_deai_result(state, section_id, verdict.to_dict())
        
        return format_deai_result(verdict, fixes)
    
    except Exception as e:
        return f"[De-AI: audit error — {type(e).__name__}: {e}]"


# ─────────────────────────────────────────────────────────────────────────────
# v2: Fix Proposal (Dry-Run mode for confirm_fix / guidance)
# ─────────────────────────────────────────────────────────────────────────────

FIX_PROPOSAL_PROMPT = """You are generating a FIX PROPOSAL for a reviewer-identified issue.
You are NOT executing the fix. You are showing the author WHAT you would change and WHY.

Output format:
1. "current_text": the exact sentence(s) from the paper that would be modified
2. "proposed_text": what it would look like after the fix
3. "rationale": why this change addresses the issue
4. "risk_notes": any concerns (e.g., might change meaning, needs author verification)
5. "author_decision_needed": what the author needs to confirm before this can be applied

Be specific. Quote exact text. Keep proposed changes minimal (sentence-level when possible).
If the issue CANNOT be fixed without author input (new data, new experiment), say so clearly
in "author_decision_needed" and leave "proposed_text" as null.

{domain_knowledge}"""

GUIDANCE_PROMPT = """You are generating REVISION GUIDANCE for a reviewer-identified issue.
The author will implement this fix themselves. Your job is to give clear, actionable instructions.

Output a JSON object with:
1. "what_to_change": specific description of what needs changing
2. "where": exact location (section + sentence/paragraph reference)
3. "how": step-by-step instructions for the fix
4. "examples": 1-2 example rewrites (if applicable, otherwise null)
5. "common_pitfalls": what to avoid when making this fix
6. "priority": "must_fix" | "should_fix" | "consider"

Be concrete. Reference the paper's actual text. Avoid vague advice like "improve clarity"."""


async def generate_fix_proposal(
    issue: dict, 
    section_id: str = None,
    provider: str = None, 
    model: str = None,
) -> dict:
    """
    Dry-Run mode: generate a fix proposal WITHOUT executing it.
    Used for confirm_fix issues (shows user what would happen) and
    as guidance output for guidance issues.
    
    Returns: proposal dict (saved to .workspace/proposals/{issue_id}.json)
    """
    # Determine section from issue if not provided
    if not section_id:
        location = issue.get("location", {})
        section_id = location.get("section_id", "")
    
    # Load section content
    index = _load_index()
    entry = _find_section(section_id, index)
    section_content = ""
    if entry:
        sec_path = Path(entry["file"])
        if sec_path.exists():
            section_content = sec_path.read_text(encoding="utf-8")

    # Choose mode based on action_type
    action_type = issue.get("effective_action", issue.get("action_type", "guidance"))
    
    if action_type == "guidance":
        system_prompt = GUIDANCE_PROMPT
    else:
        # confirm_fix → show exact proposed change
        domain_knowledge = _load_domain_knowledge(entry.get("title", "") if entry else "")
        system_prompt = FIX_PROPOSAL_PROMPT.format(
            domain_knowledge=f"\nDomain guidance:\n{domain_knowledge}" if domain_knowledge else ""
        )

    client = LLMClient(model=model, max_concurrent=3, provider=provider)

    user_msg = (
        f"## Issue to address:\n{json.dumps(issue, indent=2, ensure_ascii=False)}\n\n"
        f"## Section content:\n{section_content[:3000]}"
    )

    response = await client.chat(
        system=system_prompt,
        user=user_msg,
        max_tokens=2000,
        temperature=0.1,
        model=get_model_for_task("fix_proposal"),
    )

    # Parse JSON response
    proposal = _parse_proposal_response(response, issue)
    proposal["issue_id"] = issue.get("id", "unknown")
    proposal["action_type"] = action_type
    proposal["section_id"] = section_id

    # Save proposal
    proposal_dir = WORKSPACE / "proposals"
    proposal_dir.mkdir(parents=True, exist_ok=True)
    proposal_path = proposal_dir / f"{proposal['issue_id']}.json"
    proposal_path.write_text(
        json.dumps(proposal, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Log
    _log_revision(section_id, f"proposal generated for {proposal['issue_id']}")

    return proposal


def _parse_proposal_response(response: str, issue: dict) -> dict:
    """Parse the LLM proposal response into a structured dict."""
    response = response.strip()
    
    # Try to extract JSON
    if response.startswith("```"):
        response = response.split("```")[1]
        if response.startswith("json"):
            response = response[4:]
    
    try:
        parsed = json.loads(response)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Fallback: wrap raw response
    return {
        "raw_response": response[:2000],
        "parse_error": True,
        "what_to_change": issue.get("description", ""),
        "how": issue.get("suggestion", ""),
    }


def format_proposal_for_user(proposal: dict) -> str:
    """Format a proposal as readable text for user confirmation."""
    lines = []
    issue_id = proposal.get("issue_id", "?")
    action = proposal.get("action_type", "?")
    
    lines.append(f"{'─' * 50}")
    lines.append(f"📋 Fix Proposal [{issue_id}] (action: {action})")
    lines.append(f"{'─' * 50}")

    if action == "guidance":
        lines.append(f"\n🎯 What to change: {proposal.get('what_to_change', 'N/A')}")
        lines.append(f"📍 Where: {proposal.get('where', 'N/A')}")
        lines.append(f"\n📝 How:")
        how = proposal.get("how", "N/A")
        if isinstance(how, list):
            for i, step in enumerate(how, 1):
                lines.append(f"  {i}. {step}")
        else:
            lines.append(f"  {how}")
        examples = proposal.get("examples")
        if examples:
            lines.append(f"\n💡 Examples: {examples}")
        lines.append(f"\n⚠️ Pitfalls: {proposal.get('common_pitfalls', 'N/A')}")
    else:
        # confirm_fix — show diff
        current = proposal.get("current_text", "N/A")
        proposed = proposal.get("proposed_text")
        lines.append(f"\n📍 Current text:\n  \"{current}\"")
        if proposed:
            lines.append(f"\n✏️ Proposed change:\n  \"{proposed}\"")
        else:
            lines.append(f"\n❌ Cannot auto-fix: author input needed")
        lines.append(f"\n💭 Rationale: {proposal.get('rationale', 'N/A')}")
        lines.append(f"⚠️ Risk: {proposal.get('risk_notes', 'None identified')}")
        lines.append(f"\n🤔 Author decision: {proposal.get('author_decision_needed', 'Approve or reject')}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# v4: Atomized Pipeline (generate → commit → verify)
# ─────────────────────────────────────────────────────────────────────────────

async def generate_rewrite(
    section_id: str,
    custom_instructions: str = "",
    provider: str = None,
    model: str = None,
    fix_complexity: str = "paragraph_level",
) -> str:
    """Step 1: Generate a rewrite proposal WITHOUT saving to filesystem.

    Returns JSON with:
    - proposed_text: the full revised section text
    - changes_summary: what was changed and why
    - original_length / proposed_length: char counts
    - section_id: echoed back for reference

    The agent can inspect this, decide whether to commit, or try a different approach.
    """
    # Load section content
    index = _load_index()
    entry = _find_section(section_id, index)
    if not entry:
        return json.dumps({"error": f"Section '{section_id}' not found."})

    # Read current version (revision if exists, else original)
    rev_path = WORKSPACE / "revisions" / f"{section_id}_v2.md"
    if rev_path.exists():
        content = rev_path.read_text(encoding="utf-8")
    else:
        content = Path(entry["file"]).read_text(encoding="utf-8")

    # Load issues for this section
    issues = _get_issues_for_section(section_id)
    if not issues and not custom_instructions:
        return json.dumps({"error": f"No issues for {section_id} and no custom instructions."})

    # Load domain knowledge
    domain_knowledge = _load_domain_knowledge(entry.get("title", ""))

    # Build prompt and call LLM
    client = LLMClient(model=model, max_concurrent=3, provider=provider)
    system = REWRITE_SYSTEM_PROMPT.format(
        domain_knowledge=f"\nDomain guidance:\n{domain_knowledge}" if domain_knowledge else ""
    )

    user_parts = [f"## Section to revise: {entry['title']}\n\n{content}"]
    if issues:
        user_parts.append(f"\n\n## Issues to address:\n{json.dumps(issues, indent=2, ensure_ascii=False)}")
    if custom_instructions:
        user_parts.append(f"\n\n## Additional instructions from author:\n{custom_instructions}")

    max_tokens = calculate_max_tokens(content, fix_complexity)

    response = await client.chat(
        system=system,
        user="\n".join(user_parts),
        max_tokens=max_tokens,
        temperature=0.1,
        model=get_model_for_task("rewrite_section"),
    )

    # Parse response
    revised_text, changes_summary = _parse_rewrite_response(response)
    if not revised_text:
        return json.dumps({"error": "Could not parse rewrite output.", "raw_response": response[:500]})

    # Return proposal (NOT saved yet)
    return json.dumps({
        "section_id": section_id,
        "proposed_text": revised_text,
        "changes_summary": changes_summary,
        "original_length": len(content),
        "proposed_length": len(revised_text),
        "issues_addressed": len(issues),
        "tokens_used": client.stats().get("total_tokens", 0),
    }, ensure_ascii=False)


def commit_rewrite(section_id: str, proposed_text: str, changes_summary: str = "") -> str:
    """Step 2: Commit a proposed rewrite to the filesystem.

    Saves the text to .workspace/revisions/{section_id}_v2.md and logs the change.
    Returns confirmation with file path and stats.

    The agent calls this ONLY after reviewing generate_rewrite output.
    """
    # Validate section exists
    index = _load_index()
    entry = _find_section(section_id, index)
    if not entry:
        return json.dumps({"error": f"Section '{section_id}' not found."})

    if not proposed_text or len(proposed_text.strip()) < 50:
        return json.dumps({"error": "Proposed text is too short or empty."})

    # Save revised version
    rev_dir = WORKSPACE / "revisions"
    rev_dir.mkdir(parents=True, exist_ok=True)
    rev_path = rev_dir / f"{section_id}_v2.md"

    # Read original for comparison
    if rev_path.exists():
        previous_text = rev_path.read_text(encoding="utf-8")
    else:
        previous_text = Path(entry["file"]).read_text(encoding="utf-8")

    rev_path.write_text(proposed_text, encoding="utf-8")

    # Log
    _log_revision(section_id, changes_summary or "committed via atomic pipeline")

    # Score tracking
    try:
        import time as _time
        from utils.score_tracker import ScoreSnapshot, record_score
        snapshot = ScoreSnapshot(
            timestamp=_time.strftime("%Y-%m-%dT%H:%M:%S"),
            overall_score=0.0,
            dimension_scores={},
            issues_remaining=0,
            must_fix_remaining=0,
            trigger=f"commit_rewrite_{section_id}",
            sections_modified=[section_id],
        )
        record_score(snapshot)
    except Exception:
        pass

    # Memory persistence
    try:
        from utils.memory.integration import remember_rewrite, get_paper_id
        _meta = json.loads((WORKSPACE / "paper" / "metadata.json").read_text(encoding="utf-8"))
        _paper_id = get_paper_id(_meta.get("source_file", ""))
        remember_rewrite(
            paper_id=_paper_id,
            section_id=section_id,
            changes_summary=changes_summary,
            verify_passed=None,  # Not yet verified
        )
    except Exception:
        pass

    return json.dumps({
        "status": "committed",
        "section_id": section_id,
        "file": str(rev_path),
        "previous_length": len(previous_text),
        "new_length": len(proposed_text),
        "changes_summary": changes_summary[:300],
    }, ensure_ascii=False)


async def verify_rewrite_quality(
    section_id: str,
    provider: str = None,
    model: str = None,
) -> str:
    """Step 3: Verify the quality of a committed rewrite.

    Runs:
    1. De-AI audit (detects AI signals, optionally fixes in-place)
    2. Post-Edit Verification (consistency + voice drift + AI regression)

    Returns structured JSON with:
    - deai_verdict: pass/partial/fail + signals found
    - post_edit_verdict: pass/fail + specific failures
    - overall_passed: bool
    - fix_hints: actionable suggestions if failed

    The agent uses this to decide: accept, retry with different strategy, or escalate to user.
    """
    # Load original and current revision
    index = _load_index()
    entry = _find_section(section_id, index)
    if not entry:
        return json.dumps({"error": f"Section '{section_id}' not found."})

    rev_path = WORKSPACE / "revisions" / f"{section_id}_v2.md"
    if not rev_path.exists():
        return json.dumps({"error": f"No revision exists for {section_id}. Run commit_rewrite first."})

    revised_text = rev_path.read_text(encoding="utf-8")
    original_text = Path(entry["file"]).read_text(encoding="utf-8")

    result = {
        "section_id": section_id,
        "deai_verdict": None,
        "post_edit_verdict": None,
        "overall_passed": True,
        "fix_hints": [],
    }

    # 1. De-AI Audit
    if _text_substantially_changed(original_text, revised_text):
        deai_summary = await _run_deai_audit(
            section_id, revised_text, original_text, rev_path, provider, model
        )
        result["deai_verdict"] = deai_summary
        if "FAIL" in deai_summary.upper() or "high AI" in deai_summary.lower():
            result["overall_passed"] = False
            result["fix_hints"].append(
                "AI signals detected. Try deai_rewrite with stricter author_constraints."
            )
    else:
        result["deai_verdict"] = "skipped (no substantial change)"

    # 2. Post-Edit Verification
    # Re-read in case deai audit modified the file
    final_text = rev_path.read_text(encoding="utf-8") if rev_path.exists() else revised_text
    try:
        from tools.post_edit_verify import verify_edit, format_verification_result
        verify_result = verify_edit(
            section_id=section_id,
            old_text=original_text,
            new_text=final_text,
        )
        if not verify_result.passed:
            result["post_edit_verdict"] = format_verification_result(verify_result, section_id)
            result["overall_passed"] = False
            # Generate fix hints from specific failures
            if hasattr(verify_result, 'failures'):
                for failure in verify_result.failures:
                    result["fix_hints"].append(f"PEV failure: {failure}")
            else:
                result["fix_hints"].append(
                    "Post-edit verification failed. Check consistency and voice drift."
                )
        else:
            result["post_edit_verdict"] = "passed"
    except Exception as e:
        result["post_edit_verdict"] = f"error: {type(e).__name__}: {e}"

    return json.dumps(result, ensure_ascii=False)
