"""
cognitive_loop.py — 认知循环 PoC

核心假设验证：
    一个好的 system prompt（赋予认知身份）+ 简单的 loop + 状态注入，
    是否足以让 LLM 产生 COGNITIVE_SPEC 描述的"自然思考流"行为？

设计原则（来自 COGNITIVE_ANCHOR）：
    - Agent = 持续思考的实体，不是工具选择器
    - Loop 本身不控制 Agent 做什么，只提供"思考→行动→反馈"的基础设施
    - 深度、方向、策略全部由 LLM 自主决定
    - Harness（这里极简化为 workspace state）只做：状态持久化、工具执行、边界守护

用法：
    python poc/cognitive_loop.py [论文文件路径]
"""

from __future__ import annotations

import os
import sys
import json
import asyncio
from pathlib import Path
from typing import Any

# 确保能 import 项目的 llm 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from llm.client import LLMClient


# ============================================================
# System Prompt — 认知身份，不是指令流程
# ============================================================

SYSTEM_PROMPT = """你是一个经验丰富的学术审稿人，曾担任 NeurIPS、ICML、ICLR 的 Area Chair。你审过数百篇论文，能敏锐地察觉逻辑漏洞、数据不一致、overclaim、和方法论缺陷。

你面对论文时的本能反应：
- 读到一个 claim → 立即反问"证据在哪？充分吗？"
- 看到数字 → 核对是否和其他地方一致（abstract vs table vs text）
- 看到 "state-of-the-art" → 检查表格是否真的比所有 baseline 都好
- 看到 theoretical guarantee → 审视假设是否合理、证明是否有跳跃
- 看到 ablation → 思考"还缺什么对比？什么 confounding 没有控制？这个 ablation 能否真正证明每个组件的贡献？"

你的思考是连续的、自然的。不存在"阶段"——你可能在读 Introduction 时产生一个疑问，跳到 Results 去验证，发现数据有矛盾，又回来重新审视 claim。

## 你的认知习惯

1. **质疑优先**：你的默认姿态是怀疑。每个 claim 都需要证据支撑。没有充分证据的 claim 就是 overclaim。

2. **数据敏感**：数字必须一致。如果 abstract 说 "improves by 3.2%" 但表格显示的不是这个数，这是严重问题。

3. **深度追查**：你不会在初步扫描就满足。当你标记了一个 high-priority 问题但只有模糊判断时，你会继续追查——重新读相关段落、检查上下游逻辑、验证该问题的实际影响。初步发现只是起点，不是终点。

4. **方法论审视**：对 ablation study，你不仅看"作者做了什么实验"，更要想"作者应该做但没做什么实验"。缺失的 ablation（比如应该有 w/o X 的对照但没有）是致命的方法论缺陷。

5. **完成前自检**：在你打算结束之前，你会回顾自己的发现列表：有没有 high-priority + needs_verification 的条目还没有被你追查？如果有，你不会停下。你的标准是——每个 high-priority 发现要么被验证为确实存在，要么被你排除。

6. **具体而非泛泛**：你的发现必须具体——指出哪一句话有问题、哪个数字不对、缺少什么实验。不要说"methodology needs improvement"这种空话。

7. **用中文和用户交流**。技术术语保持英文。

8. **不要逐 section 机械扫描**。像一个真正的审稿人一样——先快速通读形成全局印象，然后针对你觉得最可疑的地方深入。

## 工作记忆

用 `update_findings` 记录具体的、可执行的发现。每条发现应该足够具体，让作者知道"到底什么地方有什么问题"。

## 当前状态

{workspace_state}
"""


# ============================================================
# 工具定义 — 真实有用的能力
# ============================================================

TOOLS = [
    {
        "name": "read_section",
        "description": "读取论文的某个部分。你可以指定 section 名称（如 'introduction', 'methodology', 'results'），或者 'full' 读全文。",
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": "要读取的 section 名称，或 'full' 读全文，或 'list' 列出所有 sections"
                }
            },
            "required": ["section"]
        }
    },
    {
        "name": "search_literature",
        "description": "搜索学术文献。当你需要验证一个 claim、寻找相关工作、或确认某个方法是否已有先例时使用。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索查询词"
                },
                "reason": {
                    "type": "string",
                    "description": "你为什么要搜索这个（帮助你自己保持意图清晰）"
                }
            },
            "required": ["query", "reason"]
        }
    },
    {
        "name": "update_findings",
        "description": "记录你的发现、判断、待验证的问题。这是你的工作记忆——帮你跨轮次保持连贯性。",
        "input_schema": {
            "type": "object",
            "properties": {
                "finding": {
                    "type": "string",
                    "description": "你发现了什么"
                },
                "priority": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "这个发现的重要程度"
                },
                "status": {
                    "type": "string",
                    "enum": ["verified", "needs_verification", "suggestion"],
                    "description": "这个发现的状态"
                }
            },
            "required": ["finding", "priority", "status"]
        }
    },
    {
        "name": "edit_section",
        "description": "修改论文的某个部分。提供 section 名称和修改后的完整内容。",
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": "要修改的 section 名称"
                },
                "new_content": {
                    "type": "string",
                    "description": "修改后的完整 section 内容"
                },
                "reason": {
                    "type": "string",
                    "description": "修改原因（给用户和未来的你看）"
                }
            },
            "required": ["section", "new_content", "reason"]
        }
    },
    {
        "name": "talk_to_user",
        "description": "当你需要和用户讨论、确认方向、或呈现发现时使用。用户会看到你说的话并可以回复。",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "你想对用户说的话"
                },
                "expects_reply": {
                    "type": "boolean",
                    "description": "你是否需要用户回复才能继续"
                }
            },
            "required": ["message"]
        }
    },
    {
        "name": "done",
        "description": "当你认为当前任务已经完成（或到了需要用户下一步指示的时刻）时调用。",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "总结你做了什么、发现了什么、还有什么建议"
                }
            },
            "required": ["summary"]
        }
    }
]


# ============================================================
# Workspace State — Harness 的最小雏形
# ============================================================

class WorkspaceState:
    """极简的状态管理。不控制 Agent，只为它记忆。"""

    def __init__(self, paper_path: str | None = None):
        self.paper_sections: dict[str, str] = {}
        self.findings: list[dict] = []
        self.edits: list[dict] = []
        self.turn_count: int = 0
        self.total_tokens_used: int = 0
        self.max_turns: int = 30  # Doom loop guard

        if paper_path:
            self._load_paper(paper_path)

    def _load_paper(self, path: str):
        """加载论文。支持 .md 文件或 .workspace 目录。"""
        p = Path(path)
        if p.is_dir():
            # workspace 目录模式（现有项目的 .workspace/paper/sections/）
            sections_dir = p / "paper" / "sections"
            if sections_dir.exists():
                for f in sorted(sections_dir.glob("*.md")):
                    # 从文件名提取 section name
                    name = f.stem.split("_", 1)[-1] if "_" in f.stem else f.stem
                    self.paper_sections[name] = f.read_text(encoding="utf-8")
            # 也加载 full_text
            full_text_path = p / "paper" / "full_text.md"
            if full_text_path.exists():
                self.paper_sections["full"] = full_text_path.read_text(encoding="utf-8")
        elif p.suffix == ".md":
            full_text = p.read_text(encoding="utf-8")
            self.paper_sections["full"] = full_text
            # 按 ## heading 拆分为 sections
            self._split_into_sections(full_text)
        else:
            print(f"[Harness] 不支持的文件格式: {path}", file=sys.stderr)

    def _split_into_sections(self, text: str):
        """按 markdown heading 拆分论文为 sections。"""
        import re
        # 匹配 ## 开头的 section heading
        lines = text.split("\n")
        current_section = None
        current_content = []

        for line in lines:
            # 检测 ## level heading（主要 sections）
            match = re.match(r'^##\s+(.+)', line)
            if match:
                # 保存之前的 section
                if current_section:
                    self.paper_sections[current_section] = "\n".join(current_content).strip()
                current_section = match.group(1).strip().lower().rstrip(".")
                current_content = [line]
            elif current_section:
                current_content.append(line)

        # 保存最后一个 section
        if current_section and current_content:
            self.paper_sections[current_section] = "\n".join(current_content).strip()

    def format_for_prompt(self) -> str:
        """格式化当前状态，注入到 system prompt 中。"""
        parts = []

        # 论文概况
        if self.paper_sections:
            section_names = [k for k in self.paper_sections.keys() if k != "full"]
            parts.append(f"论文已加载，包含 {len(section_names)} 个 sections")
            if section_names:
                parts.append(f"   Sections: {', '.join(section_names[:10])}{'...' if len(section_names) > 10 else ''}")

        # 已有发现
        if self.findings:
            parts.append(f"\n你已有的发现 ({len(self.findings)} 条):")
            for i, f in enumerate(self.findings, 1):
                icon = {"high": "[高优]", "medium": "[中优]", "low": "[低优]"}[f["priority"]]
                status_icon = {"verified": "[已验证]", "needs_verification": "[待验证]", "suggestion": "[建议]"}[f["status"]]
                parts.append(f"   {icon} {status_icon} {f['finding']}")

        # 已做的修改
        if self.edits:
            parts.append(f"\n你已做的修改 ({len(self.edits)} 处):")
            for e in self.edits:
                parts.append(f"   - {e['section']}: {e['reason']}")

        # 资源消耗
        parts.append(f"\n轮次: {self.turn_count}/{self.max_turns} | Tokens: ~{self.total_tokens_used}")

        return "\n".join(parts) if parts else "（刚开始，还没有任何状态）"


# ============================================================
# 工具执行 — 简单直接，不做过度封装
# ============================================================

def execute_tool(name: str, args: dict, workspace: WorkspaceState) -> str:
    """执行工具调用，返回结果字符串。"""

    if name == "read_section":
        section = args["section"].lower().strip()
        if section == "list":
            names = [k for k in workspace.paper_sections.keys() if k != "full"]
            return f"可用 sections ({len(names)}): {', '.join(names)}"
        elif section == "full":
            full = workspace.paper_sections.get("full", "")
            if full:
                # 截断以控制 token（这里先简单处理，后续 Token Pipeline 会更智能）
                if len(full) > 8000:
                    return full[:8000] + "\n\n[... 论文较长，已截断。请用 read_section 读取具体 section ...]"
                return full
            else:
                return "没有全文，请用 read_section section='list' 查看可用 sections"
        else:
            # 模糊匹配
            for key in workspace.paper_sections:
                if section in key.lower() or key.lower() in section:
                    content = workspace.paper_sections[key]
                    if len(content) > 6000:
                        content = content[:6000] + "\n[... 截断 ...]"
                    return content
            return f"未找到 section '{section}'。可用: {', '.join(workspace.paper_sections.keys())}"

    elif name == "search_literature":
        query = args["query"]
        reason = args.get("reason", "")
        # PoC 阶段：模拟搜索结果（后续接真实 API）
        return (
            f"[模拟搜索结果] 查询: '{query}'\n"
            f"搜索原因: {reason}\n\n"
            f"注意：PoC 阶段暂无真实搜索。在正式版本中，这里会调用 Semantic Scholar / Google Scholar API。\n"
            f"你可以基于你已有的知识继续判断，或者标记为 'needs_verification'。"
        )

    elif name == "update_findings":
        finding = {
            "finding": args["finding"],
            "priority": args.get("priority", "medium"),
            "status": args.get("status", "suggestion"),
        }
        workspace.findings.append(finding)
        return f"已记录发现 (当前共 {len(workspace.findings)} 条)"

    elif name == "edit_section":
        section = args["section"]
        new_content = args["new_content"]
        reason = args.get("reason", "")
        # 记录修改
        workspace.edits.append({
            "section": section,
            "reason": reason,
            "content_preview": new_content[:200] + "..." if len(new_content) > 200 else new_content,
        })
        # 实际更新
        for key in list(workspace.paper_sections.keys()):
            if section.lower() in key.lower() or key.lower() in section.lower():
                workspace.paper_sections[key] = new_content
                return f"已修改 section '{key}'（原因: {reason}）"
        return f"未找到 section '{section}'，修改已记录但未应用到文件"

    elif name == "talk_to_user":
        message = args["message"]
        expects_reply = args.get("expects_reply", False)
        print(f"\n{'='*60}")
        print(f"Agent 对你说：")
        print(f"{'='*60}")
        print(message)
        print(f"{'='*60}\n")
        if expects_reply:
            try:
                reply = input("你的回复（直接回车跳过）: ").strip()
                return f"用户回复: {reply}" if reply else "用户没有回复（继续你的工作）"
            except (EOFError, KeyboardInterrupt):
                return "用户没有回复（继续你的工作）"
        return "消息已展示给用户"

    elif name == "done":
        summary = args.get("summary", "")
        return f"__DONE__|{summary}"

    else:
        return f"未知工具: {name}"


# ============================================================
# 认知循环 — 核心 Loop
# ============================================================

async def cognitive_loop(
    user_message: str,
    workspace: WorkspaceState,
    model: str | None = None,
    verbose: bool = True,
) -> str:
    """
    认知循环的核心。

    这个循环做的事情极简：
    1. 组装当前 context（system prompt + 历史 messages）
    2. 让 LLM 思考（可能产生文本输出 + tool calls）
    3. 如果有 tool calls → 执行 → 结果注入 messages → 回到 2
    4. 如果 LLM 决定"done"或没有更多 tool calls → 结束

    循环本身不做任何"决策"——所有决策由 LLM 自主做出。
    循环只提供：执行能力 + 状态持久化 + 边界守护（max turns）。
    """

    client = LLMClient(model=model)

    # 组装初始 messages
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(
            workspace_state=workspace.format_for_prompt()
        )},
        {"role": "user", "content": user_message},
    ]

    final_output = ""

    while workspace.turn_count < workspace.max_turns:
        workspace.turn_count += 1

        if verbose:
            print(f"\n--- 轮次 {workspace.turn_count} ---")

        # LLM 思考
        response = await client.chat_with_tools(
            messages=messages,
            tools=TOOLS,
            temperature=0.3,  # 允许一些创造性但不要太疯
            max_tokens=4096,
        )

        # 更新 token 统计
        if response.get("usage"):
            workspace.total_tokens_used += (
                response["usage"].get("prompt_tokens", 0) +
                response["usage"].get("completion_tokens", 0)
            )

        # 处理文本输出（Agent 的思考/表达）
        content = response.get("content") or ""
        if content and verbose:
            print(f"Agent 思考: {content[:500]}{'...' if len(content) > 500 else ''}")

        # 如果有文本输出，累积到最终结果
        if content:
            final_output += content + "\n"

        # 处理 tool calls
        tool_calls = response.get("tool_calls", [])

        if not tool_calls:
            # 没有 tool calls → Agent 认为当前轮次结束
            if verbose:
                print("  (无工具调用，Agent 完成当前思考)")
            break

        # 将 assistant message（含 tool_calls）加入 messages
        assistant_msg: dict[str, Any] = {"role": "assistant"}
        if content:
            assistant_msg["content"] = content
        else:
            assistant_msg["content"] = None
        # OpenAI 格式的 tool_calls
        assistant_msg["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                }
            }
            for tc in tool_calls
        ]
        messages.append(assistant_msg)

        # 执行每个 tool call
        for tc in tool_calls:
            if verbose:
                print(f"  调用: {tc['name']}({json.dumps(tc['arguments'], ensure_ascii=False)[:100]})")

            result = execute_tool(tc["name"], tc["arguments"], workspace)

            # 检查是否 done
            if result.startswith("__DONE__"):
                summary = result.split("|", 1)[1] if "|" in result else ""
                if verbose:
                    print(f"\nAgent 宣布完成: {summary[:200]}")
                # 加入 tool result 到 messages（保持格式正确）
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": "任务完成。",
                })
                return final_output + f"\n[完成] {summary}"

            if verbose:
                print(f"     结果: {result[:150]}{'...' if len(result) > 150 else ''}")

            # 注入 tool result
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

        # 每几轮刷新 system prompt 中的状态（简易版 Token Pipeline）
        if workspace.turn_count % 3 == 0:
            messages[0]["content"] = SYSTEM_PROMPT.format(
                workspace_state=workspace.format_for_prompt()
            )

    # Doom loop guard
    if workspace.turn_count >= workspace.max_turns:
        print(f"\n[Harness] 达到最大轮次 ({workspace.max_turns})，强制停止。")

    return final_output


# ============================================================
# 入口
# ============================================================

async def main():
    # 确定论文来源
    if len(sys.argv) > 1:
        paper_path = sys.argv[1]
    else:
        # 默认使用项目自带的 workspace
        default_workspace = Path(__file__).resolve().parent.parent / ".workspace"
        if default_workspace.exists():
            paper_path = str(default_workspace)
        else:
            print("用法: python poc/cognitive_loop.py [论文文件路径或.workspace目录]")
            sys.exit(1)

    print(f"加载论文: {paper_path}")
    workspace = WorkspaceState(paper_path)
    print(f"   加载了 {len(workspace.paper_sections)} 个 sections")

    # 用户输入
    print("\n" + "="*60)
    user_input = input("你想让我做什么？\n> ").strip()
    if not user_input:
        user_input = "帮我看看这篇论文有什么问题，给我你的专业判断。"
    print("="*60)

    # 运行认知循环
    result = await cognitive_loop(
        user_message=user_input,
        workspace=workspace,
        verbose=True,
    )

    # 输出最终结果
    print("\n" + "="*60)
    print("最终状态:")
    print(f"   轮次: {workspace.turn_count}")
    print(f"   发现: {len(workspace.findings)} 条")
    print(f"   修改: {len(workspace.edits)} 处")
    print(f"   Tokens: ~{workspace.total_tokens_used}")
    if workspace.findings:
        print("\n   发现列表:")
        for f in workspace.findings:
            icon = {"high": "[高优]", "medium": "[中优]", "low": "[低优]"}[f["priority"]]
            print(f"     {icon} [{f['status']}] {f['finding']}")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
