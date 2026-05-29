"""
Phase 11: 多轮对话 E2E 验证

测试目标：验证 Agent 作为"持续思考的认知体"在多轮对话中的行为
    - H1: 多轮对话中保持对之前发现的记忆（状态连贯性）
    - H2: Agent 能从审阅模式自然切换到解释/修改模式（无 workflow）
    - H3: 压缩在多轮累积后仍有效（不破坏关键信息）

测试设计：模拟 3 轮用户交互
    Round 1: "帮我审阅这篇论文" → Agent 审阅并 talk_to_user
    Round 2: "你提到的那个机制分析问题，具体展开说说" → Agent 基于已有 findings 深入
    Round 3: "帮我改一下 Introduction，让 contribution 更突出" → Agent 自然转向修改

关键验证点：
    - Round 2 时 Agent 引用 Round 1 的 findings（而非重新审阅）
    - Round 3 时 Agent 使用 edit_section（模式切换是自然涌现的）
    - 压缩在 Round 2/3 时生效但不导致 Agent 丢失关键上下文

用法:
    python3 core/test_e2e_multiturn.py
"""

import os
import sys
import json
import time
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from llm.client import LLMClient
from core.harness import Harness
from core.identity import SCHOLAR_IDENTITY, SCHOLAR_TOOLS, build_system_prompt
from core.loop import cognitive_loop, LoopDone, LoopTalk, LoopDoomStop


# ============================================================
# Multi-turn Agent — 模拟多轮交互
# ============================================================

class MultiTurnAgent:
    """支持多轮对话的 Agent wrapper。"""

    def __init__(self, paper_path: str, max_turns_per_round: int = 12):
        self.client = LLMClient()
        self.harness = Harness(
            paper_path=paper_path,
            max_loop_turns=max_turns_per_round,
            token_budget=200_000,
        )
        self.tools = SCHOLAR_TOOLS
        self.messages: list[dict] = []
        self.max_turns_per_round = max_turns_per_round

        # 多轮追踪
        self.rounds: list[dict] = []  # 每轮的摘要数据

    async def chat(self, user_message: str) -> str:
        """
        发送一轮用户消息并获取 Agent 响应。
        多轮间保持 messages 连贯性（核心设计：Agent 是持续思考的实体）。
        """
        round_start = time.time()
        round_num = len(self.rounds) + 1

        # 多轮对话：重置 loop 计数（但不清空 messages 或 findings）
        self.harness.new_conversation_turn()

        # 动态刷新 system prompt（让 Agent 看到最新 workspace state）
        workspace_state = self.harness.format_context()
        system_prompt = build_system_prompt(
            identity=SCHOLAR_IDENTITY,
            workspace_state=workspace_state,
        )

        # 首轮：初始化 messages；后续轮：更新 system prompt + 追加 user message
        if not self.messages:
            self.messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]
        else:
            # 更新 system prompt（动态刷新 workspace state）
            self.messages[0] = {"role": "system", "content": system_prompt}
            self.messages.append({"role": "user", "content": user_message})

        print(f"\n{'='*70}", file=sys.stderr)
        print(f"  Round {round_num}: User says: \"{user_message[:80]}...\"", file=sys.stderr)
        print(f"{'='*70}", file=sys.stderr)

        # 记录轮次开始时的状态
        findings_before = len(self.harness.state.findings)
        edits_before = len(self.harness.state.edits)
        tokens_before = self.harness.state.total_tokens
        msgs_before = len(self.messages)

        # 运行认知循环
        result = await cognitive_loop(
            messages=self.messages,
            harness=self.harness,
            tools=self.tools,
            client=self.client,
            verbose=True,
        )

        # 提取响应
        if isinstance(result, LoopTalk):
            response = result.message or result.content
        elif isinstance(result, LoopDone):
            response = result.content.strip() or result.summary
        elif isinstance(result, LoopDoomStop):
            response = f"[DoomStop] {result.reason}\n{result.content}"
        else:
            response = str(result)

        elapsed = time.time() - round_start

        # 记录本轮数据
        round_data = {
            "round": round_num,
            "user_message": user_message,
            "response_preview": response[:300],
            "response_length": len(response),
            "elapsed_seconds": round(elapsed, 1),
            "turns_used": self.harness.state.loop_turns,
            "findings_added": len(self.harness.state.findings) - findings_before,
            "edits_added": len(self.harness.state.edits) - edits_before,
            "tokens_consumed": self.harness.state.total_tokens - tokens_before,
            "total_tokens": self.harness.state.total_tokens,
            "messages_count": len(self.messages),
            "messages_added": len(self.messages) - msgs_before,
        }
        self.rounds.append(round_data)

        return response

    def report(self) -> dict:
        """生成多轮交互的总报告。"""
        findings = self.harness.state.findings
        edits = self.harness.state.edits

        return {
            "total_rounds": len(self.rounds),
            "total_tokens": self.harness.state.total_tokens,
            "total_findings": len(findings),
            "total_edits": len(edits),
            "total_messages": len(self.messages),
            "rounds": self.rounds,
            "findings": [
                {
                    "finding": f["finding"][:120],
                    "priority": f.get("priority"),
                    "has_evidence": bool(f.get("evidence")),
                    "section": f.get("section", "?"),
                }
                for f in findings
            ],
            "edits": [
                {
                    "section": e["section"],
                    "reason": e["reason"][:80],
                }
                for e in edits
            ],
            "client_stats": self.client.stats(),
        }


# ============================================================
# Main E2E Multi-turn Test
# ============================================================

async def main():
    print("=" * 70)
    print("  Phase 11: Multi-turn Dialogue E2E Validation")
    print("  \"Agent 是持续思考的认知体，不是单次任务处理器\"")
    print("=" * 70)

    # 论文路径
    workspace_paper = ROOT / ".workspace"
    if not (workspace_paper / "paper" / "section_index.json").exists():
        print("ERROR: .workspace/paper/section_index.json not found")
        return 1

    # API 验证
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    model = os.environ.get("LLM_MODEL", "gpt-4.1")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set.")
        return 1

    print(f"  Model: {model}")
    print(f"  API: {base_url}")
    print(f"  Max turns per round: 12")
    print(f"  Token budget: 200k (across all rounds)")

    agent = MultiTurnAgent(paper_path=str(workspace_paper), max_turns_per_round=12)
    sections_count = len([k for k in agent.harness.state.paper_sections if k != "full"])
    print(f"  Paper: {sections_count} sections loaded")
    print()

    # ================================================================
    # Round 1: 初始审阅
    # ================================================================
    r1_response = await agent.chat(
        "请审阅这篇论文。重点关注方法论和实证结果部分，"
        "记录你发现的主要问题（附原文证据），"
        "然后用 talk_to_user 向我呈现你的审阅结论。"
    )

    print(f"\n{'─'*70}")
    print(f"  Round 1 Response (first 600 chars):")
    print(f"{'─'*70}")
    print(r1_response[:600])
    if len(r1_response) > 600:
        print(f"  [... total {len(r1_response)} chars ...]")

    # ================================================================
    # Round 2: 用户追问 — 要求展开某个 finding
    # ================================================================
    # 动态构造追问：基于 Round 1 的实际 findings
    findings = agent.harness.state.findings
    if findings:
        # 选第一条 finding 来追问
        target_finding = findings[0]["finding"][:60]
        r2_question = (
            f"你提到了'{target_finding}'这个问题，"
            f"能展开说说吗？具体是论文哪里的表述有问题？"
            f"如果可能的话，给我一个修改建议。"
        )
    else:
        r2_question = (
            "你的审阅报告中提到了一些方法论问题，"
            "能选一个最重要的展开说说吗？具体是哪些数据或论述有矛盾？"
        )

    r2_response = await agent.chat(r2_question)

    print(f"\n{'─'*70}")
    print(f"  Round 2 Response (first 600 chars):")
    print(f"{'─'*70}")
    print(r2_response[:600])
    if len(r2_response) > 600:
        print(f"  [... total {len(r2_response)} chars ...]")

    # ================================================================
    # Round 3: 方向切换 — 从审阅到修改
    # ================================================================
    r3_response = await agent.chat(
        "好的，我理解了。现在请帮我改一下 Introduction 部分，"
        "让论文的 contribution 表述更加清晰和有力。"
        "具体来说，我希望 contribution 能更突出「多种 DID 估计器交叉验证」这个方法论贡献。"
        "请直接修改并告诉我改了什么。"
    )

    print(f"\n{'─'*70}")
    print(f"  Round 3 Response (first 600 chars):")
    print(f"{'─'*70}")
    print(r3_response[:600])
    if len(r3_response) > 600:
        print(f"  [... total {len(r3_response)} chars ...]")

    # ================================================================
    # Hypothesis Validation
    # ================================================================
    report = agent.report()

    print(f"\n{'='*70}")
    print(f"  MULTI-TURN REPORT")
    print(f"{'='*70}")

    for rd in report["rounds"]:
        print(f"\n  Round {rd['round']}:")
        print(f"    Turns used: {rd['turns_used']}")
        print(f"    Tokens consumed: {rd['tokens_consumed']:,}")
        print(f"    Findings added: {rd['findings_added']}")
        print(f"    Edits added: {rd['edits_added']}")
        print(f"    Messages count: {rd['messages_count']}")
        print(f"    Elapsed: {rd['elapsed_seconds']}s")

    print(f"\n  Totals:")
    print(f"    Total tokens: {report['total_tokens']:,}")
    print(f"    Total findings: {report['total_findings']}")
    print(f"    Total edits: {report['total_edits']}")
    print(f"    Total messages: {report['total_messages']}")
    print(f"    Client stats: {json.dumps(report['client_stats'], indent=4)}")

    # ---- 验证假设 ----
    print(f"\n{'='*70}")
    print(f"  HYPOTHESIS VALIDATION")
    print(f"{'='*70}")

    # H1: Round 2 能引用 Round 1 的 findings（不重新审阅）
    # 验证: Round 2 没有大量 read_section 调用（它应该直接基于已有 findings 展开）
    r2_data = report["rounds"][1]
    h1_pass = r2_data["findings_added"] <= 2  # 追问不应该产生大量新 findings
    h1_note = (
        f"Round 2 added {r2_data['findings_added']} findings, "
        f"used {r2_data['turns_used']} turns "
        f"(expected: leverages existing findings, not full re-review)"
    )

    # H2: Round 3 使用了 edit_section（自然模式切换）
    h2_pass = report["total_edits"] >= 1
    h2_note = f"total_edits={report['total_edits']} (expected >= 1 from Round 3)"

    # H3: 压缩有效 — 总 token < 200k 且 Round 3 仍能正常工作
    h3_pass = report["total_tokens"] < 200_000
    h3_note = f"total_tokens={report['total_tokens']:,} (<200k budget)"

    # 额外: 多轮连贯性 — Round 2 的响应包含对 Round 1 findings 的引用
    # （通过检查 Round 2 response 是否包含 Round 1 findings 的关键词）
    r1_findings_keywords = [f["finding"][:30] for f in findings[:3]] if findings else []
    coherence_hits = sum(1 for kw in r1_findings_keywords if kw[:15] in r2_response)
    h_extra_pass = coherence_hits > 0 or "机制" in r2_response or "mechanism" in r2_response.lower()
    h_extra_note = (
        f"Round 2 references Round 1 findings: "
        f"keyword_hits={coherence_hits}, "
        f"contains_mechanism_ref={'机制' in r2_response or 'mechanism' in r2_response.lower()}"
    )

    checks = [
        ("H1: Round 2 leverages existing findings (no full re-review)", h1_pass, h1_note),
        ("H2: Round 3 triggers edit_section (natural mode switch)", h2_pass, h2_note),
        ("H3: Total tokens stay within budget across 3 rounds", h3_pass, h3_note),
        ("H+: Round 2 response references Round 1 findings (coherence)", h_extra_pass, h_extra_note),
    ]

    all_pass = True
    for label, passed, note in checks:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}: {label}")
        print(f"         {note}")
        if not passed:
            all_pass = False

    print(f"\n{'='*70}")
    if all_pass:
        print("  ✓ ALL HYPOTHESES VALIDATED — Multi-turn dialogue works!")
        print("    Agent maintains cognitive continuity across rounds.")
    else:
        print("  ⚠️  PARTIAL VALIDATION — Some aspects need attention")
    print(f"{'='*70}")

    # 保存报告
    report_path = ROOT / "core" / "e2e_report_phase11.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n  Full report saved to: {report_path}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
