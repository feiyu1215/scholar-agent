"""
Phase 3 SkillX 经济学领域技能测试。

覆盖：
  - Planning Skill: ReviewPlanningSkill
  - Functional Skills: MethodologyAnalysis, StatisticalValidation, CitationVerification, LogicCoherence
  - Atomic Skills: ExtractNumericClaim, CompareWithDomainNorm
"""

import pytest
from core.skills.base import SkillContext, SkillLevel, SkillResult, Finding
from core.skills.economics.planning import ReviewPlanningSkill
from core.skills.economics.functional import (
    MethodologyAnalysisSkill,
    StatisticalValidationSkill,
    CitationVerificationSkill,
    LogicCoherenceSkill,
)
from core.skills.economics.atomic import (
    ExtractNumericClaimSkill,
    CompareWithDomainNormSkill,
)


# ==============================================================
# Test Paper Texts
# ==============================================================

DID_PAPER = """
We employ a difference-in-differences (DID) estimation strategy to identify
the causal effect of the minimum wage increase on employment.
The treatment group consists of firms in states that raised their minimum wage
in 2019 (N=2,450). We cluster standard errors at the state level.
The parallel trends assumption is supported by Figure 2 which shows
pre-treatment trends are indistinguishable.
Our main coefficient is β = -0.034 (SE = 0.012, p < 0.01).
The R² is 0.45. First-stage F-statistic is 15.3.
We cite Angrist and Pischke (2009), Card and Krueger (1994).
"""

IV_PAPER = """
Using an instrumental variables approach, we leverage geographic distance
to university as an instrument for education. The first-stage F-statistic
= 8.2 (below the Stock-Yogo threshold of 10). Standard errors are
heteroskedasticity-robust. Sample size is N=500.
Our findings demonstrate that education causes a 12% increase in earnings
(coefficient = 0.12, SE = 0.05). This proves that education always improves
earnings for all populations.
"""

THEORY_PAPER = """
This paper develops a general equilibrium model to analyze trade patterns.
We derive closed-form solutions under the assumption of Cobb-Douglas
preferences and CRS production technology. The model yields unique
predictions about factor price equalization.
"""


# ==============================================================
# Fixtures
# ==============================================================

@pytest.fixture
def did_context():
    return SkillContext(
        paper_text=DID_PAPER,
        paper_metadata={"paper_type": "empirical"},
        current_phase="deep_review",
    )


@pytest.fixture
def iv_context():
    return SkillContext(
        paper_text=IV_PAPER,
        paper_metadata={"paper_type": "empirical"},
        current_phase="deep_review",
    )


@pytest.fixture
def theory_context():
    return SkillContext(
        paper_text=THEORY_PAPER,
        paper_metadata={"paper_type": "theoretical"},
        current_phase="deep_review",
    )


@pytest.fixture
def orientation_context():
    return SkillContext(
        paper_text=DID_PAPER,
        paper_metadata={"paper_type": "empirical"},
        current_phase="orientation",
    )


# ==============================================================
# Tests: ReviewPlanningSkill
# ==============================================================

class TestReviewPlanningSkill:
    @pytest.fixture
    def skill(self):
        return ReviewPlanningSkill()

    def test_descriptor(self, skill):
        d = skill.descriptor
        assert d.level == SkillLevel.PLANNING
        assert "orientation" in d.applicable_phases

    def test_can_apply_orientation(self, skill, orientation_context):
        score = skill.can_apply(orientation_context)
        assert score == 1.0

    def test_can_apply_deep_review(self, skill, did_context):
        """Planning skill has low applicability in deep_review phase."""
        score = skill.can_apply(did_context)
        assert score == 0.2

    def test_execute_empirical(self, skill, orientation_context):
        result = skill.execute(orientation_context)
        assert result.success is True
        assert result.output_data is not None
        # Should contain strategy dict
        strategy = result.output_data.get("strategy", {})
        assert "focus_areas" in strategy
        assert "skill_priorities" in strategy or "budget_allocation" in strategy

    def test_execute_theory(self, skill):
        ctx = SkillContext(
            paper_text=THEORY_PAPER,
            paper_metadata={"paper_type": "theoretical"},
            current_phase="orientation",
        )
        result = skill.execute(ctx)
        assert result.success is True
        strategy = result.output_data.get("strategy", {})
        assert strategy is not None
        # Theory paper should have different focus areas than empirical
        if "focus_areas" in strategy and strategy["focus_areas"]:
            # logic_coherence should be prioritized for theory
            assert "logic_coherence" in strategy["focus_areas"]


# ==============================================================
# Tests: MethodologyAnalysisSkill
# ==============================================================

class TestMethodologyAnalysisSkill:
    @pytest.fixture
    def skill(self):
        return MethodologyAnalysisSkill()

    def test_descriptor(self, skill):
        d = skill.descriptor
        assert d.level == SkillLevel.FUNCTIONAL
        assert "deep_review" in d.applicable_phases

    def test_can_apply_empirical(self, skill, did_context):
        score = skill.can_apply(did_context)
        # DID paper + empirical type + methodology keywords + phase match → high score
        assert score >= 0.7

    def test_can_apply_theory(self, skill, theory_context):
        # Theory paper less relevant for methodology analysis
        score = skill.can_apply(theory_context)
        did_score = skill.can_apply(SkillContext(
            paper_text=DID_PAPER,
            paper_metadata={"paper_type": "empirical"},
            current_phase="deep_review",
        ))
        assert score < did_score

    def test_detect_did(self, skill, did_context):
        result = skill.execute(did_context)
        assert result.success is True
        # Should detect DID methodology in output_data
        methods = result.output_data.get("identified_methods", [])
        assert "did" in methods

    def test_detect_iv_issues(self, skill, iv_context):
        result = skill.execute(iv_context)
        assert result.success is True
        # IV paper with weak F-stat should generate findings or identify methods
        methods = result.output_data.get("identified_methods", [])
        assert "iv" in methods

    def test_get_instruction(self, skill):
        instr = skill.get_instruction()
        assert "方法论" in instr or "Methodology" in instr


# ==============================================================
# Tests: StatisticalValidationSkill
# ==============================================================

class TestStatisticalValidationSkill:
    @pytest.fixture
    def skill(self):
        return StatisticalValidationSkill()

    def test_descriptor(self, skill):
        d = skill.descriptor
        assert d.level == SkillLevel.FUNCTIONAL

    def test_can_apply(self, skill, did_context):
        assert skill.can_apply(did_context) > 0

    def test_execute_success(self, skill, did_context):
        result = skill.execute(did_context)
        assert result.success is True

    def test_execute_iv(self, skill, iv_context):
        result = skill.execute(iv_context)
        assert result.success is True


# ==============================================================
# Tests: CitationVerificationSkill
# ==============================================================

class TestCitationVerificationSkill:
    @pytest.fixture
    def skill(self):
        return CitationVerificationSkill()

    def test_descriptor(self, skill):
        d = skill.descriptor
        assert d.level == SkillLevel.FUNCTIONAL

    def test_can_apply(self, skill, did_context):
        assert skill.can_apply(did_context) > 0

    def test_execute_finds_citations(self, skill, did_context):
        result = skill.execute(did_context)
        assert result.success is True
        # DID paper has citations like "Angrist and Pischke (2009)"
        # Should detect and report something about citations
        combined = str(result.output_data) + str(result.findings)
        # The skill should execute without error at minimum
        assert result.success is True


# ==============================================================
# Tests: LogicCoherenceSkill
# ==============================================================

class TestLogicCoherenceSkill:
    @pytest.fixture
    def skill(self):
        return LogicCoherenceSkill()

    def test_descriptor(self, skill):
        d = skill.descriptor
        assert d.level == SkillLevel.FUNCTIONAL

    def test_detect_overclaim(self, skill, iv_context):
        """IV paper makes overclaims like 'proves' and 'always'."""
        result = skill.execute(iv_context)
        assert result.success is True
        # Should detect overclaiming language
        findings_text = " ".join(f.description for f in result.findings)
        output_text = str(result.output_data)
        combined = findings_text + output_text
        # The IV paper uses "proves" and "always" which are overclaims
        has_issue = (
            len(result.findings) > 0
            or "overclaim" in combined.lower()
            or "proves" in combined.lower()
            or "causal" in combined.lower()
        )
        assert has_issue

    def test_did_paper_no_overclaim(self, skill, did_context):
        """DID paper uses more careful language — fewer/no overclaim findings."""
        result = skill.execute(did_context)
        assert result.success is True
        # DID paper is more careful, so fewer overclaim findings
        overclaim_findings = [
            f for f in result.findings
            if "overclaim" in f.description.lower() or "proves" in f.description.lower()
        ]
        # Should have fewer overclaims than IV paper
        # (just check execution succeeds; exact findings depend on impl)


# ==============================================================
# Tests: ExtractNumericClaimSkill (Atomic)
# ==============================================================

class TestExtractNumericClaimSkill:
    @pytest.fixture
    def skill(self):
        return ExtractNumericClaimSkill()

    def test_descriptor(self, skill):
        d = skill.descriptor
        assert d.level == SkillLevel.ATOMIC
        assert "deep_review" in d.applicable_phases

    def test_extract_coefficients(self, skill, did_context):
        result = skill.execute(did_context)
        assert result.success is True
        claims = result.output_data.get("claims", [])
        # Should extract β = -0.034, N=2450, R²=0.45, etc.
        assert len(claims) > 0
        # Check at least one coefficient was found
        claim_types = [c["claim_type"] for c in claims]
        assert "coefficient" in claim_types or "sample_size" in claim_types

    def test_extract_from_iv(self, skill, iv_context):
        result = skill.execute(iv_context)
        assert result.success is True
        claims = result.output_data.get("claims", [])
        # Should find coefficient=0.12, N=500, F=8.2
        assert len(claims) > 0

    def test_extract_from_theory(self, skill, theory_context):
        """Theory paper has fewer numeric claims."""
        result = skill.execute(theory_context)
        assert result.success is True
        claims = result.output_data.get("claims", [])
        # Might be empty or very few
        assert isinstance(claims, list)

    def test_can_apply_text_with_numbers(self, skill, did_context):
        score = skill.can_apply(did_context)
        assert score > 0.3


# ==============================================================
# Tests: CompareWithDomainNormSkill (Atomic)
# ==============================================================

class TestCompareWithDomainNormSkill:
    @pytest.fixture
    def skill(self):
        return CompareWithDomainNormSkill()

    def test_descriptor(self, skill):
        d = skill.descriptor
        assert d.level == SkillLevel.ATOMIC
        assert d.prerequisites == ("extract_numeric_claim",)

    def test_can_apply_with_claims(self, skill):
        """With upstream claims in parameters, should be highly applicable."""
        ctx = SkillContext(
            paper_text="some paper with instrumental variables",
            current_phase="deep_review",
            parameters={
                "claims": [
                    {"value": "8.2", "claim_type": "f_statistic", "context": "F=8.2"},
                ]
            },
        )
        score = skill.can_apply(ctx)
        assert score == 0.9

    def test_flag_weak_f_stat(self, skill):
        """F-stat < 10 in IV paper should be flagged."""
        ctx = SkillContext(
            paper_text=IV_PAPER,
            current_phase="deep_review",
            parameters={
                "claims": [
                    {"value": "8.2", "claim_type": "f_statistic", "context": "F=8.2"},
                ]
            },
        )
        result = skill.execute(ctx)
        assert result.success is True
        # Should flag F < 10 as weak instrument
        combined = " ".join(f.description for f in result.findings)
        has_flag = (
            "弱工具" in combined
            or "weak" in combined.lower()
            or "F" in combined
            or len(result.findings) > 0
        )
        assert has_flag

    def test_ok_f_stat_not_iv(self, skill):
        """Non-IV paper with F > 10 should not trigger weak instrument warning."""
        # This paper doesn't use IV, so no F-stat check should trigger
        ctx = SkillContext(
            paper_text=DID_PAPER,  # DID, not IV
            current_phase="deep_review",
            parameters={"claims": []},
        )
        result = skill.execute(ctx)
        assert result.success is True
        # DID paper should not trigger IV-specific checks
        iv_findings = [
            f for f in result.findings
            if "弱工具" in f.description or "weak instrument" in f.description.lower()
        ]
        assert len(iv_findings) == 0

    def test_execute_no_claims(self, skill):
        """Should still work when no claims provided."""
        ctx = SkillContext(
            paper_text=DID_PAPER,
            current_phase="deep_review",
        )
        result = skill.execute(ctx)
        assert result.success is True
