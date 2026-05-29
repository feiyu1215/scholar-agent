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
from core.stream_events import OnStreamCallback


# ============================================================
# Phase 5: HD-WM 工具 JSON schema（LLM 可见的工具定义）
# 仅在 enable_hdwm=True 时追加到 tools 列表
# ============================================================
_HDWM_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "generate_hypothesis",
        "description": "提出一个关于论文的可检验假说。当你对论文的某个方面（方法论、结果、声明）有一个需要验证的猜想时使用。好的假说应该是具体的、可证伪的——你能通过阅读论文的其他部分或搜索文献来支持或反驳它。例如：'作者的 DID 估计可能因为平行趋势假设不成立而有偏'、'Table 3 的显著性可能来自多重检验而非真实效应'。",
"input_schema": {
"type": "object",
"properties": {
"statement": {
"type": "string",
"description": "假说陈述——你的具体猜想是什么？应该是可检验的。"
},
"source": {
"type": "string",
"description": "假说的来源——来自论文的哪个 section 或哪个观察让你提出此假说？"
}
},
"required": ["statement"]
}
},
    {
        "name": "add_evidence",
        "description": "为已有假说添加证据——支持或反对。当你通过阅读论文内容或搜索文献找到了与某个假说相关的证据时使用。每条证据都有方向（for/against）和强度。",
        "input_schema": {
            "type": "object",
            "properties": {
                "hyp_id": {
                    "type": "string",
                    "description": "假说 ID（如 'H1', 'H2'）"
                },
                "content": {
                    "type": "string",
                    "description": "证据内容——你发现了什么？"
                },
                "direction": {
                    "type": "string",
                    "enum": ["for", "against"],
                    "description": "这条证据支持(for)还是反驳(against)该假说？"
                },
                "strength": {
                    "type": "number",
                    "description": "证据强度 0.0-1.0（0.5=中等，1.0=决定性证据）"
                },
                "source": {
                    "type": "string",
                    "description": "证据来源——来自论文的哪个 section？"
                },
                "type": {
                    "type": "string",
                    "enum": ["direct", "indirect", "counter"],
                    "description": "证据类型：direct(直接证据)、indirect(间接证据)、counter(反证)"
                }
            },
            "required": ["hyp_id", "content", "direction"]
        }
    },
    {
        "name": "resolve_hypothesis",
        "description": "解决一个假说——标记为已支持、已反驳或暂时搁置。当你收集了足够的证据来做出判断时使用。",
        "input_schema": {
            "type": "object",
            "properties": {
                "hyp_id": {
                    "type": "string",
                    "description": "假说 ID（如 'H1', 'H2'）"
                },
                "status": {
                    "type": "string",
                    "enum": ["supported", "refuted", "suspended"],
                    "description": "解决状态: supported=证据支持该假说, refuted=证据反驳该假说, suspended=暂时搁置（证据不足）"
                },
                "reason": {
                    "type": "string",
                    "description": "为什么你做出这个判断？"
                }
            },
            "required": ["hyp_id", "status"]
        }
    },
]


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
        enable_hdwm: bool = False,
        on_stream: OnStreamCallback = None,
        budget_policy: "BudgetPolicy | None" = None,
        session_model_mgr=None,
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
            enable_hdwm: Phase 5: 是否激活 Hypothesis-Driven Working Memory (D 模块)。
                        激活后 Agent 获得 generate_hypothesis/add_evidence/resolve_hypothesis 工具，
                        并在 context 中看到假说状态。关闭时零副作用退化为标准 C 方案。
            on_stream: V5 方案 B 流式回调。传入后在 GODEL_STREAMING_ENABLED=1 时
                      启用实时 StreamEvent 推送。不传时行为完全不变。
        """
        self.paper_path = paper_path
        self.verbose = verbose
        self.persona_name = persona
        self.on_stream = on_stream
        self._session_model_mgr = session_model_mgr

        # ---- BudgetPolicy 初始化（向后兼容） ----
        from core.budget_policy import BudgetPolicy
        if budget_policy is None:
            budget_policy = BudgetPolicy(token_limit=token_budget)
        self._budget_policy = budget_policy

        # 同步 token_budget：确保 state.token_budget 和 budget_policy.token_limit 语义一致
        # 核心规则：当 budget_policy 为 unlimited 时，state.token_budget 必须为 0
        # 这样 loop.py 的 spawn guard（检查 state.token_budget > 0）才能正确跳过 budget 检查
        if budget_policy.is_unlimited:
            effective_token_budget = 0
        else:
            effective_token_budget = budget_policy.token_limit

        # 根据 persona 获取 identity 和 tools
        identity, tools = get_persona(persona)

        # 初始化组件
        self.client = LLMClient(model=model)
        self.harness = Harness(
            paper_path=paper_path,
            max_loop_turns=max_loop_turns,
            token_budget=effective_token_budget,
            context_window=context_window,
            persona=persona,  # Phase 55: 传递 persona 给 Harness/Checker
            reference_paths=reference_paths,  # Phase 58: 用户参考文献
            enable_hdwm=enable_hdwm,  # Phase 5: HD-WM 可插拔开关
            budget_policy=budget_policy,  # 传递 BudgetPolicy 给 Harness
            session_model_mgr=session_model_mgr,  # Phase 2: 多模型管理器
        )

        # MCL: 注入 MetaCognitionLayer（可通过环境变量禁用）
        if os.environ.get("MCL_ENABLED", "1") != "0":
            from core.meta_cognition_layer import MetaCognitionLayer
            self.harness.mcl = MetaCognitionLayer(
                llm_client=self.client,
                session_model_mgr=session_model_mgr,
            )

        self.tools = tools
        # Phase 5: HD-WM 启用时追加假说工具的 JSON schema 到 LLM 可见工具列表
        if enable_hdwm:
            self.tools = list(tools) + _HDWM_TOOL_SCHEMAS

        # V4 D1: 追加操作型 Skill 动态注册的 tool schemas
        action_schemas = self.harness.get_action_tool_schemas()
        if action_schemas:
            self.tools = list(self.tools) + action_schemas

        # Phase 3: 追加 MCP 服务暴露的 tool schemas
        mcp_schemas = self.harness.get_mcp_tool_schemas()
        if mcp_schemas:
            self.tools = list(self.tools) + mcp_schemas

        self.identity = identity

        # Phase 53: 支持直接传入内容分段（代码审阅场景）
        if content_sections:
            self.harness.state.paper_sections = dict(content_sections)
            self.harness._paper_loaded = True

        # ============================================================
        # 双注册一致性检查: 确保 tool schema ↔ handler 同步
        # 历史教训: apply_skill/request_phase_transition/generate_cognitive_hints
        # 曾因缺少 schema 导致 LLM 永远不会调用它们 (G005 bug)
        # ============================================================
        from core.tool_consistency import check_tool_consistency
        check_tool_consistency(
            tool_schemas=self.tools,
            tool_registry=self.harness.tool_registry,
            strict=True,  # 启动时发现不一致立即失败，强制开发者修复
        )

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

        # S1-LLM: 前置 LLM 调用——让 LLM 基于论文摘要+方法论checklist 深度加工审稿策略
        # 设计原则: depth of processing effect — "写一段审稿计划"比"被动看到 checklist"更有效
        # 即使多花一次 LLM 调用，前置的认知做工能显著提升后续 Recall
        async def _llm_call_for_hints(system: str, user: str, max_tokens: int) -> str:
            return await self.client.chat(system, user, max_tokens=max_tokens)

        await self.harness.pre_generate_cognitive_hints(_llm_call_for_hints)

        # 构建初始 system prompt
        # Phase 3.3/3.4: assembler 统一输出 identity + habits + workspace state
        system_prompt = self.harness.format_context(include_identity=True)

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
            on_stream=self.on_stream,
            session_model_mgr=self._session_model_mgr,
        )

        # Deep Verification Pass: 执行 Layer 2-5 heuristic Skills（表格一致性 + 数学审查）
        # 设计决策: 结果存入 state.deep_verify_hints，由 consolidation LLM 审核后决定是否采纳
        # 遵循"提醒 + LLM 决策"模式，heuristic 不直接产出 findings
        if isinstance(result, (LoopDone, LoopDoomStop, LoopTalk)):
            await self._run_deep_verification_pass()

        # Consolidation Pass: 语义去重合并（任何退出路径都触发）
        # 修复: LoopTalk 退出时也需要 consolidation，因为 agent 可能在 talk_to_user
        # 之前已经积累了大量 findings（如 Paper_001: 17 条未去重）
        if isinstance(result, (LoopDone, LoopDoomStop, LoopTalk)):
            await self._run_consolidation_pass()

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

        # P2-fix: 检测用户纠正信号并记录到 state
        self._detect_user_correction(user_message)

        # 重置 Harness 的循环计数器（新一轮用户消息）
        self.harness.new_conversation_turn()

        # 更新 system prompt (workspace state 可能已变)
        # Phase 3.3/3.4: assembler 统一输出 identity + habits + workspace state
        system_prompt = self.harness.format_context(include_identity=True)
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
            on_stream=self.on_stream,
            session_model_mgr=self._session_model_mgr,
        )

        return self._handle_result(result)

    def _handle_result(self, result) -> str:
        """统一处理 Loop 结果。"""
        if isinstance(result, LoopTalk):
            return result.message or result.content
        elif isinstance(result, LoopDone):
            # 优先返回 content（Agent 的实际文本输出），因为 summary 可能是空的
            content = result.content.strip() or result.summary or "(Agent 完成但未产生文本输出)"
            report = self._format_progress_report()
            return f"{content}\n\n{report}"
        elif isinstance(result, LoopDoomStop):
            # Budget 截断时自动保存快照 + 沉淀记忆
            if self._budget_policy.allow_pause and "budget" in result.reason.lower():
                self._save_budget_checkpoint(result.reason)
            report = self._format_progress_report()
            return f"[系统中断] {result.reason}\n\n{report}\n\n到目前为止的输出:\n{result.content}"
        else:
            return str(result)

    # ============================================================
    # Consolidation Pass (Phase: post-loop semantic dedup)
    # ============================================================

    async def _run_consolidation_pass(self) -> None:
        """
        认知循环完成后，对 findings 做 LLM-based 语义合并。

        同时审核 deep_verify_hints（heuristic 规则引擎的检测结果），
        由 LLM 判断哪些 hints 是真正的问题后才纳入 findings。

        设计决策：
        - findings < 6 条且无 hints 时跳过（数量少无需去重，省成本）
        - 使用 MEDIUM tier 模型（结构化任务，不需深度推理）
        - 失败时 graceful fallback（返回原始 findings，永不 crash）
        - 保留 _raw_findings_pre_consolidation 方便回溯
        """
        from core.consolidation import consolidate_findings
        from llm.router import get_model_for_task

        raw_findings = self.harness.state.findings
        # 防御式访问: WorkspaceState 在旧版本中可能没有 deep_verify_hints 字段，
        # 使用 getattr 确保向后兼容（已有 session 的 state pickle 不会崩）
        deep_verify_hints = getattr(self.harness.state, "deep_verify_hints", [])

        # 如果 findings 少且没有 hints，跳过
        if len(raw_findings) < 6 and not deep_verify_hints:
            return

        # 构建论文上下文（摘要 + section 列表）
        paper_context = self._build_paper_context_for_consolidation()

        # Phase 4: 优先从 session_model_mgr 获取 consolidation 模型
        if self._session_model_mgr is not None:
            model = self._session_model_mgr.resolve_model_for_role("consolidation")
        else:
            model = get_model_for_task("consolidate")

        try:
            result = await consolidate_findings(
                raw_findings=raw_findings,
                paper_context=paper_context,
                client=self.client,
                model=model,
                deep_verify_hints=deep_verify_hints,
                session_model_mgr=self._session_model_mgr,
            )

            # 保留原始版本备查
            self.harness.state._raw_findings_pre_consolidation = raw_findings.copy()

            # 替换为合并后的版本
            self.harness.state.findings = result.findings

            # 清空已审核的 hints
            self.harness.state.deep_verify_hints = []

            if self.verbose:
                print(
                    f"[Consolidation] {result.raw_count} findings → "
                    f"{result.consolidated_count} unique findings",
                    file=sys.stderr,
                )
                if deep_verify_hints:
                    adopted = result.hints_adopted if hasattr(result, "hints_adopted") else "?"
                    print(
                        f"[Consolidation] {len(deep_verify_hints)} heuristic hints 审核 → "
                        f"{adopted} 条被 LLM 采纳",
                        file=sys.stderr,
                    )
        except Exception as e:
            # 永不因 consolidation 失败导致整体崩溃
            print(
                f"[Consolidation] 异常，返回原始 findings: {type(e).__name__}: {e}",
                file=sys.stderr,
            )
            # P1-2 fix: 异常时也必须清空 hints，否则下次 consolidation 会重复审核同一批 hints
            self.harness.state.deep_verify_hints = []

    # ============================================================
    # Deep Verification Pass (Layer 2-5 自动触发)
    # ============================================================

    # 与 SkillSelector 默认阈值保持一致（selector.py score_threshold=0.3）。
    # Deep Verify 是零 LLM 成本的 heuristic 扫描，阈值设为 0.3 比 LLM 主动
    # 调用时更宽松——多跑几个 Skill 没有额外代价。
    _DEEP_VERIFY_APPLICABILITY_THRESHOLD: float = 0.3

    async def _run_deep_verification_pass(self) -> None:
        """
        认知循环完成后，执行 Layer 2-5 深度验证 Skills 并将结果作为
        **待 LLM 审核的提示** 存入 state.deep_verify_hints。

        设计原则 (重构自 G005 修复):
        - Heuristic 是规则引擎，能发现数据不一致，但也可能误报
        - 因此 heuristic 结果 **不直接** 加入 findings
        - 而是在 consolidation 阶段由 LLM 审核后决定是否采纳
        - 这遵循"提醒 + LLM 决策"模式，而非"自动执行 + 直接输出"

        设计决策:
        - Kill Switch: SCHOLAR_GODEL_DEEP_VERIFY (默认开)
        - 结果存入 state.deep_verify_hints，由 consolidation LLM 审核
        - 失败时 graceful fallback（永不 crash）
        - 仅在 paper_sections 非空时执行（需要论文文本）
        - 适用度阈值: _DEEP_VERIFY_APPLICABILITY_THRESHOLD (0.3)
        """
        # Kill Switch
        if os.environ.get("SCHOLAR_GODEL_DEEP_VERIFY", "1").strip().lower() not in ("1", "true", "yes"):
            return

        state = self.harness.state

        # 需要论文文本才能执行
        if not state.paper_sections:
            return

        # 拼接全文（用于 Skills 分析）
        full_text = "\n\n".join(
            f"[{name}]\n{content}"
            for name, content in state.paper_sections.items()
        )

        if len(full_text) < 500:
            return  # 论文太短，无需深度验证

        hints: list[dict] = []

        # --- Layer 2: AppendixMathAuditSkill (公式符号追踪) ---
        try:
            from core.skills.economics.math_audit import AppendixMathAuditSkill
            from core.skills.base import SkillContext

            math_skill = AppendixMathAuditSkill()
            context = SkillContext(
                paper_text=full_text,
                current_phase="deep_review",
                existing_findings=state.findings,
                parameters={},
            )

            # 只在 can_apply 评分足够高时执行（论文有数学内容）
            applicability = math_skill.can_apply(context)
            if applicability >= self._DEEP_VERIFY_APPLICABILITY_THRESHOLD:
                result = math_skill.execute(context)
                if result.success and result.findings:
                    for f in result.findings:
                        hints.append({
                            "finding": f.description,
                            "severity": f.severity,
                            "category": f.category,
                            "location": f.location,
                            "confidence": f.confidence,
                            "source": "heuristic_math_audit",
                            "status": "needs_llm_verification",
                        })
                    if self.verbose:
                        print(
                            f"[DeepVerify] AppendixMathAudit: {len(result.findings)} hints "
                            f"(applicability={applicability:.2f})",
                            file=sys.stderr,
                        )
        except Exception as e:
            if self.verbose:
                print(f"[DeepVerify] AppendixMathAudit failed: {e}", file=sys.stderr)

        # --- Layer 3-4: TableConsistencySkill (表格一致性 + 跨表对比) ---
        # G005 fix: 先运行 TableExtractionSkill（含 PDF 提取），再将 EconTable 对象
        # 传给 TableConsistencySkill，确保 PDF 表格能被一致性检查覆盖。
        try:
            from core.skills.multimodal.skills import (
                TableExtractionSkill,
                TableConsistencySkill,
            )
            from core.skills.base import SkillContext

            # Step 1: 提取表格（含 PDF 路径以触发 PDFTableExtractor）
            paper_path = getattr(self, "paper_path", "") or ""
            extraction_context = SkillContext(
                paper_text=full_text,
                current_phase="deep_review",
                existing_findings=state.findings,
                parameters={},
            )
            extraction_context.paper_metadata = {"paper_path": paper_path}

            extraction_skill = TableExtractionSkill()
            ext_applicability = extraction_skill.can_apply(extraction_context)

            econ_tables = []  # EconTable objects for downstream
            if ext_applicability >= self._DEEP_VERIFY_APPLICABILITY_THRESHOLD:
                ext_result = extraction_skill.execute(extraction_context)
                if ext_result.success:
                    # 获取实际的 EconTable 对象（非序列化 dict）
                    # TableExtractionSkill 内部已经 parse_all，我们重新解析以获取对象
                    from core.skills.multimodal.table_parser import TextTableParser
                    from core.skills.multimodal.pdf_table_extractor import PDFTableExtractor
                    from core.skills.multimodal.econ_table import EconTableParser
                    from pathlib import Path

                    raw_tables = []
                    text_parser = TextTableParser()
                    raw_tables.extend(text_parser.extract_all(full_text))
                    if paper_path and Path(paper_path).exists() and paper_path.endswith(".pdf"):
                        pdf_extractor = PDFTableExtractor()
                        pdf_tables = pdf_extractor.extract(paper_path)
                        # 去重: 避免 text 版和 PDF 版同一张表同时存在，
                        # 膨胀 pairwise 比较空间导致误报
                        existing_ids = {t.table_id for t in raw_tables if hasattr(t, 'table_id')}
                        for pt in pdf_tables:
                            pt_id = getattr(pt, 'table_id', None)
                            if pt_id and pt_id in existing_ids:
                                continue  # 跳过已存在的表
                            raw_tables.append(pt)
                    if raw_tables:
                        econ_parser = EconTableParser()
                        econ_tables = econ_parser.parse_all(raw_tables)

                    if self.verbose:
                        print(
                            f"[DeepVerify] TableExtraction: {len(econ_tables)} econ tables "
                            f"(applicability={ext_applicability:.2f})",
                            file=sys.stderr,
                        )

            # Step 2: 一致性验证（传入 EconTable 对象）
            table_skill = TableConsistencySkill()
            context = SkillContext(
                paper_text=full_text,
                current_phase="deep_review",
                existing_findings=state.findings,
                parameters={"econ_tables": econ_tables} if econ_tables else {},
            )

            applicability = table_skill.can_apply(context)
            if applicability >= self._DEEP_VERIFY_APPLICABILITY_THRESHOLD:
                result = table_skill.execute(context)
                if result.success and result.findings:
                    for f in result.findings:
                        hints.append({
                            "finding": f.description,
                            "severity": f.severity,
                            "category": f.category,
                            "location": f.location,
                            "confidence": f.confidence,
                            "source": "heuristic_table_consistency",
                            "status": "needs_llm_verification",
                        })
                    if self.verbose:
                        print(
                            f"[DeepVerify] TableConsistency: {len(result.findings)} hints "
                            f"(applicability={applicability:.2f})",
                            file=sys.stderr,
                        )
        except Exception as e:
            if self.verbose:
                print(f"[DeepVerify] TableConsistency failed: {e}", file=sys.stderr)

        # --- 存入 state.deep_verify_hints（不直接加入 findings）---
        # consolidation pass 会读取这些 hints 并让 LLM 判断是否采纳
        if hints:
            # P3-1 fix: 用 extend 而非覆盖，防止多次 deep_verify 调用时丢失早期结果
            existing = getattr(state, "deep_verify_hints", [])  # 防御: state 可能尚未初始化该字段
            existing.extend(hints)
            state.deep_verify_hints = existing
            if self.verbose:
                print(
                    f"[DeepVerify] {len(hints)} hints 待 LLM 审核 "
                    f"(累计 {len(state.deep_verify_hints)} 条，不直接加入 findings)",
                    file=sys.stderr,
                )

    def _build_paper_context_for_consolidation(self) -> str:
        """构建给 consolidation LLM 的论文上下文（摘要 + section 列表）。"""
        parts = []
        state = self.harness.state

        # 摘要（如果有）
        abstract = state.paper_sections.get("abstract", "")
        if not abstract:
            # 尝试其他常见 key
            abstract = state.paper_sections.get("Abstract", "")
        if abstract:
            parts.append(f"摘要：{abstract[:500]}")

        # Section 列表
        sections = list(state.paper_sections.keys())
        if sections:
            parts.append(f"论文章节：{', '.join(sections)}")

        return "\n".join(parts) if parts else "（无论文上下文）"

    def _format_progress_report(self) -> str:
        """格式化 post-hoc 进度报告（给用户看）。"""
        state = self.harness.state
        return "[消耗统计] " + self._budget_policy.format_report(
            total_tokens_used=state.total_tokens,
            findings_count=len(state.findings),
            sections_read=len(state.sections_read),
            total_sections=len(state.paper_sections),
            loop_turns=state.loop_turns,
        )

    def _save_budget_checkpoint(self, stop_reason: str) -> None:
        """Budget 截断时自动保存完整快照 + 沉淀记忆。"""
        from core.budget_policy import serialize_budget_policy
        from core.state_checkpoint import CheckpointManager
        from pathlib import Path

        try:
            # 确定 checkpoint 存储目录（基于论文路径或临时目录）
            if self.paper_path:
                ckpt_dir = Path(self.paper_path).parent / ".scholar_checkpoints"
            else:
                import tempfile
                ckpt_dir = Path(tempfile.gettempdir()) / "scholar_agent_checkpoints"
            ckpt_dir.mkdir(parents=True, exist_ok=True)

            # CheckpointManager 内部自动创建 StateSerializer，无需外部传入
            ckpt_mgr = CheckpointManager(workdir=ckpt_dir)

            phase_fsm = self.harness.phase_fsm
            ckpt_mgr.save_full_snapshot(
                state=self.harness.state,
                messages=self.messages,
                phase=phase_fsm.phase_name,
                # Phase.history 返回 list[Phase]，需转为 list[str] 以便 JSON 序列化
                phase_history=[p.value for p in phase_fsm.history],
                transition_count=phase_fsm.transition_count,
                budget_policy_data=serialize_budget_policy(self._budget_policy),
                stop_reason=stop_reason,
                paper_path=self.paper_path or "",
                model=self.client.model,
                persona=self.persona_name,
            )

            # 沉淀记忆（Step 9: Session Persistence）
            self.end_session()

            if self.verbose:
                import sys
                print(f"[Budget] 已保存断点快照和 session 记忆", file=sys.stderr)

        except Exception as e:
            if self.verbose:
                import sys
                print(f"[Budget] 保存快照失败: {e}", file=sys.stderr)

    @classmethod
    async def resume(
        cls,
        checkpoint_path: str,
        new_token_limit: int | None = None,
        model: str | None = None,
        verbose: bool = True,
        on_stream: "OnStreamCallback" = None,
    ) -> str:
        """从断点快照恢复，继续运行。

        Args:
            checkpoint_path: checkpoint 文件路径或包含 checkpoints 的目录
            new_token_limit: 新的 token 上限（追加预算）。不传则保持原 limit。
            model: 覆盖使用的模型（不传则用原 checkpoint 中记录的模型）
            verbose: 日志输出
            on_stream: 流式回调

        Returns:
            Agent 恢复后继续运行的输出（直到下次停止或完成）
        """
        from pathlib import Path
        from core.state_checkpoint import CheckpointManager, StateSerializer, FullSnapshot
        from core.budget_policy import BudgetPolicy, deserialize_budget_policy
        from core.state import WorkspaceState
        from core.phases import Phase, PhaseState

        path = Path(checkpoint_path)
        if path.is_file():
            workdir = path.parent
            # 从文件名提取 snapshot_id（去掉 .json.gz 或 .json 后缀）
            name = path.name
            if name.endswith(".json.gz"):
                snapshot_id = name[:-len(".json.gz")]
            elif name.endswith(".json"):
                snapshot_id = name[:-len(".json")]
            else:
                snapshot_id = name
        else:
            workdir = path
            snapshot_id = None

        # 加载快照（CheckpointManager 内部自动创建 StateSerializer）
        ckpt_mgr = CheckpointManager(workdir=workdir)
        snapshot = ckpt_mgr.restore_full_snapshot(checkpoint_id=snapshot_id)

        # 恢复 BudgetPolicy
        budget_policy = deserialize_budget_policy(snapshot.budget_policy)
        if new_token_limit is not None:
            budget_policy.token_limit = new_token_limit

        # 使用快照中记录的模型（可覆盖）
        use_model = model or snapshot.model or None

        # 构造 Agent（正常构造流程：加载论文等）
        # 同步 token_budget 给 Harness，避免 state.token_budget 和 budget_policy.token_limit 不一致
        # 注意：当 budget_policy 同时传入时，__init__ 内部以 budget_policy 为准，
        # token_budget 仅作向后兼容 fallback。这里显式传 0 保持语义一致。
        agent = cls(
            paper_path=snapshot.paper_path or None,
            model=use_model,
            verbose=verbose,
            token_budget=0 if budget_policy.is_unlimited else budget_policy.token_limit,
            budget_policy=budget_policy,
            persona=snapshot.persona,
            on_stream=on_stream,
        )

        # 恢复 WorkspaceState（使用 StateSerializer 正确反序列化）
        serializer = StateSerializer()
        restored_state = serializer.deserialize(snapshot.state, WorkspaceState)
        # 逐字段覆盖（保留 load_paper 的副作用产出，只覆盖 checkpoint 中有的字段）
        from dataclasses import fields as dc_fields
        for f in dc_fields(agent.harness.state):
            if f.name in snapshot.state:
                restored_val = getattr(restored_state, f.name, None)
                if restored_val is not None or snapshot.state.get(f.name) is None:
                    setattr(agent.harness.state, f.name, restored_val)

        # 【P1 修复】恢复后强制同步 token_budget 与当前 budget_policy
        # 原因：snapshot.state 中的 token_budget 可能是旧值（如 100K），
        # 但 resume 时用户可能传了 new_token_limit=0（unlimited）。
        # 如果不同步，spawn guard（检查 state.token_budget > 0）会被误触发。
        effective_budget = 0 if budget_policy.is_unlimited else budget_policy.token_limit
        agent.harness.state.token_budget = effective_budget

        # 恢复 Phase FSM（直接设置内部状态，避免 force_transition 额外记录一次转换）
        if snapshot.phase:
            target_phase = Phase(snapshot.phase)  # phase 存的是 value string
            fsm = agent.harness.phase_fsm
            fsm._state.current = target_phase
            # 恢复 history 和 transition_count
            fsm._state.history = [Phase(p) for p in snapshot.phase_history]
            fsm._state.transition_count = snapshot.transition_count

        # 恢复 messages
        agent.messages = snapshot.messages
        agent._started = True

        if verbose:
            import sys
            print(
                f"[Resume] 从快照恢复: phase={snapshot.phase}, "
                f"tokens={agent.harness.state.total_tokens:,}, "
                f"findings={len(agent.harness.state.findings)}, "
                f"messages={len(agent.messages)}",
                file=sys.stderr,
            )

        # 驱动继续运行
        result = await cognitive_loop(
            messages=agent.messages,
            harness=agent.harness,
            tools=agent.tools,
            client=agent.client,
            verbose=agent.verbose,
            on_stream=agent.on_stream,
            session_model_mgr=agent._session_model_mgr,
        )

        return agent._handle_result(result)

    def get_findings(self) -> list[dict]:
        """获取当前所有发现（已过滤确认性/正面条目）。"""
        return [f for f in self.harness.state.findings if not self._is_confirmatory(f)]

    @staticmethod
    def _is_confirmatory(finding: dict) -> bool:
        """
        判断一条 finding 是否为确认性/正面发现（不构成批评）。

        这些条目不应出现在审稿意见中：
        - "数据一致性确认"、"符号定义一致"、"参数一致"等
        - 以"确认"、"一致"、"正确"、"合理"等正面词开头的描述
        - 明确标记为 status=confirmed 的条目

        设计原则：规则兜底，补偿 consolidation LLM 可能遗漏的过滤。
        """
        text = (finding.get("finding") or finding.get("description") or "").strip()
        status = (finding.get("status") or "").lower()

        # status 明确标记为"无问题"（注意：verified 表示"已验证为真实问题"，不是确认性）
        if status in ("confirmed_no_issue", "no_issue", "not_a_problem"):
            return True

        # 正面/确认性关键词模式（中文）
        _CONFIRMATORY_PREFIXES = (
            "确认", "验证通过", "一致性确认", "数据一致",
            "符号定义一致", "参数一致", "结果一致",
        )
        _CONFIRMATORY_KEYWORDS = (
            "一致性确认", "数据一致性确认", "符号定义一致",
            "参数一致", "无问题", "符合预期", "验证通过",
            "确认无误", "正确无误",
        )

        # 正面评价模式：finding 描述的是"做得好"而非"有问题"
        # 这些 finding 的特征是：以方括号标签开头，内容描述正面结论
        _POSITIVE_INDICATORS = (
            "连贯性]", "契合度]", "一致性]",
        )
        # 如果标签暗示正面评价，且内容中无负面词，则判定为确认性
        if any(ind in text[:30] for ind in _POSITIVE_INDICATORS):
            _NEGATIVE_WORDS = ("不足", "缺", "问题", "局限", "偏", "误", "错", "未", "缺失", "不一致")
            if not any(neg in text for neg in _NEGATIVE_WORDS):
                return True

        text_lower = text.lower()
        if any(text_lower.startswith(p) for p in _CONFIRMATORY_PREFIXES):
            return True
        if any(kw in text_lower for kw in _CONFIRMATORY_KEYWORDS):
            return True

        return False

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

    async def end_session_with_reflection(self) -> dict:
        """
        P2: 带 Agent 自省的 session 结束。

        Agent 回顾本次会话行为，自己决定学到了什么可复用的经验。
        用户可选：如果不想增加一次 LLM 开销，用 end_session() 即可。

        Returns:
            反思统计 {"reflections_count": int, "stored_count": int}
        """
        user_messages = [
            m["content"] for m in self.messages
            if m.get("role") == "user" and m.get("content")
        ]

        # Phase 4: 构造 LLM call 函数 — 根据 model_assignments.reflection 选择模型
        reflection_client = self.client
        if self._session_model_mgr is not None:
            reflection_model = self._session_model_mgr.resolve_model_for_role("reflection")
            if reflection_model and reflection_model != self.client.model:
                reflection_client = self.client.with_model_override(reflection_model)

        async def _llm_call(system: str, user: str, max_tokens: int) -> str:
            return await reflection_client.chat(system, user, max_tokens=max_tokens)

        return await self.harness.end_session_with_reflection(
            llm_call_fn=_llm_call,
            user_messages=user_messages,
        )

    # ----------------------------------------------------------
    # P2-fix: User Correction Detection
    # ----------------------------------------------------------

    # 纠正信号关键词（中英双语，覆盖常见否定表达）
    _CORRECTION_PATTERNS: list[str] = [
        # 中文
        "不对", "错了", "不是这样", "这个不对", "有误", "误报",
        "不准确", "你搞错", "不是", "应该是", "实际上",
        "这个finding错", "这条不对", "判断错误", "误判",
        # 英文
        "wrong", "incorrect", "not right", "false positive",
        "that's not", "actually", "you're wrong", "mistake",
    ]

    def _detect_user_correction(self, message: str) -> None:
        """
        检测用户消息中的纠正信号，记录到 state.user_corrections。

        纠正信号用于：
        1. end_session 时传给 SessionReflector 作为负反馈
        2. 生成 anti_pattern 类型的 ProceduralPattern
        3. 让 Agent 知道哪些审稿判断需要修正

        设计原则：
        - 宁可多检测（误报无害，只是让 reflector 多考虑一下）
        - 不做 NLP 解析（零依赖，用关键词匹配足够）
        """
        msg_lower = message.lower()

        # 检查是否包含纠正关键词
        is_correction = any(p in msg_lower for p in self._CORRECTION_PATTERNS)
        if not is_correction:
            return

        # 尝试关联到具体的 finding（如果消息中提到了数字）
        import re
        related_idx = None
        idx_match = re.search(r'(?:finding|发现|第)\s*(\d+)', msg_lower)
        if idx_match:
            related_idx = int(idx_match.group(1)) - 1  # 用户说的是 1-based

        correction_record = {
            "message": message[:200],  # 截断防止过长
            "turn": self.harness.state.conversation_turns,
            "related_finding_idx": related_idx,
        }
        self.harness.state.user_corrections.append(correction_record)


# ============================================================
# W1: UnifiedReviewAgent — 真正的单循环多人格 Agent
# ============================================================

class UnifiedReviewAgent:
    """
    W1: 统一认知循环中的多人格 Agent。

    核心设计 (来自 COGNITIVE_ANCHOR C1-C6):
    - 一个 cognitive_loop，LLM 自主决定何时/为何切换人格
    - switch_persona 工具让 LLM 主动切换（不是代码编排）
    - Messages 历史在切换后保持连续（Agent 记得之前的思考）
    - State（findings/edits）持续共享
    - 切换上限 5 次，超过 nudge（不 block）

    不在这里做的事（反模式）:
    - 不硬编码 Scholar→Writer→Scholar 序列
    - 不替 Agent 决定"何时切换"
    - 不清空切换后的 messages

    用法:
        agent = UnifiedReviewAgent(paper_path="paper.md")
        result = await agent.run()
        # result 包含完整输出和状态
    """

    def __init__(
        self,
        paper_path: str,
        model: str | None = None,
        verbose: bool = True,
        max_loop_turns: int = 60,
        token_budget: int = 300000,
        context_window: int = 128_000,
        initial_persona: str = "scholar",
        reference_paths: list[str] | None = None,
        on_stream: OnStreamCallback = None,
        budget_policy: "BudgetPolicy | None" = None,
    ):
        self.paper_path = paper_path
        self.model = model
        self.verbose = verbose
        self.max_loop_turns = max_loop_turns
        self.token_budget = token_budget
        self.context_window = context_window
        self.initial_persona = initial_persona
        self.on_stream = on_stream

        # BudgetPolicy 传播（与 ScholarAgent 对齐）
        from core.budget_policy import BudgetPolicy as _BP
        if budget_policy is None:
            budget_policy = _BP(token_limit=token_budget)
        self._budget_policy = budget_policy

        # unlimited 模式下 effective_token_budget=0，spawn guard 正确跳过
        effective_token_budget = 0 if budget_policy.is_unlimited else budget_policy.token_limit

        # Multi-model: UnifiedReviewAgent 暂不支持多模型，设为 None
        self._session_model_mgr = None

        self.harness = Harness(
            paper_path=paper_path,
            max_loop_turns=max_loop_turns,
            token_budget=effective_token_budget,
            context_window=context_window,
            persona=initial_persona,
            reference_paths=reference_paths,
            budget_policy=budget_policy,
        )

        # 设置初始 persona
        self.harness.state.current_persona = initial_persona

    async def run(self, user_intent: str | None = None) -> dict:
        """
        执行统一认知循环。Agent 自主决定审阅、修改、复审的流程。

        Args:
            user_intent: 用户意图（如果不给，使用默认审稿提示）

        Returns:
            dict: {
                "output": str,       # Agent 最终输出
                "findings": list,    # 所有 findings
                "edits": list,       # 所有 edits
                "stats": dict,       # 运行统计
            }
        """
        self.harness.load_paper()

        # 获取初始 persona 的 identity 和 tools
        identity, tools = get_persona(self.initial_persona)
        client = LLMClient(model=self.model)

        workspace_state = self.harness.format_context()
        system_prompt = build_system_prompt(
            identity=identity,
            workspace_state=workspace_state,
        )

        default_intent = (
            "请帮我审阅这篇论文。你拥有 switch_persona 工具，"
            "可以在审阅发现问题后切换到 writer 视角进行修改，"
            "修改完成后再切回 scholar 视角复审。"
            "全程由你自主决定何时切换、是否需要切换。"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_intent or default_intent},
        ]

        # 工具列表需要是可变引用（loop 中 __SWITCH__ 会 clear+extend）
        active_tools = list(tools)

        result = await cognitive_loop(
            messages=messages,
            harness=self.harness,
            tools=active_tools,
            client=client,
            verbose=self.verbose,
            on_stream=self.on_stream,
            session_model_mgr=self._session_model_mgr,
        )

        # 提取输出
        output = ""
        if isinstance(result, LoopTalk):
            output = result.message or result.content
        elif isinstance(result, LoopDone):
            output = result.content.strip() or result.summary or "(完成但无文本输出)"
        elif isinstance(result, LoopDoomStop):
            output = f"[系统中断] {result.reason}\n\n到目前为止:\n{result.content}"
        else:
            output = str(result)

        return {
            "output": output,
            "findings": self.harness.state.findings,
            "edits": self.harness.state.edits,
            "stats": self._collect_stats(),
        }

    def _collect_stats(self) -> dict:
        """收集运行统计。"""
        return {
            "total_tokens": self.harness.state.total_tokens,
            "total_loop_turns": self.harness.state.loop_turns,
            "conversation_turns": self.harness.state.conversation_turns,
            "findings_count": len(self.harness.state.findings),
            "edits_count": len(self.harness.state.edits),
            "persona_switches": self.harness.state.persona_switch_count,
            "final_persona": self.harness.state.current_persona,
        }


# ============================================================
# CollaborativeReview — 保留为快捷方式（syntactic sugar）
# 内部使用 UnifiedReviewAgent，API 向后兼容
# ============================================================

class CollaborativeReview:
    """
    Phase 51: 多人格协作审稿（向后兼容包装器）。

    W1 重构后，这不再是硬编码的三步 pipeline。
    而是调用 UnifiedReviewAgent + 暗示性 prompt，
    Agent 自主决定审阅→修改→复审的流程。

    返回的 dict 与旧 API 兼容:
        - review/revision/re_review 字段映射到 Agent 的统一输出
        - findings/edits/stats 与之前一致

    用法:
        collab = CollaborativeReview(paper_path="paper.md")
        result = await collab.run()
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
        on_stream: OnStreamCallback = None,
        budget_policy: "BudgetPolicy | None" = None,
    ):
        self.paper_path = paper_path
        self.model = model
        self.verbose = verbose
        self.max_loop_turns = max_loop_turns
        self.token_budget = token_budget
        self.context_window = context_window
        self.reference_paths = reference_paths
        self.on_stream = on_stream

        # BudgetPolicy 传播（与 ScholarAgent 对齐）
        from core.budget_policy import BudgetPolicy as _BP
        if budget_policy is None:
            budget_policy = _BP(token_limit=token_budget)
        self._budget_policy = budget_policy

        # 协作模式 3x 预算放大，但 unlimited 模式保持 0
        if budget_policy.is_unlimited:
            effective_token_budget = 0
            collab_policy = _BP(token_limit=0, allow_pause=budget_policy.allow_pause)
        else:
            effective_token_budget = budget_policy.token_limit * 3
            collab_policy = _BP(token_limit=effective_token_budget, allow_pause=budget_policy.allow_pause)

        # Multi-model: CollaborativeReview 暂不支持多模型，设为 None
        self._session_model_mgr = None

        # 向后兼容: 暴露 harness 供测试使用
        self.harness = Harness(
            paper_path=paper_path,
            max_loop_turns=max_loop_turns * 3,
            token_budget=effective_token_budget,
            context_window=context_window,
            reference_paths=reference_paths,
            budget_policy=collab_policy,
        )

        # 结果收集（向后兼容）
        self.phases: list[dict] = []

    async def run(self, user_intent: str | None = None) -> dict:
        """
        执行多人格协作审稿。

        内部使用统一认知循环 — Agent 自主决定何时/是否切换人格。

        Args:
            user_intent: 用户意图

        Returns:
            dict: 向后兼容格式
        """
        self.harness.load_paper()

        # 初始 persona: scholar
        identity, tools = get_persona("scholar")
        client = LLMClient(model=self.model)

        workspace_state = self.harness.format_context()
        system_prompt = build_system_prompt(
            identity=identity,
            workspace_state=workspace_state,
        )

        # 暗示性 prompt: 不是指令，是建议
        default_intent = (
            user_intent or "请帮我审阅这篇论文，找出所有值得关注的问题。"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": default_intent},
        ]

        active_tools = list(tools)

        # 驱动统一认知循环
        result = await cognitive_loop(
            messages=messages,
            harness=self.harness,
            tools=active_tools,
            client=client,
            verbose=self.verbose,
            on_stream=self.on_stream,
            session_model_mgr=self._session_model_mgr,
        )

        # 提取输出
        output = ""
        if isinstance(result, LoopTalk):
            output = result.message or result.content
        elif isinstance(result, LoopDone):
            output = result.content.strip() or result.summary or "(完成但无文本输出)"
        elif isinstance(result, LoopDoomStop):
            output = f"[系统中断] {result.reason}\n\n到目前为止:\n{result.content}"
        else:
            output = str(result)

        return {
            "review": output,
            "revision": output,
            "re_review": output,
            "findings": self.harness.state.findings,
            "edits": self.harness.state.edits,
            "stats": self._collect_stats(),
        }

    def _collect_stats(self) -> dict:
        """收集运行统计（向后兼容格式）。"""
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

    # Phase 15: 会话结束时沉淀记忆（带 Agent 自省）
    reflection_enabled = os.environ.get("SCHOLAR_REFLECTION", "1") == "1"
    if reflection_enabled:
        stats = await agent.end_session_with_reflection()
        print(f"\n[记忆已保存] Agent 反思产出 {stats['reflections_count']} 条经验，"
              f"存储 {stats['stored_count']} 条")
    else:
        agent.end_session()
        print("\n[记忆已保存]")

    # 结束统计
    print("\n[会话统计]")
    import json
    print(json.dumps(agent.get_stats(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(interactive_main())
