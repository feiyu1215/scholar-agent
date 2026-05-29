"""
core/checker.py — 认知校验层 (Phase 50: Cognitive Layering)

设计原则:
    Agent 的认知循环 (loop.py) 用大模型做深度推理。
    Checker 用小模型做快速校验——就像人类专家改完一段话后"扫一眼"确认没有低级错误。

    这不是 pipeline（不是"大模型写→小模型检"的串行流程）。
    这是认知分层——System 1 (快速直觉校验) 辅助 System 2 (深度推理)。

职责:
    1. Post-Edit Check: edit_section 后，快速检查修改是否引入了新问题
    2. Pre-Completion Check: mark_complete 前，快速扫描是否有明显遗漏
    3. Consistency Check: 检查 findings 和论文内容的一致性

设计约束:
    - Checker 不改变 Agent 的决策——它只产出"提醒"注入到 tool_result 中
    - Checker 用小模型（成本 < 主模型的 1/10）
    - Checker 的输出是简短的（< 200 字），不占用大量 context
    - 如果 Checker 调用失败，静默降级（不阻塞主循环）
    - loop.py 和 identity.py 不需要知道 Checker 的存在

架构关系:
    loop.py → harness.execute_tool("edit_section", ...) → harness 内部触发 checker
    Checker 的结果被 append 到 tool_result 中返回给 Agent
    Agent 看到的是一个增强版的 tool_result（包含校验反馈）
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Optional

from llm.client import LLMClient


# ============================================================
# Checker Configuration
# ============================================================

# 小模型：用于快速校验，成本极低
CHECKER_MODEL = os.environ.get("LLM_MODEL_CHECKER", "gpt-4.1-mini")

# 校验的 token 上限（保持轻量）
CHECKER_MAX_TOKENS = 300

# 是否启用 Checker（可通过环境变量关闭）
CHECKER_ENABLED = os.environ.get("SCHOLAR_CHECKER_ENABLED", "1") == "1"


# ============================================================
# Checker Prompts — 极简，聚焦于一个判断
# ============================================================

# --- 学术审稿场景 (默认) ---

POST_EDIT_CHECK_PROMPT = """你是一个快速校验器。你的任务极其简单：检查一段修改后的学术文本是否引入了新问题。

修改原因: {reason}

修改后的文本:
---
{new_content}
---

请在 3 句话内回答：
1. 修改是否引入了逻辑不一致或事实错误？（是/否 + 一句话说明）
2. 是否有明显的 AI 写作痕迹？（是/否）
3. 是否与修改原因一致？（是/否）

如果全部通过，只回复"✓ PASS"。如果有问题，简述问题（不超过 2 句话）。"""

PRE_COMPLETION_CHECK_PROMPT = """你是一个快速校验器。审稿人即将结束审阅。请检查以下审阅发现是否有明显遗漏。

论文摘要:
{abstract}

审稿人的发现 ({findings_count} 条):
{findings_summary}

请在 2 句话内回答：
1. 这些发现是否覆盖了论文的核心 claim？（是/否）
2. 是否有明显的审阅盲区？（如：只看了方法没看结果，或只看了逻辑没看数据）

如果没有明显遗漏，只回复"✓ PASS"。如果有盲区，一句话指出。"""

CONSISTENCY_CHECK_PROMPT = """你是一个快速校验器。检查以下审稿发现是否与论文原文一致。

论文原文片段:
---
{section_text}
---

审稿发现:
{finding}

请回答：这条发现引用的证据是否与原文一致？（是/否 + 一句话说明）
如果一致，只回复"✓ CONSISTENT"。"""

# --- Persona-Adaptive Prompts (Phase 55) ---
# 当 task_context 非空时，使用这些模板替代上面的学术默认版本

POST_EDIT_CHECK_PROMPT_GENERIC = """你是一个快速校验器。检查一段修改后的{task_domain}文本是否引入了新问题。

修改原因: {reason}

修改后的文本:
---
{new_content}
---

请在 3 句话内回答：
1. 修改是否引入了逻辑不一致或明显错误？（是/否 + 一句话说明）
2. 是否与修改原因一致？（是/否）

如果全部通过，只回复"✓ PASS"。如果有问题，简述问题（不超过 2 句话）。"""

PRE_COMPLETION_CHECK_PROMPT_GENERIC = """你是一个快速校验器。{reviewer_role}即将结束审阅。请检查以下发现是否有明显遗漏。

内容概要:
{abstract}

发现 ({findings_count} 条):
{findings_summary}

请在 2 句话内回答：
1. 这些发现是否覆盖了内容的核心问题？（是/否）
2. 是否有明显的审阅盲区？

如果没有明显遗漏，只回复"✓ PASS"。如果有盲区，一句话指出。"""

CONSISTENCY_CHECK_PROMPT_GENERIC = """你是一个快速校验器。检查以下发现是否与原始内容一致。

原始内容片段:
---
{section_text}
---

发现:
{finding}

请回答：这条发现引用的证据是否与原文一致？（是/否 + 一句话说明）
如果一致，只回复"✓ CONSISTENT"。"""


# Persona → task context 映射
PERSONA_TASK_CONTEXTS: dict[str, dict[str, str]] = {
    "scholar": {
        "task_domain": "学术",
        "reviewer_role": "审稿人",
    },
    "code_reviewer": {
        "task_domain": "代码",
        "reviewer_role": "代码审阅者",
    },
}


# ============================================================
# Checker Class
# ============================================================

class CognitiveChecker:
    """
    认知校验器：用小模型做快速校验，辅助大模型的深度推理。
    
    设计：
    - 同步接口（内部用 asyncio 驱动），方便 Harness 调用
    - 失败静默降级（不阻塞主循环）
    - 结果缓存（同一内容不重复校验）
    - Phase 55: Persona 适配 — 根据当前 persona 动态选择 prompt 模板
    """
    
    def __init__(self, model: str = CHECKER_MODEL, persona: str = "scholar", session_model_mgr=None):
        """
        Args:
            model: Checker 使用的模型 ID（fallback）
            persona: 当前 persona（影响 prompt 模板）
            session_model_mgr: Optional SessionModelManager for Phase 4 model assignment.
                Priority: session_model_mgr > model param > env var LLM_MODEL_CHECKER.
        """
        if session_model_mgr is not None:
            resolved = session_model_mgr.resolve_model_for_role("checker")
            self._model = resolved if resolved is not None else model
        else:
            self._model = model
        self._client: Optional[LLMClient] = None
        self._enabled = CHECKER_ENABLED
        self._persona = persona
        self._task_context = PERSONA_TASK_CONTEXTS.get(persona, {})
        # 统计
        self.total_checks = 0
        self.total_passes = 0
        self.total_warnings = 0
        self.total_tokens_used = 0
    
    def set_persona(self, persona: str):
        """动态切换 persona（用于运行时切换场景）。"""
        self._persona = persona
        self._task_context = PERSONA_TASK_CONTEXTS.get(persona, {})
    
    @property
    def client(self) -> LLMClient:
        """Lazy init — 只在第一次使用时创建 client。"""
        if self._client is None:
            self._client = LLMClient(model=self._model)
        return self._client
    
    def check_edit(self, new_content: str, reason: str) -> Optional[str]:
        """
        Post-Edit Check: 修改后快速校验。
        
        Args:
            new_content: 修改后的文本
            reason: 修改原因
            
        Returns:
            None 如果通过（或 Checker 禁用/失败）
            str 如果有问题（简短的警告文本）
        """
        if not self._enabled:
            return None
        
        # 截断过长内容（Checker 不需要看全文）
        content_preview = new_content[:3000] if len(new_content) > 3000 else new_content
        
        # Phase 55: 根据 persona 选择 prompt 模板
        if self._task_context and self._persona != "scholar":
            prompt = POST_EDIT_CHECK_PROMPT_GENERIC.format(
                task_domain=self._task_context.get("task_domain", "通用"),
                reason=reason[:200],
                new_content=content_preview,
            )
        else:
            prompt = POST_EDIT_CHECK_PROMPT.format(
                reason=reason[:200],
                new_content=content_preview,
            )
        
        result = self._run_check(prompt)
        if result is None:
            return None
        
        self.total_checks += 1
        if "PASS" in result.upper() or "✓" in result:
            self.total_passes += 1
            return None  # 通过，不注入额外信息
        else:
            self.total_warnings += 1
            return f"\n[Checker 提醒] {result.strip()}"
    
    def check_pre_completion(
        self,
        abstract: str,
        findings: list[dict],
    ) -> Optional[str]:
        """
        Pre-Completion Check: 结束前快速扫描遗漏。
        
        Args:
            abstract: 论文摘要（或代码概要等内容摘要）
            findings: 当前所有 findings
            
        Returns:
            None 如果通过
            str 如果有明显遗漏
        """
        if not self._enabled:
            return None
        
        # 构建 findings 摘要（只取前 8 条的核心信息）
        findings_lines = []
        for i, f in enumerate(findings[:8], 1):
            priority = f.get("priority", "?")
            finding_text = f.get("finding", "")[:100]
            findings_lines.append(f"  {i}. [{priority}] {finding_text}")
        findings_summary = "\n".join(findings_lines) if findings_lines else "(无发现)"
        
        # Phase 55: 根据 persona 选择 prompt 模板
        if self._task_context and self._persona != "scholar":
            prompt = PRE_COMPLETION_CHECK_PROMPT_GENERIC.format(
                reviewer_role=self._task_context.get("reviewer_role", "审阅者"),
                abstract=abstract[:1500],
                findings_count=len(findings),
                findings_summary=findings_summary,
            )
        else:
            prompt = PRE_COMPLETION_CHECK_PROMPT.format(
                abstract=abstract[:1500],
                findings_count=len(findings),
                findings_summary=findings_summary,
            )
        
        result = self._run_check(prompt)
        if result is None:
            return None
        
        self.total_checks += 1
        if "PASS" in result.upper() or "✓" in result:
            self.total_passes += 1
            return None
        else:
            self.total_warnings += 1
            return result.strip()
    
    def check_consistency(
        self,
        section_text: str,
        finding: str,
    ) -> Optional[str]:
        """
        Consistency Check: 验证 finding 与原文是否一致。
        
        Args:
            section_text: 原文片段（论文或代码等）
            finding: 发现文本
            
        Returns:
            None 如果一致（或 Checker 禁用/失败）
            str 如果不一致
        """
        if not self._enabled:
            return None
        
        # Phase 55: 根据 persona 选择 prompt 模板
        if self._task_context and self._persona != "scholar":
            prompt = CONSISTENCY_CHECK_PROMPT_GENERIC.format(
                section_text=section_text[:2000],
                finding=finding[:300],
            )
        else:
            prompt = CONSISTENCY_CHECK_PROMPT.format(
                section_text=section_text[:2000],
                finding=finding[:300],
            )
        
        result = self._run_check(prompt)
        if result is None:
            return None
        
        self.total_checks += 1
        if "CONSISTENT" in result.upper() or "✓" in result:
            self.total_passes += 1
            return None
        else:
            self.total_warnings += 1
            return result.strip()
    
    def stats(self) -> dict:
        """返回 Checker 的运行统计。"""
        return {
            "enabled": self._enabled,
            "model": self._model,
            "total_checks": self.total_checks,
            "total_passes": self.total_passes,
            "total_warnings": self.total_warnings,
            "total_tokens_used": self.total_tokens_used,
            "warning_rate": (
                f"{self.total_warnings / self.total_checks * 100:.1f}%"
                if self.total_checks > 0 else "N/A"
            ),
        }
    
    def _run_check(self, prompt: str) -> Optional[str]:
        """
        执行一次校验调用。失败时静默返回 None。
        
        内部处理 async/sync 转换：
        - 如果已在 event loop 中（被 async 代码调用），用 asyncio.ensure_future
        - 如果不在 event loop 中，用 asyncio.run
        """
        try:
            loop = asyncio.get_running_loop()
            # 已在 async 上下文中 — 创建 task 并等待
            # 但由于 Harness.execute_tool 是同步的，我们需要用 thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self._sync_check, prompt)
                return future.result(timeout=45)  # 45 秒超时（API 高负载时需要更长）
        except RuntimeError:
            # 没有 running loop — 直接 asyncio.run
            return self._sync_check(prompt)
        except Exception as e:
            # TimeoutError 或其他异常 — 静默降级
            print(f"  [Checker 超时降级] {type(e).__name__}: {e}", file=sys.stderr)
            return None
    
    def _sync_check(self, prompt: str) -> Optional[str]:
        """同步执行校验（在新 event loop 中）。"""
        try:
            result = asyncio.run(self._async_check(prompt))
            return result
        except Exception as e:
            # 静默降级
            print(f"  [Checker 降级] {type(e).__name__}: {e}", file=sys.stderr)
            return None
    
    async def _async_check(self, prompt: str) -> str:
        """实际的 async 校验调用。"""
        result = await self.client.chat(
            system="你是一个快速校验器。简洁回答，不超过 3 句话。",
            user=prompt,
            temperature=0.1,
            max_tokens=CHECKER_MAX_TOKENS,
            model=self._model,
        )
        # 统计 token（粗略估计）
        self.total_tokens_used += len(prompt) // 4 + len(result) // 4
        return result
