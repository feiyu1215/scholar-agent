#!/usr/bin/env python3
"""
ScholarAgent — 认知驱动的学术审稿 Agent

Architecture:
    LLM = CPU (makes all decisions)
    Harness = Memory + Guardrails (maintains state, enforces boundaries)
    Loop = Clock (drives the think-act cycle)

Usage:
    python main.py paper.md                  # 交互式审阅一篇 Markdown 论文
    python main.py paper.pdf                 # 支持 PDF（需要 pymupdf）
    python main.py paper.md --quiet          # 减少过程输出
    python main.py paper.md --model gpt-4o   # 指定模型
    python main.py paper.md --turns 10       # 限制最大循环轮次
    python main.py paper.md --budget 50000   # 限制 token 预算

Commands during interaction:
    quit / q / exit   — 退出
    stats             — 查看运行统计
    findings          — 查看所有发现
    (其他输入)        — 和 Agent 对话
"""

from __future__ import annotations

import os
import sys
import asyncio
import json
import argparse
from pathlib import Path

# Load environment before anything else
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")
sys.path.insert(0, str(Path(__file__).parent))

from core.agent import ScholarAgent


async def interactive_session(args):
    """交互式多轮对话会话。"""

    if not Path(args.paper).exists():
        print(f"错误: 文件不存在 — {args.paper}", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("  ScholarAgent — 认知驱动的学术审稿助手")
    print("=" * 60)
    print(f"  论文: {args.paper}")
    print(f"  模型: {args.model or os.environ.get('LLM_MODEL', 'gpt-4.1')}")
    print(f"  轮次上限: {args.turns} | Token 预算: {args.budget}")
    print(f"  命令: 'quit' 退出 | 'stats' 统计 | 'findings' 发现列表")
    print("=" * 60)

    agent = ScholarAgent(
        paper_path=args.paper,
        model=args.model,
        verbose=not args.quiet,
        max_loop_turns=args.turns,
        token_budget=args.budget,
    )

    # 启动 — Agent 自主审阅
    intent = args.intent if args.intent else None
    print("\n[Agent 正在审阅论文...]\n")
    response = await agent.start(user_intent=intent)
    print(f"\n{'─' * 40}")
    print(f"Agent: {response}")
    print(f"{'─' * 40}")

    # 多轮对话
    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue

        cmd = user_input.lower()
        if cmd in ("quit", "q", "exit"):
            break
        if cmd == "stats":
            print(json.dumps(agent.get_stats(), indent=2, ensure_ascii=False))
            continue
        if cmd == "findings":
            findings = agent.get_findings()
            if not findings:
                print("  (尚无发现)")
            for i, f in enumerate(findings, 1):
                icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(f["priority"], "⚪")
                print(f"  {icon} [{f['status']}] {f['finding']}")
            continue

        print("\n[Agent 正在思考...]\n")
        response = await agent.chat(user_input)
        print(f"\n{'─' * 40}")
        print(f"Agent: {response}")
        print(f"{'─' * 40}")

    # 结束统计
    print("\n[会话统计]")
    print(json.dumps(agent.get_stats(), indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(
        description="ScholarAgent — 认知驱动的学术审稿助手",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py paper.md                  # 审阅 Markdown 论文
  python main.py paper.pdf                 # 审阅 PDF 论文
  python main.py paper.md --intent "重点看方法论"
  python main.py paper.md --quiet --turns 8
        """,
    )
    parser.add_argument("paper", help="论文文件路径（支持 .md / .pdf）")
    parser.add_argument("--model", default=None, help="LLM 模型名称（默认从 .env 读取）")
    parser.add_argument("--quiet", action="store_true", help="减少过程输出")
    parser.add_argument("--turns", type=int, default=30, help="最大循环轮次（默认 30）")
    parser.add_argument("--budget", type=int, default=200000, help="Token 预算（默认 200000）")
    parser.add_argument("--intent", default=None, help="初始审阅意图（如 '重点看实验设计'）")
    args = parser.parse_args()

    asyncio.run(interactive_session(args))


if __name__ == "__main__":
    main()
