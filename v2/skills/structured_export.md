# Structured Export Skill

## 功能概述

将当前审稿会话的工作成果导出为结构化报告。Agent 可在 SYNTHESIS 阶段或审稿结束前调用此工具，生成包含所有 findings、edits、覆盖率分析和统计信息的完整审稿报告。

## 使用场景

- Agent 完成审稿后需要输出结构化总结
- 用户要求导出审稿结果为可读报告
- 需要 JSON 格式的机器可解析审稿结果

## 行为指令

当需要汇总审稿发现并生成结构化报告时，使用 `export_structured_review` 工具。

参数选择指南：
- `format`: 用 "markdown" 给人类阅读，"json" 给下游程序消费
- `group_by`: "priority" 适合快速定位高优问题，"section" 适合按章节逐一讨论，"status" 适合追踪修改进度
- `include_stats`: 一般保持 true，除非报告需要精简

报告包含：
1. 元信息（论文路径、findings 总数、覆盖率）
2. Findings 分组列表（按选定维度组织）
3. 已执行的 Edits 记录
4. Section 覆盖率分析（已读/未读列表）
5. 会话统计（token 消耗、轮次数、priority 分布）

<!-- tools
- name: export_structured_review
  description: "Export current review findings, edits, and session stats as a structured report (Markdown or JSON). Use at SYNTHESIS or session end to produce a comprehensive review summary."
  input_schema:
    type: object
    properties:
      format:
        type: string
        enum: ["markdown", "json"]
        description: "Output format. 'markdown' for human reading, 'json' for machine parsing."
        default: "markdown"
      group_by:
        type: string
        enum: ["priority", "section", "status"]
        description: "How to group findings in the report."
        default: "priority"
      include_stats:
        type: boolean
        description: "Whether to include session statistics (turns, tokens, distributions)."
        default: true
    required: []
  handler: "skill_handlers/structured_export.py::handle_export_review"
-->
