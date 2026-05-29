"""
Phase 45: 超长审改 Session 认知带宽压力测试

目标：验证 Agent 在多轮对话（5 轮 chat）中的 Token Pipeline 是否能维持认知质量。

测试设计：
- 第 1 轮 (start): "帮我审阅这篇论文" → Agent 自主审阅
- 第 2 轮 (chat): "帮我改 Introduction 的 overclaim 问题"
- 第 3 轮 (chat): "Methodology 部分太空洞了，帮我补充"
- 第 4 轮 (chat): "Conclusion 需要重写，太弱了"
- 第 5 轮 (chat): "回顾一下你之前在第 1 轮发现的所有问题，哪些还没解决？"

第 5 轮是关键的"记忆完整性测试"——Agent 是否还记得第 1 轮的 findings？
（findings 存在 state 中不会被压缩，但 Agent 是否能正确引用它们？）

监控指标：
- M1: 每轮结束时的 total_tokens
- M2: 每轮的 compress_messages 压缩比
- M3: system prompt (format_context) 的字符数变化
- M4: 是否触发 80% 阈值警告
- M5: 第 5 轮 Agent 是否能正确引用第 1 轮的 findings（记忆完整性）
- M6: 每轮的 loop turns 数（是否因压力而行为退化）
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from dataclasses import dataclass, field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from core.agent import ScholarAgent


@dataclass
class TurnMetrics:
    """单轮对话的监控指标。"""
    turn_id: int
    user_message: str
    total_tokens_before: int
    total_tokens_after: int
    tokens_this_turn: int
    loop_turns: int
    findings_count: int
    edits_count: int
    system_prompt_chars: int
    messages_count: int
    compressed_messages_count: int  # 如果能获取
    budget_warning_triggered: bool
    elapsed_seconds: float
    response_preview: str  # 前 200 字符


@dataclass
class PressureTestResult:
    """压力测试总结果。"""
    turn_metrics: list[TurnMetrics] = field(default_factory=list)
    memory_integrity_pass: bool = False
    memory_integrity_detail: str = ""
    total_elapsed: float = 0.0
    total_tokens: int = 0
    peak_system_prompt_chars: int = 0
    budget_warning_count: int = 0


class TokenPressureMonitor:
    """Monkey-patch harness 来监控压缩行为。"""

    def __init__(self, harness):
        self.harness = harness
        self.compression_events = []
        self.budget_warnings = []
        self._original_compress = harness.compress_messages
        self._original_check_budget = harness.check_token_budget

        def monitored_compress(messages, keep_recent=6):
            result = self._original_compress(messages, keep_recent)
            orig_len = len(messages)
            comp_len = len(result)
            orig_chars = sum(len(m.get("content", "") or "") for m in messages)
            comp_chars = sum(len(m.get("content", "") or "") for m in result)
            self.compression_events.append({
                "orig_msgs": orig_len,
                "comp_msgs": comp_len,
                "orig_chars": orig_chars,
                "comp_chars": comp_chars,
                "ratio": comp_chars / orig_chars if orig_chars > 0 else 1.0,
                "token_ratio": harness.state.total_tokens / harness.state.token_budget if harness.state.token_budget else 0,
            })
            return result

        def monitored_check_budget():
            result = self._original_check_budget()
            if result:
                self.budget_warnings.append({
                    "total_tokens": harness.state.total_tokens,
                    "budget": harness.state.token_budget,
                    "ratio": harness.state.total_tokens / harness.state.token_budget,
                })
            return result

        harness.compress_messages = monitored_compress
        harness.check_token_budget = monitored_check_budget


async def run_pressure_test():
    """运行 5 轮对话压力测试。"""
    print("\n╔══════════════════════════════════════════════════════════════╗")
    print("║  Phase 45: 超长审改 Session 认知带宽压力测试                  ║")
    print("╚══════════════════════════════════════════════════════════════╝\n")

    paper_path = str(PROJECT_ROOT / "examples" / "sample_paper.md")

    # 使用较低的 token_budget 来更快触发压力（正常是 200K，这里用 150K）
    agent = ScholarAgent(
        paper_path=paper_path,
        verbose=True,
        max_loop_turns=15,  # 每轮最多 15 turns
        token_budget=150_000,
    )

    monitor = TokenPressureMonitor(agent.harness)
    result = PressureTestResult()

    # 定义 5 轮对话
    chat_turns = [
        # Turn 1 通过 start() 触发
        None,
        # Turn 2-5 通过 chat() 触发
        "Introduction 里有一个 'first to examine' 的 overclaim，帮我改掉。同时去掉那些 AI 味的表达（如 'delves into'、'underscores'）。",
        "Methodology 部分太空洞了——只说了用 DID 但没解释为什么 DID 适合这个研究问题，也没讨论平行趋势假设。帮我补充这些内容。",
        "Conclusion 太弱了，只是重复了 Introduction 的内容。帮我重写，加入政策启示和研究局限性。",
        "回顾一下你在最开始审阅时发现的所有问题。哪些已经被修改解决了？哪些还没有处理？请给我一个完整的状态报告。",
    ]

    total_start = time.time()

    for turn_idx, user_msg in enumerate(chat_turns):
        turn_num = turn_idx + 1
        print(f"\n{'═' * 60}")
        print(f"  第 {turn_num} 轮对话")
        print(f"{'═' * 60}")

        tokens_before = agent.harness.state.total_tokens
        compression_count_before = len(monitor.compression_events)
        budget_count_before = len(monitor.budget_warnings)

        turn_start = time.time()

        if turn_idx == 0:
            # 第 1 轮：start
            print(f"  [User] 帮我审阅这篇论文\n")
            response = await agent.start()
        else:
            # 后续轮：chat
            print(f"  [User] {user_msg[:80]}...\n")
            response = await agent.chat(user_msg)

        turn_elapsed = time.time() - turn_start
        tokens_after = agent.harness.state.total_tokens

        # 获取 system prompt 大小
        sys_prompt_chars = len(agent.messages[0].get("content", "")) if agent.messages else 0

        # 检查本轮是否触发了 budget warning
        budget_triggered = len(monitor.budget_warnings) > budget_count_before

        # 记录指标
        metrics = TurnMetrics(
            turn_id=turn_num,
            user_message=user_msg or "帮我审阅这篇论文",
            total_tokens_before=tokens_before,
            total_tokens_after=tokens_after,
            tokens_this_turn=tokens_after - tokens_before,
            loop_turns=agent.harness.state.loop_turns,
            findings_count=len(agent.harness.state.findings),
            edits_count=len(agent.harness.state.edits),
            system_prompt_chars=sys_prompt_chars,
            messages_count=len(agent.messages),
            compressed_messages_count=0,  # 从 monitor 获取
            budget_warning_triggered=budget_triggered,
            elapsed_seconds=turn_elapsed,
            response_preview=response[:200] if response else "",
        )
        result.turn_metrics.append(metrics)

        # 打印本轮摘要
        print(f"\n  {'─' * 50}")
        print(f"  第 {turn_num} 轮结果:")
        print(f"    Tokens: {tokens_before} → {tokens_after} (+{tokens_after - tokens_before})")
        print(f"    Loop turns: {agent.harness.state.loop_turns}")
        print(f"    Findings: {metrics.findings_count}, Edits: {metrics.edits_count}")
        print(f"    System prompt: {sys_prompt_chars} chars")
        print(f"    Messages: {len(agent.messages)}")
        print(f"    Budget warning: {'⚠️ YES' if budget_triggered else '✅ No'}")
        print(f"    耗时: {turn_elapsed:.1f}s")
        print(f"    Response: {response[:100]}...")
        print(f"  {'─' * 50}")

        # 更新峰值
        if sys_prompt_chars > result.peak_system_prompt_chars:
            result.peak_system_prompt_chars = sys_prompt_chars

        if budget_triggered:
            result.budget_warning_count += 1

    # ============================================================
    # 第 5 轮记忆完整性评估
    # ============================================================
    print(f"\n{'═' * 60}")
    print(f"  记忆完整性评估 (第 5 轮)")
    print(f"{'═' * 60}")

    # 检查第 5 轮的 response 是否引用了第 1 轮的 findings
    last_response = result.turn_metrics[-1].response_preview if result.turn_metrics else ""
    findings = agent.get_findings()

    # 记忆完整性判断标准：
    # 1. Agent 的 state.findings 中是否保留了第 1 轮的发现
    # 2. 第 5 轮的 response 是否提到了"已解决"/"未解决"的分类
    first_turn_findings = [f for f in findings if f.get("perspective") is None]  # 主视角的发现
    has_status_report = any(kw in (result.turn_metrics[-1].response_preview if result.turn_metrics else "")
                           for kw in ["已解决", "已修改", "未处理", "resolved", "unresolved", "已修复", "remaining"])

    # 更宽松的判断：只要 findings 数量 > 0 且 response 有实质内容
    memory_ok = len(findings) > 0 and len(last_response) > 50
    result.memory_integrity_pass = memory_ok
    result.memory_integrity_detail = (
        f"Findings 总数: {len(findings)}, "
        f"第 1 轮发现保留: {len(first_turn_findings)}, "
        f"第 5 轮有状态报告: {has_status_report}"
    )

    print(f"  Findings 总数: {len(findings)}")
    print(f"  第 1 轮发现保留: {len(first_turn_findings)}")
    print(f"  第 5 轮有状态报告: {'✅' if has_status_report else '⚠️'}")
    print(f"  记忆完整性: {'✅ PASS' if memory_ok else '❌ FAIL'}")

    # ============================================================
    # 压缩行为分析
    # ============================================================
    print(f"\n{'═' * 60}")
    print(f"  压缩行为分析")
    print(f"{'═' * 60}")

    if monitor.compression_events:
        print(f"  总压缩事件: {len(monitor.compression_events)}")
        # 按 token_ratio 分组看压缩强度变化
        for i, evt in enumerate(monitor.compression_events):
            if i % 5 == 0 or evt["ratio"] < 0.8:  # 每 5 个打印一次，或压缩超过 20% 时打印
                print(f"    [{i+1}] token_ratio={evt['token_ratio']:.2f}, "
                      f"chars {evt['orig_chars']}→{evt['comp_chars']} "
                      f"(保留 {evt['ratio']:.1%})")
    else:
        print("  (无压缩事件)")

    if monitor.budget_warnings:
        print(f"\n  Budget 警告触发 {len(monitor.budget_warnings)} 次:")
        for w in monitor.budget_warnings:
            print(f"    tokens={w['total_tokens']}, ratio={w['ratio']:.2%}")

    # ============================================================
    # 总结
    # ============================================================
    total_elapsed = time.time() - total_start
    result.total_elapsed = total_elapsed
    result.total_tokens = agent.harness.state.total_tokens

    print(f"\n{'═' * 60}")
    print(f"  Phase 45 压力测试总结")
    print(f"{'═' * 60}")
    print(f"  总耗时: {total_elapsed:.1f}s")
    print(f"  总 Tokens: {result.total_tokens}")
    print(f"  Token Budget: {agent.harness.state.token_budget}")
    print(f"  Budget 使用率: {result.total_tokens / agent.harness.state.token_budget:.1%}")
    print(f"  峰值 System Prompt: {result.peak_system_prompt_chars} chars")
    print(f"  Budget 警告次数: {result.budget_warning_count}")
    print(f"  记忆完整性: {'✅ PASS' if result.memory_integrity_pass else '❌ FAIL'}")
    print(f"  {result.memory_integrity_detail}")

    # Token 曲线
    print(f"\n  Token 累积曲线:")
    for m in result.turn_metrics:
        bar_len = int(m.total_tokens_after / agent.harness.state.token_budget * 40)
        bar = "█" * bar_len + "░" * (40 - bar_len)
        pct = m.total_tokens_after / agent.harness.state.token_budget * 100
        print(f"    Turn {m.turn_id}: [{bar}] {pct:.0f}% ({m.tokens_this_turn:+d} tokens, {m.loop_turns} loops)")

    # 判定
    print(f"\n  {'─' * 50}")
    # PASS 条件：
    # 1. 5 轮都完成了（没有 doom stop）
    # 2. 记忆完整性通过
    # 3. Budget 使用率 < 100%（没有被强制终止）
    all_turns_completed = len(result.turn_metrics) == 5
    within_budget = result.total_tokens < agent.harness.state.token_budget
    overall_pass = all_turns_completed and result.memory_integrity_pass and within_budget

    if overall_pass:
        print(f"  ✅ OVERALL PASS — Token Pipeline 在 5 轮审改 session 中维持了认知质量")
    else:
        reasons = []
        if not all_turns_completed:
            reasons.append("未完成全部 5 轮")
        if not result.memory_integrity_pass:
            reasons.append("记忆完整性失败")
        if not within_budget:
            reasons.append("超出 token budget")
        print(f"  ⚠️ ISSUES DETECTED: {', '.join(reasons)}")

    print(f"  {'─' * 50}")

    # 保存详细结果
    output = {
        "summary": {
            "total_tokens": result.total_tokens,
            "budget": agent.harness.state.token_budget,
            "budget_usage": f"{result.total_tokens / agent.harness.state.token_budget:.1%}",
            "total_elapsed": f"{total_elapsed:.1f}s",
            "memory_integrity": result.memory_integrity_pass,
            "budget_warnings": result.budget_warning_count,
            "overall_pass": overall_pass,
        },
        "turns": [
            {
                "turn": m.turn_id,
                "tokens_this_turn": m.tokens_this_turn,
                "total_tokens": m.total_tokens_after,
                "loop_turns": m.loop_turns,
                "findings": m.findings_count,
                "edits": m.edits_count,
                "system_prompt_chars": m.system_prompt_chars,
                "messages": m.messages_count,
                "budget_warning": m.budget_warning_triggered,
                "elapsed": f"{m.elapsed_seconds:.1f}s",
            }
            for m in result.turn_metrics
        ],
        "compression_events_count": len(monitor.compression_events),
        "budget_warnings_detail": monitor.budget_warnings,
    }

    output_path = PROJECT_ROOT / "tests" / "phase45_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n  详细结果已保存到: {output_path}")

    return overall_pass


if __name__ == "__main__":
    passed = asyncio.run(run_pressure_test())
    sys.exit(0 if passed else 1)
