"""
utils/skill_registry.py — Skill Frontmatter Registry & Selector.

Skills are .md files in skills/ directory. Each has a YAML frontmatter header
that describes what it provides, when to load it, and token cost.

This registry:
1. Parses frontmatter from all skill files (on first access, cached)
2. Provides a selector that matches skills to current context
3. Returns ONLY the frontmatter summaries (not full content) for agent decision
4. Agent then uses load_skill to pull full content when needed

Frontmatter format (in skill .md files):
---
id: econ_writing
name: Economics Writing Style
triggers:
  - section contains "regression"
  - section title matches "introduction|conclusion|results"
  - reviewer role is "editor"
provides: writing style rules, sentence structure patterns, hedging language
token_cost: ~1500
priority: 2
---

Design: Zero-intrusion on existing skills/*.md files — if no frontmatter, 
the skill still works via manual load_skill, just won't be auto-suggested.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, field

SKILLS_DIR = Path("skills")


@dataclass
class SkillMeta:
    """Parsed skill metadata from frontmatter."""
    id: str
    name: str
    file_path: str
    triggers: List[str] = field(default_factory=list)
    provides: str = ""
    token_cost: int = 2000  # Estimated tokens when loaded
    priority: int = 5  # Lower = higher priority (loaded first)

    def matches(self, context: Dict) -> tuple[bool, str]:
        """
        Check if this skill should be suggested for the given context.
        
        Context dict may contain:
            - section_title: current section being worked on
            - section_content_preview: first 200 chars of section
            - reviewer_role: which reviewer is active
            - issue_categories: list of issue category strings
            - phase: "review" | "rewrite" | "deai"
        
        Returns: (matches: bool, reason: str)
        """
        if not self.triggers:
            return False, ""

        section_title = context.get("section_title", "").lower()
        content_preview = context.get("section_content_preview", "").lower()
        reviewer_role = context.get("reviewer_role", "").lower()
        issue_categories = [c.lower() for c in context.get("issue_categories", [])]
        phase = context.get("phase", "").lower()

        for trigger in self.triggers:
            trigger_lower = trigger.lower()

            # Pattern: "section contains <keyword>"
            if trigger_lower.startswith("section contains "):
                keyword = trigger_lower.replace("section contains ", "").strip('"\'')
                if keyword in content_preview or keyword in section_title:
                    return True, f"section contains '{keyword}'"

            # Pattern: "section title matches <regex>"
            elif trigger_lower.startswith("section title matches "):
                pattern = trigger_lower.replace("section title matches ", "").strip('"\'')
                if re.search(pattern, section_title):
                    return True, f"title matches /{pattern}/"

            # Pattern: "reviewer role is <role>"
            elif trigger_lower.startswith("reviewer role is "):
                role = trigger_lower.replace("reviewer role is ", "").strip('"\'')
                if role == reviewer_role:
                    return True, f"reviewer is {role}"

            # Pattern: "issue category <cat>"
            elif trigger_lower.startswith("issue category "):
                cat = trigger_lower.replace("issue category ", "").strip('"\'')
                if cat in issue_categories:
                    return True, f"issue category '{cat}'"

            # Pattern: "phase is <phase>"
            elif trigger_lower.startswith("phase is "):
                p = trigger_lower.replace("phase is ", "").strip('"\'')
                if p == phase:
                    return True, f"phase is {p}"

        return False, ""


# ─── Registry (lazy-loaded singleton) ─────────────────────────────────────

_registry: Optional[List[SkillMeta]] = None


def _parse_frontmatter(file_path: Path) -> Optional[SkillMeta]:
    """Parse YAML-like frontmatter from a skill .md file."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError:
        return None

    # Check for --- delimited frontmatter
    if not content.startswith("---"):
        return None

    parts = content.split("---", 2)
    if len(parts) < 3:
        return None

    frontmatter_text = parts[1].strip()
    if not frontmatter_text:
        return None

    # Simple YAML-like parsing (avoid pyyaml dependency)
    meta = {"file_path": str(file_path)}
    current_list_key = None

    for line in frontmatter_text.split("\n"):
        line = line.rstrip()

        # List item continuation
        if line.startswith("  - ") and current_list_key:
            meta.setdefault(current_list_key, []).append(line.strip("  - ").strip())
            continue

        # Key-value pair
        if ":" in line and not line.startswith(" "):
            current_list_key = None
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()

            if value:
                # Scalar value
                meta[key] = value.strip('"\'')
            else:
                # Start of list
                current_list_key = key
                meta[key] = []

    # Validate required fields
    if "id" not in meta:
        meta["id"] = file_path.stem

    if "name" not in meta:
        meta["name"] = file_path.stem.replace("_", " ").title()

    return SkillMeta(
        id=meta["id"],
        name=meta["name"],
        file_path=meta["file_path"],
        triggers=meta.get("triggers", []),
        provides=meta.get("provides", ""),
        token_cost=int(meta.get("token_cost", "2000").replace("~", "")),
        priority=int(meta.get("priority", "5")),
    )


def get_registry() -> List[SkillMeta]:
    """Get or build the skill registry (cached after first call)."""
    global _registry
    if _registry is not None:
        return _registry

    _registry = []
    if not SKILLS_DIR.exists():
        return _registry

    for skill_file in sorted(SKILLS_DIR.glob("*.md")):
        meta = _parse_frontmatter(skill_file)
        if meta:
            _registry.append(meta)

    # Sort by priority (lower = higher priority)
    _registry.sort(key=lambda s: s.priority)
    return _registry


def invalidate_registry():
    """Force rebuild on next access (call after adding/modifying skills)."""
    global _registry
    _registry = None


# ─── Public API ───────────────────────────────────────────────────────────

def suggest_skills(context: Dict, token_budget: int = 4000) -> List[Dict]:
    """
    Given current context, suggest which skills to load.
    
    Returns list of suggestions (sorted by priority), respecting token budget.
    Agent decides whether to actually load them.
    
    Args:
        context: Dict with keys like section_title, phase, reviewer_role, etc.
        token_budget: Max total tokens allowed for skill content
        
    Returns:
        List of dicts: [{id, name, provides, reason, token_cost}]
    """
    registry = get_registry()
    suggestions = []
    total_tokens = 0

    for skill in registry:
        matches, reason = skill.matches(context)
        if matches and total_tokens + skill.token_cost <= token_budget:
            suggestions.append({
                "id": skill.id,
                "name": skill.name,
                "provides": skill.provides,
                "reason": reason,
                "token_cost": skill.token_cost,
            })
            total_tokens += skill.token_cost

    return suggestions


def list_skills_summary() -> List[Dict]:
    """Return all registered skills with metadata (for /skills command)."""
    registry = get_registry()
    return [
        {
            "id": s.id,
            "name": s.name,
            "triggers": s.triggers,
            "provides": s.provides,
            "token_cost": s.token_cost,
            "priority": s.priority,
        }
        for s in registry
    ]
