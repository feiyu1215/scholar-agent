"""
core/agent.py — ScholarAgent 入口

这是"真正的 Agent"——不是 workflow 引擎，不是 tool router。
它是一个持续存在的认知实体，通过对话与用户协作。

职责:
    1. 初始化: 加载论文 → 构建 Harness → 准备 LLM client
    2. 对话循环: 接收用户消息 → 驱动 cognitive loop → 返回结果
    3. 多轮记忆: messages 列表在整个对话期间累积

用法:
    agent = ScholarAgent(paper_path="path/to/paper.md")
    await agent.start()              # 加载论文，Agent 自主开始审阅
    response = await agent.chat("你觉得 Introduction 怎么样？")
    response = await agent.chat("帮我把那个 overclaim 改了")

架构关系:
    agent.py (组装者)
      ├── identity.py (认知身份 + 工具定义)
      ├── harness.py  (状态守护 + 工具执行)
      └── loop.py     (认知循环引擎)

不在这里做的事 (来自 COGNITIVE_ANCHOR §3 anti-patterns):
    - 不做 scenario routing / intent classification
    - 不做 step-by-step workflow
    - 不做 tool registry pattern
    - 决策完全在 LLM 内部发生
"""

from __future__ import annotations

import os
import sys
import asyncio
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from llm.client import LLMClient
from core.harness import Harness
from core.identity import SCHOLAR_IDENTITY, SCHOLAR_TOOLS, build_system_prompt, get_persona
from core.loop import cognitive_loop, LoopDone, LoopTalk, LoopDoomStop


class ScholarAgent:
    """
    ScholarAgent: 一个能持续思考、多轮对话的认知 Agent。

    它不是"接收指令 → 输出结果"的工具。
    它是"持续存在的认知实体"——记得之前的对话，
    会自主决定怎么探索内容，能和用户协作解决问题。

    通过 persona 参数切换认知身份：
    - "scholar": 学术审稿人（审阅论文）
    - "writer": 学术写作专家（修改论文）
    - "code_reviewer": 代码审阅专家（审阅代码）

    所有 persona 共享同一个认知循环引擎（loop.py）和状态守护层（harness.py），
    行为差异完全来自 identity + tools 的不同。这是 Phase 53 的核心证明。
    """

    def __init__(
        self,
        paper_path: str | None = None,
        model: str | None = None,
        verbose: bool = True,
        max_loop_turns: int = 30,
        token_budget: int = 100000,
        context_window: int = 128_000,
        persona: str = "scholar",
        content_sections: dict[str, str] | None = None,
        reference_paths: list[str] | None = None,
    ):
        """
        Args:
            paper_path: 内容文件路径（论文 markdown/pdf，或代码目录）。
                        对于 code_reviewer persona，可以为 None（通过 content_sections 传入）。
            model: LLM 模型名称（默认从环境变量读取）
            verbose: 是否打印过程信息
            max_loop_turns: 单轮用户消息内的最大 loop 轮次
            token_budget: 整个对话的 token 预算（累计消耗上限）
            context_window: 模型 context window 大小（用于认知带宽管理）
            persona: 认知身份 ("scholar" / "writer" / "code_reviewer")。
                     不同 persona 使用同一个 loop 和 harness，
                     行为差异完全来自 identity + tools 的不同。
            content_sections: 直接传入内容分段（Phase 53: 支持非文件来源的内容）。
                             格式: {"section_name": "content_text", ...}
                             如果提供，将跳过文件加载，直接使用这些内容。
            reference_paths: Phase 58: 用户提供的参考文献路径列表（PDF/Markdown）。
                            加载后 Agent 可通过 read_reference 工具按需阅读。
        """
        self.paper_path = paper_path
        self.verbose = verbose
        self.persona_name = persona

        # 根据 persona 获取 identity 和 tools
        identity, tools = get_persona(persona)

        # 初始化组件
        self.client = LLMClient(model=model)
        self.harness = Harness(
            paper_path=paper_path,
            max_loop_turns=max_loop_turns,
            token_budget=token_budget,
            context_window=context_window,
            persona=persona,  # Phase 55: 传递 persona 给 Harness/Checker
            reference_paths=reference_paths,  # Phase 58: 用户参考文献
        )
        self.tools = tools
        self.identity = identity

        # Phase 53: 支持直接传入内容分段（代码审阅场景）
        if content_sections:
            self.harness.state.paper_sections = dict(content_sections)
            self.harness._paper_loaded = True

        # 对话 messages — 在整个对话期间持续累积
        self.messages: list[dict] = []
        self._started = False

    async def start(self, user_intent: str | None = None) -> str:
        """
        启动 Agent: 加载论文，让 Agent 根据用户意图自主行动。

        Args:
            user_intent: 用户想让 Agent 做什么。
                - 如果为 None，Agent 自主决定如何开始（审稿人人格会自然地审阅论文）
                - 如果有具体意图（如 "帮我看 Introduction 的逻辑"），Agent 会以此为起点

        Returns:
            Agent 的回复（talk_to_user 的内容，或 done summary）
        """
        if self._started:
            raise RuntimeError("Agent 已经启动过了。用 chat() 继续对话。")

        # 加载论文
        self.harness.load_paper()
        self._started = True

        # 构建初始 system prompt
        workspace_state = self.harness.format_context()
        system_prompt = build_system_prompt(
            identity=self.identity,
            workspace_state=workspace_state,
        )

        # 用户消息: 传递用户的真实意图，不预设策略
        # Agent 的认知身份会引导它自然地以审稿人方式思考
        if user_intent:
            first_message = user_intent
        else:
            first_message = "这篇论文已经加载好了，请帮我审阅。"

        self.messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": first_message},
        ]

        # 驱动认知循环
        result = await cognitive_loop(
            messages=self.messages,
            harness=self.harness,
            tools=self.tools,
            client=self.client,
            verbose=self.verbose,
        )

        return self._handle_result(result)

    async def chat(self, user_message: str) -> str:
        """
        和 Agent 对话: 追问、要求修改、讨论发现。

        Args:
            user_message: 用户说的话

        Returns:
            Agent 的回复
        """
        if not self._started:
            raise RuntimeError("Agent 尚未启动。先调用 await agent.start()。")

        # 重置 Harness 的循环计数器（新一轮用户消息）
        self.harness.new_conversation_turn()

        # 更新 system prompt (workspace state 可能已变)
        workspace_state = self.harness.format_context()
        system_prompt = build_system_prompt(
            identity=self.identity,
            workspace_state=workspace_state,
        )
        # 更新 messages 中的 system prompt（始终保持最新状态）
        self.messages[0] = {"role": "system", "content": system_prompt}

        # 追加用户消息
        self.messages.append({"role": "user", "content": user_message})

        # 驱动认知循环
        result = await cognitive_loop(
            messages=self.messages,
            harness=self.harness,
            tools=self.tools,
            client=self.client,
            verbose=self.verbose,
        )

        return self._handle_result(result)

    def _handle_result(self, result) -> str:
        """统一处理 Loop 结果。"""
        if isinstance(result, LoopTalk):
            return result.message or result.content
        elif isinstance(result, LoopDone):
            # 优先返回 content（Agent 的实际文本输出），因为 summary 可能是空的
            return result.content.strip() or result.summary or "(Agent 完成但未产生文本输出)"
        elif isinstance(result, LoopDoomStop):
            return f"[系统中断] {result.reason}\n\n到目前为止的输出:\n{result.content}"
        else:
            return str(result)

    def get_findings(self) -> list[dict]:
        """获取当前所有发现。"""
        return self.harness.state.findings

    def get_edits(self) -> list[dict]:
        """获取所有修改历史。"""
        return self.harness.state.edits

    def get_stats(self) -> dict:
        """获取运行统计。"""
        return {
            "model": self.client.model,
            "loop_turns_total": self.harness.state.loop_turns,
            "conversation_turns": self.harness.state.conversation_turns,
            "total_tokens": self.harness.state.total_tokens,
            "findings_count": len(self.harness.state.findings),
            "edits_count": len(self.harness.state.edits),
            "tool_calls": self.harness.state.tool_call_counts,  # Phase 31: 工具使用频次
            "client_stats": self.client.stats(),
            "checker_stats": self.harness.checker.stats(),  # Phase 50: 认知校验层统计
        }

    def end_session(self):
        """
        Phase 15: 结束当前会话，将认知产出沉淀到跨会话记忆。

        应在用户退出对话时调用。自动提取:
        - findings 摘要
        - 领域模式
        - 用户关注点
        """
        # 收集用户消息（从 messages 中提取）
        user_messages = [
            m["content"] for m in self.messages
            if m.get("role") == "user" and m.get("content")
        ]
        self.harness.end_session(user_messages=user_messages)


# ============================================================
# Phase 51: 多人格协作链
# ============================================================

class CollaborativeReview:
    """
    Phase 51: Scholar → Writer → Scholar 协作链。

    这不是 workflow。这是同一个认知实体在不同人格间的切换。
    三个阶段共享同一个 Harness（同一篇论文、同一份 findings/edits 状态），
    但各自拥有独立的 messages（独立的认知上下文）。

    认知连续性通过 user_intent 传递：
    - Scholar 的 findings → Writer 的输入上下文
    - Writer 的 edits → 复审 Scholar 的输入上下文

    设计原则（来自 COGNITIVE_ANCHOR）：
    - 不做 scenario routing：每个 persona 自主决定做什么
    - 不做 step-by-step workflow：persona 切换是"认知视角转换"
    - 决策完全在 LLM 内部发生：我们只传递上下文，不传递指令

    用法:
        collab = CollaborativeReview(paper_path="paper.md")
        result = await collab.run()
        # result 包含三阶段的完整输出
    """

    def __init__(
        self,
        paper_path: str,
        model: str | None = None,
        verbose: bool = True,
        max_loop_turns: int = 30,
        token_budget: int = 100000,
        context_window: int = 128_000,
        reference_paths: list[str] | None = None,
    ):
        self.paper_path = paper_path
        self.model = model
        self.verbose = verbose
        self.max_loop_turns = max_loop_turns
        self.token_budget = token_budget
        self.context_window = context_window

        # 共享的 Harness — 三个 persona 看到同一篇论文、同一份状态
        self.harness = Harness(
            paper_path=paper_path,
            max_loop_turns=max_loop_turns,
            token_budget=token_budget * 3,  # 三阶段总预算
            context_window=context_window,
            reference_paths=reference_paths,  # Phase 58: 用户参考文献
        )

        # 结果收集
        self.phases: list[dict] = []

    async def run(self, user_intent: str | None = None) -> dict:
        """
        执行完整的协作链: Scholar审阅 → Writer修改 → Scholar复审。

        Args:
            user_intent: 用户的原始意图（传递给第一个 Scholar）

        Returns:
            dict: {
                "review": str,       # Scholar 初审输出
                "revision": str,     # Writer 修改输出
                "re_review": str,    # Scholar 复审输出
                "findings": list,    # 最终 findings
                "edits": list,       # 所有 edits
                "stats": dict,       # 运行统计
            }
        """
        # 加载论文（只加载一次，三个 persona 共享）
        self.harness.load_paper()

        # ---- Phase 1: Scholar 初审 ----
        if self.verbose:
            print("\n" + "=" * 60, file=sys.stderr)
            print("  [Phase 1/3] Scholar 初审", file=sys.stderr)
            print("=" * 60, file=sys.stderr)

        review_output = await self._run_persona(
            persona_name="scholar",
            user_intent=user_intent or "请帮我审阅这篇论文，找出所有值得关注的问题。",
        )
        self.phases.append({"persona": "scholar", "phase": "review", "output": review_output})

        # ---- 认知连续性: 从 Harness 提取 Scholar 的 findings ----
        findings_for_writer = self._format_findings_for_writer()

        # ---- Phase 2: Writer 修改 ----
        if self.verbose:
            print("\n" + "=" * 60, file=sys.stderr)
            print("  [Phase 2/3] Writer 修改", file=sys.stderr)
            print("=" * 60, file=sys.stderr)

        # Writer 的 user_intent 包含 Scholar 的发现
        writer_intent = (
            f"审稿人对这篇论文提出了以下问题，请根据这些反馈修改论文：\n\n"
            f"{findings_for_writer}\n\n"
            f"请逐一处理这些问题，做出具体的文本修改。"
        )

        # 重置 loop 计数器（新阶段）
        self.harness.new_conversation_turn()

        revision_output = await self._run_persona(
            persona_name="writer",
            user_intent=writer_intent,
        )
        self.phases.append({"persona": "writer", "phase": "revision", "output": revision_output})

        # ---- 认知连续性: 从 Harness 提取 Writer 的 edits ----
        edits_for_reviewer = self._format_edits_for_reviewer()

        # ---- Phase 3: Scholar 复审 ----
        if self.verbose:
            print("\n" + "=" * 60, file=sys.stderr)
            print("  [Phase 3/3] Scholar 复审", file=sys.stderr)
            print("=" * 60, file=sys.stderr)

        # 复审 Scholar 的 user_intent 包含 Writer 的修改记录
        re_review_intent = (
            f"这篇论文经过修改，以下是修改记录：\n\n"
            f"{edits_for_reviewer}\n\n"
            f"请重新审阅修改后的论文，评估修改是否充分解决了之前的问题，"
            f"并指出是否有新的问题或遗漏。"
        )

        # 重置 loop 计数器
        self.harness.new_conversation_turn()

        re_review_output = await self._run_persona(
            persona_name="scholar",
            user_intent=re_review_intent,
        )
        self.phases.append({"persona": "scholar", "phase": "re_review", "output": re_review_output})

        return {
            "review": review_output,
            "revision": revision_output,
            "re_review": re_review_output,
            "findings": self.harness.state.findings,
            "edits": self.harness.state.edits,
            "stats": self._collect_stats(),
        }

    async def _run_persona(self, persona_name: str, user_intent: str) -> str:
        """
        以指定 persona 运行一轮认知循环。

        每个 persona 有独立的 messages（认知隔离），
        但共享同一个 Harness（状态连续）。
        """
        identity, tools = get_persona(persona_name)
        client = LLMClient(model=self.model)

        # 构建独立的 messages
        workspace_state = self.harness.format_context()
        system_prompt = build_system_prompt(
            identity=identity,
            workspace_state=workspace_state,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_intent},
        ]

        # 驱动认知循环
        result = await cognitive_loop(
            messages=messages,
            harness=self.harness,
            tools=tools,
            client=client,
            verbose=self.verbose,
        )

        # 提取输出
        if isinstance(result, LoopTalk):
            return result.message or result.content
        elif isinstance(result, LoopDone):
            return result.content.strip() or result.summary or "(完成但无文本输出)"
        elif isinstance(result, LoopDoomStop):
            return f"[系统中断] {result.reason}\n\n到目前为止:\n{result.content}"
        return str(result)

    def _format_findings_for_writer(self) -> str:
        """将 Scholar 的 findings 格式化为 Writer 可理解的上下文。"""
        findings = self.harness.state.findings
        if not findings:
            return "(审稿人未发现具体问题)"

        lines = []
        for i, f in enumerate(findings, 1):
            priority = f.get("priority", "medium")
            section = f.get("section", "未知")
            finding = f.get("finding", "")
            evidence = f.get("evidence", "")

            line = f"{i}. [{priority}] {finding}"
            if section:
                line += f"\n   位置: {section}"
            if evidence:
                line += f"\n   原文: \"{evidence[:200]}\""
            lines.append(line)

        return "\n\n".join(lines)

    def _format_edits_for_reviewer(self) -> str:
        """将 Writer 的 edits 格式化为复审 Scholar 可理解的上下文。"""
        edits = self.harness.state.edits
        if not edits:
            return "(Writer 未做任何修改)"

        lines = []
        for i, e in enumerate(edits, 1):
            section = e.get("section", "未知")
            description = e.get("description", "")
            line = f"{i}. [{section}] {description}"
            lines.append(line)

        return "\n".join(lines)

    def _collect_stats(self) -> dict:
        """收集三阶段的运行统计。"""
        return {
            "total_tokens": self.harness.state.total_tokens,
            "total_loop_turns": self.harness.state.loop_turns,
            "conversation_turns": self.harness.state.conversation_turns,
            "findings_count": len(self.harness.state.findings),
            "edits_count": len(self.harness.state.edits),
            "phases": [
                {"persona": p["persona"], "phase": p["phase"], "output_length": len(p["output"])}
                for p in self.phases
            ],
        }


# ============================================================
# 交互式 CLI — 方便测试多轮对话
# ============================================================

async def interactive_main():
    """交互式多轮对话入口。"""
    import argparse

    parser = argparse.ArgumentParser(description="ScholarAgent 交互式审稿")
    parser.add_argument("paper", help="论文文件路径（markdown）")
    parser.add_argument("--model", default=None, help="LLM 模型名称")
    parser.add_argument("--quiet", action="store_true", help="减少过程输出")
    args = parser.parse_args()

    if not Path(args.paper).exists():
        print(f"文件不存在: {args.paper}", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("  ScholarAgent — 认知驱动的学术审稿助手")
    print("=" * 60)
    print(f"  论文: {args.paper}")
    print(f"  模型: {args.model or os.environ.get('LLM_MODEL', 'gpt-4.1')}")
    print("  输入 'quit' 退出，'stats' 查看统计，'findings' 查看发现")
    print("=" * 60)

    agent = ScholarAgent(
        paper_path=args.paper,
        model=args.model,
        verbose=not args.quiet,
    )

    # 启动 — Agent 自主审阅
    print("\n[Agent 正在审阅论文...]\n")
    response = await agent.start()
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
        if user_input.lower() == "quit":
            break
        if user_input.lower() == "stats":
            import json
            print(json.dumps(agent.get_stats(), indent=2, ensure_ascii=False))
            continue
        if user_input.lower() == "findings":
            for i, f in enumerate(agent.get_findings(), 1):
                print(f"  [{f['priority']}][{f['status']}] {f['finding']}")
            continue

        print("\n[Agent 正在思考...]\n")
        response = await agent.chat(user_input)
        print(f"\n{'─' * 40}")
        print(f"Agent: {response}")
        print(f"{'─' * 40}")

    # Phase 15: 会话结束时沉淀记忆
    agent.end_session()
    print("\n[记忆已保存]")

    # 结束统计
    print("\n[会话统计]")
    import json
    print(json.dumps(agent.get_stats(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(interactive_main())
