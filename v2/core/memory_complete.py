"""
core/memory_complete.py — Phase 2 Complete Layer: 跨会话认知记忆完善版

四大功能模块:
    1. Proactive Retrieval — phase 开始时根据任务描述从记忆中动态检索注入
    2. Memory Decay Model — importance × recency 复合分数替代简单时间戳 GC
    3. Cross-Paper Knowledge — 共性发现自动提升为跨论文通用知识
    4. Distillation Quality Verification — LLM self-check 忠实性验证

设计原则:
    - Kill Switch: 所有功能通过环境变量控制 (默认 ON)
    - 与 memory.py / memory_distiller.py 松耦合: 通过接口协作
    - 渐进退化: 每个功能独立可关闭，不影响其他功能
    - 零外部依赖: 纯 Python 标准库 + typing
"""

from __future__ import annotations

import logging
import math
import os
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ================================================================
# Kill Switches (默认 ON — 即不设置环境变量时功能开启)
# ================================================================

def _env_enabled(key: str, default: bool = True) -> bool:
    """读取环境变量控制开关。'0'/'false'/'off' 为关闭。"""
    val = os.environ.get(key, "")
    if not val:
        return default
    return val.lower() not in ("0", "false", "off", "no", "disabled")


PROACTIVE_RETRIEVAL_ENABLED = _env_enabled("SCHOLAR_PROACTIVE_RETRIEVAL", True)
MEMORY_DECAY_ENABLED = _env_enabled("SCHOLAR_MEMORY_DECAY", True)
CROSS_PAPER_KNOWLEDGE_ENABLED = _env_enabled("SCHOLAR_CROSS_PAPER_KNOWLEDGE", True)
DISTILL_VERIFICATION_ENABLED = _env_enabled("SCHOLAR_DISTILL_VERIFICATION", True)


# ================================================================
# Module 1: Proactive Retrieval — 主动检索
# ================================================================

@dataclass
class RetrievalQuery:
    """主动检索的查询描述。"""
    phase: str                  # 当前 phase (initial_scan / deep_review / editing / synthesis)
    paper_type: str = ""        # 论文类型 (methodology / empirical / theoretical ...)
    paper_title: str = ""       # 论文标题
    current_focus: str = ""     # 当前关注点 (section name / topic)
    keywords: list[str] = field(default_factory=list)  # 从论文中提取的关键词


@dataclass
class RetrievalResult:
    """主动检索的返回结果。"""
    domain_hints: list[str] = field(default_factory=list)    # 相关领域经验
    procedural_hints: list[str] = field(default_factory=list) # 相关工作模式建议
    session_hints: list[str] = field(default_factory=list)    # 相似论文历史
    total_tokens_estimate: int = 0                            # 注入的 token 预估


class ProactiveRetriever:
    """主动检索器: 在 phase 开始时根据任务上下文从记忆中智能检索相关知识。

    与 format_memory_context 的区别:
    - format_memory_context: 被动，固定格式，每次注入相同内容
    - ProactiveRetriever: 主动，根据当前 phase 和论文特征定制检索策略

    检索策略按 phase 差异化:
    - INITIAL_SCAN: 检索同类型论文的常见问题模式 + 高效扫描策略
    - DEEP_REVIEW: 检索与当前论文方法论相关的深层领域知识
    - EDITING: 检索类似修改场景的成功模式
    - SYNTHESIS: 检索报告组织的有效模式
    """

    # 每个 phase 的检索重点
    _PHASE_RETRIEVAL_FOCUS = {
        "initial_scan": {
            "domain_categories": ["methodology", "overclaim"],
            "procedural_categories": ["tool_sequence", "strategy_effectiveness"],
            "max_domain": 3,
            "max_procedural": 2,
            "max_session": 1,
        },
        "deep_review": {
            "domain_categories": None,  # 全部类别
            "procedural_categories": ["strategy_effectiveness"],
            "max_domain": 5,
            "max_procedural": 3,
            "max_session": 2,
        },
        "editing": {
            "domain_categories": ["writing", "logic"],
            "procedural_categories": ["tool_sequence"],
            "max_domain": 2,
            "max_procedural": 2,
            "max_session": 1,
        },
        "synthesis": {
            "domain_categories": ["overclaim", "methodology"],
            "procedural_categories": ["strategy_effectiveness"],
            "max_domain": 3,
            "max_procedural": 1,
            "max_session": 0,
        },
    }

    # Token budget per phase (避免注入过多记忆淹没当前上下文)
    _PHASE_TOKEN_BUDGET = {
        "initial_scan": 300,
        "deep_review": 500,
        "editing": 200,
        "synthesis": 300,
    }

    def __init__(self, memory_store: Any):
        """
        Args:
            memory_store: core.memory.MemoryStore 实例
        """
        self.memory = memory_store

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """根据当前上下文主动检索相关记忆。

        Args:
            query: 检索查询（包含 phase、论文类型等上下文）

        Returns:
            RetrievalResult 包含分类的检索结果
        """
        if not PROACTIVE_RETRIEVAL_ENABLED:
            return RetrievalResult()

        focus = self._PHASE_RETRIEVAL_FOCUS.get(
            query.phase, self._PHASE_RETRIEVAL_FOCUS["deep_review"]
        )
        budget = self._PHASE_TOKEN_BUDGET.get(query.phase, 400)

        result = RetrievalResult()
        tokens_used = 0

        # 1. Domain hints: 检索相关领域经验
        domain_hints = self._retrieve_domain(query, focus)
        for hint in domain_hints:
            hint_tokens = len(hint) // 3  # 粗略估算
            if tokens_used + hint_tokens > budget:
                break
            result.domain_hints.append(hint)
            tokens_used += hint_tokens

        # 2. Procedural hints: 检索高效工作模式
        procedural_hints = self._retrieve_procedural(query, focus)
        for hint in procedural_hints:
            hint_tokens = len(hint) // 3
            if tokens_used + hint_tokens > budget:
                break
            result.procedural_hints.append(hint)
            tokens_used += hint_tokens

        # 3. Session hints: 检索相似论文的历史
        session_hints = self._retrieve_sessions(query, focus)
        for hint in session_hints:
            hint_tokens = len(hint) // 3
            if tokens_used + hint_tokens > budget:
                break
            result.session_hints.append(hint)
            tokens_used += hint_tokens

        result.total_tokens_estimate = tokens_used
        return result

    def format_retrieval_context(self, result: RetrievalResult) -> str | None:
        """将检索结果格式化为可注入 system prompt 的文本。

        Returns:
            格式化文本，如果无内容则返回 None
        """
        if not result.domain_hints and not result.procedural_hints and not result.session_hints:
            return None

        parts = []

        if result.domain_hints:
            parts.append("🔍 本阶段相关经验:")
            for hint in result.domain_hints:
                parts.append(f"  • {hint}")

        if result.procedural_hints:
            parts.append("⚡ 推荐工作模式:")
            for hint in result.procedural_hints:
                parts.append(f"  • {hint}")

        if result.session_hints:
            parts.append("📝 相似论文历史:")
            for hint in result.session_hints:
                parts.append(f"  • {hint}")

        return "\n".join(parts)

    def _retrieve_domain(self, query: RetrievalQuery, focus: dict) -> list[str]:
        """检索领域层记忆。"""
        categories = focus.get("domain_categories")
        max_items = focus.get("max_domain", 3)

        patterns = self.memory.get_relevant_patterns(
            categories=categories, limit=max_items * 2
        )

        # 基于关键词相关性排序
        if query.keywords:
            patterns = self._rank_by_relevance(patterns, query.keywords)

        hints = []
        for p in patterns[:max_items]:
            hints.append(f"[{p.category}] {p.description} (见过 {p.evidence_count} 次)")
        return hints

    def _retrieve_procedural(self, query: RetrievalQuery, focus: dict) -> list[str]:
        """检索程序性记忆。"""
        categories = focus.get("procedural_categories")
        max_items = focus.get("max_procedural", 2)

        procedures = self.memory.get_relevant_procedures(
            categories=categories, limit=max_items * 2
        )

        hints = []
        for proc in procedures[:max_items]:
            score_pct = int(proc.effectiveness_score * 100)
            hints.append(
                f"{proc.description} (效率 {score_pct}%, 验证 {proc.evidence_count} 次)"
            )
        return hints

    def _retrieve_sessions(self, query: RetrievalQuery, focus: dict) -> list[str]:
        """检索相似论文的会话历史。"""
        max_items = focus.get("max_session", 1)
        if max_items == 0:
            return []

        recent = self.memory.recall_recent(limit=10)

        # 如果有关键词，根据关键词匹配历史会话
        if query.keywords:
            scored = []
            for session in recent:
                score = self._compute_session_relevance(session, query)
                if score > 0:
                    scored.append((session, score))
            scored.sort(key=lambda x: x[1], reverse=True)
            recent = [s for s, _ in scored[:max_items]]
        else:
            recent = recent[:max_items]

        hints = []
        for session in recent:
            if session.key_issues:
                issues = "; ".join(session.key_issues[:2])
                hints.append(
                    f"{session.paper_title[:40]} ({session.decision}): {issues}"
                )
            else:
                hints.append(
                    f"{session.paper_title[:40]}: 决定={session.decision}, "
                    f"{len(session.findings_summary)} 条发现"
                )
        return hints

    def _rank_by_relevance(self, patterns: list, keywords: list[str]) -> list:
        """基于关键词相关性对 patterns 排序。"""
        keyword_set = set(k.lower() for k in keywords)

        def relevance(pattern) -> float:
            desc_words = set(pattern.description.lower().split())
            overlap = len(keyword_set & desc_words)
            # 加权: evidence_count 也参与排序
            return overlap * 2 + pattern.evidence_count * 0.1

        return sorted(patterns, key=relevance, reverse=True)

    def _compute_session_relevance(self, session: Any, query: RetrievalQuery) -> float:
        """计算历史会话与当前查询的相关性。"""
        score = 0.0
        keyword_set = set(k.lower() for k in query.keywords)

        # 标题词匹配
        title_words = set(session.paper_title.lower().split())
        title_overlap = len(keyword_set & title_words)
        score += title_overlap * 2.0

        # key_issues 词匹配
        for issue in session.key_issues:
            issue_words = set(issue.lower().split())
            score += len(keyword_set & issue_words) * 1.0

        # findings_summary 词匹配
        for finding in session.findings_summary:
            finding_words = set(finding.lower().split())
            score += len(keyword_set & finding_words) * 0.5

        return score


# ================================================================
# Module 2: Memory Decay Model — 记忆衰减
# ================================================================

@dataclass
class DecayConfig:
    """记忆衰减模型配置。"""
    # 半衰期参数 (天数): 多少天后权重减半
    half_life_days: float = 30.0

    # importance 权重 vs recency 权重
    importance_weight: float = 0.6
    recency_weight: float = 0.4

    # 最低保留分数阈值 (低于此分数的记忆可被 GC)
    gc_threshold: float = 0.15

    # 保护规则: evidence_count >= 此值的记忆不受衰减影响
    evidence_protection_threshold: int = 5


@dataclass
class ScoredMemoryItem:
    """带复合分数的记忆条目。"""
    item: Any                   # 原始记忆对象 (DomainPattern / ProceduralPattern)
    importance_score: float     # 重要性分数 (0-1)
    recency_score: float        # 新近度分数 (0-1)
    composite_score: float      # 复合分数 (importance × weight + recency × weight)
    is_protected: bool = False  # 是否受保护


class MemoryDecayModel:
    """记忆衰减模型: 基于 importance × recency 的复合分数。

    替代 memory.py 中简单的"按 evidence_count 排序 + max_age_days 淘汰"，
    引入平滑的指数衰减函数，让记忆随时间优雅退化而非突然消失。

    衰减函数:
        recency(t) = exp(-λ * t), λ = ln(2) / half_life_days

    重要性函数:
        importance(item) = normalize(base_score(item))
        base_score = evidence_count × effectiveness_score (for procedures)
        base_score = evidence_count (for domain patterns)

    复合分数:
        composite = importance_weight × importance + recency_weight × recency

    用法:
        model = MemoryDecayModel(config)
        scored = model.score_all(memory_state)
        # scored 按 composite_score 降序排列
        to_keep = [s for s in scored if s.composite_score >= config.gc_threshold]
    """

    def __init__(self, config: DecayConfig | None = None):
        self.config = config or DecayConfig()
        # λ = ln(2) / half_life
        self._lambda = math.log(2) / max(self.config.half_life_days, 1.0)

    def score_domain_patterns(
        self, patterns: list, now: datetime | None = None
    ) -> list[ScoredMemoryItem]:
        """对 DomainPattern 列表打分并排序。

        Args:
            patterns: DomainPattern 实例列表
            now: 当前时间（用于计算 recency），默认 UTC now

        Returns:
            按 composite_score 降序排列的 ScoredMemoryItem 列表
        """
        if not MEMORY_DECAY_ENABLED:
            # 退化模式: 直接返回，不打分
            return [
                ScoredMemoryItem(
                    item=p, importance_score=1.0,
                    recency_score=1.0, composite_score=1.0
                )
                for p in patterns
            ]

        if now is None:
            now = datetime.now(timezone.utc)

        # 计算 importance 归一化基准
        max_evidence = max((p.evidence_count for p in patterns), default=1)

        scored = []
        for p in patterns:
            importance = p.evidence_count / max(max_evidence, 1)
            recency = self._compute_recency(p.last_seen or p.first_seen, now)
            composite = (
                self.config.importance_weight * importance
                + self.config.recency_weight * recency
            )
            is_protected = p.evidence_count >= self.config.evidence_protection_threshold
            scored.append(ScoredMemoryItem(
                item=p,
                importance_score=importance,
                recency_score=recency,
                composite_score=composite,
                is_protected=is_protected,
            ))

        scored.sort(key=lambda s: s.composite_score, reverse=True)
        return scored

    def score_procedural_patterns(
        self, procedures: list, now: datetime | None = None
    ) -> list[ScoredMemoryItem]:
        """对 ProceduralPattern 列表打分并排序。

        与 domain patterns 的区别: importance 同时考虑 evidence_count 和 effectiveness_score。

        Args:
            procedures: ProceduralPattern 实例列表
            now: 当前时间

        Returns:
            按 composite_score 降序排列的 ScoredMemoryItem 列表
        """
        if not MEMORY_DECAY_ENABLED:
            return [
                ScoredMemoryItem(
                    item=p, importance_score=1.0,
                    recency_score=1.0, composite_score=1.0
                )
                for p in procedures
            ]

        if now is None:
            now = datetime.now(timezone.utc)

        # importance 基准: effectiveness × evidence_count
        raw_scores = [
            p.effectiveness_score * p.evidence_count for p in procedures
        ]
        max_raw = max(raw_scores, default=1.0)

        scored = []
        for i, p in enumerate(procedures):
            importance = raw_scores[i] / max(max_raw, 0.01)
            recency = self._compute_recency(p.last_seen or p.first_seen, now)
            composite = (
                self.config.importance_weight * importance
                + self.config.recency_weight * recency
            )
            is_protected = p.evidence_count >= self.config.evidence_protection_threshold
            scored.append(ScoredMemoryItem(
                item=p,
                importance_score=importance,
                recency_score=recency,
                composite_score=composite,
                is_protected=is_protected,
            ))

        scored.sort(key=lambda s: s.composite_score, reverse=True)
        return scored

    def gc_with_decay(
        self,
        patterns: list,
        pattern_type: str = "domain",
        max_size: int = 100,
        now: datetime | None = None,
    ) -> tuple[list, int]:
        """使用衰减模型进行 GC，替代 memory.py 的 gc_procedures。

        Args:
            patterns: 待 GC 的 pattern 列表
            pattern_type: "domain" 或 "procedural"
            max_size: 保留上限
            now: 当前时间

        Returns:
            (surviving_patterns, removed_count)
        """
        if not MEMORY_DECAY_ENABLED:
            return patterns, 0

        if not patterns:
            return [], 0

        if pattern_type == "procedural":
            scored = self.score_procedural_patterns(patterns, now)
        else:
            scored = self.score_domain_patterns(patterns, now)

        surviving = []
        for s in scored:
            # 受保护的不淘汰
            if s.is_protected:
                surviving.append(s.item)
                continue
            # 低于阈值的淘汰
            if s.composite_score < self.config.gc_threshold:
                continue
            surviving.append(s.item)

        # 硬容量限制
        if len(surviving) > max_size:
            # 已经按 composite_score 降序，直接截断
            surviving = surviving[:max_size]

        removed = len(patterns) - len(surviving)
        return surviving, removed

    def _compute_recency(self, timestamp_str: str, now: datetime) -> float:
        """计算新近度分数: exp(-λ × days_elapsed)。

        Args:
            timestamp_str: ISO 格式时间戳
            now: 当前时间

        Returns:
            0.0 ~ 1.0 的新近度分数 (1.0 = 刚刚发生)
        """
        if not timestamp_str:
            return 0.3  # 无时间戳默认给个中间值

        try:
            ts = datetime.fromisoformat(timestamp_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            days_elapsed = max((now - ts).total_seconds() / 86400, 0)
            return math.exp(-self._lambda * days_elapsed)
        except (ValueError, TypeError):
            return 0.3


# ================================================================
# Module 3: Cross-Paper Knowledge — 跨论文知识积累
# ================================================================

@dataclass
class CrossPaperPattern:
    """跨论文积累的通用知识模式。

    比 DomainPattern 更高一层: DomainPattern 是"某个类别的模式"，
    CrossPaperPattern 是"多篇论文中反复出现的共性发现"的泛化。

    例如:
    - "使用 DID 方法的论文中，80% 缺少 parallel trends test 的充分讨论"
    - "声称因果关系的实证论文中，50% 存在 omitted variable 风险"
    """
    pattern_id: str
    generalization: str          # 泛化后的通用描述
    source_pattern_ids: list[str] = field(default_factory=list)  # 来源 DomainPattern IDs
    paper_count: int = 0          # 涉及论文数量
    confidence: float = 0.0       # 置信度 (基于 evidence 强度)
    category: str = ""            # 归属类别
    first_seen: str = ""
    last_updated: str = ""
    # 可操作建议
    actionable_hint: str = ""     # "审阅此类论文时应特别检查..."


class CrossPaperKnowledgeAccumulator:
    """跨论文知识积累器: 自动将重复出现的 DomainPattern 提升为通用知识。

    触发条件:
    - 同一 category 下的 DomainPattern 数量 >= promotion_threshold
    - 且这些 patterns 的总 evidence_count 达到 min_evidence

    提升逻辑:
    1. 聚类: 将同类 patterns 按描述相似度分组
    2. 归纳: 从每个组中提取通用表述
    3. 注册: 创建 CrossPaperPattern 并附带 actionable_hint

    与 MemoryDistiller 的关系:
    - MemoryDistiller 做 L1→L2→L3 的纵向蒸馏
    - CrossPaperKnowledgeAccumulator 做 L2 层的横向归纳
    - 两者互补: 一个纵深，一个横广
    """

    def __init__(
        self,
        promotion_threshold: int = 3,
        min_evidence_total: int = 5,
        similarity_threshold: float = 0.4,
    ):
        """
        Args:
            promotion_threshold: 同类别 pattern 达到此数量触发提升
            min_evidence_total: 组内 patterns 的 evidence_count 总和最低要求
            similarity_threshold: 聚类时的相似度阈值
        """
        self.promotion_threshold = promotion_threshold
        self.min_evidence_total = min_evidence_total
        self.similarity_threshold = similarity_threshold
        self._cross_patterns: list[CrossPaperPattern] = []

    @property
    def cross_patterns(self) -> list[CrossPaperPattern]:
        """当前积累的跨论文知识。"""
        return list(self._cross_patterns)

    def analyze_and_promote(
        self, domain_patterns: list, force: bool = False
    ) -> list[CrossPaperPattern]:
        """分析当前 domain patterns，提升符合条件的为跨论文知识。

        Args:
            domain_patterns: 当前 MemoryState.patterns 列表
            force: 是否忽略 kill switch

        Returns:
            新发现的 CrossPaperPattern 列表
        """
        if not CROSS_PAPER_KNOWLEDGE_ENABLED and not force:
            return []

        if not domain_patterns:
            return []

        # 按 category 分组
        by_category: dict[str, list] = {}
        for p in domain_patterns:
            by_category.setdefault(p.category, []).append(p)

        new_cross_patterns = []

        for category, patterns in by_category.items():
            if len(patterns) < self.promotion_threshold:
                continue

            total_evidence = sum(p.evidence_count for p in patterns)
            if total_evidence < self.min_evidence_total:
                continue

            # 聚类
            clusters = self._cluster_patterns(patterns)

            for cluster in clusters:
                if len(cluster) < 2:
                    continue

                # 检查是否已有对应的跨论文知识
                existing = self._find_existing_cross_pattern(cluster, category)
                if existing:
                    # 更新已有
                    self._update_cross_pattern(existing, cluster)
                    continue

                # 创建新的跨论文知识
                cross_pattern = self._promote_cluster(cluster, category)
                if cross_pattern:
                    self._cross_patterns.append(cross_pattern)
                    new_cross_patterns.append(cross_pattern)

        return new_cross_patterns

    def format_cross_knowledge_context(self, limit: int = 5) -> str | None:
        """格式化跨论文知识为可注入的上下文。"""
        if not self._cross_patterns:
            return None

        # 按置信度排序
        sorted_patterns = sorted(
            self._cross_patterns, key=lambda p: p.confidence, reverse=True
        )[:limit]

        parts = ["🌐 跨论文通用经验:"]
        for cp in sorted_patterns:
            conf_pct = int(cp.confidence * 100)
            parts.append(f"  [{cp.category}] {cp.generalization} ({cp.paper_count} 篇, 置信度 {conf_pct}%)")
            if cp.actionable_hint:
                parts.append(f"    → {cp.actionable_hint}")

        return "\n".join(parts)

    def serialize(self) -> list[dict]:
        """序列化跨论文知识。"""
        return [
            {
                "pattern_id": cp.pattern_id,
                "generalization": cp.generalization,
                "source_pattern_ids": cp.source_pattern_ids,
                "paper_count": cp.paper_count,
                "confidence": cp.confidence,
                "category": cp.category,
                "first_seen": cp.first_seen,
                "last_updated": cp.last_updated,
                "actionable_hint": cp.actionable_hint,
            }
            for cp in self._cross_patterns
        ]

    def deserialize(self, data: list[dict]) -> None:
        """从序列化数据恢复。"""
        self._cross_patterns = []
        for item in data:
            self._cross_patterns.append(CrossPaperPattern(
                pattern_id=item.get("pattern_id", ""),
                generalization=item.get("generalization", ""),
                source_pattern_ids=item.get("source_pattern_ids", []),
                paper_count=item.get("paper_count", 0),
                confidence=item.get("confidence", 0.0),
                category=item.get("category", ""),
                first_seen=item.get("first_seen", ""),
                last_updated=item.get("last_updated", ""),
                actionable_hint=item.get("actionable_hint", ""),
            ))

    def _cluster_patterns(self, patterns: list) -> list[list]:
        """将 patterns 按描述相似度聚类（简单贪心聚类）。"""
        if not patterns:
            return []

        clusters: list[list] = []
        used = set()

        for i, p in enumerate(patterns):
            if i in used:
                continue
            cluster = [p]
            used.add(i)

            for j in range(i + 1, len(patterns)):
                if j in used:
                    continue
                if self._text_similarity(p.description, patterns[j].description) >= self.similarity_threshold:
                    cluster.append(patterns[j])
                    used.add(j)

            clusters.append(cluster)

        return clusters

    def _text_similarity(self, a: str, b: str) -> float:
        """基于 token overlap 的 Jaccard 相似度。"""
        words_a = set(a.lower().split())
        words_b = set(b.lower().split())
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union) if union else 0.0

    def _find_existing_cross_pattern(
        self, cluster: list, category: str
    ) -> CrossPaperPattern | None:
        """查找已有的对应跨论文知识。"""
        source_ids = {p.pattern_id for p in cluster}
        for cp in self._cross_patterns:
            if cp.category != category:
                continue
            existing_sources = set(cp.source_pattern_ids)
            overlap = source_ids & existing_sources
            if len(overlap) >= len(source_ids) * 0.5:
                return cp
        return None

    def _update_cross_pattern(self, cross_pattern: CrossPaperPattern, cluster: list) -> None:
        """更新已有的跨论文知识。"""
        now = datetime.now(timezone.utc).isoformat()
        new_ids = {p.pattern_id for p in cluster}
        existing_ids = set(cross_pattern.source_pattern_ids)
        cross_pattern.source_pattern_ids = list(existing_ids | new_ids)
        cross_pattern.paper_count = sum(p.evidence_count for p in cluster)
        cross_pattern.confidence = min(
            0.5 + len(cross_pattern.source_pattern_ids) * 0.1,
            0.95,
        )
        cross_pattern.last_updated = now

    def _promote_cluster(self, cluster: list, category: str) -> CrossPaperPattern | None:
        """将一个 cluster 提升为 CrossPaperPattern。"""
        if not cluster:
            return None

        now = datetime.now(timezone.utc).isoformat()

        # 选择 evidence_count 最高的作为代表
        representative = max(cluster, key=lambda p: p.evidence_count)

        # 泛化描述: 取代表的描述 + 统计信息
        total_evidence = sum(p.evidence_count for p in cluster)
        paper_count = total_evidence  # 近似

        generalization = self._generalize_descriptions(cluster)

        pattern_id = hashlib.md5(
            f"cross:{category}:{generalization[:50]}".encode()
        ).hexdigest()[:12]

        # 生成可操作建议
        actionable = self._generate_actionable_hint(cluster, category)

        return CrossPaperPattern(
            pattern_id=pattern_id,
            generalization=generalization,
            source_pattern_ids=[p.pattern_id for p in cluster],
            paper_count=paper_count,
            confidence=min(0.5 + len(cluster) * 0.1, 0.9),
            category=category,
            first_seen=now,
            last_updated=now,
            actionable_hint=actionable,
        )

    def _generalize_descriptions(self, cluster: list) -> str:
        """从 cluster 中归纳出通用描述。

        策略: 找出共同关键词，组合为通用表述。
        """
        if len(cluster) == 1:
            return cluster[0].description

        # 收集所有描述的共同词
        word_sets = [set(p.description.lower().split()) for p in cluster]
        common_words = word_sets[0]
        for ws in word_sets[1:]:
            common_words = common_words & ws

        # 如果共同词太少，使用代表的描述
        if len(common_words) < 3:
            representative = max(cluster, key=lambda p: p.evidence_count)
            return f"多篇论文的共性模式: {representative.description[:100]}"

        # 用代表描述但标注为通用
        representative = max(cluster, key=lambda p: p.evidence_count)
        return f"{representative.description[:100]} (跨 {len(cluster)} 个相似发现)"

    def _generate_actionable_hint(self, cluster: list, category: str) -> str:
        """生成可操作的审稿建议。"""
        # 基于 category 生成模板化建议
        hints_by_category = {
            "methodology": "审阅此类论文时应重点检查研究方法的有效性和前提假设",
            "overclaim": "注意作者是否在因果推断上过度声明，检查替代解释",
            "statistics": "仔细核查统计检验的正确性和报告完整性",
            "logic": "追踪论证链条，检查前后矛盾和推理跳跃",
            "writing": "关注表述清晰度和论证组织结构",
        }
        base_hint = hints_by_category.get(category, "应仔细审查相关内容")

        # 附加具体模式信息
        if cluster:
            top = max(cluster, key=lambda p: p.evidence_count)
            return f"{base_hint}。历史高频问题: {top.description[:60]}"
        return base_hint


# ================================================================
# Module 4: Distillation Quality Verification — 蒸馏质量验证
# ================================================================

@dataclass
class VerificationResult:
    """蒸馏质量验证的结果。"""
    is_faithful: bool            # 是否忠实于原始数据
    confidence: float            # 验证置信度 (0-1)
    issues: list[str] = field(default_factory=list)  # 发现的问题
    original_summary: str = ""   # 原始内容摘要
    distilled_summary: str = ""  # 蒸馏后内容摘要


@runtime_checkable
class LLMVerifier(Protocol):
    """验证器依赖的 LLM 接口。"""
    async def verify_faithfulness(
        self, original: str, distilled: str, schema: dict
    ) -> dict:
        """调用 LLM 验证蒸馏结果的忠实性。"""
        ...


class DistillationQualityVerifier:
    """蒸馏质量验证器: 通过 LLM self-check 验证蒸馏产物的忠实性。

    核心问题: 蒸馏是有损压缩，如何确保不引入幻觉或丢失关键信息？

    验证策略:
    1. 忠实性检查: 蒸馏后的知识是否在原始数据中有支撑
    2. 完整性检查: 是否遗漏了重要的原始信息
    3. 无幻觉检查: 是否引入了原始数据中不存在的信息

    两种模式:
    - LLM 模式: 使用 LLM 做深层语义验证
    - 规则模式: 基于词汇覆盖率做近似验证 (fallback)

    用法:
        verifier = DistillationQualityVerifier(llm=my_verifier)
        result = await verifier.verify_domain_fact(original_findings, distilled_fact)
        if not result.is_faithful:
            discard(distilled_fact)
    """

    def __init__(
        self,
        llm: LLMVerifier | None = None,
        min_coverage_ratio: float = 0.5,
        max_hallucination_ratio: float = 0.3,
    ):
        """
        Args:
            llm: LLM 验证接口 (可选，无则用规则 fallback)
            min_coverage_ratio: 规则模式下，蒸馏产物中的关键词至少覆盖原始的比例
            max_hallucination_ratio: 规则模式下，蒸馏产物中新引入词的最大比例
        """
        self.llm = llm
        self.min_coverage_ratio = min_coverage_ratio
        self.max_hallucination_ratio = max_hallucination_ratio

    async def verify_domain_fact(
        self,
        original_findings: list[dict],
        distilled_fact: Any,
    ) -> VerificationResult:
        """验证一条蒸馏出的 DomainFact 的忠实性。

        Args:
            original_findings: 蒸馏前的原始 findings 列表
            distilled_fact: 蒸馏出的 DomainFact

        Returns:
            VerificationResult
        """
        if not DISTILL_VERIFICATION_ENABLED:
            return VerificationResult(is_faithful=True, confidence=1.0)

        # 构建原始文本摘要
        original_text = self._format_original(original_findings)
        distilled_text = self._format_distilled_fact(distilled_fact)

        # 优先 LLM 验证
        if self.llm:
            try:
                return await self._llm_verify(original_text, distilled_text)
            except Exception as e:
                logger.warning("LLM verification failed, falling back to rules: %s", e)

        # 规则 fallback
        return self._rule_verify(original_text, distilled_text)

    async def verify_procedural_rule(
        self,
        source_facts: list[Any],
        distilled_rule: Any,
    ) -> VerificationResult:
        """验证一条蒸馏出的 ProceduralRule 的忠实性。

        Args:
            source_facts: 规则归纳的来源 DomainFacts
            distilled_rule: 蒸馏出的 ProceduralRule

        Returns:
            VerificationResult
        """
        if not DISTILL_VERIFICATION_ENABLED:
            return VerificationResult(is_faithful=True, confidence=1.0)

        original_text = self._format_source_facts(source_facts)
        distilled_text = self._format_distilled_rule(distilled_rule)

        if self.llm:
            try:
                return await self._llm_verify(original_text, distilled_text)
            except Exception as e:
                logger.warning("LLM verification failed, falling back to rules: %s", e)

        return self._rule_verify(original_text, distilled_text)

    async def verify_batch(
        self,
        original_findings: list[dict],
        distilled_facts: list[Any],
    ) -> list[VerificationResult]:
        """批量验证多条蒸馏产物。

        Args:
            original_findings: 原始 findings
            distilled_facts: 蒸馏出的 facts 列表

        Returns:
            与 distilled_facts 一一对应的验证结果列表
        """
        results = []
        for fact in distilled_facts:
            result = await self.verify_domain_fact(original_findings, fact)
            results.append(result)
        return results

    def filter_faithful(
        self, facts: list[Any], results: list[VerificationResult]
    ) -> tuple[list[Any], list[Any]]:
        """根据验证结果过滤，返回 (通过的, 未通过的)。"""
        passed = []
        failed = []
        for fact, result in zip(facts, results):
            if result.is_faithful:
                passed.append(fact)
            else:
                failed.append(fact)
        return passed, failed

    async def _llm_verify(
        self, original_text: str, distilled_text: str
    ) -> VerificationResult:
        """使用 LLM 进行语义级验证。"""
        schema = {
            "type": "object",
            "properties": {
                "is_faithful": {"type": "boolean"},
                "confidence": {"type": "number"},
                "issues": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["is_faithful", "confidence"],
        }

        result = await self.llm.verify_faithfulness(original_text, distilled_text, schema)

        return VerificationResult(
            is_faithful=result.get("is_faithful", True),
            confidence=result.get("confidence", 0.5),
            issues=result.get("issues", []),
            original_summary=original_text[:200],
            distilled_summary=distilled_text[:200],
        )

    def _rule_verify(
        self, original_text: str, distilled_text: str
    ) -> VerificationResult:
        """基于规则的验证 (LLM fallback)。

        策略:
        1. 忠实性: 蒸馏产物中的关键词是否在原始文本中出现
        2. 无幻觉: 蒸馏产物中有多少"新"词不在原始中
        """
        original_words = set(original_text.lower().split())
        distilled_words = set(distilled_text.lower().split())

        if not distilled_words:
            return VerificationResult(
                is_faithful=True, confidence=0.5,
                issues=["蒸馏产物为空"]
            )

        # 覆盖率: 蒸馏中有多少词在原始中可以找到
        covered = distilled_words & original_words
        coverage_ratio = len(covered) / len(distilled_words)

        # 幻觉率: 蒸馏中有多少词是"新"的（去除常用停用词）
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "have", "has", "had", "do", "does", "did", "will", "would",
            "could", "should", "may", "might", "shall", "can", "need",
            "and", "or", "but", "if", "then", "else", "when", "while",
            "in", "on", "at", "to", "for", "of", "with", "by", "from",
            "this", "that", "these", "those", "it", "its", "their",
            "的", "了", "在", "是", "和", "与", "或", "不", "有", "中",
            "为", "以", "对", "等", "及", "从", "到", "被", "也", "将",
        }
        meaningful_new = (distilled_words - original_words) - stop_words
        hallucination_ratio = (
            len(meaningful_new) / len(distilled_words) if distilled_words else 0
        )

        # 判定
        issues = []
        is_faithful = True

        if coverage_ratio < self.min_coverage_ratio:
            issues.append(
                f"覆盖率过低 ({coverage_ratio:.2f} < {self.min_coverage_ratio}): "
                f"蒸馏产物与原始数据关联不足"
            )
            is_faithful = False

        if hallucination_ratio > self.max_hallucination_ratio:
            issues.append(
                f"幻觉率过高 ({hallucination_ratio:.2f} > {self.max_hallucination_ratio}): "
                f"可能引入了不存在的信息"
            )
            is_faithful = False

        confidence = (coverage_ratio + (1 - hallucination_ratio)) / 2

        return VerificationResult(
            is_faithful=is_faithful,
            confidence=confidence,
            issues=issues,
            original_summary=original_text[:200],
            distilled_summary=distilled_text[:200],
        )

    def _format_original(self, findings: list[dict]) -> str:
        """格式化原始 findings 为验证文本。"""
        parts = []
        for f in findings:
            text = f.get("finding", "")
            evidence = f.get("evidence", "")
            if text:
                parts.append(text)
            if evidence:
                parts.append(evidence)
        return " ".join(parts)

    def _format_distilled_fact(self, fact: Any) -> str:
        """格式化蒸馏后的 DomainFact 为验证文本。"""
        parts = []
        if hasattr(fact, "description") and fact.description:
            parts.append(fact.description)
        if hasattr(fact, "evidence_text") and fact.evidence_text:
            parts.append(fact.evidence_text)
        if hasattr(fact, "category") and fact.category:
            parts.append(fact.category)
        if hasattr(fact, "subcategory") and fact.subcategory:
            parts.append(fact.subcategory)
        return " ".join(parts)

    def _format_source_facts(self, facts: list[Any]) -> str:
        """格式化来源 facts 为验证文本。"""
        parts = []
        for f in facts:
            if hasattr(f, "description") and f.description:
                parts.append(f.description)
            if hasattr(f, "evidence_text") and f.evidence_text:
                parts.append(f.evidence_text)
        return " ".join(parts)

    def _format_distilled_rule(self, rule: Any) -> str:
        """格式化蒸馏后的 ProceduralRule 为验证文本。"""
        parts = []
        if hasattr(rule, "trigger") and rule.trigger:
            parts.append(rule.trigger)
        if hasattr(rule, "action") and rule.action:
            parts.append(rule.action)
        if hasattr(rule, "rationale") and rule.rationale:
            parts.append(rule.rationale)
        return " ".join(parts)


# ================================================================
# Orchestrator: 统一调度四个模块
# ================================================================

class MemoryCompleteOrchestrator:
    """Phase 2 Complete 层的统一调度器。

    将四个模块组合为一个内聚的管理接口，
    供 Harness 在适当时机调用。

    生命周期钩子:
    - on_phase_start: 触发主动检索
    - on_session_end: 触发跨论文积累 + 衰减 GC
    - on_distillation: 触发蒸馏质量验证
    """

    def __init__(
        self,
        memory_store: Any,
        llm_verifier: LLMVerifier | None = None,
        decay_config: DecayConfig | None = None,
    ):
        self.memory = memory_store
        self.retriever = ProactiveRetriever(memory_store)
        self.decay_model = MemoryDecayModel(decay_config)
        self.cross_paper = CrossPaperKnowledgeAccumulator()
        self.verifier = DistillationQualityVerifier(llm=llm_verifier)

    def on_phase_start(
        self,
        phase: str,
        paper_type: str = "",
        paper_title: str = "",
        current_focus: str = "",
        keywords: list[str] | None = None,
    ) -> str | None:
        """Phase 开始时的钩子: 主动检索相关记忆。

        Returns:
            格式化的检索上下文，或 None
        """
        query = RetrievalQuery(
            phase=phase,
            paper_type=paper_type,
            paper_title=paper_title,
            current_focus=current_focus,
            keywords=keywords or [],
        )
        result = self.retriever.retrieve(query)

        # 同时附加跨论文知识
        cross_context = self.cross_paper.format_cross_knowledge_context(limit=3)

        retrieval_context = self.retriever.format_retrieval_context(result)

        if retrieval_context and cross_context:
            return f"{retrieval_context}\n\n{cross_context}"
        return retrieval_context or cross_context

    def on_session_end(self) -> dict[str, int]:
        """Session 结束时的钩子: 跨论文积累 + 衰减 GC。

        Returns:
            操作统计 {"cross_patterns_new": N, "gc_removed_domain": N, "gc_removed_procedural": N}
        """
        stats = {"cross_patterns_new": 0, "gc_removed_domain": 0, "gc_removed_procedural": 0}

        # 1. 跨论文知识积累
        new_cross = self.cross_paper.analyze_and_promote(self.memory.state.patterns)
        stats["cross_patterns_new"] = len(new_cross)

        # 2. 衰减 GC — Domain Patterns
        if MEMORY_DECAY_ENABLED and self.memory.state.patterns:
            surviving, removed = self.decay_model.gc_with_decay(
                self.memory.state.patterns,
                pattern_type="domain",
                max_size=100,
            )
            self.memory.state.patterns = surviving
            stats["gc_removed_domain"] = removed

        # 3. 衰减 GC — Procedural Patterns
        if MEMORY_DECAY_ENABLED and self.memory.state.procedures:
            surviving, removed = self.decay_model.gc_with_decay(
                self.memory.state.procedures,
                pattern_type="procedural",
                max_size=50,
            )
            self.memory.state.procedures = surviving
            stats["gc_removed_procedural"] = removed

        return stats

    async def on_distillation(
        self,
        original_findings: list[dict],
        distilled_facts: list[Any],
    ) -> tuple[list[Any], list[Any]]:
        """蒸馏后的钩子: 验证忠实性，过滤不合格的。

        Args:
            original_findings: 原始 findings
            distilled_facts: 蒸馏产出的 DomainFacts

        Returns:
            (通过验证的 facts, 被拒绝的 facts)
        """
        results = await self.verifier.verify_batch(original_findings, distilled_facts)
        passed, failed = self.verifier.filter_faithful(distilled_facts, results)

        if failed:
            logger.info(
                "Distillation quality check: %d passed, %d rejected",
                len(passed), len(failed),
            )

        return passed, failed

    def get_cross_paper_state(self) -> list[dict]:
        """获取跨论文知识的序列化状态（用于持久化）。"""
        return self.cross_paper.serialize()

    def load_cross_paper_state(self, data: list[dict]) -> None:
        """加载跨论文知识状态。"""
        self.cross_paper.deserialize(data)
