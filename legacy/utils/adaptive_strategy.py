"""
Adaptive Strategy — Dynamic workflow adjustment based on paper/session characteristics.

Instead of a one-size-fits-all pipeline, the agent adapts its approach:
- Short papers (< 3000 words): lighter review, skip parallel multi-role
- Long papers (> 15000 words): section-by-section processing, aggressive budget
- Already-polished papers: focus on high-level issues, skip deai
- Draft-quality papers: full pipeline with deai and deep revision

This module analyzes signals and produces strategy recommendations
that modify the agent's behavior without changing its core loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class PaperProfile:
    """Characteristics of the loaded paper that inform strategy."""
    word_count: int = 0
    section_count: int = 0
    language: str = "unknown"  # en, zh, mixed
    has_math: bool = False
    has_citations: bool = False
    avg_section_words: int = 0
    quality_signal: str = "unknown"  # draft, moderate, polished


@dataclass
class SessionStrategy:
    """Recommended strategy settings for the current session.

    These are advisory — the agent can override based on user requests.
    """
    review_depth: str = "standard"    # light | standard | deep
    parallel_review: bool = True       # Use multi-role parallel review?
    deai_enabled: bool = True          # Run de-AI pipeline?
    max_rewrite_passes: int = 3        # Cap on revision loops per section
    section_batch_size: int = 3        # How many sections to process at once
    budget_mode: str = "full"          # full | medium | minimal (override)
    citation_check: bool = True        # Verify citations?
    voice_matching: bool = True        # Apply voice profile matching?
    priority_sections: list = field(default_factory=list)  # Sections to focus on first
    skip_sections: list = field(default_factory=list)      # Sections to skip

    def to_context_string(self) -> str:
        """Generate strategy summary for system prompt injection."""
        lines = ["## Adaptive Strategy"]
        lines.append(f"Review depth: {self.review_depth}")
        if not self.parallel_review:
            lines.append("Parallel review: DISABLED (paper too short)")
        if not self.deai_enabled:
            lines.append("De-AI: DISABLED (paper already polished)")
        if self.max_rewrite_passes != 3:
            lines.append(f"Max rewrite passes: {self.max_rewrite_passes}")
        if self.priority_sections:
            lines.append(f"Priority sections: {', '.join(self.priority_sections)}")
        if self.skip_sections:
            lines.append(f"Skip sections: {', '.join(self.skip_sections)}")
        return "\n".join(lines)


class AdaptiveEngine:
    """Analyzes paper profile and recommends session strategy.

    Called once after paper parsing, and can be re-evaluated mid-session
    if new information emerges (e.g., after first review reveals quality level).
    """

    def __init__(self, workspace: Path):
        self._workspace = workspace
        self._paper_profile: Optional[PaperProfile] = None
        self._strategy: Optional[SessionStrategy] = None

    def analyze_paper(self, section_index: list[dict] = None) -> PaperProfile:
        """Build paper profile from section index metadata.

        Args:
            section_index: List of section dicts with 'id', 'title', 'word_count' fields.
                           If None, reads from workspace file.
        """
        if section_index is None:
            idx_path = self._workspace / "paper" / "section_index.json"
            if idx_path.exists():
                try:
                    section_index = json.loads(idx_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    section_index = []
            else:
                section_index = []

        profile = PaperProfile()
        profile.section_count = len(section_index)

        total_words = 0
        for sec in section_index:
            wc = sec.get("word_count", 0)
            total_words += wc
            # Detect math/citations from content hints
            title = sec.get("title", "").lower()
            if "reference" in title or "bibliography" in title:
                profile.has_citations = True

        profile.word_count = total_words
        profile.avg_section_words = total_words // max(profile.section_count, 1)

        # Language detection heuristic (from metadata if available)
        meta_path = self._workspace / "paper" / "metadata.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                profile.language = meta.get("language", "unknown")
                profile.has_math = meta.get("has_math", False)
            except (json.JSONDecodeError, OSError):
                pass

        self._paper_profile = profile
        return profile

    def compute_strategy(self, profile: PaperProfile = None,
                         budget: str = "full") -> SessionStrategy:
        """Compute recommended strategy based on paper profile.

        Args:
            profile: Paper characteristics. Uses cached if None.
            budget: User-selected budget mode (full/medium/minimal).
        """
        if profile is None:
            profile = self._paper_profile or PaperProfile()

        strategy = SessionStrategy(budget_mode=budget)

        # --- Word count based adjustments ---
        if profile.word_count < 3000:
            # Short paper: lighter treatment
            strategy.review_depth = "light"
            strategy.parallel_review = False
            strategy.max_rewrite_passes = 2
            strategy.section_batch_size = profile.section_count  # All at once
        elif profile.word_count > 15000:
            # Long paper: section-by-section, deeper review
            strategy.review_depth = "deep"
            strategy.max_rewrite_passes = 4
            strategy.section_batch_size = 2  # Process in smaller batches
        else:
            strategy.review_depth = "standard"

        # --- Budget overrides ---
        if budget == "minimal":
            strategy.deai_enabled = False
            strategy.parallel_review = False
            strategy.citation_check = False
            strategy.voice_matching = False
            strategy.max_rewrite_passes = 0
        elif budget == "medium":
            strategy.parallel_review = False
            strategy.max_rewrite_passes = 2

        # --- Language-specific ---
        if profile.language == "zh":
            # Chinese papers often need heavier de-AI
            strategy.deai_enabled = True
        elif profile.language == "en" and profile.word_count < 5000:
            # Short English papers likely polished already
            strategy.deai_enabled = False

        self._strategy = strategy
        return strategy

    def update_quality_signal(self, quality: str):
        """Update quality assessment after first review.

        Called by the agent after initial review to refine strategy.
        """
        if not self._paper_profile:
            return
        self._paper_profile.quality_signal = quality

        if quality == "polished" and self._strategy:
            self._strategy.deai_enabled = False
            self._strategy.review_depth = "light"
            self._strategy.max_rewrite_passes = 1
        elif quality == "draft" and self._strategy:
            self._strategy.deai_enabled = True
            self._strategy.review_depth = "deep"
            self._strategy.max_rewrite_passes = 4

    def get_strategy(self) -> Optional[SessionStrategy]:
        """Return current strategy (computed or default)."""
        return self._strategy

    def get_context_injection(self) -> str:
        """Generate context for system prompt."""
        if self._strategy:
            return self._strategy.to_context_string()
        return ""
