"""
Phase 10: E2E 验证 — 压缩 + 元认知反思 真实效果

测试目标 (3 个核心假设):
    1. Phase 8 压缩: 第 6 轮后 context window 字符量节省 50%+
    2. Phase 9 reflect: Agent 在 10-15 轮内自主触发 1-3 次 reflect_and_plan
    3. 审阅质量: 产出 3+ 条 findings 且包含 evidence

使用 .workspace/paper/ 中已有的 51 sections 经济学论文 (比 PDF 提取更干净、更快)。
如果 workspace 中无论文，fallback 到 sample PDF。

用法:
    python3 core/test_e2e_phase8_9.py
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
# Instrumented Agent — 带可观测性的 Agent
# ============================================================

class InstrumentedAgent:
    """带指标追踪的 Agent wrapper。"""

    def __init__(self, paper_path: str, max_turns: int = 15):
        self.client = LLMClient()
        self.harness = Harness(
            paper_path=paper_path,
            max_loop_turns=max_turns,
            token_budget=150_000,
        )
        # paper 在 Harness.__init__ 中已自动加载
        self.tools = SCHOLAR_TOOLS
        self.messages: list[dict] = []

        # 可观测指标
        self.metrics = {
            "compression_events": [],   # 每轮的压缩数据
            "reflect_calls": [],        # reflect_and_plan 被调用的记录
            "tool_calls_log": [],       # 所有 tool call 名称记录
            "per_turn_tokens": [],      # 每轮 token 消耗
        }

        # Monkey-patch harness.execute_tool 来追踪 reflect 调用
        self._original_execute = self.harness.execute_tool
        self.harness.execute_tool = self._tracked_execute

    def _tracked_execute(self, name: str, args: dict) -> str:
        """包装 execute_tool 来追踪指标。"""
        self.metrics["tool_calls_log"].append({
            "turn": self.harness.state.loop_turns,
            "tool": name,
            "args_preview": str(args)[:100],
        })
        if name == "reflect_and_plan":
            self.metrics["reflect_calls"].append({
                "turn": self.harness.state.loop_turns,
                "trigger": args.get("trigger", "?"),
                "current_thinking": args.get("current_thinking", "")[:80],
            })
        return self._original_execute(name, args)

    async def run(self, user_intent: str) -> str:
        """运行一次完整的认知循环。"""
        workspace_state = self.harness.format_context()
        system_prompt = build_system_prompt(
            identity=SCHOLAR_IDENTITY,
            workspace_state=workspace_state,
        )

        self.messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_intent},
        ]

        result = await cognitive_loop(
            messages=self.messages,
            harness=self.harness,
            tools=self.tools,
            client=self.client,
            verbose=True,
        )

        if isinstance(result, LoopTalk):
            return result.message or result.content
        elif isinstance(result, LoopDone):
            return result.content.strip() or result.summary
        elif isinstance(result, LoopDoomStop):
            return f"[DoomStop] {result.reason}\n{result.content}"
        return str(result)

    def report(self) -> dict:
        """生成可观测性报告。"""
        findings = self.harness.state.findings
        findings_with_evidence = [f for f in findings if f.get("evidence")]

        return {
            "summary": {
                "total_turns": self.harness.state.loop_turns,
                "total_tokens": self.harness.state.total_tokens,
                "total_tool_calls": len(self.metrics["tool_calls_log"]),
                "findings_count": len(findings),
                "findings_with_evidence": len(findings_with_evidence),
                "reflect_calls_count": len(self.metrics["reflect_calls"]),
            },
            "reflect_calls": self.metrics["reflect_calls"],
            "tool_sequence": [t["tool"] for t in self.metrics["tool_calls_log"]],
            "findings": [
                {
                    "finding": f["finding"][:120],
                    "priority": f.get("priority"),
                    "status": f.get("status"),
                    "has_evidence": bool(f.get("evidence")),
                    "section": f.get("section", "?"),
                }
                for f in findings
            ],
            "client_stats": self.client.stats(),
        }


# ============================================================
# Main E2E Test
# ============================================================

async def main():
    print("=" * 70)
    print("  Phase 10: E2E Validation — Compression + Reflection + Quality")
    print("=" * 70)

    # 选择论文来源：优先 workspace（已分割好的 sections，更快更干净）
    workspace_paper = ROOT / ".workspace"
    sample_pdf = ROOT / "examples" / "sample_paper_economics.pdf"

    if workspace_paper.exists() and (workspace_paper / "paper" / "section_index.json").exists():
        paper_path = str(workspace_paper)
        paper_source = "workspace (pre-split sections)"
    elif sample_pdf.exists():
        paper_path = str(sample_pdf)
        paper_source = "PDF (sample_paper_economics.pdf)"
    else:
        print("ERROR: 没有可用论文。需要 .workspace/ 或 examples/sample_paper_economics.pdf")
        return 1

    # 验证 API 连接
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set. Check .env file.")
        return 1
    print(f"  API: {base_url} (key: ...{api_key[-6:]})")

    model = os.environ.get("LLM_MODEL", "gpt-4.1")
    print(f"  Paper source: {paper_source}")
    print(f"  Model: {model}")
    print(f"  Max turns: 15")
    print(f"  Token budget: 150k")
    print()

    # ---- 运行 Agent ----
    agent = InstrumentedAgent(paper_path=paper_path, max_turns=15)

    sections_count = len([k for k in agent.harness.state.paper_sections if k != "full"])
    total_chars = sum(len(v) for k, v in agent.harness.state.paper_sections.items() if k != "full")
    print(f"  Paper loaded: {sections_count} sections, ~{total_chars:,} chars")
    print()

    start_time = time.time()

    print("─" * 70)
    print("  [Running cognitive loop...]")
    print("─" * 70)

    response = await agent.run(
        "请审阅这篇论文。你应该战略性地阅读关键 sections，"
        "记录具体的发现（附原文证据），在适当时机反思进度。"
        "审阅完毕后用 talk_to_user 呈现你的审阅结论。"
    )

    elapsed = time.time() - start_time
    print("\n" + "─" * 70)
    print(f"  [Completed in {elapsed:.1f}s]")
    print("─" * 70)

    # ---- 输出 Agent 回复 ----
    print("\n" + "=" * 70)
    print("  AGENT RESPONSE (first 2000 chars)")
    print("=" * 70)
    print(response[:2000])
    if len(response) > 2000:
        print(f"\n  [... truncated, total {len(response)} chars ...]")

    # ---- 可观测性报告 ----
    report = agent.report()
    print("\n" + "=" * 70)
    print("  OBSERVABILITY REPORT")
    print("=" * 70)
    print(f"\n  Summary:")
    for k, v in report["summary"].items():
        print(f"    {k}: {v}")

    print(f"\n  Tool call sequence ({len(report['tool_sequence'])} calls):")
    for i, tool in enumerate(report["tool_sequence"]):
        marker = " ← REFLECT" if tool == "reflect_and_plan" else ""
        print(f"    [{i+1:2d}] {tool}{marker}")

    if report["reflect_calls"]:
        print(f"\n  Reflect calls ({len(report['reflect_calls'])}):")
        for r in report["reflect_calls"]:
            print(f"    Turn {r['turn']}: trigger='{r['trigger'][:60]}'")
    else:
        print(f"\n  ⚠️  No reflect_and_plan calls detected")

    print(f"\n  Findings ({len(report['findings'])}):")
    for i, f in enumerate(report["findings"], 1):
        ev_icon = "📄" if f["has_evidence"] else "⚠️"
        print(f"    [{i}] [{f['priority']}][{f['status']}] {ev_icon} {f['finding'][:80]}")

    print(f"\n  Client stats: {json.dumps(report['client_stats'], indent=4)}")

    # ---- 验证 3 个假设 ----
    print("\n" + "=" * 70)
    print("  HYPOTHESIS VALIDATION")
    print("=" * 70)

    s = report["summary"]

    # H1: 压缩效果 — 通过 token 节省间接验证
    # (直接验证需要 loop 层记录每轮 chars，这里用总 token < 预期上限作为代理)
    h1_pass = s["total_tokens"] < 100_000  # 15 轮无压缩预计 150k+
    h1_note = f"total_tokens={s['total_tokens']:,} (<100k threshold)"

    # H2: Agent 自主触发 reflect
    h2_pass = s["reflect_calls_count"] >= 1
    h2_note = f"reflect called {s['reflect_calls_count']} time(s)"

    # H3: 审阅质量 — findings >= 3 且 evidence 覆盖 >= 50%
    evidence_ratio = s["findings_with_evidence"] / max(s["findings_count"], 1)
    h3_pass = s["findings_count"] >= 3 and evidence_ratio >= 0.5
    h3_note = (
        f"findings={s['findings_count']}, "
        f"with_evidence={s['findings_with_evidence']} ({evidence_ratio*100:.0f}%)"
    )

    checks = [
        ("H1: Compression keeps tokens < 100k", h1_pass, h1_note),
        ("H2: Agent self-triggers reflect >= 1 time", h2_pass, h2_note),
        ("H3: Quality: >= 3 findings, >= 50% with evidence", h3_pass, h3_note),
    ]

    all_pass = True
    for label, passed, note in checks:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}: {label}")
        print(f"         {note}")
        if not passed:
            all_pass = False

    print(f"\n{'=' * 70}")
    if all_pass:
        print("  ✓ ALL HYPOTHESES VALIDATED — Phase 8+9 E2E verification passed!")
    else:
        print("  ⚠️  PARTIAL VALIDATION — Some hypotheses not confirmed")
        print("  This may indicate the need for prompt tuning or more turns.")
    print(f"{'=' * 70}")

    # 保存完整报告到文件
    report_path = ROOT / "core" / "e2e_report_phase10.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n  Full report saved to: {report_path}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
