"""
tools/parallel_rewrite.py — Section-Level Parallel Rewrite.

Enables concurrent rewriting of multiple independent sections.
Key constraint: sections must be NON-OVERLAPPING (no cross-references
between them that could cause conflict).

Safety rules:
1. Never parallelize Introduction + Conclusion (they reference each other)
2. Never parallelize sections that share the same variables/equations
3. Max parallelism = 3 (to avoid rate limits and context confusion)
4. Each rewrite gets its own LLMClient instance (isolated context)

Usage by agent:
- Agent calls parallel_rewrite with a list of section_ids
- This tool validates independence, then runs concurrent rewrites
- Returns consolidated results with any conflict warnings
"""

from __future__ import annotations

import json
import asyncio
from pathlib import Path
from typing import List, Dict, Tuple

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.write_engine import rewrite_section

WORKSPACE = Path(".workspace")
MAX_PARALLEL = 3

# Sections that must NOT be rewritten in parallel with each other
CONFLICT_PAIRS = {
    frozenset({"introduction", "conclusion"}),
    frozenset({"abstract", "introduction"}),
    frozenset({"abstract", "conclusion"}),
    frozenset({"methods", "results"}),  # Often share notation
}


def check_independence(section_ids: List[str]) -> Tuple[List[str], List[str]]:
    """
    Validate that requested sections can be rewritten in parallel.
    
    Returns:
        (safe_ids, conflict_warnings)
    """
    warnings = []
    blocked = set()
    
    # Normalize section IDs for matching
    normalized = {sid: sid.lower().split("_")[-1] if "_" in sid else sid.lower()
                  for sid in section_ids}
    
    for i, sid1 in enumerate(section_ids):
        for sid2 in section_ids[i+1:]:
            pair = frozenset({normalized[sid1], normalized[sid2]})
            for conflict_pair in CONFLICT_PAIRS:
                # Check if any conflict keyword matches
                if any(kw in normalized[sid1] for kw in conflict_pair) and \
                   any(kw in normalized[sid2] for kw in conflict_pair):
                    warnings.append(
                        f"Conflict: '{sid1}' and '{sid2}' may reference each other. "
                        f"'{sid2}' moved to sequential queue."
                    )
                    blocked.add(sid2)
                    break
    
    safe = [sid for sid in section_ids if sid not in blocked]
    
    # Enforce max parallelism
    if len(safe) > MAX_PARALLEL:
        overflow = safe[MAX_PARALLEL:]
        safe = safe[:MAX_PARALLEL]
        warnings.append(
            f"Parallelism capped at {MAX_PARALLEL}. "
            f"Sections {overflow} queued sequentially."
        )
    
    return safe, warnings


async def parallel_rewrite(
    section_ids: List[str],
    provider: str = None,
    model: str = None,
    custom_instructions: str = "",
) -> str:
    """
    Rewrite multiple sections concurrently.
    
    Returns: consolidated status report.
    """
    # Step 1: Check independence
    safe_ids, warnings = check_independence(section_ids)
    blocked_ids = [sid for sid in section_ids if sid not in safe_ids]
    
    results = []
    
    # Step 2: Run safe sections in parallel
    if safe_ids:
        tasks = [
            rewrite_section(sid, provider=provider, model=model,
                          custom_instructions=custom_instructions)
            for sid in safe_ids
        ]
        parallel_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for sid, result in zip(safe_ids, parallel_results):
            if isinstance(result, Exception):
                results.append(f"❌ {sid}: Error — {type(result).__name__}: {result}")
            else:
                results.append(f"✅ {sid}: {result}")
    
    # Step 3: Run blocked sections sequentially
    for sid in blocked_ids:
        try:
            result = await rewrite_section(sid, provider=provider, model=model,
                                          custom_instructions=custom_instructions)
            results.append(f"✅ {sid} (sequential): {result}")
        except Exception as e:
            results.append(f"❌ {sid}: Error — {type(e).__name__}: {e}")
    
    # Step 4: Format report
    report_lines = []
    report_lines.append("=" * 50)
    report_lines.append("PARALLEL REWRITE COMPLETE")
    report_lines.append("=" * 50)
    
    if warnings:
        report_lines.append("\n⚠️ Warnings:")
        for w in warnings:
            report_lines.append(f"  • {w}")
    
    report_lines.append(f"\nResults ({len(results)} sections):")
    for r in results:
        report_lines.append(f"  {r}")
    
    return "\n".join(report_lines)


def suggest_parallel_groups(section_ids: List[str]) -> List[List[str]]:
    """
    Given a list of sections to rewrite, suggest optimal parallel groups.
    Respects conflict rules and max parallelism.
    
    Returns: List of groups, each group can be run in parallel.
    """
    remaining = list(section_ids)
    groups = []
    
    while remaining:
        group = []
        still_remaining = []
        
        for sid in remaining:
            candidate_group = group + [sid]
            safe, _ = check_independence(candidate_group)
            if len(safe) == len(candidate_group):
                group.append(sid)
                if len(group) >= MAX_PARALLEL:
                    still_remaining.extend(remaining[remaining.index(sid)+1:])
                    break
            else:
                still_remaining.append(sid)
        
        if group:
            groups.append(group)
        remaining = still_remaining
    
    return groups
