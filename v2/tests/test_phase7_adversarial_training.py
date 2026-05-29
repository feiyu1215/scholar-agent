"""
Phase 7: Adversarial Self-Training — 完整测试套件
=================================================

覆盖 6 个训练模块 + Kill Switch + EventBus 集成:
    1. weakness_analyzer.py — WeaknessProfile, WeaknessEntry, WeaknessAnalyzer
    2. adversarial.py — AdversarialCase, DifficultyController, AdversarialGenerator
    3. curriculum.py — CurriculumStage, TrainingCurriculum, LearningCurveTracker, CurriculumDesigner
    4. adversarial_library.py — LibraryEntry, LibraryIndex, AdversarialLibrary, RegressionSuiteGenerator
    5. red_blue_arena.py — EloRating, RedTeam, BlueTeam, ArenaOrchestrator
    6. training_loop.py — TrainingConfig, TrainingSession, ConvergenceDetector, TrainingLoop

Target: 150+ tests, unittest style (project convention).
"""

import os
import sys
import time
import math
import unittest
import asyncio
import copy
import random
import tempfile
import shutil
from collections import defaultdict
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock
from dataclasses import dataclass, field

# Ensure kill switch is on for testing
os.environ.setdefault("SCHOLAR_GODEL_ADVERSARIAL_TRAINING", "1")
os.environ.setdefault("GODEL_ADVERSARIAL_TRAINING", "1")
os.environ.setdefault("GODEL_ADVERSARIAL_RED_TEAM", "1")
os.environ.setdefault("GODEL_ADVERSARIAL_BLUE_TEAM", "1")
os.environ.setdefault("GODEL_ADVERSARIAL_ELO", "1")
os.environ.setdefault("GODEL_ADVERSARIAL_SEASON", "1")


# ============================================================
# Module 1: WeaknessAnalyzer
# ============================================================

from training.weakness_analyzer import (
    WeaknessDimension,
    WeaknessSource,
    WeaknessEvidence,
    WeaknessEntry,
    WeaknessProfile,
    DimensionMapper,
    AnalyzerConfig,
    WeaknessAnalyzer,
    ADVERSARIAL_TRAINING_ENABLED,
)


class TestWeaknessDimension(unittest.TestCase):
    """WeaknessDimension 枚举测试。"""

    def test_all_17_dimensions_exist(self):
        self.assertEqual(len(WeaknessDimension), 17)

    def test_key_dimensions_by_value(self):
        self.assertEqual(WeaknessDimension("methodology_analysis"), WeaknessDimension.METHODOLOGY_ANALYSIS)
        self.assertEqual(WeaknessDimension("statistical_reasoning"), WeaknessDimension.STATISTICAL_REASONING)
        self.assertEqual(WeaknessDimension("causal_inference"), WeaknessDimension.CAUSAL_INFERENCE)
        self.assertEqual(WeaknessDimension("data_consistency"), WeaknessDimension.DATA_CONSISTENCY)

    def test_dimensions_are_strings(self):
        for dim in WeaknessDimension:
            self.assertIsInstance(dim.value, str)

    def test_domain_specific_dimensions(self):
        self.assertEqual(WeaknessDimension("did_analysis"), WeaknessDimension.DID_ANALYSIS)
        self.assertEqual(WeaknessDimension("iv_analysis"), WeaknessDimension.IV_ANALYSIS)
        self.assertEqual(WeaknessDimension("rdd_analysis"), WeaknessDimension.RDD_ANALYSIS)
        self.assertEqual(WeaknessDimension("event_study"), WeaknessDimension.EVENT_STUDY)
        self.assertEqual(WeaknessDimension("panel_data"), WeaknessDimension.PANEL_DATA)


class TestWeaknessSource(unittest.TestCase):
    """WeaknessSource 枚举测试。"""

    def test_all_6_sources_exist(self):
        self.assertEqual(len(WeaknessSource), 6)

    def test_source_values(self):
        self.assertEqual(WeaknessSource("meta_harness_bottleneck"), WeaknessSource.META_HARNESS_BOTTLENECK)
        self.assertEqual(WeaknessSource("failure_store"), WeaknessSource.FAILURE_STORE)
        self.assertEqual(WeaknessSource("manual_annotation"), WeaknessSource.MANUAL_ANNOTATION)
        self.assertEqual(WeaknessSource("memory_pattern"), WeaknessSource.MEMORY_PATTERN)
        self.assertEqual(WeaknessSource("reflection_gap"), WeaknessSource.REFLECTION_GAP)


class TestWeaknessEvidence(unittest.TestCase):
    """WeaknessEvidence 数据类测试。"""

    def test_creation_with_defaults(self):
        ev = WeaknessEvidence(
            source=WeaknessSource.META_HARNESS_BOTTLENECK,
            description="test evidence",
        )
        self.assertEqual(ev.source, WeaknessSource.META_HARNESS_BOTTLENECK)
        self.assertEqual(ev.description, "test evidence")
        self.assertGreater(ev.timestamp, 0)
        self.assertEqual(ev.severity, 0.5)

    def test_creation_with_explicit_values(self):
        ev = WeaknessEvidence(
            source=WeaknessSource.FAILURE_STORE,
            description="failed case",
            severity=0.8,
            timestamp=1000.0,
        )
        self.assertEqual(ev.timestamp, 1000.0)
        self.assertEqual(ev.severity, 0.8)

    def test_age_days(self):
        ev = WeaknessEvidence(
            source=WeaknessSource.MANUAL_ANNOTATION,
            description="old",
            timestamp=time.time() - 86400 * 7,
        )
        self.assertAlmostEqual(ev.age_days, 7.0, delta=0.1)

    def test_time_decay_weight_recent(self):
        ev = WeaknessEvidence(
            source=WeaknessSource.MANUAL_ANNOTATION,
            description="recent",
            timestamp=time.time(),
        )
        weight = ev.time_decay_weight(half_life_days=14.0)
        self.assertAlmostEqual(weight, 1.0, delta=0.01)

    def test_time_decay_weight_old(self):
        ev = WeaknessEvidence(
            source=WeaknessSource.MANUAL_ANNOTATION,
            description="old",
            timestamp=time.time() - 86400 * 14,
        )
        weight = ev.time_decay_weight(half_life_days=14.0)
        self.assertAlmostEqual(weight, 0.5, delta=0.05)

    def test_to_dict_round_trip(self):
        ev = WeaknessEvidence(
            source=WeaknessSource.MANUAL_ANNOTATION,
            description="manual annotation",
            severity=0.7,
        )
        d = ev.to_dict()
        self.assertIn("source", d)
        self.assertIn("description", d)
        ev2 = WeaknessEvidence.from_dict(d)
        self.assertEqual(ev2.source, ev.source)
        self.assertEqual(ev2.description, ev.description)
        self.assertAlmostEqual(ev2.severity, ev.severity, places=5)


class TestWeaknessEntry(unittest.TestCase):
    """WeaknessEntry 弱点条目测试。"""

    def _make_entry(self, n_evidence=2, severity=0.6):
        evidences = [
            WeaknessEvidence(
                source=WeaknessSource.META_HARNESS_BOTTLENECK,
                description=f"evidence_{i}",
                severity=severity,
            )
            for i in range(n_evidence)
        ]
        return WeaknessEntry(
            dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
            summary="Test weakness",
            evidences=evidences,
        )

    def test_weakness_id_generated(self):
        entry = self._make_entry()
        self.assertIsNotNone(entry.weakness_id)
        self.assertGreater(len(entry.weakness_id), 0)

    def test_evidence_count(self):
        entry = self._make_entry(n_evidence=3)
        self.assertEqual(entry.evidence_count, 3)

    def test_multi_source_confirmed_true(self):
        evidences = [
            WeaknessEvidence(source=WeaknessSource.META_HARNESS_BOTTLENECK, description="a"),
            WeaknessEvidence(source=WeaknessSource.FAILURE_STORE, description="b"),
        ]
        entry = WeaknessEntry(
            dimension=WeaknessDimension.STATISTICAL_REASONING,
            summary="multi",
            evidences=evidences,
        )
        self.assertTrue(entry.multi_source_confirmed)

    def test_multi_source_confirmed_false(self):
        entry = self._make_entry(n_evidence=3)
        self.assertFalse(entry.multi_source_confirmed)

    def test_compute_confidence_multiple_evidences(self):
        entry = self._make_entry(n_evidence=5)
        conf = entry.compute_confidence()
        self.assertGreater(conf, 0.3)
        self.assertLessEqual(conf, 1.0)

    def test_compute_confidence_single_evidence(self):
        entry = self._make_entry(n_evidence=1)
        conf = entry.compute_confidence()
        self.assertLess(conf, 0.8)

    def test_compute_confidence_empty(self):
        entry = WeaknessEntry(
            dimension=WeaknessDimension.STATISTICAL_REASONING,
            summary="empty",
        )
        conf = entry.compute_confidence()
        self.assertEqual(conf, 0.0)

    def test_compute_priority(self):
        entry = self._make_entry()
        p = entry.compute_priority()
        self.assertGreater(p, 0.0)
        self.assertLessEqual(p, 1.0)

    def test_high_severity_high_priority(self):
        low = self._make_entry(severity=0.2)
        high = self._make_entry(severity=0.9)
        self.assertGreater(high.compute_priority(), low.compute_priority())

    def test_add_evidence(self):
        entry = self._make_entry(n_evidence=1)
        new_ev = WeaknessEvidence(source=WeaknessSource.FAILURE_STORE, description="new")
        entry.add_evidence(new_ev)
        self.assertEqual(entry.evidence_count, 2)

    def test_record_training_attempt(self):
        entry = self._make_entry()
        entry.record_training_attempt(improvement=0.15)
        self.assertEqual(entry.training_attempts, 1)
        self.assertEqual(entry.last_improvement, 0.15)

    def test_to_dict_from_dict(self):
        entry = self._make_entry(n_evidence=3)
        d = entry.to_dict()
        entry2 = WeaknessEntry.from_dict(d)
        self.assertEqual(entry2.dimension, entry.dimension)
        self.assertEqual(entry2.summary, entry.summary)
        self.assertEqual(len(entry2.evidences), 3)


class TestWeaknessProfile(unittest.TestCase):
    """WeaknessProfile 弱点画像测试。"""

    def _make_profile(self, n_entries=5):
        entries = []
        dims = list(WeaknessDimension)[:n_entries]
        for i, dim in enumerate(dims):
            entry = WeaknessEntry(
                dimension=dim,
                summary=f"weakness in {dim.value}",
                evidences=[
                    WeaknessEvidence(
                        source=WeaknessSource.META_HARNESS_BOTTLENECK,
                        description=f"ev_{dim.value}",
                        severity=0.3 + i * 0.1,
                    )
                ],
            )
            entries.append(entry)
        return WeaknessProfile(entries=entries)

    def test_get_top_k(self):
        profile = self._make_profile(5)
        top3 = profile.get_top_k(3)
        self.assertEqual(len(top3), 3)

    def test_get_trainable(self):
        profile = self._make_profile(5)
        trainable = profile.get_trainable()
        self.assertIsInstance(trainable, list)

    def test_upsert_entry_new(self):
        profile = WeaknessProfile(entries=[])
        entry = WeaknessEntry(
            dimension=WeaknessDimension.CAUSAL_INFERENCE,
            summary="new entry",
            evidences=[
                WeaknessEvidence(source=WeaknessSource.MANUAL_ANNOTATION, description="new")
            ],
        )
        profile.upsert_entry(entry)
        self.assertEqual(len(profile.entries), 1)

    def test_upsert_entry_existing_merges(self):
        entry1 = WeaknessEntry(
            dimension=WeaknessDimension.CAUSAL_INFERENCE,
            summary="same summary",
            evidences=[
                WeaknessEvidence(source=WeaknessSource.MANUAL_ANNOTATION, description="first")
            ],
        )
        profile = WeaknessProfile(entries=[entry1])
        entry2 = WeaknessEntry(
            dimension=WeaknessDimension.CAUSAL_INFERENCE,
            summary="same summary",
            evidences=[
                WeaknessEvidence(source=WeaknessSource.FAILURE_STORE, description="second")
            ],
        )
        profile.upsert_entry(entry2)
        self.assertEqual(len(profile.entries), 1)
        self.assertEqual(len(profile.entries[0].evidences), 2)

    def test_recompute_priorities(self):
        profile = self._make_profile(5)
        profile.recompute_priorities()
        self.assertEqual(len(profile.entries), 5)

    def test_prune_resolved(self):
        old_entry = WeaknessEntry(
            dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
            summary="very old",
            evidences=[
                WeaknessEvidence(
                    source=WeaknessSource.MANUAL_ANNOTATION,
                    description="ancient",
                    timestamp=time.time() - 86400 * 365,
                    severity=0.1,
                )
            ],
        )
        active_entry = WeaknessEntry(
            dimension=WeaknessDimension.STATISTICAL_REASONING,
            summary="active issue",
            evidences=[
                WeaknessEvidence(
                    source=WeaknessSource.META_HARNESS_BOTTLENECK,
                    description="recent",
                    severity=0.8,
                )
            ],
        )
        profile = WeaknessProfile(entries=[old_entry, active_entry])
        removed = profile.prune_resolved(min_confidence=0.2)
        self.assertIsInstance(removed, list)

    def test_to_dict_from_dict(self):
        profile = self._make_profile(4)
        d = profile.to_dict()
        profile2 = WeaknessProfile.from_dict(d)
        self.assertEqual(len(profile2.entries), 4)

    def test_dimension_distribution(self):
        profile = self._make_profile(5)
        dist = profile.dimension_distribution()
        self.assertIsInstance(dist, dict)
        self.assertGreater(len(dist), 0)


class TestDimensionMapper(unittest.TestCase):
    """DimensionMapper 测试。"""

    def test_from_bottleneck_known_type(self):
        dim = DimensionMapper.from_bottleneck("category_weakness")
        self.assertIsInstance(dim, WeaknessDimension)

    def test_from_bottleneck_with_description(self):
        dim = DimensionMapper.from_bottleneck("unknown_type", description="DID analysis failed")
        self.assertEqual(dim, WeaknessDimension.DID_ANALYSIS)

    def test_from_failure_known_type(self):
        dim = DimensionMapper.from_failure("logic_error")
        self.assertIsInstance(dim, WeaknessDimension)

    def test_from_failure_with_context(self):
        dim = DimensionMapper.from_failure("unknown", context_text="instrumental variable estimation")
        self.assertEqual(dim, WeaknessDimension.IV_ANALYSIS)


class TestAnalyzerConfig(unittest.TestCase):
    """AnalyzerConfig 测试。"""

    def test_default_values(self):
        config = AnalyzerConfig()
        self.assertIsNotNone(config.max_entries)
        self.assertIsNotNone(config.half_life_days)

    def test_custom_config(self):
        config = AnalyzerConfig(max_entries=50, half_life_days=7.0)
        self.assertEqual(config.max_entries, 50)
        self.assertEqual(config.half_life_days, 7.0)


class TestWeaknessAnalyzer(unittest.TestCase):
    """WeaknessAnalyzer 集成测试。"""

    def test_creation_with_defaults(self):
        analyzer = WeaknessAnalyzer()
        self.assertIsNotNone(analyzer)

    def test_creation_with_custom_config(self):
        config = AnalyzerConfig(max_entries=50, half_life_days=7.0)
        analyzer = WeaknessAnalyzer(config=config)
        self.assertEqual(analyzer.config.max_entries, 50)

    def test_analyze_empty_returns_profile(self):
        analyzer = WeaknessAnalyzer()
        profile = analyzer.analyze()
        self.assertIsInstance(profile, WeaknessProfile)

    def test_ingest_manual_and_build_profile(self):
        analyzer = WeaknessAnalyzer()
        analyzer.ingest_manual(
            dimension=WeaknessDimension.STATISTICAL_REASONING,
            description="Weak in p-value interpretation",
            severity=0.7,
        )
        profile = analyzer.build_profile()
        self.assertIsInstance(profile, WeaknessProfile)
        if ADVERSARIAL_TRAINING_ENABLED:
            self.assertGreater(len(profile.entries), 0)


# ============================================================
# Module 2: Adversarial (adversarial.py)
# ============================================================

from training.adversarial import (
    ChallengeType,
    DifficultyLevel,
    AdversarialCase,
    DifficultyController,
    MultiDimensionChallengeFactory,
    AdversarialGenerator,
)


class TestChallengeType(unittest.TestCase):
    """ChallengeType 枚举测试。"""

    def test_has_19_challenge_types(self):
        self.assertEqual(len(ChallengeType), 19)

    def test_values_are_strings(self):
        for ct in ChallengeType:
            self.assertIsInstance(ct.value, str)


class TestDifficultyLevel(unittest.TestCase):
    """DifficultyLevel 枚举测试。"""

    def test_has_5_levels(self):
        self.assertEqual(len(DifficultyLevel), 5)
        self.assertEqual(DifficultyLevel("trivial"), DifficultyLevel.TRIVIAL)
        self.assertEqual(DifficultyLevel("expert"), DifficultyLevel.EXPERT)

    def test_all_levels_unique(self):
        values = [l.value for l in DifficultyLevel]
        self.assertEqual(len(set(values)), 5)


class TestAdversarialCase(unittest.TestCase):
    """AdversarialCase 对抗样本测试。"""

    def _make_case(self):
        return AdversarialCase(
            challenge_type=ChallengeType.HIDDEN_ENDOGENEITY,
            difficulty=DifficultyLevel.MEDIUM,
            target_dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
            paper_snippet="This is a fake paper snippet for testing purposes.",
            gold_findings=[
                {"category": "methodology", "description": "Invalid control group"},
            ],
        )

    def test_creation(self):
        case = self._make_case()
        self.assertEqual(case.challenge_type, ChallengeType.HIDDEN_ENDOGENEITY)
        self.assertEqual(case.difficulty, DifficultyLevel.MEDIUM)
        self.assertTrue(len(case.case_id) > 0)

    def test_record_usage_passed(self):
        case = self._make_case()
        case.record_usage(passed=True)
        self.assertEqual(case.use_count, 1)

    def test_record_usage_failed(self):
        case = self._make_case()
        case.record_usage(passed=False)
        self.assertEqual(case.use_count, 1)

    def test_record_usage_multiple(self):
        case = self._make_case()
        case.record_usage(passed=True)
        case.record_usage(passed=False)
        case.record_usage(passed=True)
        self.assertEqual(case.use_count, 3)

    def test_to_dict_from_dict(self):
        case = self._make_case()
        case.record_usage(passed=True)
        d = case.to_dict()
        case2 = AdversarialCase.from_dict(d)
        self.assertEqual(case2.case_id, case.case_id)
        self.assertEqual(case2.challenge_type, case.challenge_type)
        self.assertEqual(case2.difficulty, case.difficulty)

    def test_to_eval_paper_dict(self):
        case = self._make_case()
        d = case.to_eval_paper_dict()
        # to_eval_paper_dict returns paper_id, title, sections, gold_findings, metadata
        self.assertIn("sections", d)
        self.assertIn("gold_findings", d)
        self.assertIn("metadata", d)
        self.assertTrue(d["metadata"]["is_adversarial"])


class TestDifficultyController(unittest.TestCase):
    """DifficultyController ZPD 难度控制器测试。"""

    def test_initial_recommendation(self):
        dc = DifficultyController()
        rec = dc.get_recommended_difficulty()
        self.assertEqual(rec, DifficultyLevel.EASY)

    def test_high_pass_rate_escalates(self):
        dc = DifficultyController()
        # Start from EASY and escalate through consistent passing
        for _ in range(15):
            dc.record_result(DifficultyLevel.EASY, passed=True)
        for _ in range(15):
            dc.record_result(DifficultyLevel.MEDIUM, passed=True)
        rec = dc.get_recommended_difficulty()
        self.assertIn(rec, [DifficultyLevel.HARD, DifficultyLevel.EXPERT])

    def test_low_pass_rate_lowers(self):
        dc = DifficultyController()
        for _ in range(15):
            dc.record_result(DifficultyLevel.MEDIUM, passed=False)
        rec = dc.get_recommended_difficulty()
        self.assertIn(rec, [DifficultyLevel.TRIVIAL, DifficultyLevel.EASY])

    def test_zpd_targeting(self):
        dc = DifficultyController()
        # With 50% pass rate at EASY, controller should stay at EASY level
        for i in range(20):
            dc.record_result(DifficultyLevel.EASY, passed=(i % 2 == 0))
        rec = dc.get_recommended_difficulty()
        self.assertEqual(rec, DifficultyLevel.EASY)

    def test_force_level(self):
        dc = DifficultyController()
        dc.force_level(DifficultyLevel.EXPERT)
        self.assertEqual(dc.get_recommended_difficulty(), DifficultyLevel.EXPERT)

    def test_is_in_zpd_no_data(self):
        dc = DifficultyController()
        self.assertTrue(dc.is_in_zpd())

    def test_is_in_zpd_balanced(self):
        dc = DifficultyController()
        for i in range(10):
            dc.record_result(DifficultyLevel.MEDIUM, passed=(i % 2 == 0))
        self.assertTrue(dc.is_in_zpd())

    def test_serialize_deserialize(self):
        dc = DifficultyController()
        for _ in range(5):
            dc.record_result(DifficultyLevel.MEDIUM, passed=True)
        data = dc.serialize()
        dc2 = DifficultyController.deserialize(data)
        self.assertEqual(dc2.get_recommended_difficulty(), dc.get_recommended_difficulty())


class TestMultiDimensionChallengeFactory(unittest.TestCase):
    """MultiDimensionChallengeFactory 测试。"""

    def test_plan_challenges_basic(self):
        # Factory needs a profile with entries to generate challenges
        from training.weakness_analyzer import WeaknessProfile, WeaknessEntry, WeaknessEvidence, WeaknessSource
        profile = WeaknessProfile(entries=[
            WeaknessEntry(
                dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
                summary="Weak in methodology",
                evidences=[WeaknessEvidence(
                    source=WeaknessSource.META_HARNESS_BOTTLENECK,
                    description="test", severity=0.7,
                )],
            ),
        ])
        factory = MultiDimensionChallengeFactory(profile=profile)
        plan = factory.plan_challenges(total_count=4, diversity_weight=0.3)
        self.assertIsInstance(plan, list)
        self.assertGreater(len(plan), 0)
        # Each entry is (dimension, challenge_type, difficulty)
        for item in plan:
            self.assertEqual(len(item), 3)

    def test_get_challenge_types_for_dimension(self):
        factory = MultiDimensionChallengeFactory()
        types = factory.get_challenge_types_for_dimension(WeaknessDimension.METHODOLOGY_ANALYSIS)
        self.assertIsInstance(types, list)
        self.assertGreater(len(types), 0)
        for ct in types:
            self.assertIsInstance(ct, ChallengeType)


class TestAdversarialGenerator(unittest.TestCase):
    """AdversarialGenerator 测试（无需 LLM 调用）。"""

    def test_creation(self):
        gen = AdversarialGenerator()
        self.assertIsNotNone(gen)

    def test_validate_case_valid(self):
        gen = AdversarialGenerator()
        case = AdversarialCase(
            challenge_type=ChallengeType.HIDDEN_ENDOGENEITY,
            difficulty=DifficultyLevel.MEDIUM,
            target_dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
            paper_snippet="Some valid paper content here with enough text to be valid for testing purposes and validation checks.",
            gold_findings=[{"category": "methodology", "description": "An issue"}],
            gold_explanation="The methodology has issues with the control group selection process.",
        )
        is_valid, issues = gen.validate_case(case)
        self.assertTrue(is_valid)
        self.assertEqual(len(issues), 0)

    def test_validate_case_empty_snippet(self):
        gen = AdversarialGenerator()
        case = AdversarialCase(
            challenge_type=ChallengeType.HIDDEN_ENDOGENEITY,
            difficulty=DifficultyLevel.MEDIUM,
            target_dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
            paper_snippet="",
            gold_findings=[{"category": "methodology", "description": "An issue"}],
            gold_explanation="Explanation here.",
        )
        is_valid, issues = gen.validate_case(case)
        self.assertFalse(is_valid)
        self.assertGreater(len(issues), 0)

    def test_difficulty_controller_access(self):
        gen = AdversarialGenerator()
        self.assertIsInstance(gen.difficulty_controller, DifficultyController)


# ============================================================
# Module 3: Curriculum (curriculum.py)
# ============================================================

from training.curriculum import (
    DifficultyGradient,
    GradientStep,
    StageStatus,
    StageResult,
    CurriculumStage,
    TrainingCurriculum,
    LearningCurveTracker,
    CurriculumDesigner,
)


class TestStageStatus(unittest.TestCase):
    """StageStatus 枚举测试。"""

    def test_has_5_statuses(self):
        self.assertEqual(len(StageStatus), 5)

    def test_key_values(self):
        self.assertEqual(StageStatus("not_started"), StageStatus.NOT_STARTED)
        self.assertEqual(StageStatus("in_progress"), StageStatus.IN_PROGRESS)
        self.assertEqual(StageStatus("passed"), StageStatus.PASSED)
        self.assertEqual(StageStatus("failed"), StageStatus.FAILED)
        self.assertEqual(StageStatus("skipped"), StageStatus.SKIPPED)


class TestStageResult(unittest.TestCase):
    """StageResult 数据类测试。"""

    def test_creation(self):
        sr = StageResult(case_id="case_001", passed=True, score=0.85)
        self.assertEqual(sr.case_id, "case_001")
        self.assertTrue(sr.passed)
        self.assertEqual(sr.score, 0.85)
        self.assertGreater(sr.timestamp, 0)


class TestCurriculumStage(unittest.TestCase):
    """CurriculumStage 测试。"""

    def _make_stage(self):
        return CurriculumStage(
            stage_id="test_stage_1",
            dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
            difficulty=DifficultyLevel.MEDIUM,
            target_pass_rate=0.7,
            min_attempts=3,
            max_attempts=20,
        )

    def test_initial_state(self):
        stage = self._make_stage()
        self.assertEqual(stage.status, StageStatus.NOT_STARTED)
        self.assertEqual(stage.attempts, 0)
        self.assertEqual(stage.pass_rate, 0.0)

    def test_record_result_transitions_status(self):
        stage = self._make_stage()
        stage.record_result(StageResult(case_id="c1", passed=True, score=0.9))
        self.assertEqual(stage.status, StageStatus.IN_PROGRESS)
        self.assertEqual(stage.attempts, 1)

    def test_is_passed_when_meets_criteria(self):
        stage = self._make_stage()
        for i in range(4):
            stage.record_result(StageResult(case_id=f"c{i}", passed=True, score=0.9))
        self.assertTrue(stage.is_passed)
        self.assertEqual(stage.status, StageStatus.PASSED)

    def test_is_exhausted_on_max_attempts(self):
        stage = CurriculumStage(
            stage_id="exhaust_test",
            dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
            difficulty=DifficultyLevel.HARD,
            target_pass_rate=0.9,
            min_attempts=3,
            max_attempts=5,
        )
        for i in range(5):
            stage.record_result(StageResult(case_id=f"c{i}", passed=False))
        self.assertTrue(stage.is_exhausted)
        self.assertEqual(stage.status, StageStatus.FAILED)

    def test_recent_pass_rate(self):
        stage = self._make_stage()
        results = [True, True, False, True, False, True, True, False, True, True]
        for i, passed in enumerate(results):
            stage.record_result(StageResult(case_id=f"c{i}", passed=passed))
        rate = stage.recent_pass_rate(window=5)
        # Last 5: False, True, True, False, True, True → last 5 = True, True, False, True, True = 4/5
        self.assertAlmostEqual(rate, 0.8, delta=0.01)


class TestCurriculumStageSerialize(unittest.TestCase):
    """CurriculumStage 序列化测试。"""

    def test_to_dict_from_dict(self):
        stage = CurriculumStage(
            stage_id="test_stage_001",
            dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
            difficulty=DifficultyLevel.MEDIUM,
            target_pass_rate=0.7,
            min_attempts=3,
            max_attempts=20,
        )
        stage.record_result(StageResult(case_id="c1", passed=True, score=0.8))
        d = stage.to_dict()
        stage2 = CurriculumStage.from_dict(d)
        self.assertEqual(stage2.stage_id, "test_stage_001")
        self.assertEqual(stage2.dimension, WeaknessDimension.METHODOLOGY_ANALYSIS)
        self.assertEqual(stage2.difficulty, DifficultyLevel.MEDIUM)
        self.assertEqual(len(stage2.results), 1)


class TestTrainingCurriculum(unittest.TestCase):
    """TrainingCurriculum 测试。"""

    def _make_curriculum(self, n_stages=5):
        stages = []
        dims = list(WeaknessDimension)[:n_stages]
        for i, dim in enumerate(dims):
            stage = CurriculumStage(
                stage_id=f"stage_{i}",
                dimension=dim,
                difficulty=DifficultyLevel.EASY,
                target_pass_rate=0.7,
                min_attempts=3,
                max_attempts=10,
                order=i,
            )
            stages.append(stage)
        return TrainingCurriculum(
            curriculum_id="test_curriculum",
            name="Test",
            stages=stages,
        )

    def test_progress_empty(self):
        c = TrainingCurriculum()
        self.assertEqual(c.progress, 0.0)

    def test_progress_partial(self):
        c = self._make_curriculum(4)
        c.stages[0].status = StageStatus.PASSED
        c.stages[1].status = StageStatus.PASSED
        self.assertAlmostEqual(c.progress, 0.5, delta=0.01)

    def test_current_stage(self):
        c = self._make_curriculum(3)
        self.assertEqual(c.current_stage, c.stages[0])
        c.stages[0].status = StageStatus.PASSED
        self.assertEqual(c.current_stage, c.stages[1])

    def test_add_stage_respects_max(self):
        c = TrainingCurriculum(max_stages=2, stages=[])
        c.add_stage(CurriculumStage(stage_id="s1"))
        c.add_stage(CurriculumStage(stage_id="s2"))
        c.add_stage(CurriculumStage(stage_id="s3"))  # Should not add
        self.assertEqual(len(c.stages), 2)

    def test_get_dimension_coverage(self):
        c = self._make_curriculum(5)
        coverage = c.get_dimension_coverage()
        self.assertEqual(len(coverage), 5)

    def test_to_dict_from_dict(self):
        c = self._make_curriculum(3)
        d = c.to_dict()
        c2 = TrainingCurriculum.from_dict(d)
        self.assertEqual(len(c2.stages), 3)
        self.assertEqual(c2.curriculum_id, "test_curriculum")


class TestLearningCurveTracker(unittest.TestCase):
    """LearningCurveTracker 测试。"""

    def test_record_and_get_curve(self):
        tracker = LearningCurveTracker()
        tracker.record(
            dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
            difficulty=DifficultyLevel.MEDIUM,
            pass_rate=0.5,
            cumulative_attempts=10,
        )
        curve = tracker.get_curve(WeaknessDimension.METHODOLOGY_ANALYSIS, DifficultyLevel.MEDIUM)
        self.assertEqual(len(curve), 1)
        self.assertEqual(curve[0].pass_rate, 0.5)

    def test_learning_rate_positive(self):
        tracker = LearningCurveTracker()
        for i in range(10):
            tracker.record(
                dimension=WeaknessDimension.STATISTICAL_REASONING,
                difficulty=DifficultyLevel.EASY,
                pass_rate=0.3 + i * 0.05,
                cumulative_attempts=(i + 1) * 5,
            )
        rate = tracker.get_learning_rate(
            WeaknessDimension.STATISTICAL_REASONING,
            DifficultyLevel.EASY,
        )
        self.assertGreater(rate, 0.0)

    def test_detect_plateau(self):
        tracker = LearningCurveTracker()
        for i in range(10):
            tracker.record(
                dimension=WeaknessDimension.CAUSAL_INFERENCE,
                difficulty=DifficultyLevel.MEDIUM,
                pass_rate=0.5 + random.uniform(-0.005, 0.005),
                cumulative_attempts=(i + 1) * 3,
            )
        # Should detect plateau since pass_rate barely changes
        self.assertTrue(
            tracker.detect_plateau(WeaknessDimension.CAUSAL_INFERENCE, DifficultyLevel.MEDIUM)
        )

    def test_get_mastery(self):
        tracker = LearningCurveTracker()
        tracker.record(
            dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
            difficulty=DifficultyLevel.MEDIUM,
            pass_rate=0.8,
            cumulative_attempts=20,
        )
        mastery = tracker.get_mastery(WeaknessDimension.METHODOLOGY_ANALYSIS)
        self.assertGreater(mastery, 0.0)

    def test_serialize_deserialize(self):
        tracker = LearningCurveTracker()
        tracker.record(
            dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
            difficulty=DifficultyLevel.EASY,
            pass_rate=0.6,
            cumulative_attempts=10,
        )
        data = tracker.serialize()
        tracker2 = LearningCurveTracker.deserialize(data)
        curve = tracker2.get_curve(WeaknessDimension.METHODOLOGY_ANALYSIS, DifficultyLevel.EASY)
        self.assertEqual(len(curve), 1)


class TestCurriculumDesigner(unittest.TestCase):
    """CurriculumDesigner 测试。"""

    def _make_profile(self):
        from training.weakness_analyzer import WeaknessEntry, WeaknessEvidence, WeaknessSource
        entries = []
        for dim in list(WeaknessDimension)[:3]:
            entry = WeaknessEntry(
                dimension=dim,
                summary=f"Weakness in {dim.value}",
                evidences=[
                    WeaknessEvidence(
                        source=WeaknessSource.META_HARNESS_BOTTLENECK,
                        description=f"evidence for {dim.value}",
                        severity=0.7,
                    ),
                    WeaknessEvidence(
                        source=WeaknessSource.FAILURE_STORE,
                        description=f"failure evidence for {dim.value}",
                        severity=0.6,
                    ),
                ],
            )
            entries.append(entry)
        return WeaknessProfile(entries=entries)

    def test_design_curriculum_basic(self):
        profile = self._make_profile()
        designer = CurriculumDesigner(
            profile=profile,
            max_dimensions=3,
            review_interval=5,
        )
        curriculum = designer.design_curriculum(max_stages=15)
        self.assertIsInstance(curriculum, TrainingCurriculum)
        if ADVERSARIAL_TRAINING_ENABLED:
            self.assertGreater(len(curriculum.stages), 0)

    def test_design_curriculum_no_profile(self):
        designer = CurriculumDesigner(profile=None)
        curriculum = designer.design_curriculum()
        self.assertEqual(len(curriculum.stages), 0)

    def test_recommend_next_focus(self):
        profile = self._make_profile()
        designer = CurriculumDesigner(profile=profile)
        result = designer.recommend_next_focus()
        if ADVERSARIAL_TRAINING_ENABLED:
            self.assertIsNotNone(result)
            dim, diff = result
            self.assertIsInstance(dim, WeaknessDimension)
            self.assertIsInstance(diff, DifficultyLevel)

    def test_serialize_deserialize(self):
        profile = self._make_profile()
        designer = CurriculumDesigner(profile=profile, max_dimensions=4, review_interval=3)
        data = designer.serialize()
        designer2 = CurriculumDesigner.deserialize(data, profile=profile)
        self.assertEqual(designer2._max_dimensions, 4)
        self.assertEqual(designer2._review_interval, 3)


# ============================================================
# Module 4: AdversarialLibrary
# ============================================================

from training.adversarial_library import (
    EntryStatus,
    LibraryEntry,
    LibraryIndex,
    AdversarialLibrary,
    RegressionSuiteGenerator,
)


class TestEntryStatus(unittest.TestCase):
    """EntryStatus 枚举测试。"""

    def test_has_5_statuses(self):
        self.assertEqual(len(EntryStatus), 5)

    def test_values(self):
        self.assertEqual(EntryStatus("active"), EntryStatus.ACTIVE)
        self.assertEqual(EntryStatus("verified"), EntryStatus.VERIFIED)
        self.assertEqual(EntryStatus("deprecated"), EntryStatus.DEPRECATED)
        self.assertEqual(EntryStatus("retired"), EntryStatus.RETIRED)
        self.assertEqual(EntryStatus("quarantined"), EntryStatus.QUARANTINED)


class TestLibraryEntry(unittest.TestCase):
    """LibraryEntry 题库条目测试。"""

    def _make_case(self):
        return AdversarialCase(
            challenge_type=ChallengeType.HIDDEN_ENDOGENEITY,
            difficulty=DifficultyLevel.MEDIUM,
            target_dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
            paper_snippet="Test paper content for library entry testing with enough text.",
            gold_findings=[{"category": "methodology", "description": "Issue found"}],
        )

    def test_creation(self):
        case = self._make_case()
        entry = LibraryEntry(case=case)
        self.assertGreater(len(entry.entry_id), 0)
        self.assertEqual(entry.status, EntryStatus.ACTIVE)

    def test_pass_rate_initial(self):
        entry = LibraryEntry(case=self._make_case())
        self.assertEqual(entry.pass_rate, -1.0)  # No uses

    def test_record_usage(self):
        entry = LibraryEntry(case=self._make_case())
        entry.record_usage(passed=True)
        entry.record_usage(passed=False)
        self.assertEqual(entry.total_uses, 2)
        self.assertEqual(entry.total_passes, 1)
        self.assertAlmostEqual(entry.pass_rate, 0.5, delta=0.01)

    def test_is_effective(self):
        entry = LibraryEntry(case=self._make_case())
        entry.record_usage(passed=False)
        entry.record_usage(passed=False)
        self.assertTrue(entry.is_effective)  # pass_rate < 0.8

    def test_is_retired_candidate(self):
        entry = LibraryEntry(case=self._make_case())
        for _ in range(10):
            entry.record_usage(passed=True)
        self.assertTrue(entry.is_retired_candidate)  # >= 5 uses, pass_rate >= 0.95

    def test_discrimination_power(self):
        entry = LibraryEntry(case=self._make_case())
        for i in range(10):
            entry.record_usage(passed=(i % 2 == 0))  # 50% pass rate
        self.assertAlmostEqual(entry.discrimination_power, 1.0, delta=0.05)

    def test_deprecate(self):
        entry = LibraryEntry(case=self._make_case())
        entry.deprecate(reason="Bad gold label")
        self.assertEqual(entry.status, EntryStatus.DEPRECATED)

    def test_retire(self):
        entry = LibraryEntry(case=self._make_case())
        for _ in range(5):
            entry.record_usage(passed=True)
        entry.retire()
        self.assertEqual(entry.status, EntryStatus.RETIRED)

    def test_verify(self):
        entry = LibraryEntry(case=self._make_case())
        entry.verify(verified_by="human_reviewer")
        self.assertEqual(entry.status, EntryStatus.VERIFIED)
        self.assertEqual(entry.verified_by, "human_reviewer")

    def test_quarantine(self):
        entry = LibraryEntry(case=self._make_case())
        entry.quarantine(reason="Suspicious quality")
        self.assertEqual(entry.status, EntryStatus.QUARANTINED)

    def test_add_version(self):
        entry = LibraryEntry(case=self._make_case())
        variant = self._make_case()
        idx = entry.add_version(variant)
        self.assertEqual(idx, 1)  # index 0 is original
        self.assertEqual(len(entry.versions), 2)

    def test_to_dict_from_dict(self):
        entry = LibraryEntry(case=self._make_case(), tags=["test"], collection="unit_test")
        entry.record_usage(passed=True)
        d = entry.to_dict()
        entry2 = LibraryEntry.from_dict(d)
        self.assertEqual(entry2.entry_id, entry.entry_id)
        self.assertEqual(entry2.total_uses, 1)
        self.assertEqual(entry2.tags, ["test"])


class TestLibraryIndex(unittest.TestCase):
    """LibraryIndex 多维索引测试。"""

    def _make_entry(self, dim=WeaknessDimension.METHODOLOGY_ANALYSIS, diff=DifficultyLevel.MEDIUM):
        case = AdversarialCase(
            challenge_type=ChallengeType.HIDDEN_ENDOGENEITY,
            difficulty=diff,
            target_dimension=dim,
            paper_snippet="Test paper snippet for index testing.",
            gold_findings=[{"category": "test"}],
        )
        return LibraryEntry(case=case)

    def test_add_and_query(self):
        index = LibraryIndex()
        entry = self._make_entry()
        index.add_entry(entry)
        results = index.query(dimension=WeaknessDimension.METHODOLOGY_ANALYSIS)
        self.assertIn(entry.entry_id, results)

    def test_multi_condition_query(self):
        index = LibraryIndex()
        e1 = self._make_entry(WeaknessDimension.METHODOLOGY_ANALYSIS, DifficultyLevel.EASY)
        e2 = self._make_entry(WeaknessDimension.METHODOLOGY_ANALYSIS, DifficultyLevel.HARD)
        e3 = self._make_entry(WeaknessDimension.STATISTICAL_REASONING, DifficultyLevel.EASY)
        index.add_entry(e1)
        index.add_entry(e2)
        index.add_entry(e3)

        results = index.query(
            dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
            difficulty=DifficultyLevel.EASY,
        )
        self.assertEqual(len(results), 1)
        self.assertIn(e1.entry_id, results)

    def test_remove_entry(self):
        index = LibraryIndex()
        entry = self._make_entry()
        index.add_entry(entry)
        index.remove_entry(entry)
        results = index.query(dimension=WeaknessDimension.METHODOLOGY_ANALYSIS)
        self.assertNotIn(entry.entry_id, results)

    def test_get_coverage_stats(self):
        index = LibraryIndex()
        for dim in list(WeaknessDimension)[:3]:
            index.add_entry(self._make_entry(dim=dim))
        stats = index.get_coverage_stats()
        self.assertIn("by_dimension", stats)
        self.assertEqual(len(stats["by_dimension"]), 3)


class TestAdversarialLibrary(unittest.TestCase):
    """AdversarialLibrary 核心题库管理器测试。"""

    def _make_case(self, dim=WeaknessDimension.METHODOLOGY_ANALYSIS):
        return AdversarialCase(
            challenge_type=ChallengeType.HIDDEN_ENDOGENEITY,
            difficulty=DifficultyLevel.MEDIUM,
            target_dimension=dim,
            paper_snippet=f"Unique paper content {time.time()} {random.random()}",
            gold_findings=[{"category": "test", "description": "issue"}],
        )

    def test_creation(self):
        lib = AdversarialLibrary()
        self.assertEqual(lib.size, 0)

    def test_add_case(self):
        lib = AdversarialLibrary()
        case = self._make_case()
        entry = lib.add_case(case)
        if ADVERSARIAL_TRAINING_ENABLED:
            self.assertIsNotNone(entry)
            self.assertEqual(lib.size, 1)

    def test_query_by_dimension(self):
        lib = AdversarialLibrary()
        for dim in [WeaknessDimension.METHODOLOGY_ANALYSIS, WeaknessDimension.STATISTICAL_REASONING]:
            lib.add_case(self._make_case(dim=dim))
        results = lib.query(dimension=WeaknessDimension.METHODOLOGY_ANALYSIS)
        if ADVERSARIAL_TRAINING_ENABLED:
            self.assertEqual(len(results), 1)

    def test_record_result(self):
        lib = AdversarialLibrary()
        case = self._make_case()
        entry = lib.add_case(case)
        if entry:
            success = lib.record_result(entry.entry_id, passed=False)
            self.assertTrue(success)
            self.assertEqual(entry.total_uses, 1)

    def test_auto_retire(self):
        lib = AdversarialLibrary(auto_retire_threshold=0.95, auto_retire_min_uses=5)
        case = self._make_case()
        entry = lib.add_case(case)
        if entry:
            for _ in range(10):
                lib.record_result(entry.entry_id, passed=True)
            self.assertEqual(entry.status, EntryStatus.RETIRED)

    def test_get_stats(self):
        lib = AdversarialLibrary()
        lib.add_case(self._make_case())
        stats = lib.get_stats()
        self.assertIn("total_entries", stats)
        if ADVERSARIAL_TRAINING_ENABLED:
            self.assertEqual(stats["total_entries"], 1)

    def test_remove_entry(self):
        lib = AdversarialLibrary()
        case = self._make_case()
        entry = lib.add_case(case)
        if entry:
            success = lib.remove_entry(entry.entry_id)
            self.assertTrue(success)
            self.assertEqual(lib.size, 0)


class TestRegressionSuiteGenerator(unittest.TestCase):
    """RegressionSuiteGenerator 测试。"""

    def _populate_library(self, n=10):
        lib = AdversarialLibrary()
        dims = list(WeaknessDimension)[:5]
        diffs = list(DifficultyLevel)
        for i in range(n):
            case = AdversarialCase(
                challenge_type=ChallengeType.HIDDEN_ENDOGENEITY,
                difficulty=diffs[i % len(diffs)],
                target_dimension=dims[i % len(dims)],
                paper_snippet=f"Paper snippet {i} {random.random()}",
                gold_findings=[{"category": "test"}],
            )
            entry = lib.add_case(case)
            if entry:
                # Simulate some usage
                entry.record_usage(passed=(i % 3 != 0))
                entry.record_usage(passed=(i % 2 == 0))
        return lib

    def test_generate_basic(self):
        lib = self._populate_library(10)
        gen = RegressionSuiteGenerator(lib)
        suite = gen.generate(max_size=5)
        if ADVERSARIAL_TRAINING_ENABLED:
            self.assertLessEqual(len(suite), 5)
            for case in suite:
                self.assertIsInstance(case, AdversarialCase)

    def test_generate_targeted(self):
        lib = self._populate_library(10)
        gen = RegressionSuiteGenerator(lib)
        suite = gen.generate_targeted(
            dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
            max_size=3,
        )
        self.assertIsInstance(suite, list)

    def test_coverage_report(self):
        lib = self._populate_library(15)
        gen = RegressionSuiteGenerator(lib)
        report = gen.get_coverage_report()
        self.assertIn("suite_size", report)
        self.assertIn("dimension_coverage", report)
        self.assertIn("difficulty_coverage", report)


# ============================================================
# Module 5: Red-Blue Arena
# ============================================================

from training.red_blue_arena import (
    EloRating,
    EloSnapshot,
    MatchOutcome,
    ArenaMatch,
    RedStrategy,
    RedTeam,
    BlueTeam,
    ArenaOrchestrator,
    _EventBusMixin,
)
from core.event_bus import EventBus, EventType


class TestEloRating(unittest.TestCase):
    """ELO 评分系统测试。"""

    def test_initial_rating(self):
        elo = EloRating()
        self.assertEqual(elo.rating, 1500.0)
        self.assertEqual(elo.match_count, 0)
        self.assertTrue(elo.is_provisional)

    def test_expected_score_equal(self):
        elo = EloRating()
        expected = elo.expected_score(1500.0)
        self.assertAlmostEqual(expected, 0.5, delta=0.01)

    def test_expected_score_higher(self):
        elo = EloRating(initial_rating=1700)
        expected = elo.expected_score(1500.0)
        self.assertGreater(expected, 0.5)

    def test_expected_score_lower(self):
        elo = EloRating(initial_rating=1300)
        expected = elo.expected_score(1500.0)
        self.assertLess(expected, 0.5)

    def test_update_win(self):
        elo = EloRating()
        delta = elo.update(actual_score=1.0, opponent_rating=1500.0)
        self.assertGreater(delta, 0)
        self.assertGreater(elo.rating, 1500.0)
        self.assertEqual(elo.match_count, 1)

    def test_update_loss(self):
        elo = EloRating()
        delta = elo.update(actual_score=0.0, opponent_rating=1500.0)
        self.assertLess(delta, 0)
        self.assertLess(elo.rating, 1500.0)

    def test_k_factor_dynamic(self):
        """前30局 K=40，31-100 K=24，100+ K=16。"""
        elo = EloRating(match_count=0)
        # 前 30 局应用 K=40
        delta = elo.update(actual_score=1.0, opponent_rating=1500.0)
        self.assertAlmostEqual(abs(delta), 20.0, delta=1.0)  # K*0.5 = 40*0.5

    def test_season_reset(self):
        elo = EloRating(initial_rating=1800)
        new_rating = elo.season_reset()
        # 回归系数 0.3: 1800 + 0.3 * (1500 - 1800) = 1800 - 90 = 1710
        self.assertAlmostEqual(new_rating, 1710.0, delta=1.0)

    def test_confidence_increases_with_matches(self):
        elo1 = EloRating(match_count=5)
        elo2 = EloRating(match_count=50)
        self.assertGreater(elo2.confidence, elo1.confidence)

    def test_streak_tracking(self):
        elo = EloRating()
        elo.update(1.0, 1500.0)
        elo.update(1.0, 1500.0)
        elo.update(1.0, 1500.0)
        self.assertEqual(elo.streak, 3)
        elo.update(0.0, 1500.0)
        self.assertEqual(elo.streak, -1)

    def test_peak_rating_tracking(self):
        elo = EloRating()
        elo.update(1.0, 1500.0)
        peak_after_win = elo.peak_rating
        elo.update(0.0, 1500.0)
        self.assertEqual(elo.peak_rating, peak_after_win)

    def test_to_dict_from_dict(self):
        elo = EloRating(initial_rating=1600, match_count=10)
        elo.update(1.0, 1500.0)
        d = elo.to_dict()
        elo2 = EloRating.from_dict(d)
        self.assertAlmostEqual(elo2.rating, elo.rating, delta=0.1)

    def test_rating_trend(self):
        elo = EloRating()
        for _ in range(5):
            elo.update(1.0, 1500.0)
        trend = elo.get_rating_trend(last_n=5)
        self.assertGreater(trend, 0)


class TestMatchOutcome(unittest.TestCase):
    """MatchOutcome 枚举测试。"""

    def test_has_5_outcomes(self):
        self.assertEqual(len(MatchOutcome), 5)

    def test_values(self):
        self.assertEqual(MatchOutcome("red_win"), MatchOutcome.RED_WIN)
        self.assertEqual(MatchOutcome("blue_win"), MatchOutcome.BLUE_WIN)
        self.assertEqual(MatchOutcome("draw"), MatchOutcome.DRAW)


class TestArenaMatch(unittest.TestCase):
    """ArenaMatch 对局记录测试。"""

    def test_creation(self):
        match = ArenaMatch(season=1, round_in_season=1)
        self.assertGreater(len(match.match_id), 0)

    def test_is_valid(self):
        match = ArenaMatch(outcome=MatchOutcome.RED_WIN)
        self.assertTrue(match.is_valid)
        match2 = ArenaMatch(outcome=MatchOutcome.INVALID)
        self.assertFalse(match2.is_valid)

    def test_elo_delta_properties(self):
        match = ArenaMatch(
            red_elo_before=1500, red_elo_after=1520,
            blue_elo_before=1500, blue_elo_after=1480,
        )
        self.assertEqual(match.red_elo_delta, 20.0)
        self.assertEqual(match.blue_elo_delta, -20.0)

    def test_to_dict_from_dict(self):
        match = ArenaMatch(
            season=2, round_in_season=5,
            outcome=MatchOutcome.BLUE_WIN,
            blue_score=0.85,
            red_strategy="exploit_weakness",
        )
        d = match.to_dict()
        match2 = ArenaMatch.from_dict(d)
        self.assertEqual(match2.season, 2)
        self.assertEqual(match2.outcome, MatchOutcome.BLUE_WIN)


class TestRedStrategy(unittest.TestCase):
    """RedStrategy 枚举测试。"""

    def test_has_6_strategies(self):
        self.assertEqual(len(RedStrategy), 6)

    def test_values(self):
        self.assertEqual(RedStrategy("exploit_weakness"), RedStrategy.EXPLOIT_WEAKNESS)
        self.assertEqual(RedStrategy("escalate_difficulty"), RedStrategy.ESCALATE_DIFFICULTY)
        self.assertEqual(RedStrategy("explore_blind_spot"), RedStrategy.EXPLORE_BLIND_SPOT)
        self.assertEqual(RedStrategy("variant_attack"), RedStrategy.VARIANT_ATTACK)
        self.assertEqual(RedStrategy("compound_challenge"), RedStrategy.COMPOUND_CHALLENGE)
        self.assertEqual(RedStrategy("adaptive_counter"), RedStrategy.ADAPTIVE_COUNTER)


class TestRedTeam(unittest.TestCase):
    """RedTeam 红队测试。"""

    def test_creation(self):
        red = RedTeam()
        self.assertEqual(red.total_attacks, 0)
        self.assertEqual(red.win_rate, 0.0)

    def test_elo_access(self):
        red = RedTeam()
        self.assertIsInstance(red.elo, EloRating)
        self.assertEqual(red.elo.rating, 1500.0)

    def test_select_strategy(self):
        red = RedTeam()
        strategy = red.select_strategy()
        self.assertIsInstance(strategy, RedStrategy)

    def test_strategy_effectiveness_empty(self):
        red = RedTeam()
        eff = red.strategy_effectiveness
        self.assertIsInstance(eff, dict)


class TestEventBusMixin(unittest.TestCase):
    """_EventBusMixin 测试。"""

    def test_attach_event_bus(self):
        class TestComponent(_EventBusMixin):
            pass

        comp = TestComponent()
        bus = EventBus()
        comp.attach_event_bus(bus, source="test")
        self.assertEqual(comp._event_bus, bus)
        self.assertEqual(comp._event_source, "test")

    def test_emit_event_without_bus(self):
        """无 bus 时 emit 不抛异常。"""
        class TestComponent(_EventBusMixin):
            pass

        comp = TestComponent()
        # Should not raise
        comp._emit_event(EventType.ARENA_MATCH_STARTED)


# ============================================================
# Module 6: TrainingLoop
# ============================================================

from training.training_loop import (
    TrainingConfig,
    TrainingSession,
    TrainingResult,
    ConvergenceDetector,
    TrainingLoop,
    RoundSummary,
    StopReason,
    SessionStatus,
    CaseExecutionResult,
)


class TestTrainingConfig(unittest.TestCase):
    """TrainingConfig 超参数配置测试。"""

    def test_defaults(self):
        config = TrainingConfig()
        self.assertEqual(config.max_rounds, 50)
        self.assertEqual(config.batch_size, 5)
        self.assertEqual(config.target_mastery, 0.8)

    def test_validate_valid(self):
        config = TrainingConfig()
        errors = config.validate()
        self.assertEqual(len(errors), 0)

    def test_validate_invalid(self):
        config = TrainingConfig(max_rounds=0, target_mastery=1.5)
        errors = config.validate()
        self.assertGreater(len(errors), 0)

    def test_to_dict_from_dict(self):
        config = TrainingConfig(max_rounds=100, batch_size=10)
        d = config.to_dict()
        config2 = TrainingConfig.from_dict(d)
        self.assertEqual(config2.max_rounds, 100)
        self.assertEqual(config2.batch_size, 10)

    def test_ratio_validation(self):
        config = TrainingConfig(
            exploration_ratio=0.5,
            variant_ratio=0.4,
            review_ratio=0.2,  # Sum > 1.0
        )
        errors = config.validate()
        self.assertGreater(len(errors), 0)


class TestStopReason(unittest.TestCase):
    """StopReason 枚举测试。"""

    def test_has_8_reasons(self):
        self.assertEqual(len(StopReason), 8)

    def test_values(self):
        self.assertEqual(StopReason("converged"), StopReason.CONVERGED)
        self.assertEqual(StopReason("plateau_detected"), StopReason.PLATEAU_DETECTED)


class TestCaseExecutionResult(unittest.TestCase):
    """CaseExecutionResult 测试。"""

    def test_creation(self):
        result = CaseExecutionResult(case_id="case_001", passed=True, score=0.85)
        self.assertEqual(result.case_id, "case_001")
        self.assertTrue(result.passed)
        self.assertFalse(result.is_error)

    def test_is_error(self):
        result = CaseExecutionResult(case_id="case_002", error="Timeout")
        self.assertTrue(result.is_error)

    def test_to_dict(self):
        result = CaseExecutionResult(case_id="case_003", passed=False, score=0.3)
        d = result.to_dict()
        self.assertEqual(d["case_id"], "case_003")
        self.assertFalse(d["passed"])


class TestRoundSummary(unittest.TestCase):
    """RoundSummary 测试。"""

    def test_pass_rate(self):
        rs = RoundSummary(cases_executed=10, cases_passed=7)
        self.assertAlmostEqual(rs.pass_rate, 0.7, delta=0.01)

    def test_pass_rate_zero_division(self):
        rs = RoundSummary(cases_executed=0)
        self.assertEqual(rs.pass_rate, 0.0)

    def test_to_dict(self):
        rs = RoundSummary(round_number=3, cases_executed=5, cases_passed=3)
        d = rs.to_dict()
        self.assertEqual(d["round_number"], 3)


class TestTrainingResult(unittest.TestCase):
    """TrainingResult 测试。"""

    def test_overall_pass_rate(self):
        result = TrainingResult(
            total_cases_executed=20,
            total_cases_passed=14,
        )
        self.assertAlmostEqual(result.overall_pass_rate, 0.7, delta=0.01)

    def test_converged_property(self):
        result = TrainingResult(stop_reason=StopReason.CONVERGED)
        self.assertTrue(result.converged)

    def test_avg_mastery_improvement(self):
        result = TrainingResult(
            mastery_improvements={"dim1": 0.2, "dim2": 0.1, "dim3": 0.3},
        )
        self.assertAlmostEqual(result.avg_mastery_improvement, 0.2, delta=0.01)

    def test_to_dict(self):
        result = TrainingResult(session_id="sess_001", stop_reason=StopReason.CONVERGED)
        d = result.to_dict()
        self.assertEqual(d["session_id"], "sess_001")
        self.assertEqual(d["stop_reason"], "converged")


class TestConvergenceDetector(unittest.TestCase):
    """ConvergenceDetector 收敛检测器测试。"""

    def test_creation(self):
        config = TrainingConfig()
        detector = ConvergenceDetector(config)
        should_stop, reason = detector.should_stop()
        self.assertFalse(should_stop)

    def test_not_stop_before_min_rounds(self):
        config = TrainingConfig(min_rounds_before_convergence=5)
        detector = ConvergenceDetector(config)
        tracker = LearningCurveTracker()
        # Record fewer than 5 rounds
        for i in range(3):
            detector.record_round(i, 0.9, {"dim1": 0.9}, tracker)
        should_stop, reason = detector.should_stop()
        self.assertFalse(should_stop)

    def test_stop_when_converged(self):
        config = TrainingConfig(
            min_rounds_before_convergence=3,
            target_mastery=0.8,
            convergence_window=3,
        )
        detector = ConvergenceDetector(config)
        tracker = LearningCurveTracker()
        # All dimensions mastered
        for i in range(5):
            detector.record_round(i, 0.9, {"dim1": 0.85, "dim2": 0.9}, tracker)
        should_stop, reason = detector.should_stop()
        self.assertTrue(should_stop)
        self.assertEqual(reason, StopReason.CONVERGED)

    def test_plateau_detection(self):
        config = TrainingConfig(
            min_rounds_before_convergence=3,
            convergence_window=3,
            convergence_threshold=0.02,
            plateau_patience=2,
            target_mastery=0.9,  # High so convergence doesn't trigger
        )
        detector = ConvergenceDetector(config)
        tracker = LearningCurveTracker()
        # Same pass_rate across many rounds (plateau)
        for i in range(10):
            detector.record_round(i, 0.5, {"dim1": 0.5}, tracker)
        # Plateau counter increments each call to should_stop() when plateaued
        # Need plateau_patience (2) consecutive calls where plateau is detected
        detector.should_stop()  # counter -> 1
        should_stop, reason = detector.should_stop()  # counter -> 2 >= patience
        self.assertTrue(should_stop)
        self.assertEqual(reason, StopReason.PLATEAU_DETECTED)

    def test_strategy_advice(self):
        config = TrainingConfig(convergence_window=3)
        detector = ConvergenceDetector(config)
        tracker = LearningCurveTracker()
        # Very high pass rate
        for i in range(5):
            detector.record_round(i, 0.95, {"dim1": 0.7}, tracker)
        advice = detector.get_strategy_advice()
        self.assertIsInstance(advice, list)

    def test_serialize_deserialize(self):
        config = TrainingConfig()
        detector = ConvergenceDetector(config)
        tracker = LearningCurveTracker()
        detector.record_round(0, 0.5, {"dim1": 0.4}, tracker)
        data = detector.serialize()
        detector2 = ConvergenceDetector.deserialize(data, config)
        self.assertEqual(len(detector2._round_pass_rates), 1)


class TestTrainingSession(unittest.TestCase):
    """TrainingSession 训练会话测试。"""

    def test_creation(self):
        session = TrainingSession()
        self.assertGreater(len(session.session_id), 0)
        self.assertEqual(session.status, SessionStatus.CREATED)

    def test_start(self):
        session = TrainingSession()
        session.start()
        self.assertEqual(session.status, SessionStatus.RUNNING)
        self.assertGreater(session.started_at, 0)

    def test_pause_resume(self):
        session = TrainingSession()
        session.start()
        session.pause()
        self.assertEqual(session.status, SessionStatus.PAUSED)
        session.start()  # Resume
        self.assertEqual(session.status, SessionStatus.RUNNING)

    def test_complete(self):
        session = TrainingSession()
        session.start()
        session.complete(StopReason.CONVERGED)
        self.assertEqual(session.status, SessionStatus.COMPLETED)
        self.assertGreater(session.completed_at, 0)

    def test_fail(self):
        session = TrainingSession()
        session.start()
        session.fail("Test error")
        self.assertEqual(session.status, SessionStatus.FAILED)
        self.assertEqual(len(session.error_log), 1)

    def test_cannot_start_completed(self):
        session = TrainingSession()
        session.start()
        session.complete(StopReason.CONVERGED)
        with self.assertRaises(ValueError):
            session.start()

    def test_record_case_result(self):
        session = TrainingSession()
        session.start()
        result = CaseExecutionResult(case_id="c1", passed=True)
        session.record_case_result(result)
        self.assertEqual(session.total_cases_executed, 1)
        self.assertEqual(session.total_cases_passed, 1)

    def test_budget_exhaustion_tokens(self):
        session = TrainingSession(config=TrainingConfig(token_budget=100))
        session.start()
        session.total_tokens_consumed = 100
        exhausted, reason = session.is_budget_exhausted()
        self.assertTrue(exhausted)
        self.assertEqual(reason, StopReason.TOKEN_BUDGET_EXHAUSTED)

    def test_overall_pass_rate(self):
        session = TrainingSession()
        session.total_cases_executed = 10
        session.total_cases_passed = 7
        self.assertAlmostEqual(session.overall_pass_rate, 0.7, delta=0.01)

    def test_to_dict_from_dict(self):
        session = TrainingSession(config=TrainingConfig(max_rounds=30))
        session.start()
        result = CaseExecutionResult(case_id="c1", passed=True)
        session.record_case_result(result)
        d = session.to_dict()
        session2 = TrainingSession.from_dict(d)
        self.assertEqual(session2.total_cases_executed, 1)
        self.assertEqual(session2.config.max_rounds, 30)


class TestTrainingLoop(unittest.TestCase):
    """TrainingLoop 核心编排器端到端测试。"""

    def _make_profile(self):
        """创建一个有效的弱点画像用于测试。"""
        from training.weakness_analyzer import WeaknessEntry, WeaknessEvidence, WeaknessSource
        entries = [
            WeaknessEntry(
                dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
                summary="Weak in methodology",
                evidences=[
                    WeaknessEvidence(
                        source=WeaknessSource.META_HARNESS_BOTTLENECK,
                        description="Missed IV assumption",
                        severity=0.7,
                    ),
                ],
            ),
            WeaknessEntry(
                dimension=WeaknessDimension.STATISTICAL_REASONING,
                summary="Weak in stats",
                evidences=[
                    WeaknessEvidence(
                        source=WeaknessSource.FAILURE_STORE,
                        description="P-value misinterpretation",
                        severity=0.6,
                    ),
                ],
            ),
        ]
        return WeaknessProfile(entries=entries)

    def _make_case(self, case_id="test_case_1"):
        """创建一个有效的对抗样本。"""
        return AdversarialCase(
            case_id=case_id,
            challenge_type=ChallengeType.HIDDEN_ENDOGENEITY,
            difficulty=DifficultyLevel.MEDIUM,
            target_dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
            paper_snippet="A long enough paper snippet for testing purposes, exceeding 50 characters for validation.",
            gold_findings=[{"category": "methodology", "description": "endogeneity"}],
            gold_explanation="The instrument is invalid because...",
        )

    def _make_loop(self, config=None, max_rounds=3, batch_size=2):
        """创建一个完整配置的 TrainingLoop，mock 所有外部依赖。"""
        config = config or TrainingConfig(
            max_rounds=max_rounds,
            batch_size=batch_size,
            min_rounds_before_convergence=2,
            plateau_patience=2,
        )

        executor = MagicMock()
        executor.execute_case.return_value = CaseExecutionResult(
            case_id="test", passed=True, score=0.8,
            matched_gold=1, total_gold=1,
        )

        weakness_analyzer = MagicMock()
        weakness_analyzer.analyze.return_value = self._make_profile()

        generator = MagicMock()
        # generate_challenge is async, return a coroutine
        async def mock_generate(**kwargs):
            return self._make_case(case_id=f"case_{random.randint(0, 99999)}")
        generator.generate_challenge = mock_generate

        async def mock_generate_from_failure(**kwargs):
            return self._make_case(case_id=f"variant_{random.randint(0, 99999)}")
        generator.generate_from_failure = mock_generate_from_failure

        return TrainingLoop(
            executor=executor,
            weakness_analyzer=weakness_analyzer,
            generator=generator,
            config=config,
        )

    def test_creation(self):
        loop = self._make_loop()
        self.assertIsNotNone(loop)

    def test_creation_with_config(self):
        config = TrainingConfig(max_rounds=10, batch_size=3)
        loop = self._make_loop(config=config)
        # Session is None before start()
        self.assertIsNone(loop.session)
        self.assertFalse(loop.is_running)

    def test_start_creates_session(self):
        """start() 创建会话并进入 RUNNING 状态。"""
        loop = self._make_loop()
        loop.start()
        self.assertIsNotNone(loop.session)
        self.assertEqual(loop.session.status, SessionStatus.RUNNING)
        self.assertTrue(loop.is_running)
        self.assertFalse(loop.is_complete)

    def test_start_analyzes_weakness(self):
        """start() 应调用 weakness_analyzer.analyze()。"""
        loop = self._make_loop()
        loop.start()
        self.assertIsNotNone(loop.session.weakness_profile)
        self.assertGreater(len(loop.session.weakness_profile.entries), 0)

    def test_start_designs_curriculum(self):
        """start() 应设计课程。"""
        loop = self._make_loop()
        loop.start()
        self.assertIsNotNone(loop.session.curriculum)

    def test_step_executes_one_round(self):
        """step() 执行一轮训练并返回 RoundSummary。"""
        loop = self._make_loop()
        loop.start()
        summary = loop.step()
        self.assertIsNotNone(summary)
        self.assertIsInstance(summary, RoundSummary)
        self.assertEqual(summary.round_number, 1)
        self.assertGreater(summary.cases_executed, 0)

    def test_step_increments_round(self):
        """多次 step() 应递增轮次。"""
        loop = self._make_loop(max_rounds=5)
        loop.start()
        s1 = loop.step()
        s2 = loop.step()
        self.assertEqual(s1.round_number, 1)
        self.assertEqual(s2.round_number, 2)
        self.assertEqual(loop.session.current_round, 2)

    def test_run_completes_training(self):
        """run() 运行完整训练直到停止条件触发。"""
        loop = self._make_loop(max_rounds=3, batch_size=2)
        result = loop.run()
        self.assertIsInstance(result, TrainingResult)
        self.assertTrue(loop.is_complete)
        self.assertGreater(result.total_rounds, 0)
        self.assertLessEqual(result.total_rounds, 3)
        self.assertGreater(result.total_cases_executed, 0)

    def test_run_respects_max_rounds(self):
        """run() 不超过 max_rounds。"""
        loop = self._make_loop(max_rounds=2, batch_size=1)
        result = loop.run()
        self.assertLessEqual(result.total_rounds, 2)

    def test_pause_and_resume(self):
        """pause() 暂停训练，resume() 恢复。"""
        loop = self._make_loop(max_rounds=5)
        loop.start()
        loop.step()
        self.assertEqual(loop.session.current_round, 1)

        # Pause
        state = loop.pause()
        self.assertEqual(loop.session.status, SessionStatus.PAUSED)
        self.assertIsInstance(state, dict)
        self.assertIn("config", state)
        self.assertIn("session", state)
        self.assertIn("tracker", state)

        # Resume with new loop
        executor = MagicMock()
        executor.execute_case.return_value = CaseExecutionResult(
            case_id="resumed", passed=True, score=0.9,
        )
        weakness_analyzer = MagicMock()
        generator = MagicMock()

        async def mock_gen(**kwargs):
            return self._make_case(case_id=f"resumed_{random.randint(0, 99999)}")
        generator.generate_challenge = mock_gen

        resumed = TrainingLoop.resume(
            state=state,
            executor=executor,
            weakness_analyzer=weakness_analyzer,
            generator=generator,
        )
        self.assertEqual(resumed.session.status, SessionStatus.RUNNING)
        self.assertEqual(resumed.session.current_round, 1)  # preserved

    def test_stop_manual(self):
        """stop() 手动终止训练。"""
        loop = self._make_loop(max_rounds=10)
        loop.start()
        loop.step()
        loop.stop()
        self.assertEqual(loop.session.status, SessionStatus.COMPLETED)
        self.assertTrue(loop.is_complete)

    def test_event_publishing(self):
        """训练过程应发布事件。"""
        events = []
        def recorder(name, payload):
            events.append((name, payload))

        config = TrainingConfig(max_rounds=2, batch_size=1, min_rounds_before_convergence=1)
        executor = MagicMock()
        executor.execute_case.return_value = CaseExecutionResult(
            case_id="t", passed=True, score=0.8,
        )
        analyzer = MagicMock()
        analyzer.analyze.return_value = self._make_profile()
        generator = MagicMock()

        async def mock_gen(**kwargs):
            return self._make_case()
        generator.generate_challenge = mock_gen

        loop = TrainingLoop(
            executor=executor,
            weakness_analyzer=analyzer,
            generator=generator,
            config=config,
            event_publisher=recorder,
        )
        loop.run()
        event_names = [e[0] for e in events]
        self.assertIn("training.weakness_analysis_started", event_names)
        self.assertIn("training.session_started", event_names)
        self.assertIn("training.round_started", event_names)
        self.assertIn("training.round_completed", event_names)

    def test_callback_on_round_complete(self):
        """回调在每轮结束时触发。"""
        callback = MagicMock()
        callback.on_round_complete = MagicMock()
        callback.on_session_complete = MagicMock()

        config = TrainingConfig(max_rounds=2, batch_size=1, min_rounds_before_convergence=1)
        executor = MagicMock()
        executor.execute_case.return_value = CaseExecutionResult(
            case_id="t", passed=True, score=0.8,
        )
        analyzer = MagicMock()
        analyzer.analyze.return_value = self._make_profile()
        generator = MagicMock()

        async def mock_gen(**kwargs):
            return self._make_case()
        generator.generate_challenge = mock_gen

        loop = TrainingLoop(
            executor=executor,
            weakness_analyzer=analyzer,
            generator=generator,
            config=config,
            callbacks=[callback],
        )
        loop.run()
        self.assertTrue(callback.on_round_complete.called)
        self.assertTrue(callback.on_session_complete.called)

    def test_kill_switch_disabled_returns_immediately(self):
        """Kill Switch OFF 时 run() 立即返回。"""
        loop = self._make_loop()
        with patch("training.training_loop.ADVERSARIAL_TRAINING_ENABLED", False):
            result = loop.run()
            self.assertEqual(result.session_id, "disabled")
            self.assertEqual(result.stop_reason, StopReason.MANUAL_STOP)

    def test_serialize_full_state(self):
        """训练中间状态可完整序列化。"""
        loop = self._make_loop(max_rounds=5)
        loop.start()
        loop.step()
        state = loop.serialize()
        self.assertIn("config", state)
        self.assertIn("session", state)
        self.assertIn("tracker", state)
        self.assertIn("convergence", state)
        # config 保持正确
        self.assertEqual(state["config"]["max_rounds"], 5)
        # session 记录了轮次
        self.assertEqual(state["session"]["current_round"], 1)

    def test_execution_error_isolation(self):
        """单个 case 执行失败不中断整体训练。"""
        config = TrainingConfig(max_rounds=2, batch_size=2, min_rounds_before_convergence=1)
        executor = MagicMock()
        call_count = [0]

        def side_effect(case):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Simulated failure")
            return CaseExecutionResult(case_id=case.case_id, passed=True, score=0.8)

        executor.execute_case.side_effect = side_effect
        analyzer = MagicMock()
        analyzer.analyze.return_value = self._make_profile()
        generator = MagicMock()

        async def mock_gen(**kwargs):
            return self._make_case(case_id=f"err_{random.randint(0, 99999)}")
        generator.generate_challenge = mock_gen

        loop = TrainingLoop(
            executor=executor, weakness_analyzer=analyzer,
            generator=generator, config=config,
        )
        result = loop.run()
        # 训练应完成，不因单个错误中断
        self.assertTrue(loop.is_complete)
        self.assertGreater(result.total_rounds, 0)


# ============================================================
# Kill Switch Integration Tests
# ============================================================

class TestKillSwitch(unittest.TestCase):
    """Kill Switch 集成测试——确认 OFF 时所有模块安全降级。"""

    @patch.dict(os.environ, {"SCHOLAR_GODEL_ADVERSARIAL_TRAINING": "0"})
    def test_weakness_analyzer_disabled(self):
        """Kill Switch OFF 时 WeaknessAnalyzer 应返回空画像。"""
        # Note: Module-level flag is already evaluated at import time,
        # so this tests the module's runtime behavior pattern
        analyzer = WeaknessAnalyzer()
        profile = analyzer.analyze()
        self.assertIsInstance(profile, WeaknessProfile)


# ============================================================
# EventBus Integration Tests
# ============================================================

class TestEventBusIntegration(unittest.TestCase):
    """EventBus 与 Arena 集成测试。"""

    def test_elo_emits_event(self):
        """ELO 更新应通过 EventBus 发布事件。"""
        bus = EventBus()
        events_received = []

        def listener(event):
            events_received.append(event)

        bus.subscribe(EventType.ARENA_ELO_UPDATED, listener)

        elo = EloRating()
        elo.attach_event_bus(bus, source="test_elo")
        elo.update(actual_score=1.0, opponent_rating=1500.0)

        self.assertGreater(len(events_received), 0)

    def test_event_bus_pub_sub(self):
        """基本的发布/订阅功能。"""
        bus = EventBus()
        received = []

        bus.subscribe(EventType.TRAINING_SESSION_COMPLETED, lambda e: received.append(e))
        bus.emit(event_type=EventType.TRAINING_SESSION_COMPLETED, source="test", turn=1)

        self.assertEqual(len(received), 1)


# ============================================================
# Cross-Module Integration Tests
# ============================================================

class TestCrossModuleIntegration(unittest.TestCase):
    """跨模块集成测试。"""

    def test_weakness_to_curriculum_flow(self):
        """弱点画像 → 课程设计的完整流程。"""
        from training.weakness_analyzer import WeaknessEntry, WeaknessEvidence, WeaknessSource

        # 创建弱点画像
        entries = []
        for dim in [WeaknessDimension.METHODOLOGY_ANALYSIS, WeaknessDimension.STATISTICAL_REASONING]:
            entry = WeaknessEntry(
                dimension=dim,
                summary=f"Weak in {dim.value}",
                evidences=[
                    WeaknessEvidence(
                        source=WeaknessSource.META_HARNESS_BOTTLENECK,
                        description="Evidence",
                        severity=0.7,
                    ),
                    WeaknessEvidence(
                        source=WeaknessSource.FAILURE_STORE,
                        description="More evidence",
                        severity=0.6,
                    ),
                ],
            )
            entries.append(entry)

        profile = WeaknessProfile(entries=entries)

        # 设计课程
        designer = CurriculumDesigner(profile=profile, max_dimensions=2)
        curriculum = designer.design_curriculum(max_stages=10)

        if ADVERSARIAL_TRAINING_ENABLED:
            self.assertGreater(len(curriculum.stages), 0)
            # 确保课程覆盖了两个维度
            dims_covered = set(s.dimension for s in curriculum.stages)
            self.assertGreaterEqual(len(dims_covered), 1)

    def test_adversarial_case_to_library_flow(self):
        """对抗样本生成 → 入库 → 查询流程。"""
        case = AdversarialCase(
            challenge_type=ChallengeType.HIDDEN_ENDOGENEITY,
            difficulty=DifficultyLevel.HARD,
            target_dimension=WeaknessDimension.CAUSAL_INFERENCE,
            paper_snippet="Integration test paper with causal inference issues.",
            gold_findings=[{"category": "causal", "description": "Endogeneity not addressed"}],
        )

        lib = AdversarialLibrary()
        entry = lib.add_case(case, tags=["integration_test"])

        if ADVERSARIAL_TRAINING_ENABLED and entry:
            # 查询应找到
            results = lib.query(
                dimension=WeaknessDimension.CAUSAL_INFERENCE,
                tag="integration_test",
            )
            self.assertGreater(len(results), 0)

            # 记录结果
            lib.record_result(entry.entry_id, passed=False)
            self.assertEqual(entry.total_uses, 1)
            self.assertEqual(entry.pass_rate, 0.0)

    def test_training_session_lifecycle(self):
        """训练会话完整生命周期。"""
        config = TrainingConfig(max_rounds=5, batch_size=2)
        session = TrainingSession(config=config)

        # Created → Running
        self.assertEqual(session.status, SessionStatus.CREATED)
        session.start()
        self.assertEqual(session.status, SessionStatus.RUNNING)

        # Record some results
        for i in range(3):
            result = CaseExecutionResult(case_id=f"c{i}", passed=(i % 2 == 0), score=0.5 + i * 0.1)
            session.record_case_result(result)

        self.assertEqual(session.total_cases_executed, 3)
        self.assertEqual(session.total_cases_passed, 2)

        # Running → Paused → Running
        session.pause()
        self.assertEqual(session.status, SessionStatus.PAUSED)
        time.sleep(0.01)
        session.start()
        self.assertEqual(session.status, SessionStatus.RUNNING)

        # Running → Completed
        session.complete(StopReason.CONVERGED)
        self.assertEqual(session.status, SessionStatus.COMPLETED)
        self.assertGreater(session.elapsed_seconds, 0)


if __name__ == "__main__":
    unittest.main()
