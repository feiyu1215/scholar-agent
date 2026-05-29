"""
core/metacognition.py — 元认知自我模型 (Phase 32)

设计灵感:
    - all-agentic-architectures 项目的 Metacognitive Agent 架构:
      显式 self-model (known_domains, confidence, strategy_choice)
    - TencentDB Agent Memory 的 "上层保结构、下层保证据" 原则
    - Anthropic Barry Zhang: "把自己放进 Agent 的上下文窗口"

设计原则:
    - CognitiveState 是 Agent 的"自我认知镜像"——Harness 维护，Agent 可读也可写
    - 它解决的核心问题: context 压缩后 Agent 丢失"我在做什么/我的假说是什么"
    - 它不控制 Agent (仍然遵守 COGNITIVE_ANCHOR §4.3 约束-而非-控制)
    - 它只是一面镜子: 帮 Agent 记住自己的认知状态，即使对话历史被压缩

结构:
    CognitiveState 包含:
    1. current_strategy — 当前审阅策略 (深度追查/广度扫描/修改模式/收尾整理)
    2. hypotheses — Agent 当前持有的假说列表 (带信心分数)
    3. open_questions — 明确的待解答问题
    4. confidence_assessment — 整体信心自评
    5. strategy_rationale — 选择当前策略的原因

交互方式:
    - Agent 通过 reflect_and_plan 工具的增强版更新 CognitiveState
    - Harness 在每轮 format_context 中注入 CognitiveState 的精简表示
    - compress_messages 不会丢弃 CognitiveState (因为它在 system prompt 中动态注入)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ============================================================
# Data Models
# ============================================================

@dataclass
class Hypothesis:
    """Agent 持有的一个假说。"""
    claim: str              # 假说内容 (< 120 chars)
    confidence: float       # 0.0 ~ 1.0, Agent 对该假说的信心
    evidence_for: list[str] = field(default_factory=list)    # 支持证据 (finding refs)
    evidence_against: list[str] = field(default_factory=list) # 反面证据
    status: Literal["active", "confirmed", "refuted", "suspended"] = "active"


@dataclass
class CognitiveState:
    """
    Agent 的元认知自我模型。
    
    Harness 拥有它，Agent 通过 reflect_and_plan 更新它。
    每轮通过 format_context 注入到 system prompt 中。
    
    设计约束:
    - 总序列化长度 < 800 tokens (避免占据过多 context 预算)
    - 纯数据结构，不含任何控制逻辑
    - Harness 的 compress_messages 不影响它 (因为它在 system msg 中动态刷新)
    """
    
    # 当前策略
    current_strategy: Literal[
        "deep_investigation",   # 深度追查: 聚焦于少数关键问题，追根溯源
        "breadth_scan",         # 广度扫描: 快速浏览多个 section，建立整体印象
        "targeted_verification", # 定向验证: 已有假说，正在寻找证据验证/推翻
        "revision_mode",        # 修改模式: 开始做修改建议或实际修改
        "synthesis",            # 综合收尾: 整合发现，准备最终意见
        "undecided",            # 尚未确定策略
    ] = "undecided"
    strategy_rationale: str = ""  # 为什么选择这个策略 (< 100 chars)
    
    # 假说空间
    hypotheses: list[Hypothesis] = field(default_factory=list)
    
    # 开放问题 (Agent 明确想回答的问题)
    open_questions: list[str] = field(default_factory=list)
    
    # 整体自评
    overall_confidence: float = 0.0  # 0.0~1.0: Agent 对"我已充分理解这篇论文"的信心
    assessment_note: str = ""  # 一句话自评 (< 80 chars)
    
    # 最近更新时间 (loop_turn 编号)
    last_updated_turn: int = 0
    
    def format_for_context(self) -> str:
        """
        序列化为 ≤800 token 的字符串，注入到 system prompt。
        
        设计:
        - 使用紧凑的文本格式 (不用 JSON，因为 LLM 读 natural language 更高效)
        - 只展示 active 假说 (refuted/confirmed 不再占空间)
        - open_questions 最多展示 5 条
        """
        if self.current_strategy == "undecided" and not self.hypotheses and not self.open_questions:
            return ""  # 初始状态不注入，减少噪音
        
        parts = []
        
        # 策略
        strategy_labels = {
            "deep_investigation": "深度追查",
            "breadth_scan": "广度扫描",
            "targeted_verification": "定向验证",
            "revision_mode": "修改模式",
            "synthesis": "综合收尾",
            "undecided": "待定",
        }
        parts.append(f"═══ 你的认知状态 (Turn {self.last_updated_turn}) ═══")
        parts.append(f"策略: {strategy_labels.get(self.current_strategy, self.current_strategy)}"
                     + (f" — {self.strategy_rationale}" if self.strategy_rationale else ""))
        
        # 假说
        active_hypos = [h for h in self.hypotheses if h.status == "active"]
        if active_hypos:
            parts.append(f"假说 ({len(active_hypos)} 条活跃):")
            for h in active_hypos[:5]:  # 最多展示 5 条
                conf_bar = "▓" * int(h.confidence * 5) + "░" * (5 - int(h.confidence * 5))
                evidence_info = ""
                if h.evidence_for or h.evidence_against:
                    evidence_info = f" [+{len(h.evidence_for)}/-{len(h.evidence_against)}]"
                parts.append(f"  [{conf_bar}] {h.claim[:100]}{evidence_info}")
        
        # 已确认/推翻的假说数量 (不展示细节，只告诉 Agent 有进展)
        confirmed = [h for h in self.hypotheses if h.status == "confirmed"]
        refuted = [h for h in self.hypotheses if h.status == "refuted"]
        if confirmed or refuted:
            parts.append(f"  (已确认 {len(confirmed)} 条, 已推翻 {len(refuted)} 条)")
        
        # 开放问题
        if self.open_questions:
            parts.append(f"待答问题 ({len(self.open_questions)}):")
            for q in self.open_questions[:5]:
                parts.append(f"  ? {q[:100]}")
            if len(self.open_questions) > 5:
                parts.append(f"  ...还有 {len(self.open_questions) - 5} 个问题")
        
        # 整体信心
        if self.overall_confidence > 0:
            conf_pct = f"{self.overall_confidence * 100:.0f}%"
            parts.append(f"整体理解信心: {conf_pct}"
                         + (f" ({self.assessment_note})" if self.assessment_note else ""))
        
        return "\n".join(parts)
    
    def update_from_reflection(self, reflection_output: dict) -> None:
        """
        从 Agent 的 reflect_and_plan 输出中更新认知状态。
        
        Agent 调 reflect_and_plan 时可以传入 cognitive_update 参数:
        {
            "strategy": "deep_investigation",
            "strategy_rationale": "发现方法论有根本问题，需要深入追查",
            "hypotheses": [
                {"claim": "DID 的平行趋势假设可能不成立", "confidence": 0.7}
            ],
            "questions": ["作者是否做了 placebo test?"],
            "confidence": 0.4,
            "assessment": "还没读结果部分，信心较低"
        }
        """
        if "strategy" in reflection_output:
            valid_strategies = {
                "deep_investigation", "breadth_scan", "targeted_verification",
                "revision_mode", "synthesis", "undecided"
            }
            s = reflection_output["strategy"]
            if s in valid_strategies:
                self.current_strategy = s
        
        if "strategy_rationale" in reflection_output:
            self.strategy_rationale = str(reflection_output["strategy_rationale"])[:150]
        
        if "hypotheses" in reflection_output:
            for h_data in reflection_output["hypotheses"]:
                if isinstance(h_data, dict) and "claim" in h_data:
                    claim = str(h_data["claim"])[:200]
                    confidence = min(1.0, max(0.0, float(h_data.get("confidence", 0.5))))
                    # 检查是否已有同名假说 (简单文本匹配)
                    existing = next(
                        (h for h in self.hypotheses if h.claim == claim), None
                    )
                    if existing:
                        existing.confidence = confidence
                        if "status" in h_data:
                            existing.status = h_data["status"]
                    else:
                        self.hypotheses.append(Hypothesis(
                            claim=claim,
                            confidence=confidence,
                            status=h_data.get("status", "active"),
                        ))
        
        if "questions" in reflection_output:
            for q in reflection_output["questions"]:
                q_str = str(q)[:150]
                if q_str and q_str not in self.open_questions:
                    self.open_questions.append(q_str)
        
        if "resolved_questions" in reflection_output:
            # Agent 可以标记问题已解决
            for q in reflection_output["resolved_questions"]:
                if q in self.open_questions:
                    self.open_questions.remove(q)
        
        if "confidence" in reflection_output:
            self.overall_confidence = min(1.0, max(0.0, float(reflection_output["confidence"])))
        
        if "assessment" in reflection_output:
            self.assessment_note = str(reflection_output["assessment"])[:100]
    
    def auto_infer_strategy(self, state_snapshot: dict) -> None:
        """
        基于 WorkspaceState 的客观状态，自动推断初始策略。
        
        这是 Harness 级别的推断 (不调 LLM)，只在 Agent 尚未自主设置策略时生效。
        一旦 Agent 通过 reflect_and_plan 设置了策略，此方法不再覆盖。
        
        Args:
            state_snapshot: {
                "sections_read_count": int,
                "total_sections": int,
                "findings_count": int,
                "edits_count": int,
                "loop_turns": int,
            }
        """
        if self.current_strategy != "undecided":
            return  # Agent 已自主决策，不覆盖
        
        read_ratio = state_snapshot["sections_read_count"] / max(1, state_snapshot["total_sections"])
        findings = state_snapshot["findings_count"]
        edits = state_snapshot["edits_count"]
        turns = state_snapshot["loop_turns"]
        
        if turns <= 3:
            self.current_strategy = "breadth_scan"
            self.strategy_rationale = "初始阶段，先建立全局印象"
        elif read_ratio < 0.3 and findings < 3:
            self.current_strategy = "breadth_scan"
            self.strategy_rationale = f"已读 {read_ratio*100:.0f}% sections，继续扫描"
        elif findings >= 2 and any(h.status == "active" for h in self.hypotheses):
            self.current_strategy = "targeted_verification"
            self.strategy_rationale = "已有假说待验证"
        elif edits > 0:
            self.current_strategy = "revision_mode"
            self.strategy_rationale = "已开始修改"
        elif read_ratio > 0.6 and findings >= 3:
            self.current_strategy = "deep_investigation"
            self.strategy_rationale = "已有足够基础，深入核心问题"
