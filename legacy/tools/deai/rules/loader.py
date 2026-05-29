"""
tools/deai/rules/loader.py - Structured rule loader for de-AI engine.

Replaces the old _load_rules() Markdown parser with YAML-based structured rules.
Each scene has its own YAML file with typed rule objects.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field

try:
    import yaml
except ImportError:
    yaml = None  # Graceful fallback if PyYAML not installed


RULES_DIR = Path(__file__).parent

# Scene file mapping
_SCENE_FILES = {
    "S_GENERAL": "s_general.yaml",
    "S1": "s1_cs_english.yaml",
    "S2": "s2_chinese.yaml",
    "S3": "s3_economics.yaml",
}


@dataclass
class Rule:
    """A single de-AI detection rule."""
    id: str
    name: str
    category: str  # vocabulary, sentence, structure, rhythm, format, meta, hedging, voice
    description: str = ""
    signal_type: str = ""  # Maps to signal enum if applicable
    banned_words: List[str] = field(default_factory=list)
    banned_phrases: List[str] = field(default_factory=list)
    replacements: Dict[str, str] = field(default_factory=dict)
    override: str = ""  # Scene-specific override note
    threshold: Optional[str] = None
    thresholds: Dict[str, float] = field(default_factory=dict)


@dataclass
class SceneOverride:
    """A scene-specific signal override (suppress/modify detection)."""
    signal: str
    action: str  # "suppress" | "suppress_partial"
    reason: str = ""
    allowed_patterns: List[str] = field(default_factory=list)


@dataclass
class SignalCategory:
    """Extended signal category (S2 programmatic detection reference)."""
    id: str
    name: str
    signal_type: str = ""
    threshold: str = ""
    density_note: str = ""


@dataclass
class ConflictResolution:
    """Scene-specific conflict resolution rule."""
    condition: str
    verdict: str  # "allow" | "deny"
    note: str = ""


@dataclass
class SceneRules:
    """Complete rule set for a scene."""
    scene: str
    description: str = ""
    target_voice: str = ""
    style_notes: str = ""
    rules: List[Rule] = field(default_factory=list)
    scene_overrides: List[SceneOverride] = field(default_factory=list)
    signal_categories: List[SignalCategory] = field(default_factory=list)
    conflict_resolutions: List[ConflictResolution] = field(default_factory=list)

    def get_rules_by_category(self, category: str) -> List[Rule]:
        """Filter rules by category."""
        return [r for r in self.rules if r.category == category]

    def get_all_banned_words(self) -> List[str]:
        """Collect all banned words across rules."""
        all_banned: List[str] = []
        for r in self.rules:
            all_banned.extend(r.banned_words)
        return all_banned

    def get_all_banned_phrases(self) -> List[str]:
        """Collect all banned phrases across rules."""
        all_phrases: List[str] = []
        for r in self.rules:
            all_phrases.extend(r.banned_phrases)
        return all_phrases

    def get_all_replacements(self) -> Dict[str, str]:
        """Merge all replacement mappings across rules."""
        all_repl: Dict[str, str] = {}
        for r in self.rules:
            all_repl.update(r.replacements)
        return all_repl

    def get_suppressed_signals(self) -> Set[str]:
        """Signals fully suppressed in this scene."""
        return {o.signal for o in self.scene_overrides if o.action == "suppress"}

    def get_partially_suppressed(self) -> Dict[str, List[str]]:
        """Signals partially suppressed — returns signal→allowed_patterns."""
        result: Dict[str, List[str]] = {}
        for o in self.scene_overrides:
            if o.action == "suppress_partial":
                result[o.signal] = o.allowed_patterns
        return result


def _parse_rule(data: dict) -> Rule:
    """Parse a single rule dict from YAML into a Rule dataclass."""
    return Rule(
        id=str(data.get("id") or ""),
        name=data.get("name") or "",
        category=data.get("category") or "",
        description=data.get("description") or "",
        signal_type=data.get("signal_type") or "",
        banned_words=data.get("banned_words") or [],
        banned_phrases=data.get("banned_phrases") or [],
        replacements=data.get("replacements") or {},
        override=data.get("override") or "",
        threshold=data.get("threshold"),
        thresholds=data.get("thresholds") or {},
    )


def _parse_scene_yaml(path: Path) -> SceneRules:
    """Parse a scene YAML file into SceneRules."""
    if yaml is None:
        return SceneRules(scene="unknown", description="PyYAML not installed")

    if not path.exists():
        return SceneRules(scene="unknown", description=f"File not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data:
        return SceneRules(scene="unknown")

    rules = [_parse_rule(r) for r in data.get("rules", [])]

    overrides = [
        SceneOverride(
            signal=o.get("signal", ""),
            action=o.get("action", ""),
            reason=o.get("reason", ""),
            allowed_patterns=o.get("allowed_patterns", []),
        )
        for o in data.get("scene_overrides", [])
    ]

    signal_cats = [
        SignalCategory(
            id=sc.get("id", ""),
            name=sc.get("name", ""),
            signal_type=sc.get("signal_type", ""),
            threshold=sc.get("threshold", ""),
            density_note=sc.get("density_note", ""),
        )
        for sc in data.get("signal_categories", [])
    ]

    conflict_res = [
        ConflictResolution(
            condition=cr.get("condition", ""),
            verdict=cr.get("verdict", ""),
            note=cr.get("note", ""),
        )
        for cr in data.get("conflict_resolutions", [])
    ]

    return SceneRules(
        scene=data.get("scene", "unknown"),
        description=data.get("description", ""),
        target_voice=data.get("target_voice", ""),
        style_notes=data.get("style_notes", ""),
        rules=rules,
        scene_overrides=overrides,
        signal_categories=signal_cats,
        conflict_resolutions=conflict_res,
    )


# ─── Cache ────────────────────────────────────────────────────────────────────
_cache: Dict[str, SceneRules] = {}


def load_scene_rules(scene: str) -> SceneRules:
    """Load rules for a specific scene. Results are cached.
    
    Args:
        scene: One of "S_GENERAL", "S1", "S2", "S3"
    
    Returns:
        SceneRules dataclass with all rules and metadata.
    """
    if scene in _cache:
        return _cache[scene]

    filename = _SCENE_FILES.get(scene)
    if not filename:
        return SceneRules(scene=scene, description=f"Unknown scene: {scene}")

    path = RULES_DIR / filename
    result = _parse_scene_yaml(path)
    _cache[scene] = result
    return result


def clear_cache() -> None:
    """Clear the rules cache (useful for testing)."""
    _cache.clear()


def load_rules_for_audit(scene: str) -> str:
    """Load rules formatted as text for LLM audit prompt.
    
    Backward-compatible with the old _load_rules() function.
    Always includes S_GENERAL + scene-specific rules + shared principles.
    
    Args:
        scene: "S1", "S2", or "S3"
    
    Returns:
        Formatted text string suitable for injection into LLM prompt.
    """
    general = load_scene_rules("S_GENERAL")
    specific = load_scene_rules(scene)

    parts: List[str] = []

    # ─── S_GENERAL section ───
    parts.append(f"## {general.scene}: {general.description}")
    parts.append(f"Target: {general.target_voice}")
    parts.append("")
    for rule in general.rules:
        parts.append(f"**【{rule.id}】{rule.name}**")
        if rule.description:
            parts.append(rule.description)
        if rule.banned_words:
            parts.append(f"Banned: {', '.join(rule.banned_words[:10])}")
        if rule.banned_phrases:
            parts.append(f"Banned phrases: {'; '.join(rule.banned_phrases[:5])}")
        parts.append("")

    # ─── Scene-specific section ───
    parts.append("---")
    parts.append(f"## {specific.scene}: {specific.description}")
    parts.append(f"Target voice: {specific.target_voice}")
    if specific.style_notes:
        parts.append(f"Style: {specific.style_notes}")
    parts.append("")

    for rule in specific.rules:
        parts.append(f"**【{rule.id}】{rule.name}**")
        if rule.description:
            parts.append(rule.description)
        if rule.banned_words:
            parts.append(f"Banned: {', '.join(rule.banned_words[:10])}")
        if rule.banned_phrases:
            parts.append(f"Banned phrases: {'; '.join(rule.banned_phrases[:5])}")
        if rule.replacements:
            repls = [f"{k}→{v}" for k, v in list(rule.replacements.items())[:5]]
            parts.append(f"Replacements: {', '.join(repls)}")
        if rule.override:
            parts.append(f"⚠️ Override: {rule.override}")
        parts.append("")

    # ─── Scene overrides ───
    if specific.scene_overrides:
        parts.append("### Scene Overrides")
        for ov in specific.scene_overrides:
            parts.append(f"- {ov.signal}: {ov.action} ({ov.reason})")
        parts.append("")

    # ─── Shared principles (always appended) ───
    parts.append("---")
    parts.append("## Fix Principles")
    parts.append("1. Minimum slice: fix only flagged sentence")
    parts.append("2. Preserve meaning: semantically equivalent")
    parts.append("3. No quality loss: if fix reduces readability, keep original")
    parts.append("4. Academic register: maintain formality")
    parts.append("5. Author voice: respect consistent style")
    parts.append("6. Perplexity injection: occasionally choose less-predictable word")
    parts.append("7. Min sentence floor: ≥10 words (EN) or ≥15 chars (ZH)")
    parts.append("8. Voice Profile priority: author style > scene rules")
    parts.append("")
    parts.append("## Priority Chain")
    parts.append("User explicit request > Voice Profile > Scene rules > S_GENERAL > Default: don't change")

    return "\n".join(parts)


def get_scene_overrides(scene: str) -> Dict[str, str]:
    """Get signal overrides for a scene (for programmatic detectors).
    
    Returns dict of signal_type → action ("suppress" or "suppress_partial").
    Used by the detection engine to skip/modify certain checks per scene.
    """
    rules = load_scene_rules(scene)
    overrides: Dict[str, str] = {}
    for ov in rules.scene_overrides:
        overrides[ov.signal] = ov.action
    return overrides
