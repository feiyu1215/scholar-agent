"""
test_skillx_complete.py — Phase 3 Complete Layer 全量测试

覆盖:
    - SkillOrchestrator: 串联/并联/条件组合，失败策略，条件跳过
    - SkillPerformanceTracker: 执行记录，质量贡献度，排名
    - SkillVersionManager: 版本注册，A/B 测试，round-robin 分配
"""

import os
import time
import pytest
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

from core.skillx_complete import (
    CompositionPlan,
    CompositionStep,
    CompositionType,
    OrchestrationResult,
    SkillOrchestrator,
    SkillPerformanceTracker,
    SkillPerformanceRecord,
    QualityContribution,
    SkillVersionManager,
    SkillVersion,
    ABComparisonResult,
)


# ================================================================
# Test Fixtures & Mocks
# ================================================================

@dataclass
class MockFinding:
    """模拟 Finding 对象。"""
    description: str = "test finding"
    severity: str = "major"
    confidence: float = 0.8


@dataclass
class MockSkillResult:
    """模拟 SkillResult 对象。"""
    success: bool = True
    findings: list = field(default_factory=list)
    tokens_used: int = 100
    execution_time_ms: float = 50.0
    output_data: dict = field(default_factory=dict)


@dataclass
class MockContext:
    """模拟 SkillContext 对象。"""
    parameters: dict = field(default_factory=dict)
    existing_findings: list = field(default_factory=list)


class MockSkill:
    """模拟 Skill 对象。"""
    def __init__(self, name: str = "test_skill"):
        self.name = name


class MockExecutor:
    """模拟 SkillExecutor。"""
    def __init__(self, results: Optional[List[MockSkillResult]] = None):
        self._results = results or [MockSkillResult()]
        self._call_index = 0
        self.run_calls: List = []

    def run(self, skill, context):
        self.run_calls.append((skill, context))
        if self._call_index < len(self._results):
            result = self._results[self._call_index]
        else:
            result = MockSkillResult()
        self._call_index += 1
        return result


class MockRegistry:
    """模拟 Skill Registry。"""
    def __init__(self, skills: Optional[Dict[str, Any]] = None):
        self._skills = skills or {}

    def get(self, name: str):
        return self._skills.get(name)


# ================================================================
# Test: SkillOrchestrator
# ================================================================

class TestSkillOrchestrator:
    """SkillOrchestrator 测试套件。"""

    def test_sequential_execution_basic(self):
        """基本串联: 3 个 Skill 依次执行。"""
        results = [
            MockSkillResult(findings=[MockFinding("f1")], output_data={"step": 1}),
            MockSkillResult(findings=[MockFinding("f2")], output_data={"step": 2}),
            MockSkillResult(findings=[MockFinding("f3")], output_data={"step": 3}),
        ]
        executor = MockExecutor(results)
        registry = MockRegistry({
            "skill_a": MockSkill("a"),
            "skill_b": MockSkill("b"),
            "skill_c": MockSkill("c"),
        })
        orch = SkillOrchestrator(executor, registry)

        plan = CompositionPlan(
            name="test_seq",
            composition_type=CompositionType.SEQUENTIAL,
            steps=[
                CompositionStep("skill_a"),
                CompositionStep("skill_b"),
                CompositionStep("skill_c"),
            ],
        )

        ctx = MockContext()
        result = orch.execute(plan, ctx)

        assert result.steps_executed == 3
        assert result.steps_skipped == 0
        assert result.steps_failed == 0
        assert len(result.merged_findings) == 3
        assert result.total_tokens == 300
        assert result.total_time_ms > 0

    def test_sequential_output_chaining(self):
        """串联: 前一个 output_data 传给后一个 context。"""
        results = [
            MockSkillResult(output_data={"key": "from_first"}),
            MockSkillResult(),
        ]
        executor = MockExecutor(results)
        registry = MockRegistry({
            "first": MockSkill("first"),
            "second": MockSkill("second"),
        })
        orch = SkillOrchestrator(executor, registry)

        plan = CompositionPlan(
            name="chain",
            composition_type=CompositionType.SEQUENTIAL,
            steps=[
                CompositionStep("first"),
                CompositionStep("second"),
            ],
        )
        ctx = MockContext()
        orch.execute(plan, ctx)

        # second skill 执行时 context 应包含 first 的 output
        _, second_ctx = executor.run_calls[1]
        assert second_ctx.parameters.get("key") == "from_first"

    def test_sequential_condition_skip(self):
        """串联: 条件不满足时跳过。"""
        results = [
            MockSkillResult(findings=[]),  # 无 findings
            MockSkillResult(findings=[MockFinding()]),  # 不应执行
        ]
        executor = MockExecutor(results)
        registry = MockRegistry({
            "step1": MockSkill("s1"),
            "step2": MockSkill("s2"),
        })
        orch = SkillOrchestrator(executor, registry)

        plan = CompositionPlan(
            name="cond_skip",
            composition_type=CompositionType.SEQUENTIAL,
            steps=[
                CompositionStep("step1"),
                CompositionStep("step2",
                    condition=lambda ctx, prev: len(prev[-1].findings) > 0),
            ],
        )
        ctx = MockContext()
        result = orch.execute(plan, ctx)

        assert result.steps_executed == 1
        assert result.steps_skipped == 1
        assert len(executor.run_calls) == 1

    def test_sequential_abort_on_failure(self):
        """串联: on_failure='abort' 时停止后续执行。"""
        results = [
            MockSkillResult(success=False),
            MockSkillResult(findings=[MockFinding()]),  # 不应执行
        ]
        executor = MockExecutor(results)
        registry = MockRegistry({
            "failing": MockSkill("f"),
            "after": MockSkill("a"),
        })
        orch = SkillOrchestrator(executor, registry)

        plan = CompositionPlan(
            name="abort_test",
            composition_type=CompositionType.SEQUENTIAL,
            steps=[
                CompositionStep("failing", on_failure="abort"),
                CompositionStep("after"),
            ],
        )
        ctx = MockContext()
        result = orch.execute(plan, ctx)

        assert result.success is False
        assert result.steps_failed == 1
        assert len(executor.run_calls) == 1

    def test_sequential_continue_on_failure(self):
        """串联: on_failure='continue' 时继续。"""
        results = [
            MockSkillResult(success=False),
            MockSkillResult(findings=[MockFinding("ok")]),
        ]
        executor = MockExecutor(results)
        registry = MockRegistry({
            "failing": MockSkill("f"),
            "after": MockSkill("a"),
        })
        orch = SkillOrchestrator(executor, registry)

        plan = CompositionPlan(
            name="continue_test",
            composition_type=CompositionType.SEQUENTIAL,
            steps=[
                CompositionStep("failing", on_failure="continue"),
                CompositionStep("after"),
            ],
        )
        ctx = MockContext()
        result = orch.execute(plan, ctx)

        assert result.steps_failed == 1
        assert result.steps_executed == 1
        assert len(executor.run_calls) == 2
        assert len(result.merged_findings) == 1

    def test_sequential_skill_not_found(self):
        """串联: Skill 不存在时跳过。"""
        executor = MockExecutor()
        registry = MockRegistry({})  # 空 registry
        orch = SkillOrchestrator(executor, registry)

        plan = CompositionPlan(
            name="not_found",
            composition_type=CompositionType.SEQUENTIAL,
            steps=[CompositionStep("nonexistent")],
        )
        ctx = MockContext()
        result = orch.execute(plan, ctx)

        assert result.steps_skipped == 1
        assert result.steps_executed == 0

    def test_parallel_execution_basic(self):
        """并联: 多个 Skill 独立执行并合并结果。"""
        results = [
            MockSkillResult(findings=[MockFinding("f1"), MockFinding("f2")]),
            MockSkillResult(findings=[MockFinding("f3")]),
            MockSkillResult(findings=[MockFinding("f4")]),
        ]
        executor = MockExecutor(results)
        registry = MockRegistry({
            "a": MockSkill("a"),
            "b": MockSkill("b"),
            "c": MockSkill("c"),
        })
        orch = SkillOrchestrator(executor, registry)

        plan = CompositionPlan(
            name="par_test",
            composition_type=CompositionType.PARALLEL,
            steps=[
                CompositionStep("a"),
                CompositionStep("b"),
                CompositionStep("c"),
            ],
        )
        ctx = MockContext()
        result = orch.execute(plan, ctx)

        assert result.steps_executed == 3
        assert len(result.merged_findings) == 4
        assert result.total_tokens == 300

    def test_parallel_isolation(self):
        """并联: 各分支共享原始 context，互不影响。"""
        results = [
            MockSkillResult(output_data={"from_a": True}),
            MockSkillResult(output_data={"from_b": True}),
        ]
        executor = MockExecutor(results)
        registry = MockRegistry({"a": MockSkill("a"), "b": MockSkill("b")})
        orch = SkillOrchestrator(executor, registry)

        plan = CompositionPlan(
            name="isolation",
            composition_type=CompositionType.PARALLEL,
            steps=[CompositionStep("a"), CompositionStep("b")],
        )
        ctx = MockContext(parameters={"original": True})
        result = orch.execute(plan, ctx)

        # 第二次调用时 context 不应包含第一次的输出
        _, ctx_for_b = executor.run_calls[1]
        assert "from_a" not in ctx_for_b.parameters
        assert ctx_for_b.parameters.get("original") is True

    def test_parallel_deduplicate_merge(self):
        """并联: deduplicate 策略去重。"""
        results = [
            MockSkillResult(findings=[MockFinding("same"), MockFinding("unique1")]),
            MockSkillResult(findings=[MockFinding("same"), MockFinding("unique2")]),
        ]
        executor = MockExecutor(results)
        registry = MockRegistry({"a": MockSkill("a"), "b": MockSkill("b")})
        orch = SkillOrchestrator(executor, registry)

        plan = CompositionPlan(
            name="dedup",
            composition_type=CompositionType.PARALLEL,
            steps=[CompositionStep("a"), CompositionStep("b")],
            merge_strategy="deduplicate",
        )
        ctx = MockContext()
        result = orch.execute(plan, ctx)

        # "same" 应只出现一次
        assert len(result.merged_findings) == 3

    def test_parallel_best_n_merge(self):
        """并联: best_n 策略取 top N。"""
        findings = [MockFinding(f"f{i}", confidence=i * 0.1) for i in range(8)]
        results = [
            MockSkillResult(findings=findings[:4]),
            MockSkillResult(findings=findings[4:]),
        ]
        executor = MockExecutor(results)
        registry = MockRegistry({"a": MockSkill("a"), "b": MockSkill("b")})
        orch = SkillOrchestrator(executor, registry)

        plan = CompositionPlan(
            name="best_n",
            composition_type=CompositionType.PARALLEL,
            steps=[CompositionStep("a"), CompositionStep("b")],
            merge_strategy="best_n",
            merge_top_n=3,
        )
        ctx = MockContext()
        result = orch.execute(plan, ctx)

        assert len(result.merged_findings) == 3
        # 按 confidence 降序排列
        assert result.merged_findings[0].confidence >= result.merged_findings[1].confidence

    def test_conditional_execution(self):
        """条件执行: 只有满足条件的 step 被执行。"""
        results = [MockSkillResult(findings=[MockFinding("only")])]
        executor = MockExecutor(results)
        registry = MockRegistry({
            "should_run": MockSkill("sr"),
            "should_skip": MockSkill("ss"),
        })
        orch = SkillOrchestrator(executor, registry)

        plan = CompositionPlan(
            name="cond",
            composition_type=CompositionType.CONDITIONAL,
            steps=[
                CompositionStep("should_run",
                    condition=lambda ctx, prev: True),
                CompositionStep("should_skip",
                    condition=lambda ctx, prev: False),
            ],
        )
        ctx = MockContext()
        result = orch.execute(plan, ctx)

        assert result.steps_executed == 1
        assert result.steps_skipped == 1
        assert len(executor.run_calls) == 1

    def test_plan_registration(self):
        """计划注册和按名称执行。"""
        results = [MockSkillResult(findings=[MockFinding("registered")])]
        executor = MockExecutor(results)
        registry = MockRegistry({"s": MockSkill("s")})
        orch = SkillOrchestrator(executor, registry)

        plan = CompositionPlan(
            name="registered_plan",
            composition_type=CompositionType.SEQUENTIAL,
            steps=[CompositionStep("s")],
        )
        orch.register_plan(plan)

        assert "registered_plan" in orch.list_plans()
        result = orch.execute_by_name("registered_plan", MockContext())
        assert result is not None
        assert result.steps_executed == 1

    def test_kill_switch_disabled(self):
        """Kill Switch 关闭时返回空结果。"""
        with patch("core.skillx_complete.SKILL_ORCHESTRATION_ENABLED", False):
            executor = MockExecutor()
            orch = SkillOrchestrator(executor, None)
            plan = CompositionPlan(
                name="disabled",
                composition_type=CompositionType.SEQUENTIAL,
                steps=[CompositionStep("any")],
            )
            result = orch.execute(plan, MockContext())
            assert result.success is True
            assert result.steps_executed == 0
            assert len(executor.run_calls) == 0

    def test_step_parameters_injection(self):
        """Step 参数注入到 context。"""
        results = [MockSkillResult()]
        executor = MockExecutor(results)
        registry = MockRegistry({"s": MockSkill("s")})
        orch = SkillOrchestrator(executor, registry)

        plan = CompositionPlan(
            name="params",
            composition_type=CompositionType.SEQUENTIAL,
            steps=[CompositionStep("s", parameters={"custom_key": "custom_val"})],
        )
        ctx = MockContext()
        orch.execute(plan, ctx)

        _, used_ctx = executor.run_calls[0]
        assert used_ctx.parameters.get("custom_key") == "custom_val"


# ================================================================
# Test: SkillPerformanceTracker
# ================================================================

class TestSkillPerformanceTracker:
    """SkillPerformanceTracker 测试套件。"""

    def test_basic_recording(self):
        """基本执行记录。"""
        tracker = SkillPerformanceTracker()
        result = MockSkillResult(
            findings=[MockFinding(severity="major"), MockFinding(severity="minor")],
            tokens_used=500,
            execution_time_ms=200.0,
        )
        tracker.on_skill_executed("test_skill", result, execution_time_ms=200.0)

        record = tracker.get_performance("test_skill")
        assert record is not None
        assert record.total_executions == 1
        assert record.successful_executions == 1
        assert record.total_findings_produced == 2
        assert record.total_tokens_consumed == 500
        assert record.findings_by_severity["major"] == 1
        assert record.findings_by_severity["minor"] == 1

    def test_multiple_executions(self):
        """多次执行累积统计。"""
        tracker = SkillPerformanceTracker()
        for i in range(5):
            result = MockSkillResult(
                findings=[MockFinding() for _ in range(i)],
                tokens_used=100,
            )
            tracker.on_skill_executed("multi", result)

        record = tracker.get_performance("multi")
        assert record.total_executions == 5
        assert record.total_findings_produced == 0 + 1 + 2 + 3 + 4  # 10
        assert record.total_tokens_consumed == 500

    def test_failed_execution(self):
        """失败执行的记录。"""
        tracker = SkillPerformanceTracker()
        result = MockSkillResult(success=False, findings=[])
        tracker.on_skill_executed("failing", result)

        record = tracker.get_performance("failing")
        assert record.total_executions == 1
        assert record.successful_executions == 0

    def test_findings_retained(self):
        """保留/丢弃记录。"""
        tracker = SkillPerformanceTracker()
        result = MockSkillResult(findings=[MockFinding() for _ in range(5)])
        tracker.on_skill_executed("skill", result)
        tracker.on_findings_retained("skill", retained_count=3, discarded_count=2)

        record = tracker.get_performance("skill")
        assert record.findings_retained == 3
        assert record.findings_discarded == 2

    def test_quality_contribution(self):
        """质量贡献度计算。"""
        tracker = SkillPerformanceTracker()
        for _ in range(10):
            result = MockSkillResult(
                findings=[MockFinding(severity="critical"), MockFinding(severity="major")],
                tokens_used=200,
            )
            tracker.on_skill_executed("high_quality", result)
        tracker.on_findings_retained("high_quality", retained_count=15, discarded_count=5)

        qc = tracker.compute_quality_contribution("high_quality")
        assert qc is not None
        assert qc.precision == 15 / 20  # 0.75
        assert qc.productivity == 2.0   # 20 findings / 10 executions
        assert qc.efficiency > 0
        assert qc.severity_weight > 1.0  # critical*3 + major*2
        assert 0 < qc.overall_score <= 1.0

    def test_quality_contribution_nonexistent(self):
        """不存在的 Skill 返回 None。"""
        tracker = SkillPerformanceTracker()
        assert tracker.compute_quality_contribution("ghost") is None

    def test_rankings(self):
        """排名功能。"""
        tracker = SkillPerformanceTracker()

        # High performer
        for _ in range(10):
            result = MockSkillResult(
                findings=[MockFinding(severity="critical") for _ in range(3)],
                tokens_used=100,
            )
            tracker.on_skill_executed("star", result)
        tracker.on_findings_retained("star", retained_count=25, discarded_count=5)

        # Low performer
        for _ in range(10):
            result = MockSkillResult(findings=[], tokens_used=500)
            tracker.on_skill_executed("weak", result)
        tracker.on_findings_retained("weak", retained_count=0, discarded_count=0)

        rankings = tracker.get_rankings()
        assert len(rankings) == 2
        assert rankings[0].skill_name == "star"
        assert rankings[1].skill_name == "weak"

    def test_recent_efficiency_window(self):
        """滑动窗口限制。"""
        tracker = SkillPerformanceTracker()
        for i in range(30):
            result = MockSkillResult(findings=[MockFinding()] * i)
            tracker.on_skill_executed("windowed", result)

        record = tracker.get_performance("windowed")
        assert len(record.recent_efficiency) == tracker.RECENT_WINDOW

    def test_serialize_deserialize(self):
        """序列化/反序列化一致性。"""
        tracker = SkillPerformanceTracker()
        result = MockSkillResult(
            findings=[MockFinding(severity="critical"), MockFinding(severity="minor")],
            tokens_used=300,
        )
        tracker.on_skill_executed("persist", result)
        tracker.on_findings_retained("persist", retained_count=1, discarded_count=1)

        data = tracker.serialize()
        assert len(data) == 1

        tracker2 = SkillPerformanceTracker()
        tracker2.deserialize(data)
        record = tracker2.get_performance("persist")
        assert record is not None
        assert record.total_executions == 1
        assert record.findings_retained == 1

    def test_kill_switch_disabled(self):
        """Kill Switch 关闭时不记录。"""
        with patch("core.skillx_complete.SKILL_PERFORMANCE_TRACKING_ENABLED", False):
            tracker = SkillPerformanceTracker()
            result = MockSkillResult(findings=[MockFinding()])
            tracker.on_skill_executed("disabled", result)
            assert tracker.get_performance("disabled") is None

    def test_get_all_records(self):
        """获取所有记录。"""
        tracker = SkillPerformanceTracker()
        tracker.on_skill_executed("a", MockSkillResult())
        tracker.on_skill_executed("b", MockSkillResult())

        records = tracker.get_all_records()
        assert "a" in records
        assert "b" in records


# ================================================================
# Test: SkillVersionManager
# ================================================================

class TestSkillVersionManager:
    """SkillVersionManager 测试套件。"""

    def test_register_and_get_active(self):
        """注册版本并获取活跃版本。"""
        manager = SkillVersionManager()
        skill_v1 = MockSkill("v1")
        manager.register_version("check_stats", "1.0", skill_v1)

        active = manager.get_active("check_stats")
        assert active is skill_v1

    def test_multiple_versions(self):
        """多版本注册，第一个为活跃。"""
        manager = SkillVersionManager()
        skill_v1 = MockSkill("v1")
        skill_v2 = MockSkill("v2")
        manager.register_version("skill", "1.0", skill_v1)
        manager.register_version("skill", "2.0", skill_v2)

        active = manager.get_active("skill")
        assert active is skill_v1  # 第一个注册的为活跃

    def test_set_active(self):
        """切换活跃版本。"""
        manager = SkillVersionManager()
        skill_v1 = MockSkill("v1")
        skill_v2 = MockSkill("v2")
        manager.register_version("skill", "1.0", skill_v1)
        manager.register_version("skill", "2.0", skill_v2)

        manager.set_active("skill", "2.0")
        active = manager.get_active("skill")
        assert active is skill_v2

    def test_list_versions(self):
        """列出版本信息。"""
        manager = SkillVersionManager()
        manager.register_version("skill", "1.0", MockSkill("v1"))
        manager.register_version("skill", "2.0", MockSkill("v2"))

        versions = manager.list_versions("skill")
        assert len(versions) == 2
        assert versions[0]["version"] == "1.0"
        assert versions[0]["is_active"] is True
        assert versions[1]["version"] == "2.0"
        assert versions[1]["is_active"] is False

    def test_start_ab_test(self):
        """启动 A/B 测试。"""
        manager = SkillVersionManager()
        manager.register_version("skill", "1.0", MockSkill("v1"))
        manager.register_version("skill", "2.0", MockSkill("v2"))

        success = manager.start_ab_test("skill", control="1.0", treatment="2.0")
        assert success is True

        status = manager.get_ab_status("skill")
        assert status is not None
        assert status["control"] == "1.0"
        assert status["treatment"] == "2.0"

    def test_ab_round_robin(self):
        """A/B 测试 round-robin 分配。"""
        manager = SkillVersionManager()
        skill_v1 = MockSkill("v1")
        skill_v2 = MockSkill("v2")
        manager.register_version("skill", "1.0", skill_v1)
        manager.register_version("skill", "2.0", skill_v2)
        manager.start_ab_test("skill", control="1.0", treatment="2.0")

        # 交替获取
        first = manager.get_active("skill")
        second = manager.get_active("skill")
        third = manager.get_active("skill")

        assert first is skill_v1   # control
        assert second is skill_v2  # treatment
        assert third is skill_v1   # control again

    def test_stop_ab_test(self):
        """停止 A/B 测试。"""
        tracker = SkillPerformanceTracker()
        manager = SkillVersionManager(tracker)
        manager.register_version("skill", "1.0", MockSkill("v1"))
        manager.register_version("skill", "2.0", MockSkill("v2"))
        manager.start_ab_test("skill", control="1.0", treatment="2.0")

        result = manager.stop_ab_test("skill")
        assert result is not None
        assert result.skill_name == "skill"
        assert manager.get_ab_status("skill") is None

    def test_ab_result_insufficient_data(self):
        """A/B 结果: 数据不足时 no_difference。"""
        tracker = SkillPerformanceTracker()
        # 模拟少量数据
        tracker.on_skill_executed("skill:v1.0", MockSkillResult(findings=[MockFinding()]))
        tracker.on_skill_executed("skill:v2.0", MockSkillResult(findings=[MockFinding()]))

        manager = SkillVersionManager(tracker)
        manager.register_version("skill", "1.0", MockSkill("v1"))
        manager.register_version("skill", "2.0", MockSkill("v2"))
        manager.start_ab_test("skill", control="1.0", treatment="2.0")

        result = manager.stop_ab_test("skill")
        assert result.winner == "no_difference"
        assert "样本不足" in result.recommendation

    def test_register_version_dedup(self):
        """重复注册同一版本号: 更新实例。"""
        manager = SkillVersionManager()
        skill_old = MockSkill("old")
        skill_new = MockSkill("new")
        manager.register_version("skill", "1.0", skill_old)
        manager.register_version("skill", "1.0", skill_new)

        versions = manager.list_versions("skill")
        assert len(versions) == 1
        # 活跃版本应为新实例
        active = manager.get_active("skill")
        assert active is skill_new

    def test_nonexistent_skill(self):
        """不存在的 Skill 返回 None。"""
        manager = SkillVersionManager()
        assert manager.get_active("ghost") is None

    def test_start_ab_invalid_version(self):
        """启动 A/B 测试失败: 版本不存在。"""
        manager = SkillVersionManager()
        manager.register_version("skill", "1.0", MockSkill("v1"))
        success = manager.start_ab_test("skill", control="1.0", treatment="9.9")
        assert success is False

    def test_kill_switch_disabled(self):
        """Kill Switch 关闭时版本管理无效。"""
        with patch("core.skillx_complete.SKILL_VERSION_MANAGEMENT_ENABLED", False):
            manager = SkillVersionManager()
            manager.register_version("skill", "1.0", MockSkill("v1"))
            assert manager.get_active("skill") is None
            assert manager.start_ab_test("skill", "1.0", "2.0") is False

    def test_serialize(self):
        """序列化版本管理状态。"""
        manager = SkillVersionManager()
        manager.register_version("skill", "1.0", MockSkill("v1"))
        manager.register_version("skill", "2.0", MockSkill("v2"))

        data = manager.serialize()
        assert "versions" in data
        assert "skill" in data["versions"]
        assert len(data["versions"]["skill"]) == 2

    def test_ab_test_with_full_tracker_data(self):
        """A/B 测试: 有完整 tracker 数据时的比较。"""
        tracker = SkillPerformanceTracker()

        # 模拟足够的执行次数
        for _ in range(10):
            ctrl_result = MockSkillResult(
                findings=[MockFinding(severity="minor")],
                tokens_used=200,
            )
            tracker.on_skill_executed("skill:v1.0", ctrl_result)

        for _ in range(10):
            treat_result = MockSkillResult(
                findings=[MockFinding(severity="critical"), MockFinding(severity="major")],
                tokens_used=200,
            )
            tracker.on_skill_executed("skill:v2.0", treat_result)

        tracker.on_findings_retained("skill:v1.0", retained_count=5, discarded_count=5)
        tracker.on_findings_retained("skill:v2.0", retained_count=15, discarded_count=5)

        manager = SkillVersionManager(tracker)
        manager.register_version("skill", "1.0", MockSkill("v1"))
        manager.register_version("skill", "2.0", MockSkill("v2"))
        manager.start_ab_test("skill", control="1.0", treatment="2.0")

        result = manager.stop_ab_test("skill")
        assert result.winner == "treatment"
        assert result.confidence > 0.5
        assert "2.0" in result.recommendation


# ================================================================
# Integration: Orchestrator + Tracker + VersionManager
# ================================================================

class TestIntegration:
    """集成测试: 三个模块协同工作。"""

    def test_orchestrator_with_tracker(self):
        """Orchestrator 执行时通过 Tracker 记录性能。"""
        tracker = SkillPerformanceTracker()
        results = [
            MockSkillResult(findings=[MockFinding(), MockFinding()]),
            MockSkillResult(findings=[MockFinding()]),
        ]
        executor = MockExecutor(results)
        registry = MockRegistry({"a": MockSkill("a"), "b": MockSkill("b")})
        orch = SkillOrchestrator(executor, registry)

        plan = CompositionPlan(
            name="tracked",
            composition_type=CompositionType.SEQUENTIAL,
            steps=[CompositionStep("a"), CompositionStep("b")],
        )
        ctx = MockContext()
        orch_result = orch.execute(plan, ctx)

        # 手动记录到 tracker (实际整合中 Orchestrator 会自动调用)
        for step_info in orch_result.step_results:
            mock_r = MockSkillResult(
                findings=[MockFinding()] * step_info["findings_count"],
                tokens_used=step_info["tokens_used"],
            )
            tracker.on_skill_executed(step_info["skill"], mock_r)

        assert tracker.get_performance("a").total_findings_produced == 2
        assert tracker.get_performance("b").total_findings_produced == 1

    def test_version_manager_with_orchestrator(self):
        """VersionManager 选择版本，Orchestrator 执行。"""
        manager = SkillVersionManager()
        skill_v1 = MockSkill("stats_v1")
        skill_v2 = MockSkill("stats_v2")
        manager.register_version("check_stats", "1.0", skill_v1)
        manager.register_version("check_stats", "2.0", skill_v2)
        manager.set_active("check_stats", "2.0")

        active = manager.get_active("check_stats")
        assert active is skill_v2

    def test_full_pipeline(self):
        """完整流水线: 版本选择 → 编排执行 → 性能记录。"""
        tracker = SkillPerformanceTracker()
        manager = SkillVersionManager(tracker)

        # 注册
        manager.register_version("extract", "1.0", MockSkill("extract_v1"))
        manager.register_version("verify", "1.0", MockSkill("verify_v1"))

        # 模拟执行
        results = [
            MockSkillResult(findings=[MockFinding("claim1"), MockFinding("claim2")]),
            MockSkillResult(findings=[MockFinding("verified")]),
        ]
        executor = MockExecutor(results)
        registry = MockRegistry({
            "extract": manager.get_active("extract"),
            "verify": manager.get_active("verify"),
        })
        orch = SkillOrchestrator(executor, registry)

        plan = CompositionPlan(
            name="extract_and_verify",
            composition_type=CompositionType.SEQUENTIAL,
            steps=[
                CompositionStep("extract"),
                CompositionStep("verify",
                    condition=lambda ctx, prev: len(prev[-1].findings) > 0),
            ],
        )

        orch_result = orch.execute(plan, MockContext())
        assert orch_result.steps_executed == 2
        assert len(orch_result.merged_findings) == 3

        # 记录性能
        tracker.on_skill_executed("extract", results[0])
        tracker.on_skill_executed("verify", results[1])
        tracker.on_findings_retained("extract", retained_count=2, discarded_count=0)
        tracker.on_findings_retained("verify", retained_count=1, discarded_count=0)

        # 验证贡献度
        qc = tracker.compute_quality_contribution("extract")
        assert qc.precision == 1.0
        assert qc.productivity == 2.0
