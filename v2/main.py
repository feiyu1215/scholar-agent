#!/usr/bin/env python3
"""
ScholarAgent V2 — 用户级 CLI 入口。

这是 V2 的唯一正式入口。它做纯路由，不包含任何认知逻辑。
所有实际工作委托给 core/agent.py 中的 ScholarAgent 和 CollaborativeReview。

用法:
    # 交互式审稿（默认）
    python v2/main.py paper.pdf

    # 交互式 + HD-WM 假说驱动
    python v2/main.py paper.pdf --hdwm

    # 完整协作链：Scholar审阅 → Writer修改 → Scholar复审
    python v2/main.py paper.pdf --mode full

    # Writer 视角（修改论文）
    python v2/main.py paper.pdf --persona writer

    # 带参考文献
    python v2/main.py paper.pdf --references ref1.pdf ref2.md

    # 控制循环参数
    python v2/main.py paper.pdf --max-turns 20 --budget 80000
"""
import asyncio
import argparse
import os
import sys
from pathlib import Path

# 确保 v2/ 在 sys.path（支持 `python v2/main.py` 从项目根运行）
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="scholar-agent",
        description="ScholarAgent V2 — 认知驱动的学术审稿/写作 Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
模式说明:
  interactive  多轮对话式审稿（默认）。Agent 先自主审阅，然后你可以追问。
  full         完整协作链：Scholar初审 → Writer修改 → Scholar复审。自动运行，无需交互。

示例:
  %(prog)s paper.pdf                       # 交互审稿
  %(prog)s paper.pdf --mode full           # 三阶段自动协作
  %(prog)s paper.pdf --hdwm --persona scholar  # HD-WM + 指定 persona
  %(prog)s paper.pdf --references a.pdf b.md   # 加载参考文献
""",
    )

    # === 位置参数 ===
    parser.add_argument(
        "paper",
        help="论文文件路径（支持 PDF、Markdown、纯文本）",
    )

    # === 模式选择 ===
    parser.add_argument(
        "--mode", "-m",
        choices=["interactive", "full"],
        default="interactive",
        help="运行模式（默认: interactive）",
    )

    # === 认知配置 ===
    cognitive = parser.add_argument_group("认知配置")
    cognitive.add_argument(
        "--persona", "-p",
        choices=["scholar", "writer", "code_reviewer"],
        default="scholar",
        help="认知身份（默认: scholar）。仅对 interactive 模式有效。",
    )
    cognitive.add_argument(
        "--hdwm",
        action="store_true",
        help="激活 Hypothesis-Driven Working Memory（假说驱动工作记忆）",
    )
    cognitive.add_argument(
        "--max-turns",
        type=int,
        default=30,
        help="单轮用户消息内的最大 loop 轮次（默认: 30）",
    )
    cognitive.add_argument(
        "--budget",
        type=int,
        default=100000,
        help="Token 预算上限（默认: 100000）",
    )
    cognitive.add_argument(
        "--context-window",
        type=int,
        default=128000,
        help="模型 context window 大小（默认: 128000）",
    )

    # === 输入/输出 ===
    io_group = parser.add_argument_group("输入/输出")
    io_group.add_argument(
        "--references", "-r",
        nargs="+",
        default=None,
        help="参考文献路径列表（PDF/Markdown），Agent 可按需阅读",
    )
    io_group.add_argument(
        "--model",
        default=None,
        help=f"LLM 模型名称（默认: 环境变量 LLM_MODEL 或 gpt-4.1）",
    )
    io_group.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="减少过程输出",
    )
    io_group.add_argument(
        "--intent",
        default=None,
        help="初始审阅意图（可选，默认由 Agent 自主决定审阅策略）",
    )
    io_group.add_argument(
        "--stream",
        action="store_true",
        help="启用流式输出（需要 SCHOLAR_GODEL_STREAMING=1 环境变量）",
    )

    return parser


def _build_stream_callback(enabled: bool):
    """构建流式输出回调。enabled=False 时返回 None（零侵入）。"""
    if not enabled:
        return None

    from core.stream_events import StreamEvent

    def _on_stream(event: StreamEvent) -> None:
        """实时打印 StreamEvent 到 stderr（不干扰 stdout 的最终输出）。"""
        if event.type == "turn_start":
            print(f"\n{'·' * 40} Turn {event.turn} {'·' * 10}", file=sys.stderr)
        elif event.type == "thinking":
            # 逐 chunk 打印，不换行（模拟打字机效果）
            print(event.text, end="", file=sys.stderr, flush=True)
        elif event.type == "tool_start":
            print(f"\n  ⚙ {event.tool_name}...", end="", file=sys.stderr, flush=True)
        elif event.type == "tool_result":
            print(f" ✓", file=sys.stderr)
        elif event.type == "done":
            print(f"\n{'━' * 40} Done {'━' * 10}", file=sys.stderr)

    return _on_stream


async def run_interactive(args: argparse.Namespace) -> None:
    """交互式多轮审稿模式。"""
    from core.agent import ScholarAgent

    print("=" * 60, file=sys.stderr)
    print("  ScholarAgent V2 — 认知驱动的学术审稿助手", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  论文: {args.paper}", file=sys.stderr)
    print(f"  模型: {args.model or os.environ.get('LLM_MODEL', 'gpt-4.1')}", file=sys.stderr)
    print(f"  Persona: {args.persona}", file=sys.stderr)
    print(f"  HD-WM: {'ON' if args.hdwm else 'OFF'}", file=sys.stderr)
    print(f"  Streaming: {'ON' if args.stream else 'OFF'}", file=sys.stderr)
    print(f"  Max turns: {args.max_turns}", file=sys.stderr)
    print("  ────────────────────────────────────────────", file=sys.stderr)
    print("  命令: quit=退出  stats=统计  findings=发现列表", file=sys.stderr)
    print("        models=模型列表  switch <model>=切换模型  budget=预算", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    on_stream = _build_stream_callback(args.stream)

    # Multi-model: 尝试加载 SessionModelManager（可选）
    session_model_mgr = None
    providers_config = Path(__file__).resolve().parent / "config" / "providers.json"
    if providers_config.exists():
        try:
            from llm.session_model_manager import SessionModelManager
            session_model_mgr = SessionModelManager(config_path=providers_config)
            print(f"  多模型: ON ({len(session_model_mgr.list_models())} 个模型可用)", file=sys.stderr)
        except Exception as e:
            print(f"  多模型: OFF ({e})", file=sys.stderr)
    else:
        print("  多模型: OFF (运行 python3 -m llm.bootstrap 配置多模型)", file=sys.stderr)

    agent = ScholarAgent(
        paper_path=args.paper,
        model=args.model,
        verbose=not args.quiet,
        max_loop_turns=args.max_turns,
        token_budget=args.budget,
        context_window=args.context_window,
        persona=args.persona,
        reference_paths=args.references,
        enable_hdwm=args.hdwm,
        on_stream=on_stream,
        session_model_mgr=session_model_mgr,
    )

    # Agent 自主审阅
    print("\n[Agent 正在审阅论文...]\n", file=sys.stderr)
    response = await agent.start(user_intent=args.intent)
    print(f"\n{'─' * 40}")
    print(f"Agent: {response}")
    print(f"{'─' * 40}")

    # 多轮对话
    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！", file=sys.stderr)
            break

        if not user_input:
            continue
        if user_input.lower() == "quit":
            break
        if user_input.lower() == "stats":
            import json
            print(json.dumps(agent.get_stats(), indent=2, ensure_ascii=False))
            continue
        if user_input.lower() == "findings":
            findings = agent.get_findings()
            if not findings:
                print("  （暂无发现）")
            else:
                for i, f in enumerate(findings, 1):
                    print(f"  {i}. [{f['priority']}][{f['status']}] {f['finding']}")
            continue

        # Multi-model CLI commands
        if user_input.lower() == "models":
            if session_model_mgr is None:
                print("  多模型功能未启用。请先配置 config/providers.json。")
            else:
                try:
                    print(session_model_mgr.list_models_formatted())
                    # Phase 4: 显示模型分配表
                    print()
                    print(session_model_mgr.list_assignments_formatted())
                except Exception as e:
                    print(f"  ⚠️ 模型信息显示出错: {e}")
            continue
        if user_input.lower() == "budget":
            if session_model_mgr is None:
                print("  多模型功能未启用。")
            else:
                budget_report = session_model_mgr.get_budget_status()
                print(f"  {budget_report}")
                # Phase 1 补充：session_model_mgr 未注入 loop，显示 agent 级 token 统计
                if session_model_mgr.get_total_tokens() == 0:
                    agent_tokens = agent.harness.state.total_tokens
                    if agent_tokens > 0:
                        print(f"  (注: Agent 级统计 {agent_tokens:,} tokens，"
                              f"多模型维度追踪将在 Phase 2 启用)")
            continue
        if user_input.lower().startswith("switch "):
            target = user_input[7:].strip()
            if not target:
                print("  用法: switch <model-id>")
                continue
            if session_model_mgr is None:
                print("  多模型功能未启用。请先配置 config/providers.json。")
                continue
            try:
                msg = await session_model_mgr.switch_model(
                    target_model_id=target,
                    reason="用户 CLI 手动切换",
                    client=agent.client,
                    messages=agent.messages,
                )
                print(f"  {msg}")
            except ValueError as e:
                print(f"  切换失败: {e}")
            except Exception as e:
                print(f"  切换失败（内部错误）: {type(e).__name__}: {e}")
            continue

        print("\n[Agent 正在思考...]\n", file=sys.stderr)
        response = await agent.chat(user_input)
        print(f"\n{'─' * 40}")
        print(f"Agent: {response}")
        print(f"{'─' * 40}")

    # 会话结束（带 Agent 自省 + MetaReflector）
    print("\n[Agent 正在反思本次会话...]\n", file=sys.stderr)
    reflection_stats = await agent.end_session_with_reflection()
    print("\n[记忆已保存]", file=sys.stderr)
    if reflection_stats.get("reflections_count", 0) > 0:
        print(
            f"  反思产出: {reflection_stats['reflections_count']} 条经验, "
            f"存储: {reflection_stats.get('stored_count', 0)} 条",
            file=sys.stderr,
        )

    import json
    stats = agent.get_stats()
    print("\n[会话统计]", file=sys.stderr)
    print(json.dumps(stats, indent=2, ensure_ascii=False), file=sys.stderr)


async def run_full(args: argparse.Namespace) -> None:
    """完整协作链模式：Scholar初审 → Writer修改 → Scholar复审。"""
    from core.agent import CollaborativeReview

    print("=" * 60, file=sys.stderr)
    print("  ScholarAgent V2 — 协作审改模式 (Scholar→Writer→Scholar)", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  论文: {args.paper}", file=sys.stderr)
    print(f"  模型: {args.model or os.environ.get('LLM_MODEL', 'gpt-4.1')}", file=sys.stderr)
    print(f"  Max turns/phase: {args.max_turns}", file=sys.stderr)
    print(f"  Token budget: {args.budget}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    on_stream = _build_stream_callback(args.stream)

    collab = CollaborativeReview(
        paper_path=args.paper,
        model=args.model,
        verbose=not args.quiet,
        max_loop_turns=args.max_turns,
        token_budget=args.budget,
        context_window=args.context_window,
        reference_paths=args.references,
        on_stream=on_stream,
    )

    result = await collab.run(user_intent=args.intent)

    # 输出结果
    import json

    print("\n" + "=" * 60)
    print("  协作审改完成")
    print("=" * 60)

    if result.get("review"):
        print("\n[Phase 1 — Scholar 初审]")
        print("─" * 40)
        print(result["review"][:2000])
        if len(result.get("review", "")) > 2000:
            print(f"\n  ... (共 {len(result['review'])} 字符)")

    if result.get("revision"):
        print("\n[Phase 2 — Writer 修改]")
        print("─" * 40)
        print(result["revision"][:2000])
        if len(result.get("revision", "")) > 2000:
            print(f"\n  ... (共 {len(result['revision'])} 字符)")

    if result.get("re_review"):
        print("\n[Phase 3 — Scholar 复审]")
        print("─" * 40)
        print(result["re_review"][:2000])
        if len(result.get("re_review", "")) > 2000:
            print(f"\n  ... (共 {len(result['re_review'])} 字符)")

    # 统计
    print("\n[运行统计]", file=sys.stderr)
    stats = result.get("stats", {})
    print(json.dumps(stats, indent=2, ensure_ascii=False), file=sys.stderr)

    # findings 摘要
    findings = result.get("findings", [])
    if findings:
        print(f"\n[共 {len(findings)} 个 findings]", file=sys.stderr)
        for f in findings[:10]:
            print(f"  [{f.get('priority', '?')}] {f.get('finding', '')[:80]}", file=sys.stderr)
        if len(findings) > 10:
            print(f"  ... 及其他 {len(findings) - 10} 个", file=sys.stderr)


def main():
    """CLI 入口。"""
    parser = build_parser()
    args = parser.parse_args()

    # 校验文件存在
    paper_path = Path(args.paper)
    if not paper_path.exists():
        print(f"错误: 文件不存在 — {args.paper}", file=sys.stderr)
        sys.exit(1)

    # 规范化路径
    args.paper = str(paper_path.resolve())

    # 校验参考文献路径
    if args.references:
        for ref in args.references:
            if not Path(ref).exists():
                print(f"警告: 参考文献不存在 — {ref}", file=sys.stderr)

    # 路由到对应模式
    if args.mode == "full":
        asyncio.run(run_full(args))
    else:
        asyncio.run(run_interactive(args))


if __name__ == "__main__":
    main()
