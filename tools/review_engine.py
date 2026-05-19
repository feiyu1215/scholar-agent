"""
tools/review_engine.py — Multi-role review via subagent pattern.

Each reviewer role (editor, theory, methodology, logic, literature) runs in 
its own isolated context with only the relevant section(s) loaded.
This is the s04 subagent pattern applied to academic review.

Design choices:
- Each reviewer sees ONLY the section(s) relevant to their focus + the abstract
- Reviewers output structured issues (JSON), not free-form text
- Issues are anchored to specific locations (section_id + quote)
- Final consolidation happens after all reviewers finish
"""

from __future__ import annotations

import json
import asyncio
from pathlib import Path
from typing import Optional, List, Dict

from llm.client import LLMClient

WORKSPACE = Path(".workspace")

# Reviewer role definitions
REVIEWER_ROLES = {
    "editor": {
        "focus": "Desk-reject screening: novelty, scope, presentation quality, fatal flaws",
        "reads": ["abstract", "introduction"],  # Editor only needs intro to decide
    },
    "theory": {
        "focus": "Theoretical contribution: novelty of argument, dialogue with existing theory, logical rigor of claims",
        "reads": ["introduction", "model", "theory", "discussion"],
    },
    "methodology": {
        "focus": "Methods transparency: reproducibility, validity threats, sample adequacy, statistical rigor",
        "reads": ["methodology", "methods", "data", "results"],
    },
    "logic": {
        "focus": "Argument coherence: do claims follow from evidence? Internal contradictions? Overclaims?",
        "reads": ["introduction", "results", "discussion", "conclusion"],
    },
    "literature": {
        "focus": "Literature dialogue: is the gap genuine? Selective citation? Missing key references?",
        "reads": ["introduction", "related_work", "literature_review", "discussion"],
    },
}

REVIEW_SYSTEM_PROMPT = """You are an academic reviewer ({role}) evaluating a research paper.

Your focus: {focus}

You must output a JSON array of issues found. Each issue has:
- "severity": "major" | "moderate" | "minor"
- "category": brief category name
- "location": which section and approximate quote
- "description": clear explanation of the problem
- "suggestion": concrete suggestion for improvement (optional)

If you find no issues, return an empty array [].

Be rigorous but fair. Anchor every finding to specific text. Do not fabricate problems.
Do not comment on formatting/typos unless they impede understanding."""

CONSOLIDATION_PROMPT = """You are a senior editor consolidating reviews from 5 independent reviewers.

You have received separate review outputs from: Editor, Theory, Methodology, Logic, Literature reviewers.

Your job:
1. Merge duplicate/overlapping issues (keep the more detailed version)
2. Assign final severity: major (submission blocker), moderate (should fix), minor (nice to fix)
3. For EACH issue, classify an action_type using these rules:
   - "guidance": issue requires information NOT in the paper (new data, experiments, references the author must find)
   - "confirm_fix": issue touches core argument framing, author's subjective choices, or causal claims
   - "auto_fix": issue is clearly fixable from existing text (grammar, structure, citation format, logical connectors, hedging)
4. Order by severity then by section order
5. Produce a revision roadmap: what to fix first, what can wait
6. Give an overall score (1-10) using: start at 9.0, subtract 1.5 per major, 0.7 per moderate, 0.2 per minor, floor at 1.0

Each issue in the output MUST include these fields:
- "id": sequential ID like "ISS-001"
- "severity": "major" | "moderate" | "minor"
- "category": brief category name
- "location": {"section_id": "...", "quote": "..."}
- "description": clear explanation
- "suggestion": concrete suggestion
- "action_type": "auto_fix" | "confirm_fix" | "guidance"
- "action_rationale": one sentence explaining WHY this action_type was chosen
- "fix_complexity": "sentence_level" | "paragraph_level" | "section_level" | "cross_section"

Output format:
{
  "overall_score": <float>,
  "verdict": "accept" | "minor_revision" | "major_revision" | "reject",
  "total_issues": {"major": N, "moderate": N, "minor": N},
  "action_summary": {"auto_fix": N, "confirm_fix": N, "guidance": N},
  "issues": [<consolidated issue list with action_type>],
  "revision_roadmap": [<ordered list of what to fix>],
  "strengths": [<what the paper does well>]
}"""


async def review_paper(provider: str = None, model: str = None) -> str:
    """Run full multi-role review. Returns consolidated review summary.
    
    Each reviewer runs as an isolated "subagent" — fresh context, only relevant sections.
    Reviewers run in parallel (asyncio.gather) for speed.
    """
    index = _load_index()
    if not index:
        return "Error: No paper parsed. Use parse_paper first."

    client = LLMClient(model=model, max_concurrent=5, provider=provider)

    # Run all reviewers in parallel
    tasks = []
    for role_name, role_config in REVIEWER_ROLES.items():
        tasks.append(_run_reviewer(client, role_name, role_config, index))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect reviewer outputs
    all_issues = {}
    for role_name, result in zip(REVIEWER_ROLES.keys(), results):
        if isinstance(result, Exception):
            all_issues[role_name] = f"Error: {result}"
        else:
            all_issues[role_name] = result

    # Save individual reviewer outputs
    review_dir = WORKSPACE / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    for role_name, issues in all_issues.items():
        (review_dir / f"reviewer_{role_name}.json").write_text(
            json.dumps(issues, indent=2, ensure_ascii=False) if isinstance(issues, list)
            else json.dumps({"error": str(issues)}),
            encoding="utf-8"
        )

    # Consolidate
    consolidated = await _consolidate(client, all_issues)

    # Save consolidated output
    (review_dir / "consolidated.json").write_text(
        json.dumps(consolidated, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (review_dir / "issues.json").write_text(
        json.dumps(consolidated.get("issues", []), indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Return compact summary for agent context
    summary = _format_review_summary(consolidated, client.stats())
    return summary


async def _run_reviewer(client: LLMClient, role_name: str, role_config: dict, index: list) -> list:
    """Run a single reviewer in isolated context. Returns list of issues."""
    # Gather relevant sections for this reviewer
    sections_text = _gather_sections(role_config["reads"], index)
    if not sections_text:
        return []

    system = REVIEW_SYSTEM_PROMPT.format(role=role_name, focus=role_config["focus"])
    user = f"Review the following paper sections:\n\n{sections_text}"

    response = await client.chat(system=system, user=user, max_tokens=3000, temperature=0.1)

    # Parse JSON from response
    try:
        # Try to extract JSON array from response
        response = response.strip()
        if response.startswith("```"):
            response = response.split("```")[1]
            if response.startswith("json"):
                response = response[4:]
        issues = json.loads(response)
        if isinstance(issues, list):
            # Tag each issue with reviewer role
            for issue in issues:
                issue["reviewer"] = role_name
            return issues
    except json.JSONDecodeError:
        pass

    return [{"severity": "note", "category": "parse_error",
             "description": f"Could not parse {role_name} output", "raw": response[:500]}]


async def _consolidate(client: LLMClient, all_issues: dict) -> dict:
    """Consolidate all reviewer outputs into a single assessment."""
    reviewer_summary = json.dumps(all_issues, indent=2, ensure_ascii=False)

    response = await client.chat(
        system=CONSOLIDATION_PROMPT,
        user=f"Reviewer outputs:\n\n{reviewer_summary}",
        max_tokens=4000,
        temperature=0.0,
    )

    try:
        response = response.strip()
        if response.startswith("```"):
            response = response.split("```")[1]
            if response.startswith("json"):
                response = response[4:]
        return json.loads(response)
    except json.JSONDecodeError:
        return {
            "overall_score": 0,
            "verdict": "error",
            "parse_error": True,
            "raw_response": response[:2000],
            "issues": [],
        }


def _gather_sections(target_slugs: List[str], index: list) -> str:
    """Load sections matching target slugs. Fuzzy match on slug/title."""
    texts = []
    for entry in index:
        slug = entry["slug"].lower()
        title = entry["title"].lower()
        for target in target_slugs:
            target_lower = target.lower()
            if target_lower in slug or target_lower in title:
                sec_path = Path(entry["file"])
                if sec_path.exists():
                    content = sec_path.read_text(encoding="utf-8")
                    texts.append(f"=== {entry['title']} ===\n{content}")
                break
    return "\n\n".join(texts)


def _load_index() -> list:
    index_path = WORKSPACE / "paper" / "section_index.json"
    if not index_path.exists():
        return []
    return json.loads(index_path.read_text(encoding="utf-8"))


def _format_review_summary(consolidated: dict, stats: dict) -> str:
    """Format review results for display. Compact but informative."""
    lines = []
    lines.append("=" * 60)
    lines.append("REVIEW COMPLETE")
    lines.append("=" * 60)

    score = consolidated.get("overall_score", "N/A")
    verdict = consolidated.get("verdict", "N/A")
    totals = consolidated.get("total_issues", {})
    actions = consolidated.get("action_summary", {})

    lines.append(f"\nScore: {score}/10 | Verdict: {verdict}")
    lines.append(f"Issues: {totals.get('major', 0)} major, {totals.get('moderate', 0)} moderate, {totals.get('minor', 0)} minor")
    if actions:
        lines.append(f"Actions: {actions.get('auto_fix', 0)} auto_fix, "
                     f"{actions.get('confirm_fix', 0)} confirm_fix, "
                     f"{actions.get('guidance', 0)} guidance")

    strengths = consolidated.get("strengths", [])
    if strengths:
        lines.append(f"\nStrengths: {'; '.join(strengths[:3])}")

    issues = consolidated.get("issues", [])
    if issues:
        lines.append("\nTop Issues:")
        for i, issue in enumerate(issues[:5]):
            sev = issue.get("severity", "?")
            cat = issue.get("category", "")
            desc = issue.get("description", "")[:100]
            lines.append(f"  [{sev.upper()}] {cat}: {desc}")
        if len(issues) > 5:
            lines.append(f"  ... and {len(issues) - 5} more (see .workspace/review/consolidated.json)")

    roadmap = consolidated.get("revision_roadmap", [])
    if roadmap:
        lines.append("\nRevision Roadmap (priority order):")
        for i, item in enumerate(roadmap[:5]):
            if isinstance(item, str):
                lines.append(f"  {i+1}. {item}")
            elif isinstance(item, dict):
                lines.append(f"  {i+1}. {item.get('action', item)}")

    lines.append(f"\n[LLM Stats: {stats['total_calls']} calls, "
                 f"{stats['total_input_tokens']} in / {stats['total_output_tokens']} out tokens, "
                 f"~${stats['estimated_cost_usd']}]")

    return "\n".join(lines)
