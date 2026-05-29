"""
test_meta_harness_complete.py — Phase 5 Complete Layer 全量测试

覆盖:
    - PromptOptimizer: 变体注册、反馈记录、最优选择、建议变体、收敛检测
    - ConfigSpaceSearcher: 参数定义、随机搜索、局部搜索、最优获取
    - RegressionTestSuite: baseline 锁定、退化检测、多 baseline 检查
    - EvalDatasetBuilder: 样本添加、过滤、共识构建、统计
    - MetaHarnessOrchestrator: 协调各模块
"""

import os
import random
import pytest
from dataclasses import dataclass, field
from unittest.mock import patch, MagicMock

from core.meta_harness_complete import (
    PromptOptimizer,
    PromptVariant,
    OptimizationFeedback,
    ConfigSpaceSearcher,
    ConfigParameter,
    ConfigPoint,
    SearchResult,
    RegressionTestSuite,
    BaselineSnapshot,
    RegressionCheckResult,
    EvalDatasetBuilder,
    EvalSample,
    DatasetStats,
    MetaHarnessOrchestrator,
)


# ================================================================
# Test: PromptOptimizer
# ================================================================

class TestPromptOptimizer:
    """PromptOptimizer 测试套件。"""

    def test_register_variant(self):
        """注册变体。"""
        opt = PromptOptimizer()
        v = opt.register_variant("v1", "Review {section} for {criteria}")
        assert v.variant_id == "v1"
        assert v.template == "Review {section} for {criteria}"
        assert v.executions == 0

    def test_record_feedback(self):
        """记录反馈更新分数。"""
        opt = PromptOptimizer()
        opt.register_variant("v1", "template")
        opt.record_feedback(OptimizationFeedback("v1", score=0.8))
        opt.record_feedback(OptimizationFeedback("v1", score=0.6))

        v = opt.get_variant("v1")
        assert v.executions == 2
        assert v.score == pytest.approx(0.7)
        assert v.total_score == pytest.approx(1.4)

    def test_get_best_variant_with_min_executions(self):
        """最优变体需满足最小执行次数。"""
        opt = PromptOptimizer(min_executions_for_comparison=3)
        opt.register_variant("good", "good template")
        opt.register_variant("better", "better template")

        # 'better' 只执行 1 次
        opt.record_feedback(OptimizationFeedback("better", score=1.0))

        # 'good' 执行 3 次
        for _ in range(3):
            opt.record_feedback(OptimizationFeedback("good", score=0.7))

        best = opt.get_best_variant()
        assert best.variant_id == "good"  # 'better' 不满足最小执行次数

    def test_get_best_variant_fallback(self):
        """无满足条件的变体时返回执行最多的。"""
        opt = PromptOptimizer(min_executions_for_comparison=10)
        opt.register_variant("v1", "t1")
        opt.record_feedback(OptimizationFeedback("v1", score=0.5))

        best = opt.get_best_variant()
        assert best is not None
        assert best.variant_id == "v1"

    def test_suggest_next_variant(self):
        """建议下一个变体。"""
        opt = PromptOptimizer(min_executions_for_comparison=1)
        opt.register_variant("v1", "template", variables={"temp": 0.7})
        opt.record_feedback(OptimizationFeedback("v1", score=0.6))

        new_v = opt.suggest_next_variant()
        assert new_v is not None
        assert new_v.parent_id == "v1"
        assert new_v.variant_id != "v1"

    def test_suggest_with_custom_mutation(self):
        """自定义变异函数。"""
        opt = PromptOptimizer(min_executions_for_comparison=1)
        opt.register_variant("v1", "original", variables={"x": 1})
        opt.record_feedback(OptimizationFeedback("v1", score=0.5))

        def custom_mutate(template, variables):
            return "mutated_" + template, {"x": variables["x"] + 10}

        new_v = opt.suggest_next_variant(mutation_fn=custom_mutate)
        assert "mutated_" in new_v.template
        assert new_v.variables["x"] == 11

    def test_prune_worst_on_overflow(self):
        """超过 max_variants 时移除最差的。"""
        opt = PromptOptimizer(max_variants=3, min_executions_for_comparison=1)

        # 先注册并给予 feedback，让所有变体都有足够 execution
        for i in range(3):
            opt.register_variant(f"v{i}", f"template_{i}")
            opt.record_feedback(OptimizationFeedback(f"v{i}", score=(i + 1) * 0.2))

        # v0=0.2, v1=0.4, v2=0.6，已满 3 个
        # 注册第 4 个 (高分) 触发 prune
        opt.register_variant("v3", "template_3")
        opt.record_feedback(OptimizationFeedback("v3", score=0.9))

        # 应该只保留 3 个
        assert len(opt._variants) <= 3
        # 最差的 (v0, score=0.2) 应被移除
        assert opt.get_variant("v0") is None

    def test_should_explore_decreasing(self):
        """探索率随迭代递减。"""
        opt = PromptOptimizer()
        # 初始时探索率高
        random.seed(42)
        explores_early = sum(opt.should_explore() for _ in range(100))

        # 模拟很多迭代后
        opt._iteration = 20
        random.seed(42)
        explores_late = sum(opt.should_explore() for _ in range(100))

        # 早期探索更多
        assert explores_early >= explores_late

    def test_optimization_report(self):
        """优化报告生成。"""
        opt = PromptOptimizer()
        opt.register_variant("v1", "t1")
        for _ in range(5):
            opt.record_feedback(OptimizationFeedback("v1", score=0.8))

        report = opt.get_optimization_report()
        assert report["total_iterations"] == 5
        assert report["total_variants"] == 1
        assert report["best_variant"] == "v1"
        assert report["best_score"] == pytest.approx(0.8)

    def test_serialize_deserialize(self):
        """序列化/反序列化一致性。"""
        opt = PromptOptimizer()
        opt.register_variant("v1", "template_1", variables={"x": 1.0})
        opt.record_feedback(OptimizationFeedback("v1", score=0.75))

        data = opt.serialize()
        opt2 = PromptOptimizer()
        opt2.deserialize(data)

        v = opt2.get_variant("v1")
        assert v is not None
        assert v.score == pytest.approx(0.75)
        assert v.variables == {"x": 1.0}
        assert opt2._iteration == 1

    def test_convergence_detection(self):
        """收敛检测。"""
        opt = PromptOptimizer()
        opt.register_variant("v1", "t1")
        # 模拟已收敛 (低方差)
        for _ in range(15):
            opt.record_feedback(OptimizationFeedback("v1", score=0.80))

        report = opt.get_optimization_report()
        assert report["convergence"]["converged"] is True

    def test_kill_switch_disabled(self):
        """Kill Switch 关闭时不执行优化。"""
        with patch("core.meta_harness_complete.PROMPT_OPTIMIZATION_ENABLED", False):
            opt = PromptOptimizer()
            v = opt.register_variant("v1", "t1")
            assert v.variant_id == "v1"

            opt.record_feedback(OptimizationFeedback("v1", score=0.9))
            # 没有实际记录
            assert opt.get_variant("v1") is None

    def test_feedback_nonexistent_variant(self):
        """对不存在的变体记录反馈: 静默忽略。"""
        opt = PromptOptimizer()
        opt.record_feedback(OptimizationFeedback("ghost", score=0.9))
        assert opt.get_variant("ghost") is None


# ================================================================
# Test: ConfigSpaceSearcher
# ================================================================

class TestConfigSpaceSearcher:
    """ConfigSpaceSearcher 测试套件。"""

    def test_define_parameter(self):
        """定义参数。"""
        searcher = ConfigSpaceSearcher()
        searcher.define_parameter(ConfigParameter("temp", "float", 0.0, 1.0))
        searcher.define_parameter(ConfigParameter("top_k", "int", 1, 20))

        assert "temp" in searcher.list_parameters()
        assert "top_k" in searcher.list_parameters()

    def test_random_sample_in_bounds(self):
        """随机采样在边界内。"""
        searcher = ConfigSpaceSearcher(n_initial_random=100)
        searcher.define_parameter(ConfigParameter("temp", "float", 0.0, 1.0))
        searcher.define_parameter(ConfigParameter("top_k", "int", 1, 20))
        searcher.define_parameter(ConfigParameter(
            "mode", "categorical", choices=["fast", "thorough", "balanced"]
        ))

        for _ in range(20):
            config = searcher.suggest_next()
            assert 0.0 <= config["temp"] <= 1.0
            assert 1 <= config["top_k"] <= 20
            assert config["mode"] in ["fast", "thorough", "balanced"]

    def test_record_and_get_best(self):
        """记录评估后获取最优。"""
        searcher = ConfigSpaceSearcher()
        searcher.define_parameter(ConfigParameter("temp", "float", 0.0, 1.0))

        searcher.record_evaluation({"temp": 0.3}, score=0.6)
        searcher.record_evaluation({"temp": 0.7}, score=0.9)
        searcher.record_evaluation({"temp": 0.5}, score=0.75)

        result = searcher.get_best()
        assert result.best_config["temp"] == 0.7
        assert result.best_score == 0.9
        assert result.total_evaluations == 3

    def test_local_search_near_best(self):
        """局部搜索: 在最优点附近。"""
        searcher = ConfigSpaceSearcher(n_initial_random=2, exploitation_ratio=1.0)
        searcher.define_parameter(ConfigParameter("temp", "float", 0.0, 1.0))

        # 先做 2 次随机 + 1 次记录最优
        searcher.record_evaluation({"temp": 0.1}, score=0.3)
        searcher.record_evaluation({"temp": 0.8}, score=0.95)

        # 后续应该做局部搜索
        random.seed(42)
        config = searcher.suggest_next()
        # 应该在 0.8 附近 (±10%)
        assert 0.6 <= config["temp"] <= 1.0

    def test_get_top_n(self):
        """获取 top N 配置。"""
        searcher = ConfigSpaceSearcher()
        searcher.define_parameter(ConfigParameter("x", "float", 0, 10))

        for i in range(10):
            searcher.record_evaluation({"x": float(i)}, score=i * 0.1)

        top3 = searcher.get_top_n(3)
        assert len(top3) == 3
        assert top3[0].score == pytest.approx(0.9)

    def test_search_stats(self):
        """搜索统计。"""
        searcher = ConfigSpaceSearcher()
        searcher.define_parameter(ConfigParameter("x", "float", 0, 1))

        for i in range(5):
            searcher.record_evaluation({"x": i * 0.2}, score=i * 0.2)

        stats = searcher.get_search_stats()
        assert stats["total_evaluations"] == 5
        assert stats["best_score"] == pytest.approx(0.8)
        assert stats["worst_score"] == pytest.approx(0.0)

    def test_improvement_rate(self):
        """改进率计算。"""
        searcher = ConfigSpaceSearcher()
        searcher.define_parameter(ConfigParameter("x", "float", 0, 1))

        # 前半分数低，后半分数高
        for i in range(10):
            score = 0.3 if i < 5 else 0.8
            searcher.record_evaluation({"x": i * 0.1}, score=score)

        stats = searcher.get_search_stats()
        assert stats["improvement_rate"] > 0

    def test_serialize_deserialize(self):
        """序列化/反序列化。"""
        searcher = ConfigSpaceSearcher()
        searcher.define_parameter(ConfigParameter("temp", "float", 0.0, 1.0))
        searcher.record_evaluation({"temp": 0.5}, score=0.8)

        data = searcher.serialize()
        searcher2 = ConfigSpaceSearcher()
        searcher2.deserialize(data)

        assert "temp" in searcher2.list_parameters()
        result = searcher2.get_best()
        assert result.best_score == pytest.approx(0.8)

    def test_default_config(self):
        """默认配置使用中间值。"""
        searcher = ConfigSpaceSearcher()
        searcher.define_parameter(ConfigParameter("temp", "float", 0.0, 1.0, default=0.7))
        searcher.define_parameter(ConfigParameter("k", "int", 1, 10))

        config = searcher._get_default_config()
        assert config["temp"] == 0.7  # 使用 default
        assert config["k"] == 5       # 使用中间值

    def test_kill_switch_disabled(self):
        """Kill Switch 关闭时返回默认配置。"""
        with patch("core.meta_harness_complete.CONFIG_SEARCH_ENABLED", False):
            searcher = ConfigSpaceSearcher()
            searcher.define_parameter(ConfigParameter("temp", "float", 0.0, 1.0, default=0.5))
            config = searcher.suggest_next()
            assert config["temp"] == 0.5

    def test_categorical_parameter(self):
        """分类参数处理。"""
        searcher = ConfigSpaceSearcher()
        searcher.define_parameter(ConfigParameter(
            "strategy", "categorical", choices=["greedy", "beam", "sample"]
        ))

        config = searcher.suggest_next()
        assert config["strategy"] in ["greedy", "beam", "sample"]


# ================================================================
# Test: RegressionTestSuite
# ================================================================

class TestRegressionTestSuite:
    """RegressionTestSuite 测试套件。"""

    def test_lock_and_get_baseline(self):
        """锁定并获取 baseline。"""
        suite = RegressionTestSuite()
        snapshot = suite.lock_baseline("v1.0", {"precision": 0.85, "recall": 0.72})

        assert snapshot.snapshot_id == "v1.0"
        assert snapshot.metrics["precision"] == 0.85

        retrieved = suite.get_baseline("v1.0")
        assert retrieved is snapshot

    def test_no_degradation(self):
        """无退化: 通过检测。"""
        suite = RegressionTestSuite(degradation_threshold=0.05)
        suite.lock_baseline("v1", {"precision": 0.80, "recall": 0.70})

        result = suite.check_regression("v1", {"precision": 0.82, "recall": 0.69})
        assert result.passed is True
        assert len(result.degraded_metrics) == 0

    def test_degradation_detected(self):
        """检测到退化。"""
        suite = RegressionTestSuite(degradation_threshold=0.05)
        suite.lock_baseline("v1", {"precision": 0.80, "recall": 0.70})

        result = suite.check_regression("v1", {"precision": 0.70, "recall": 0.72})
        assert result.passed is False
        assert "precision" in result.degraded_metrics

    def test_improvement_detected(self):
        """检测到改进。"""
        suite = RegressionTestSuite(improvement_threshold=0.05)
        suite.lock_baseline("v1", {"precision": 0.80})

        result = suite.check_regression("v1", {"precision": 0.90})
        assert result.passed is True
        assert "precision" in result.improved_metrics

    def test_multiple_metrics_mixed(self):
        """混合结果: 部分退化，部分改进。"""
        suite = RegressionTestSuite()
        suite.lock_baseline("v1", {
            "precision": 0.80,
            "recall": 0.70,
            "f1": 0.75,
        })

        result = suite.check_regression("v1", {
            "precision": 0.70,   # 退化 12.5%
            "recall": 0.85,     # 改进 21.4%
            "f1": 0.76,         # 基本不变
        })
        assert result.passed is False
        assert "precision" in result.degraded_metrics
        assert "recall" in result.improved_metrics
        assert "f1" in result.unchanged_metrics

    def test_missing_metric_in_current(self):
        """当前指标缺失: 不算退化。"""
        suite = RegressionTestSuite()
        suite.lock_baseline("v1", {"precision": 0.80, "recall": 0.70})

        result = suite.check_regression("v1", {"precision": 0.80})
        assert result.passed is True
        assert result.details["recall"] == "missing_in_current"

    def test_baseline_not_found(self):
        """Baseline 不存在: 通过 (graceful)。"""
        suite = RegressionTestSuite()
        result = suite.check_regression("nonexistent", {"precision": 0.5})
        assert result.passed is True
        assert "error" in result.details

    def test_check_multi_baselines(self):
        """多 baseline 检测。"""
        suite = RegressionTestSuite()
        suite.lock_baseline("v1", {"score": 0.7})
        suite.lock_baseline("v2", {"score": 0.8})

        results = suite.check_regression_multi({"score": 0.75})
        assert "v1" in results
        assert "v2" in results
        assert results["v1"].passed is True   # 0.75 > 0.7
        assert results["v2"].passed is False  # 0.75 < 0.8 (退化 6.25%)

    def test_zero_baseline_value(self):
        """Baseline 值为 0 的边界情况。"""
        suite = RegressionTestSuite()
        suite.lock_baseline("v1", {"errors": 0})

        result = suite.check_regression("v1", {"errors": 0})
        assert result.passed is True
        assert "errors" in result.unchanged_metrics

    def test_history_recording(self):
        """历史记录。"""
        suite = RegressionTestSuite()
        suite.lock_baseline("v1", {"score": 0.8})
        suite.check_regression("v1", {"score": 0.7})
        suite.check_regression("v1", {"score": 0.85})

        history = suite.get_history()
        assert len(history) == 2
        assert history[0]["passed"] is False
        assert history[1]["passed"] is True

    def test_serialize_deserialize(self):
        """序列化/反序列化。"""
        suite = RegressionTestSuite(degradation_threshold=0.1)
        suite.lock_baseline("v1", {"precision": 0.8}, description="first release")

        data = suite.serialize()
        suite2 = RegressionTestSuite()
        suite2.deserialize(data)

        assert suite2._degradation_threshold == 0.1
        baseline = suite2.get_baseline("v1")
        assert baseline is not None
        assert baseline.metrics["precision"] == 0.8
        assert baseline.description == "first release"

    def test_kill_switch_disabled(self):
        """Kill Switch 关闭时总是通过。"""
        with patch("core.meta_harness_complete.REGRESSION_TEST_ENABLED", False):
            suite = RegressionTestSuite()
            suite.lock_baseline("v1", {"score": 0.9})
            result = suite.check_regression("v1", {"score": 0.1})
            assert result.passed is True


# ================================================================
# Test: EvalDatasetBuilder
# ================================================================

class TestEvalDatasetBuilder:
    """EvalDatasetBuilder 测试套件。"""

    def test_add_manual_sample(self):
        """添加手动标注样本。"""
        builder = EvalDatasetBuilder()
        sample = builder.add_manual_sample(
            paper_text="The methodology section lacks detail...",
            expected_findings=[{"description": "Insufficient methodology"}],
            section="methodology",
            tags=["weak_methods"],
        )

        assert sample.source == "manual"
        assert sample.quality_score == 1.0
        assert sample.paper_section == "methodology"

    def test_add_from_historical(self):
        """从历史记录添加样本。"""
        builder = EvalDatasetBuilder()
        sample = builder.add_from_historical(
            paper_text="Results show p < 0.05 for all comparisons...",
            findings=[{"description": "P-hacking suspected", "severity": "major"}],
            section="results",
            quality=0.7,
        )

        assert sample is not None
        assert sample.source == "historical"
        assert sample.quality_score == 0.7

    def test_add_from_historical_empty(self):
        """空文本或 findings 不添加。"""
        builder = EvalDatasetBuilder()
        assert builder.add_from_historical("", []) is None
        assert builder.add_from_historical("text", []) is None
        assert builder.add_from_historical("", [{"d": "f"}]) is None

    def test_add_from_consensus(self):
        """多 Skill 共识创建样本。"""
        builder = EvalDatasetBuilder()
        sample = builder.add_from_consensus(
            paper_text="The sample size of 10 participants is quite small...",
            findings_from_multiple_skills=[
                [{"description": "small sample"}, {"description": "unique_a"}],
                [{"description": "small sample"}, {"description": "unique_b"}],
                [{"description": "small sample"}, {"description": "unique_c"}],
            ],
            agreement_threshold=0.6,
        )

        assert sample is not None
        assert sample.source == "expert_agreement"
        # "small sample" 出现在 3/3 skills = 100%
        assert len(sample.expected_findings) >= 1
        assert sample.expected_findings[0]["description"] == "small sample"

    def test_consensus_no_agreement(self):
        """无共识时不创建样本。"""
        builder = EvalDatasetBuilder()
        sample = builder.add_from_consensus(
            paper_text="some text",
            findings_from_multiple_skills=[
                [{"description": "only_a"}],
                [{"description": "only_b"}],
                [{"description": "only_c"}],
            ],
            agreement_threshold=0.6,
        )
        assert sample is None

    def test_get_dataset_filtering(self):
        """数据集过滤。"""
        builder = EvalDatasetBuilder()
        builder.add_manual_sample("text1", [{"d": "f1"}], section="methods", tags=["weak"])
        builder.add_manual_sample("text2", [{"d": "f2"}], section="results", tags=["stats"])
        builder.add_from_historical("text3", [{"d": "f3"}], section="methods", quality=0.5)

        # 按 section 过滤
        methods = builder.get_dataset(section_filter="methods")
        assert len(methods) == 2

        # 按 source 过滤
        manual = builder.get_dataset(source_filter="manual")
        assert len(manual) == 2

        # 按质量过滤
        high_quality = builder.get_dataset(min_quality=0.8)
        assert len(high_quality) == 2  # 只有 manual (quality=1.0)

        # 按 tag 过滤
        weak = builder.get_dataset(tag_filter="weak")
        assert len(weak) == 1

    def test_get_dataset_limit(self):
        """数据集 limit。"""
        builder = EvalDatasetBuilder()
        for i in range(10):
            builder.add_manual_sample(f"text_{i}", [{"d": f"f{i}"}])

        limited = builder.get_dataset(limit=3)
        assert len(limited) == 3

    def test_evict_lowest_quality(self):
        """超过 max 时移除最低质量。"""
        builder = EvalDatasetBuilder(max_samples=3)
        builder.add_from_historical("low", [{"d": "low"}], quality=0.1)
        builder.add_from_historical("mid", [{"d": "mid"}], quality=0.5)
        builder.add_from_historical("high", [{"d": "high"}], quality=0.9)
        # 第 4 个应触发逐出
        builder.add_from_historical("new", [{"d": "new"}], quality=0.6)

        dataset = builder.get_dataset()
        assert len(dataset) == 3
        # 最低质量的 (0.1) 应被移除
        qualities = [s.quality_score for s in dataset]
        assert 0.1 not in qualities

    def test_remove_sample(self):
        """移除样本。"""
        builder = EvalDatasetBuilder()
        sample = builder.add_manual_sample("text", [{"d": "f"}])
        assert builder.remove_sample(sample.sample_id) is True
        assert builder.get_dataset() == []

    def test_get_stats(self):
        """数据集统计。"""
        builder = EvalDatasetBuilder()
        builder.add_manual_sample("t1", [{"d": "f1"}], section="methods", tags=["a"])
        builder.add_from_historical("t2", [{"d": "f2"}], section="results", quality=0.6)

        stats = builder.get_stats()
        assert stats.total_samples == 2
        assert stats.by_source["manual"] == 1
        assert stats.by_source["historical"] == 1
        assert stats.by_section["methods"] == 1
        assert stats.by_section["results"] == 1
        assert stats.avg_quality == pytest.approx(0.8)  # (1.0 + 0.6) / 2

    def test_serialize_deserialize(self):
        """序列化/反序列化。"""
        builder = EvalDatasetBuilder()
        builder.add_manual_sample("sample text", [{"d": "finding"}], tags=["test"])

        data = builder.serialize()
        builder2 = EvalDatasetBuilder()
        builder2.deserialize(data)

        dataset = builder2.get_dataset()
        assert len(dataset) == 1
        assert dataset[0].source == "manual"
        assert "test" in dataset[0].tags

    def test_kill_switch_disabled(self):
        """Kill Switch 关闭时不添加。"""
        with patch("core.meta_harness_complete.EVAL_DATASET_ENABLED", False):
            builder = EvalDatasetBuilder()
            result = builder.add_sample(EvalSample(sample_id="x"))
            assert result is False
            assert builder.add_from_historical("text", [{"d": "f"}]) is None

    def test_consensus_single_skill(self):
        """单个 Skill 不能形成共识。"""
        builder = EvalDatasetBuilder()
        sample = builder.add_from_consensus(
            paper_text="text",
            findings_from_multiple_skills=[[{"description": "only one"}]],
        )
        assert sample is None


# ================================================================
# Test: MetaHarnessOrchestrator
# ================================================================

class TestMetaHarnessOrchestrator:
    """MetaHarnessOrchestrator 测试套件。"""

    def test_on_session_start_returns_config(self):
        """会话开始返回配置。"""
        searcher = ConfigSpaceSearcher()
        searcher.define_parameter(ConfigParameter("temp", "float", 0.0, 1.0, default=0.5))
        searcher.record_evaluation({"temp": 0.7}, score=0.9)

        harness = MetaHarnessOrchestrator(searcher=searcher)
        config = harness.on_session_start()
        assert "temp" in config
        assert config["temp"] == pytest.approx(0.7)

    def test_on_session_start_with_override(self):
        """会话开始可覆盖配置。"""
        harness = MetaHarnessOrchestrator()
        config = harness.on_session_start({"custom_key": True})
        assert config.get("custom_key") is True

    def test_on_skill_executed_records_feedback(self):
        """Skill 执行记录到优化器。"""
        optimizer = PromptOptimizer()
        optimizer.register_variant("v1", "template")

        harness = MetaHarnessOrchestrator(optimizer=optimizer)
        harness.on_skill_executed("check_methods", "v1", MagicMock(), score=0.8)

        v = optimizer.get_variant("v1")
        assert v.executions == 1
        assert v.score == pytest.approx(0.8)

    def test_on_session_end_regression_check(self):
        """会话结束: 回归检测。"""
        regression = RegressionTestSuite()
        regression.lock_baseline("v1", {"precision": 0.80})

        harness = MetaHarnessOrchestrator(regression=regression)
        result = harness.on_session_end(
            session_metrics={"precision": 0.70},
            baseline_id="v1",
        )

        assert "regression" in result
        assert result["regression"].passed is False
        assert "precision" in result["regression"].degraded_metrics

    def test_on_session_end_dataset_building(self):
        """会话结束: 数据集构建。"""
        builder = EvalDatasetBuilder()
        harness = MetaHarnessOrchestrator(dataset_builder=builder)

        result = harness.on_session_end(
            session_metrics={"confidence": 0.8},
            paper_text="Important methodology finding...",
            findings=[{"description": "Missing control group"}],
        )

        assert result.get("dataset_added") is True
        assert builder.get_stats().total_samples == 1

    def test_on_session_end_config_recording(self):
        """会话结束: 配置搜索记录。"""
        searcher = ConfigSpaceSearcher()
        searcher.define_parameter(ConfigParameter("temp", "float", 0, 1))
        harness = MetaHarnessOrchestrator(searcher=searcher)

        harness.on_session_end(
            session_metrics={"overall_score": 0.85, "config": {"temp": 0.6}},
        )

        result = searcher.get_best()
        assert result.best_score == pytest.approx(0.85)

    def test_get_status(self):
        """获取整体状态。"""
        harness = MetaHarnessOrchestrator()
        harness.optimizer.register_variant("v1", "t")
        harness.regression.lock_baseline("b1", {"x": 1})

        status = harness.get_status()
        assert "optimizer" in status
        assert "searcher" in status
        assert "regression_baselines" in status
        assert "b1" in status["regression_baselines"]
        assert "dataset_stats" in status
