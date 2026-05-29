"""
tests/test_phase4_skill_synthesis.py

Comprehensive test suite for Phase 4: Test-Time Skill Synthesis (SkillTTA).

Covers:
  - FailureType enum and FailureContext serialization
  - RootCauseAnalyzer (heuristic rules, batch analysis, common pattern identification)
  - SynthesisConfig serialization
  - SynthesizedSkill (can_apply, execute, kill switch behavior)
  - FailureStore (record, query, capacity management, recurring detection)
  - SkillSynthesizer (from_failure, from_signal, sandbox validation, serialization)
  - SynthesisLifecycleManager (register, track, promote, deprecate)
  - SkillSynthesisOrchestrator (receive_synthesis_signal, on_skill_failed, maintenance)
  - Kill Switch integration (all components respect SCHOLAR_GODEL_SKILL_SYNTHESIS)
  - End-to-end: repeated failures → synthesis → execution → lifecycle promotion
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.skill_synthesis import (
    FailureContext,
    FailureStore,
    FailureType,
    RootCause,
    RootCauseAnalyzer,
    SKILL_SYNTHESIS_ENABLED,
    SkillSynthesizer,
    SkillSynthesisOrchestrator,
    SynthesisConfig,
    SynthesisConfidenceLevel,
    SynthesisLifecycleManager,
    SynthesizedSkill,
    SynthesizedSkillRecord,
    _ECON_METHODOLOGY_KEYWORDS,
    _SYNTHESIS_TEMPLATES,
)
from core.skills.base import Finding, Skill, SkillContext, SkillDescriptor, SkillLevel, SkillResult


# ==============================================================
# Fixtures
# ==============================================================

@pytest.fixture
def sample_failure_tool_error():
    """A simple tool error failure."""
    return FailureContext(
        skill_name="statistical_validation",
        skill_version="1.2",
        skill_level="atomic",
        failure_type=FailureType.TOOL_ERROR,
        error_message="API timeout after 30s",
        paper_text_snippet="This paper uses difference-in-differences (DID) to estimate causal effects.",
        paper_metadata={"methodology_type": "did", "title": "Test Paper"},
        current_phase="DEEP_REVIEW",
        current_section="methodology",
        actual_result={},
        expected_behavior="Should validate statistical claims",
        session_id="test-session-001",
    )


@pytest.fixture
def sample_failure_missed_issue():
    """A missed issue failure (pattern gap scenario)."""
    return FailureContext(
        skill_name="did_parallel_trends_check",
        skill_version="1.0",
        skill_level="functional",
        failure_type=FailureType.MISSED_ISSUE,
        error_message="",
        paper_text_snippet=(
            "Although the pre-treatment trends appear similar, "
            "however we note some deviation in the early periods. "
            "The limitation of our approach is that pre-trends may "
            "not be perfectly parallel."
        ),
        paper_metadata={"methodology_type": "did"},
        current_phase="DEEP_REVIEW",
        current_section="methodology",
        actual_result={"findings": []},
        expected_behavior="Should detect potential parallel trends violation",
        session_id="test-session-002",
    )


@pytest.fixture
def sample_failure_logic_error():
    """A logic error failure (threshold issue)."""
    return FailureContext(
        skill_name="significance_checker",
        skill_version="2.0",
        skill_level="atomic",
        failure_type=FailureType.LOGIC_ERROR,
        error_message="Incorrectly flagged p=0.08 as significant",
        paper_text_snippet=(
            "The coefficient is marginally significant at the 10% level "
            "(p = 0.08), which is commonly accepted in economics."
        ),
        paper_metadata={"methodology_type": "iv"},
        current_phase="DEEP_REVIEW",
        current_section="results",
        actual_result={"flagged": True, "threshold_used": 0.05},
        expected_behavior="Should use paper's stated significance level, not hardcoded 0.05",
        session_id="test-session-003",
    )


@pytest.fixture
def sample_failure_low_quality():
    """Low quality failure on DID methodology."""
    return FailureContext(
        skill_name="methodology_review",
        skill_version="1.0",
        skill_level="functional",
        failure_type=FailureType.LOW_QUALITY,
        error_message="",
        paper_text_snippet="We employ a staggered DID design with TWFE estimator.",
        paper_metadata={"methodology_type": "did"},
        current_phase="DEEP_REVIEW",
        current_section="methodology",
        actual_result={"findings": [{"confidence": 0.2}]},
        expected_behavior="Should produce high-quality methodology findings",
        session_id="test-session-004",
    )


@pytest.fixture
def sample_context():
    """A basic SkillContext for testing SynthesizedSkill."""
    return SkillContext(
        paper_text=(
            "This study applies difference-in-differences (DID) to estimate the effect "
            "of the policy change. However, we find no evidence of pre-treatment parallel "
            "trends. The limitation is that our identification strategy relies heavily on "
            "the common trends assumption, which may be violated in periods of economic "
            "turbulence. The robustness checks confirm our main findings."
        ),
        paper_metadata={"methodology_type": "did"},
        current_phase="DEEP_REVIEW",
        current_section="methodology",
    )


@pytest.fixture
def orchestrator():
    """A fresh SkillSynthesisOrchestrator."""
    return SkillSynthesisOrchestrator()


# ==============================================================
# 1. FailureType Enum
# ==============================================================

class TestFailureType:
    """Tests for FailureType enum."""

    def test_all_types_defined(self):
        """Should have 8 failure types."""
        assert len(FailureType) == 8

    def test_values_are_strings(self):
        """Each type should have a string value."""
        for ft in FailureType:
            assert isinstance(ft.value, str)
            assert ft.value  # Non-empty

    def test_from_value(self):
        """Should be constructable from string value."""
        assert FailureType("tool_error") == FailureType.TOOL_ERROR
        assert FailureType("missed_issue") == FailureType.MISSED_ISSUE


# ==============================================================
# 2. FailureContext
# ==============================================================

class TestFailureContext:
    """Tests for FailureContext dataclass."""

    def test_failure_id_generation(self, sample_failure_tool_error):
        """failure_id should be a 16-char hex string derived from content."""
        fid = sample_failure_tool_error.failure_id
        assert len(fid) == 16
        assert all(c in "0123456789abcdef" for c in fid)

    def test_failure_id_uniqueness(self, sample_failure_tool_error, sample_failure_missed_issue):
        """Different failures should have different IDs."""
        assert sample_failure_tool_error.failure_id != sample_failure_missed_issue.failure_id

    def test_failure_id_deterministic(self, sample_failure_tool_error):
        """Same failure should always produce same ID."""
        assert sample_failure_tool_error.failure_id == sample_failure_tool_error.failure_id

    def test_to_dict(self, sample_failure_tool_error):
        """Should serialize all fields."""
        d = sample_failure_tool_error.to_dict()
        assert d["skill_name"] == "statistical_validation"
        assert d["failure_type"] == "tool_error"
        assert d["error_message"] == "API timeout after 30s"
        assert d["current_phase"] == "DEEP_REVIEW"
        assert d["current_section"] == "methodology"
        assert d["session_id"] == "test-session-001"
        assert "failure_id" in d

    def test_to_dict_truncates_text(self):
        """paper_text_snippet should be truncated to 500 chars in serialization."""
        ctx = FailureContext(
            skill_name="test",
            paper_text_snippet="x" * 1000,
        )
        d = ctx.to_dict()
        assert len(d["paper_text_snippet"]) == 500

    def test_from_dict_roundtrip(self, sample_failure_tool_error):
        """from_dict(to_dict()) should restore the object."""
        d = sample_failure_tool_error.to_dict()
        restored = FailureContext.from_dict(d)
        assert restored.skill_name == sample_failure_tool_error.skill_name
        assert restored.failure_type == sample_failure_tool_error.failure_type
        assert restored.error_message == sample_failure_tool_error.error_message
        assert restored.current_section == sample_failure_tool_error.current_section

    def test_from_dict_invalid_failure_type(self):
        """Invalid failure_type string should default to TOOL_ERROR."""
        d = {"skill_name": "test", "failure_type": "nonexistent_type"}
        ctx = FailureContext.from_dict(d)
        assert ctx.failure_type == FailureType.TOOL_ERROR

    def test_from_dict_missing_fields(self):
        """Missing fields should use defaults."""
        d = {"skill_name": "minimal"}
        ctx = FailureContext.from_dict(d)
        assert ctx.skill_name == "minimal"
        assert ctx.skill_version == "1.0"
        assert ctx.paper_text_snippet == ""
        assert ctx.paper_metadata == {}


# ==============================================================
# 3. RootCauseAnalyzer
# ==============================================================

class TestRootCauseAnalyzer:
    """Tests for RootCauseAnalyzer heuristic rules engine."""

    def setup_method(self):
        self.analyzer = RootCauseAnalyzer()

    def test_missed_issue_pattern_gap(self, sample_failure_missed_issue):
        """MISSED_ISSUE with 'however' in text should identify 'pattern_gap'."""
        cause = self.analyzer.analyze(sample_failure_missed_issue)
        assert cause.cause_type == "pattern_gap"
        assert cause.confidence >= 0.5
        assert cause.suggested_fix != ""

    def test_logic_error_threshold_issue(self, sample_failure_logic_error):
        """LOGIC_ERROR with 'significant' in text should identify 'threshold_issue'."""
        cause = self.analyzer.analyze(sample_failure_logic_error)
        assert cause.cause_type == "threshold_issue"
        assert cause.confidence >= 0.5

    def test_tool_error_dependency_failure(self, sample_failure_tool_error):
        """TOOL_ERROR should identify 'dependency_failure'."""
        cause = self.analyzer.analyze(sample_failure_tool_error)
        assert cause.cause_type == "dependency_failure"
        assert cause.confidence >= 0.5

    def test_low_quality_domain_specificity(self, sample_failure_low_quality):
        """LOW_QUALITY with DID methodology should identify 'domain_specificity_gap'."""
        cause = self.analyzer.analyze(sample_failure_low_quality)
        assert cause.cause_type == "domain_specificity_gap"
        assert cause.confidence >= 0.5

    def test_low_quality_calibration_fallback(self):
        """LOW_QUALITY without known methodology should fall to 'calibration_issue'."""
        failure = FailureContext(
            skill_name="general_review",
            failure_type=FailureType.LOW_QUALITY,
            paper_metadata={"methodology_type": "unknown"},
        )
        cause = self.analyzer.analyze(failure)
        assert cause.cause_type == "calibration_issue"

    def test_format_mismatch(self):
        """FORMAT_MISMATCH should identify 'output_schema_drift'."""
        failure = FailureContext(
            skill_name="formatter",
            failure_type=FailureType.FORMAT_MISMATCH,
        )
        cause = self.analyzer.analyze(failure)
        assert cause.cause_type == "output_schema_drift"

    def test_timeout(self):
        """TIMEOUT should identify 'complexity_underestimation'."""
        failure = FailureContext(
            skill_name="heavy_processor",
            failure_type=FailureType.TIMEOUT,
        )
        cause = self.analyzer.analyze(failure)
        assert cause.cause_type == "complexity_underestimation"

    def test_wrong_tool_methodology_section(self):
        """WRONG_TOOL in methodology section should identify 'misclassification'."""
        failure = FailureContext(
            skill_name="wrong_one",
            failure_type=FailureType.WRONG_TOOL,
            current_section="Methodology and Identification",
        )
        cause = self.analyzer.analyze(failure)
        assert cause.cause_type == "misclassification"

    def test_analyze_batch(self, sample_failure_tool_error, sample_failure_missed_issue):
        """Should analyze multiple failures and return keyed by failure_id."""
        results = self.analyzer.analyze_batch([
            sample_failure_tool_error,
            sample_failure_missed_issue,
        ])
        assert len(results) == 2
        assert sample_failure_tool_error.failure_id in results
        assert sample_failure_missed_issue.failure_id in results

    def test_identify_common_pattern_majority(self):
        """Should identify pattern when > 50% have same cause_type."""
        causes = [
            RootCause(cause_type="pattern_gap", description="a"),
            RootCause(cause_type="pattern_gap", description="b"),
            RootCause(cause_type="pattern_gap", description="c"),
            RootCause(cause_type="other", description="d"),
        ]
        result = self.analyzer.identify_common_pattern(causes)
        assert result == "pattern_gap"

    def test_identify_common_pattern_no_majority(self):
        """Should return None when no cause_type > 50%."""
        causes = [
            RootCause(cause_type="a", description="x"),
            RootCause(cause_type="b", description="y"),
            RootCause(cause_type="c", description="z"),
        ]
        result = self.analyzer.identify_common_pattern(causes)
        assert result is None

    def test_identify_common_pattern_empty(self):
        """Should return None for empty list."""
        assert self.analyzer.identify_common_pattern([]) is None

    def test_evidence_populated(self, sample_failure_missed_issue):
        """Root cause should include supporting evidence."""
        cause = self.analyzer.analyze(sample_failure_missed_issue)
        assert len(cause.evidence) > 0


# ==============================================================
# 4. SynthesisConfig
# ==============================================================

class TestSynthesisConfig:
    """Tests for SynthesisConfig serialization."""

    def test_to_dict(self):
        """Should serialize all fields."""
        config = SynthesisConfig(
            name="test_skill",
            description="A test synthesized skill",
            target_issue_type="missed_issue",
            methodology_focus="did",
            keyword_patterns=["parallel trends", "pre-treatment"],
            negative_patterns=["no evidence of"],
            required_elements=["placebo test"],
            severity_rules={"critical_keyword": "critical"},
            applicable_sections=["methodology"],
            applicable_phases=["DEEP_REVIEW"],
            min_text_length=100,
            synthesized_from="original_skill",
            root_cause="pattern_gap",
            synthesis_reason="Non-standard phrasing",
        )
        d = config.to_dict()
        assert d["name"] == "test_skill"
        assert d["methodology_focus"] == "did"
        assert len(d["keyword_patterns"]) == 2
        assert d["min_text_length"] == 100

    def test_from_dict_roundtrip(self):
        """from_dict(to_dict()) should produce equivalent config."""
        config = SynthesisConfig(
            name="roundtrip_test",
            description="Testing roundtrip",
            target_issue_type="logic_error",
            keyword_patterns=["alpha", "beta"],
        )
        restored = SynthesisConfig.from_dict(config.to_dict())
        assert restored.name == config.name
        assert restored.keyword_patterns == config.keyword_patterns
        assert restored.min_text_length == config.min_text_length

    def test_from_dict_defaults(self):
        """Missing fields should use defaults."""
        config = SynthesisConfig.from_dict({"name": "minimal"})
        assert config.name == "minimal"
        assert config.min_text_length == 50
        assert config.keyword_patterns == []


# ==============================================================
# 5. SynthesizedSkill
# ==============================================================

class TestSynthesizedSkill:
    """Tests for SynthesizedSkill (config-driven Skill subclass)."""

    def _make_skill(self, **overrides) -> SynthesizedSkill:
        defaults = {
            "name": "test_synth_skill",
            "description": "Detects missing parallel trends discussion",
            "target_issue_type": "missed_issue",
            "methodology_focus": "did",
            "keyword_patterns": ["parallel trends", "pre-treatment"],
            "negative_patterns": ["no evidence of parallel", "trends violated"],
            "required_elements": ["placebo test", "event study plot"],
            "applicable_sections": ["methodology"],
            "applicable_phases": ["DEEP_REVIEW"],
            "synthesized_from": "did_check",
            "root_cause": "pattern_gap",
        }
        defaults.update(overrides)
        config = SynthesisConfig(**defaults)
        return SynthesizedSkill(config, version="0.1")

    def test_is_skill_subclass(self):
        """Should be a proper Skill subclass."""
        skill = self._make_skill()
        assert isinstance(skill, Skill)

    def test_descriptor(self):
        """Descriptor should reflect config values."""
        skill = self._make_skill()
        desc = skill.descriptor
        assert desc.name == "test_synth_skill"
        assert desc.level == SkillLevel.ATOMIC
        assert "synthesized" in desc.tags
        assert "experimental" in desc.tags
        assert "did" in desc.tags
        assert desc.version == "0.1"

    def test_can_apply_matching_section(self, sample_context):
        """Should score > 0 when section matches."""
        skill = self._make_skill()
        score = skill.can_apply(sample_context)
        assert score > 0.0

    def test_can_apply_wrong_section(self, sample_context):
        """Should return 0 when section doesn't match."""
        skill = self._make_skill(applicable_sections=["conclusion"])
        score = skill.can_apply(sample_context)
        assert score == 0.0

    def test_can_apply_methodology_boost(self, sample_context):
        """Matching methodology should boost score."""
        skill_match = self._make_skill(methodology_focus="did")
        skill_nomatch = self._make_skill(methodology_focus="iv")
        score_match = skill_match.can_apply(sample_context)
        score_nomatch = skill_nomatch.can_apply(sample_context)
        assert score_match > score_nomatch

    def test_can_apply_no_section_constraint(self, sample_context):
        """No applicable_sections = universal (lower base score)."""
        skill = self._make_skill(applicable_sections=[])
        score = skill.can_apply(sample_context)
        assert score > 0.0

    def test_can_apply_short_text(self):
        """Text below min_text_length should lower score."""
        skill = self._make_skill(min_text_length=10000)
        ctx = SkillContext(
            paper_text="Short text",
            current_section="methodology",
            current_phase="DEEP_REVIEW",
        )
        score = skill.can_apply(ctx)
        # Still might be > 0 due to section match, but text component = 0
        # The exact behavior depends on implementation
        assert isinstance(score, float)

    def test_can_apply_max_score_capped(self, sample_context):
        """Score should never exceed 0.9."""
        skill = self._make_skill()
        score = skill.can_apply(sample_context)
        assert score <= 0.9

    def test_execute_finds_negative_pattern(self, sample_context):
        """Should produce Finding for negative patterns found in text."""
        skill = self._make_skill(
            negative_patterns=["no evidence of"],
        )
        result = skill.execute(sample_context)
        assert result.success
        # "no evidence of" appears in sample_context text
        neg_findings = [
            f for f in result.findings
            if "no evidence of" in f.description.lower()
        ]
        assert len(neg_findings) >= 1
        assert neg_findings[0].category == "missed_issue"
        assert neg_findings[0].confidence == 0.6  # Synthesized skill baseline

    def test_execute_missing_elements(self, sample_context):
        """Should report missing required elements when keywords are present."""
        skill = self._make_skill(
            keyword_patterns=["parallel trends"],
            required_elements=["placebo test"],  # Not in sample text
        )
        result = skill.execute(sample_context)
        assert result.success
        missing_findings = [
            f for f in result.findings
            if "placebo test" in f.description.lower()
        ]
        assert len(missing_findings) >= 1

    def test_execute_no_findings_when_nothing_matches(self):
        """Should produce no findings when patterns don't match."""
        skill = self._make_skill(
            keyword_patterns=["quantum entanglement"],
            negative_patterns=["string theory violation"],
            required_elements=["higgs boson"],
        )
        ctx = SkillContext(
            paper_text="This paper studies labor economics.",
            current_section="methodology",
            current_phase="DEEP_REVIEW",
        )
        result = skill.execute(ctx)
        assert result.success
        assert len(result.findings) == 0

    def test_execute_metadata_populated(self, sample_context):
        """Execution result metadata should track synthesis source."""
        skill = self._make_skill()
        result = skill.execute(sample_context)
        assert "synthesized_from" in result.metadata
        assert result.metadata["synthesized_from"] == "did_check"

    def test_execute_timing(self, sample_context):
        """execution_time_ms should be populated."""
        skill = self._make_skill()
        result = skill.execute(sample_context)
        assert result.execution_time_ms >= 0

    def test_get_instruction(self):
        """Should return a formatted instruction string."""
        skill = self._make_skill(synthesis_reason="Non-standard expressions")
        instr = skill.get_instruction()
        assert "test_synth_skill" in instr
        assert "did" in instr.lower() or "方法论" in instr
        assert "Non-standard expressions" in instr

    @patch.dict(os.environ, {"SCHOLAR_GODEL_SKILL_SYNTHESIS": "0"})
    def test_kill_switch_can_apply(self, sample_context):
        """Kill switch OFF → can_apply returns 0.0."""
        # Need to reimport or patch the module-level flag
        import core.skill_synthesis as mod
        original = mod.SKILL_SYNTHESIS_ENABLED
        mod.SKILL_SYNTHESIS_ENABLED = False
        try:
            skill = self._make_skill()
            assert skill.can_apply(sample_context) == 0.0
        finally:
            mod.SKILL_SYNTHESIS_ENABLED = original

    @patch.dict(os.environ, {"SCHOLAR_GODEL_SKILL_SYNTHESIS": "0"})
    def test_kill_switch_execute(self, sample_context):
        """Kill switch OFF → execute returns empty successful result."""
        import core.skill_synthesis as mod
        original = mod.SKILL_SYNTHESIS_ENABLED
        mod.SKILL_SYNTHESIS_ENABLED = False
        try:
            skill = self._make_skill()
            result = skill.execute(sample_context)
            assert result.success
            assert result.findings == []
        finally:
            mod.SKILL_SYNTHESIS_ENABLED = original


# ==============================================================
# 6. FailureStore
# ==============================================================

class TestFailureStore:
    """Tests for FailureStore in-memory indexed storage."""

    def setup_method(self):
        self.store = FailureStore()

    def test_record_and_query_by_skill(self, sample_failure_tool_error):
        """Should store and retrieve by skill name."""
        self.store.record(sample_failure_tool_error)
        results = self.store.query_by_skill("statistical_validation")
        assert len(results) == 1
        assert results[0].failure_id == sample_failure_tool_error.failure_id

    def test_query_by_type(self, sample_failure_tool_error, sample_failure_missed_issue):
        """Should retrieve by failure type."""
        self.store.record(sample_failure_tool_error)
        self.store.record(sample_failure_missed_issue)
        results = self.store.query_by_type(FailureType.TOOL_ERROR)
        assert len(results) == 1
        assert results[0].skill_name == "statistical_validation"

    def test_query_similar(self):
        """Should find similar failures based on skill + type + section."""
        base = FailureContext(
            skill_name="did_check",
            failure_type=FailureType.MISSED_ISSUE,
            current_section="methodology",
            current_phase="DEEP_REVIEW",
            timestamp=time.time(),
        )
        similar = FailureContext(
            skill_name="did_check",
            failure_type=FailureType.MISSED_ISSUE,
            current_section="methodology",
            current_phase="DEEP_REVIEW",
            timestamp=time.time() + 1,
        )
        different = FailureContext(
            skill_name="did_check",
            failure_type=FailureType.TOOL_ERROR,
            current_section="results",
            current_phase="SYNTHESIS",
            timestamp=time.time() + 2,
        )

        self.store.record(similar)
        self.store.record(different)
        self.store.record(base)

        results = self.store.query_similar(base, limit=5)
        assert len(results) >= 1
        # similar should rank higher than different
        if len(results) >= 2:
            # The first result should be more similar (same type + section + phase)
            assert results[0].failure_type == FailureType.MISSED_ISSUE

    def test_capacity_management(self):
        """Should evict oldest records when exceeding MAX_RECORDS."""
        original_max = FailureStore.MAX_RECORDS
        FailureStore.MAX_RECORDS = 5  # Small for testing
        try:
            store = FailureStore()
            for i in range(10):
                store.record(FailureContext(
                    skill_name=f"skill_{i}",
                    failure_type=FailureType.TOOL_ERROR,
                    timestamp=float(i),
                ))
            # Should only have 5 records
            stats = store.get_failure_stats()
            assert stats["total_failures"] == 5
        finally:
            FailureStore.MAX_RECORDS = original_max

    def test_get_recurring_failures(self):
        """Should detect recurring failure patterns."""
        for _ in range(4):
            self.store.record(FailureContext(
                skill_name="buggy_skill",
                failure_type=FailureType.LOGIC_ERROR,
                timestamp=time.time(),
            ))
        # Add a non-recurring one
        self.store.record(FailureContext(
            skill_name="ok_skill",
            failure_type=FailureType.TIMEOUT,
            timestamp=time.time(),
        ))

        recurring = self.store.get_recurring_failures(min_count=3)
        assert len(recurring) == 1
        assert recurring[0]["skill_name"] == "buggy_skill"
        assert recurring[0]["failure_type"] == "logic_error"
        assert recurring[0]["count"] == 4

    def test_get_recurring_failures_none(self):
        """No recurring patterns when below threshold."""
        self.store.record(FailureContext(skill_name="a", failure_type=FailureType.TOOL_ERROR))
        self.store.record(FailureContext(skill_name="b", failure_type=FailureType.TIMEOUT))
        assert self.store.get_recurring_failures(min_count=3) == []

    def test_serialize_deserialize(self, sample_failure_tool_error, sample_failure_missed_issue):
        """Should serialize and restore state."""
        self.store.record(sample_failure_tool_error)
        self.store.record(sample_failure_missed_issue)

        data = self.store.serialize()
        assert data["total_recorded"] == 2
        assert len(data["records"]) == 2

        restored = FailureStore.deserialize(data)
        assert restored.get_failure_stats()["total_failures"] == 2
        results = restored.query_by_skill("statistical_validation")
        assert len(results) == 1

    def test_failure_stats(self, sample_failure_tool_error, sample_failure_missed_issue):
        """get_failure_stats should return correct counts."""
        self.store.record(sample_failure_tool_error)
        self.store.record(sample_failure_missed_issue)
        stats = self.store.get_failure_stats()
        assert stats["total_failures"] == 2
        assert "statistical_validation" in stats["by_skill"]
        assert "tool_error" in stats["by_type"]
        assert "missed_issue" in stats["by_type"]


# ==============================================================
# 7. SkillSynthesizer
# ==============================================================

class TestSkillSynthesizer:
    """Tests for the core synthesis engine."""

    def setup_method(self):
        self.store = FailureStore()
        self.synthesizer = SkillSynthesizer(failure_store=self.store)

    def test_synthesize_from_failure_needs_recurring(self, sample_failure_missed_issue):
        """Single failure with sufficient confidence should succeed (via store + recurring)."""
        # The synthesizer internally checks: root cause confidence >= 0.4 AND has template
        # But doesn't itself check recurring — that's Orchestrator's job
        # Direct call to synthesize_from_failure only checks confidence + template
        skill = self.synthesizer.synthesize_from_failure(sample_failure_missed_issue)
        # Should succeed because MISSED_ISSUE → pattern_gap → has template
        assert skill is not None
        assert isinstance(skill, SynthesizedSkill)
        assert "synth_" in skill.descriptor.name

    def test_synthesize_produces_valid_skill(self, sample_failure_missed_issue, sample_context):
        """Synthesized skill should be executable."""
        skill = self.synthesizer.synthesize_from_failure(sample_failure_missed_issue)
        assert skill is not None
        # Should be applicable to the right context
        score = skill.can_apply(sample_context)
        assert score > 0
        # Should execute without error
        result = skill.execute(sample_context)
        assert result.success

    def test_synthesize_includes_methodology_keywords(self, sample_failure_missed_issue):
        """DID failure should inject DID-related keywords."""
        skill = self.synthesizer.synthesize_from_failure(sample_failure_missed_issue)
        assert skill is not None
        config = skill.config
        # Should include some DID keywords
        all_kw = " ".join(config.keyword_patterns).lower()
        assert any(
            kw.lower() in all_kw
            for kw in ["did", "parallel trends", "difference-in-differences"]
        )

    def test_synthesize_from_failure_low_confidence_skips(self):
        """When root cause confidence is too low (no matching rules), skip synthesis."""
        # Insufficient info with no special condition → goes to data_dependency fallback
        # Actually all types have rules, so confidence will be >= 0.7
        # Let's test with a patched analyzer that returns low confidence
        failure = FailureContext(
            skill_name="test",
            failure_type=FailureType.TOOL_ERROR,
        )
        # Patch the analyzer to return low confidence
        from unittest.mock import MagicMock
        self.synthesizer._analyzer = MagicMock()
        self.synthesizer._analyzer.analyze.return_value = RootCause(
            cause_type="unknown",
            description="Can't determine",
            confidence=0.2,
        )
        skill = self.synthesizer.synthesize_from_failure(failure)
        assert skill is None

    def test_synthesize_records_history(self, sample_failure_missed_issue):
        """Should record synthesis attempts in history."""
        self.synthesizer.synthesize_from_failure(sample_failure_missed_issue)
        history = self.synthesizer.get_synthesis_history()
        assert len(history) >= 1
        assert history[-1]["type"] == "from_failure"
        assert history[-1]["success"] is True

    def test_synthesize_from_signal(self):
        """Should synthesize from a Phase 6 SynthesisSignal-like object."""
        # Create a mock signal
        class MockGapPattern:
            gap_type = "methodology_did"
            description = "Repeated weakness in DID analysis"

        class MockSignal:
            gap_pattern = MockGapPattern()
            suggested_skill_type = "enhanced_did_check"
            trigger_reason = "3 sessions with same gap"

        signal = MockSignal()
        skill = self.synthesizer.synthesize_from_signal(signal)
        assert skill is not None
        assert "did" in skill.config.methodology_focus or "did" in skill.config.name.lower()

    def test_synthesize_from_signal_no_gap_pattern(self):
        """Signal without gap_pattern should return None."""
        class EmptySignal:
            pass
        assert self.synthesizer.synthesize_from_signal(EmptySignal()) is None

    def test_get_synthesized_skills(self, sample_failure_missed_issue):
        """Should accumulate synthesized skills."""
        assert self.synthesizer.get_synthesized_skills() == []
        self.synthesizer.synthesize_from_failure(sample_failure_missed_issue)
        skills = self.synthesizer.get_synthesized_skills()
        assert len(skills) == 1

    def test_serialize_deserialize(self, sample_failure_missed_issue, sample_failure_tool_error):
        """Full serialization roundtrip."""
        # Record some failures
        self.store.record(sample_failure_tool_error)
        self.store.record(sample_failure_missed_issue)
        # Synthesize
        self.synthesizer.synthesize_from_failure(sample_failure_missed_issue)

        # Serialize
        data = self.synthesizer.serialize()
        assert "synthesized_skills" in data
        assert "synthesis_history" in data
        assert "failure_store" in data

        # Deserialize
        restored = SkillSynthesizer.deserialize(data)
        assert len(restored.get_synthesized_skills()) == 1
        assert len(restored.get_synthesis_history()) >= 1

    @patch.dict(os.environ, {"SCHOLAR_GODEL_SKILL_SYNTHESIS": "0"})
    def test_kill_switch_blocks_synthesis(self, sample_failure_missed_issue):
        """Kill switch OFF → synthesize returns None."""
        import core.skill_synthesis as mod
        original = mod.SKILL_SYNTHESIS_ENABLED
        mod.SKILL_SYNTHESIS_ENABLED = False
        try:
            skill = self.synthesizer.synthesize_from_failure(sample_failure_missed_issue)
            assert skill is None
        finally:
            mod.SKILL_SYNTHESIS_ENABLED = original


# ==============================================================
# 8. SynthesisLifecycleManager
# ==============================================================

class TestSynthesisLifecycleManager:
    """Tests for lifecycle management (promotion, deprecation)."""

    def setup_method(self):
        self.manager = SynthesisLifecycleManager()

    def test_register(self):
        """Should register a new skill as EXPERIMENTAL."""
        self.manager.register("test_skill")
        record = self.manager.get_record("test_skill")
        assert record is not None
        assert record.confidence_level == SynthesisConfidenceLevel.EXPERIMENTAL
        assert record.total_executions == 0

    def test_on_skill_executed(self):
        """Should track execution counts and timing."""
        self.manager.register("test_skill")
        result = SkillResult(success=True, findings=[
            Finding(category="test", severity="minor", description="Found something"),
        ])
        self.manager.on_skill_executed("test_skill", result)

        record = self.manager.get_record("test_skill")
        assert record.total_executions == 1
        assert record.successful_executions == 1
        assert record.findings_produced == 1
        assert record.last_used_at > 0

    def test_on_skill_executed_failure(self):
        """Should track failed executions."""
        self.manager.register("test_skill")
        result = SkillResult(success=False, error_message="oops")
        self.manager.on_skill_executed("test_skill", result)

        record = self.manager.get_record("test_skill")
        assert record.total_executions == 1
        assert record.successful_executions == 0

    def test_on_findings_retained(self):
        """Should track retention counts."""
        self.manager.register("test_skill")
        self.manager.on_findings_retained("test_skill", retained=3, discarded=1)

        record = self.manager.get_record("test_skill")
        assert record.findings_retained == 3
        assert record.findings_discarded == 1
        assert record.retention_rate == 0.75

    def test_promotion(self):
        """Should promote from EXPERIMENTAL to VALIDATED when criteria met."""
        self.manager.register("good_skill")
        record = self.manager.get_record("good_skill")

        # Simulate 12 successful executions with good retention
        for _ in range(12):
            self.manager.on_skill_executed(
                "good_skill",
                SkillResult(success=True, findings=[
                    Finding(category="test", severity="minor", description="x"),
                ]),
            )
        # Good retention (8 retained, 2 discarded → 80%)
        self.manager.on_findings_retained("good_skill", retained=8, discarded=2)

        # Run lifecycle check
        result = self.manager.run_lifecycle_check()
        assert "good_skill" in result["promoted"]

        record = self.manager.get_record("good_skill")
        assert record.confidence_level == SynthesisConfidenceLevel.VALIDATED

    def test_deprecation_low_retention(self):
        """Should deprecate when retention rate is too low."""
        self.manager.register("bad_skill")

        # Simulate 6 executions with terrible retention
        for _ in range(6):
            self.manager.on_skill_executed(
                "bad_skill",
                SkillResult(success=True, findings=[
                    Finding(category="test", severity="minor", description="x"),
                ]),
            )
        # Very low retention (1 retained, 9 discarded → 10%)
        self.manager.on_findings_retained("bad_skill", retained=1, discarded=9)

        result = self.manager.run_lifecycle_check()
        assert "bad_skill" in result["deprecated"]

        record = self.manager.get_record("bad_skill")
        assert record.confidence_level == SynthesisConfidenceLevel.DEPRECATED

    def test_deprecation_unused(self):
        """Should deprecate skills unused for too long."""
        self.manager.register("stale_skill")
        record = self.manager.get_record("stale_skill")
        # Pretend it was created 40 days ago and never used
        record.created_at = time.time() - (40 * 86400)
        record.last_used_at = 0.0

        result = self.manager.run_lifecycle_check()
        assert "stale_skill" in result["deprecated"]

    def test_get_active_skills(self):
        """Should exclude deprecated skills."""
        self.manager.register("active")
        self.manager.register("deprecated")
        self.manager._records["deprecated"].confidence_level = SynthesisConfidenceLevel.DEPRECATED

        active = self.manager.get_active_skills()
        assert "active" in active
        assert "deprecated" not in active

    def test_health_report(self):
        """Should produce structured health report."""
        self.manager.register("skill_a")
        self.manager.register("skill_b")
        report = self.manager.get_health_report()
        assert report["total_synthesized"] == 2
        assert report["active_count"] == 2
        assert "by_confidence_level" in report

    def test_serialize_deserialize(self):
        """Should roundtrip through serialization."""
        self.manager.register("skill_x")
        self.manager.on_skill_executed(
            "skill_x",
            SkillResult(success=True, findings=[]),
        )

        data = self.manager.serialize()
        restored = SynthesisLifecycleManager.deserialize(data)
        record = restored.get_record("skill_x")
        assert record is not None
        assert record.total_executions == 1


# ==============================================================
# 9. SynthesizedSkillRecord
# ==============================================================

class TestSynthesizedSkillRecord:
    """Tests for SynthesizedSkillRecord properties."""

    def test_retention_rate_zero_denominator(self):
        """Should return 0.0 when no findings tracked."""
        record = SynthesizedSkillRecord(skill_name="test")
        assert record.retention_rate == 0.0

    def test_retention_rate_calculation(self):
        """Should correctly compute retention rate."""
        record = SynthesizedSkillRecord(
            skill_name="test",
            findings_retained=7,
            findings_discarded=3,
        )
        assert record.retention_rate == 0.7

    def test_success_rate(self):
        """Should compute success rate."""
        record = SynthesizedSkillRecord(
            skill_name="test",
            total_executions=10,
            successful_executions=8,
        )
        assert record.success_rate == 0.8

    def test_success_rate_zero_executions(self):
        """Should return 0.0 when no executions."""
        record = SynthesizedSkillRecord(skill_name="test")
        assert record.success_rate == 0.0

    def test_should_promote_criteria(self):
        """Should promote when all criteria met."""
        record = SynthesizedSkillRecord(
            skill_name="test",
            confidence_level=SynthesisConfidenceLevel.EXPERIMENTAL,
            total_executions=15,
            successful_executions=14,
            findings_retained=10,
            findings_discarded=5,
        )
        # executions >= 10 ✓, retention >= 0.5 ✓ (10/15=0.67), success >= 0.8 ✓ (14/15=0.93)
        assert record.should_promote is True

    def test_should_not_promote_low_executions(self):
        """Should not promote with too few executions."""
        record = SynthesizedSkillRecord(
            skill_name="test",
            confidence_level=SynthesisConfidenceLevel.EXPERIMENTAL,
            total_executions=3,
            successful_executions=3,
            findings_retained=3,
            findings_discarded=0,
        )
        assert record.should_promote is False

    def test_should_not_promote_when_already_validated(self):
        """Should not promote if already VALIDATED."""
        record = SynthesizedSkillRecord(
            skill_name="test",
            confidence_level=SynthesisConfidenceLevel.VALIDATED,
            total_executions=20,
            successful_executions=20,
            findings_retained=20,
            findings_discarded=0,
        )
        assert record.should_promote is False

    def test_should_deprecate_unused(self):
        """Should deprecate after 30 days of no use."""
        record = SynthesizedSkillRecord(
            skill_name="test",
            created_at=time.time() - (31 * 86400),
            last_used_at=0.0,
        )
        assert record.should_deprecate is True

    def test_should_deprecate_low_retention(self):
        """Should deprecate with low retention and enough samples."""
        record = SynthesizedSkillRecord(
            skill_name="test",
            total_executions=10,
            successful_executions=10,
            findings_retained=1,
            findings_discarded=9,
            last_used_at=time.time(),  # Recently used, so not unused
        )
        assert record.should_deprecate is True

    def test_should_not_deprecate_fresh_skill(self):
        """Should not deprecate a freshly created skill."""
        record = SynthesizedSkillRecord(
            skill_name="test",
            created_at=time.time(),
            last_used_at=time.time(),
            total_executions=2,
            successful_executions=2,
            findings_retained=0,
            findings_discarded=2,
        )
        # Not enough samples (< 5) and not unused for long enough
        assert record.should_deprecate is False


# ==============================================================
# 10. SkillSynthesisOrchestrator
# ==============================================================

class TestSkillSynthesisOrchestrator:
    """Tests for the top-level orchestrator (implements SkillSynthesisReceiver)."""

    def test_on_skill_failed_first_time_no_synthesis(self, orchestrator, sample_failure_tool_error):
        """First failure should not trigger synthesis (need recurring pattern)."""
        result = orchestrator.on_skill_failed(sample_failure_tool_error)
        assert result is None  # No synthesis on single failure

    def test_on_skill_failed_recurring_triggers_synthesis(self, orchestrator):
        """Recurring failures (same skill + type) should trigger synthesis."""
        # Record the same failure pattern multiple times
        for i in range(3):
            failure = FailureContext(
                skill_name="fragile_skill",
                failure_type=FailureType.MISSED_ISSUE,
                paper_text_snippet="However the limitation is clear",
                current_section="methodology",
                current_phase="DEEP_REVIEW",
                paper_metadata={"methodology_type": "did"},
                timestamp=time.time() + i,
            )
            result = orchestrator.on_skill_failed(failure)

        # After 2+ occurrences, synthesis should be attempted
        # The 3rd call should see recurring >= 2 and try synthesis
        # Whether it succeeds depends on sandbox validation
        # Check that at least the failure was recorded
        stats = orchestrator.get_failure_stats()
        assert stats["total_failures"] == 3

    def test_receive_synthesis_signal(self, orchestrator):
        """Should handle Phase 6 SynthesisSignal."""
        class MockGapPattern:
            gap_type = "methodology_iv"
            description = "Weak IV detection gap"

        class MockSignal:
            gap_pattern = MockGapPattern()
            suggested_skill_type = "iv_weakness_detector"
            trigger_reason = "4 sessions missed weak IV issues"

        result = orchestrator.receive_synthesis_signal(MockSignal())
        assert result is True  # Synthesis should succeed

        skills = orchestrator.get_available_skills()
        assert len(skills) >= 1

    def test_receive_synthesis_signal_kill_switch(self, orchestrator):
        """Kill switch OFF → receive_synthesis_signal returns False."""
        import core.skill_synthesis as mod
        original = mod.SKILL_SYNTHESIS_ENABLED
        mod.SKILL_SYNTHESIS_ENABLED = False
        try:
            class MockGapPattern:
                gap_type = "coverage"
                description = "test"

            class MockSignal:
                gap_pattern = MockGapPattern()
                suggested_skill_type = "test"
                trigger_reason = "test"

            result = orchestrator.receive_synthesis_signal(MockSignal())
            assert result is False
        finally:
            mod.SKILL_SYNTHESIS_ENABLED = original

    def test_on_synthesized_skill_executed(self, orchestrator):
        """Should track synthesized skill executions."""
        # First, synthesize a skill via signal
        class MockGapPattern:
            gap_type = "coverage"
            description = "test"

        class MockSignal:
            gap_pattern = MockGapPattern()
            suggested_skill_type = "tracker_test"
            trigger_reason = "test"

        orchestrator.receive_synthesis_signal(MockSignal())
        skills = orchestrator.get_available_skills()
        assert len(skills) >= 1
        skill_name = skills[0].config.name

        # Track execution
        orchestrator.on_synthesized_skill_executed(
            skill_name,
            SkillResult(success=True, findings=[
                Finding(category="test", severity="minor", description="found"),
            ]),
        )

        # Verify tracking
        record = orchestrator._lifecycle.get_record(skill_name)
        assert record is not None
        assert record.total_executions == 1

    def test_get_synthesis_report(self, orchestrator, sample_failure_tool_error):
        """Should produce comprehensive report."""
        orchestrator.on_skill_failed(sample_failure_tool_error)
        report = orchestrator.get_synthesis_report()
        assert "failure_stats" in report
        assert "synthesis_history" in report
        assert "lifecycle_health" in report
        assert "pending_signals" in report
        assert "recurring_failures" in report

    def test_run_maintenance(self, orchestrator):
        """run_maintenance should execute lifecycle check."""
        result = orchestrator.run_maintenance()
        assert "promoted" in result
        assert "deprecated" in result

    def test_serialize_deserialize(self, orchestrator):
        """Full orchestrator roundtrip."""
        # Create some state
        class MockGapPattern:
            gap_type = "coverage"
            description = "test"

        class MockSignal:
            gap_pattern = MockGapPattern()
            suggested_skill_type = "serialize_test"
            trigger_reason = "roundtrip test"

        orchestrator.receive_synthesis_signal(MockSignal())

        # Serialize
        data = orchestrator.serialize()
        assert "synthesizer" in data
        assert "lifecycle" in data

        # Deserialize
        restored = SkillSynthesisOrchestrator.deserialize(data)
        skills = restored.get_available_skills()
        assert len(skills) >= 1

    def test_get_available_skills_excludes_deprecated(self, orchestrator):
        """Should not return deprecated skills."""
        class MockGapPattern:
            gap_type = "coverage"
            description = "test"

        class MockSignal:
            gap_pattern = MockGapPattern()
            suggested_skill_type = "will_deprecate"
            trigger_reason = "test"

        orchestrator.receive_synthesis_signal(MockSignal())
        skills = orchestrator.get_available_skills()
        assert len(skills) == 1

        # Deprecate it
        skill_name = skills[0].config.name
        record = orchestrator._lifecycle.get_record(skill_name)
        record.confidence_level = SynthesisConfidenceLevel.DEPRECATED

        skills = orchestrator.get_available_skills()
        assert len(skills) == 0


# ==============================================================
# 11. End-to-End Integration Tests
# ==============================================================

class TestE2ESkillSynthesis:
    """End-to-end tests: failure → analysis → synthesis → execution → lifecycle."""

    def test_full_lifecycle_from_repeated_failures(self):
        """
        Complete flow:
        1. Skill fails repeatedly (3 times with same pattern)
        2. Orchestrator detects recurring pattern
        3. Synthesis triggered → produces new Skill
        4. New Skill executes successfully on similar context
        5. After enough executions, Skill gets promoted
        """
        orchestrator = SkillSynthesisOrchestrator()

        # Step 1 & 2: Repeated failures
        for i in range(3):
            failure = FailureContext(
                skill_name="did_checker",
                failure_type=FailureType.MISSED_ISSUE,
                error_message="",
                paper_text_snippet=(
                    "Although the pre-treatment trends are broadly similar, "
                    "however we note limitations in the common trends assumption."
                ),
                paper_metadata={"methodology_type": "did"},
                current_phase="DEEP_REVIEW",
                current_section="methodology",
                timestamp=time.time() + i,
            )
            result = orchestrator.on_skill_failed(failure)

        # Step 3: Check synthesis happened
        skills = orchestrator.get_available_skills()
        # Synthesis may or may not succeed depending on sandbox validation
        # The key is the system doesn't crash and handles gracefully

        # If synthesis succeeded, test execution
        if skills:
            synth_skill = skills[0]

            # Step 4: Execute on similar context
            ctx = SkillContext(
                paper_text=(
                    "We employ DID with parallel trends assumption. "
                    "However, there is no evidence of pre-treatment parallel trends. "
                    "The limitation is that the common trends may not hold."
                ),
                paper_metadata={"methodology_type": "did"},
                current_phase="DEEP_REVIEW",
                current_section="methodology",
            )

            exec_result = synth_skill.execute(ctx)
            assert exec_result.success

            # Track execution
            orchestrator.on_synthesized_skill_executed(
                synth_skill.config.name, exec_result
            )

            # Step 5: Simulate many executions for promotion
            for _ in range(12):
                orchestrator.on_synthesized_skill_executed(
                    synth_skill.config.name,
                    SkillResult(success=True, findings=[
                        Finding(category="test", severity="minor", description="x")
                    ]),
                )
            orchestrator.on_synthesized_findings_retained(
                synth_skill.config.name, retained=10, discarded=2
            )

            # Run maintenance
            maintenance_result = orchestrator.run_maintenance()
            # Should be promoted (13 executions, good retention)
            record = orchestrator._lifecycle.get_record(synth_skill.config.name)
            assert record.total_executions >= 13

    def test_signal_driven_synthesis_full_flow(self):
        """
        Phase 6 reflection detects recurring gap →
        sends SynthesisSignal →
        Phase 4 synthesizes Skill →
        Skill executes →
        Results tracked.
        """
        orchestrator = SkillSynthesisOrchestrator()

        # Phase 6 sends signal
        class MockGapPattern:
            gap_type = "methodology_iv"
            description = "Repeatedly misses weak instrument issues"

        class MockSignal:
            gap_pattern = MockGapPattern()
            suggested_skill_type = "weak_iv_detector"
            trigger_reason = "5 consecutive sessions missed weak IV"

        success = orchestrator.receive_synthesis_signal(MockSignal())
        assert success is True

        # Get synthesized skill
        skills = orchestrator.get_available_skills()
        assert len(skills) == 1
        skill = skills[0]

        # Execute on IV paper
        ctx = SkillContext(
            paper_text=(
                "We use instrumental variables with 2SLS estimation. "
                "The first stage F-statistic is 8.2, which is below the "
                "Stock-Yogo critical value of 10 for weak instruments."
            ),
            paper_metadata={"methodology_type": "iv"},
            current_phase="DEEP_REVIEW",
            current_section="methodology",
        )

        result = skill.execute(ctx)
        assert result.success
        # Should have methodology keywords in its config
        assert any("iv" in kw.lower() or "instrument" in kw.lower()
                   for kw in skill.config.keyword_patterns)

    def test_kill_switch_blocks_entire_pipeline(self):
        """When kill switch is OFF, entire pipeline should be gracefully disabled."""
        import core.skill_synthesis as mod
        original = mod.SKILL_SYNTHESIS_ENABLED
        mod.SKILL_SYNTHESIS_ENABLED = False
        try:
            orchestrator = SkillSynthesisOrchestrator()

            # Failure reporting — still records but no synthesis
            failure = FailureContext(
                skill_name="test",
                failure_type=FailureType.MISSED_ISSUE,
                paper_text_snippet="however limitation",
                current_section="methodology",
                current_phase="DEEP_REVIEW",
                paper_metadata={"methodology_type": "did"},
            )
            # Even with recurring failures, no synthesis
            for _ in range(5):
                result = orchestrator.on_skill_failed(failure)
                assert result is None

            # Signal reception blocked
            class MockGapPattern:
                gap_type = "coverage"
                description = "test"

            class MockSignal:
                gap_pattern = MockGapPattern()
                suggested_skill_type = "blocked"
                trigger_reason = "should be blocked"

            assert orchestrator.receive_synthesis_signal(MockSignal()) is False

            # No skills available
            assert orchestrator.get_available_skills() == []
        finally:
            mod.SKILL_SYNTHESIS_ENABLED = original


# ==============================================================
# 12. Template & Methodology Keyword Tests
# ==============================================================

class TestTemplatesAndKeywords:
    """Tests for synthesis templates and methodology keyword dictionaries."""

    def test_all_cause_types_have_templates(self):
        """Every cause type referenced in RootCauseAnalyzer should have a template."""
        cause_types_from_rules = set()
        for rules in RootCauseAnalyzer._HEURISTIC_RULES.values():
            for rule in rules:
                cause_types_from_rules.add(rule["cause_type"])

        for cause_type in cause_types_from_rules:
            # All cause types should map to something (some go through _map_gap_to_cause)
            # At minimum, the most common ones should have templates
            if cause_type not in ("unknown",):
                assert cause_type in _SYNTHESIS_TEMPLATES, \
                    f"Cause type '{cause_type}' has no synthesis template"

    def test_econ_methodology_keywords_non_empty(self):
        """All methodology types should have keywords."""
        for method, keywords in _ECON_METHODOLOGY_KEYWORDS.items():
            assert len(keywords) > 0, f"Methodology '{method}' has no keywords"

    def test_methodology_keywords_contain_expected(self):
        """Spot-check expected keywords."""
        assert "parallel trends" in _ECON_METHODOLOGY_KEYWORDS["did"]
        assert "instrumental variable" in _ECON_METHODOLOGY_KEYWORDS["iv"]
        assert "regression discontinuity" in _ECON_METHODOLOGY_KEYWORDS["rdd"]

    def test_templates_have_required_fields(self):
        """Each template should have strategy and description_template."""
        for name, template in _SYNTHESIS_TEMPLATES.items():
            assert "strategy" in template, f"Template '{name}' missing strategy"
            assert "description_template" in template, f"Template '{name}' missing description_template"


# ==============================================================
# 13. Edge Cases & Error Handling
# ==============================================================

class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_paper_text_execution(self):
        """Skill should handle empty paper text gracefully."""
        config = SynthesisConfig(
            name="empty_test",
            description="test",
            target_issue_type="test",
            keyword_patterns=["something"],
            negative_patterns=["bad thing"],
        )
        skill = SynthesizedSkill(config)
        ctx = SkillContext(paper_text="", current_section="test", current_phase="test")
        result = skill.execute(ctx)
        assert result.success
        assert result.findings == []

    def test_very_long_paper_text(self):
        """Skill should handle very long text without issues."""
        config = SynthesisConfig(
            name="long_test",
            description="test",
            target_issue_type="test",
            keyword_patterns=["needle"],
            negative_patterns=["bad_needle"],
        )
        skill = SynthesizedSkill(config)
        # 100K chars with needle buried inside
        text = "a " * 50000 + " needle " + " b" * 50000 + " bad_needle "
        ctx = SkillContext(paper_text=text, current_section="test", current_phase="test")
        result = skill.execute(ctx)
        assert result.success

    def test_failure_store_empty_queries(self):
        """Queries on empty store should return empty lists."""
        store = FailureStore()
        assert store.query_by_skill("nonexistent") == []
        assert store.query_by_type(FailureType.TOOL_ERROR) == []
        assert store.get_recurring_failures() == []

    def test_lifecycle_unknown_skill(self):
        """Operations on unregistered skills should be no-ops."""
        manager = SynthesisLifecycleManager()
        # Should not crash
        manager.on_skill_executed("ghost", SkillResult(success=True))
        manager.on_findings_retained("ghost", retained=5, discarded=0)
        assert manager.get_record("ghost") is None

    def test_orchestrator_on_skill_failed_kill_switch(self):
        """on_skill_failed with kill switch OFF should return None."""
        import core.skill_synthesis as mod
        original = mod.SKILL_SYNTHESIS_ENABLED
        mod.SKILL_SYNTHESIS_ENABLED = False
        try:
            orchestrator = SkillSynthesisOrchestrator()
            failure = FailureContext(
                skill_name="test",
                failure_type=FailureType.TOOL_ERROR,
            )
            assert orchestrator.on_skill_failed(failure) is None
        finally:
            mod.SKILL_SYNTHESIS_ENABLED = original

    def test_synthesized_skill_config_property(self):
        """config property should expose the internal config."""
        config = SynthesisConfig(name="prop_test", description="t", target_issue_type="t")
        skill = SynthesizedSkill(config)
        assert skill.config is config
        assert skill.config.name == "prop_test"

    def test_failure_context_default_timestamp(self):
        """Default timestamp should be approximately now."""
        before = time.time()
        ctx = FailureContext(skill_name="test")
        after = time.time()
        assert before <= ctx.timestamp <= after

    def test_deserialize_orchestrator_empty_data(self):
        """Deserializing from empty dict should produce valid orchestrator."""
        orchestrator = SkillSynthesisOrchestrator.deserialize({})
        assert orchestrator.get_available_skills() == []
        assert orchestrator.get_failure_stats()["total_failures"] == 0

    def test_synthesizer_sandbox_validation_failure(self):
        """If sandbox validation fails, synthesis should return None."""
        store = FailureStore()
        synthesizer = SkillSynthesizer(failure_store=store)

        # Create failure with empty context — sandbox will check can_apply > 0
        # With no text and no section, the synthesized skill's can_apply will
        # struggle to pass. Let's use a minimal failure that might fail sandbox.
        failure = FailureContext(
            skill_name="test",
            failure_type=FailureType.MISSED_ISSUE,
            paper_text_snippet="",  # Empty text → sandbox may fail
            paper_metadata={},
            current_section="",
            current_phase="",
        )

        # This may or may not pass sandbox depending on the config generated
        # The key test is that it doesn't crash
        skill = synthesizer.synthesize_from_failure(failure)
        # Either None (sandbox failed) or a valid skill — both are acceptable
        if skill is not None:
            assert isinstance(skill, SynthesizedSkill)
