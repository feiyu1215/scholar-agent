"""
core/skillx_integration.py — SkillX 集成层 (Phase 3 → Harness)

将 SkillX 三层技能体系 (core/skills/) 接入审稿流程:
  1. 初始化 UnifiedSkillRegistry + SkillSelector + ToolGroupManager
  2. 在 Phase 转换时自动切换激活 ToolGroup
  3. 提供 apply_skill tool 让 Agent 主动触发 Functional/Atomic Skill
  4. 在 Context 组装时注入 SkillX 可用能力提示

设计原则:
  - Kill Switch 守卫: SCHOLAR_GODEL_SKILLX (默认开, "0" 降级)
  - Graceful Degradation: 任何 SkillX 失败不影响核心审稿流程
  - 向后兼容: 旧 SkillRegistry 继续独立工作, SkillX 是增量能力
  - 场景驱动: Phase 感知的 Skill 选择, 不是全量加载
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ==============================================================
# SkillX 系统初始化器
# ==============================================================

class SkillXIntegration:
    """SkillX 集成管理器 — Harness 持有此实例。

    职责:
    - 初始化并持有 UnifiedSkillRegistry, SkillSelector, ToolGroupManager, SkillExecutor
    - 提供 on_phase_transition() hook 用于切换 ToolGroup
    - 提供 execute_skill() 方法供 tool_apply_skill 调用
    - 提供 get_skill_hints() 方法供 ContextAssembler 使用
    - 所有方法都有 try/except 保护, 失败不影响调用方
    """

    def __init__(
        self,
        legacy_registry=None,
        handler_loader=None,
        event_bus=None,
    ):
        """初始化 SkillX 体系。

        Args:
            legacy_registry: 旧 SkillRegistry 实例 (用于桥接)
            handler_loader: SkillHandlerLoader 实例 (桥接 Action Skills)
            event_bus: EventBus 实例 (用于发布执行事件)
        """
        from core.skills.bridge import UnifiedSkillRegistry
        from core.skills.selector import SkillSelector
        from core.skills.executor import SkillExecutor
        from core.skills.tool_group import ToolGroupManager

        # 加载原生经济学 Skills
        native_skills = self._load_economics_skills()

        # 构建统一注册表
        self.unified_registry = UnifiedSkillRegistry(
            skillx_skills=native_skills,
            legacy_registry=legacy_registry,
            handler_loader=handler_loader,
        )

        # 构建 Selector (使用统一注册表的全部 Skills)
        self.selector = SkillSelector(self.unified_registry.all_skills())

        # 构建 ToolGroup Manager
        self.tool_group_manager = ToolGroupManager()
        self._setup_tool_groups()

        # 构建 Executor
        self.executor = SkillExecutor(event_bus=event_bus)

        # 当前激活 Phase (用于 SkillContext 构建)
        self._current_phase: str = "orientation"

        native_count = len(native_skills)
        adapted_count = len(self.unified_registry.adapted_skills())
        total = native_count + adapted_count
        logger.info(
            "[SkillX] Initialized: %d native + %d adapted = %d total skills, "
            "%d tool groups",
            native_count, adapted_count, total,
            len(self.tool_group_manager.all_group_names),
        )

    def _load_economics_skills(self) -> list:
        """加载 core/skills/economics/ 中的原生 SkillX Skills。"""
        skills = []
        try:
            from core.skills.economics.planning import ReviewPlanningSkill
            skills.append(ReviewPlanningSkill())
        except Exception as exc:
            logger.warning("[SkillX] Failed to load ReviewPlanningSkill: %s", exc)

        try:
            from core.skills.economics.functional import (
                MethodologyAnalysisSkill,
                StatisticalValidationSkill,
                CitationVerificationSkill,
                LogicCoherenceSkill,
            )
            skills.extend([
                MethodologyAnalysisSkill(),
                StatisticalValidationSkill(),
                CitationVerificationSkill(),
                LogicCoherenceSkill(),
            ])
        except Exception as exc:
            logger.warning("[SkillX] Failed to load Functional skills: %s", exc)

        try:
            from core.skills.economics.atomic import (
                ExtractNumericClaimSkill,
                CompareWithDomainNormSkill,
            )
            skills.extend([
                ExtractNumericClaimSkill(),
                CompareWithDomainNormSkill(),
            ])
        except Exception as exc:
            logger.warning("[SkillX] Failed to load Atomic skills: %s", exc)

        try:
            from core.skills.economics.math_audit import AppendixMathAuditSkill
            skills.append(AppendixMathAuditSkill())
        except Exception as exc:
            logger.warning("[SkillX] Failed to load AppendixMathAuditSkill: %s", exc)

        # Phase 9A: 表格处理 Skills (跨表对比 + 一致性验证)
        try:
            from core.skills.multimodal import (
                TableExtractionSkill,
                TableConsistencySkill,
            )
            skills.extend([
                TableExtractionSkill(),
                TableConsistencySkill(),
            ])
        except Exception as exc:
            logger.warning("[SkillX] Failed to load Phase 9A Table skills: %s", exc)

        return skills

    def _setup_tool_groups(self) -> None:
        """创建默认 ToolGroup 结构并自动分配 Skills。"""
        from core.skills.base import SkillLevel

        # 1. 创建 basic 组 (Atomic skills, 始终激活)
        self.tool_group_manager.create_group(
            name="basic",
            description="基础 Atomic Skills (数值提取、领域对比)",
            is_basic=True,
        )

        # 2. 创建 Phase 相关组
        self.tool_group_manager.create_group(
            name="methodology_analysis",
            description="方法论分析 (DID/IV/RDD 检查)",
        )
        self.tool_group_manager.create_group(
            name="statistical_validation",
            description="统计检验验证",
        )
        self.tool_group_manager.create_group(
            name="citation_verification",
            description="引文格式与一致性检查",
        )
        self.tool_group_manager.create_group(
            name="logic_coherence",
            description="逻辑连贯性检查",
        )
        self.tool_group_manager.create_group(
            name="structure_analysis",
            description="结构分析与快速扫描",
        )
        self.tool_group_manager.create_group(
            name="synthesis_scoring",
            description="综合评分与建议生成",
        )
        self.tool_group_manager.create_group(
            name="table_processing",
            description="表格提取与跨表一致性验证 (Phase 9A)",
        )

        # 3. 将 Skills 自动分配到组
        for skill in self.unified_registry.all_skills():
            self.tool_group_manager.auto_assign(skill)

    # ----------------------------------------------------------
    # Phase 转换 Hook
    # ----------------------------------------------------------

    def on_phase_transition(self, new_phase: str) -> None:
        """Phase 转换时调用 — 切换激活的 ToolGroup。

        Args:
            new_phase: 新 Phase 名称 (小写, 如 "deep_review")
        """
        try:
            self._current_phase = new_phase
            activated = self.tool_group_manager.activate_for_phase(new_phase)
            logger.info(
                "[SkillX] Phase '%s' -> activated groups: %s",
                new_phase, activated,
            )
        except Exception as exc:
            logger.warning(
                "[SkillX] on_phase_transition failed for '%s': %s",
                new_phase, exc,
            )

    # ----------------------------------------------------------
    # Skill 执行 (供 apply_skill tool 调用)
    # ----------------------------------------------------------

    def execute_skill(
        self,
        skill_name: str,
        parameters: Optional[dict] = None,
        paper_text: str = "",
        existing_findings: Optional[list] = None,
    ) -> dict[str, Any]:
        """执行指定 Skill 并返回结果。

        Args:
            skill_name: 要执行的 Skill 名称
            parameters: 传给 Skill 的参数
            paper_text: 当前论文文本上下文
            existing_findings: 已有的 findings

        Returns:
            dict with keys: success, findings, output_data, error, execution_time_ms
        """
        from core.skills.base import SkillContext, Finding

        # 查找 Skill
        skill = self.unified_registry.get_by_name(skill_name)
        if skill is None:
            return {
                "success": False,
                "error": f"Skill '{skill_name}' not found. "
                         f"Available: {[s.descriptor.name for s in self.unified_registry.all_skills()[:10]]}",
                "findings": [],
                "output_data": {},
                "execution_time_ms": 0.0,
            }

        # 构建 SkillContext
        context = SkillContext(
            paper_text=paper_text,
            current_phase=self._current_phase,
            existing_findings=list(existing_findings) if existing_findings else [],
            parameters=dict(parameters) if parameters else {},
        )

        # 执行
        result = self.executor.run(skill, context)

        return {
            "success": result.success,
            "findings": [
                {"text": f.description, "severity": f.severity, "category": f.category,
                 "location": f.location, "confidence": f.confidence}
                for f in result.findings
            ],
            "output_data": result.output_data,
            "error": result.error_message,
            "execution_time_ms": result.execution_time_ms,
        }

    # ----------------------------------------------------------
    # Context 提示 (供 ContextAssembler 使用)
    # ----------------------------------------------------------

    def get_skill_hints(self, token_budget: int = 1500) -> str:
        """生成当前可用 SkillX 能力的提示文本。

        基于当前激活的 ToolGroup 和 Phase, 选出相关 Skills 并格式化为
        简短的提示 (Layer 1 元数据级别), 供 LLM 了解当前可用的分析能力。

        Args:
            token_budget: 提示文本的 token 预算

        Returns:
            格式化的提示文本 (空字符串表示无可用 Skill)
        """
        from core.skills.base import SkillContext, SkillLevel

        active_skills = self.tool_group_manager.get_active_skills()
        if not active_skills:
            return ""

        # 按层次分组展示
        planning = [s for s in active_skills if s.descriptor.level == SkillLevel.PLANNING]
        functional = [s for s in active_skills if s.descriptor.level == SkillLevel.FUNCTIONAL]
        atomic = [s for s in active_skills if s.descriptor.level == SkillLevel.ATOMIC]

        parts = ["[SkillX 可用能力]"]

        if planning:
            parts.append("策略层:")
            for s in planning:
                parts.append(f"  - {s.descriptor.name}: {s.descriptor.description[:60]}")

        if functional:
            parts.append("功能层:")
            for s in functional:
                parts.append(f"  - {s.descriptor.name}: {s.descriptor.description[:60]}")

        if atomic:
            parts.append("原子层:")
            for s in atomic[:5]:  # 最多展示 5 个 Atomic
                parts.append(f"  - {s.descriptor.name}: {s.descriptor.description[:60]}")

        parts.append(f"(使用 apply_skill tool 触发执行, 当前 phase: {self._current_phase})")

        hint_text = "\n".join(parts)

        # 粗略 token 估算 (1 token ≈ 4 chars for English/mixed)
        estimated_tokens = len(hint_text) // 3
        if estimated_tokens > token_budget:
            # 裁剪: 只保留 functional 层
            parts = ["[SkillX 可用能力]"]
            if functional:
                for s in functional[:4]:
                    parts.append(f"  - {s.descriptor.name}: {s.descriptor.description[:50]}")
            parts.append(f"(apply_skill tool, phase: {self._current_phase})")
            hint_text = "\n".join(parts)

        return hint_text

    # ----------------------------------------------------------
    # 诊断 / 统计
    # ----------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """获取 SkillX 运行统计。"""
        return {
            "total_skills": len(self.unified_registry.all_skills()),
            "native_skills": len(self.unified_registry.native_skills()),
            "adapted_skills": len(self.unified_registry.adapted_skills()),
            "active_groups": self.tool_group_manager.active_group_names,
            "active_skills": self.tool_group_manager.get_active_skill_names(),
            "current_phase": self._current_phase,
            "executor_stats": self.executor.get_stats(),
        }


# ==============================================================
# apply_skill Tool Handler
# ==============================================================

def tool_apply_skill(args: dict, skillx: SkillXIntegration, state: Any) -> str:
    """Agent 调用 SkillX Skill 执行分析检查。

    Args (from tool call):
        skill_name: 要执行的 Skill 名称
        parameters: (可选) 传给 Skill 的参数 dict
        section_context: (可选) 当前正在分析的 section 文本

    Returns:
        格式化的结果文本
    """
    skill_name = args.get("skill_name", "").strip()
    if not skill_name:
        # 列出可用 Skills
        active = skillx.tool_group_manager.get_active_skills()
        if not active:
            return "[SkillX] 当前 Phase 无激活的 Skills。"
        names = [s.descriptor.name for s in active]
        return f"[SkillX] 请指定 skill_name。当前可用: {', '.join(names)}"

    parameters = args.get("parameters", {})
    section_context = args.get("section_context", "")

    # 收集论文上下文
    paper_text = section_context
    if not paper_text and state and hasattr(state, "paper_sections"):
        # 提供最近读过的 section 文本 (如果有的话)
        if state.sections_read:
            last_section = state.sections_read[-1]
            paper_text = state.paper_sections.get(last_section, "")[:3000]

    # 收集已有 findings
    existing_findings = []
    if state and hasattr(state, "findings"):
        existing_findings = state.findings

    result = skillx.execute_skill(
        skill_name=skill_name,
        parameters=parameters,
        paper_text=paper_text,
        existing_findings=existing_findings,
    )

    if not result["success"]:
        return f"[SkillX Error] {result['error']}"

    # 格式化输出
    parts = [f"[SkillX: {skill_name}] 执行成功 ({result['execution_time_ms']:.0f}ms)"]

    if result["findings"]:
        parts.append(f"\n发现 {len(result['findings'])} 个问题:")
        for i, f in enumerate(result["findings"], 1):
            severity = f.get("severity", "medium")
            text = f.get("text", "")
            category = f.get("category", "")
            loc = f.get("location", "")
            prefix = f"  [{severity}]"
            if category:
                prefix += f" [{category}]"
            if loc:
                prefix += f" @{loc}"
            parts.append(f"{prefix} {text}")

    if result["output_data"]:
        # 只展示关键 output (避免过长)
        output_summary = str(result["output_data"])
        if len(output_summary) > 500:
            output_summary = output_summary[:500] + "..."
        parts.append(f"\n分析数据: {output_summary}")

    return "\n".join(parts)
