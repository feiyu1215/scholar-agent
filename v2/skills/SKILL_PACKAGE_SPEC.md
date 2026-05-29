# Skill Package Specification v1.0

## 概述

本文档定义 ScholarAgent Skill 包的标准格式。用户下载或创建 Skill 后，通过 `SkillInstaller` 安装到 Agent 中。

## 包结构

```
my-skill/
├── skill.json          # 元数据（必须）
├── content.md          # 知识内容或行为指令（必须）
└── handler.py          # Action Skill 的 handler（可选，仅 type=action 时需要）
```

## skill.json Schema

```json
{
  "id": "string (required)",
  "type": "knowledge | action (required)",
  "name": "string (required)",
  "description": "string (required)",
  "version": "string, semver (required)",
  "tags": ["string array (required, may be empty)"],
  "applicable_paper_types": ["string array (required)"],
  "applicable_phases": ["string array (required)"],
  "token_estimate": "integer (required, > 0)",
  "priority_hint": "integer (required, 0-100)"
}
```

### 字段说明

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| `id` | string | ✓ | 唯一标识符，仅允许 `[a-z0-9_]`，不超过 64 字符 |
| `type` | string | ✓ | `"knowledge"` 或 `"action"` |
| `name` | string | ✓ | 人类可读名称 |
| `description` | string | ✓ | 功能描述 |
| `version` | string | ✓ | 语义化版本号（如 `"1.0.0"`） |
| `tags` | string[] | ✓ | 分类标签（可为空数组） |
| `applicable_paper_types` | string[] | ✓ | 适用论文类型 |
| `applicable_phases` | string[] | ✓ | 适用审稿阶段 |
| `token_estimate` | int | ✓ | 预估 token 消耗（> 0） |
| `priority_hint` | int | ✓ | 加载优先级（0-100，越高越优先） |

### Action Skill 额外字段

当 `type` 为 `"action"` 时，`skill.json` 还应包含 `tools` 数组：

```json
{
  "tools": [
    {
      "name": "tool_function_name",
      "description": "Tool description for LLM",
      "input_schema": { "type": "object", "properties": {...} },
      "handler": "handler.py::function_name"
    }
  ]
}
```

## content.md

Skill 的知识内容或行为指令，以 Markdown 格式编写。Agent 在匹配到该 Skill 时会将此文件内容注入 context。

## handler.py（可选）

Action Skill 的 Python handler 实现。要求：

- 导出的 handler 函数签名：`def handler_name(args: dict, state: Any) -> str`
- 不应有外部依赖（仅使用标准库 + 项目内部模块）
- 不应进行网络请求或文件系统写操作（除 `.workspace/` 目录）

## 安装流程

1. `SkillInstaller.validate(skill_dir)` — 验证包合法性
2. `SkillInstaller.install(skill_dir)` — 验证 → 复制文件 → 注册到 registry.json
3. 安装后 Agent 下次启动自动加载

## 卸载流程

1. `SkillInstaller.uninstall(skill_id)` — 从 registry 移除 → 删除文件

## ID 冲突处理

- 安装时如果 registry 中已存在相同 `id`，安装失败并返回明确错误
- 用户需先 uninstall 旧版本再安装新版本（未来可扩展为 upgrade 流程）

## 版本兼容性

- 本规范为 v1.0，`skill.json` 中缺少的可选字段使用默认值
- 未来新增字段保持向后兼容
