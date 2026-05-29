"""
Phase 14: Long-Conversation Integration Stress Test
====================================================

用真实 LLM 运行 3 轮对话，检测 Agent 是否在长对话中退化。

退化信号检测：
  A) Agent 重复读取已读过的 section (forget it already read)
  B) Agent 回复中不引用早期 findings (context loss)
  C) Agent 丢失用户原始意图

使用方式:
    cd scholar-agent-public
    python3 tests/run_stress_integration.py [--model MODEL] [--verbose]

需要 .env 中配置 LLM API key。
"""

import asyncio
import sys
import time
import json
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from core.agent import ScholarAgent


# ============================================================
# Config
# ============================================================

PAPER_PATH = str(Path(__file__).parent.parent / "examples" / "sample_paper.md")

# 多轮对话脚本：每轮用户消息设计为测试记忆保持
CONVERSATION_SCRIPT = [
    # Round 1: 自主审阅（Agent 自己产生 findings）
    None,  # start() — no user message, Agent reviews autonomously
    
    # Round 2: 追问早期发现 — 测试 Agent 是否记得 Round 1 的结论
    (
        "你在第一轮审阅中提到了一些方法论问题。请回顾一下你之前关于 identification strategy "
        "和 DID 设计的发现，告诉我哪些是最严重的。不要重新读论文，用你已有的发现回答。"
    ),
    
    # Round 3: 跨 section 综合判断 — 需要整合多个 findings
    (
        "基于你所有的发现，请给出最终的审稿建议：这篇论文应该 accept、revise 还是 reject？"
        "列出支持你决定的 top 3 理由，每个都要引用你之前的具体发现。"
    ),
]


# ============================================================
# Degradation Signal Detectors
# ============================================================

class DegradationTracker:
    """追踪多轮对话中的退化信号。"""
    
    def __init__(self, agent: ScholarAgent):
        self.agent = agent
        self.sections_read = []  # (turn, section)
        self.findings_per_round = []  # [round1_findings, round2_findings, ...]
        self.responses = []  # Agent 每轮回复
        self.tool_calls_log = []  # 所有 tool 调用记录
        
        # Hook into harness to track tool calls
        self._original_execute = agent.harness.execute_tool
        agent.harness.execute_tool = self._tracked_execute
    
    def _tracked_execute(self, name: str, args: dict) -> str:
        """拦截 tool 调用以记录。"""
        self.tool_calls_log.append({
            "turn": self.agent.harness.state.conversation_turns,
            "loop_turn": self.agent.harness.state.loop_turns,
            "tool": name,
            "args": args,
        })
        if name == "read_section":
            section = args.get("section", "?")
            self.sections_read.append((self.agent.harness.state.conversation_turns, section))
        return self._original_execute(name, args)
    
    def record_round(self, response: str):
        """记录一轮结束后的状态。"""
        self.responses.append(response)
        self.findings_per_round.append(list(self.agent.get_findings()))
    
    def analyze(self) -> dict:
        """分析退化信号。"""
        signals = {
            "A_repeated_reads": self._check_repeated_reads(),
            "B_context_loss": self._check_context_loss(),
            "C_intent_drift": self._check_intent_drift(),
            "summary": {},
        }
        
        # 总体评估
        total_signals = sum(1 for v in signals.values() if isinstance(v, dict) and v.get("detected"))
        signals["summary"] = {
            "total_signals": total_signals,
            "verdict": "DEGRADED" if total_signals >= 2 else "MILD" if total_signals == 1 else "STABLE",
            "rounds": len(self.responses),
            "total_tool_calls": len(self.tool_calls_log),
            "total_findings": len(self.agent.get_findings()),
            "sections_read_total": len(self.sections_read),
        }
        
        return signals
    
    def _check_repeated_reads(self) -> dict:
        """Signal A: 同一 section 被读取多次（在不同 conversation turn 中）。"""
        from collections import Counter
        section_counts = Counter(s for _, s in self.sections_read)
        repeated = {s: c for s, c in section_counts.items() if c > 1}
        
        return {
            "detected": len(repeated) > 0,
            "repeated_sections": repeated,
            "detail": f"{len(repeated)} section(s) read more than once" if repeated else "None",
        }
    
    def _check_context_loss(self) -> dict:
        """
        Signal B: Round 2/3 的回复未引用 Round 1 的 findings。
        检测方法：看 Round 2+ 回复中是否提到了 Round 1 findings 的关键词。
        """
        if len(self.responses) < 2:
            return {"detected": False, "detail": "Not enough rounds"}
        
        # Round 1 的 findings 关键词
        round1_findings = self.findings_per_round[0] if self.findings_per_round else []
        if not round1_findings:
            return {"detected": False, "detail": "No findings in Round 1"}
        
        # 提取 Round 1 findings 的核心关键词
        r1_keywords = set()
        for f in round1_findings:
            # 取 finding 中的关键名词/动词（粗略匹配）
            text = f.get("finding", "")
            for word in text.split():
                if len(word) > 4:  # 过滤短词
                    r1_keywords.add(word.lower())
        
        # 检查 Round 2+ 回复中是否引用了 Round 1 的关键词
        later_responses = " ".join(self.responses[1:]).lower()
        matches = sum(1 for kw in r1_keywords if kw in later_responses)
        match_ratio = matches / max(len(r1_keywords), 1)
        
        return {
            "detected": match_ratio < 0.1,  # 如果 <10% 关键词被引用，认为 context loss
            "match_ratio": f"{match_ratio:.2%}",
            "r1_keywords_count": len(r1_keywords),
            "matches_in_later_rounds": matches,
            "detail": f"Round 1 keywords recall: {match_ratio:.1%} ({matches}/{len(r1_keywords)})",
        }
    
    def _check_intent_drift(self) -> dict:
        """
        Signal C: Round 3 的回复是否回答了用户的问题。
        检测方法：Round 3 用户问的是 accept/revise/reject 建议，回复中应该包含这些词。
        """
        if len(self.responses) < 3:
            return {"detected": False, "detail": "Not enough rounds"}
        
        r3_response = self.responses[2].lower()
        decision_keywords = ["accept", "reject", "revise", "revision", "接受", "拒绝", "修改"]
        has_decision = any(kw in r3_response for kw in decision_keywords)
        
        return {
            "detected": not has_decision,
            "has_decision_keyword": has_decision,
            "detail": "Response contains editorial decision" if has_decision else "NO editorial decision found — possible intent drift",
        }


# ============================================================
# Main Test Runner
# ============================================================

async def run_stress_test(model: str = None, verbose: bool = True):
    """运行 3 轮对话压力测试。"""
    
    print("=" * 60)
    print("  Phase 14: LLM Integration Stress Test")
    print("=" * 60)
    print(f"  Paper: {PAPER_PATH}")
    print(f"  Model: {model or os.environ.get('LLM_MODEL', 'default')}")
    print(f"  Rounds: {len(CONVERSATION_SCRIPT)}")
    print("=" * 60)
    
    # 创建 Agent
    agent = ScholarAgent(
        paper_path=PAPER_PATH,
        model=model,
        verbose=verbose,
        max_loop_turns=15,  # 给足空间
        token_budget=150_000,
    )
    tracker = DegradationTracker(agent)
    
    # Round 1: Agent 自主审阅
    print(f"\n{'─' * 50}")
    print("  [Round 1] Agent 自主审阅...")
    print(f"{'─' * 50}")
    t0 = time.time()
    
    r1_response = await agent.start()
    t1 = time.time()
    tracker.record_round(r1_response)
    
    print(f"\n  Response ({t1-t0:.1f}s): {r1_response[:200]}...")
    print(f"  Findings after R1: {len(agent.get_findings())}")
    
    # Round 2 & 3: 追问
    for round_idx in range(1, len(CONVERSATION_SCRIPT)):
        user_msg = CONVERSATION_SCRIPT[round_idx]
        print(f"\n{'─' * 50}")
        print(f"  [Round {round_idx + 1}] User: {user_msg[:80]}...")
        print(f"{'─' * 50}")
        
        t_start = time.time()
        response = await agent.chat(user_msg)
        t_end = time.time()
        tracker.record_round(response)
        
        print(f"\n  Response ({t_end-t_start:.1f}s): {response[:200]}...")
        print(f"  Findings after R{round_idx + 1}: {len(agent.get_findings())}")
    
    # 分析退化信号
    print(f"\n{'=' * 60}")
    print("  退化信号分析")
    print(f"{'=' * 60}")
    
    analysis = tracker.analyze()
    
    # Signal A
    sig_a = analysis["A_repeated_reads"]
    icon_a = "🔴" if sig_a["detected"] else "🟢"
    print(f"\n  {icon_a} Signal A (重复读取): {sig_a['detail']}")
    if sig_a.get("repeated_sections"):
        for section, count in sig_a["repeated_sections"].items():
            print(f"      '{section}' 读了 {count} 次")
    
    # Signal B
    sig_b = analysis["B_context_loss"]
    icon_b = "🔴" if sig_b["detected"] else "🟢"
    print(f"  {icon_b} Signal B (上下文丢失): {sig_b['detail']}")
    
    # Signal C
    sig_c = analysis["C_intent_drift"]
    icon_c = "🔴" if sig_c["detected"] else "🟢"
    print(f"  {icon_c} Signal C (意图偏移): {sig_c['detail']}")
    
    # 总结
    summary = analysis["summary"]
    print(f"\n  {'─' * 40}")
    print(f"  总评: {summary['verdict']}")
    print(f"  总 tool 调用: {summary['total_tool_calls']}")
    print(f"  总 findings: {summary['total_findings']}")
    print(f"  Sections 读取: {summary['sections_read_total']}")
    
    # Agent 统计
    stats = agent.get_stats()
    print(f"\n  Agent 统计:")
    print(f"    Model: {stats['model']}")
    print(f"    Total tokens: {stats['total_tokens']}")
    print(f"    Conversation turns: {stats['conversation_turns']}")
    
    # 最终判定
    print(f"\n{'=' * 60}")
    if summary["verdict"] == "STABLE":
        print("  ✅ STABLE — Agent 在 3 轮对话中记忆保持良好")
        print("  结论: 当前 Token Pipeline 设计足以应对 3 轮多 turn 对话")
    elif summary["verdict"] == "MILD":
        print("  ⚠️  MILD DEGRADATION — 检测到 1 个退化信号")
        print("  建议: 观察是否为偶发行为，可考虑增强 format_context 的信息密度")
    else:
        print("  🔴 DEGRADED — 检测到 2+ 个退化信号")
        print("  建议: 需要引入结构化外部记忆或调整压缩策略")
    print(f"{'=' * 60}")
    
    return analysis


# ============================================================
# CLI Entry
# ============================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Phase 14 Integration Stress Test")
    parser.add_argument("--model", default=None, help="LLM model name")
    parser.add_argument("--verbose", action="store_true", help="Show loop details")
    parser.add_argument("--quiet", action="store_true", help="Minimal output")
    args = parser.parse_args()
    
    if not Path(PAPER_PATH).exists():
        print(f"ERROR: Paper not found: {PAPER_PATH}")
        sys.exit(1)
    
    analysis = asyncio.run(run_stress_test(
        model=args.model,
        verbose=args.verbose and not args.quiet,
    ))
    
    # Exit code based on verdict
    verdict = analysis["summary"]["verdict"]
    sys.exit(0 if verdict == "STABLE" else 1 if verdict == "MILD" else 2)
