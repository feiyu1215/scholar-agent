"""
core/skillx_complete.py — Phase 3 Complete Layer: SkillX 系统完善版

三大功能模块:
    1. SkillOrchestrator (组合语法) — 串联/并联/条件组合
    2. SkillPerformanceTracker (性能追踪) — per-skill 统计 + 质量贡献度
    3. SkillVersionManager (版本管理 + A/B) — 版本注册 + A/B 比较框架

设计原则:
    - Kill Switch: 所有功能通过环境变量控制 (默认 ON)
    - 与现有 skills/ 子系统松耦合
    - 不修改 base.py/executor.py/selector.py，而是在其上构建
    - 渐进退化: 任何组件故障不影响基础 Skill 执行
"""

from __future__ import annotations

import copy
import logging
import os
import time
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional, Protocol

logger = logging.getLogger(__name__)


# ================================================================
# Kill Switches
# ================================================================

def _env_enabled(key: str, default: bool = True) -> bool:
    val = os.environ.get(key, "")
    if not val:
        return default
    return val.lower() not in ("0", "false", "off", "no", "disabled")


SKILL_ORCHESTRATION_ENABLED = _env_enabled("SCHOLAR_SKILL_ORCHESTRATION", True)
SKILL_PERFORMANCE_TRACKING_ENABLED = _env_enabled("SCHOLAR_SKILL_PERFORMANCE_TRACKING", True)
SKILL_VERSION_MANAGEMENT_ENABLED = _env_enabled("SCHOLAR_SKILL_VERSION_MANAGEMENT", True)


# ================================================================
# Module 1: Skill Orchestrator — 组合语法
# ================================================================

class CompositionType(Enum):
    """Skill 组合类型。"""
    SEQUENTIAL = "sequential"   # 串联: A → B → C (前一个输出是后一个输入)
    PARALLEL = "parallel"       # 并联: A | B | C (同时执行，合并结果)
    CONDITIONAL = "conditional" # 条件: IF condition THEN A ELSE B


@dataclass
class CompositionStep:
    """组合中的一个步骤。"""
    skill_name: str
    parameters: dict = field(default_factory=dict)
    # 条件执行: condition_fn(context, prev_results) -> bool
    condition: Optional[Callable] = None
    # 失败策略
    on_failure: str = "continue"  # "continue" | "abort" | "skip_rest"
    # 超时 (ms)
    timeout_ms: float = 30000.0


@dataclass
class CompositionPlan:
    """完整的组合执行计划。"""
    name: str
    composition_type: CompositionType
    steps: list[CompositionStep] = field(default_factory=list)
    # 并联时的结果合并策略
    merge_strategy: str = "union"  # "union" | "deduplicate" | "best_n"
    merge_top_n: int = 10
    description: str = ""


@dataclass
class OrchestrationResult:
    """组合执行的结果。"""
    plan_name: str
    composition_type: str
    step_results: list[dict] = field(default_factory=list)
    merged_findings: list[Any] = field(default_factory=list)
    total_time_ms: float = 0.0
    total_tokens: int = 0
    steps_executed: int = 0
    steps_skipped: int = 0
    steps_failed: int = 0
    success: bool = True


class SkillOrchestrator:
    """Skill 组合执行器: 实现串联/并联/条件组合语法。

    与 SkillExecutor.run_batch 的区别:
    - run_batch: 简单串行执行列表
    - SkillOrchestrator: 支持并联、条件分支、失败策略、结果合并

    Usage:
        orch = SkillOrchestrator(executor, skill_registry)

        # 定义组合计划
        plan = CompositionPlan(
            name="methodology_deep_check",
            composition_type=CompositionType.SEQUENTIAL,
            steps=[
                CompositionStep("extract_claims"),
                CompositionStep("check_methodology"),
                CompositionStep("verify_statistics",
                    condition=lambda ctx, prev: len(prev[-1].findings) > 0),
            ]
        )

        result = orch.execute(plan, context)
    """

    def __init__(self, executor: Any, skill_registry: Any = None):
        """
        Args:
            executor: SkillExecutor 实例
            skill_registry: Skill 注册表 (用于按名称查找 Skill)
        """
        self._executor = executor
        self._registry = skill_registry
        self._plans: dict[str, CompositionPlan] = {}

    def register_plan(self, plan: CompositionPlan) -> None:
        """注册一个组合计划。"""
        self._plans[plan.name] = plan

    def get_plan(self, name: str) -> CompositionPlan | None:
        """获取已注册的组合计划。"""
        return self._plans.get(name)

    def list_plans(self) -> list[str]:
        """列出所有已注册的计划名称。"""
        return list(self._plans.keys())

    def execute(
        self, plan: CompositionPlan, context: Any
    ) -> OrchestrationResult:
        """执行组合计划。

        Args:
            plan: 组合计划
            context: SkillContext

        Returns:
            OrchestrationResult
        """
        if not SKILL_ORCHESTRATION_ENABLED:
            return OrchestrationResult(
                plan_name=plan.name,
                composition_type=plan.composition_type.value,
                success=True,
            )

        start_time = time.time()

        if plan.composition_type == CompositionType.SEQUENTIAL:
            result = self._execute_sequential(plan, context)
        elif plan.composition_type == CompositionType.PARALLEL:
            result = self._execute_parallel(plan, context)
        elif plan.composition_type == CompositionType.CONDITIONAL:
            result = self._execute_conditional(plan, context)
        else:
            result = OrchestrationResult(
                plan_name=plan.name,
                composition_type=plan.composition_type.value,
                success=False,
            )

        result.total_time_ms = (time.time() - start_time) * 1000
        return result

    def execute_by_name(self, plan_name: str, context: Any) -> OrchestrationResult | None:
        """按名称执行已注册的计划。"""
        plan = self._plans.get(plan_name)
        if not plan:
            return None
        return self.execute(plan, context)

    def _execute_sequential(
        self, plan: CompositionPlan, context: Any
    ) -> OrchestrationResult:
        """串联执行: A → B → C，前一个输出传给后一个。"""
        result = OrchestrationResult(
            plan_name=plan.name,
            composition_type="sequential",
        )
        prev_results: list[Any] = []

        for step in plan.steps:
            # 条件检查
            if step.condition is not None:
                try:
                    should_run = step.condition(context, prev_results)
                except Exception:
                    should_run = True

                if not should_run:
                    result.steps_skipped += 1
                    result.step_results.append({
                        "skill": step.skill_name,
                        "status": "skipped",
                        "reason": "condition_false",
                    })
                    continue

            # 获取 Skill
            skill = self._resolve_skill(step.skill_name)
            if not skill:
                result.step_results.append({
                    "skill": step.skill_name,
                    "status": "skipped",
                    "reason": "skill_not_found",
                })
                result.steps_skipped += 1
                continue

            # 注入步骤参数
            if step.parameters:
                context.parameters.update(step.parameters)

            # 执行
            skill_result = self._executor.run(skill, context)
            prev_results.append(skill_result)

            step_info = {
                "skill": step.skill_name,
                "status": "success" if skill_result.success else "failed",
                "findings_count": len(skill_result.findings),
                "tokens_used": skill_result.tokens_used,
                "time_ms": skill_result.execution_time_ms,
            }
            result.step_results.append(step_info)
            result.total_tokens += skill_result.tokens_used

            if skill_result.success:
                result.steps_executed += 1
                result.merged_findings.extend(skill_result.findings)
                # 传递 output_data 给后续步骤
                if skill_result.output_data:
                    context.parameters.update(skill_result.output_data)
                # 累积 findings 到 context
                if skill_result.findings:
                    context.existing_findings.extend(skill_result.findings)
            else:
                result.steps_failed += 1
                if step.on_failure == "abort":
                    result.success = False
                    break
                elif step.on_failure == "skip_rest":
                    break

        return result

    def _execute_parallel(
        self, plan: CompositionPlan, context: Any
    ) -> OrchestrationResult:
        """并联执行: A | B | C，独立执行后合并结果。

        注意: 由于 Python GIL 和同步 Skill 接口，这里实际是"逻辑并联"——
        每个 Skill 独立获得原始 context（不受其他 Skill 输出影响），
        但仍然是顺序执行的。真正的并行需要 async 改造。
        """
        result = OrchestrationResult(
            plan_name=plan.name,
            composition_type="parallel",
        )
        all_findings = []

        # 保存原始 context state (deep copy 防止嵌套对象共享)
        original_params = copy.deepcopy(context.parameters)
        original_findings = copy.deepcopy(context.existing_findings)

        for step in plan.steps:
            # 恢复原始 context (并联各分支独立 — deep copy)
            context.parameters = copy.deepcopy(original_params)
            context.existing_findings = copy.deepcopy(original_findings)

            if step.parameters:
                context.parameters.update(step.parameters)

            skill = self._resolve_skill(step.skill_name)
            if not skill:
                result.steps_skipped += 1
                result.step_results.append({
                    "skill": step.skill_name,
                    "status": "skipped",
                    "reason": "skill_not_found",
                })
                continue

            skill_result = self._executor.run(skill, context)

            step_info = {
                "skill": step.skill_name,
                "status": "success" if skill_result.success else "failed",
                "findings_count": len(skill_result.findings),
                "tokens_used": skill_result.tokens_used,
                "time_ms": skill_result.execution_time_ms,
            }
            result.step_results.append(step_info)
            result.total_tokens += skill_result.tokens_used

            if skill_result.success:
                result.steps_executed += 1
                all_findings.extend(skill_result.findings)
            else:
                result.steps_failed += 1

        # 合并结果
        result.merged_findings = self._merge_findings(all_findings, plan)

        # 恢复 context
        context.parameters = original_params
        context.existing_findings = original_findings

        return result

    def _execute_conditional(
        self, plan: CompositionPlan, context: Any
    ) -> OrchestrationResult:
        """条件执行: 每个 step 有 condition，只执行满足条件的。"""
        result = OrchestrationResult(
            plan_name=plan.name,
            composition_type="conditional",
        )
        prev_results: list[Any] = []

        for step in plan.steps:
            if step.condition is not None:
                try:
                    should_run = step.condition(context, prev_results)
                except Exception:
                    should_run = False

                if not should_run:
                    result.steps_skipped += 1
                    result.step_results.append({
                        "skill": step.skill_name,
                        "status": "skipped",
                        "reason": "condition_false",
                    })
                    continue

            skill = self._resolve_skill(step.skill_name)
            if not skill:
                result.steps_skipped += 1
                continue

            if step.parameters:
                context.parameters.update(step.parameters)

            skill_result = self._executor.run(skill, context)
            prev_results.append(skill_result)

            result.step_results.append({
                "skill": step.skill_name,
                "status": "success" if skill_result.success else "failed",
                "findings_count": len(skill_result.findings),
            })
            result.total_tokens += skill_result.tokens_used

            if skill_result.success:
                result.steps_executed += 1
                result.merged_findings.extend(skill_result.findings)
            else:
                result.steps_failed += 1

        return result

    def _resolve_skill(self, name: str) -> Any | None:
        """按名称查找 Skill。"""
        if self._registry is None:
            return None
        # 尝试 registry 的 get 方法
        if hasattr(self._registry, "get"):
            return self._registry.get(name)
        # 或 query
        if hasattr(self._registry, "query"):
            results = self._registry.query(name=name)
            return results[0] if results else None
        return None

    def _merge_findings(self, findings: list, plan: CompositionPlan) -> list:
        """根据策略合并并联执行的 findings。"""
        if plan.merge_strategy == "union":
            return findings
        elif plan.merge_strategy == "deduplicate":
            seen = set()
            unique = []
            for f in findings:
                key = getattr(f, "description", str(f))
                if key not in seen:
                    seen.add(key)
                    unique.append(f)
            return unique
        elif plan.merge_strategy == "best_n":
            # 按 confidence 排序取 top N
            sorted_f = sorted(
                findings,
                key=lambda f: getattr(f, "confidence", 0.5),
                reverse=True,
            )
            return sorted_f[:plan.merge_top_n]
        return findings


# ================================================================
# Module 2: Skill Performance Tracker — 性能追踪
# ================================================================

@dataclass
class SkillPerformanceRecord:
    """单个 Skill 的累积性能统计。"""
    skill_name: str
    total_executions: int = 0
    successful_executions: int = 0
    total_findings_produced: int = 0
    total_tokens_consumed: int = 0
    total_time_ms: float = 0.0
    # 质量贡献度: 该 Skill 产出的 findings 中有多少被最终保留
    findings_retained: int = 0
    findings_discarded: int = 0
    # 按 severity 分类的 findings 计数
    findings_by_severity: dict = field(default_factory=lambda: {
        "critical": 0, "major": 0, "minor": 0, "suggestion": 0
    })
    # 历史趋势 (最近 N 次执行的 findings/execution)
    recent_efficiency: list[float] = field(default_factory=list)
    last_executed: str = ""


@dataclass
class QualityContribution:
    """Skill 的质量贡献度评估。"""
    skill_name: str
    precision: float = 0.0      # 保留率: retained / produced
    productivity: float = 0.0   # 生产力: findings / execution
    efficiency: float = 0.0     # 效率: findings / tokens
    severity_weight: float = 0.0 # 严重度加权: critical*3 + major*2 + minor*1
    overall_score: float = 0.0  # 综合评分


class SkillPerformanceTracker:
    """Skill 性能追踪器: 追踪每个 Skill 的执行统计和质量贡献度。

    生命周期:
    1. on_skill_executed(): 每次 Skill 执行后调用
    2. on_findings_retained(): 当 Findings 被最终报告保留时调用
    3. get_performance(): 获取单个 Skill 的性能报告
    4. get_rankings(): 获取所有 Skill 的排名

    Usage:
        tracker = SkillPerformanceTracker()
        tracker.on_skill_executed("check_methodology", result)
        # ... later when report is finalized ...
        tracker.on_findings_retained("check_methodology", retained_count=3, discarded_count=1)
        report = tracker.get_performance("check_methodology")
    """

    RECENT_WINDOW = 20  # 最近 N 次执行的滑动窗口

    def __init__(self):
        self._records: dict[str, SkillPerformanceRecord] = {}

    def on_skill_executed(
        self, skill_name: str, result: Any, execution_time_ms: float = 0.0
    ) -> None:
        """记录一次 Skill 执行。

        Args:
            skill_name: Skill 名称
            result: SkillResult 实例
            execution_time_ms: 执行时间
        """
        if not SKILL_PERFORMANCE_TRACKING_ENABLED:
            return

        record = self._get_or_create(skill_name)
        record.total_executions += 1

        if hasattr(result, "success") and result.success:
            record.successful_executions += 1

        findings_count = 0
        if hasattr(result, "findings"):
            findings_count = len(result.findings)
            record.total_findings_produced += findings_count

            # 按 severity 分类
            for f in result.findings:
                severity = getattr(f, "severity", "minor")
                if severity in record.findings_by_severity:
                    record.findings_by_severity[severity] += 1

        tokens = getattr(result, "tokens_used", 0)
        record.total_tokens_consumed += tokens
        record.total_time_ms += execution_time_ms or getattr(result, "execution_time_ms", 0)

        # 更新效率趋势
        record.recent_efficiency.append(float(findings_count))
        if len(record.recent_efficiency) > self.RECENT_WINDOW:
            record.recent_efficiency = record.recent_efficiency[-self.RECENT_WINDOW:]

        record.last_executed = datetime.now(timezone.utc).isoformat()

    def on_findings_retained(
        self, skill_name: str, retained_count: int, discarded_count: int = 0
    ) -> None:
        """记录 Skill 产出的 Findings 被保留/丢弃的情况。"""
        if not SKILL_PERFORMANCE_TRACKING_ENABLED:
            return

        record = self._get_or_create(skill_name)
        record.findings_retained += retained_count
        record.findings_discarded += discarded_count

    def get_performance(self, skill_name: str) -> SkillPerformanceRecord | None:
        """获取单个 Skill 的性能记录。"""
        return self._records.get(skill_name)

    def compute_quality_contribution(self, skill_name: str) -> QualityContribution | None:
        """计算 Skill 的质量贡献度。"""
        record = self._records.get(skill_name)
        if not record or record.total_executions == 0:
            return None

        # Precision: 保留率
        total_produced = record.findings_retained + record.findings_discarded
        precision = record.findings_retained / max(total_produced, 1)

        # Productivity: 每次执行产出
        productivity = record.total_findings_produced / record.total_executions

        # Efficiency: 每千 token 产出
        efficiency = (
            record.total_findings_produced / max(record.total_tokens_consumed / 1000, 0.1)
        )

        # Severity weight: 加权严重度
        severity_weights = {"critical": 3, "major": 2, "minor": 1, "suggestion": 0.5}
        weighted_sum = sum(
            count * severity_weights.get(sev, 1)
            for sev, count in record.findings_by_severity.items()
        )
        severity_weight = weighted_sum / max(record.total_findings_produced, 1)

        # Overall: 综合评分
        overall = (
            precision * 0.3
            + min(productivity / 3.0, 1.0) * 0.3
            + min(efficiency / 5.0, 1.0) * 0.2
            + min(severity_weight / 2.0, 1.0) * 0.2
        )

        return QualityContribution(
            skill_name=skill_name,
            precision=precision,
            productivity=productivity,
            efficiency=efficiency,
            severity_weight=severity_weight,
            overall_score=overall,
        )

    def get_rankings(self) -> list[QualityContribution]:
        """获取所有 Skill 按质量贡献度排名。"""
        contributions = []
        for name in self._records:
            qc = self.compute_quality_contribution(name)
            if qc:
                contributions.append(qc)
        return sorted(contributions, key=lambda c: c.overall_score, reverse=True)

    def get_all_records(self) -> dict[str, SkillPerformanceRecord]:
        """获取全部性能记录。"""
        return dict(self._records)

    def serialize(self) -> list[dict]:
        """序列化性能数据。"""
        return [
            {
                "skill_name": r.skill_name,
                "total_executions": r.total_executions,
                "successful_executions": r.successful_executions,
                "total_findings_produced": r.total_findings_produced,
                "total_tokens_consumed": r.total_tokens_consumed,
                "total_time_ms": r.total_time_ms,
                "findings_retained": r.findings_retained,
                "findings_discarded": r.findings_discarded,
                "findings_by_severity": r.findings_by_severity,
                "recent_efficiency": r.recent_efficiency,
                "last_executed": r.last_executed,
            }
            for r in self._records.values()
        ]

    def deserialize(self, data: list[dict]) -> None:
        """反序列化性能数据。"""
        self._records.clear()
        for item in data:
            name = item.get("skill_name", "")
            if not name:
                continue
            record = SkillPerformanceRecord(
                skill_name=name,
                total_executions=item.get("total_executions", 0),
                successful_executions=item.get("successful_executions", 0),
                total_findings_produced=item.get("total_findings_produced", 0),
                total_tokens_consumed=item.get("total_tokens_consumed", 0),
                total_time_ms=item.get("total_time_ms", 0.0),
                findings_retained=item.get("findings_retained", 0),
                findings_discarded=item.get("findings_discarded", 0),
                findings_by_severity=item.get("findings_by_severity", {}),
                recent_efficiency=item.get("recent_efficiency", []),
                last_executed=item.get("last_executed", ""),
            )
            self._records[name] = record

    def _get_or_create(self, skill_name: str) -> SkillPerformanceRecord:
        if skill_name not in self._records:
            self._records[skill_name] = SkillPerformanceRecord(skill_name=skill_name)
        return self._records[skill_name]


# ================================================================
# Module 3: Skill Version Manager + A/B — 版本管理
# ================================================================

@dataclass
class SkillVersion:
    """Skill 的一个版本。"""
    skill_name: str
    version: str
    skill_instance: Any          # Skill 实例
    registered_at: str = ""
    is_active: bool = True       # 当前是否为活跃版本
    is_ab_candidate: bool = False # 是否参与 A/B 比较
    ab_group: str = ""           # "control" | "treatment"


@dataclass
class ABComparisonResult:
    """A/B 比较的结果。"""
    skill_name: str
    control_version: str
    treatment_version: str
    # 统计
    control_executions: int = 0
    treatment_executions: int = 0
    control_findings_avg: float = 0.0
    treatment_findings_avg: float = 0.0
    control_precision: float = 0.0
    treatment_precision: float = 0.0
    # 结论
    winner: str = ""              # "control" | "treatment" | "no_difference"
    confidence: float = 0.0       # 统计置信度
    recommendation: str = ""      # 建议


class SkillVersionManager:
    """Skill 版本管理器: 支持多版本注册、A/B 比较、版本切换。

    用法:
        manager = SkillVersionManager()
        manager.register_version("check_stats", "1.0", skill_v1)
        manager.register_version("check_stats", "2.0", skill_v2)
        manager.start_ab_test("check_stats", control="1.0", treatment="2.0")

        # 获取当前要执行的版本
        skill = manager.get_active("check_stats")

        # 比较结果
        result = manager.get_ab_result("check_stats")
    """

    def __init__(self, tracker: SkillPerformanceTracker | None = None):
        """
        Args:
            tracker: 性能追踪器 (用于 A/B 比较时获取统计数据)
        """
        self._versions: dict[str, list[SkillVersion]] = {}  # name -> versions
        self._ab_tests: dict[str, dict] = {}  # name -> ab_config
        self._tracker = tracker
        self._ab_counter: dict[str, int] = {}  # name -> round-robin counter

    def register_version(
        self, skill_name: str, version: str, skill_instance: Any
    ) -> None:
        """注册一个 Skill 的新版本。"""
        if not SKILL_VERSION_MANAGEMENT_ENABLED:
            return

        now = datetime.now(timezone.utc).isoformat()
        sv = SkillVersion(
            skill_name=skill_name,
            version=version,
            skill_instance=skill_instance,
            registered_at=now,
            is_active=(len(self._versions.get(skill_name, [])) == 0),
        )

        if skill_name not in self._versions:
            self._versions[skill_name] = []

        # 去重
        existing_versions = [v.version for v in self._versions[skill_name]]
        if version in existing_versions:
            # 更新已有版本
            for i, v in enumerate(self._versions[skill_name]):
                if v.version == version:
                    self._versions[skill_name][i] = sv
                    return
        else:
            self._versions[skill_name].append(sv)

    def get_active(self, skill_name: str) -> Any | None:
        """获取当前活跃版本的 Skill 实例。

        如果正在进行 A/B 测试，使用 round-robin 分配。
        """
        if not SKILL_VERSION_MANAGEMENT_ENABLED:
            return None

        versions = self._versions.get(skill_name, [])
        if not versions:
            return None

        # 如果有 A/B 测试
        if skill_name in self._ab_tests:
            return self._get_ab_instance(skill_name)

        # 返回活跃版本
        for v in versions:
            if v.is_active:
                return v.skill_instance

        # 默认返回最后注册的
        return versions[-1].skill_instance

    def set_active(self, skill_name: str, version: str) -> bool:
        """设置活跃版本。"""
        versions = self._versions.get(skill_name, [])
        found = False
        for v in versions:
            v.is_active = (v.version == version)
            if v.is_active:
                found = True
        return found

    def list_versions(self, skill_name: str) -> list[dict]:
        """列出 Skill 的所有版本。"""
        versions = self._versions.get(skill_name, [])
        return [
            {
                "version": v.version,
                "is_active": v.is_active,
                "registered_at": v.registered_at,
                "is_ab_candidate": v.is_ab_candidate,
                "ab_group": v.ab_group,
            }
            for v in versions
        ]

    def start_ab_test(
        self, skill_name: str, control: str, treatment: str
    ) -> bool:
        """开始 A/B 测试。

        Args:
            skill_name: Skill 名称
            control: 对照组版本号
            treatment: 实验组版本号

        Returns:
            是否成功启动
        """
        if not SKILL_VERSION_MANAGEMENT_ENABLED:
            return False

        versions = self._versions.get(skill_name, [])
        control_v = next((v for v in versions if v.version == control), None)
        treatment_v = next((v for v in versions if v.version == treatment), None)

        if not control_v or not treatment_v:
            return False

        control_v.is_ab_candidate = True
        control_v.ab_group = "control"
        treatment_v.is_ab_candidate = True
        treatment_v.ab_group = "treatment"

        self._ab_tests[skill_name] = {
            "control": control,
            "treatment": treatment,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "control_runs": 0,
            "treatment_runs": 0,
        }
        self._ab_counter[skill_name] = 0

        return True

    def stop_ab_test(self, skill_name: str) -> ABComparisonResult | None:
        """停止 A/B 测试并返回结果。"""
        if skill_name not in self._ab_tests:
            return None

        config = self._ab_tests.pop(skill_name)
        self._ab_counter.pop(skill_name, None)

        # 清除 A/B 标记
        for v in self._versions.get(skill_name, []):
            v.is_ab_candidate = False
            v.ab_group = ""

        # 如果有 tracker，计算比较结果
        return self._compute_ab_result(
            skill_name, config["control"], config["treatment"]
        )

    def get_ab_status(self, skill_name: str) -> dict | None:
        """获取 A/B 测试状态。"""
        return self._ab_tests.get(skill_name)

    def _get_ab_instance(self, skill_name: str) -> Any | None:
        """A/B round-robin 分配。"""
        config = self._ab_tests[skill_name]
        counter = self._ab_counter.get(skill_name, 0)

        # 交替分配
        if counter % 2 == 0:
            target_version = config["control"]
            config["control_runs"] = config.get("control_runs", 0) + 1
        else:
            target_version = config["treatment"]
            config["treatment_runs"] = config.get("treatment_runs", 0) + 1

        self._ab_counter[skill_name] = counter + 1

        versions = self._versions.get(skill_name, [])
        for v in versions:
            if v.version == target_version:
                return v.skill_instance

        return None

    def _compute_ab_result(
        self, skill_name: str, control_ver: str, treatment_ver: str
    ) -> ABComparisonResult:
        """计算 A/B 比较结果。"""
        result = ABComparisonResult(
            skill_name=skill_name,
            control_version=control_ver,
            treatment_version=treatment_ver,
        )

        if not self._tracker:
            result.winner = "no_difference"
            result.recommendation = "无性能追踪数据，无法判断"
            return result

        # 从 tracker 获取两个版本的性能数据
        # 使用 "skill_name:version" 格式的 key 区分
        control_key = f"{skill_name}:v{control_ver}"
        treatment_key = f"{skill_name}:v{treatment_ver}"

        control_record = self._tracker.get_performance(control_key)
        treatment_record = self._tracker.get_performance(treatment_key)

        if control_record:
            result.control_executions = control_record.total_executions
            result.control_findings_avg = (
                control_record.total_findings_produced / max(control_record.total_executions, 1)
            )
            total_ctrl = control_record.findings_retained + control_record.findings_discarded
            result.control_precision = control_record.findings_retained / max(total_ctrl, 1)

        if treatment_record:
            result.treatment_executions = treatment_record.total_executions
            result.treatment_findings_avg = (
                treatment_record.total_findings_produced / max(treatment_record.total_executions, 1)
            )
            total_treat = treatment_record.findings_retained + treatment_record.findings_discarded
            result.treatment_precision = treatment_record.findings_retained / max(total_treat, 1)

        # 简单判定逻辑（实际应做统计检验，但此处用启发式）
        min_runs = 5
        if result.control_executions < min_runs or result.treatment_executions < min_runs:
            result.winner = "no_difference"
            result.confidence = 0.0
            result.recommendation = f"样本不足 (需要至少 {min_runs} 次执行)"
        else:
            ctrl_score = result.control_findings_avg * 0.5 + result.control_precision * 0.5
            treat_score = result.treatment_findings_avg * 0.5 + result.treatment_precision * 0.5
            diff = treat_score - ctrl_score

            if abs(diff) < 0.1:
                result.winner = "no_difference"
                result.confidence = 0.3
                result.recommendation = "两版本差异不显著，建议继续观察"
            elif diff > 0:
                result.winner = "treatment"
                result.confidence = min(0.5 + abs(diff), 0.95)
                result.recommendation = f"建议升级到 v{treatment_ver}"
            else:
                result.winner = "control"
                result.confidence = min(0.5 + abs(diff), 0.95)
                result.recommendation = f"保留当前 v{control_ver}，新版本表现更差"

        return result

    def serialize(self) -> dict:
        """序列化版本管理状态。"""
        return {
            "versions": {
                name: [
                    {"version": v.version, "is_active": v.is_active, "registered_at": v.registered_at}
                    for v in versions
                ]
                for name, versions in self._versions.items()
            },
            "ab_tests": dict(self._ab_tests),
        }
