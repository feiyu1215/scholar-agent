"""
Classifies user intent to help the Agent choose the right tool combination.
"""


def classify_intent(user_message: str, context: str = "") -> dict:
    """
    Analyzes a user message to classify intent and suggest tool routing.
    
    Returns: {
        "primary_intent": str,
        "confidence": float,
        "alternative_intents": [...],
        "suggested_tools": [...],
        "needs_clarification": bool,
        "clarification_question": str or None
    }
    """
    message_lower = user_message.lower()
    
    # Intent patterns
    intents = {
        "deai_fix": {
            "keywords": ["去ai", "de-ai", "deai", "ai痕迹", "ai味", "降低ai", "humanize", "ai signal"],
            "tools": ["detect_ai_signals", "diagnose_signals", "rewrite_text", "verify_rewrite"]
        },
        "review": {
            "keywords": ["review", "审稿", "评审", "评价", "打分"],
            "tools": ["review_paper"]
        },
        "citation_check": {
            "keywords": ["citation", "引用", "参考文献", "reference", "bibliography"],
            "tools": ["verify_and_enrich_citations"]
        },
        "voice_check": {
            "keywords": ["声纹", "风格", "voice", "tone", "style drift", "一致性"],
            "tools": ["check_voice_drift"]
        },
        "full_polish": {
            "keywords": ["polish", "润色", "修改", "改好", "make better", "improve"],
            "tools": ["detect_ai_signals", "review_paper", "rewrite_text", "verify_rewrite"]
        },
        "structure": {
            "keywords": ["结构", "structure", "reorganize", "outline", "大纲"],
            "tools": ["review_paper"]  # with focus_dimensions=["structure"]
        }
    }
    
    scores = {}
    for intent_name, intent_data in intents.items():
        score = sum(1 for kw in intent_data["keywords"] if kw in message_lower)
        if score > 0:
            scores[intent_name] = score
    
    if not scores:
        return {
            "primary_intent": "unknown",
            "confidence": 0.0,
            "alternative_intents": list(intents.keys()),
            "suggested_tools": [],
            "needs_clarification": True,
            "clarification_question": "I'm not sure what you'd like me to do. Could you clarify: do you want me to check for AI signals, review the paper, check citations, or something else?"
        }
    
    sorted_intents = sorted(scores.items(), key=lambda x: -x[1])
    primary = sorted_intents[0]
    
    # If top two are close, needs clarification
    needs_clarification = len(sorted_intents) > 1 and sorted_intents[1][1] >= primary[1] - 1
    
    alternatives = [s[0] for s in sorted_intents[1:3]]
    
    clarification = None
    if needs_clarification:
        clarification = f"Your request could mean '{primary[0]}' or '{alternatives[0]}'. Which would you prefer?"
    
    return {
        "primary_intent": primary[0],
        "confidence": min(primary[1] / 3.0, 1.0),
        "alternative_intents": alternatives,
        "suggested_tools": intents[primary[0]]["tools"],
        "needs_clarification": needs_clarification,
        "clarification_question": clarification
    }
