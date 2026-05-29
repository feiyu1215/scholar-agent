"""
core/memory.py — 跨会话认知记忆 (Phase 15 + Phase 54)

设计原则:
    - 与 WorkspaceState 正交: State 管当前会话，Memory 管历史会话
    - Harness 拥有 Memory，LLM 不直接访问
    - 注入方式: format_context 在有历史记忆时追加精简摘要 (< 500 tokens)
    - 零外部依赖: 纯 JSON 文件持久化
    - 渐进退化: 没有 memory 文件时系统完全正常工作

三层记忆架构:
    Layer 1 — Session Memory (会话级):
        每次会话结束时沉淀: 论文标题、核心发现、最终决定、关键争论点
        作用: 下次审同一篇论文时 Agent 能"记得上次聊到哪"

    Layer 2 — Domain Knowledge (领域级, 声明性):
        跨论文积累的模式识别: "DID 论文常见的平行趋势问题"、"计量论文的 overclaim 模式"
        作用: 审第 21 篇论文时比审第 1 篇更有经验
        性质: WHAT — 什么问题存在

    Layer 3 — Procedural Memory (程序性, Phase 54):
        跨会话积累的"如何高效工作"知识:
        - 策略有效性: "deep_investigation 在 findings>=3 后切入效率最高"
        - 高效工具序列: "read_section→search_literature→update_findings 是高产序列"
        - 低效模式: "连续 5 轮 read_section 不产出 findings 是低效信号"
        作用: Agent 不仅知道"什么问题存在"，还知道"怎么高效地找到问题"
        性质: HOW — 如何高效工作

不做的事:
    - 不做 intra-session 压缩（Token Pipeline 已解决）
    - 不做四层 TencentDB 架构（overengineering for our scale）
    - 不做向量检索（论文数量级不需要 embedding search）
"""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field, fields as dc_fields, asdict, MISSING
from typing import Any, Optional


# ============================================================
# Data Models
# ============================================================

@dataclass
class SessionRecord:
    """一次完成会话的精简记录。"""

    # 身份信息
    session_id: str  # 时间戳 + paper hash
    paper_id: str  # 论文内容的 hash（同一篇论文的不同会话共享）
    paper_title: str  # 论文标题（人类可读）
    timestamp: str  # ISO format

    # 认知产出
    findings_summary: list[str]  # 核心发现摘要（每条 < 80 字符）
    decision: str  # accept / major_revision / minor_revision / reject / incomplete
    key_issues: list[str]  # 最重要的 2-3 个问题（供下次快速回忆）

    # 统计
    loop_turns_total: int = 0
    conversation_turns: int = 0
    total_tokens: int = 0

    # 用户交互摘要
    user_questions: list[str] = field(default_factory=list)  # 用户提问的精炼版


@dataclass
class DomainPattern:
    """跨论文积累的领域级模式（Layer 2: 声明性 — WHAT）。"""

    pattern_id: str  # content hash
    category: str  # methodology / overclaim / statistics / writing / logic
    description: str  # 模式描述（< 120 字符）
    evidence_count: int = 1  # 在多少篇论文中见过
    first_seen: str = ""  # ISO timestamp
    last_seen: str = ""
    examples: list[str] = field(default_factory=list)  # 来自哪些论文（paper_id 列表）


@dataclass
class ProceduralPattern:
    """
    跨会话积累的程序性知识（Layer 3: 程序性 — HOW）。Phase 54。

    与 DomainPattern 的区别:
    - DomainPattern 记录"什么问题存在"（声明性知识）
    - ProceduralPattern 记录"如何高效地工作"（程序性知识）

    类比人类专家:
    - 新手知道"DID 论文可能有平行趋势问题"（DomainPattern）
    - 专家还知道"审 DID 论文时，先读 methods 找 identification strategy，
      再搜索 parallel trends test，效率最高"（ProceduralPattern）

    设计原则:
    - 从 tool_call_history + CognitiveState 中自动提取，不需要 Agent 显式记录
    - 注入时遵循 §4.3（信息呈现，不是指令）：呈现"你过去的有效策略"，Agent 自主决定是否采纳
    - 渐进退化：没有 procedural patterns 时系统完全正常
    """

    pattern_id: str  # content hash
    category: str  # strategy_effectiveness / tool_sequence / anti_pattern
    description: str  # 模式描述（< 150 字符）
    trigger_context: str  # 触发条件描述（"当 findings>=3 且 read_ratio>0.5 时"）
    effectiveness_score: float = 0.0  # 该模式的效率评分（0.0~1.0）
    evidence_count: int = 1  # 在多少次会话中验证过
    first_seen: str = ""  # ISO timestamp
    last_seen: str = ""


@dataclass
class MemoryState:
    """完整的跨会话记忆状态。"""

    # Layer 1: Session Records（按 paper_id 分组）
    sessions: list[SessionRecord] = field(default_factory=list)

    # Layer 2: Domain Patterns（声明性 — WHAT）
    patterns: list[DomainPattern] = field(default_factory=list)

    # Layer 3: Procedural Patterns（程序性 — HOW, Phase 54）
    procedures: list[ProceduralPattern] = field(default_factory=list)

    # === V3 NEW: Hierarchical Experience Store ===
    # L0: Section-level micro-experience (sliding window 500)
    section_experiences: list[dict] = field(default_factory=list)
    # L1: Session-level V3 enhanced experience (sliding window 100)
    session_experiences_v3: list[dict] = field(default_factory=list)
    # L2: Evolution-level metadata (permanent)
    evolution_records: list[dict] = field(default_factory=list)
    # IntraSession Contrast results
    contrast_results: list[dict] = field(default_factory=list)
    # Per paper_type maturity levels
    maturity_levels: dict = field(default_factory=dict)
    # P2-fix11: Habit combination effectiveness log (persisted cross-session)
    combination_log: list[dict] = field(default_factory=list)
    # P2-fix12: Evolution engine session stats history (sliding window 50)
    evolution_stats: list[dict] = field(default_factory=list)

    # === V3 Phase 2: Tri-Frequency MetaReflector state ===
    # FastReflector alerts (injected into next session's context)
    fast_reflect_alerts: list[str] = field(default_factory=list)
    # Internal counters for trigger tracking (prefixed with _ convention but stored)
    _last_fast_reflect_count: int = 0
    _last_deep_reflect_count: int = 0

    # Meta
    version: str = "3.0"  # Bumped for V3 Hierarchical Experience (backward compatible)
    last_updated: str = ""


# ============================================================
# Memory Store — 持久化与检索
# ============================================================

class MemoryStore:
    """
    跨会话记忆的持久化存储。

    使用方式:
        store = MemoryStore(base_dir="path/to/workspace")
        store.load()  # 从磁盘加载
        store.recall_for_paper(paper_id)  # 检索特定论文的历史
        store.persist_session(record)  # 保存新会话
        store.save()  # 写入磁盘
    """

    MEMORY_FILE = "memory.json"

    def __init__(self, base_dir: str | Path):
        """
        Args:
            base_dir: 存储目录（通常是 .workspace/ 或项目根目录下的 .memory/）
        """
        self.base_dir = Path(base_dir)
        self.memory_path = self.base_dir / self.MEMORY_FILE
        self.state = MemoryState()
        self._loaded = False

    def load(self) -> bool:
        """
        从磁盘加载记忆。如果文件不存在则使用空状态（渐进退化）。

        Returns:
            True if loaded from file, False if using empty state
        """
        if self.memory_path.exists():
            try:
                raw = json.loads(self.memory_path.read_text(encoding="utf-8"))
                self.state = self._deserialize(raw)
                self._loaded = True
                return True
            except (json.JSONDecodeError, KeyError, TypeError):
                # 文件损坏，使用空状态但不删除文件
                self.state = MemoryState()
                self._loaded = False
                return False
        self._loaded = False
        return False

    def save(self):
        """
        将当前记忆状态持久化到磁盘（原子写入）。

        使用 write-to-tmp-then-rename 模式：
        - 先写入临时文件 memory.json.tmp
        - fsync 确保数据落盘
        - 原子 rename 覆盖目标文件
        - 这样即使进程崩溃，也不会损坏已有的 memory.json
        """
        import os
        import tempfile

        self.state.last_updated = datetime.now(timezone.utc).isoformat()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        raw = self._serialize(self.state)
        content = json.dumps(raw, ensure_ascii=False, indent=2)

        # 原子写入：先写临时文件，再 rename
        tmp_path = self.memory_path.with_suffix(".json.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            # 原子 rename（POSIX 上是原子操作）
            tmp_path.replace(self.memory_path)
        except Exception:
            # 如果 rename 失败，尝试清理临时文件
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise

    # ----------------------------------------------------------
    # Session Layer Operations
    # ----------------------------------------------------------

    def persist_session(self, record: SessionRecord):
        """保存一个会话记录。"""
        self.state.sessions.append(record)
        # 限制总记录数（保留最近 50 个会话）
        if len(self.state.sessions) > 50:
            self.state.sessions = self.state.sessions[-50:]

    def recall_for_paper(self, paper_id: str) -> list[SessionRecord]:
        """检索特定论文的所有历史会话。按时间倒序。"""
        matching = [s for s in self.state.sessions if s.paper_id == paper_id]
        return sorted(matching, key=lambda s: s.timestamp, reverse=True)

    def recall_recent(self, limit: int = 5) -> list[SessionRecord]:
        """检索最近的 N 个会话记录。"""
        return sorted(self.state.sessions, key=lambda s: s.timestamp, reverse=True)[:limit]

    # ----------------------------------------------------------
    # Domain Pattern Layer Operations
    # ----------------------------------------------------------

    def add_or_reinforce_pattern(self, category: str, description: str, paper_id: str):
        """
        添加或强化一个领域模式。

        如果描述相似的模式已存在（基于前缀匹配），则增加 evidence_count。
        否则创建新模式。
        """
        now = datetime.now(timezone.utc).isoformat()

        # 寻找已有的相似模式（简单前缀匹配，避免引入 NLP 依赖）
        for pattern in self.state.patterns:
            if pattern.category == category and self._is_similar(pattern.description, description):
                pattern.evidence_count += 1
                pattern.last_seen = now
                if paper_id not in pattern.examples:
                    pattern.examples.append(paper_id)
                    # 最多保留 10 个例子
                    pattern.examples = pattern.examples[-10:]
                return

        # 新模式
        pattern_id = hashlib.md5(f"{category}:{description}".encode()).hexdigest()[:12]
        self.state.patterns.append(DomainPattern(
            pattern_id=pattern_id,
            category=category,
            description=description,
            evidence_count=1,
            first_seen=now,
            last_seen=now,
            examples=[paper_id],
        ))

        # 限制总模式数（保留 evidence_count 最高的 100 个）
        if len(self.state.patterns) > 100:
            self.state.patterns.sort(key=lambda p: p.evidence_count, reverse=True)
            self.state.patterns = self.state.patterns[:100]

    def get_relevant_patterns(self, categories: list[str] | None = None, limit: int = 10) -> list[DomainPattern]:
        """
        获取相关的领域模式。按 evidence_count 排序。

        Args:
            categories: 限定类别。None 表示全部。
            limit: 最多返回多少条。
        """
        patterns = self.state.patterns
        if categories:
            patterns = [p for p in patterns if p.category in categories]
        patterns = sorted(patterns, key=lambda p: p.evidence_count, reverse=True)
        return patterns[:limit]

    # ----------------------------------------------------------
    # Procedural Pattern Layer Operations (Phase 54)
    # ----------------------------------------------------------

    def add_or_reinforce_procedure(self, category: str, description: str,
                                    trigger_context: str, effectiveness_score: float):
        """
        添加或强化一个程序性模式。

        如果描述相似的模式已存在，则增加 evidence_count 并更新 effectiveness_score（加权平均）。
        否则创建新模式。

        Args:
            category: strategy_effectiveness / tool_sequence / anti_pattern
            description: 模式描述（< 150 字符）
            trigger_context: 触发条件描述
            effectiveness_score: 效率评分（0.0~1.0）
        """
        now = datetime.now(timezone.utc).isoformat()

        # 寻找已有的相似模式
        for proc in self.state.procedures:
            if proc.category == category and self._is_similar(proc.description, description):
                # 加权平均更新 effectiveness_score
                old_weight = proc.evidence_count
                new_weight = 1
                total = old_weight + new_weight
                proc.effectiveness_score = (
                    proc.effectiveness_score * old_weight + effectiveness_score * new_weight
                ) / total
                proc.evidence_count += 1
                proc.last_seen = now
                return

        # 新模式
        pattern_id = hashlib.md5(f"proc:{category}:{description}".encode()).hexdigest()[:12]
        self.state.procedures.append(ProceduralPattern(
            pattern_id=pattern_id,
            category=category,
            description=description,
            trigger_context=trigger_context,
            effectiveness_score=effectiveness_score,
            evidence_count=1,
            first_seen=now,
            last_seen=now,
        ))

        # 限制总数（保留 effectiveness_score * evidence_count 最高的 50 个）
        if len(self.state.procedures) > 50:
            self.state.procedures.sort(
                key=lambda p: p.effectiveness_score * p.evidence_count, reverse=True
            )
            self.state.procedures = self.state.procedures[:50]

    def get_relevant_procedures(self, categories: list[str] | None = None,
                                 limit: int = 5) -> list[ProceduralPattern]:
        """
        获取相关的程序性模式。按 effectiveness_score * evidence_count 排序。

        Args:
            categories: 限定类别。None 表示全部。
            limit: 最多返回多少条。
        """
        procedures = self.state.procedures
        if categories:
            procedures = [p for p in procedures if p.category in categories]
        procedures = sorted(
            procedures,
            key=lambda p: p.effectiveness_score * p.evidence_count,
            reverse=True,
        )
        return procedures[:limit]

    # ----------------------------------------------------------
    # V3: Hierarchical Experience Store
    # ----------------------------------------------------------

    MAX_SECTION_EXPERIENCES = 500   # L0 sliding window
    MAX_SESSION_EXPERIENCES_V3 = 100  # L1 sliding window

    def persist_section_experience(self, exp: dict) -> None:
        """
        Store L0 section-level experience, maintain sliding window.

        Args:
            exp: Section experience dict with keys:
                session_id, section_name, paper_type, turns_spent,
                findings_produced, evidence_chains_built, hypotheses_generated,
                active_habit_ids, tokens_consumed, findings_per_token
        """
        self.state.section_experiences.append(exp)
        if len(self.state.section_experiences) > self.MAX_SECTION_EXPERIENCES:
            self.state.section_experiences = (
                self.state.section_experiences[-self.MAX_SECTION_EXPERIENCES:]
            )

    def persist_session_experience_v3(self, exp: dict) -> None:
        """
        Store L1 session-level experience (V3 enhanced), maintain sliding window.

        Args:
            exp: Session experience dict with V3 fields (phase_a/b, pcg_coverage, etc.)
        """
        self.state.session_experiences_v3.append(exp)
        if len(self.state.session_experiences_v3) > self.MAX_SESSION_EXPERIENCES_V3:
            self.state.session_experiences_v3 = (
                self.state.session_experiences_v3[-self.MAX_SESSION_EXPERIENCES_V3:]
            )

    def persist_evolution_record(self, record: dict) -> None:
        """Store L2 evolution record. Permanent (no window)."""
        self.state.evolution_records.append(record)

    def persist_contrast_result(self, result: dict) -> None:
        """Store IntraSession contrast result."""
        self.state.contrast_results.append(result)

    def get_section_experiences_for_habit(
        self, habit_id: str
    ) -> tuple[list[dict], list[dict]]:
        """
        Get section experiences split by whether habit was active.

        Returns: (with_habit, without_habit)
        """
        with_h = [
            e for e in self.state.section_experiences
            if habit_id in e.get("active_habit_ids", [])
        ]
        without_h = [
            e for e in self.state.section_experiences
            if habit_id not in e.get("active_habit_ids", [])
        ]
        return with_h, without_h

    def get_historical_baseline(self) -> dict[str, float]:
        """
        Compute per-paper_type avg findings_per_1k_tokens baseline.

        Used by compute_relative_effectiveness() for relative efficiency scoring.
        """
        from collections import defaultdict
        totals: dict = defaultdict(lambda: {"findings": 0, "tokens": 0})
        for exp in self.state.session_experiences_v3:
            pt = exp.get("paper_type", "unknown")
            totals[pt]["findings"] += exp.get("findings_count", 0)
            totals[pt]["tokens"] += exp.get("total_tokens", 1)
        return {
            pt: data["findings"] / max(data["tokens"] / 1000, 0.1)
            for pt, data in totals.items()
        }

    # ----------------------------------------------------------
    # Memory Garbage Collection (Phase 0 of C3 Gödel Agent)
    # ----------------------------------------------------------

    def gc_procedures(
        self,
        max_size: int = 50,
        min_effectiveness: float = 0.3,
        max_age_days: int = 60,
    ) -> int:
        """
        程序性记忆垃圾回收。

        淘汰规则（按优先级）:
        1. effectiveness < min_effectiveness 且 evidence <= 1 → 删除（低质量+未验证）
        2. last_seen > max_age_days 前 → 删除（长期未强化的记忆衰退）
        3. 如果仍超过 max_size → 按 effectiveness * evidence 从低到高裁剪

        保护规则:
        - evidence >= 3 的 pattern 永不被规则 1/2 自动删除（已充分验证的知识）
        - 规则 3 不设保护（硬容量限制兜底）

        Args:
            max_size: procedures 列表的目标上限
            min_effectiveness: 低于此值且 evidence<=1 的直接淘汰
            max_age_days: 超过此天数未 reinforce 的淘汰

        Returns:
            本次删除的 pattern 数量
        """
        if not self.state.procedures:
            return 0

        original_count = len(self.state.procedures)
        now = datetime.now(timezone.utc)

        surviving: list[ProceduralPattern] = []

        for proc in self.state.procedures:
            # 保护规则：evidence >= 3 的永不被规则 1/2 删除
            if proc.evidence_count >= 3:
                surviving.append(proc)
                continue

            # 规则 1：低效 + 未验证 → 删除
            if proc.effectiveness_score < min_effectiveness and proc.evidence_count <= 1:
                continue  # 不保留

            # 规则 2：长期未强化 → 删除
            if proc.last_seen:
                try:
                    last_seen_dt = datetime.fromisoformat(proc.last_seen)
                    # 确保 timezone-aware 比较
                    if last_seen_dt.tzinfo is None:
                        last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)
                    age = (now - last_seen_dt).days
                    if age > max_age_days:
                        continue  # 不保留
                except (ValueError, TypeError):
                    pass  # 解析失败则不淘汰

            surviving.append(proc)

        # 规则 3：硬容量限制
        if len(surviving) > max_size:
            surviving.sort(
                key=lambda p: p.effectiveness_score * p.evidence_count, reverse=True
            )
            surviving = surviving[:max_size]

        self.state.procedures = surviving
        return original_count - len(self.state.procedures)

    # ----------------------------------------------------------
    # Context Generation — 给 format_context 用
    # ----------------------------------------------------------

    def format_memory_context(self, paper_id: str | None = None) -> str | None:
        """
        生成注入 system prompt 的跨会话记忆摘要。

        设计目标: < 500 tokens（~1500 字符），只提供"我记得什么"的信号，
        不提供详细内容（Agent 如需详细信息可用工具检索）。

        Args:
            paper_id: 当前论文的 ID。如果提供，会包含此论文的历史。

        Returns:
            格式化的记忆摘要字符串。如果没有任何记忆则返回 None。
        """
        if not self.state.sessions and not self.state.patterns and not self.state.procedures:
            return None

        parts = []

        # 1. 当前论文的历史（如果有）
        if paper_id:
            paper_sessions = self.recall_for_paper(paper_id)
            if paper_sessions:
                latest = paper_sessions[0]
                parts.append("📚 你之前审阅过这篇论文:")
                parts.append(f"  上次会话: {latest.timestamp[:10]} | 决定: {latest.decision}")
                if latest.key_issues:
                    issues_str = "; ".join(latest.key_issues[:3])
                    parts.append(f"  核心问题: {issues_str}")
                if latest.user_questions:
                    parts.append(f"  用户关注: {'; '.join(latest.user_questions[:2])}")
                if len(paper_sessions) > 1:
                    parts.append(f"  (共 {len(paper_sessions)} 次审阅历史)")

        # 2. 领域经验摘要
        strong_patterns = [p for p in self.state.patterns if p.evidence_count >= 3]
        if strong_patterns:
            parts.append("")
            parts.append(f"🧠 你的领域经验 ({len(strong_patterns)} 条高频模式):")
            for p in strong_patterns[:5]:
                parts.append(f"  [{p.category}] {p.description} (见过 {p.evidence_count} 次)")

        # 3. 程序性记忆摘要（Phase 54: HOW to work efficiently）
        effective_procedures = self.get_relevant_procedures(limit=3)
        if effective_procedures:
            parts.append("")
            parts.append("⚡ 你的高效工作模式:")
            for proc in effective_procedures:
                score_pct = int(proc.effectiveness_score * 100)
                parts.append(
                    f"  [{proc.category}] {proc.description} "
                    f"(效率 {score_pct}%, 验证 {proc.evidence_count} 次)"
                )

        # 4. 审阅统计
        total_sessions = len(self.state.sessions)
        if total_sessions > 0:
            parts.append("")
            parts.append(f"📊 累计审阅: {total_sessions} 篇论文")

        return "\n".join(parts) if parts else None

    # ----------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------

    @staticmethod
    def compute_paper_id(paper_sections: dict[str, str]) -> str:
        """
        计算论文的唯一 ID（基于内容 hash）。

        使用 abstract + title + methods 的前 500 字符作为指纹，
        这样小修改不会改变 paper_id，但本质不同的论文会有不同 id。
        """
        fingerprint_parts = []
        for key in sorted(paper_sections.keys()):
            if any(k in key.lower() for k in ["abstract", "title", "introduction", "method"]):
                fingerprint_parts.append(paper_sections[key][:500])
        if not fingerprint_parts:
            # Fallback: 用全部内容的前 2000 字符
            all_content = "".join(paper_sections.values())[:2000]
            fingerprint_parts.append(all_content)

        fingerprint = "".join(fingerprint_parts)
        return hashlib.sha256(fingerprint.encode()).hexdigest()[:16]

    @staticmethod
    def _normalize_for_similarity(text: str) -> str:
        """
        模板化文本：去掉数值、百分比、浮点数，统一为占位符。

        目的：让 "连续 6 次 read_section" 和 "连续 4 次 read_section"
        归一化为相同模板，从而能被 _is_similar 正确匹配。
        """
        import re
        # 去掉数值（整数、浮点数、百分比）→ 统一占位符 <N>
        normalized = re.sub(r'\d+\.?\d*%?', '<N>', text)
        return normalized.lower()

    @staticmethod
    def _tokenize_mixed(text: str) -> set[str]:
        """
        混合分词：对中英混合文本做 token 提取（零外部依赖）。

        策略：
        - 英文/数字部分：按空格和标点分词
        - 中文部分：字符级 bigram（相邻两字组合）
        - 这样中文 "平行趋势假设" → {"平行", "行趋", "趋势", "势假", "假设"}

        比 split() 按空格分词对中文的覆盖好 100 倍。
        """
        import re
        tokens: set[str] = set()

        # 拆分为连续的中文段和非中文段
        # 中文 Unicode 范围（含 CJK 统一汉字 + 扩展）
        segments = re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf]+|[a-z0-9_]+', text.lower())

        for seg in segments:
            if '\u4e00' <= seg[0] <= '\u9fff' or '\u3400' <= seg[0] <= '\u4dbf':
                # 中文段：bigram
                if len(seg) == 1:
                    tokens.add(seg)
                else:
                    for i in range(len(seg) - 1):
                        tokens.add(seg[i:i+2])
            else:
                # 英文/数字段：整个作为一个 token
                if len(seg) >= 2:  # 忽略单字符噪声
                    tokens.add(seg)

        return tokens

    @staticmethod
    def _is_similar(existing: str, new: str) -> bool:
        """
        相似度判断（支持中英混合，零外部依赖）。

        策略：
        1. 先做模板化（去数值）→ 让 "连续6次" 和 "连续4次" 视为相同
        2. 对模板化后的文本做混合分词（中文 bigram + 英文 word）
        3. Jaccard 相似度 > 0.4 则认为相似

        阈值 0.4（比原来的 0.5 宽松）：
        - 允许 LLM 生成的反思有一定措辞差异
        - 但核心关键词（工具名、中文术语）匹配即可
        """
        norm_a = MemoryStore._normalize_for_similarity(existing)
        norm_b = MemoryStore._normalize_for_similarity(new)

        tokens_a = MemoryStore._tokenize_mixed(norm_a)
        tokens_b = MemoryStore._tokenize_mixed(norm_b)

        if not tokens_a or not tokens_b:
            return False

        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b
        jaccard = len(intersection) / len(union) if union else 0.0
        return jaccard > 0.4

    @staticmethod
    def _serialize(state: MemoryState) -> dict:
        """将 MemoryState 序列化为 JSON-safe dict。"""
        data: dict[str, Any] = {
            "version": state.version,
            "last_updated": state.last_updated,
            "sessions": [asdict(s) for s in state.sessions],
            "patterns": [asdict(p) for p in state.patterns],
            "procedures": [asdict(p) for p in state.procedures],
        }
        # V3 fields (only serialize if non-empty for backward compat)
        if state.section_experiences:
            data["section_experiences"] = state.section_experiences
        if state.session_experiences_v3:
            data["session_experiences_v3"] = state.session_experiences_v3
        if state.evolution_records:
            data["evolution_records"] = state.evolution_records
        if state.contrast_results:
            data["contrast_results"] = state.contrast_results
        if state.maturity_levels:
            data["maturity_levels"] = state.maturity_levels
        # P2-fix11: Habit combination log (persisted cross-session)
        if state.combination_log:
            data["combination_log"] = state.combination_log
        # P2-fix12: Evolution stats history (sliding window 50)
        if state.evolution_stats:
            data["evolution_stats"] = state.evolution_stats
        # V3 Phase 2: MetaReflector state
        if state.fast_reflect_alerts:
            data["fast_reflect_alerts"] = state.fast_reflect_alerts
        data["_last_fast_reflect_count"] = state._last_fast_reflect_count
        data["_last_deep_reflect_count"] = state._last_deep_reflect_count
        return data

    @staticmethod
    def _deserialize(raw: dict) -> MemoryState:
        """从 JSON dict 反序列化为 MemoryState。向后兼容 v1.0/v1.1 格式。"""
        state = MemoryState(
            version=raw.get("version", "1.0"),
            last_updated=raw.get("last_updated", ""),
        )
        for s in raw.get("sessions", []):
            state.sessions.append(_safe_construct(SessionRecord, s))
        for p in raw.get("patterns", []):
            state.patterns.append(_safe_construct(DomainPattern, p))
        for proc in raw.get("procedures", []):
            state.procedures.append(_safe_construct(ProceduralPattern, proc))
        # V3 fields (backward compat: missing = empty)
        state.section_experiences = raw.get("section_experiences", [])
        state.session_experiences_v3 = raw.get("session_experiences_v3", [])
        state.evolution_records = raw.get("evolution_records", [])
        state.contrast_results = raw.get("contrast_results", [])
        state.maturity_levels = raw.get("maturity_levels", {})
        # P2-fix11/12: combination_log & evolution_stats
        state.combination_log = raw.get("combination_log", [])
        state.evolution_stats = raw.get("evolution_stats", [])
        # V3 Phase 2: MetaReflector state
        state.fast_reflect_alerts = raw.get("fast_reflect_alerts", [])
        state._last_fast_reflect_count = raw.get("_last_fast_reflect_count", 0)
        state._last_deep_reflect_count = raw.get("_last_deep_reflect_count", 0)
        return state


# ============================================================
# Session Builder — 从 WorkspaceState 提取会话摘要
# ============================================================

def build_session_record(
    paper_id: str,
    paper_title: str,
    findings: list[dict],
    conversation_turns: int,
    loop_turns: int,
    total_tokens: int,
    user_messages: list[str] | None = None,
) -> SessionRecord:
    """
    从会话结束时的状态构建 SessionRecord。

    这是 Harness 在会话结束时调用的"沉淀"函数——
    它不是简单复制 findings，而是做智能压缩：
    - findings → 只保留 high priority 的摘要
    - user messages → 提取问题句
    - decision → 从 findings 中推断

    Args:
        paper_id: 论文唯一标识
        paper_title: 论文标题
        findings: 完整的 findings 列表
        conversation_turns: 对话轮数
        loop_turns: 总 loop 轮次
        total_tokens: 总 token 消耗
        user_messages: 用户发送的消息列表（可选）

    Returns:
        压缩后的 SessionRecord
    """
    now = datetime.now(timezone.utc).isoformat()
    session_id = f"{now[:10]}_{paper_id[:8]}"

    # 提取核心发现摘要（只保留 high 和部分 medium）
    high_findings = [f for f in findings if f.get("priority") == "high"]
    medium_findings = [f for f in findings if f.get("priority") == "medium"]

    findings_summary = []
    for f in high_findings[:5]:
        summary = f["finding"][:80]
        findings_summary.append(f"[high] {summary}")
    for f in medium_findings[:3]:
        summary = f["finding"][:60]
        findings_summary.append(f"[med] {summary}")

    # 推断决定
    decision = _infer_decision(findings)

    # 提取关键问题（top 3 high priority）
    key_issues = [f["finding"][:100] for f in high_findings[:3]]

    # 提取用户问题（如果有）
    user_questions = []
    if user_messages:
        for msg in user_messages[-5:]:  # 最近 5 条
            if "?" in msg or "？" in msg or len(msg) < 100:
                user_questions.append(msg[:80])
        user_questions = user_questions[:3]

    return SessionRecord(
        session_id=session_id,
        paper_id=paper_id,
        paper_title=paper_title,
        timestamp=now,
        findings_summary=findings_summary,
        decision=decision,
        key_issues=key_issues,
        loop_turns_total=loop_turns,
        conversation_turns=conversation_turns,
        total_tokens=total_tokens,
        user_questions=user_questions,
    )


def extract_procedural_patterns(
    tool_call_history: list[str],
    findings_count: int,
    loop_turns: int,
    strategy_transitions: list[tuple[str, str]] | None = None,
) -> list[tuple[str, str, str, float]]:
    """
    从一次会话的行为数据中提取程序性模式（Phase 54）。

    分析 Agent 的工具调用序列和策略切换，提取"如何高效工作"的知识。
    返回 (category, description, trigger_context, effectiveness_score) 列表。

    提取三类模式:
    1. strategy_effectiveness — 策略切换的时机与效果
    2. tool_sequence — 高产出的工具调用序列
    3. anti_pattern — 低效行为模式

    Args:
        tool_call_history: 工具调用名称序列（按时间顺序）
        findings_count: 本次会话产出的 findings 数量
        loop_turns: 总 loop 轮次
        strategy_transitions: 策略切换记录 [(from_strategy, to_strategy), ...]

    Returns:
        [(category, description, trigger_context, effectiveness_score), ...]
    """
    patterns: list[tuple[str, str, str, float]] = []

    if not tool_call_history:
        return patterns

    # 计算基础效率指标
    efficiency = findings_count / max(loop_turns, 1)  # findings per turn

    # --- 1. 高产出工具序列检测 ---
    # 寻找 3-gram 工具序列中，紧跟 update_findings 的模式
    productive_sequences = _find_productive_sequences(tool_call_history)
    for seq, count in productive_sequences:
        if count >= 2:  # 至少出现 2 次
            seq_str = "→".join(seq)
            # 模板化：description 不含具体 count 数值，让跨会话可累积
            patterns.append((
                "tool_sequence",
                f"高产序列: {seq_str}",
                f"当需要产出 findings 时，优先使用 {seq_str} 序列",
                min(0.6 + count * 0.1, 0.95),
            ))

    # --- 2. 低效模式检测 ---
    # 连续重复同一工具超过 4 次且无 update_findings
    anti_patterns = _find_anti_patterns(tool_call_history)
    for tool_name, repeat_count in anti_patterns:
        # 模板化：description 不含具体重复次数
        patterns.append((
            "anti_pattern",
            f"低效重复: 连续多次 {tool_name} 未产出 findings",
            f"当连续调用 {tool_name} 超过 3 次时应切换策略",
            0.2,  # 低效模式的 effectiveness 低
        ))

    # --- 3. 策略有效性 ---
    if strategy_transitions:
        for from_s, to_s in strategy_transitions:
            # 策略切换后效率是否提升（简化判断：如果整体效率高，认为切换有效）
            if efficiency > 0.3:  # 每 3 轮产出 1 个 finding 以上
                # 模板化：不含具体效率数值
                patterns.append((
                    "strategy_effectiveness",
                    f"策略切换 {from_s}→{to_s} 有效",
                    f"当 {from_s} 策略进展缓慢时切换到 {to_s}",
                    min(efficiency * 2, 0.9),
                ))

    # --- 4. 整体效率模式 ---
    read_count = tool_call_history.count("read_section")
    search_count = tool_call_history.count("search_literature")
    total_calls = len(tool_call_history)

    if total_calls >= 5 and efficiency > 0.4:
        read_ratio = read_count / total_calls
        if read_ratio > 0.3 and search_count > 0:
            # 模板化：用区间描述代替精确数字，确保跨会话可累积
            ratio_band = "高" if read_ratio > 0.5 else "中"
            patterns.append((
                "strategy_effectiveness",
                f"高效会话模式: read占比{ratio_band}, 配合 search_literature",
                "当开始新审阅时参考此工具分配比例",
                min(efficiency * 1.5, 0.9),
            ))

    return patterns


def _find_productive_sequences(
    tool_calls: list[str],
) -> list[tuple[tuple[str, ...], int]]:
    """
    找出紧跟 update_findings 的 3-gram 工具序列。

    Returns:
        [(sequence_tuple, count), ...] 按 count 降序
    """
    from collections import Counter

    if len(tool_calls) < 4:
        return []

    productive_trigrams: Counter[tuple[str, ...]] = Counter()

    for i in range(len(tool_calls) - 3):
        # 如果第 4 个调用是 update_findings，前 3 个是高产序列
        if tool_calls[i + 3] == "update_findings":
            trigram = (tool_calls[i], tool_calls[i + 1], tool_calls[i + 2])
            # 排除包含 update_findings 本身的序列（避免自引用）
            if "update_findings" not in trigram:
                productive_trigrams[trigram] += 1

    # 返回出现 >= 2 次的序列
    results = [(seq, cnt) for seq, cnt in productive_trigrams.most_common(5) if cnt >= 2]
    return results


def _find_anti_patterns(tool_calls: list[str]) -> list[tuple[str, int]]:
    """
    检测连续重复调用同一工具超过 4 次且中间无 update_findings 的模式。

    Returns:
        [(tool_name, max_repeat_count), ...]
    """
    if len(tool_calls) < 5:
        return []

    anti_patterns: list[tuple[str, int]] = []
    current_tool = tool_calls[0]
    current_count = 1
    max_repeats: dict[str, int] = {}

    for i in range(1, len(tool_calls)):
        if tool_calls[i] == current_tool:
            current_count += 1
        else:
            if current_count >= 4 and current_tool != "update_findings":
                if current_tool not in max_repeats or current_count > max_repeats[current_tool]:
                    max_repeats[current_tool] = current_count
            current_tool = tool_calls[i]
            current_count = 1

    # 处理最后一段
    if current_count >= 4 and current_tool != "update_findings":
        if current_tool not in max_repeats or current_count > max_repeats[current_tool]:
            max_repeats[current_tool] = current_count

    for tool_name, count in max_repeats.items():
        anti_patterns.append((tool_name, count))

    return anti_patterns


def extract_domain_patterns(findings: list[dict], paper_id: str) -> list[tuple[str, str]]:
    """
    从一次会话的 findings 中提取可积累的领域模式。

    返回 (category, description) 列表。只提取 verified 的 high/medium 级别发现。
    这些会被 MemoryStore.add_or_reinforce_pattern() 处理。
    """
    patterns = []

    for f in findings:
        if f.get("status") != "verified":
            continue
        if f.get("priority") not in ("high", "medium"):
            continue

        finding_text = f.get("finding", "")
        category = _categorize_finding(finding_text)
        if category:
            # 精简描述（去掉具体论文细节，保留模式）
            description = _generalize_finding(finding_text)
            if description:
                patterns.append((category, description))

    return patterns


# ============================================================
# Internal Helpers
# ============================================================


def _safe_construct(cls, data: dict):
    """
    安全构造 dataclass 实例：过滤未知字段、为缺失必填字段提供合理默认值。

    解决 schema 演进问题：当 memory JSON 由旧版本产生时可能有多余/缺失字段，
    直接 cls(**data) 会 TypeError。此函数保证向后兼容。
    """
    known_fields = {f.name for f in dc_fields(cls)}
    # 只保留 cls 定义的字段
    filtered = {k: v for k, v in data.items() if k in known_fields}
    # 对于没有默认值的必填字段，如果缺失则提供类型零值
    for f in dc_fields(cls):
        if f.name not in filtered:
            # 有 default 或 default_factory 的字段会由 dataclass 自动处理
            if f.default is not MISSING or f.default_factory is not MISSING:
                # 至少有一个 default 机制可用，跳过
                continue
            # 都是 MISSING —— 这是必填字段，需要提供零值
            if f.type in ("str", str):
                filtered[f.name] = ""
            elif f.type in ("int", int):
                filtered[f.name] = 0
            elif f.type in ("float", float):
                filtered[f.name] = 0.0
            elif "list" in str(f.type).lower():
                filtered[f.name] = []
            elif "dict" in str(f.type).lower():
                filtered[f.name] = {}
            else:
                filtered[f.name] = None  # type: ignore[assignment]
    return cls(**filtered)


def _infer_decision(findings: list[dict]) -> str:
    """从 findings 推断审稿决定。"""
    high_count = sum(1 for f in findings if f.get("priority") == "high")
    verified_high = sum(
        1 for f in findings
        if f.get("priority") == "high" and f.get("status") == "verified"
    )

    if not findings:
        return "incomplete"
    if verified_high >= 3:
        return "reject"
    if verified_high >= 1 or high_count >= 2:
        return "major_revision"
    if high_count == 1:
        return "minor_revision"
    return "accept"


def _categorize_finding(text: str) -> str | None:
    """
    将 finding 文本分类到领域模式类别。

    优先级：overclaim > methodology > statistics > logic
    （更具体的类别优先匹配）

    Returns:
        类别字符串，或 None（如果无法归类）
    """
    text_lower = text.lower()

    # Overclaim 优先（最具体的模式，优先于 methodology）
    overclaim_keywords = [
        "overclaim", "over-claim", "causal claim", "causation",
        "过度声明", "因果声明", "不能推断",
    ]
    if any(kw.lower() in text_lower for kw in overclaim_keywords):
        return "overclaim"

    methodology_keywords = [
        "identification", "endogeneity", "selection bias", "parallel trend",
        "robustness", "placebo", "DID", "difference-in-difference",
        "regression", "instrument", "PSM", "RDD",
        "内生性", "平行趋势", "安慰剂", "稳健性", "工具变量", "倾向得分",
    ]
    if any(kw.lower() in text_lower for kw in methodology_keywords):
        return "methodology"

    statistics_keywords = [
        "significant", "p-value", "standard error", "coefficient",
        "cluster", "heteroskedast", "sample size",
        "显著性", "标准误", "系数", "聚类", "样本量",
    ]
    if any(kw.lower() in text_lower for kw in statistics_keywords):
        return "statistics"

    logic_keywords = [
        "contradiction", "inconsisten", "logic", "non sequitur",
        "矛盾", "逻辑", "不一致", "推理",
    ]
    if any(kw.lower() in text_lower for kw in logic_keywords):
        return "logic"

    return None


def _generalize_finding(text: str) -> str | None:
    """
    将具体 finding 泛化为可复用的模式描述。

    策略: 截取前 120 字符作为模式描述。
    未来可以做更智能的泛化（如去掉具体数字和论文名），但 V1 先用简单方案。
    """
    if len(text) < 20:
        return None
    # 截断并清理
    desc = text[:120].strip()
    # 去掉末尾不完整的词
    if len(text) > 120 and not desc.endswith((".", "。", "!", "！")):
        last_space = desc.rfind(" ")
        last_comma = desc.rfind(",")
        last_period = max(desc.rfind("."), desc.rfind("。"))
        cut_point = max(last_space, last_comma, last_period)
        if cut_point > 60:
            desc = desc[:cut_point]
    return desc
