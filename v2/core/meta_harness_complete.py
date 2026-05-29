"""
core/meta_harness_complete.py — Phase 5 Complete Layer: Meta-Harness 自我进化引擎

四大功能模块:
    1. PromptOptimizer (自动 Prompt 优化) — DSPy 风格反馈循环
    2. ConfigSpaceSearcher (配置空间搜索) — 超参搜索 + 贝叶斯优化
    3. RegressionTestSuite (回归测试套件) — baseline 锁定 + 退化检测
    4. EvalDatasetBuilder (评估数据集构建) — 从审稿历史中提取高质量样本

设计原则:
    - Kill Switch: 所有功能通过环境变量控制 (默认 ON)
    - 无侵入: 不修改已有模块，以 hook/observer 模式接入
    - 可持久化: 所有状态支持 serialize/deserialize
    - 渐进退化: 优化器故障不影响基础审稿功能
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ================================================================
# Kill Switches
# ================================================================

def _env_enabled(key: str, default: bool = True) -> bool:
    val = os.environ.get(key, "")
    if not val:
        return default
    return val.lower() not in ("0", "false", "off", "no", "disabled")


PROMPT_OPTIMIZATION_ENABLED = _env_enabled("SCHOLAR_PROMPT_OPTIMIZATION", True)
CONFIG_SEARCH_ENABLED = _env_enabled("SCHOLAR_CONFIG_SEARCH", True)
REGRESSION_TEST_ENABLED = _env_enabled("SCHOLAR_REGRESSION_TEST", True)
EVAL_DATASET_ENABLED = _env_enabled("SCHOLAR_EVAL_DATASET", True)


# ================================================================
# Module 1: PromptOptimizer — DSPy 风格反馈循环
# ================================================================

@dataclass
class PromptVariant:
    """一个 Prompt 变体。"""
    variant_id: str
    template: str           # prompt template (可含 {variable} 占位符)
    variables: dict = field(default_factory=dict)
    # 性能指标
    score: float = 0.0
    executions: int = 0
    total_score: float = 0.0
    # 元数据
    created_at: str = ""
    parent_id: str = ""     # 源自哪个变体


@dataclass
class OptimizationFeedback:
    """单次执行的反馈信号。"""
    variant_id: str
    score: float            # [0, 1] 分数
    metrics: dict = field(default_factory=dict)  # 具体指标
    error: str = ""         # 错误信息 (如果有)


@dataclass
class OptimizationHistory:
    """优化历史记录。"""
    iteration: int
    variant_id: str
    score: float
    timestamp: str = ""


class PromptOptimizer:
    """DSPy 风格的自动 Prompt 优化器。

    核心流程:
    1. 注册初始 Prompt 变体
    2. 执行后收集反馈 (score)
    3. 根据反馈生成改进变体
    4. 评估新变体，保留最优

    变体生成策略:
    - 随机扰动: 修改 template 中的关键词
    - 变量搜索: 调整 variables 参数
    - LLM 改写: 让 LLM 基于反馈改写 prompt (需外部 LLM 接口)

    Usage:
        optimizer = PromptOptimizer(max_variants=10)
        optimizer.register_variant("v1", template="Review {section} for {criteria}")
        optimizer.record_feedback(OptimizationFeedback("v1", score=0.7))
        best = optimizer.get_best_variant()
    """

    def __init__(
        self,
        max_variants: int = 20,
        min_executions_for_comparison: int = 3,
        improvement_threshold: float = 0.05,
    ):
        self._variants: dict[str, PromptVariant] = {}
        self._history: list[OptimizationHistory] = []
        self._iteration: int = 0
        self._max_variants = max_variants
        self._min_executions = min_executions_for_comparison
        self._improvement_threshold = improvement_threshold

    def register_variant(
        self,
        variant_id: str,
        template: str,
        variables: dict | None = None,
        parent_id: str = "",
    ) -> PromptVariant:
        """注册一个 Prompt 变体。"""
        if not PROMPT_OPTIMIZATION_ENABLED:
            return PromptVariant(variant_id=variant_id, template=template)

        variant = PromptVariant(
            variant_id=variant_id,
            template=template,
            variables=variables or {},
            created_at=datetime.now(timezone.utc).isoformat(),
            parent_id=parent_id,
        )
        self._variants[variant_id] = variant

        # 限制总数
        if len(self._variants) > self._max_variants:
            self._prune_worst()

        return variant

    def record_feedback(self, feedback: OptimizationFeedback) -> None:
        """记录一次执行的反馈分数。"""
        if not PROMPT_OPTIMIZATION_ENABLED:
            return

        variant = self._variants.get(feedback.variant_id)
        if not variant:
            return

        variant.executions += 1
        variant.total_score += feedback.score
        variant.score = variant.total_score / variant.executions

        self._iteration += 1
        self._history.append(OptimizationHistory(
            iteration=self._iteration,
            variant_id=feedback.variant_id,
            score=feedback.score,
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))

    def get_best_variant(self) -> PromptVariant | None:
        """获取当前最优变体 (需满足最小执行次数)。"""
        candidates = [
            v for v in self._variants.values()
            if v.executions >= self._min_executions
        ]
        if not candidates:
            # 如果没有满足条件的，返回执行次数最多的
            all_variants = list(self._variants.values())
            return max(all_variants, key=lambda v: v.executions) if all_variants else None

        return max(candidates, key=lambda v: v.score)

    def get_variant(self, variant_id: str) -> PromptVariant | None:
        """获取指定变体。"""
        return self._variants.get(variant_id)

    def suggest_next_variant(
        self,
        mutation_fn: Callable[[str, dict], tuple[str, dict]] | None = None,
    ) -> PromptVariant | None:
        """基于当前最优变体建议下一个要尝试的变体。

        Args:
            mutation_fn: 自定义变异函数 (template, variables) -> (new_template, new_variables)
                         如果不提供，使用内置简单变异

        Returns:
            新创建的变体 (已注册)，或 None (如果无法建议)
        """
        if not PROMPT_OPTIMIZATION_ENABLED:
            return None

        best = self.get_best_variant()
        if not best:
            return None

        if mutation_fn:
            new_template, new_variables = mutation_fn(best.template, best.variables)
        else:
            new_template, new_variables = self._default_mutation(best.template, best.variables)

        new_id = f"v{self._iteration + 1}_{hashlib.md5(new_template.encode()).hexdigest()[:6]}"

        return self.register_variant(
            variant_id=new_id,
            template=new_template,
            variables=new_variables,
            parent_id=best.variant_id,
        )

    def should_explore(self) -> bool:
        """判断是否应该探索新变体 (vs 利用当前最优)。

        使用 epsilon-greedy: 随时间减少探索率。
        """
        epsilon = max(0.1, 1.0 - self._iteration * 0.05)
        return random.random() < epsilon

    def get_optimization_report(self) -> dict:
        """获取优化报告。

        best_variant 与 get_best_variant() 逻辑一致：
        优先选择满足最小执行次数的最高分变体。
        """
        # 使用与 get_best_variant 一致的逻辑
        best = self.get_best_variant()
        best_id = best.variant_id if best else None
        best_score = best.score if best else 0.0

        sorted_variants = sorted(
            self._variants.values(),
            key=lambda v: v.score,
            reverse=True,
        )
        return {
            "total_iterations": self._iteration,
            "total_variants": len(self._variants),
            "best_variant": best_id,
            "best_score": best_score,
            "variants": [
                {
                    "id": v.variant_id,
                    "score": v.score,
                    "executions": v.executions,
                    "parent": v.parent_id,
                }
                for v in sorted_variants[:10]
            ],
            "convergence": self._check_convergence(),
        }

    def serialize(self) -> dict:
        """序列化优化器状态。"""
        return {
            "variants": {
                vid: {
                    "template": v.template,
                    "variables": v.variables,
                    "score": v.score,
                    "executions": v.executions,
                    "total_score": v.total_score,
                    "created_at": v.created_at,
                    "parent_id": v.parent_id,
                }
                for vid, v in self._variants.items()
            },
            "history": [
                {"iteration": h.iteration, "variant_id": h.variant_id,
                 "score": h.score, "timestamp": h.timestamp}
                for h in self._history[-100:]  # 保留最近 100 条
            ],
            "iteration": self._iteration,
        }

    def deserialize(self, data: dict) -> None:
        """反序列化。"""
        self._variants.clear()
        self._history.clear()

        for vid, vdata in data.get("variants", {}).items():
            self._variants[vid] = PromptVariant(
                variant_id=vid,
                template=vdata.get("template", ""),
                variables=vdata.get("variables", {}),
                score=vdata.get("score", 0.0),
                executions=vdata.get("executions", 0),
                total_score=vdata.get("total_score", 0.0),
                created_at=vdata.get("created_at", ""),
                parent_id=vdata.get("parent_id", ""),
            )

        for hdata in data.get("history", []):
            self._history.append(OptimizationHistory(
                iteration=hdata.get("iteration", 0),
                variant_id=hdata.get("variant_id", ""),
                score=hdata.get("score", 0.0),
                timestamp=hdata.get("timestamp", ""),
            ))

        self._iteration = data.get("iteration", 0)

    def _default_mutation(self, template: str, variables: dict) -> tuple[str, dict]:
        """内置简单变异: 对 variables 进行微调。"""
        new_vars = dict(variables)
        if new_vars:
            # 随机选一个 variable 进行变异
            key = random.choice(list(new_vars.keys()))
            val = new_vars[key]
            if isinstance(val, (int, float)):
                # 数值: ±10% 扰动
                perturbation = val * 0.1 * (2 * random.random() - 1)
                new_vars[key] = val + perturbation
            elif isinstance(val, str):
                # 字符串: 在末尾添加标记 (简单策略)
                new_vars[key] = val + " [refined]"
        return template, new_vars

    def _prune_worst(self) -> None:
        """移除表现最差的变体。

        策略: 优先从已有足够执行次数的变体中选最差的删除。
        如果没有满足条件的，删除最老的变体 (非当前最新注册的)。
        """
        # 优先删除已评估过且分数最低的
        evaluated = [
            v for v in self._variants.values()
            if v.executions >= self._min_executions
        ]
        if evaluated:
            worst = min(evaluated, key=lambda v: v.score)
            del self._variants[worst.variant_id]
        else:
            # 都没评估过，删除最老注册的 (按 created_at 排序)
            all_variants = sorted(
                self._variants.values(),
                key=lambda v: v.created_at or "",
            )
            if all_variants:
                del self._variants[all_variants[0].variant_id]

    def _check_convergence(self) -> dict:
        """检查是否收敛。"""
        if len(self._history) < 10:
            return {"converged": False, "reason": "insufficient_data"}

        recent = self._history[-10:]
        scores = [h.score for h in recent]
        variance = sum((s - sum(scores) / len(scores)) ** 2 for s in scores) / len(scores)

        return {
            "converged": variance < 0.01,
            "variance": variance,
            "recent_avg": sum(scores) / len(scores),
        }


# ================================================================
# Module 2: ConfigSpaceSearcher — 配置空间搜索
# ================================================================

@dataclass
class ConfigParameter:
    """一个配置参数的定义。"""
    name: str
    param_type: str = "float"   # "float" | "int" | "categorical"
    min_val: float = 0.0
    max_val: float = 1.0
    choices: list = field(default_factory=list)  # for categorical
    default: Any = None


@dataclass
class ConfigPoint:
    """配置空间中的一个点。"""
    point_id: str
    config: dict          # parameter_name -> value
    score: float = 0.0
    evaluated: bool = False
    timestamp: str = ""


@dataclass
class SearchResult:
    """搜索结果。"""
    best_config: dict
    best_score: float
    total_evaluations: int
    search_path: list[dict] = field(default_factory=list)


class ConfigSpaceSearcher:
    """配置空间搜索器: 支持随机搜索和贝叶斯优化风格的搜索。

    搜索策略:
    1. 随机搜索 (初始探索)
    2. 局部搜索 (围绕最优点探索)
    3. UCB (Upper Confidence Bound) 风格的平衡策略

    配置参数包括:
    - Skill 相关: temperature, max_findings, confidence_threshold
    - Memory 相关: decay_rate, retrieval_top_k
    - Guard 相关: max_iterations, patience

    Usage:
        searcher = ConfigSpaceSearcher()
        searcher.define_parameter(ConfigParameter("temperature", "float", 0.0, 1.0))
        searcher.define_parameter(ConfigParameter("max_findings", "int", 1, 20))

        config = searcher.suggest_next()
        score = evaluate(config)
        searcher.record_evaluation(config, score)

        best = searcher.get_best()
    """

    def __init__(
        self,
        n_initial_random: int = 10,
        exploitation_ratio: float = 0.7,
    ):
        self._parameters: dict[str, ConfigParameter] = {}
        self._evaluated_points: list[ConfigPoint] = []
        self._n_initial = n_initial_random
        self._exploitation_ratio = exploitation_ratio

    def define_parameter(self, param: ConfigParameter) -> None:
        """定义一个搜索参数。"""
        self._parameters[param.name] = param

    def get_parameter(self, name: str) -> ConfigParameter | None:
        """获取参数定义。"""
        return self._parameters.get(name)

    def list_parameters(self) -> list[str]:
        """列出所有参数名。"""
        return list(self._parameters.keys())

    def suggest_next(self) -> dict:
        """建议下一个要评估的配置点。

        策略:
        - 前 N 次: 随机采样 (拉丁超立方)
        - 之后: exploitation_ratio 概率做局部搜索，其余随机
        """
        if not CONFIG_SEARCH_ENABLED:
            return self._get_default_config()

        if len(self._evaluated_points) < self._n_initial:
            return self._random_sample()

        if random.random() < self._exploitation_ratio:
            return self._local_search()
        else:
            return self._random_sample()

    def record_evaluation(self, config: dict, score: float) -> None:
        """记录一次评估结果。"""
        if not CONFIG_SEARCH_ENABLED:
            return

        point_id = hashlib.md5(json.dumps(config, sort_keys=True).encode()).hexdigest()[:8]
        point = ConfigPoint(
            point_id=point_id,
            config=config,
            score=score,
            evaluated=True,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._evaluated_points.append(point)

    def get_best(self) -> SearchResult:
        """获取当前最优配置。"""
        if not self._evaluated_points:
            return SearchResult(
                best_config=self._get_default_config(),
                best_score=0.0,
                total_evaluations=0,
            )

        best = max(self._evaluated_points, key=lambda p: p.score)
        return SearchResult(
            best_config=best.config,
            best_score=best.score,
            total_evaluations=len(self._evaluated_points),
            search_path=[
                {"config": p.config, "score": p.score}
                for p in self._evaluated_points[-20:]
            ],
        )

    def get_top_n(self, n: int = 5) -> list[ConfigPoint]:
        """获取 top N 配置。"""
        sorted_points = sorted(self._evaluated_points, key=lambda p: p.score, reverse=True)
        return sorted_points[:n]

    def get_search_stats(self) -> dict:
        """获取搜索统计。"""
        if not self._evaluated_points:
            return {"total_evaluations": 0, "parameters": len(self._parameters)}

        scores = [p.score for p in self._evaluated_points]
        return {
            "total_evaluations": len(self._evaluated_points),
            "parameters": len(self._parameters),
            "best_score": max(scores),
            "worst_score": min(scores),
            "mean_score": sum(scores) / len(scores),
            "improvement_rate": self._compute_improvement_rate(),
        }

    def serialize(self) -> dict:
        """序列化搜索状态。"""
        return {
            "parameters": {
                name: {
                    "param_type": p.param_type,
                    "min_val": p.min_val,
                    "max_val": p.max_val,
                    "choices": p.choices,
                    "default": p.default,
                }
                for name, p in self._parameters.items()
            },
            "evaluated_points": [
                {"config": p.config, "score": p.score, "timestamp": p.timestamp}
                for p in self._evaluated_points[-50:]
            ],
        }

    def deserialize(self, data: dict) -> None:
        """反序列化。"""
        self._parameters.clear()
        self._evaluated_points.clear()

        for name, pdata in data.get("parameters", {}).items():
            self._parameters[name] = ConfigParameter(
                name=name,
                param_type=pdata.get("param_type", "float"),
                min_val=pdata.get("min_val", 0.0),
                max_val=pdata.get("max_val", 1.0),
                choices=pdata.get("choices", []),
                default=pdata.get("default"),
            )

        for pdata in data.get("evaluated_points", []):
            self._evaluated_points.append(ConfigPoint(
                point_id=hashlib.md5(
                    json.dumps(pdata["config"], sort_keys=True).encode()
                ).hexdigest()[:8],
                config=pdata["config"],
                score=pdata["score"],
                evaluated=True,
                timestamp=pdata.get("timestamp", ""),
            ))

    def _random_sample(self) -> dict:
        """随机采样一个配置点。"""
        config = {}
        for name, param in self._parameters.items():
            if param.param_type == "float":
                config[name] = random.uniform(param.min_val, param.max_val)
            elif param.param_type == "int":
                config[name] = random.randint(int(param.min_val), int(param.max_val))
            elif param.param_type == "categorical":
                config[name] = random.choice(param.choices) if param.choices else None
        return config

    def _local_search(self) -> dict:
        """围绕最优点进行局部搜索。"""
        if not self._evaluated_points:
            return self._random_sample()

        best = max(self._evaluated_points, key=lambda p: p.score)
        config = dict(best.config)

        # 对每个参数做小扰动
        for name, param in self._parameters.items():
            if name not in config:
                continue
            if param.param_type == "float":
                range_size = param.max_val - param.min_val
                perturbation = range_size * 0.1 * (2 * random.random() - 1)
                new_val = config[name] + perturbation
                config[name] = max(param.min_val, min(param.max_val, new_val))
            elif param.param_type == "int":
                delta = max(1, int((param.max_val - param.min_val) * 0.1))
                new_val = config[name] + random.randint(-delta, delta)
                config[name] = max(int(param.min_val), min(int(param.max_val), new_val))
            elif param.param_type == "categorical":
                # 10% 概率切换
                if random.random() < 0.1 and param.choices:
                    config[name] = random.choice(param.choices)

        return config

    def _get_default_config(self) -> dict:
        """获取默认配置。"""
        config = {}
        for name, param in self._parameters.items():
            if param.default is not None:
                config[name] = param.default
            elif param.param_type == "float":
                config[name] = (param.min_val + param.max_val) / 2
            elif param.param_type == "int":
                config[name] = int((param.min_val + param.max_val) / 2)
            elif param.param_type == "categorical":
                config[name] = param.choices[0] if param.choices else None
        return config

    def _compute_improvement_rate(self) -> float:
        """计算改进率 (最近一半 vs 前一半的平均分差)。"""
        n = len(self._evaluated_points)
        if n < 4:
            return 0.0
        mid = n // 2
        first_half = sum(p.score for p in self._evaluated_points[:mid]) / mid
        second_half = sum(p.score for p in self._evaluated_points[mid:]) / (n - mid)
        return second_half - first_half


# ================================================================
# Module 3: RegressionTestSuite — 回归测试
# ================================================================

@dataclass
class BaselineSnapshot:
    """一个基准快照。"""
    snapshot_id: str
    metrics: dict          # metric_name -> value
    config: dict = field(default_factory=dict)
    created_at: str = ""
    description: str = ""


@dataclass
class RegressionCheckResult:
    """回归检测结果。"""
    passed: bool
    degraded_metrics: list[str] = field(default_factory=list)
    improved_metrics: list[str] = field(default_factory=list)
    unchanged_metrics: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


class RegressionTestSuite:
    """回归测试套件: 锁定 baseline，检测性能退化。

    工作流程:
    1. 建立 baseline (锁定当前指标)
    2. 每次优化/变更后运行回归检查
    3. 检测退化指标并报警
    4. 支持多 baseline 比较

    指标类型:
    - 精度: findings_precision, severity_accuracy
    - 召回: findings_recall, coverage_rate
    - 效率: tokens_per_finding, time_per_paper
    - 质量: reviewer_agreement, false_positive_rate

    Usage:
        suite = RegressionTestSuite()
        suite.lock_baseline("v1.0", {"precision": 0.85, "recall": 0.72})
        result = suite.check_regression("v1.0", {"precision": 0.80, "recall": 0.75})
        # result.degraded_metrics = ["precision"]
    """

    def __init__(
        self,
        degradation_threshold: float = 0.05,
        improvement_threshold: float = 0.05,
    ):
        self._baselines: dict[str, BaselineSnapshot] = {}
        self._degradation_threshold = degradation_threshold
        self._improvement_threshold = improvement_threshold
        self._history: list[dict] = []

    def lock_baseline(
        self,
        snapshot_id: str,
        metrics: dict,
        config: dict | None = None,
        description: str = "",
    ) -> BaselineSnapshot:
        """锁定一个 baseline。"""
        snapshot = BaselineSnapshot(
            snapshot_id=snapshot_id,
            metrics=dict(metrics),
            config=config or {},
            created_at=datetime.now(timezone.utc).isoformat(),
            description=description,
        )
        self._baselines[snapshot_id] = snapshot
        return snapshot

    def get_baseline(self, snapshot_id: str) -> BaselineSnapshot | None:
        """获取 baseline。"""
        return self._baselines.get(snapshot_id)

    def list_baselines(self) -> list[str]:
        """列出所有 baseline ID。"""
        return list(self._baselines.keys())

    def check_regression(
        self,
        baseline_id: str,
        current_metrics: dict,
    ) -> RegressionCheckResult:
        """对比 baseline 检测退化。

        Args:
            baseline_id: 基准快照 ID
            current_metrics: 当前指标

        Returns:
            RegressionCheckResult
        """
        if not REGRESSION_TEST_ENABLED:
            return RegressionCheckResult(passed=True)

        baseline = self._baselines.get(baseline_id)
        if not baseline:
            return RegressionCheckResult(
                passed=True,
                details={"error": f"Baseline '{baseline_id}' not found"},
            )

        degraded = []
        improved = []
        unchanged = []
        details = {}

        for metric_name, baseline_val in baseline.metrics.items():
            current_val = current_metrics.get(metric_name)
            if current_val is None:
                details[metric_name] = "missing_in_current"
                continue

            if baseline_val == 0:
                # 避免除零
                if current_val > 0:
                    improved.append(metric_name)
                else:
                    unchanged.append(metric_name)
                continue

            relative_change = (current_val - baseline_val) / abs(baseline_val)
            details[metric_name] = {
                "baseline": baseline_val,
                "current": current_val,
                "change": relative_change,
            }

            if relative_change < -self._degradation_threshold:
                degraded.append(metric_name)
            elif relative_change > self._improvement_threshold:
                improved.append(metric_name)
            else:
                unchanged.append(metric_name)

        passed = len(degraded) == 0

        result = RegressionCheckResult(
            passed=passed,
            degraded_metrics=degraded,
            improved_metrics=improved,
            unchanged_metrics=unchanged,
            details=details,
        )

        # 记录历史
        self._history.append({
            "baseline_id": baseline_id,
            "passed": passed,
            "degraded_count": len(degraded),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return result

    def check_regression_multi(
        self,
        current_metrics: dict,
    ) -> dict[str, RegressionCheckResult]:
        """对所有 baseline 进行回归检测。"""
        results = {}
        for baseline_id in self._baselines:
            results[baseline_id] = self.check_regression(baseline_id, current_metrics)
        return results

    def get_history(self, limit: int = 20) -> list[dict]:
        """获取检测历史。"""
        return self._history[-limit:]

    def serialize(self) -> dict:
        """序列化。"""
        return {
            "baselines": {
                sid: {
                    "metrics": s.metrics,
                    "config": s.config,
                    "created_at": s.created_at,
                    "description": s.description,
                }
                for sid, s in self._baselines.items()
            },
            "degradation_threshold": self._degradation_threshold,
            "improvement_threshold": self._improvement_threshold,
            "history": self._history[-50:],
        }

    def deserialize(self, data: dict) -> None:
        """反序列化。"""
        self._baselines.clear()
        self._history.clear()

        self._degradation_threshold = data.get("degradation_threshold", 0.05)
        self._improvement_threshold = data.get("improvement_threshold", 0.05)

        for sid, sdata in data.get("baselines", {}).items():
            self._baselines[sid] = BaselineSnapshot(
                snapshot_id=sid,
                metrics=sdata.get("metrics", {}),
                config=sdata.get("config", {}),
                created_at=sdata.get("created_at", ""),
                description=sdata.get("description", ""),
            )

        self._history = data.get("history", [])


# ================================================================
# Module 4: EvalDatasetBuilder — 评估数据集构建
# ================================================================

@dataclass
class EvalSample:
    """一个评估样本。"""
    sample_id: str
    # 输入
    paper_text: str = ""
    paper_section: str = ""
    paper_metadata: dict = field(default_factory=dict)
    # 标注 (ground truth)
    expected_findings: list[dict] = field(default_factory=list)
    expected_severity: str = ""
    expected_recommendation: str = ""
    # 来源
    source: str = ""             # "manual" | "expert_agreement" | "historical"
    quality_score: float = 0.0   # 样本质量
    # 元数据
    tags: list[str] = field(default_factory=list)
    created_at: str = ""


@dataclass
class DatasetStats:
    """数据集统计。"""
    total_samples: int = 0
    by_source: dict = field(default_factory=dict)
    by_section: dict = field(default_factory=dict)
    by_tag: dict = field(default_factory=dict)
    avg_quality: float = 0.0


class EvalDatasetBuilder:
    """评估数据集构建工具: 从审稿历史中提取高质量评测样本。

    数据来源:
    1. 历史审稿记录 (findings + 被接受/拒绝的反馈)
    2. 专家标注 (手动创建的 ground truth)
    3. 多 Agent 共识 (多个 Skill 一致的 findings)

    质量控制:
    - 样本质量评分: 基于来源可靠性 + 标注一致性
    - 去重: 基于论文内容 hash
    - 平衡: 确保不同 section/severity 分布均衡

    Usage:
        builder = EvalDatasetBuilder()
        builder.add_from_historical(session_record, skill_results)
        builder.add_manual_sample(paper_text, expected_findings)
        dataset = builder.get_dataset(min_quality=0.7)
    """

    def __init__(self, max_samples: int = 1000):
        self._samples: dict[str, EvalSample] = {}
        self._max_samples = max_samples

    def add_sample(self, sample: EvalSample) -> bool:
        """添加一个评估样本。"""
        if not EVAL_DATASET_ENABLED:
            return False

        if len(self._samples) >= self._max_samples:
            # 替换最低质量的
            self._evict_lowest_quality()

        self._samples[sample.sample_id] = sample
        return True

    def add_from_historical(
        self,
        paper_text: str,
        findings: list[dict],
        section: str = "",
        metadata: dict | None = None,
        quality: float = 0.5,
    ) -> EvalSample | None:
        """从历史审稿结果中创建样本。

        Args:
            paper_text: 论文文本片段
            findings: 该片段上的 findings 列表
            section: 论文 section
            metadata: 论文元数据
            quality: 样本质量 (默认 0.5)

        Returns:
            创建的样本
        """
        if not EVAL_DATASET_ENABLED:
            return None

        if not paper_text or not findings:
            return None

        sample_id = hashlib.md5(
            (paper_text[:200] + json.dumps(findings[:3], default=str)).encode()
        ).hexdigest()[:12]

        sample = EvalSample(
            sample_id=sample_id,
            paper_text=paper_text,
            paper_section=section,
            paper_metadata=metadata or {},
            expected_findings=findings,
            source="historical",
            quality_score=quality,
            tags=[section] if section else [],
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        self.add_sample(sample)
        return sample

    def add_manual_sample(
        self,
        paper_text: str,
        expected_findings: list[dict],
        section: str = "",
        tags: list[str] | None = None,
    ) -> EvalSample:
        """添加手动标注的样本 (最高质量)。"""
        sample_id = hashlib.md5(paper_text[:200].encode()).hexdigest()[:12]

        sample = EvalSample(
            sample_id=sample_id,
            paper_text=paper_text,
            paper_section=section,
            expected_findings=expected_findings,
            source="manual",
            quality_score=1.0,  # 手动标注最高质量
            tags=tags or [],
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        self.add_sample(sample)
        return sample

    def add_from_consensus(
        self,
        paper_text: str,
        findings_from_multiple_skills: list[list[dict]],
        agreement_threshold: float = 0.6,
        section: str = "",
    ) -> EvalSample | None:
        """从多 Skill 共识中创建样本。

        当多个 Skill 对同一文本产出类似 findings 时，
        取共识部分作为 ground truth。
        """
        if not EVAL_DATASET_ENABLED:
            return None

        if not findings_from_multiple_skills or len(findings_from_multiple_skills) < 2:
            return None

        # 简单共识: 找出出现频率超过 threshold 的 findings
        all_descriptions = []
        for skill_findings in findings_from_multiple_skills:
            for f in skill_findings:
                desc = f.get("description", "") if isinstance(f, dict) else str(f)
                all_descriptions.append(desc)

        # 计算每个 finding 出现的 skill 数
        n_skills = len(findings_from_multiple_skills)
        desc_counts: dict[str, int] = {}
        for skill_findings in findings_from_multiple_skills:
            seen_in_skill = set()
            for f in skill_findings:
                desc = f.get("description", "") if isinstance(f, dict) else str(f)
                if desc not in seen_in_skill:
                    desc_counts[desc] = desc_counts.get(desc, 0) + 1
                    seen_in_skill.add(desc)

        # 筛选共识 findings
        consensus_findings = [
            {"description": desc, "agreement": count / n_skills}
            for desc, count in desc_counts.items()
            if count / n_skills >= agreement_threshold
        ]

        if not consensus_findings:
            return None

        quality = sum(f["agreement"] for f in consensus_findings) / len(consensus_findings)

        sample_id = hashlib.md5(paper_text[:200].encode()).hexdigest()[:12]
        sample = EvalSample(
            sample_id=sample_id,
            paper_text=paper_text,
            paper_section=section,
            expected_findings=consensus_findings,
            source="expert_agreement",
            quality_score=min(quality, 0.95),
            tags=[section, "consensus"] if section else ["consensus"],
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        self.add_sample(sample)
        return sample

    def get_dataset(
        self,
        min_quality: float = 0.0,
        section_filter: str = "",
        tag_filter: str = "",
        source_filter: str = "",
        limit: int = 0,
    ) -> list[EvalSample]:
        """获取数据集 (支持过滤)。"""
        samples = list(self._samples.values())

        if min_quality > 0:
            samples = [s for s in samples if s.quality_score >= min_quality]
        if section_filter:
            samples = [s for s in samples if s.paper_section == section_filter]
        if tag_filter:
            samples = [s for s in samples if tag_filter in s.tags]
        if source_filter:
            samples = [s for s in samples if s.source == source_filter]

        # 按质量排序
        samples.sort(key=lambda s: s.quality_score, reverse=True)

        if limit > 0:
            samples = samples[:limit]

        return samples

    def get_stats(self) -> DatasetStats:
        """获取数据集统计。"""
        samples = list(self._samples.values())
        if not samples:
            return DatasetStats()

        by_source: dict[str, int] = {}
        by_section: dict[str, int] = {}
        by_tag: dict[str, int] = {}

        for s in samples:
            by_source[s.source] = by_source.get(s.source, 0) + 1
            if s.paper_section:
                by_section[s.paper_section] = by_section.get(s.paper_section, 0) + 1
            for tag in s.tags:
                by_tag[tag] = by_tag.get(tag, 0) + 1

        avg_quality = sum(s.quality_score for s in samples) / len(samples)

        return DatasetStats(
            total_samples=len(samples),
            by_source=by_source,
            by_section=by_section,
            by_tag=by_tag,
            avg_quality=avg_quality,
        )

    def remove_sample(self, sample_id: str) -> bool:
        """移除样本。"""
        if sample_id in self._samples:
            del self._samples[sample_id]
            return True
        return False

    def serialize(self) -> dict:
        """序列化。"""
        return {
            "samples": [
                {
                    "sample_id": s.sample_id,
                    "paper_text": s.paper_text[:500],  # 截断以减少体积
                    "paper_section": s.paper_section,
                    "paper_metadata": s.paper_metadata,
                    "expected_findings": s.expected_findings,
                    "source": s.source,
                    "quality_score": s.quality_score,
                    "tags": s.tags,
                    "created_at": s.created_at,
                }
                for s in self._samples.values()
            ],
            "max_samples": self._max_samples,
        }

    def deserialize(self, data: dict) -> None:
        """反序列化。"""
        self._samples.clear()
        self._max_samples = data.get("max_samples", 1000)

        for sdata in data.get("samples", []):
            sample = EvalSample(
                sample_id=sdata.get("sample_id", ""),
                paper_text=sdata.get("paper_text", ""),
                paper_section=sdata.get("paper_section", ""),
                paper_metadata=sdata.get("paper_metadata", {}),
                expected_findings=sdata.get("expected_findings", []),
                source=sdata.get("source", ""),
                quality_score=sdata.get("quality_score", 0.0),
                tags=sdata.get("tags", []),
                created_at=sdata.get("created_at", ""),
            )
            self._samples[sample.sample_id] = sample

    def _evict_lowest_quality(self) -> None:
        """移除质量最低的样本。"""
        if not self._samples:
            return
        worst = min(self._samples.values(), key=lambda s: s.quality_score)
        del self._samples[worst.sample_id]


# ================================================================
# Orchestrator: MetaHarnessOrchestrator
# ================================================================

class MetaHarnessOrchestrator:
    """Meta-Harness 统一协调器。

    在审稿流程的关键节点触发:
    - on_session_start(): 初始化配置
    - on_skill_executed(): 记录执行，触发 prompt 优化
    - on_session_end(): 回归检测 + 数据集构建
    - on_config_change(): 配置搜索反馈

    Usage:
        harness = MetaHarnessOrchestrator()
        harness.on_session_start(config)
        # ... 审稿过程 ...
        harness.on_skill_executed("check_methods", result)
        # ... 结束 ...
        harness.on_session_end(session_metrics)
    """

    def __init__(
        self,
        optimizer: PromptOptimizer | None = None,
        searcher: ConfigSpaceSearcher | None = None,
        regression: RegressionTestSuite | None = None,
        dataset_builder: EvalDatasetBuilder | None = None,
    ):
        self.optimizer = optimizer or PromptOptimizer()
        self.searcher = searcher or ConfigSpaceSearcher()
        self.regression = regression or RegressionTestSuite()
        self.dataset_builder = dataset_builder or EvalDatasetBuilder()

    def on_session_start(self, config: dict | None = None) -> dict:
        """会话开始时: 建议最优配置。

        Returns:
            建议使用的配置
        """
        suggested_config = self.searcher.get_best().best_config
        if config:
            suggested_config.update(config)
        return suggested_config

    def on_skill_executed(
        self,
        skill_name: str,
        prompt_variant_id: str,
        result: Any,
        score: float = 0.0,
    ) -> None:
        """Skill 执行后: 记录反馈。"""
        # Prompt 优化反馈
        if prompt_variant_id:
            self.optimizer.record_feedback(OptimizationFeedback(
                variant_id=prompt_variant_id,
                score=score,
            ))

    def on_session_end(
        self,
        session_metrics: dict,
        paper_text: str = "",
        findings: list[dict] | None = None,
        baseline_id: str = "",
    ) -> dict:
        """会话结束时: 回归检测 + 数据集更新。

        Returns:
            {'regression': RegressionCheckResult, 'dataset_added': bool}
        """
        result = {}

        # 回归检测
        if baseline_id:
            reg_result = self.regression.check_regression(baseline_id, session_metrics)
            result["regression"] = reg_result

        # 配置搜索记录
        if session_metrics:
            overall_score = session_metrics.get("overall_score", 0.0)
            config = session_metrics.get("config", {})
            if config:
                self.searcher.record_evaluation(config, overall_score)

        # 数据集构建
        if paper_text and findings:
            sample = self.dataset_builder.add_from_historical(
                paper_text=paper_text,
                findings=findings,
                quality=session_metrics.get("confidence", 0.5),
            )
            result["dataset_added"] = sample is not None

        return result

    def get_status(self) -> dict:
        """获取整体状态。"""
        return {
            "optimizer": self.optimizer.get_optimization_report(),
            "searcher": self.searcher.get_search_stats(),
            "regression_baselines": self.regression.list_baselines(),
            "dataset_stats": {
                "total": self.dataset_builder.get_stats().total_samples,
                "avg_quality": self.dataset_builder.get_stats().avg_quality,
            },
        }
