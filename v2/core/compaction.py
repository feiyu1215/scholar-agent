"""
core/v2/compaction.py — Smart Compaction Engine

设计依据:
    - TencentDB Agent Memory: L0(Raw)→L1(Summary)→L2(Digest) 分层压缩
    - Claude Code: Smart Compaction with workspace restoration
    - Anthropic: "模型对世界的理解压在 1-2 万 token 里"

核心思想:
    - 压缩不是"丢弃"，而是"变换表示"
    - 压缩后必须恢复工作台（Agent 不能"忘记"自己在做什么）
    - 不同信息有不同的压缩策略（findings 不压缩，对话历史压缩）

与 v1 compress_messages 的关系:
    - v1 的压缩逻辑仍在 harness.py 中保留，作为底层 message truncation
    - 本模块在其之上增加"工作台恢复"层，确保压缩后 Agent 无缝继续

与 offload.py 的关系:
    - OffloadStore 管理"卸载的完整内容可按 ref_id 回溯"
    - Compaction 管理"对话历史何时压缩 + 压缩后注入什么恢复信息"
    - 两者互补: offload 是证据底座，compaction 是记忆变换

M2 升级 (Phase 14):
    - 恢复信息分层优先级: Findings > Session Memory > Hypotheses > Paper Structure > Progress
    - token 预算裁剪: 总预算 6000 tokens，从低优先级层开始截断
    - 措辞为认知辅助模式（参考，非指令）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Protocol

from core.godel_config import TOTAL_CONTEXT_WINDOW, compute_capacity_pct

if TYPE_CHECKING:
    from core.state import WorkspaceState

logger = logging.getLogger(__name__)


# ============================================================
# Token Estimation
# ============================================================

def _estimate_tokens(text: str) -> int:
    """
    粗略估算 token 数量。

    中文约 1.5 字符/token，英文约 4 字符/token。
    混合文本取折中：约 2.5 字符/token。
    """
    if not text:
        return 0
    return max(1, len(text) // 3)


# ============================================================
# Config
# ============================================================

@dataclass
class CompactionConfig:
    """压缩引擎配置。"""

    # 压缩触发阈值
    trigger_token_ratio: float = 0.5     # context window 占比超过此值时开始考虑压缩
    aggressive_ratio: float = 0.7        # 超过此值时使用更激进的压缩

    # 保留策略
    recent_turns_to_keep: int = 6        # 保留最近 N 组 assistant 交互不压缩
    aggressive_keep: int = 3             # 激进模式下保留数
    min_messages_for_compaction: int = 14 # 消息太少时不压缩 (7 组交互)

    # M2: 恢复信息 token 预算
    restoration_budget_tokens: int = 6000  # 恢复文本总 token 预算

    # B4: capacity 信号所需的总 context window 大小（单一数据源：godel_config.py）
    total_context_window: int = TOTAL_CONTEXT_WINDOW

    # B7: frozen_prefix 最大 token 预算（防止无限累积）
    max_frozen_prefix_tokens: int = 12000  # 约占 context window 的 ~10%


# ============================================================
# Restoration Layer
# ============================================================

@dataclass
class RestorationLayer:
    """恢复信息的一个层级。"""
    name: str           # 层标识（用于日志）
    priority: int       # 优先级，数值越大越重要（100=最高）
    content: str        # 格式化好的文本
    critical: bool = False  # True 表示永不裁剪（如 Findings）

    @property
    def token_estimate(self) -> int:
        return _estimate_tokens(self.content)


# ============================================================
# WorkspaceSnapshot
# ============================================================

@dataclass
class WorkspaceSnapshot:
    """
    工作台快照——压缩后 Agent 需要知道的一切。

    这是 Smart Compaction 的核心创新:
    不只是总结历史，还要恢复当前工作状态。
    Agent 压缩后应该能无缝继续工作，就像从未压缩过一样。

    M2: 新增分层优先级恢复——不同层按优先级竞争 token 预算。
    """

    # 审稿进度
    sections_read: list[str] = field(default_factory=list)
    total_sections: int = 0
    loop_turns: int = 0

    # 核心产出（不可丢失）
    findings_count: int = 0
    findings_summary: list[str] = field(default_factory=list)

    # 当前策略状态
    consecutive_read_turns: int = 0
    recent_tools: list[str] = field(default_factory=list)  # 最近 5 次工具调用

    # 对话摘要
    history_summary: str = ""  # 被压缩掉的历史的结构化摘要

    # Phase 13/M1: Session Memory — 认知笔记恢复文本
    session_memory_text: str = ""  # 由 SessionMemoryManager.format_for_restoration() 生成

    # Phase 14/M2: HD-WM 假说恢复文本
    hypothesis_text: str = ""  # 由 HypothesisModule.format_for_restoration() 生成

    # Phase 14/M2: 论文结构索引恢复文本
    paper_structure_text: str = ""  # 由 PaperStructureIndex.format_for_context() 生成

    # V3/Phase 0.5: Paper Cognition Graph 序列化快照
    pcg_snapshot: str = ""  # 由 PCG.serialize_for_compaction() 生成

    # V3/Phase 0.5: CognitiveState 快照（HD-WM 认知状态）
    cognitive_state_snapshot: str = ""  # 由 CognitiveState.format_for_context() 生成

    # V3/Phase 0.5: EvidenceChain 引用摘要
    evidence_chain_refs: str = ""  # 由 EvidenceChainTracker 生成的链引用摘要

    # 模型切换优化/方案一: Agent 行为状态（压缩后防重复操作）
    findings_submitted_ids: list[int] = field(default_factory=list)

    # B7: Frozen Snapshot 前缀缓存
    frozen_prefix: str = ""  # 上次压缩的恢复文本（本次不再覆盖，只追加增量）
    compaction_seq: int = 0   # 第几次压缩（从 0 开始，每次 compact 后 +1）

    def _build_layers(self) -> list[RestorationLayer]:
        """
        构建分层恢复信息。

        优先级设计（UPGRADE_PLAN_DRAFT §M2 + V3/Phase 0.5 + 模型切换优化/方案一）:
            Layer 0  (priority=100, critical): Findings — 永不裁剪
            Layer 0b (priority=95,  critical): Agent 行为状态 — 已读/已提交记录，防重复操作
            Layer 1  (priority=90):  Session Memory 认知笔记 — 恢复累积判断
            Layer 2  (priority=80):  HD-WM 假说状态 — 恢复验证追踪
            Layer 3  (priority=60):  论文结构索引 — 恢复心智模型
            Layer 4  (priority=40):  进度与历史摘要 — 参考信息
            Layer 5  (priority=55, critical): PCG 认知图快照 — 恢复图结构理解（C9 约束）
            Layer 5b (priority=50):  CognitiveState 快照 — 恢复 HD-WM 认知状态
            Layer 6  (priority=35):  EvidenceChain 引用 — 恢复推理链上下文
        """
        layers = []

        # Layer 0: Findings（永不裁剪）
        if self.findings_summary:
            content = (
                f"## 已发现问题 ({self.findings_count} 个)\n"
                + "\n".join(f"- {f}" for f in self.findings_summary)
            )
            layers.append(RestorationLayer(
                name="findings", priority=100, content=content, critical=True
            ))

        # 方案一: Agent 行为状态层（critical, priority=95）
        # ROI: ~200 tokens 投入 → 节省 80-140K tokens 的重复操作
        behavioral_parts = []
        if self.sections_read:
            behavioral_parts.append(f"[已读sections] {' | '.join(self.sections_read)}")
        if self.findings_submitted_ids:
            ids_str = ", ".join(f"#{i+1}" for i in self.findings_submitted_ids)
            behavioral_parts.append(f"[已提交findings] {ids_str}")

        if behavioral_parts:
            layers.append(RestorationLayer(
                name="agent_behavioral_state",
                priority=95,
                content="=== Agent 行为状态（请勿重复已完成的操作） ===\n" + "\n".join(behavioral_parts),
                critical=True,
            ))

        # Layer 1: Session Memory 认知笔记
        if self.session_memory_text:
            layers.append(RestorationLayer(
                name="session_memory", priority=90, content=self.session_memory_text
            ))

        # Layer 2: HD-WM 假说状态
        if self.hypothesis_text:
            layers.append(RestorationLayer(
                name="hypotheses", priority=80, content=self.hypothesis_text
            ))

        # Layer 3: 论文结构索引（精简版）
        if self.paper_structure_text:
            layers.append(RestorationLayer(
                name="paper_structure", priority=60, content=self.paper_structure_text
            ))

        # Layer 4: 审稿进度 + 历史摘要
        progress_parts = []
        read_info = f"{len(self.sections_read)}/{self.total_sections} sections"
        progress_parts.append(
            f"## 审稿进度\n"
            f"已读 sections: {read_info} | 轮次: {self.loop_turns}"
        )
        # 注：已读列表已提升至 critical 行为状态层，此处只保留计数
        if self.recent_tools:
            progress_parts.append(f"最近操作: {'→'.join(self.recent_tools)}")
        if self.history_summary:
            progress_parts.append(f"\n{self.history_summary}")

        progress_content = "\n".join(progress_parts)
        layers.append(RestorationLayer(
            name="progress", priority=40, content=progress_content
        ))

        # Layer 5: PCG 认知图快照（V3/Phase 0.5）
        # critical=True: Agent 不能丢失论文结构理解（Constitutional C9: PCG integrity）
        if self.pcg_snapshot:
            layers.append(RestorationLayer(
                name="pcg_snapshot", priority=55, content=self.pcg_snapshot,
                critical=True,
            ))

        # Layer 5b: CognitiveState 快照（V3/Phase 0.5）
        if self.cognitive_state_snapshot:
            layers.append(RestorationLayer(
                name="cognitive_state", priority=50, content=self.cognitive_state_snapshot
            ))

        # Layer 6: EvidenceChain 引用摘要（V3/Phase 0.5）
        if self.evidence_chain_refs:
            layers.append(RestorationLayer(
                name="evidence_chains", priority=35, content=self.evidence_chain_refs
            ))

        return layers

    def format_restoration(
        self, budget_tokens: int = 6000,
        max_frozen_tokens: int = 0,
    ) -> str:
        """
        格式化为可注入 prompt 的恢复文本。

        M2 升级: 分层优先级裁剪。
        - 按 priority 从高到低填充
        - critical 层永不裁剪
        - 超出预算时，从最低优先级层开始丢弃

        B7 升级: Frozen Snapshot 前缀缓存。
        - 如果 frozen_prefix 非空，本次只生成增量 delta
        - 最终注入 = frozen_prefix + separator + delta
        - 完整输出存入 frozen_prefix 供下次使用
        - frozen_prefix 超过 max_frozen_tokens 时截断旧增量段

        注意: 此方法有副作用（更新 frozen_prefix 和 compaction_seq）。
        同一 snapshot 的 format_restoration() 应只调用一次。
        如需重试请使用新的 snapshot。

        Args:
            budget_tokens: delta 部分的 token 预算
            max_frozen_tokens: frozen_prefix 的最大 token 数（0=使用默认 12000）
        """
        # 重入守卫：检查是否已被调用过（基于 compaction_seq 在本次是否已递增）
        expected_seq = self.compaction_seq
        delta_text = self._build_restoration_text(budget_tokens)

        if self.frozen_prefix:
            # B7: 增量模式 — frozen_prefix 已含历史，只追加 delta
            separator = "\n\n---\n[增量更新 #{}]\n".format(expected_seq)
            full_output = self.frozen_prefix + separator + delta_text
        else:
            # 首次压缩 — 完整生成
            full_output = delta_text

        # B7 审计修复: 防止 frozen_prefix 无限增长
        cap = max_frozen_tokens if max_frozen_tokens > 0 else 12000
        full_output = self._cap_frozen_prefix(full_output, cap)

        # 将完整输出存为下次的 frozen_prefix
        self.frozen_prefix = full_output
        self.compaction_seq += 1

        return full_output

    @staticmethod
    def _cap_frozen_prefix(text: str, max_tokens: int) -> str:
        """如果 text 估算超过 max_tokens，从尾部保留最新内容。

        截断策略: 按增量分隔符 '---' 分段，从最旧的增量段开始丢弃，
        直到总 token 数 <= max_tokens。保留第一段（初始 restoration）
        的摘要标记，确保 Agent 知道有历史被截断。
        """
        est_tokens = _estimate_tokens(text)
        if est_tokens <= max_tokens:
            return text

        # 按增量分隔符分段
        segments = text.split("\n\n---\n")
        if len(segments) <= 2:
            # 只有 0-1 个增量段，无法进一步截断，直接返回
            return text

        # 保留第一段（原始 restoration）和尽可能多的新增量段（从后往前）
        first_segment = segments[0]
        remaining_segments = segments[1:]

        # 从最新的段开始往前贪心保留
        kept_segments: list[str] = []
        truncation_marker = "[...历史增量已截断...]"
        overhead = _estimate_tokens(first_segment) + _estimate_tokens(truncation_marker)
        budget = max_tokens - overhead

        for seg in reversed(remaining_segments):
            seg_tokens = _estimate_tokens(seg)
            if budget >= seg_tokens:
                kept_segments.insert(0, seg)
                budget -= seg_tokens
            else:
                break

        if len(kept_segments) < len(remaining_segments):
            # 有段被截断
            result = first_segment + "\n\n" + truncation_marker
            for seg in kept_segments:
                result += "\n\n---\n" + seg
            return result
        else:
            # 全部保留（不应到达这里，因为已判断超限）
            return text

    def _build_restoration_text(self, budget_tokens: int = 6000) -> str:
        """构建本次恢复文本（分层优先级裁剪）。

        B7 提取: 从 format_restoration 中提取为独立方法，
        以便 format_restoration 可以在此基础上叠加 frozen_prefix 逻辑。
        """
        layers = self._build_layers()

        # 按 priority 降序排列
        layers.sort(key=lambda l: l.priority, reverse=True)

        # 分离 critical 和 non-critical
        critical_layers = [l for l in layers if l.critical]
        optional_layers = [l for l in layers if not l.critical]

        # critical 层无条件保留，计算剩余预算
        critical_tokens = sum(l.token_estimate for l in critical_layers)
        remaining_budget = budget_tokens - critical_tokens

        # optional 层按优先级从高到低贪心填充
        included_optional = []
        for layer in optional_layers:
            cost = layer.token_estimate
            if cost <= remaining_budget:
                included_optional.append(layer)
                remaining_budget -= cost
            else:
                # 预算不足：跳过该层及后续所有更低优先级层
                logger.info(
                    "Restoration budget exceeded at layer '%s' "
                    "(need %d, remaining %d). Skipping.",
                    layer.name, cost, remaining_budget
                )
                break

        # 按 priority 降序组装最终文本
        all_included = critical_layers + included_optional
        all_included.sort(key=lambda l: l.priority, reverse=True)

        parts = [layer.content for layer in all_included if layer.content]
        return "\n\n".join(parts)


# ============================================================
# Pre-Compact Hook Protocol (B4)
# ============================================================

class PreCompactHook(Protocol):
    """Hook 协议: 压缩前各模块用来"保存现场"的回调。

    使用场景:
        - SessionMemory 在压缩前 flush pending notes
        - HypothesisModule 在压缩前保存未验证假说
        - 任何需要在压缩前执行清理/持久化的模块

    异常处理: hook 抛出异常不应阻断压缩流程。
    """
    def __call__(self, snapshot: WorkspaceSnapshot) -> None: ...


# ============================================================
# CompactionEngine
# ============================================================

class CompactionEngine:
    """
    Smart Compaction Engine.

    职责:
    1. 判断是否需要压缩 (should_compact)
    2. 构建工作台快照 (build_snapshot)
    3. 执行压缩并注入恢复信息 (compact)
    4. 在压缩前触发 pre_compact_hooks（B4）
    5. 提供 get_capacity_pct() 实时 capacity 信号（B4）

    用法:
        engine = CompactionEngine()
        if engine.should_compact(state, messages):
            snapshot = engine.build_snapshot(state, messages)
            messages = engine.compact(messages, snapshot, state)
    """

    def __init__(self, config: CompactionConfig | None = None) -> None:
        self.config = config or CompactionConfig()
        self._pre_compact_hooks: list[Callable[[WorkspaceSnapshot], None]] = []

    def register_pre_compact_hook(self, hook: Callable[[WorkspaceSnapshot], None]) -> None:
        """注册压缩前回调。

        hook 将在 build_snapshot 之后、裁剪历史之前被调用。
        hook 异常不阻断压缩流程（try-except + warning log）。
        """
        self._pre_compact_hooks.append(hook)

    def _fire_pre_compact_hooks(self, snapshot: WorkspaceSnapshot) -> None:
        """触发所有 pre-compact hooks。"""
        for hook in self._pre_compact_hooks:
            try:
                hook(snapshot)
            except Exception as e:
                logger.warning(
                    "Pre-compact hook %s failed: %s",
                    getattr(hook, "__name__", repr(hook)), e,
                )

    def get_capacity_pct(self, current_context_tokens: int) -> float:
        """返回 context window 已用百分比 (0.0~1.0)。

        用于在 Harness 每轮 status block 中注入 capacity 信息（格式：[Context: 63%]），
        让 Agent 具备主动感知 capacity 的能力。

        委托给 godel_config.compute_capacity_pct() 作为单一数据源，
        确保与 TokenBudgetManager.compute_used_pct() 结果一致。

        Args:
            current_context_tokens: 当前已使用的 context token 数

        Returns:
            已用百分比，cap 在 0.0~1.0 范围内
        """
        return compute_capacity_pct(
            current_context_tokens, self.config.total_context_window
        )

    def should_compact(self, state: WorkspaceState, messages: list[dict]) -> bool:
        """
        判断是否应该执行压缩。

        双条件触发:
        1. context window 占比超过阈值
        2. 消息数量足够（太少时压缩无意义）
        """
        if len(messages) < self.config.min_messages_for_compaction:
            return False

        if state.context_window <= 0:
            return False

        ratio = state.last_prompt_tokens / state.context_window
        return ratio >= self.config.trigger_token_ratio

    def get_keep_recent(self, state: WorkspaceState) -> int:
        """根据 context 压力决定保留多少轮。"""
        if state.context_window <= 0:
            return self.config.recent_turns_to_keep

        ratio = state.last_prompt_tokens / state.context_window
        if ratio >= self.config.aggressive_ratio:
            return self.config.aggressive_keep
        return self.config.recent_turns_to_keep

    def build_snapshot(
        self, state: WorkspaceState, messages: list[dict],
        session_memory_text: str = "",
        hypothesis_text: str = "",
        paper_structure_text: str = "",
        pcg_snapshot: str = "",
        cognitive_state_snapshot: str = "",
        evidence_chain_refs: str = "",
    ) -> WorkspaceSnapshot:
        """
        从当前状态构建工作台快照。

        快照包含 Agent 恢复工作所需的所有关键信息，
        这些信息在压缩后会作为合成消息注入对话历史。

        Args:
            state: 当前工作状态
            messages: 对话历史
            session_memory_text: SessionMemoryManager 生成的认知笔记恢复文本
            hypothesis_text: HypothesisModule.format_for_restoration() 生成的假说状态
            paper_structure_text: PaperStructureIndex.format_for_context() 生成的结构索引
            pcg_snapshot: PCG.serialize_for_compaction() 生成的认知图快照
            cognitive_state_snapshot: CognitiveState.format_for_context() 生成的认知状态快照
            evidence_chain_refs: EvidenceChainTracker 生成的推理链引用摘要
        """
        # Findings 摘要：只保留精炼版（每条 <= 100 字符）
        findings_summary = []
        for f in state.findings:
            priority = f.get("priority", "medium")
            text = f.get("finding", "")[:100]
            findings_summary.append(f"[{priority}] {text}")

        # 最近 5 次工具调用
        recent_tools = []
        if state.tool_call_history:
            recent_tools = [
                entry.get("name", "?")
                for entry in state.tool_call_history[-5:]
            ]

        # 历史摘要：从被压缩掉的消息中提取关键操作
        keep_recent = self.get_keep_recent(state)
        history_summary = self._summarize_compressed_history(
            messages, keep_recent
        )

        return WorkspaceSnapshot(
            sections_read=list(state.sections_read),
            total_sections=len(state.paper_sections),
            loop_turns=state.loop_turns,
            findings_count=len(state.findings),
            findings_summary=findings_summary,
            consecutive_read_turns=state.consecutive_read_turns,
            recent_tools=recent_tools,
            history_summary=history_summary,
            session_memory_text=session_memory_text,
            hypothesis_text=hypothesis_text,
            paper_structure_text=paper_structure_text,
            pcg_snapshot=pcg_snapshot,
            cognitive_state_snapshot=cognitive_state_snapshot,
            evidence_chain_refs=evidence_chain_refs,
            # 方案一: 记录已提交 findings 的索引，压缩后防重复提交
            findings_submitted_ids=list(range(len(state.findings))),
        )

    def compact(
        self,
        messages: list[dict],
        snapshot: WorkspaceSnapshot,
        state: WorkspaceState,
    ) -> list[dict]:
        """
        执行压缩：截断旧消息 + 注入工作台恢复。

        压缩后的 messages 结构:
        [system] + [restoration_user] + [restoration_assistant] + [recent N turns]

        B4: 在裁剪历史前触发 pre_compact_hooks，让各模块"保存现场"。

        Args:
            messages: 原始 messages 列表（不会被 mutate）
            snapshot: 工作台快照
            state: 当前状态（用于判断激进程度）

        Returns:
            压缩后的 messages 列表
        """
        # B4: 触发 pre-compact hooks（snapshot 已构建，裁剪前最后机会保存现场）
        self._fire_pre_compact_hooks(snapshot)

        keep_recent = self.get_keep_recent(state)

        # 找到保留边界
        assistant_indices = [
            i for i, m in enumerate(messages) if m.get("role") == "assistant"
        ]

        if len(assistant_indices) <= keep_recent:
            # 不够压缩
            return messages

        # 保留边界：最近 keep_recent 组 assistant 之前
        cut_idx = assistant_indices[-keep_recent]

        # 提取 system message 和 recent messages
        system_msgs = [m for m in messages[:cut_idx] if m.get("role") == "system"]
        recent_msgs = messages[cut_idx:]

        # 构建恢复信息（M2: 使用 token 预算裁剪）
        restoration_text = snapshot.format_restoration(
            budget_tokens=self.config.restoration_budget_tokens
        )
        restoration_pair = [
            {
                "role": "user",
                "content": (
                    "[上下文恢复] 以下是你之前审稿工作的状态摘要，"
                    "对话历史已被压缩以节省空间：\n\n" + restoration_text
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "我已理解之前的审稿进展。"
                    f"当前已发现 {snapshot.findings_count} 个问题，"
                    f"已读 {len(snapshot.sections_read)}/{snapshot.total_sections} sections。"
                    "继续审稿。"
                ),
            },
        ]

        # 组装最终消息列表
        compacted = system_msgs + restoration_pair + recent_msgs

        logger.info(
            "Compaction executed: %d→%d messages, "
            "kept %d recent turns, snapshot has %d findings",
            len(messages),
            len(compacted),
            keep_recent,
            snapshot.findings_count,
        )

        return compacted

    def _summarize_compressed_history(
        self, messages: list[dict], keep_recent: int
    ) -> str:
        """
        从将被压缩的历史消息中提取关键信息摘要。

        策略：扫描旧消息中的工具调用，构建"做了什么"的时间线。
        不使用 LLM（保持同步、零外部依赖），用规则提取。
        """
        assistant_indices = [
            i for i, m in enumerate(messages) if m.get("role") == "assistant"
        ]
        if len(assistant_indices) <= keep_recent:
            return ""

        cut_idx = assistant_indices[-keep_recent]
        old_messages = messages[:cut_idx]

        # 提取工具调用序列
        tool_calls = []
        for msg in old_messages:
            if msg.get("role") == "assistant" and "tool_calls" in msg:
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    name = func.get("name", "?")
                    tool_calls.append(name)

        if not tool_calls:
            return ""

        # 统计工具使用频率
        from collections import Counter
        counts = Counter(tool_calls)
        total = len(tool_calls)

        # 构建摘要
        parts = [f"历史中共执行了 {total} 次工具调用:"]
        for tool, count in counts.most_common(5):
            parts.append(f"  {tool}: {count}次")

        return "\n".join(parts)
