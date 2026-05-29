"""
utils/author_profile.py — Author Profile + Rejection Buffer.

Learns the author's preferences over time:
- Which fix categories they approve/reject
- Writing style preferences (hedging, active/passive, sentence length)
- Red lines they've explicitly set

The "rejection buffer" pattern:
When a user rejects a confirm_fix proposal, we record WHY and adjust future behavior:
- Same category → downgrade to guidance (don't propose same type of fix again)
- Pattern detection: if 3+ rejections share a common trait, generalize the rule

Persistence: .workspace/author_profile.json (survives session, travels with paper)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict

WORKSPACE = Path(".workspace")
PROFILE_PATH = WORKSPACE / "author_profile.json"


@dataclass
class RejectionEntry:
    """A recorded rejection of a proposed fix."""
    issue_id: str
    category: str
    reason: str  # User's stated reason (or inferred)
    timestamp: float
    proposal_summary: str = ""  # Brief description of what was rejected


@dataclass 
class AuthorProfile:
    """Accumulated author preference model."""
    # Categories the author consistently approves
    approved_categories: Dict[str, int] = field(default_factory=dict)  # category → count
    
    # Categories the author tends to reject
    rejected_categories: Dict[str, int] = field(default_factory=dict)  # category → count
    
    # Explicit preferences stated by author
    explicit_preferences: List[str] = field(default_factory=list)
    
    # Rejection history (for pattern detection)
    rejections: List[Dict] = field(default_factory=list)
    
    # Learned rules (generalized from rejections)
    learned_rules: List[str] = field(default_factory=list)
    
    # Style observations (extracted from original paper)
    style_observations: Dict[str, str] = field(default_factory=dict)
    
    # Timestamps
    created_at: float = 0.0
    updated_at: float = 0.0


def load_profile() -> AuthorProfile:
    """Load or create author profile."""
    if PROFILE_PATH.exists():
        try:
            data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            return AuthorProfile(**data)
        except (json.JSONDecodeError, TypeError):
            pass
    
    profile = AuthorProfile(created_at=time.time(), updated_at=time.time())
    save_profile(profile)
    return profile


def save_profile(profile: AuthorProfile):
    """Persist author profile to disk."""
    profile.updated_at = time.time()
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(
        json.dumps(asdict(profile), indent=2, ensure_ascii=False), encoding="utf-8"
    )


def record_approval(profile: AuthorProfile, issue_id: str, category: str):
    """Record that the author approved a fix in this category."""
    profile.approved_categories[category] = profile.approved_categories.get(category, 0) + 1
    save_profile(profile)


def record_rejection(
    profile: AuthorProfile,
    issue_id: str,
    category: str,
    reason: str = "",
    proposal_summary: str = "",
):
    """
    Record that the author rejected a fix. Updates rejection buffer
    and potentially generalizes new rules.
    """
    profile.rejected_categories[category] = profile.rejected_categories.get(category, 0) + 1
    
    entry = RejectionEntry(
        issue_id=issue_id,
        category=category,
        reason=reason,
        timestamp=time.time(),
        proposal_summary=proposal_summary,
    )
    profile.rejections.append(asdict(entry))
    
    # Pattern detection: if 3+ rejections in same category, generalize
    cat_rejections = [r for r in profile.rejections if r["category"] == category]
    if len(cat_rejections) >= 3:
        rule = f"Downgrade '{category}' to guidance — author consistently rejects these fixes"
        if rule not in profile.learned_rules:
            profile.learned_rules.append(rule)
    
    save_profile(profile)


def add_explicit_preference(profile: AuthorProfile, preference: str):
    """Record an explicit preference stated by the author."""
    if preference not in profile.explicit_preferences:
        profile.explicit_preferences.append(preference)
        save_profile(profile)


def observe_style(profile: AuthorProfile, aspect: str, observation: str):
    """
    Record a style observation from the original paper.
    
    Examples:
        observe_style(profile, "hedging", "Uses 'may' and 'suggests that' frequently")
        observe_style(profile, "voice", "Mostly active voice in results, passive in methods")
        observe_style(profile, "sentence_length", "Avg 22 words, range 8-45")
    """
    profile.style_observations[aspect] = observation
    save_profile(profile)


def get_action_recommendation(profile: AuthorProfile, category: str) -> str:
    """
    Based on profile, recommend action type for a new issue in this category.
    
    Returns: "auto_fix" | "confirm_fix" | "guidance"
    """
    approved = profile.approved_categories.get(category, 0)
    rejected = profile.rejected_categories.get(category, 0)
    
    # If consistently rejected (3+ with no approvals), downgrade to guidance
    if rejected >= 3 and approved == 0:
        return "guidance"
    
    # If mostly rejected (ratio > 2:1), downgrade to guidance
    if rejected > 0 and approved > 0 and rejected / approved > 2:
        return "guidance"
    
    # If consistently approved (3+), could upgrade to auto_fix
    if approved >= 3 and rejected == 0:
        return "auto_fix"
    
    # Default: confirm_fix (ask user)
    return "confirm_fix"


def get_profile_context_for_prompt(profile: AuthorProfile) -> str:
    """
    Format profile as context string to inject into rewrite/review prompts.
    Keeps it concise to minimize token cost.
    """
    parts = []
    
    if profile.explicit_preferences:
        parts.append("Author preferences: " + "; ".join(profile.explicit_preferences[:5]))
    
    if profile.learned_rules:
        parts.append("Learned rules: " + "; ".join(profile.learned_rules[:3]))
    
    if profile.style_observations:
        style_items = [f"{k}: {v}" for k, v in list(profile.style_observations.items())[:4]]
        parts.append("Style: " + " | ".join(style_items))
    
    if not parts:
        return ""
    
    return "\n".join(parts)


def format_profile_summary(profile: AuthorProfile) -> str:
    """Format for /profile command display."""
    lines = [
        "=" * 50,
        "AUTHOR PROFILE",
        "=" * 50,
    ]
    
    if profile.explicit_preferences:
        lines.append(f"\nPreferences ({len(profile.explicit_preferences)}):")
        for pref in profile.explicit_preferences:
            lines.append(f"  • {pref}")
    
    if profile.style_observations:
        lines.append(f"\nStyle observations:")
        for k, v in profile.style_observations.items():
            lines.append(f"  {k}: {v}")
    
    if profile.learned_rules:
        lines.append(f"\nLearned rules ({len(profile.learned_rules)}):")
        for rule in profile.learned_rules:
            lines.append(f"  ⚡ {rule}")
    
    if profile.approved_categories or profile.rejected_categories:
        lines.append(f"\nCategory history:")
        all_cats = set(list(profile.approved_categories.keys()) + 
                      list(profile.rejected_categories.keys()))
        for cat in sorted(all_cats):
            a = profile.approved_categories.get(cat, 0)
            r = profile.rejected_categories.get(cat, 0)
            rec = get_action_recommendation(profile, cat)
            lines.append(f"  {cat}: ✅{a} ❌{r} → {rec}")
    
    lines.append(f"\nTotal rejections: {len(profile.rejections)}")
    
    return "\n".join(lines)
