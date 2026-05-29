"""
Estimates the impact of a proposed change before execution.
Helps the Agent decide whether to proceed, warn the user, or take a safer approach.
"""


def estimate_edit_impact(original_text: str, proposed_action: str, scope: str = "paragraph") -> dict:
    """
    Estimates what a proposed edit action would affect.
    
    Args:
        original_text: The text that would be modified
        proposed_action: Description of what will be done (e.g., "remove hedging language")
        scope: "sentence", "paragraph", "section", or "full_paper"
    
    Returns: {
        "estimated_change_ratio": float,  # 0-1, how much text would change
        "risk_level": str,  # "low", "medium", "high"
        "affected_elements": [...],  # what would be touched
        "reversibility": str,  # "easy", "moderate", "difficult"
        "recommendations": [...],  # suggestions for the Agent
        "proceed": bool  # whether it's safe to proceed without user confirmation
    }
    """
    # Implementation: analyze the text and proposed action
    word_count = len(original_text.split())
    
    # Heuristic risk assessment
    high_risk_actions = ["rewrite", "restructure", "remove all", "completely change"]
    medium_risk_actions = ["reduce", "simplify", "rephrase", "adjust tone"]
    
    risk = "low"
    change_ratio = 0.1
    for action in high_risk_actions:
        if action in proposed_action.lower():
            risk = "high"
            change_ratio = 0.6
            break
    if risk == "low":
        for action in medium_risk_actions:
            if action in proposed_action.lower():
                risk = "medium"
                change_ratio = 0.3
                break
    
    # Scope multiplier
    scope_multipliers = {"sentence": 0.3, "paragraph": 0.6, "section": 0.8, "full_paper": 1.0}
    multiplier = scope_multipliers.get(scope, 0.6)
    change_ratio *= multiplier
    
    proceed = risk != "high" and scope != "full_paper"
    
    return {
        "estimated_change_ratio": round(change_ratio, 2),
        "risk_level": risk,
        "affected_elements": _identify_affected_elements(original_text, proposed_action),
        "reversibility": "easy" if change_ratio < 0.3 else ("moderate" if change_ratio < 0.6 else "difficult"),
        "recommendations": _generate_recommendations(risk, scope, change_ratio),
        "proceed": proceed
    }


def _identify_affected_elements(text: str, action: str) -> list:
    elements = []
    if "citation" in action.lower() or "reference" in action.lower():
        elements.append("citations")
    if "hedg" in action.lower():
        elements.append("hedging expressions")
    if "structure" in action.lower() or "reorganize" in action.lower():
        elements.append("paragraph structure")
    if "tone" in action.lower() or "voice" in action.lower():
        elements.append("writing voice/tone")
    if not elements:
        elements.append("general text content")
    return elements


def _generate_recommendations(risk: str, scope: str, change_ratio: float) -> list:
    recs = []
    if risk == "high":
        recs.append("Consider processing in smaller chunks to allow incremental review")
        recs.append("Run verify_rewrite after each chunk to catch regressions early")
    if scope == "full_paper":
        recs.append("Process section-by-section rather than the entire paper at once")
    if change_ratio > 0.5:
        recs.append("High change ratio — confirm with user before proceeding")
    return recs
