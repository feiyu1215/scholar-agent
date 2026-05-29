"""
Dry Run estimation tool.

Before executing expensive operations, the Agent can call this to show the user:
- Estimated number of LLM calls
- Estimated token usage
- Estimated time
- Estimated cost (if pricing is configured)

This helps users decide whether to proceed, adjust scope, or use a cheaper approach.
"""

from typing import Dict, Any, Optional, List


# Cost per 1M tokens (approximate, for estimation only)
MODEL_COSTS = {
    "high": {"input": 10.0, "output": 30.0},    # e.g., GPT-4o
    "medium": {"input": 2.5, "output": 10.0},   # e.g., GPT-4o-mini  
    "low": {"input": 0.5, "output": 1.5},       # e.g., deepseek
}

# Average tokens per operation type
OPERATION_PROFILES = {
    "review_paper": {
        "description": "Full multi-role paper review (5 reviewers + consolidation)",
        "llm_calls": 6,  # 5 reviewers + 1 consolidation
        "avg_input_tokens_per_call": 4000,
        "avg_output_tokens_per_call": 2000,
        "model_tier": "high",
        "parallelizable": True,
        "serial_time_seconds": 60,
        "parallel_time_seconds": 15,
    },
    "review_paper_custom": {
        "description": "Custom reviewer count review",
        "base_calls": 1,  # consolidation
        "per_reviewer_calls": 1,
        "avg_input_tokens_per_call": 4000,
        "avg_output_tokens_per_call": 2000,
        "model_tier": "high",
        "parallelizable": True,
    },
    "deai_detect": {
        "description": "Detect AI signals in text",
        "llm_calls": 1,
        "avg_input_tokens_per_call": 3000,
        "avg_output_tokens_per_call": 1500,
        "model_tier": "medium",
        "parallelizable": False,
        "serial_time_seconds": 8,
    },
    "deai_full_pipeline": {
        "description": "Full de-AI pipeline (detect + diagnose + rewrite + verify)",
        "llm_calls": 4,
        "avg_input_tokens_per_call": 3000,
        "avg_output_tokens_per_call": 2000,
        "model_tier": "medium",
        "parallelizable": False,
        "serial_time_seconds": 35,
    },
    "deai_closed_loop": {
        "description": "Closed-loop de-AI with retries (worst case: 2 retries)",
        "llm_calls": 12,  # 4 per iteration * 3 iterations max
        "avg_input_tokens_per_call": 3000,
        "avg_output_tokens_per_call": 2000,
        "model_tier": "medium",
        "parallelizable": False,
        "serial_time_seconds": 90,
    },
    "rewrite_section": {
        "description": "Rewrite a single section",
        "llm_calls": 1,
        "avg_input_tokens_per_call": 5000,
        "avg_output_tokens_per_call": 4000,
        "model_tier": "high",
        "parallelizable": False,
        "serial_time_seconds": 12,
    },
    "parallel_rewrite": {
        "description": "Parallel rewrite of multiple sections",
        "base_calls": 0,
        "per_section_calls": 1,
        "avg_input_tokens_per_call": 5000,
        "avg_output_tokens_per_call": 4000,
        "model_tier": "high",
        "parallelizable": True,
        "serial_time_per_section": 12,
        "parallel_time_per_section": 5,
    },
    "search_literature": {
        "description": "Academic literature search",
        "llm_calls": 0,
        "avg_input_tokens_per_call": 0,
        "avg_output_tokens_per_call": 0,
        "model_tier": "low",
        "parallelizable": False,
        "serial_time_seconds": 5,
        "note": "API calls only, no LLM cost",
    },
    "verify_and_enrich_citations": {
        "description": "Citation verification and enrichment",
        "llm_calls": 0,
        "avg_input_tokens_per_call": 0,
        "avg_output_tokens_per_call": 0,
        "model_tier": "low",
        "parallelizable": False,
        "serial_time_seconds": 3,
        "note": "Rule-based, no LLM cost",
    },
    "presubmission_check": {
        "description": "Pre-submission format check",
        "llm_calls": 0,
        "avg_input_tokens_per_call": 0,
        "avg_output_tokens_per_call": 0,
        "model_tier": "low",
        "parallelizable": False,
        "serial_time_seconds": 1,
        "note": "Rule-based, zero cost",
    },
}


def estimate_operation(
    operation: str,
    text_length_words: int = 0,
    section_count: int = 1,
    reviewer_count: int = 5,
) -> Dict[str, Any]:
    """
    Estimate the cost/time of a single operation.
    
    Args:
        operation: Name of the operation (must be in OPERATION_PROFILES)
        text_length_words: Approximate word count of input text
        section_count: Number of sections (for parallel operations)
        reviewer_count: Number of reviewers (for review operations)
    
    Returns: {
        "operation": str,
        "description": str,
        "estimated_llm_calls": int,
        "estimated_input_tokens": int,
        "estimated_output_tokens": int,
        "estimated_total_tokens": int,
        "estimated_cost_usd": float,
        "estimated_time_seconds": float,
        "model_tier": str,
        "notes": list[str]
    }
    """
    profile = OPERATION_PROFILES.get(operation)
    if not profile:
        return {
            "operation": operation,
            "description": "Unknown operation",
            "estimated_llm_calls": 0,
            "estimated_input_tokens": 0,
            "estimated_output_tokens": 0,
            "estimated_total_tokens": 0,
            "estimated_cost_usd": 0.0,
            "estimated_time_seconds": 0.0,
            "model_tier": "unknown",
            "notes": [f"No profile found for '{operation}'. Available: {list(OPERATION_PROFILES.keys())}"],
        }
    
    # Calculate LLM calls
    llm_calls = profile.get("llm_calls", 0)
    if "per_reviewer_calls" in profile:
        llm_calls = profile["base_calls"] + (profile["per_reviewer_calls"] * reviewer_count)
    if "per_section_calls" in profile:
        llm_calls = profile.get("base_calls", 0) + (profile["per_section_calls"] * section_count)
    
    # Scale input tokens by text length if provided
    scale_factor = 1.0
    if text_length_words > 0:
        # Assume avg profile is for ~1000 word input; scale linearly
        scale_factor = max(1.0, text_length_words / 1000.0)
    
    input_tokens = int(profile.get("avg_input_tokens_per_call", 0) * llm_calls * scale_factor)
    output_tokens = int(profile.get("avg_output_tokens_per_call", 0) * llm_calls)
    total_tokens = input_tokens + output_tokens
    
    # Cost estimate
    tier = profile.get("model_tier", "medium")
    costs = MODEL_COSTS.get(tier, MODEL_COSTS["medium"])
    cost = (input_tokens / 1_000_000 * costs["input"]) + (output_tokens / 1_000_000 * costs["output"])
    
    # Time estimate
    if profile.get("parallelizable") and section_count > 1:
        time_est = profile.get("parallel_time_per_section", profile.get("parallel_time_seconds", 10)) * min(section_count, 3)
    elif "serial_time_per_section" in profile:
        time_est = profile["serial_time_per_section"] * section_count
    else:
        time_est = profile.get("serial_time_seconds", llm_calls * 8)
    
    notes = []
    if profile.get("note"):
        notes.append(profile["note"])
    if scale_factor > 1.5:
        notes.append(f"Scaled {scale_factor:.1f}x for {text_length_words}-word input")
    
    return {
        "operation": operation,
        "description": profile["description"],
        "estimated_llm_calls": llm_calls,
        "estimated_input_tokens": input_tokens,
        "estimated_output_tokens": output_tokens,
        "estimated_total_tokens": total_tokens,
        "estimated_cost_usd": round(cost, 4),
        "estimated_time_seconds": round(time_est, 1),
        "model_tier": tier,
        "notes": notes,
    }


def estimate_plan(
    operations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Estimate the total cost of a multi-step plan.
    
    Args:
        operations: List of dicts, each with "operation" key and optional 
                   "text_length_words", "section_count", "reviewer_count"
    
    Returns: {
        "steps": [...],  # Individual estimates
        "total_llm_calls": int,
        "total_tokens": int,
        "total_cost_usd": float,
        "total_time_seconds": float,
        "summary": str  # Human-readable summary
    }
    """
    steps = []
    for op in operations:
        est = estimate_operation(
            operation=op["operation"],
            text_length_words=op.get("text_length_words", 0),
            section_count=op.get("section_count", 1),
            reviewer_count=op.get("reviewer_count", 5),
        )
        steps.append(est)
    
    total_calls = sum(s["estimated_llm_calls"] for s in steps)
    total_tokens = sum(s["estimated_total_tokens"] for s in steps)
    total_cost = sum(s["estimated_cost_usd"] for s in steps)
    total_time = sum(s["estimated_time_seconds"] for s in steps)
    
    # Format summary
    summary_lines = [
        f"Plan: {len(steps)} operations",
        f"LLM calls: ~{total_calls}",
        f"Tokens: ~{total_tokens:,}",
        f"Est. cost: ${total_cost:.3f}",
        f"Est. time: {total_time:.0f}s ({total_time/60:.1f} min)",
    ]
    
    return {
        "steps": steps,
        "total_llm_calls": total_calls,
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 4),
        "total_time_seconds": round(total_time, 1),
        "summary": " | ".join(summary_lines),
    }


def format_dry_run_report(estimate: Dict[str, Any]) -> str:
    """Format an estimate as a human-readable report for display."""
    if "steps" in estimate:
        # Multi-step plan
        lines = ["📊 **Dry Run Estimate**", ""]
        for i, step in enumerate(estimate["steps"], 1):
            lines.append(f"  {i}. {step['operation']}: {step['description']}")
            if step["estimated_llm_calls"] > 0:
                lines.append(f"     → {step['estimated_llm_calls']} LLM calls, ~{step['estimated_total_tokens']:,} tokens, ~${step['estimated_cost_usd']:.3f}")
            else:
                lines.append(f"     → Zero cost (rule-based)")
        lines.append("")
        lines.append(f"  **Total**: ~{estimate['total_llm_calls']} calls | ~{estimate['total_tokens']:,} tokens | ~${estimate['total_cost_usd']:.3f} | ~{estimate['total_time_seconds']:.0f}s")
        return "\n".join(lines)
    else:
        # Single operation
        lines = [
            f"📊 **Dry Run**: {estimate['operation']}",
            f"   {estimate['description']}",
            f"   LLM calls: {estimate['estimated_llm_calls']} | Tokens: ~{estimate['estimated_total_tokens']:,} | Cost: ~${estimate['estimated_cost_usd']:.3f} | Time: ~{estimate['estimated_time_seconds']:.0f}s",
        ]
        if estimate["notes"]:
            lines.append(f"   Notes: {'; '.join(estimate['notes'])}")
        return "\n".join(lines)
