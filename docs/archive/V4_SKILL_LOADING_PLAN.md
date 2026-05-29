# ScholarAgent V4: Skill 加载机制 + 场景模板化 + 资源结构化管理

> 基于 V3 全量实现完成后的扩展计划。V3 全部代码已实现并通过评估（F1=0.72, vs V2 +0.16）。
> 优先级逻辑：先把散装资源结构化 → 再让知识按场景注入 prompt → 最后支持操作型扩展。

---

## 背景：当前 Skill 机制的 Gap

| Gap | 性质 | 影响 |
|-----|------|------|
| `v2/skills/` 的 8 个 Markdown 无代码路径加载 | 功能缺失 | 领域知识（overclaim 规则、方法论清单）存在但从未被 Agent 消费 |
| 论文类型无法影响审稿策略 | 可用性 | 经济学论文和 NLP 论文用同一套 identity，无差异化 |
| 无动态 tool 扩展机制 | 扩展性 | 新增能力必须修改 identity.py 的硬编码 tool list |
| 无外部 Skill 引入路径 | 生态性 | 无法复用 CatDesk 等外部 Skill 生态的领域知识 |

---

## Skill 的两层语义

| 类型 | 加载方式 | 运行时效果 | 示例 |
|------|----------|-----------|------|
| **知识型** | 文本注入 system prompt | LLM 获得额外领域知识/审稿规则 | `overclaim_rules.md`, `methodology_checklist.md` |
| **操作型** | 文本注入 system prompt + 动态注册 tools | LLM 获得新的行为指令和对应工具 | "LaTeX 公式验证器"、"结构化审稿报告导出" |

**关键认知**：两者的加载机制没有本质区别——都是把 Markdown 内容注入到 prompt 里。区别在于操作型 Skill 的文本里包含了"当 X 条件满足时，调用 Y 工具"的行为指令，并需要在 Harness 中注册对应的 tool handler。

这和 Claude Code / CatDesk 的 Skill 机制本质相同：Skill 文件 = 注入 prompt 的文本 + 可选的工具声明。

---

## 设计哲学

| 原则 | V4 应用 |
|------|---------|
| Constrain, don't control | 知识型 Skill 是"参考资料"；操作型 Skill 提供能力但不强制使用 |
| Kill Switch 降级 | 新增 `SCHOLAR_GODEL_SKILL_LOADING` 环境变量，OFF 时回退到纯 identity 行为 |
| Assembler 优先级架构 | Skill 知识内容作为 DynamicSection 注入，受 token budget 裁剪 |
| 动态 Tool 注册 | 操作型 Skill 的 tools 在 Harness 启动时动态加入 tool list |
| 宪法层不变 | 不触碰 MAX_META_DEPTH, SIGNAL_MAX_PER_TURN 等约束 |
| Token Budget 统一管理 | 操作型 Skill 的 prompt 注入同样受 Zone A budget 约束 |
| EvidenceChain 可选 | 操作型 Skill 不强制接入 EvidenceChain（仅审稿核心路径追踪） |

---

## Phase A：资源结构化管理（Resource Registry）

### A1. registry.json 索引 + SkillMeta 数据模型

**预计工时**：~1h  
**优先级**：P0  
**前置**：无

#### 现状

`v2/skills/` 有 8 个 Markdown 文件，但没有任何元数据描述"这个文件适用于什么论文类型、什么阶段、多大 token 开销"。纯靠文件名猜测用途。

#### 实施步骤

1. 创建 `v2/skills/registry.json`：为每个现有 Skill 定义结构化元数据
   ```json
   {
     "version": "1.0",
     "skills": [
       {
         "id": "overclaim_rules",
         "type": "knowledge",
         "file": "overclaim_rules.md",
         "name": "Overclaim Detection",
         "description": "检测学术论文中过度声称的语言模式",
         "tags": ["quality_check", "language"],
         "applicable_paper_types": ["empirical", "theoretical", "review", "mixed"],
         "applicable_phases": ["DEEP_REVIEW", "SYNTHESIS"],
         "token_estimate": 1200,
         "priority_hint": 72
       }
     ]
   }
   ```

2. 创建 `v2/core/skill_registry.py`：
   - `SkillMeta` dataclass：对应 registry.json 中一条记录
   - `SkillRegistry` 类：从 registry.json 加载，提供 `query()` 和 `load_content()` 方法
   - `query(paper_type, phase, budget_tokens)` → 返回按 priority_hint 排序、累计 token 不超限的 SkillMeta 列表

3. 为全部 8 个现有 Skill 文件编写准确的元数据条目

#### 测试要求

- `registry.json` 可被正确 parse
- `query()` 按 paper_type 过滤正确（empirical 论文匹配 methodology_checklist 但不匹配 chinese_academic_standards）
- `query()` 按 budget 裁剪正确（超限时低 priority_hint 的 skill 被丢弃）
- `load_content()` 正确读取 Markdown 文件内容

#### 状态：☑

---

### A2. Kill Switch + 宪法层常量

**预计工时**：~30min  
**优先级**：P0  
**前置**：无（与 A1 独立并行）

#### 实施步骤

1. 在 `godel_config.py` 新增：
   ```python
   GODEL_SKILL_LOADING_ENABLED: bool = _env_flag("SCHOLAR_GODEL_SKILL_LOADING")
   """Skill 动态加载。OFF 时不注入任何 v2/skills/ 内容。"""
   
   SKILL_ZONE_BUDGET: int = 2000
   """动态加载 skills 的 token 预算上限。Zone A 内部分配。"""
   ```

2. 在 `log_config_status()` 的 flags dict 中追加 `"SkillLoading": GODEL_SKILL_LOADING_ENABLED`

#### 测试要求

- `SCHOLAR_GODEL_SKILL_LOADING=0` 时 flag 为 False
- `SCHOLAR_GODEL_SKILL_LOADING=1`（默认）时 flag 为 True
- `log_config_status()` 输出包含 SkillLoading 状态

#### 状态：☑

---

## Phase B：场景模板化（Scenario Templates）

### B1. 场景模板文件 + Schema

**预计工时**：~3h  
**优先级**：P1  
**前置**：无

#### 现状

`paper_type_hints.py` 的 `CognitiveHints` 已支持 Agent 自主生成审稿策略（focus_dimensions, typical_weaknesses, verification_strategies）。但生成时没有领域专家预置的模板作为 seed——Agent 完全从零开始，容易遗漏领域特有的关注维度。

#### 实施步骤

1. 创建 `v2/skills/templates/` 目录

2. 创建 `_template_schema.yaml`：定义模板格式规范

3. 编写至少 6 个场景模板：
   | 文件 | 论文类型 |
   |------|---------|
   | `empirical_economics.yaml` | DID/RDD/IV 实证经济学 |
   | `theoretical_model.yaml` | 博弈论/机制设计 |
   | `ml_experiment.yaml` | ML/DL 实验论文 |
   | `nlp_system.yaml` | NLP 系统论文 |
   | `clinical_trial.yaml` | RCT/临床试验 |
   | `survey_review.yaml` | 系统综述/Survey |

4. 每个模板包含：
   - `match_signals`：keywords + structure_patterns（用于自动匹配）
   - `seed_hints`：预填 CognitiveHints 的初始值
   - `recommended_skills`：推荐加载的知识型 Skill ID 列表
   - `gate_overrides`：completion gate 参数调整（可选）

#### 测试要求

- 所有 YAML 文件可被正确 parse
- match_signals 匹配逻辑覆盖常见论文类型
- seed_hints 结构与 CognitiveHints dataclass 对齐

#### 状态：☑

---

### B2. 模板匹配 + CognitiveHints 注入

**预计工时**：~2h  
**优先级**：P1  
**前置**：B1、A2

#### 现状

`generate_cognitive_hints` tool 接收 Agent 的输入直接构造 CognitiveHints。无模板参与。

#### 实施步骤

1. 在 `skill_registry.py` 新增 `TemplateRegistry` 类：
   - 加载 `skills/templates/*.yaml`
   - `match(paper_type_description: str, keywords: list[str]) → Template | None`

2. 修改 `tool_handlers/metacognition.py` 的 `tool_generate_cognitive_hints`：
   - 如果 `GODEL_SKILL_LOADING_ENABLED`：
     - 调用 `TemplateRegistry.match()` 尝试匹配模板
     - 匹配成功 → 用 `seed_hints` 预填 CognitiveHints 字段（Agent 的输入 override 模板值）
     - 匹配失败 → V3 行为（纯 Agent 输入）
   - 如果 OFF → V3 行为

3. 模板的 `recommended_skills` 存入 `state`，供 Phase C 的 Assembler 集成使用

#### 测试要求

- 模板匹配：输入 "DID" 相关描述 → 匹配 empirical_economics
- Agent override：Agent 提供 focus_dimensions → 覆盖模板的 seed_hints.focus_dimensions
- 匹配失败降级：未知论文类型 → CognitiveHints 完全由 Agent 输入决定

#### 状态：☑

---

## Phase C：Assembler 集成（知识型 Skill 注入）

### C1. domain_skills Section 注册

**预计工时**：~2h  
**优先级**：P0  
**前置**：A1、A2

#### 现状

Assembler 有完整的 Section 注册系统（priority + condition_fn + cache_policy），已注册 ~15 个 section。但 `v2/skills/` 内容从未通过此路径注入。

#### 实施步骤

1. 在 `assembler.py` 新增 `_has_domain_skills` 和 `_compute_domain_skills` 函数

2. `_compute_domain_skills` 逻辑：
   - 从 state 获取论文类型（CognitiveHints.paper_type_description 或 PaperStructureIndex）
   - 调用 `skill_registry.query(paper_type, current_phase, budget_tokens=SKILL_ZONE_BUDGET)`
   - 组装匹配 skills 的完整内容，用认知辅助框架措辞包裹：
     ```
     [领域审稿参考 — 按需加载，非指令]
     --- Overclaim Detection ---
     (文件内容)
     [以上为参考知识，审稿应基于论文实际内容。]
     ```

3. 在 `_register_default_sections()` 中注册：
   ```python
   self.registry.register(
       name="domain_skills",
       priority=73,
       cache_policy=CachePolicy.PHASE,
       compute_fn=_compute_domain_skills,
       condition_fn=_has_domain_skills,
   )
   ```

4. `ContextAssembler.__init__` 新增 `skill_registry` 可选参数

5. `harness.py` 中初始化 SkillRegistry 并传入 Assembler

#### 测试要求

- `GODEL_SKILL_LOADING_ENABLED=1` + empirical 论文 → assemble() 输出包含 methodology_checklist 内容
- token 超限时低 priority_hint skill 被裁剪
- `GODEL_SKILL_LOADING_ENABLED=0` → domain_skills section 不出现
- PHASE 缓存：同阶段内多次 assemble() 不重复计算

#### 状态：☑

---

### C2. 模板 recommended_skills 对接

**预计工时**：~1h  
**优先级**：P1  
**前置**：B2、C1

#### 现状

Phase B2 中模板的 `recommended_skills` 已存入 state。C1 的 `_compute_domain_skills` 需要优先加载模板推荐的 skills。

#### 实施步骤

1. `_compute_domain_skills` 查询逻辑调整：
   - 如果 state 有 `recommended_skills`（来自模板匹配）→ 优先加载这些 skills
   - 剩余 budget 再用 `query()` 补充其他匹配 skills
   - 合并去重

2. 确保模板推荐的 skill ID 在 registry.json 中存在（不存在则 warn + 跳过）

#### 测试要求

- 模板推荐 `["methodology_checklist", "econ_writing"]` → 两者都被加载（在 budget 内）
- 推荐不存在的 skill_id → 日志警告 + 不崩溃

#### 状态：☑

---

## Phase D：操作型 Skill（Dynamic Tool Registration）

### D1. Skill 文件格式扩展 + Handler 加载

**预计工时**：~4h  
**优先级**：P2  
**前置**：C1

#### 现状

`identity.py` 的 `SCHOLAR_TOOLS` 是硬编码的 17+ tools 列表。Harness 通过 `get_persona("scholar")` 一次性获取。新增工具必须改代码。

#### 实施步骤

1. 定义操作型 Skill 的 Markdown 格式：底部包含 `<!-- tools ... -->` YAML 块
   ```markdown
   # Skill Name
   (行为指令和使用说明)
   
   <!-- tools
   - name: tool_name
     description: "..."
     input_schema: { ... }
     handler: "skill_handlers/module.py::handler_function"
   -->
   ```

2. 在 `SkillRegistry` 中扩展：
   - `SkillMeta` 增加 `type: "knowledge" | "action"` 字段
   - `tools` 字段（操作型独有）：`list[ToolDef]`
   - `get_action_skills(paper_type, phase) → list[ActionSkillMeta]`

3. 在 `Harness.__init__` 中：
   ```python
   identity_text, base_tools = get_persona(self.persona_name)
   
   if GODEL_SKILL_LOADING_ENABLED and self.skill_registry:
       action_skills = self.skill_registry.get_action_skills(...)
       for skill in action_skills:
           base_tools = base_tools + skill.tool_schemas
           self._register_skill_handler(skill)
   
   self.tools = base_tools
   ```

4. `_register_skill_handler`：用 `importlib.import_module` 动态加载 handler 函数

5. 创建 `v2/skills/skill_handlers/` 目录

#### 测试要求

- `type: "action"` Skill 的 tools 出现在最终 tool list 中
- Handler 可被动态 import 并正确执行
- Handler import 失败 → graceful 降级（跳过该 tool + warn log）
- Kill Switch OFF → 动态 tools 不注册

#### 状态：☑

**实现记录**：
- `ToolDef` dataclass 已在 `skill_registry.py` (L37-66) 实现，含 `to_api_schema()` 方法
- `SkillMeta` 扩展了 `type` + `tools` 字段，`get_action_skills()` 方法已实现
- `skill_handler_loader.py` 新文件：importlib 动态加载 + 安全约束 + 缓存
- `v2/skills/skill_handlers/` 目录已创建
- `harness.py`: `_register_action_skill_tools()` 通过闭包桥接 `(args, state) → (args)` 签名差异
- `harness.py`: `get_action_tool_schemas()` 返回 API schema 列表（防御性副本）
- `agent.py`: `self.tools` 合并 action skill schemas
- 21 个单元测试全部通过 (`test_v4_action_skill_tools.py`)

---

### D2. 示范操作型 Skill 实现

**预计工时**：~3h  
**优先级**：P2  
**前置**：D1

#### 实施步骤

1. 实现一个完整的操作型 Skill（二选一，根据实用性决定）：

   **选项 A: structured_export** — 审稿完成后将 findings 导出为结构化 JSON/Markdown 报告
   - Tool: `export_structured_review`
   - Handler: 从 state.findings 组装格式化报告，写入 `.workspace/reports/`

   **选项 B: formula_verifier** — 验证论文中的 LaTeX 公式推导
   - Tool: `verify_formula`
   - Handler: 接收 LaTeX 步骤，用 sympy 验证等式

2. 编写完整的 Skill Markdown 文件（含行为指令 + tools 块）

3. 在 registry.json 中注册

4. E2E 验证：Agent 在审稿过程中成功调用 Skill tool

#### 测试要求

- 端到端：Agent 调用 Skill tool → 获得正确返回值
- Skill tool 结果可被 Agent 用于后续决策（如 formula_verifier 发现错误 → Agent 记录 finding）

#### 状态：☑

**实现记录**（选择 Option A: structured_export）：
- `v2/skills/skill_handlers/structured_export.py`: handler `handle_export_review(args, state) -> str`
  - 支持 Markdown / JSON 双格式输出
  - 支持按 priority / section / status 三维度分组
  - 支持覆盖率分析（sections_read vs paper_sections 交集）
  - 支持会话统计（token 消耗、priority/status 分布）
  - 空状态和缺失属性 graceful 处理
- `v2/skills/structured_export.md`: Skill 文件含行为指令 + `<!-- tools -->` YAML 声明块
- `registry.json`: 新增 `structured_export` 条目（type="action", applicable_phases=["SYNTHESIS"]）
- `v2/tests/test_d2_structured_export.py`: 31 项测试全部通过
  - TestParameterValidation (3): 参数校验
  - TestMarkdownOutput (9): Markdown 格式正确性
  - TestJsonOutput (6): JSON 可解析 + 结构验证
  - TestEdgeCases (6): 空状态 / 缺失字段 / 异常覆盖率
  - TestSkillRegistryIntegration (6): registry + loader + Markdown YAML 解析
  - TestHarnessIntegration (1): 模拟完整注册链路并验证执行
- 全部 V4 核心测试 (84 项) 通过，无回归

---

## Phase E：外部 Skill 引入

### E1. 外部 Skill 加载路径

**预计工时**：~2h  
**优先级**：P2  
**前置**：D1（需要 knowledge/action 区分完整）

#### 现状

所有 Skill 必须放在 `v2/skills/` 目录内。无法引用外部路径的 Skill 文件。

#### 实施步骤

1. `registry.json` 支持 `source: "external"` + `path` 字段：
   ```json
   {
     "id": "econ_writing_external",
     "type": "knowledge",
     "source": "external",
     "path": "/path/to/catdesk/skills/econ-write/SKILL.md",
     "extract_sections": ["Core Rules", "Writing Patterns"],
     "applicable_paper_types": ["empirical"],
     "applicable_phases": ["EDITING", "SYNTHESIS"],
     "token_estimate": 1500,
     "priority_hint": 65
   }
   ```

2. `SkillRegistry.load_content()` 扩展：
   - `source: "internal"`（默认）→ 从 `skills_dir / file` 读取
   - `source: "external"` → 从绝对路径读取
   - `extract_sections` 非空时 → 只提取指定 heading 下的内容

3. 安全约束：
   - 只接受本地文件路径（不支持网络 URL）
   - 外部操作型 Skill 的 handler 必须在本地 `skill_handlers/` 中实现（不执行外部代码）
   - 文件不存在 → graceful 跳过 + warn log

#### 测试要求

- 外部路径文件存在 → 内容正确加载
- `extract_sections` → 只返回指定 heading 的内容
- 文件不存在 → 跳过不崩溃
- 网络 URL → 拒绝加载

#### 状态：☑

**实现记录**：
- `core/skill_registry.py` SkillMeta 新增 `source`/`path`/`extract_sections` 字段
- `load_content()` 扩展：支持 `source="external"` 从绝对路径读取
- `_resolve_skill_path()` 安全校验：拒绝 http/https/ftp URL、空 path
- `_extract_sections()` 静态方法：按 heading 匹配提取指定 section 内容（大小写不敏感）
- `registry.json` 新增示例条目 `econ_writing_external_example`（source="external"）
- `tests/test_e1_external_skills.py`：25 项测试全部通过
  - TestExternalSkillFullLoad (2): 外部文件完整加载
  - TestExtractSections (4): section 提取正确性
  - TestGracefulDegradation (5): URL 拒绝 + 文件不存在 + 空路径
  - TestBackwardCompatibility (4): internal source 不受影响
  - TestSkillMetaNewFields (3): 新字段解析
  - TestExtractSectionsHelper (7): 静态方法单元测试

---

## 执行顺序与依赖图

```
A1 (Registry) ────────────────────┐
                                  ├──→ C1 (Assembler 集成) ──→ C2 (模板对接)
A2 (Kill Switch) ── 独立并行 ──────┘         │
                                             ↓
B1 (模板文件) ── 独立 ──→ B2 (模板匹配) ──→ C2
                                             │
                                             ↓
                                  D1 (操作型 Skill 框架)
                                             │
                                             ↓
                                  D2 (示范 Skill 实现)
                                             │
                                             ↓
                                  E1 (外部 Skill 引入)
```

**建议执行批次**：
- **Batch 1**（可并行）：A1 + A2 + B1
- **Batch 2**（依赖 Batch 1）：C1 + B2
- **Batch 3**（依赖 Batch 2）：C2
- **Batch 4**（依赖 C1）：D1 + D2
- **Batch 5**（依赖 D1）：E1

---

## 验收标准总表

| # | 检查项 | Phase | 状态 |
|---|--------|-------|------|
| 1 | `registry.json` 包含全部 8 个现有 skill 的结构化描述 | A1 | ☑ |
| 2 | `SkillRegistry.query()` 按 paper_type + phase + budget 正确筛选 | A1 | ☑ |
| 3 | Kill Switch OFF 时 Skill 加载完全不触发 | A2 | ☑ |
| 4 | 至少 6 个场景模板覆盖主流论文类型 | B1 | ☑ |
| 5 | 模板匹配结果正确 seed CognitiveHints | B2 | ☑ |
| 6 | Assembler 在 empirical 论文场景下注入 methodology_checklist | C1 | ☑ |
| 7 | 模板 recommended_skills 被优先加载 | C2 | ☑ |
| 8 | 操作型 Skill 的 tools 在 Harness 中正确注册 | D1 | ☑ |
| 9 | 至少 1 个操作型 Skill 端到端可用 | D2 | ☑ |
| 10 | 外部 Skill 文件可被正确加载（含 extract_sections） | E1 | ☑ |

---

## 工作量估计

| Phase | 新增文件 | 修改文件 | 估计行数 | 难度 |
|-------|----------|----------|----------|------|
| A1 | `skill_registry.py`, `registry.json` | 无 | ~200 | 低 |
| A2 | 无 | `godel_config.py` | ~15 | 低 |
| B1 | `skills/templates/*.yaml` (6+) | 无 | ~350 | 中 |
| B2 | 无 | `tool_handlers/metacognition.py`, `skill_registry.py` | ~80 | 中 |
| C1 | 无 | `assembler.py`, `harness.py` | ~80 | 中 |
| C2 | 无 | `assembler.py` | ~30 | 低 |
| D1 | `skill_handlers/__init__.py` | `harness.py`, `skill_registry.py` | ~150 | 高 |
| D2 | 示范 Skill `.md` + handler `.py` | `registry.json` | ~200 | 中 |
| E1 | 无 | `skill_registry.py` | ~60 | 低 |
| 测试 | `test_skill_*.py` (3+ files) | 无 | ~400 | 中 |

**总计**：~1565 行新代码 + 6 个 YAML 模板 + 1 个示范操作型 Skill

---

## 与现有架构的兼容性保证

1. **Assembler 注册系统**：`domain_skills` 是标准 Section，priority=73 无冲突（现有 priorities: 100,95,90,89,88,86,85,82,80,77,75,70,65,60,55,52,50）
2. **Identity / Tool List**：操作型 Skill 通过 list concatenation 扩展 `base_tools`，不修改 `SCHOLAR_TOOLS` 常量
3. **Harness execute_tool**：现有 tool dispatch 是 dict lookup (`self._tool_handlers[name]`)，动态注入新 handler 无缝兼容
4. **Kill Switch**：新增 `SCHOLAR_GODEL_SKILL_LOADING`，与现有 9 个 kill switch 并列
5. **Token Budget**：从 Zone A 预留 2000 tokens，不侵占 Zone B（论文）或 Zone C（对话）
6. **CognitiveHints**：模板通过修改 `generate_cognitive_hints` 的 seed 值影响，不改已有 priority=86 注入逻辑
7. **EvidenceChain**：操作型 Skill tools 不自动接入，保持审稿推理链纯净
8. **测试回归**：Kill Switch OFF 时所有新代码完全 no-op，现有 992 tests 不受影响

---

## 状态跟踪约定

> **每完成一个实施步骤后，必须更新本文件中对应的状态标记**：
> - `☐` → `☑`（完成）
> - 在验收标准总表中同步更新
> - 如有计划变更，在对应 Phase 下方追加 `> [日期] 变更说明`
