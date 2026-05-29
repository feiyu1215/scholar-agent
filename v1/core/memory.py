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
from dataclasses import dataclass, field, asdict
from typing import Optional


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

    # Meta
    version: str = "1.1"  # Bumped for Phase 54 (backward compatible: missing field = empty list)
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
        """将当前记忆状态持久化到磁盘。"""
        self.state.last_updated = datetime.now(timezone.utc).isoformat()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        raw = self._serialize(self.state)
        self.memory_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

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
    def _is_similar(existing: str, new: str) -> bool:
        """
        简单的相似度判断（无外部依赖）。

        策略: 如果两个描述共享超过 60% 的词（忽略大小写），认为相似。
        """
        words_a = set(existing.lower().split())
        words_b = set(new.lower().split())
        if not words_a or not words_b:
            return False
        intersection = words_a & words_b
        smaller = min(len(words_a), len(words_b))
        return len(intersection) / smaller > 0.5 if smaller > 0 else False

    @staticmethod
    def _serialize(state: MemoryState) -> dict:
        """将 MemoryState 序列化为 JSON-safe dict。"""
        return {
            "version": state.version,
            "last_updated": state.last_updated,
            "sessions": [asdict(s) for s in state.sessions],
            "patterns": [asdict(p) for p in state.patterns],
            "procedures": [asdict(p) for p in state.procedures],
        }

    @staticmethod
    def _deserialize(raw: dict) -> MemoryState:
        """从 JSON dict 反序列化为 MemoryState。向后兼容 v1.0 格式（无 procedures 字段）。"""
        state = MemoryState(
            version=raw.get("version", "1.0"),
            last_updated=raw.get("last_updated", ""),
        )
        for s in raw.get("sessions", []):
            state.sessions.append(SessionRecord(**s))
        for p in raw.get("patterns", []):
            state.patterns.append(DomainPattern(**p))
        for proc in raw.get("procedures", []):
            state.procedures.append(ProceduralPattern(**proc))
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
            patterns.append((
                "tool_sequence",
                f"高产序列: {seq_str} (本次出现 {count} 次后产出 findings)",
                f"当需要产出 findings 时",
                min(0.6 + count * 0.1, 0.95),
            ))

    # --- 2. 低效模式检测 ---
    # 连续重复同一工具超过 4 次且无 update_findings
    anti_patterns = _find_anti_patterns(tool_call_history)
    for tool_name, repeat_count in anti_patterns:
        patterns.append((
            "anti_pattern",
            f"低效重复: 连续 {repeat_count} 次 {tool_name} 未产出 findings",
            f"当连续调用 {tool_name} 超过 3 次时应切换策略",
            0.2,  # 低效模式的 effectiveness 低
        ))

    # --- 3. 策略有效性 ---
    if strategy_transitions:
        for from_s, to_s in strategy_transitions:
            # 策略切换后效率是否提升（简化判断：如果整体效率高，认为切换有效）
            if efficiency > 0.3:  # 每 3 轮产出 1 个 finding 以上
                patterns.append((
                    "strategy_effectiveness",
                    f"策略切换 {from_s}→{to_s} 在高产出会话中出现",
                    f"当 {from_s} 策略进展缓慢时切换到 {to_s}",
                    min(efficiency * 2, 0.9),
                ))

    # --- 4. 整体效率模式 ---
    read_count = tool_call_history.count("read_section")
    search_count = tool_call_history.count("search_literature")
    update_count = tool_call_history.count("update_findings")
    total_calls = len(tool_call_history)

    if total_calls >= 5 and efficiency > 0.4:
        read_ratio = read_count / total_calls
        if read_ratio > 0.3 and search_count > 0:
            patterns.append((
                "strategy_effectiveness",
                f"高效会话模式: read占比{int(read_ratio*100)}%, "
                f"search {search_count}次, 效率{efficiency:.2f} findings/turn",
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
