"""
core/harness.py — Harness: Agent 的状态守护层

设计原则 (来自 COGNITIVE_ANCHOR §5.2, §4.3):
    - Harness 不控制 Agent 做什么，只守护边界
    - Harness 替 LLM 记住一切（LLM 是无状态 CPU）
    - Harness 在每轮提供 context，执行 tool call，检查约束

职责:
    1. 状态持久化 — 论文内容、发现、修改历史、对话记忆
    2. 边界守护 — doom loop guard、completion quality gate、token budget
    3. 工具执行 — 接收 tool call，执行并返回结果
    4. Context 组装 — 每轮为 LLM 组装当前状态摘要

不做:
    - 不决定 Agent 下一步做什么
    - 不路由到不同 pipeline
    - 不维护 tool registry（Agent 的 tools 在 identity 里定义）
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

from core.memory import (
    MemoryStore,
    SessionRecord,
    build_session_record,
    extract_domain_patterns,
    extract_procedural_patterns,
)
from core.post_edit_verify import (
    verify_edit,
    format_verification_feedback,
    extract_voice,
    VoiceFingerprint,
)
from core.claim_signal import detect_verifiable_claims
from core.metacognition import CognitiveState
from core.offload import OffloadStore
from core.checker import CognitiveChecker


# ============================================================
# Workspace State — Agent 的外部记忆
# ============================================================

@dataclass
class WorkspaceState:
    """Agent 的完整工作状态。Harness 拥有并维护它，LLM 永远不直接访问它。"""

    # 论文内容
    paper_sections: dict[str, str] = field(default_factory=dict)
    paper_path: str | None = None

    # Agent 的工作记忆
    findings: list[dict] = field(default_factory=list)
    edits: list[dict] = field(default_factory=list)
    sections_read: list[str] = field(default_factory=list)  # Phase 14: 追踪已读 sections，防止重复读取
    section_digests: dict[str, str] = field(default_factory=dict)  # Phase 16: section 摘要缓存（压缩后仍可回溯）

    # Phase 57+58: 参考文献工作区 — 统一存储所有外部文献（无论来源）
    # key = paper_id 或 title_slug, value = {title, authors, year, venue, abstract, tldr, key_refs, ...}
    # source 字段区分来源: "user_provided" (用户提供) / "agent_fetched" (Agent 主动获取) / "api_detail" (API 详情)
    reference_papers: dict[str, dict] = field(default_factory=dict)

    # Phase 58: 用户提供的参考文献原文 — 可按需深入阅读的完整内容
    # key = reference_id (如 "ref_1", "ref_2"), value = {title, sections: {name: content}, source_path, ...}
    user_reference_docs: dict[str, dict] = field(default_factory=dict)

    # 对话历史（多轮支持的关键）
    conversation_turns: int = 0  # 用户发了几轮消息

    # 写作风格指纹（Phase 20: Post-Edit Verification）
    voice_profile: VoiceFingerprint | None = None  # 作者累计风格指纹，Agent 读 section 时自动构建

    # 认知行为追踪（Phase 17: Cognitive Output Prompter）
    consecutive_read_turns: int = 0  # 连续"只读不记"的轮次计数
    last_findings_count: int = 0     # 上一轮检查时的 findings 数量（用于判断是否产出了新发现）

    # 资源追踪
    loop_turns: int = 0  # 当前任务内的 LLM 调用轮次
    total_tokens: int = 0  # 累计 API 消耗（prompt + completion），用于成本报告
    last_prompt_tokens: int = 0  # Phase 45: 最近一次 API 调用的 prompt_tokens，用于认知带宽判断
    tool_call_counts: dict[str, int] = field(default_factory=dict)  # Phase 31: 工具使用频次
    tool_call_history: list[dict] = field(default_factory=list)  # Phase 34: 逐条工具调用记录（用于认知行为分析）

    # 配置
    max_loop_turns: int = 50  # 灾难保底上限（非认知约束，仅防无限循环）
    token_budget: int = 200_000  # 总 token 上限（累计消耗上限，用于成本控制）
    context_window: int = 128_000  # Phase 45: 模型 context window 大小（用于认知带宽管理）


# ============================================================
# Harness — 守护层
# ============================================================

class Harness:
    """
    Agent 的 Harness。职责：状态管理 + 边界守护 + 工具执行。

    使用方式:
        harness = Harness(paper_path="paper.md")
        # 每轮循环中:
        context = harness.format_context()  # 给 LLM 看的状态摘要
        result = harness.execute_tool(name, args)  # 执行 tool call
        verdict = harness.check_completion(findings)  # 检查是否允许完成
    """

    def __init__(self, paper_path: str | None = None, max_loop_turns: int = 50, token_budget: int = 200_000, context_window: int = 128_000, memory_dir: str | Path | None = None, persona: str = "scholar", reference_paths: list[str] | None = None):
        self.state = WorkspaceState(
            paper_path=paper_path,
            max_loop_turns=max_loop_turns,
            token_budget=token_budget,
            context_window=context_window,
        )
        self._paper_loaded = False
        self._persona = persona  # Phase 55: 当前 persona 标识
        if paper_path:
            self._load_paper(paper_path)
            self._paper_loaded = True

        # Phase 58: 加载用户提供的参考文献
        if reference_paths:
            self._load_user_references(reference_paths)

        # Phase 15: 跨会话记忆
        # memory_dir 默认为 paper_path 同级的 .memory/ 目录
        if memory_dir:
            self._memory_dir = Path(memory_dir)
        elif paper_path:
            p = Path(paper_path)
            self._memory_dir = (p.parent if p.is_file() else p) / ".memory"
        else:
            self._memory_dir = Path(".memory")
        self.memory = MemoryStore(self._memory_dir)
        self.memory.load()  # 渐进退化: 文件不存在时使用空状态
        self._paper_id: str | None = None
        if self._paper_loaded and self.state.paper_sections:
            self._paper_id = MemoryStore.compute_paper_id(self.state.paper_sections)

        # Phase 32: 元认知自我模型
        self.cognitive_state = CognitiveState()

        # Phase 54: 策略切换追踪（用于程序性记忆提取）
        self._strategy_transitions: list[tuple[str, str]] = []
        self._last_strategy: str = "undecided"

        # Phase 50/55: 认知校验层（小模型快速校验，支持 persona 适配）
        self.checker = CognitiveChecker(persona=persona)

        # Phase 32: 可恢复的上下文卸载
        workspace_root = Path(paper_path).parent if paper_path and Path(paper_path).is_file() else Path(paper_path or ".")
        refs_dir = workspace_root / ".workspace" / "refs"
        self.offload_store = OffloadStore(refs_dir=refs_dir)

    # ----------------------------------------------------------
    # 论文加载
    # ----------------------------------------------------------

    def load_paper(self, path: str | None = None):
        """公开接口: 加载论文。如已加载则跳过。"""
        if self._paper_loaded:
            return
        target = path or self.state.paper_path
        if target:
            self._load_paper(target)
            self._paper_loaded = True

    def _load_paper(self, path: str):
        """加载论文到 state。支持:
        - workspace 目录 (含 paper/section_index.json)
        - 单个 .md 文件
        - 单个 .pdf 文件
        """
        import re
        import json
        p = Path(path)

        if p.is_dir():
            # 优先使用 section_index.json — 包含 title 和文件路径
            index_path = p / "paper" / "section_index.json"
            if index_path.exists():
                index = json.loads(index_path.read_text(encoding="utf-8"))
                for entry in index:
                    title = entry.get("title", entry.get("slug", entry["id"]))
                    file_path = Path(entry["file"])
                    if file_path.exists():
                        # key 用小写 title，方便模糊匹配
                        self.state.paper_sections[title.lower()] = file_path.read_text(encoding="utf-8")
            else:
                # 退化: 直接扫描 sections 目录
                sections_dir = p / "paper" / "sections"
                if sections_dir.exists():
                    for f in sorted(sections_dir.glob("*.md")):
                        name = f.stem.split("_", 1)[-1] if "_" in f.stem else f.stem
                        self.state.paper_sections[name] = f.read_text(encoding="utf-8")

            # 全文（可选，长论文不一定需要）
            full_text_path = p / "paper" / "full_text.md"
            if full_text_path.exists():
                self.state.paper_sections["full"] = full_text_path.read_text(encoding="utf-8")

        elif p.suffix == ".pdf":
            from core.pdf_loader import load_pdf_as_sections
            self.state.paper_sections = load_pdf_as_sections(p)

        elif p.suffix == ".md":
            full_text = p.read_text(encoding="utf-8")
            self.state.paper_sections["full"] = full_text
            # 按 ## heading 拆分
            lines = full_text.split("\n")
            current_section = None
            current_content: list[str] = []

            for line in lines:
                match = re.match(r'^##\s+(.+)', line)
                if match:
                    if current_section:
                        self.state.paper_sections[current_section] = "\n".join(current_content).strip()
                    current_section = match.group(1).strip().lower().rstrip(".")
                    current_content = [line]
                elif current_section:
                    current_content.append(line)

            if current_section and current_content:
                self.state.paper_sections[current_section] = "\n".join(current_content).strip()

    # ----------------------------------------------------------
    # Phase 58: 用户参考文献加载
    # ----------------------------------------------------------

    def _load_user_references(self, paths: list[str]):
        """加载用户提供的参考文献。

        支持 PDF 和 Markdown 文件。加载后存入 user_reference_docs（完整内容）
        和 reference_papers（元数据摘要，source="user_provided"）。

        Agent 可以通过 read_reference 工具按需阅读具体内容。
        """
        for i, path_str in enumerate(paths, 1):
            p = Path(path_str)
            if not p.exists():
                continue

            ref_id = f"ref_{i}"
            title = p.stem.replace("_", " ").replace("-", " ")

            if p.suffix == ".pdf":
                try:
                    from core.pdf_loader import load_pdf_as_sections
                    sections = load_pdf_as_sections(p)
                    # 提取摘要（如果有 abstract section）
                    abstract = ""
                    for key in sections:
                        if "abstract" in key.lower():
                            abstract = sections[key][:500]
                            break
                    if not abstract:
                        # 取前 500 字符作为预览
                        first_section = next(iter(sections.values()), "")
                        abstract = first_section[:500]
                except Exception:
                    sections = {"full": f"[PDF 加载失败: {path_str}]"}
                    abstract = ""

            elif p.suffix == ".md":
                full_text = p.read_text(encoding="utf-8")
                sections = {"full": full_text}
                # 按 ## heading 拆分
                lines = full_text.split("\n")
                current_section = None
                current_content: list[str] = []
                for line in lines:
                    match = re.match(r'^##\s+(.+)', line)
                    if match:
                        if current_section:
                            sections[current_section] = "\n".join(current_content).strip()
                        current_section = match.group(1).strip().lower()
                        current_content = [line]
                    elif current_section:
                        current_content.append(line)
                if current_section and current_content:
                    sections[current_section] = "\n".join(current_content).strip()
                abstract = full_text[:500]
            else:
                # 尝试作为纯文本读取
                try:
                    text = p.read_text(encoding="utf-8")
                    sections = {"full": text}
                    abstract = text[:500]
                except Exception:
                    continue

            # 存入完整内容（供 read_reference 按需读取）
            self.state.user_reference_docs[ref_id] = {
                "title": title,
                "source_path": str(p),
                "sections": sections,
                "section_names": list(sections.keys()),
            }

            # 同时存入 reference_papers 元数据（供 format_context 展示）
            self.state.reference_papers[ref_id] = {
                "title": title,
                "authors": [],
                "year": None,
                "venue": None,
                "abstract": abstract[:200] if abstract else None,
                "tldr": None,
                "citation_count": None,
                "source": "user_provided",
                "source_path": str(p),
                "fetch_reason": "用户提供的参考文献",
                "section_count": len(sections),
                "total_chars": sum(len(v) for v in sections.values()),
            }

    def load_references(self, paths: list[str]):
        """公开接口: 运行时追加参考文献。"""
        self._load_user_references(paths)

    # ----------------------------------------------------------
    # Context 组装 — 给 LLM 看的状态摘要
    # ----------------------------------------------------------

    def format_context(self) -> str:
        """格式化当前状态，注入到 system prompt 的 {workspace_state} 占位符。
        
        Phase 18 重构：只提供客观事实（section 名 + 字符数），不做优先级分类。
        Agent 自己决定阅读策略——它的 identity prompt 已经包含了"战略性阅读"的能力定义。
        """
        parts = []
        s = self.state

        # 论文概况 — 纯事实，不分类
        if s.paper_sections:
            section_names = [k for k in s.paper_sections if k != "full"]
            total_chars = sum(len(v) for k, v in s.paper_sections.items() if k != "full")
            parts.append(f"论文已加载 | {len(section_names)} 个 sections | 总计 ~{total_chars} 字符")

            # Phase 18: 平铺展示所有 section + 字符数，不强加优先级判断
            section_list = []
            for name in section_names:
                char_count = len(s.paper_sections[name])
                if char_count < 50:
                    section_list.append(f"{name} (空)")
                else:
                    section_list.append(f"{name} ({char_count}字)")
            parts.append(f"  Sections: {', '.join(section_list)}")
            parts.append("  用 read_section('<name>') 按需读取，长 section 支持 offset 续读")

            # Phase 14: 显示已读 sections — 减少 Agent 重复读取的概率
            if s.sections_read:
                parts.append(f"  ✅ 你已读过 ({len(s.sections_read)}): {', '.join(s.sections_read)}")
                unread = [n for n in section_names if n not in s.sections_read]
                if unread:
                    parts.append(f"  📖 尚未读取: {', '.join(unread)}")

            # Phase 16: 展示 section digests — 压缩后仍可回溯的结构化摘要
            if s.section_digests:
                parts.append(f"\n  📝 Section 摘要缓存 ({len(s.section_digests)} 条，无需重读即可回溯):")
                for sec_name, digest in list(s.section_digests.items())[:10]:
                    parts.append(f"    • {sec_name}: {digest}")

        # 已有发现（带 evidence 摘要，方便 Agent 快速回忆）
        if s.findings:
            parts.append(f"\n你已有的发现 ({len(s.findings)} 条):")
            for i, f in enumerate(s.findings, 1):
                icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(f["priority"], "⚪")
                status = {"verified": "✓", "needs_verification": "?", "suggestion": "→"}.get(f["status"], "")
                evidence_hint = ""
                if f.get("evidence"):
                    evidence_hint = f" [证据: \"{f['evidence'][:60]}...\"]"
                elif f.get("section"):
                    evidence_hint = f" [来自: {f['section']}, 无证据]"
                parts.append(f"  {icon}[{status}] {f['finding'][:120]}{evidence_hint}")
            # 统计提示
            no_evidence = sum(1 for f in s.findings if not f.get("evidence"))
            if no_evidence > 0:
                parts.append(f"  ⚠️ {no_evidence} 条发现缺少原文证据，建议用 review_findings 复核")

        # Phase 57+58: 参考文献工作区 — 统一展示所有外部文献
        if s.reference_papers:
            # 分类展示
            user_refs = {k: v for k, v in s.reference_papers.items() if v.get("source") == "user_provided"}
            agent_refs = {k: v for k, v in s.reference_papers.items() if v.get("source") != "user_provided"}

            if user_refs:
                parts.append(f"\n📎 用户提供的参考文献 ({len(user_refs)} 篇，可用 read_reference 深入阅读):")
                for ref_id, info in user_refs.items():
                    char_info = f", {info.get('total_chars', '?')}字" if info.get("total_chars") else ""
                    sec_info = f", {info.get('section_count', '?')} sections" if info.get("section_count") else ""
                    abstract_hint = f" — {info['abstract'][:80]}..." if info.get("abstract") else ""
                    parts.append(f"  • [{ref_id}] {info.get('title', '?')}{abstract_hint} ({sec_info}{char_info})")

            if agent_refs:
                parts.append(f"\n📚 Agent 获取的外部论文 ({len(agent_refs)} 篇):")
                for pid, info in list(agent_refs.items())[:5]:
                    authors_short = ", ".join(info.get("authors", [])[:2])
                    if len(info.get("authors", [])) > 2:
                        authors_short += " et al."
                    tldr = info.get("tldr", "")
                    tldr_hint = f" — {tldr[:80]}..." if tldr else ""
                    parts.append(f"  • {info.get('title', '?')} ({info.get('year', '?')}, {info.get('venue', '?')}){tldr_hint}")
                if len(agent_refs) > 5:
                    parts.append(f"  ... 还有 {len(agent_refs) - 5} 篇")

        # 已做的修改
        if s.edits:
            parts.append(f"\n你已做的修改 ({len(s.edits)} 处): {', '.join(e['section'] for e in s.edits)}")

        # Phase 15: 跨会话记忆注入
        memory_context = self.memory.format_memory_context(paper_id=self._paper_id)
        if memory_context:
            parts.append(f"\n{memory_context}")

        # Phase 32: 元认知状态注入
        cognitive_context = self.cognitive_state.format_for_context()
        if cognitive_context:
            parts.append(f"\n{cognitive_context}")

        # Phase 32: 可恢复上下文引用列表
        refs_summary = self.offload_store.format_refs_summary()
        if refs_summary:
            parts.append(f"\n{refs_summary}")

        # 资源状态
        parts.append(f"\n轮次: {s.loop_turns}/{s.max_loop_turns} | 对话轮: {s.conversation_turns} | Tokens: ~{s.total_tokens}")

        return "\n".join(parts) if parts else "（刚开始，还没有任何状态）"

    # ----------------------------------------------------------
    # 工具执行
    # ----------------------------------------------------------

    def execute_tool(self, name: str, args: dict) -> str:
        """执行 tool call，返回结果字符串。纯逻辑，不含 LLM 调用。"""

        # Phase 31: 统计工具使用频次
        self.state.tool_call_counts[name] = self.state.tool_call_counts.get(name, 0) + 1

        # Phase 34: 逐条记录（用于认知行为分析，只保留 input 元信息不含大段结果）
        self.state.tool_call_history.append({"name": name, "input": args})

        # 容错: 如果 LLM 返回的 arguments 解析失败，client 层会传入 __parse_error__ 标记
        if "__parse_error__" in args:
            raw = args.get("__raw__", "")[:300]
            return (
                f"[工具调用失败] 你的参数格式无法解析。"
                f"错误: {args['__parse_error__']}。"
                f"原始内容: {raw}。"
                f"请重新调用 {name}，确保参数是合法的 JSON。"
            )

        if name == "read_section":
            result = self._tool_read_section(args)
        elif name == "search_literature":
            result = self._tool_search_literature(args)
        elif name == "update_findings":
            result = self._tool_update_findings(args)
        elif name == "review_findings":
            result = self._tool_review_findings(args)
        elif name == "edit_section":
            result = self._tool_edit_section(args)
        elif name == "talk_to_user":
            result = self._tool_talk_to_user(args)
        elif name == "spawn_perspective":
            result = self._tool_spawn_perspective(args)
        elif name == "reflect_and_plan":
            result = self._tool_reflect_and_plan(args)
        elif name == "detect_ai_signals":
            result = self._tool_detect_ai_signals(args)
        elif name == "verify_citations":
            result = self._tool_verify_citations(args)
        elif name == "recall_context":
            result = self._tool_recall_context(args)
        elif name == "fetch_paper_detail":
            result = self._tool_fetch_paper_detail(args)
        elif name == "read_reference":
            result = self._tool_read_reference(args)
        elif name == "done" or name == "mark_complete":
            result = self._tool_done(args)
        else:
            result = f"未知工具: {name}"

        # Phase 55: 停滞检测 — 主动呈现产出密度信号
        # 不依赖 Agent 主动调用 reflect_and_plan，在任何工具返回后检测
        # 只在非元认知工具上触发（避免在 reflect/review/done 上重复）
        stagnation_signal = self._check_stagnation(name)
        if stagnation_signal:
            result += stagnation_signal

        return result

    def _tool_read_section(self, args: dict) -> str:
        section = args.get("section", "").lower().strip()
        offset = args.get("offset", 0) or 0  # Phase 18: 续读支持
        sections = self.state.paper_sections

        # Phase 14: 追踪已读 sections，用于 format_context 提示防止重复读取
        # Phase 16: 同时生成 section digest 用于压缩后回溯
        # Phase 20: 同时累积 voice profile（零成本，为后续修改验证准备）
        # Phase 32: 同时 offload 到外部文件（折叠≠丢弃）
        def _record_read(resolved_name: str, content: str):
            if resolved_name not in self.state.sections_read:
                self.state.sections_read.append(resolved_name)
                # Phase 32: 首次读取时 offload 完整内容到外部文件
                if self.offload_store.should_offload(content, "read_section"):
                    digest = self.state.section_digests.get(resolved_name, content[:80])
                    self.offload_store.offload(
                        tool_name="read_section",
                        key=resolved_name,
                        content=content,
                        summary=digest,
                        loop_turn=self.state.loop_turns,
                    )
            # Phase 16: 生成 2 句话 digest（纯启发式，不调 LLM）
            if resolved_name not in self.state.section_digests:
                self.state.section_digests[resolved_name] = _generate_section_digest(resolved_name, content)
            # Phase 20: 提取并累积作者写作风格指纹
            if len(content) >= 200:  # 只对有实质内容的 section 提取
                section_fp = extract_voice(content)
                if section_fp.total_words_analyzed > 0:
                    if self.state.voice_profile is None:
                        self.state.voice_profile = section_fp
                    else:
                        # 加权合并（基于已分析的 word 数量）
                        existing = self.state.voice_profile
                        total = existing.total_words_analyzed + section_fp.total_words_analyzed
                        w1 = existing.total_words_analyzed / total
                        w2 = section_fp.total_words_analyzed / total
                        self.state.voice_profile = VoiceFingerprint(
                            avg_sentence_length=round(existing.avg_sentence_length * w1 + section_fp.avg_sentence_length * w2, 1),
                            sentence_length_std=round(existing.sentence_length_std * w1 + section_fp.sentence_length_std * w2, 1),
                            passive_ratio=round(existing.passive_ratio * w1 + section_fp.passive_ratio * w2, 2),
                            hedge_frequency=round(existing.hedge_frequency * w1 + section_fp.hedge_frequency * w2, 2),
                            total_words_analyzed=total,
                        )

        # Phase 18: 统一的截断+续读返回逻辑
        # 设计原则 (§4.3 约束-而非-控制):
        #   - 单次窗口 6000 字符是 token 预算约束（合理）
        #   - 但 Agent 有权决定是否继续读取（恢复自主权）
        #   - 截断时明确告知剩余量和续读方式
        WINDOW = 6000

        def _windowed_return(resolved_name: str, content: str) -> str:
            """返回 content[offset:offset+WINDOW]，必要时附带续读提示。
            
            Phase 31: 当内容含 verifiable claims 时，在末尾附加 claim signal。
            设计原则 (§4.3 约束-而非-控制):
            - 这是环境给 Agent 的信号，不是指令
            - Agent 可以忽略它（它只是一个 [信号]，不是 "你必须搜索"）
            - 类比：人类审稿人读到 "no prior work" 时，大脑会自动标记"这需要验证"
            - 我们只是在模拟这个"标记"过程，Agent 决定是否行动
            """
            total = len(content)
            if offset >= total:
                return (
                    f"[已到达 section '{resolved_name}' 末尾] "
                    f"全文 {total} 字符，offset={offset} 已超出范围。无需续读。"
                )
            chunk = content[offset:offset + WINDOW]
            end_pos = offset + len(chunk)
            remaining = total - end_pos

            # Phase 31: 检测 chunk 中的 verifiable claims
            claim_signal = detect_verifiable_claims(chunk)

            if remaining <= 0:
                # 本次返回已包含所有（从 offset 到末尾），无需续读提示
                if offset > 0:
                    result = f"[续读 {resolved_name}, 字符 {offset}-{end_pos}/{total}]\n\n{chunk}"
                else:
                    result = chunk
                return result + claim_signal if claim_signal else result
            else:
                # 还有剩余内容——告知 Agent 如何续读
                hint = (
                    f"\n\n[... 已显示字符 {offset}-{end_pos}，"
                    f"剩余 {remaining} 字符 (共 {total})。"
                    f"如需继续阅读，调用 read_section(section=\"{resolved_name}\", offset={end_pos}) ...]"
                )
                if offset > 0:
                    result = f"[续读 {resolved_name}, 字符 {offset}-{end_pos}/{total}]\n\n{chunk}{hint}"
                else:
                    result = chunk + hint
                return result + claim_signal if claim_signal else result

        if section == "list":
            names = [k for k in sections if k != "full"]
            lines = [f"可用 sections ({len(names)}):"]
            for name in names:
                char_count = len(sections[name])
                lines.append(f"  - {name} ({char_count} 字符)")
            return "\n".join(lines)
        elif section == "full":
            full = sections.get("full", "")
            if not full:
                return "没有全文。请用 read_section('list') 查看可用 sections，逐段读取。"
            # 短论文直接返回全文；长论文提示分段读取
            if len(full) > 12000:
                names = [k for k in sections if k != "full"]
                return (
                    full[:3000]
                    + f"\n\n[... 论文共 {len(full)} 字符，已截断。"
                    f"可用 sections: {', '.join(names[:10])}。"
                    f"请用 read_section 按需读取具体 section，避免全文注入浪费 token。 ...]"
                )
            return full
        else:
            # 1. 精确匹配
            if section in sections:
                content = sections[section]
                if len(content) < 50:
                    return (
                        f"[注意] Section '{section}' 内容极少（仅 {len(content)} 字符: \"{content.strip()}\"）。"
                        f"这可能是一个空壳子标题，实际内容在其子 section 中。"
                        f"建议读取其他相关 section。"
                    )
                _record_read(section, content)
                return _windowed_return(section, content)

            # 2. 模糊匹配：选择最佳匹配（最长 key 优先，避免短 key 意外匹配）
            candidates = []
            for key in sections:
                if key == "full":
                    continue
                if section in key.lower() or key.lower() in section:
                    candidates.append(key)

            if candidates:
                best = max(candidates, key=len)
                content = sections[best]
                if len(content) < 50:
                    return (
                        f"[注意] Section '{best}' 内容极少（仅 {len(content)} 字符: \"{content.strip()}\"）。"
                        f"这可能是一个空壳子标题，实际内容在其子 section 中。"
                        f"建议读取其他相关 section。"
                    )
                _record_read(best, content)
                return _windowed_return(best, content)

            # 3. 尝试数字匹配（如用户输入 "3" 匹配 "3. methodology"）
            for key in sections:
                if key.startswith(section + ".") or key.startswith(section + " "):
                    content = sections[key]
                    _record_read(key, content)
                    return _windowed_return(key, content)
            available = ", ".join(k for k in sections.keys() if k != "full")
            return f"未找到 section '{section}'。可用: {available}"

    def _tool_search_literature(self, args: dict) -> str:
        query = args.get("query", "")
        reason = args.get("reason", "")
        # Phase 39: 记录搜索行为用于认知观察
        if not hasattr(self, '_search_log'):
            self._search_log = []
        try:
            from core.web_search import intelligent_search
            response = intelligent_search(query, limit=5)
            self._search_log.append({
                "query": query,
                "reason": reason,
                "results_count": len(response.results),
                "source": response.source,
                "loop_turn": self.state.loop_turns,
            })
            if not response.results:
                return f"搜索 '{query}' 无结果。{response.error or ''}\n原因: {reason}"
            lines = [f"搜索 '{query}' 的结果 (来源: {response.source}, 共 {response.total_found} 条):"]
            for i, r in enumerate(response.results, 1):
                authors = ", ".join(r.authors[:3])
                if len(r.authors) > 3:
                    authors += " et al."
                lines.append(f"  [{i}] {r.title} ({r.year or '?'})")
                lines.append(f"      作者: {authors} | 发表于: {r.venue or '?'} | 引用: {r.citation_count or 'N/A'}")
                if r.abstract:
                    lines.append(f"      摘要: {r.abstract[:150]}...")
            result = "\n".join(lines)

            # Phase 32: offload 搜索结果
            if self.offload_store.should_offload(result, "search_literature"):
                summary = f"搜索'{query}'得到{len(response.results)}条结果"
                self.offload_store.offload(
                    tool_name="search_literature",
                    key=query,
                    content=result,
                    summary=summary,
                    loop_turn=self.state.loop_turns,
                )

            return result
        except Exception as e:
            self._search_log.append({
                "query": query,
                "reason": reason,
                "results_count": 0,
                "source": "error",
                "loop_turn": self.state.loop_turns,
                "error": str(e),
            })
            return f"搜索失败 ({type(e).__name__}: {e})。你可以基于已有知识继续判断，或标记为 'needs_verification'。"

    def _tool_fetch_paper_detail(self, args: dict) -> str:
        """Phase 57: 获取外部论文的详细信息，存入参考文献工作区。
        
        让 Agent 能"翻开"搜索结果中的论文，看到完整摘要、TLDR、
        关键引用关系——就像审稿人从书架上拿下一篇论文来对比方法论。
        """
        paper_id = args.get("paper_id")
        doi = args.get("doi")
        title = args.get("title")
        reason = args.get("reason", "")

        if not any([paper_id, doi, title]):
            return "必须提供 paper_id、doi 或 title 中的至少一个。"

        try:
            from core.web_search import fetch_paper_detail
            detail = fetch_paper_detail(paper_id=paper_id, doi=doi, title=title)

            if detail.error:
                return f"获取论文详情失败: {detail.error}\n原因: {reason}"

            # 存入参考文献工作区
            store_key = detail.paper_id or title or doi or "unknown"
            self.state.reference_papers[store_key] = {
                "title": detail.title,
                "authors": detail.authors,
                "year": detail.year,
                "venue": detail.venue,
                "abstract": detail.abstract,
                "tldr": detail.tldr,
                "citation_count": detail.citation_count,
                "reference_count": detail.reference_count,
                "influential_citation_count": detail.influential_citation_count,
                "fields_of_study": detail.fields_of_study,
                "key_references": detail.key_references,
                "key_citations": detail.key_citations,
                "fetched_at_turn": self.state.loop_turns,
                "fetch_reason": reason,
            }

            # 格式化返回给 Agent 的详细信息
            lines = [f"📄 论文详情: {detail.title}"]
            lines.append(f"   作者: {', '.join(detail.authors[:5])}")
            lines.append(f"   年份: {detail.year or '?'} | 发表于: {detail.venue or '?'}")
            lines.append(f"   引用: {detail.citation_count or 'N/A'} (其中 influential: {detail.influential_citation_count or 'N/A'})")
            lines.append(f"   参考文献数: {detail.reference_count or 'N/A'}")
            if detail.fields_of_study:
                lines.append(f"   领域: {', '.join(detail.fields_of_study)}")

            if detail.tldr:
                lines.append(f"\n   TLDR: {detail.tldr}")

            if detail.abstract:
                lines.append(f"\n   完整摘要: {detail.abstract}")

            if detail.key_references:
                lines.append(f"\n   关键参考文献 (该论文引用的高影响力论文, top {len(detail.key_references)}):")
                for i, ref in enumerate(detail.key_references[:7], 1):
                    lines.append(f"     [{i}] {ref['title']} ({ref['year']}, {ref['venue']})")

            if detail.key_citations:
                lines.append(f"\n   关键后续引用 (引用该论文的高影响力论文, top {len(detail.key_citations)}):")
                for i, cit in enumerate(detail.key_citations[:7], 1):
                    lines.append(f"     [{i}] {cit['title']} ({cit['year']}, {cit['venue']})")

            lines.append(f"\n   [已存入参考文献工作区，共 {len(self.state.reference_papers)} 篇]")

            result = "\n".join(lines)

            # Offload if too long
            if self.offload_store.should_offload(result, "fetch_paper_detail"):
                summary = f"获取了'{detail.title}'的详情 (TLDR: {(detail.tldr or '')[:60]})"
                self.offload_store.offload(
                    tool_name="fetch_paper_detail",
                    key=detail.title or store_key,
                    content=result,
                    summary=summary,
                    loop_turn=self.state.loop_turns,
                )

            return result

        except Exception as e:
            return f"获取论文详情时出错 ({type(e).__name__}: {e})。你可以基于搜索结果中的摘要继续判断。"

    def _tool_read_reference(self, args: dict) -> str:
        """Phase 58: 读取用户提供的参考文献内容。

        Agent 可以按 ref_id 读取特定 section，或列出可用 sections。
        支持 offset 续读长文档，与 read_section 的交互模式一致。
        """
        ref_id = args.get("ref_id", "")
        section = args.get("section", "")
        offset = args.get("offset", 0)
        max_chars = args.get("max_chars", 3000)

        # 如果没有用户参考文献
        if not self.state.user_reference_docs:
            return "当前没有用户提供的参考文献。参考文献工作区中的论文来自 Agent 搜索，请用 fetch_paper_detail 获取详情。"

        # 如果没指定 ref_id，列出所有可用的参考文献
        if not ref_id:
            lines = ["可用的参考文献:"]
            for rid, doc in self.state.user_reference_docs.items():
                sections_str = ", ".join(doc["section_names"][:10])
                lines.append(f"  • {rid}: {doc['title']} (sections: {sections_str})")
            lines.append("\n用 read_reference(ref_id='ref_1', section='abstract') 读取具体内容。")
            return "\n".join(lines)

        # 查找指定的参考文献
        if ref_id not in self.state.user_reference_docs:
            available = ", ".join(self.state.user_reference_docs.keys())
            return f"未找到参考文献 '{ref_id}'。可用的 ref_id: {available}"

        doc = self.state.user_reference_docs[ref_id]

        # 如果没指定 section，列出该文献的所有 sections
        if not section:
            lines = [f"📎 {doc['title']} 的可用 sections:"]
            for sec_name in doc["section_names"]:
                char_count = len(doc["sections"].get(sec_name, ""))
                lines.append(f"  • {sec_name} ({char_count}字)")
            lines.append(f"\n用 read_reference(ref_id='{ref_id}', section='<name>') 读取具体 section。")
            return "\n".join(lines)

        # 模糊匹配 section 名
        matched_section = None
        section_lower = section.lower().strip()
        for sec_name in doc["section_names"]:
            if sec_name.lower() == section_lower:
                matched_section = sec_name
                break
        if not matched_section:
            # 尝试部分匹配
            for sec_name in doc["section_names"]:
                if section_lower in sec_name.lower() or sec_name.lower() in section_lower:
                    matched_section = sec_name
                    break
        if not matched_section:
            available = ", ".join(doc["section_names"])
            return f"在 '{ref_id}' 中未找到 section '{section}'。可用: {available}"

        # 读取内容
        content = doc["sections"].get(matched_section, "")
        total_chars = len(content)

        if offset >= total_chars:
            return f"offset {offset} 超出 section '{matched_section}' 的总长度 ({total_chars}字)。"

        chunk = content[offset:offset + max_chars]
        remaining = total_chars - offset - len(chunk)

        header = f"📎 [{ref_id}] {doc['title']} → section: {matched_section}\n"
        header += f"   ({total_chars}字, 当前 offset={offset}, 返回 {len(chunk)}字"
        if remaining > 0:
            header += f", 剩余 {remaining}字 — 用 offset={offset + len(chunk)} 续读"
        header += ")\n\n"

        return header + chunk

    def _tool_update_findings(self, args: dict) -> str:
        finding = {
            "finding": args["finding"],
            "priority": args.get("priority", "medium"),
            "status": args.get("status", "suggestion"),
            "evidence": args.get("evidence", ""),  # 原文证据
            "section": args.get("section", ""),    # 出处章节
            "recorded_at_turn": self.state.loop_turns,  # Phase 52: 记录产出时的轮次
        }
        
        # Phase 47: 前置去重检查 — 如果新 finding 与已有 finding 高度重叠，提醒而非追加
        if self.state.findings:
            overlap_warning = self._check_finding_overlap(finding)
            if overlap_warning:
                return overlap_warning
        
        self.state.findings.append(finding)
        evidence_note = f" (含原文证据, 来自 '{finding['section']}')" if finding['evidence'] else ""
        return f"已记录发现{evidence_note} (当前共 {len(self.state.findings)} 条)"
    
    def _check_finding_overlap(self, new_finding: dict) -> str | None:
        """
        Phase 47: 检查新 finding 是否与已有 findings 高度重叠。
        
        如果重叠度 >= 70%，返回提醒字符串（不追加）。
        如果不重叠，返回 None（允许追加）。
        
        设计原则：
        - 不阻止 Agent 更新已有发现的状态（如 needs_verification → verified）
        - 只阻止"同一个观察记录两次"的情况
        - 如果新 finding 的 status 与已有不同（如升级为 verified），允许追加但提醒合并
        """
        import re as _re
        
        def _extract_terms(text: str) -> set[str]:
            en_words = set(_re.findall(r'[a-zA-Z]{4,}', text.lower()))
            stopwords = {'this', 'that', 'with', 'from', 'have', 'been', 'which', 'their',
                         'more', 'than', 'also', 'some', 'other', 'about', 'would', 'could',
                         'should', 'these', 'those', 'into', 'only', 'very', 'such', 'each',
                         'finding', 'section', 'paper', 'author', 'however', 'therefore',
                         'does', 'will', 'what', 'when', 'where', 'there', 'over', 'under',
                         'between', 'through', 'during', 'before', 'after', 'above', 'below'}
            return {w for w in en_words if w not in stopwords}
        
        new_terms = _extract_terms(new_finding["finding"])
        if len(new_terms) < 3:
            return None  # 太短，无法判断
        
        for i, existing in enumerate(self.state.findings):
            existing_terms = _extract_terms(existing.get("finding", ""))
            if len(existing_terms) < 3:
                continue
            
            intersection = new_terms & existing_terms
            overlap_coeff = len(intersection) / min(len(new_terms), len(existing_terms))
            
            if overlap_coeff >= 0.70:
                # 高度重叠 — 检查是否是状态更新
                new_status = new_finding.get("status", "suggestion")
                old_status = existing.get("status", "suggestion")
                
                if new_status != old_status:
                    # 状态变化：允许追加但建议合并
                    self.state.findings.append(new_finding)
                    return (
                        f"已记录，但注意：这条发现与已有发现 #{i+1} 高度重叠 "
                        f"(术语重合 {overlap_coeff:.0%})。"
                        f"已有状态: {old_status} → 新状态: {new_status}。"
                        f"建议：如果是同一个问题的状态更新，考虑直接说明'之前的怀疑已验证/排除'即可。"
                        f" (当前共 {len(self.state.findings)} 条)"
                    )
                else:
                    # 同状态重复：不追加，直接提醒
                    return (
                        f"⚠️ 未记录：这条发现与已有发现 #{i+1} 高度重叠 "
                        f"(术语重合 {overlap_coeff:.0%})。"
                        f"重复的发现不增加审稿价值。"
                        f"如果你想更新已有发现的状态或补充证据，请明确说明。"
                        f" (当前仍为 {len(self.state.findings)} 条)"
                    )
        
        return None  # 无重叠，允许追加

    def _tool_review_findings(self, args: dict) -> str:
        """回顾已有发现，支持按过滤器查看。Agent 可用此工具自审、复核。"""
        filter_type = args.get("filter", "all")
        findings = self.state.findings

        if not findings:
            return "当前没有任何发现记录。"

        # 过滤
        if filter_type == "high":
            filtered = [f for f in findings if f.get("priority") == "high"]
        elif filter_type == "needs_verification":
            filtered = [f for f in findings if f.get("status") == "needs_verification"]
        elif filter_type == "verified":
            filtered = [f for f in findings if f.get("status") == "verified"]
        else:
            filtered = findings

        if not filtered:
            return f"按 filter='{filter_type}' 筛选后无匹配项。全部 {len(findings)} 条发现中: " + \
                   f"high={sum(1 for f in findings if f.get('priority')=='high')}, " + \
                   f"needs_verification={sum(1 for f in findings if f.get('status')=='needs_verification')}。"

        lines = [f"发现回顾 (filter='{filter_type}', 共 {len(filtered)}/{len(findings)} 条):"]
        lines.append("="*60)
        for i, f in enumerate(filtered, 1):
            icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(f["priority"], "⚪")
            status_label = {"verified": "✓已验证", "needs_verification": "?待验证", "suggestion": "→建议"}.get(f["status"], f["status"])
            lines.append(f"\n[{i}] {icon} [{status_label}] {f['finding']}")
            if f.get("section"):
                lines.append(f"    📍 出处: {f['section']}")
            if f.get("evidence"):
                # 展示证据，限制长度避免 token 浪费
                ev = f['evidence']
                if len(ev) > 300:
                    ev = ev[:300] + "..."
                lines.append(f"    📄 原文证据: \"{ev}\"")
            else:
                lines.append(f"    ⚠️ 无原文证据 — 建议重新查阅 section 补充")
        lines.append("\n" + "="*60)
        lines.append(f"提示: 对 '待验证' 的发现，可 read_section 重读原文核实，再 update_findings 更新状态。")
        return "\n".join(lines)

    def _tool_edit_section(self, args: dict) -> str:
        section = args.get("section", "")
        new_content = args.get("new_content", "")
        reason = args.get("reason", "")

        self.state.edits.append({
            "section": section,
            "reason": reason,
            "content_preview": new_content[:200] + "..." if len(new_content) > 200 else new_content,
        })

        for key in list(self.state.paper_sections.keys()):
            if section.lower() in key.lower() or key.lower() in section.lower():
                old_content = self.state.paper_sections[key]
                self.state.paper_sections[key] = new_content

                # Phase 20: 零成本三层验证，结果作为反馈返回给 Agent
                all_text = "\n\n".join(self.state.paper_sections.values())
                verification = verify_edit(
                    section_name=key,
                    old_text=old_content,
                    new_text=new_content,
                    all_sections_text=all_text,
                    voice_profile=self.state.voice_profile,
                )
                feedback = format_verification_feedback(verification, key)
                result = f"已修改 section '{key}'（原因: {reason}）\n\n{feedback}"

                # Phase 50: 小模型快速校验（认知分层 — System 1 辅助 System 2）
                checker_warning = self.checker.check_edit(new_content, reason)
                if checker_warning:
                    result += checker_warning

                return result
        return f"未找到 section '{section}'，修改已记录但未应用"

    def _tool_talk_to_user(self, args: dict) -> str:
        message = args.get("message", "")
        expects_reply = args.get("expects_reply", False)
        # 在交互模式下，这里会真正暂停等用户回复
        # 具体行为由 loop 层控制（loop 决定是否 yield 给用户）
        return f"__TALK__|{json.dumps({'message': message, 'expects_reply': expects_reply}, ensure_ascii=False)}"

    def _tool_spawn_perspective(self, args: dict) -> str:
        """发起子视角审视。返回 __SPAWN__ 信号给 loop 层驱动子循环。"""
        lens = args.get("lens", "")
        focus = args.get("focus", "")
        question = args.get("question", "")

        if not lens or not question:
            return "spawn_perspective 需要 lens 和 question 参数。"

        # 打包信息给 loop 层
        spawn_payload = json.dumps({
            "lens": lens,
            "focus": focus,
            "question": question,
        }, ensure_ascii=False)
        return f"__SPAWN__|{spawn_payload}"

    def ingest_perspective_findings(self, findings: list[dict], lens: str, summary: str) -> str:
        """
        将子视角的发现注入主 Agent 的 state。由 loop 层在子循环完成后调用。

        Args:
            findings: 子视角产出的 findings 列表
            lens: 子视角的身份标签
            summary: 子视角的总结

        Returns:
            给主 Agent 的结果摘要
        """
        injected_count = 0
        for f in findings:
            # 标记来源视角，避免混淆
            f["perspective"] = lens
            self.state.findings.append(f)
            injected_count += 1

        # 构建给主 Agent 的结果摘要
        lines = [f"独立视角 [{lens}] 审视完成。"]
        if injected_count > 0:
            lines.append(f"发现 {injected_count} 条问题（已加入你的工作记忆，标记为来自此视角）:")
            for i, f in enumerate(findings, 1):
                icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(f.get("priority", ""), "⚪")
                lines.append(f"  {icon} {f.get('finding', '')[:150]}")
        else:
            lines.append("未发现显著问题。")
        if summary:
            lines.append(f"视角总结: {summary}")
        return "\n".join(lines)

    def create_sub_harness(self, focus_sections: list[str]) -> "Harness":
        """
        创建一个轻量的子 Harness，只包含指定 sections 的内容。
        用于子视角的独立循环。

        Args:
            focus_sections: 需要提供给子视角的 section 名称列表

        Returns:
            一个独立的 Harness 实例（共享论文内容但有独立的 findings/state）
        """
        sub = Harness(max_loop_turns=8, token_budget=30000)
        sub._paper_loaded = True

        # 复制相关 sections 到子 harness
        for key, content in self.state.paper_sections.items():
            if key == "full":
                continue
            # 模糊匹配：focus 里的任何一个能匹配上就给
            for focus in focus_sections:
                if focus.lower() in key.lower() or key.lower() in focus.lower():
                    sub.state.paper_sections[key] = content
                    break

        # 如果没匹配到任何东西，给全部（退化为完整论文）
        if not sub.state.paper_sections:
            sub.state.paper_sections = dict(self.state.paper_sections)

        return sub

    def _tool_reflect_and_plan(self, args: dict) -> str:
        """
        元认知工具：Agent 主动触发反思。
        
        Harness 汇总结构化的反思上下文，帮助 Agent 做"暂停-审视-调整"。
        这不是控制 Agent——Agent 随时可以不调用它。但当 Agent 想要
        "退后一步看全局"时，这个工具给它一面镜子。
        """
        trigger = args.get("trigger", "自主反思")
        current_thinking = args.get("current_thinking", "")
        
        s = self.state
        
        # 1. 进度摘要
        findings_by_priority = {
            "high": [f for f in s.findings if f.get("priority") == "high"],
            "medium": [f for f in s.findings if f.get("priority") == "medium"],
            "low": [f for f in s.findings if f.get("priority") == "low"],
        }
        unverified = [f for f in s.findings if f.get("status") == "needs_verification"]
        
        # 2. 资源状态
        turns_remaining = s.max_loop_turns - s.loop_turns
        token_pct = (s.total_tokens / s.token_budget * 100) if s.token_budget else 0
        
        # 3. 覆盖度分析（哪些 section 还没读过——基于 findings 的 section 字段推断）
        all_sections = set(k for k in s.paper_sections if k != "full")
        touched_sections = set()
        for f in s.findings:
            sec = f.get("section", "")
            if sec:
                # 模糊匹配回 section names
                for name in all_sections:
                    if sec.lower() in name.lower() or name.lower() in sec.lower():
                        touched_sections.add(name)
                        break
        # Phase 18: 不再用 _classify_section 硬编码判断核心/非核心
        # 只提供"尚未触及的 sections"列表——Agent 自己知道哪些重要
        untouched = sorted(all_sections - touched_sections)
        
        # 4. 开放问题（needs_verification 的 findings）
        open_questions = []
        for f in unverified:
            open_questions.append(f"- [{f.get('priority', '?')}] {f['finding'][:100]}")
        
        # 5. 组装反思上下文
        lines = [
            "═══ 反思时刻 ═══",
            f"触发原因: {trigger}",
            "",
            "【进度】",
            f"  已记录 {len(s.findings)} 条发现: {len(findings_by_priority['high'])} high, "
            f"{len(findings_by_priority['medium'])} medium, {len(findings_by_priority['low'])} low",
            f"  已修改 {len(s.edits)} 个 section",
            "",
            "【资源】",
            f"  轮次: 已用 {s.loop_turns}/{s.max_loop_turns} (剩余 {turns_remaining})",
            f"  Token: ~{s.total_tokens} / {s.token_budget} ({token_pct:.0f}% 已消耗)",
            "",
            "【覆盖度】",
            f"  论文共 {len(all_sections)} sections",
            f"  已触及 {len(touched_sections)} sections: {', '.join(sorted(touched_sections)[:8])}",
        ]
        
        if untouched:
            lines.append(f"  尚未阅读: {', '.join(untouched[:10])}"
                         + (f" ...等 {len(untouched)} 个" if len(untouched) > 10 else ""))
        
        if open_questions:
            lines.append("")
            lines.append(f"【待验证 ({len(unverified)} 条)】")
            lines.extend(open_questions[:5])
            if len(open_questions) > 5:
                lines.append(f"  ...还有 {len(open_questions) - 5} 条")
        
        # Phase 39+41: 外部验证状态
        search_count = len(getattr(self, '_search_log', []))
        lines.append("")
        lines.append("【外部验证】")
        lines.append(f"  search_literature 已调用 {search_count} 次")
        if search_count == 0 and len(s.findings) > 0:
            lines.append("  ⚠ 你有发现但尚未查过外部文献——你的判断完全基于论文自身的叙述。")
            lines.append("  一个好审稿人会用外部文献校准自己的判断——尤其是对方法论和核心 claim 的判断。")
        elif search_count == 0 and len(s.sections_read) >= 4:
            # Phase 41: 读了很多但没搜索（即使还没产出 findings）
            lines.append(f"  ⚠ 你已读了 {len(s.sections_read)} 个 section 但尚未查过外部文献。")
            lines.append("  即使你还在形成判断，外部文献可以帮你更快定位论文的真正弱点。")
        
        # Phase 40: 追查缺口事实
        unverified_findings = [f for f in s.findings if f.get("status") == "needs_verification"]
        if unverified_findings:
            lines.append("")
            lines.append(f"【追查缺口】")
            lines.append(f"  你有 {len(unverified_findings)} 条发现标记为 needs_verification:")
            for uf in unverified_findings[:4]:
                lines.append(f"    • {uf['finding'][:80]}")
            lines.append(f"  这些发现目前只是你的怀疑——你还没有回去验证它们是否真的成立。")
            lines.append(f"  一个好审稿人不会把'我怀疑有问题'写进 report——他会追查到'确认有问题'或'排除了这个怀疑'。")
        
        # Phase 40: Findings 重叠检测
        if len(s.findings) >= 2:
            overlaps = _detect_finding_overlaps(s.findings)
            if overlaps:
                lines.append("")
                lines.append("【发现重叠警告】")
                for pair_desc in overlaps[:3]:
                    lines.append(f"  ⚠ {pair_desc}")
                lines.append("  重复的发现不增加审稿价值——考虑合并它们，然后去找新的角度。")
        
        # Phase 43: 维度覆盖度分析——告诉 Agent 它的 findings 集中在哪些维度
        if len(s.findings) >= 2:
            finding_texts = " ".join(f.get("finding", "") for f in s.findings).lower()
            # 简单启发式：检测 findings 是否集中在同一个主题
            dimension_keywords = {
                "识别假设/因果推断": ["quasi-random", "random assignment", "selection", "identification", "causal", "endogen"],
                "结构模型/函数形式": ["structural", "functional form", "parametric", "distribut", "model specif"],
                "外部有效性": ["external valid", "generaliz", "other setting", "推广"],
                "数据质量/测量": ["measurement", "ascertainment", "data quality", "missing", "attrition"],
                "时间稳定性/动态": ["time-varying", "stability", "dynamic", "temporal", "learning"],
            }
            covered_dims = []
            uncovered_dims = []
            for dim_name, keywords in dimension_keywords.items():
                if any(kw in finding_texts for kw in keywords):
                    covered_dims.append(dim_name)
                else:
                    uncovered_dims.append(dim_name)
            
            if covered_dims and uncovered_dims and len(covered_dims) <= 2:
                lines.append("")
                lines.append("【维度覆盖度】")
                lines.append(f"  你当前的发现集中在: {', '.join(covered_dims)}")
                lines.append(f"  尚未触及的维度: {', '.join(uncovered_dims)}")
                lines.append(f"  （这不是要求你覆盖所有维度——只是让你知道你目前的视角范围。）")
        
        # Phase 46: 学科能力边界提示——当论文跨学科时，提醒 Agent 可以用独立视角
        if len(s.findings) >= 2 and not any(
            t.get("name") == "spawn_perspective" for t in s.tool_call_history
        ):
            # 检测论文是否涉及多个学科（基于 section 内容的启发式判断）
            paper_text_sample = " ".join(
                content[:500] for content in s.paper_sections.values() if content
            ).lower()
            discipline_signals = {
                "统计/计量方法": ["propensity", "causal inference", "double machine learning", "dml", "semiparametric", "asymptotic"],
                "机器学习/深度学习": ["transformer", "neural network", "deep learning", "attention", "gradient", "training"],
                "临床医学/流行病学": ["patient", "clinical", "treatment effect", "randomized trial", "rct", "ehr", "electronic health"],
                "经济学/社会科学": ["difference-in-differences", "instrumental variable", "regression discontinuity", "welfare"],
            }
            detected_disciplines = []
            for disc_name, signals in discipline_signals.items():
                if sum(1 for sig in signals if sig in paper_text_sample) >= 2:
                    detected_disciplines.append(disc_name)
            
            if len(detected_disciplines) >= 2:
                # 检查 findings 覆盖了哪些学科
                findings_text = " ".join(f.get("finding", "") for f in s.findings).lower()
                findings_disciplines = []
                for disc_name, signals in discipline_signals.items():
                    if any(sig in findings_text for sig in signals):
                        findings_disciplines.append(disc_name)
                
                uncovered_disciplines = [d for d in detected_disciplines if d not in findings_disciplines]
                
                if uncovered_disciplines:
                    lines.append("")
                    lines.append("【学科覆盖度】")
                    lines.append(f"  这篇论文涉及 {len(detected_disciplines)} 个学科: {', '.join(detected_disciplines)}")
                    covered_str = ', '.join(findings_disciplines) if findings_disciplines else '(尚未明确)'
                    lines.append(f"  你的发现目前覆盖: {covered_str}")
                    lines.append(f"  尚未深入审视: {', '.join(uncovered_disciplines)}")
                    lines.append(f"  （你可以用 spawn_perspective 请一个该领域的独立专家来审视你不确定的部分。）")
        
        # Phase 52: 边际产出信号 (Marginal Productivity Signal)
        # 核心思想: 让 Agent 看到"最近几轮的产出密度"vs"历史平均"，
        # 当边际产出显著衰减时，Agent 自己决定是否切换方向。
        # 遵循 §4.3: 信息呈现，不是指令。
        if s.loop_turns >= 6 and len(s.findings) >= 2:
            productivity_signal = self._compute_marginal_productivity()
            if productivity_signal:
                lines.append("")
                lines.append("【边际产出】")
                lines.extend(f"  {line}" for line in productivity_signal)
        
        lines.append("")
        lines.append("【反思提示】")
        lines.append("  基于以上信息，思考:")
        lines.append("  1. 我的主要假说是否已被充分验证/推翻？")
        lines.append("  2. 剩余资源够做什么？该深入还是该收尾？")
        lines.append("  3. 有没有我遗漏的重要角度？")
        lines.append("  4. 我的判断有没有外部校准？（是否需要搜索文献确认？）")
        lines.append("  5. 这篇论文是否跨学科？我对每个学科的判断置信度是否一样？")
        lines.append("  6. 我在当前方向上的边际产出是否在递减？是否该换个角度？")
        
        if current_thinking:
            lines.append(f"\n你当前的思路: {current_thinking}")
        
        # Phase 32: 处理认知状态更新
        cognitive_update = args.get("cognitive_update")
        if cognitive_update and isinstance(cognitive_update, dict):
            self.cognitive_state.update_from_reflection(cognitive_update)
            self.cognitive_state.last_updated_turn = s.loop_turns
            lines.append("\n[认知状态已更新]")
        else:
            # 自动推断策略（仅在 Agent 未显式设置时）
            self.cognitive_state.auto_infer_strategy({
                "sections_read_count": len(s.sections_read),
                "total_sections": len([k for k in s.paper_sections if k != "full"]),
                "findings_count": len(s.findings),
                "edits_count": len(s.edits),
                "loop_turns": s.loop_turns,
            })
            self.cognitive_state.last_updated_turn = s.loop_turns

        # Phase 54: 追踪策略切换（用于程序性记忆提取）
        new_strategy = self.cognitive_state.current_strategy
        if new_strategy != self._last_strategy and self._last_strategy != "undecided":
            self._strategy_transitions.append((self._last_strategy, new_strategy))
        self._last_strategy = new_strategy
        
        # 记录反思事件（用于后续分析 Agent 的元认知频率）
        if not hasattr(self, '_reflection_log'):
            self._reflection_log = []
        self._reflection_log.append({
            "turn": s.loop_turns,
            "trigger": trigger,
            "findings_count": len(s.findings),
            "current_thinking": current_thinking[:100],
            "cognitive_strategy": self.cognitive_state.current_strategy,
        })
        
        return "\n".join(lines)

    def _compute_marginal_productivity(self) -> list[str] | None:
        """
        Phase 52: 计算边际产出信号。
        
        核心逻辑:
        - 将 findings 按 recorded_at_turn 分布到时间轴上
        - 计算"最近 window 轮"的产出密度 vs "之前所有轮"的产出密度
        - 当最近窗口的密度显著低于历史平均时，生成信号
        
        设计原则 (§4.3):
        - 只呈现事实（"你最近 5 轮产出了 0 条新发现"），不下指令
        - Agent 看到信号后自主决定是否切换方向
        - 不触发时返回 None（避免信息噪音）
        """
        s = self.state
        current_turn = s.loop_turns
        
        # 收集有 turn 信息的 findings（兼容旧 findings 没有 recorded_at_turn 的情况）
        findings_with_turn = [
            f for f in s.findings if "recorded_at_turn" in f
        ]
        
        if len(findings_with_turn) < 2:
            return None  # 数据不足，不生成信号
        
        # 动态窗口: 取最近 1/3 的轮次作为"近期窗口"，最少 4 轮
        window_size = max(4, current_turn // 3)
        window_start = current_turn - window_size
        
        # 分割: 近期 vs 早期
        recent_findings = [f for f in findings_with_turn if f["recorded_at_turn"] >= window_start]
        earlier_findings = [f for f in findings_with_turn if f["recorded_at_turn"] < window_start]
        
        # 计算密度 (findings per turn)
        recent_density = len(recent_findings) / window_size if window_size > 0 else 0
        earlier_turns = window_start  # 早期跨越的轮次数
        earlier_density = len(earlier_findings) / earlier_turns if earlier_turns > 0 else 0
        
        # 判断是否触发信号
        # 条件: 早期有产出（排除"一开始就没产出"的情况），且近期密度显著下降
        if earlier_density <= 0:
            return None  # 早期也没产出，不是"衰减"问题
        
        decay_ratio = recent_density / earlier_density if earlier_density > 0 else 1.0
        
        # 只在显著衰减时触发 (近期密度 < 早期的 40%)
        if decay_ratio >= 0.4:
            return None  # 产出还算正常，不需要信号
        
        # 生成信号文本
        lines = []
        lines.append(f"最近 {window_size} 轮 (Turn {window_start+1}~{current_turn}): "
                     f"产出 {len(recent_findings)} 条新发现 "
                     f"(密度 {recent_density:.2f} 条/轮)")
        lines.append(f"之前 {earlier_turns} 轮 (Turn 1~{window_start}): "
                     f"产出 {len(earlier_findings)} 条新发现 "
                     f"(密度 {earlier_density:.2f} 条/轮)")
        
        # 根据衰减程度给出不同强度的事实陈述
        if decay_ratio == 0:
            lines.append(f"⚠ 你在最近 {window_size} 轮中没有产出任何新发现。")
        else:
            lines.append(f"近期产出密度降至早期的 {decay_ratio*100:.0f}%。")
        
        # 补充当前策略信息（帮助 Agent 关联"我在做什么"和"产出如何"）
        strategy = self.cognitive_state.current_strategy
        if strategy != "undecided":
            strategy_labels = {
                "deep_investigation": "深度追查",
                "breadth_scan": "广度扫描",
                "targeted_verification": "定向验证",
                "revision_mode": "修改模式",
                "synthesis": "综合收尾",
            }
            label = strategy_labels.get(strategy, strategy)
            lines.append(f"当前策略: {label}")
        
        # §4.3 约束: 不说"你应该切换"，只说"这是事实，你来判断"
        lines.append("（这是客观产出数据。是否需要调整方向，由你判断。）")
        
        return lines

    def _check_stagnation(self, current_tool: str) -> str | None:
        """
        Phase 55: 停滞检测 — 主动呈现产出密度信号。

        解决的问题:
            Agent 缺乏"我在原地打转"的自我感知。Phase 52 的 _compute_marginal_productivity
            只在 reflect_and_plan 中触发，但如果 Agent 不主动 reflect（比如一直在 read_section），
            就永远看不到停滞信号。

        设计原则 (§4.3):
            - 数据呈现，不是指令: 只告诉 Agent "你最近 N 轮没有新产出"
            - 不在元认知工具上触发: reflect_and_plan/review_findings/mark_complete 已有自己的信号
            - 频率控制: 不是每轮都提醒，只在连续无产出达到阈值时触发一次
            - 冷却期: 触发后 3 轮内不再重复（避免信息噪音）

        Returns:
            None 如果不需要信号
            str 如果检测到停滞（追加到 tool_result 末尾）
        """
        # 不在元认知/终止工具上触发（这些工具有自己的信号机制）
        meta_tools = {"reflect_and_plan", "review_findings", "mark_complete", "done", "talk_to_user"}
        if current_tool in meta_tools:
            return None

        s = self.state
        current_turn = s.loop_turns

        # 至少运行 6 轮后才开始检测（给 Agent 热身时间）
        if current_turn < 6:
            return None

        # 冷却期检查: 如果最近 3 轮内已经触发过，不再重复
        last_signal_turn = getattr(self, '_last_stagnation_signal_turn', 0)
        if current_turn - last_signal_turn < 3:
            return None

        # 计算最近 N 轮的产出情况
        # 检查最近 5 轮内是否有 update_findings 调用
        recent_window = 5
        recent_history = s.tool_call_history[-recent_window:] if len(s.tool_call_history) >= recent_window else s.tool_call_history
        recent_tool_names = [t.get("name", "") for t in recent_history]

        # 如果最近 5 轮有 update_findings，说明还在产出，不触发
        if "update_findings" in recent_tool_names:
            return None

        # 如果总 findings 为 0 且轮次 < 8，可能还在初始探索阶段
        if len(s.findings) == 0 and current_turn < 8:
            return None

        # 检查是否有 findings 带 recorded_at_turn（更精确的判断）
        findings_with_turn = [f for f in s.findings if "recorded_at_turn" in f]
        if findings_with_turn:
            last_finding_turn = max(f["recorded_at_turn"] for f in findings_with_turn)
            turns_since_last = current_turn - last_finding_turn
            # 如果距离上次产出不到 5 轮，不触发
            if turns_since_last < 5:
                return None
        else:
            # 没有 turn 信息的 findings，用总轮次和 findings 数量粗略判断
            if len(s.findings) > 0 and current_turn < 10:
                return None

        # 触发停滞信号
        self._last_stagnation_signal_turn = current_turn

        # 构建信号文本（简洁，< 100 字符）
        turns_without = current_turn - (max(f["recorded_at_turn"] for f in findings_with_turn) if findings_with_turn else 0)
        signal = (
            f"\n\n---\n"
            f"📉 产出观察: 最近 {turns_without} 轮未产出新发现。"
            f"当前共 {len(s.findings)} 条 findings，已读 {len(s.sections_read)} 个 sections。"
        )
        return signal

    def _tool_detect_ai_signals(self, args: dict) -> str:
        """Phase 22: 调用程序化 AI 信号检测器。"""
        text = args.get("text", "")
        if not text:
            return "错误: 'text' 参数为空。请传入要检测的文本内容。"
        
        from core.deai_detector import detect_ai_signals
        result = detect_ai_signals(text)
        return result.summary()

    def _tool_verify_citations(self, args: dict) -> str:
        """Phase 22: 验证参考文献完整性和引用一致性。"""
        bib_content = args.get("bib_content", "")
        tex_content = args.get("tex_content", "")
        project_dir = args.get("project_dir", "")
        check_orphaned = args.get("check_orphaned", True)

        if not bib_content and not project_dir:
            return "错误: 请传入 bib_content（.bib 文件内容）或 project_dir（项目目录路径）。"

        from core.bib_verify import verify_citations
        result = verify_citations(
            bib_content=bib_content or None,
            tex_content=tex_content or None,
            project_dir=project_dir or None,
            check_orphaned=check_orphaned,
        )
        return result.summary()

    def _tool_recall_context(self, args: dict) -> str:
        """Phase 32: 从 offload store 回查之前卸载的完整内容。"""
        ref_id = args.get("ref_id", "")
        key = args.get("key", "")

        if ref_id:
            content = self.offload_store.recall(ref_id)
            if content:
                return f"[回查 {ref_id}] 完整内容 ({len(content)} chars):\n\n{content}"
            return f"[回查失败] 找不到 ref_id='{ref_id}'。请检查可用的 ref_id 列表。"
        elif key:
            content = self.offload_store.recall_by_key(key)
            if content:
                return f"[回查 '{key}'] 完整内容 ({len(content)} chars):\n\n{content}"
            return f"[回查失败] 找不到 key='{key}' 的卸载内容。"
        else:
            return "错误: 请传入 ref_id (如 'ref_003') 或 key (如 section 名)。"

    def _tool_done(self, args: dict) -> str:
        summary = args.get("summary", "")

        # Phase 50: Pre-Completion Check（小模型快速扫描遗漏）
        # 在 quality gate 之前执行——如果 Checker 发现明显盲区，作为 nudge 返回
        abstract = self.state.paper_sections.get("abstract", "")
        if not abstract:
            # 尝试模糊匹配 abstract
            for key in self.state.paper_sections:
                if "abstract" in key.lower():
                    abstract = self.state.paper_sections[key]
                    break
        checker_nudge = self.checker.check_pre_completion(
            abstract=abstract,
            findings=self.state.findings,
        )
        if checker_nudge:
            return f"__NUDGE__|[Checker 校验] {checker_nudge}"

        # 在返回前检查 completion quality gate
        gate_result = self._check_completion_gate()
        if gate_result:
            return f"__NUDGE__|{gate_result}"
        return f"__DONE__|{summary}"

    # ----------------------------------------------------------
    # 边界守护
    # ----------------------------------------------------------

    def check_doom_loop(self) -> str | None:
        """边界守护：接近/超过 max turns 时的行为。
        
        策略：
        - max_turns - 2: 注入"快收尾"提醒（通过 check_token_budget 机制注入 system msg）
        - max_turns + 2: 硬截断（给 Agent 额外 2 轮做完总结）
        
        返回 None 表示正常，返回 str 表示硬截断。
        """
        hard_limit = self.state.max_loop_turns + 2  # 给 2 轮缓冲总结
        if self.state.loop_turns >= hard_limit:
            return f"已达到硬性上限 ({hard_limit} 轮)。强制结束。"
        return None

    def check_soft_turn_limit(self) -> str | None:
        """认知自评提问（Phase 28: Agent 自主终止判断）。
        
        设计原则 (COGNITIVE_ANCHOR §4.3 约束-而非-控制):
        - 不告诉 Agent "你该收尾了"（那是控制）
        - 而是问 Agent "你准备好了吗？"（那是约束+信任）
        - Agent 可以回答 "还没有，我还需要 X" → 继续
        - Agent 可以回答 "准备好了" → 调 mark_complete
        
        触发时机：
        - 第 15 轮：首次自评（"你有足够信息了吗？"）
        - 第 25 轮：再次自评（此时明确告知资源消耗情况）
        - 第 40 轮：最后警告（给出剩余轮次的客观事实）
        
        hard limit (50 轮) 仅作灾难保底，正常不应触及。
        """
        turns = self.state.loop_turns
        
        if turns == 15:
            findings_count = len(self.state.findings)
            search_count = len(getattr(self, '_search_log', []))
            search_note = ""
            if search_count == 0 and findings_count > 0:
                search_note = (
                    "另外注意：你尚未使用 search_literature 查过外部文献——"
                    "你的判断完全基于论文自身的叙述，缺少外部校准。"
                )
            # Phase 46: 学科能力边界提示
            discipline_note = ""
            if not any(t.get("name") == "spawn_perspective" for t in self.state.tool_call_history):
                paper_text = " ".join(
                    content[:300] for content in self.state.paper_sections.values() if content
                ).lower()
                disc_signals = {
                    "统计/计量": ["propensity", "causal inference", "double machine learning", "semiparametric"],
                    "ML/深度学习": ["transformer", "neural network", "deep learning", "attention mechanism"],
                    "临床/流行病学": ["patient", "clinical trial", "randomized", "ehr", "electronic health"],
                }
                detected = [name for name, sigs in disc_signals.items() if sum(1 for s in sigs if s in paper_text) >= 2]
                if len(detected) >= 2:
                    discipline_note = (
                        f"这篇论文跨越了 {len(detected)} 个学科（{', '.join(detected)}）。"
                        f"问自己：你对每个学科的方法论判断是否同样有信心？"
                        f"如果某个学科你只能做表面判断，可以用 spawn_perspective 请独立专家审视。"
                    )
            return (
                f"[自评时刻] 你已完成 {turns} 轮思考，产出 {findings_count} 条发现。"
                f"问自己：我对这篇论文的核心方法论理解够了吗？我的主要假说验证完了吗？"
                f"{search_note}{discipline_note}"
                f"如果够了，用 mark_complete 结束；如果不够，说明你还需要验证什么，然后继续。"
            )
        elif turns == 25:
            findings_count = len(self.state.findings)
            tokens = self.state.total_tokens
            return (
                f"[自评时刻] 已用 {turns} 轮，{findings_count} 条发现，~{tokens} tokens。"
                f"你是否还有 high-priority 的未验证假说？如果所有关键问题已有答案，"
                f"继续深入的边际价值可能在递减。做出你的判断。"
            )
        elif turns == 40:
            return (
                f"[资源提示] 已用 {turns}/{self.state.max_loop_turns} 轮。"
                f"灾难保底上限为 {self.state.max_loop_turns} 轮。"
                f"请评估：你的核心发现是否已足够支撑一份有价值的审阅意见？"
            )
        return None

    def check_cognitive_output(self) -> str | None:
        """
        Phase 17: 认知产出催促器 (Cognitive Output Prompter)
        
        检测 Agent 是否陷入"只读不记"的认知模式——连续多轮 read_section
        而不 update_findings。当检测到这一模式时，注入 system message
        提醒 Agent "边读边记"。
        
        设计原则 (COGNITIVE_ANCHOR §4.3 约束-而非-控制):
        - 这不是控制 Agent 做什么——Agent 仍然可以选择继续读
        - 这模拟的是人类专家的"笔记习惯"——真正的审稿人不会读完
          整篇论文才开始记笔记
        - 目的是防止"延迟记录"导致压缩后失忆的认知退化模式
        
        触发条件:
        - 连续 3+ 轮只做 read_section（或其他非产出工具）而无 update_findings
        - findings 数量没有增长
        
        行为:
        - 第一次触发 (3轮): 温和提醒
        - 之后每 2 轮: 更强烈提醒
        - 不会阻止 Agent 继续读取——只是在 messages 中注入信号
        
        Returns:
            str | None: 催促消息（注入为 system msg），或 None（无需催促）
        """
        s = self.state
        
        # 检查 findings 是否增长
        current_findings = len(s.findings)
        if current_findings > s.last_findings_count:
            # Agent 产出了新发现，重置计数器
            s.consecutive_read_turns = 0
            s.last_findings_count = current_findings
            return None
        
        # findings 没增长，检查是否已经读了足够多的 sections
        # （只有在已读 sections > 0 的情况下才计数，避免初始化时误触发）
        if not s.sections_read:
            return None
        
        # 累加连续读取计数（由 loop 在每轮结束时调用）
        # 这里只做检查和返回消息
        threshold_first = 3   # 首次触发阈值
        threshold_repeat = 2  # 后续触发间隔
        
        if s.consecutive_read_turns < threshold_first:
            return None
        
        # 已达到首次阈值
        turns_since_first = s.consecutive_read_turns - threshold_first
        
        # 首次触发 或 每隔 threshold_repeat 轮再次触发
        if turns_since_first == 0 or (turns_since_first > 0 and turns_since_first % threshold_repeat == 0):
            sections_read_count = len(s.sections_read)
            
            if s.consecutive_read_turns == threshold_first:
                # 首次催促：温和
                return (
                    f"[认知提醒] 你已连续读了 {s.consecutive_read_turns} 轮 "
                    f"({sections_read_count} 个 sections) 但尚未记录任何发现。"
                    f"建议：边读边记——每读 2-3 个 section 就用 update_findings 记录初步印象，"
                    f"哪怕是暂定的 'needs_verification' 状态。"
                    f"这样即使后续 context 被压缩，你的关键观察也不会丢失。"
                )
            else:
                # 后续催促：更强烈
                return (
                    f"[认知警告] 你已连续 {s.consecutive_read_turns} 轮纯读取，"
                    f"仍有 0 条新发现。当前已读 {sections_read_count} 个 sections，"
                    f"早期内容正在被压缩——如果你现在才开始总结，可能已经丢失了重要细节。"
                    f"请立即用 update_findings 记录你到目前为止的核心观察，"
                    f"即使不完美也好过遗忘。"
                )
        
        return None

    def track_cognitive_output(self, tool_name: str):
        """
        Phase 17: 追踪每轮的工具使用类型，用于判断"只读不记"模式。
        
        由 loop 在处理完每个 tool_call 后调用。
        
        - 产出型工具 (update_findings, edit_section): 重置计数器
        - 读取型工具 (read_section, search_literature, review_findings): 增加计数器
        - 元认知型工具 (reflect_and_plan): 不计数（反思不算产出也不算纯读取）
        """
        s = self.state
        
        # 产出型工具：Agent 做了有意义的认知输出
        output_tools = {"update_findings", "edit_section"}
        # 中性工具：不改变计数
        neutral_tools = {"reflect_and_plan", "talk_to_user", "done", "mark_complete", "spawn_perspective"}
        
        if tool_name in output_tools:
            s.consecutive_read_turns = 0
            s.last_findings_count = len(s.findings)
        elif tool_name not in neutral_tools:
            # 读取型工具（read_section, search_literature, review_findings 等）
            # 只在轮次边界累加（避免同一轮多个 read_section 重复计数）
            # 实际累加在 loop 层的轮次边界做
            pass

    def increment_read_turn(self):
        """Phase 17: 由 loop 在一轮结束且该轮无产出时调用。"""
        self.state.consecutive_read_turns += 1

    def check_reflection_needed(self) -> str | None:
        """
        Phase 37+40+41: 反思催促器 (Reflection Nudge)
        
        检测 Agent 是否长时间未暂停反思——连续行动但从未"抬头看全局"。
        
        设计原则 (COGNITIVE_ANCHOR §4.3 约束-而非-控制):
        - 不强制 Agent 反思——只是提醒"你已经连续行动了一段时间"
        - 模拟人类专家的自然节奏：行动-行动-反思-行动-行动-反思
        - Phase 40: 增加第二触发条件——有 needs_verification findings 但 Agent 似乎要收尾
        - Phase 41: 增加第三触发条件——从未搜索外部文献但已有实质性产出
        
        触发条件:
        - 条件 A (Phase 37): 已读 4+ 个 sections 且从未调用 reflect_and_plan
        - 条件 B (Phase 40): 有 needs_verification findings + 距上次反思已过 4+ 轮
        - 条件 C (Phase 41): 从未搜索 + 已有 2+ findings + 已过 8+ 轮
        
        认知链 (条件 C):
            条件 C 催促反思 → 反思时镜子呈现"外部验证"缺失 → Agent 自主决定是否搜索
            这不是"你必须搜索"，而是"你即将结束但从未查过外部文献——要不要暂停看看？"
        
        Returns:
            str | None: 催促消息，或 None
        """
        s = self.state
        
        # === 条件 A: 首次反思催促（Phase 37 原有逻辑）===
        if not getattr(s, '_reflection_nudge_fired', False):
            # 如果 Agent 已经反思过，不需要首次催促
            if hasattr(self, '_reflection_log') and self._reflection_log:
                pass  # 跳过条件 A，继续检查条件 B
            elif len(s.sections_read) >= 4:
                s._reflection_nudge_fired = True
                return (
                    "[轻提醒] 你已经连续读了好几个 section 了。"
                    "要不要暂停一下，用 reflect_and_plan 看看全局？"
                    "——确认一下方向对不对、接下来该把精力放在哪里。"
                    "（这只是提醒，如果你觉得当前方向很清晰，继续行动也完全可以。）"
                )
        
        # === 条件 B: 追查缺口催促（Phase 40 新增）===
        # 触发条件：有 needs_verification findings + 距上次反思已过 4+ 轮 + 未触发过此催促
        if not getattr(s, '_verification_nudge_fired', False):
            unverified = [f for f in s.findings if f.get("status") == "needs_verification"]
            if unverified:
                # 计算距上次反思的轮次
                last_reflect_turn = 0
                if hasattr(self, '_reflection_log') and self._reflection_log:
                    last_reflect_turn = self._reflection_log[-1].get("turn", 0)
                
                turns_since_reflect = s.loop_turns - last_reflect_turn
                
                if turns_since_reflect >= 4:
                    s._verification_nudge_fired = True
                    return (
                        f"[追查提醒] 你有 {len(unverified)} 条发现标记为 needs_verification，"
                        f"但距离你上次反思已经过了 {turns_since_reflect} 轮。"
                        f"要不要用 reflect_and_plan 看看——这些怀疑是否值得追查？"
                        f"（一个好审稿人的 report 里不会有'我怀疑但没验证'的条目。）"
                    )
        
        # === 条件 C: 搜索缺失催促（Phase 41 新增）===
        # 触发条件：从未搜索 + 已有 2+ findings + 已过 8+ 轮 + 未触发过此催促
        # 设计原则 (§4.3): 呈现事实"你从未查过外部文献"，不命令"你必须搜索"
        # 认知链：条件 C 催促反思 → 反思时镜子呈现搜索缺失 → Agent 自主决定
        if not getattr(s, '_search_nudge_fired', False):
            search_count = len(getattr(self, '_search_log', []))
            if search_count == 0 and len(s.findings) >= 2 and s.loop_turns >= 8:
                s._search_nudge_fired = True
                return (
                    f"[外部校准提醒] 你已审阅了 {s.loop_turns} 轮、产出了 {len(s.findings)} 条发现，"
                    f"但尚未使用 search_literature 查过任何外部文献。"
                    f"要不要用 reflect_and_plan 暂停一下，看看哪些判断值得用外部文献校准？"
                    f"（这只是提醒——如果你的判断完全基于论文内部证据且你有信心，继续也可以。）"
                )
        
        return None

    def check_token_budget(self) -> str | None:
        """如果当前 context window 占用率接近危险区，返回警告；否则返回 None。
        
        Phase 16: 阈值从 90% 降至 80%，对齐 Anthropic 研究的
        "上下文腐烂" 结论——context 超过 80% 后模型注意力显著涣散。
        
        Phase 45 修正: 使用 last_prompt_tokens / context_window 作为信号，
        而非 total_tokens / token_budget。前者反映"当前注意力负载"，
        后者反映"累计成本"——两者是不同的问题。
        
        累计成本超限由 doom loop guard 的硬停处理（total_tokens > token_budget）。
        """
        # Phase 45: 基于当前 prompt 大小判断认知带宽压力
        context_ratio = self.state.last_prompt_tokens / self.state.context_window if self.state.context_window else 0
        if context_ratio > 0.8:
            remaining = self.state.context_window - self.state.last_prompt_tokens
            return (f"当前 context 占用 {context_ratio:.0%}（{self.state.last_prompt_tokens}/{self.state.context_window} tokens）。"
                    f"注意力可能开始涣散，请聚焦核心问题并尽快总结结论。")
        
        # 累计成本警告（仅在超过 budget 时触发一次性提醒，不重复）
        if self.state.total_tokens > self.state.token_budget and not hasattr(self, '_cost_warned'):
            self._cost_warned = True
            return f"累计 token 消耗已超过预算（{self.state.total_tokens}/{self.state.token_budget}）。建议尽快完成当前任务。"
        
        return None

    def _check_completion_gate(self) -> str | None:
        """
        Completion Quality Gate: 当 Agent 想结束时，检查是否有未完成的高优事项。

        这不是"控制 Agent 做什么"——而是设置一个质量标准：
        "如果你有标记为 high + needs_verification 的发现还没验证，你真的准备好了吗？"

        Agent 仍然可以选择忽略 nudge 再次调 done（Harness 不会无限循环拦截）。
        """
        unverified_high = [
            f for f in self.state.findings
            if f.get("priority") == "high" and f.get("status") == "needs_verification"
        ]
        if unverified_high:
            items = "; ".join(f["finding"][:60] for f in unverified_high[:3])
            return (
                f"你还有 {len(unverified_high)} 条高优先级发现标记为 needs_verification: "
                f"{items}。\n"
                f"建议：要么继续追查验证，要么将其降级/标记为 verified。"
                f"如果你确认可以结束，再次调用 done 即可。"
            )
        return None

    def increment_turn(self, usage: dict | None = None):
        """每轮 loop 结束时调用，更新统计。"""
        self.state.loop_turns += 1
        if usage:
            self.state.total_tokens += usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)

    def new_conversation_turn(self):
        """用户发了新消息，开始新的对话轮次。重置单轮 loop 计数。"""
        self.state.conversation_turns += 1
        self.state.loop_turns = 0  # 每轮对话重置 loop 计数器（防止累计触发 doom loop）

    # ----------------------------------------------------------
    # Phase 15: 会话结束时的记忆沉淀
    # ----------------------------------------------------------

    def end_session(self, paper_title: str = "", user_messages: list[str] | None = None):
        """
        会话结束时调用: 将当前会话的认知产出沉淀到跨会话记忆。

        职责:
            1. 构建 SessionRecord（压缩 findings 为摘要）
            2. 从 verified findings 中提取可积累的领域模式（Layer 2: WHAT）
            3. 从工具调用序列中提取程序性模式（Layer 3: HOW, Phase 54）
            4. 持久化到磁盘

        Args:
            paper_title: 论文标题（如果未提供，尝试从 paper_sections 推断）
            user_messages: 用户发送的消息列表（用于记录用户关注点）
        """
        if not self.state.findings:
            # 没有任何发现，不值得记录
            return

        # 确保 paper_id 存在
        if not self._paper_id and self.state.paper_sections:
            self._paper_id = MemoryStore.compute_paper_id(self.state.paper_sections)

        if not self._paper_id:
            return

        # 推断论文标题
        if not paper_title:
            # 尝试从 paper_sections 中找 title
            for key in self.state.paper_sections:
                if "title" in key.lower() or "abstract" in key.lower():
                    content = self.state.paper_sections[key]
                    # 取第一行非空文本作为标题
                    for line in content.split("\n"):
                        line = line.strip().strip("#").strip()
                        if line and len(line) > 10:
                            paper_title = line[:100]
                            break
                    if paper_title:
                        break
            if not paper_title:
                paper_title = f"Paper_{self._paper_id[:8]}"

        # 1. 构建 SessionRecord
        record = build_session_record(
            paper_id=self._paper_id,
            paper_title=paper_title,
            findings=self.state.findings,
            conversation_turns=self.state.conversation_turns,
            loop_turns=self.state.loop_turns,
            total_tokens=self.state.total_tokens,
            user_messages=user_messages,
        )
        self.memory.persist_session(record)

        # 2. 提取并积累领域模式（Layer 2: WHAT）
        patterns = extract_domain_patterns(self.state.findings, self._paper_id)
        for category, description in patterns:
            self.memory.add_or_reinforce_pattern(category, description, self._paper_id)

        # 3. Phase 54: 提取并积累程序性模式（Layer 3: HOW）
        tool_names = [t.get("name", "") for t in self.state.tool_call_history]
        procedural_patterns = extract_procedural_patterns(
            tool_call_history=tool_names,
            findings_count=len(self.state.findings),
            loop_turns=self.state.loop_turns,
            strategy_transitions=self._strategy_transitions if self._strategy_transitions else None,
        )
        for cat, desc, trigger, score in procedural_patterns:
            self.memory.add_or_reinforce_procedure(cat, desc, trigger, score)

        # 4. 持久化
        self.memory.save()

    # ----------------------------------------------------------
    # Context Window 管理 — Token Pipeline 核心
    # ----------------------------------------------------------

    def compress_messages(self, messages: list[dict], keep_recent: int = 6) -> list[dict]:
        """
        压缩 messages 列表以控制 context window 膨胀。
        
        策略（来自 COGNITIVE_ANCHOR §5.3 Token Pipeline）：
        - 保留 system prompt（始终完整）
        - 保留最近 keep_recent 组完整的 assistant+tool_result 交互
        - 更早的历史：压缩 tool_result 为摘要，保留 assistant 的 tool_call 元信息
        - 始终保留 user messages（对话连贯性关键）
        
        Phase 16: Adaptive keep_recent — 当 token 消耗接近 80% 阈值时，
        自动收紧到 keep_recent=4，释放更多空间给 Agent 的当前推理。
        
        这不是删除信息——Agent 的关键发现已在 state.findings 中（通过 format_context 注入），
        section digests 提供压缩后的结构化回溯，压缩掉的只是原始 section 文本的冗余副本。
        
        Args:
            messages: 原始 messages 列表（不会被 mutate）
            keep_recent: 保留最近多少组完整交互（默认 6，压力下自动降到 4）
            
        Returns:
            压缩后的 messages 列表（新列表）
        """
        # Phase 45: Adaptive compression — 基于当前 context window 占用率（而非累计消耗）
        # 这修正了 Phase 16 的设计缺陷：累计消耗在多轮对话中会快速超过 budget，
        # 导致 adaptive compression 过早触发最激进模式，但实际 context window 远未满。
        context_ratio = self.state.last_prompt_tokens / self.state.context_window if self.state.context_window else 0
        if context_ratio > 0.5:
            keep_recent = min(keep_recent, 4)  # context 超过 50%：收紧到 4
        if context_ratio > 0.7:
            keep_recent = min(keep_recent, 3)  # context 超过 70%：收紧到 3
        if len(messages) <= keep_recent * 2 + 2:
            # 太短，不需要压缩
            return messages
        
        # 找到所有 assistant messages 的位置（它们是交互组的开始）
        assistant_indices = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
        
        if len(assistant_indices) <= keep_recent:
            return messages
        
        # 确定压缩边界：keep_recent 组之前的都需要压缩
        compress_before_idx = assistant_indices[-keep_recent]
        
        # 构建压缩后的 messages
        compressed = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            
            if i >= compress_before_idx:
                # 在保留区内，完整保留
                compressed.append(msg)
                i += 1
                continue
            
            if msg.get("role") == "system":
                # system 始终保留
                compressed.append(msg)
                i += 1
            elif msg.get("role") == "user":
                # user messages 始终保留（对话语义关键）
                compressed.append(msg)
                i += 1
            elif msg.get("role") == "assistant":
                # 压缩 assistant msg: 保留 tool_call 的 name，去掉 arguments 细节
                compressed_assistant = self._compress_assistant_msg(msg)
                compressed.append(compressed_assistant)
                i += 1
            elif msg.get("role") == "tool":
                # 压缩 tool result: 长内容→摘要
                compressed_tool = self._compress_tool_result(msg)
                compressed.append(compressed_tool)
                i += 1
            else:
                compressed.append(msg)
                i += 1
        
        return compressed

    def _compress_assistant_msg(self, msg: dict) -> dict:
        """压缩 assistant message：保留 tool_call 元信息，精简 arguments。"""
        compressed = {"role": "assistant", "content": msg.get("content") or None}
        
        if "tool_calls" in msg:
            compressed_calls = []
            for tc in msg["tool_calls"]:
                func = tc.get("function", {})
                name = func.get("name", "unknown")
                # 保留 tool name + 精简参数（只保留 key 信息）
                args_str = func.get("arguments", "{}")
                try:
                    args = json.loads(args_str)
                    # 对 read_section 只保留 section 名
                    if name == "read_section":
                        short_args = json.dumps({"section": args.get("section", "?")}, ensure_ascii=False)
                    elif name == "update_findings":
                        short_args = json.dumps({
                            "finding": args.get("finding", "")[:80] + "...",
                            "priority": args.get("priority", "?"),
                        }, ensure_ascii=False)
                    elif name == "search_literature":
                        short_args = json.dumps({"query": args.get("query", "?")}, ensure_ascii=False)
                    elif name == "edit_section":
                        short_args = json.dumps({
                            "section": args.get("section", "?"),
                            "reason": args.get("reason", "")[:60],
                        }, ensure_ascii=False)
                    else:
                        # 其他工具：截断 arguments
                        short_args = args_str[:100] + "..." if len(args_str) > 100 else args_str
                except (json.JSONDecodeError, TypeError):
                    short_args = args_str[:100] if len(args_str) > 100 else args_str
                
                compressed_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": name, "arguments": short_args},
                })
            compressed["tool_calls"] = compressed_calls
        
        return compressed

    def _compress_tool_result(self, msg: dict) -> dict:
        """压缩 tool result：长文本→摘要。"""
        content = msg.get("content", "")
        tool_call_id = msg.get("tool_call_id", "")
        
        if len(content) <= 200:
            # 短结果不压缩
            return msg
        
        # 基于内容类型做不同的压缩
        if content.startswith("[注意]"):
            # 空壳 section 提示，保留完整（很短且重要）
            summary = content
        elif content.startswith("搜索 '"):
            # 搜索结果：只保留概要
            lines = content.split("\n")
            summary = lines[0] + f" [完整结果已压缩, 原文 {len(content)} 字符]"
        elif content.startswith("已记录发现"):
            # update_findings 的确认，完整保留
            summary = content
        elif content.startswith("发现回顾"):
            # review_findings 结果：保留前 200 字符
            summary = content[:200] + f"... [已压缩, 原文 {len(content)} 字符]"
        elif content.startswith("可用 sections"):
            # section list：完整保留（结构信息重要）
            summary = content
        else:
            # 默认：论文 section 内容 → 只保留前 150 字符 + 长度信息
            summary = (
                f"[历史读取, {len(content)} 字符] "
                + content[:150].replace("\n", " ")
                + "..."
            )
        
        return {"role": "tool", "tool_call_id": tool_call_id, "content": summary}


# ============================================================
# Section 分类 — 为 format_context 提供优先级信号
# ============================================================

# 核心审阅 section：通常包含论文的核心贡献和可审查的实质内容
_CORE_PATTERNS = re.compile(
    r"(abstract|introduction|method|result|experiment|finding|"
    r"empirical|analysis|conclusion|discussion|"
    r"main result|baseline|treatment effect|robustness)",
    re.IGNORECASE,
)

# 可跳过 section：对审稿几乎无价值
_SKIP_PATTERNS = re.compile(
    r"(reference|bibliography|acknowledge|appendix|"
    r"author|affiliation|supplementar|table of content)",
    re.IGNORECASE,
)


def _classify_section(name: str) -> str:
    """
    将 section 名称分类为 core/support/skip。
    
    - core: 审稿核心内容（方法、结果、结论）
    - skip: 几乎无需阅读（参考文献、致谢）
    - support: 其他（数据描述、相关工作、背景等）
    """
    if _SKIP_PATTERNS.search(name):
        return "skip"
    if _CORE_PATTERNS.search(name):
        return "core"
    return "support"


# ============================================================
# Phase 40: Findings 重叠检测 — 纯启发式，不调 LLM
# ============================================================

def _detect_finding_overlaps(findings: list[dict]) -> list[str]:
    """
    检测 findings 之间的文本重叠。
    
    设计原则:
    - 纯启发式（不调 LLM），零额外 API 成本
    - 目标：让 Agent 在反思时意识到自己在重复同一个问题
    - 核心信号：英文术语重叠（学术论文的关键概念几乎都是英文）
    - 使用 overlap coefficient (intersection / min) 对短文本更公平
    - 阈值：英文术语 overlap >= 70% 即报告（高阈值避免误报）
    
    Returns:
        描述重叠对的字符串列表（最多 3 条）
    """
    if len(findings) < 2:
        return []
    
    def _extract_en_terms(text: str) -> set[str]:
        """提取英文术语（4+ 字母，去停用词）。"""
        import re as _re
        en_words = set(_re.findall(r'[a-zA-Z]{4,}', text.lower()))
        stopwords = {'this', 'that', 'with', 'from', 'have', 'been', 'which', 'their',
                     'more', 'than', 'also', 'some', 'other', 'about', 'would', 'could',
                     'should', 'these', 'those', 'into', 'only', 'very', 'such', 'each',
                     'finding', 'section', 'paper', 'author', 'however', 'therefore',
                     'does', 'will', 'what', 'when', 'where', 'there', 'over', 'under',
                     'between', 'through', 'during', 'before', 'after', 'above', 'below'}
        return {w for w in en_words if w not in stopwords}
    
    overlaps = []
    for i in range(len(findings)):
        for j in range(i + 1, len(findings)):
            text_i = findings[i].get("finding", "")
            text_j = findings[j].get("finding", "")
            
            terms_i = _extract_en_terms(text_i)
            terms_j = _extract_en_terms(text_j)
            
            # 至少需要 3 个英文术语才有比较意义
            if len(terms_i) < 3 or len(terms_j) < 3:
                continue
            
            # Overlap coefficient: intersection / min(|A|, |B|)
            # 短文本的术语如果大部分出现在长文本中，就是重复
            intersection = terms_i & terms_j
            overlap_coeff = len(intersection) / min(len(terms_i), len(terms_j))
            
            if overlap_coeff >= 0.7:
                overlaps.append(
                    f"发现 #{i+1} 和 #{j+1} 高度重叠 (核心术语重合 {overlap_coeff:.0%})——"
                    f"它们可能是同一个问题的不同表述"
                )
    
    return overlaps[:3]


# ============================================================
# Phase 16: Section Digest Generator — 纯启发式，不调 LLM
# ============================================================

def _generate_section_digest(section_name: str, content: str) -> str:
    """
    为已读 section 生成一个 1-2 句话的结构化摘要。
    
    设计原则:
    - 纯启发式（不调 LLM），零额外 API 成本
    - 目标：让 Agent 在 section 原文被压缩出 messages 后，
      仍能回溯"这个 section 讲了什么"而不需要重新 read_section
    - 不是完美的摘要——是"够用的记忆锚点"
    
    策略:
    1. 提取第一句有意义的文本（通常是 section 的核心主张）
    2. 统计关键数字（表格行数、公式、引用数）
    3. 拼接为 ≤150 字符的摘要
    """
    if not content or len(content) < 50:
        return f"({len(content)} chars, 内容极少)"
    
    # 去掉 markdown 标题行
    lines = [l.strip() for l in content.split("\n") if l.strip() and not l.strip().startswith("#")]
    
    # 提取第一句有实质内容的文本（跳过空行和表格标记）
    first_sentence = ""
    for line in lines:
        # 跳过表格分隔符、图片引用、空白占位
        if line.startswith("|") or line.startswith("![") or line.startswith("---"):
            continue
        # 取第一句（句号/问号/感叹号截断）
        for end_char in ["。", ".", "?", "？", "!", "！"]:
            idx = line.find(end_char)
            if idx > 10:  # 至少有一些内容
                first_sentence = line[:idx + 1]
                break
        if first_sentence:
            break
        # 如果一整行没有句号，取前 100 字符
        if len(line) > 20:
            first_sentence = line[:100]
            break
    
    if not first_sentence:
        first_sentence = lines[0][:80] if lines else "无内容"
    
    # 统计特征
    features = []
    table_rows = sum(1 for l in content.split("\n") if l.strip().startswith("|"))
    if table_rows > 2:
        features.append(f"含{table_rows}行表格")
    
    num_count = len(re.findall(r'\d+\.\d+', content))
    if num_count > 5:
        features.append(f"~{num_count}个数值")
    
    # 组装 digest (≤150 chars)
    digest = first_sentence[:120]
    if features:
        digest += f" [{', '.join(features)}]"
    
    return digest[:150]
