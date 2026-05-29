# ScholarAgent V2 — 功能走查验证日志

> 执行时间: 2026-05-30 00:26
> 执行方式: 代码审查 + 单元测试 + 端到端调用验证
> 测试套件: 3091 passed in 13.16s (全量通过)

---

## Layer 1: 基础可用性

### [L1.1] 论文加载

- **状态**: PASS
- **执行时间**: 2026-05-30 00:20
- **观察结果**: `load_pdf_as_sections('paper_001.pdf')` 成功加载 24 个 sections，全文正确提取。Section 名称包含完整的层级结构（1 introduction, 2.1 optimal water conservation 等）。
- **异常/问题**: 无

### [L1.2] 基本审稿循环

- **状态**: PASS (代码审查 + 测试覆盖)
- **执行时间**: 2026-05-30 00:15
- **观察结果**: `cognitive_loop` (1204行) 信号协议完整（__DONE__, __TALK__, __NUDGE__, __SPAWN__, __SWITCH__）。Doom loop 保护、token budget 硬截断、soft turn 警告均有测试覆盖。Phase 自动转换条件（sections_read≥3 + findings≥1）在 boundary_guard 中实现。

---

## Layer 2: 核心工具链

### [L2.1] 阅读工具

- **状态**: PASS
- **执行时间**: 2026-05-30 00:16
- **观察结果**:
  - `read_section`: 支持精确匹配→模糊匹配→数字前缀匹配三级策略。窗口 6000 chars，续读通过 offset 参数。自动生成 section digest（≤150 chars）、累积 VoiceFingerprint、被动同步 PCG。
  - `search_literature`: 4 后端 fallback（Semantic Scholar → OpenAlex → CrossRef → arXiv），内置 rate limiting + 磁盘缓存。
  - `fetch_paper_detail`: 存入 `state.reference_papers` 持久化工作区。
  - `read_reference`: 支持用户提供的本地参考文献，模糊 section 匹配。
- **异常/问题**: 无独立单元测试文件（通过集成测试间接覆盖）。

### [L2.2] 记录工具

- **状态**: PASS
- **执行时间**: 2026-05-30 00:17
- **观察结果**: 三信号去重机制完整实现：
  1. 术语重叠（Jaccard + CJK bigram）
  2. 数字/表格引用重叠
  3. Section 归属一致性
  - 三条判定规则（70%纯术语 / 60%+数字 / 50%+同Section+数字）
  - 状态升级原地更新、同状态追加证据、降级阻止
  - 8 个专项测试全部通过

### [L2.3] 元认知工具

- **状态**: PASS
- **执行时间**: 2026-05-30 00:18
- **观察结果**: `reflect_and_plan` 通过 `cognitive_update` dict 显式更新或 `auto_infer_strategy` 自动推断。Phase 54 追踪策略切换历史。停滞检测有 cooldown 保护（≥3轮间隔，第6轮前不触发）。

### [L2.4] 编辑工具

- **状态**: PASS
- **执行时间**: 2026-05-30 00:18
- **观察结果**: EDIT-5 三级验证（PASS/WARN/FAIL）+ 重试计数（max 3）+ checker 小模型双重检查。`generate_edit_plan` 校验 finding_ids 引用合法性和 action 有效性。

### [L2.5] 交互/并行工具

- **状态**: PASS
- **执行时间**: 2026-05-30 00:18
- **观察结果**:
  - `spawn_perspective`: 返回 `__SPAWN__` 信号 → loop 捕获 → `_run_sub_perspective` 独立子循环
  - `spawn_parallel_readers`: 硬约束 `_MAX_PARALLEL_READERS = 8`（非文档中的4），`asyncio.gather` 真并行
  - 预算 guard: 父级剩余 < 8000 tokens 时跳过
  - 子视角工具限制: `SUB_PERSPECTIVE_TOOLS` 移除 spawn 类工具

### [L2.6] 验证工具

- **状态**: PASS (代码审查)
- **执行时间**: 2026-05-30 00:18
- **观察结果**: `detect_ai_signals` (14种信号+5维度评分)、`verify_citations` (natbib/biblatex)、`recall_context` (Memory store) 均有实现。

---

## Layer 3: 高级认知功能

### [L3.1] MetaCognitionLayer (MCL)

- **状态**: PASS
- **执行时间**: 2026-05-30 00:20
- **观察结果**:
  - 模型: `gpt-4.1-mini`（可通过 `MCL_MODEL` 环境变量覆盖）
  - Gate 流程: findings<3 跳过 → LLM 评审 → block/pass → `_gate_fired` 一次性机制
  - 优雅降级: LLM 异常时自动 pass
  - Auto-spawn: MCL 可推荐并行子视角（bypass Agent 决策）
  - 27 个测试全部通过

### [L3.2] Hypothesis-Driven Working Memory (HD-WM)

- **状态**: PASS
- **执行时间**: 2026-05-30 00:21
- **观察结果**:
  - 饱和检测: `SATURATION_WINDOW = 3` 轮无新假说 → saturated
  - review_readiness: `resolution_rate * 0.7 + coverage_bonus * 0.3`
  - 可插拔: `enable_hdwm=True` 激活
  - 90 个相关测试全部通过

### [L3.3] Spawn 子视角系统

- **状态**: PASS
- **执行时间**: 2026-05-30 00:18
- **观察结果**: 独立 sub_harness + 精简工具集 + MCL 驱动模型路由 + findings 回注主循环 + token 消耗回流。兜底机制：子 LLM 产出分析文本但 0 findings 时从 content 提取结论。

### [L3.4] Evolution 进化引擎

- **状态**: PASS
- **执行时间**: 2026-05-30 00:22
- **观察结果**:
  - HabitLearner: evidence_count≥3 + effectiveness≥0.6 → LearnedHabit
  - IntraSession Contrast: Phase A/B 对照实验，delta>0.15 reinforce
  - 三频 MetaReflector: Emergency(每次) + Fast(每3次) + Deep(每10次)
  - 235 个相关测试全部通过

### [L3.5] Dual Loop 编排器

- **状态**: PASS
- **执行时间**: 2026-05-30 00:22
- **观察结果**:
  - Kill Switch: `SCHOLAR_GODEL_DUAL_LOOP` (默认 ON)
  - PaperProfile: 纯 regex 启发式（方法论/复杂度/领域检测）
  - 5 个策略模板 + 动态重规划（stuck检测/budget重分配/强制收尾）
  - DualLoopSignal 双向通信（Inner→Outer 观察 + Outer→Inner 建议）

---

## Layer 4: Skills 体系

### [L4.1] 知识型 Skills

- **状态**: PASS (代码审查)
- **执行时间**: 2026-05-30 00:22
- **观察结果**: 9 个知识型 Skills 通过 SkillRegistry 注册，按 phase 和 paper 特征自动激活。

### [L4.2] 经济学原子 Skills

- **状态**: PASS
- **执行时间**: 2026-05-30 00:22
- **观察结果**: AppendixMathAuditSkill 在 `_run_deep_verification_pass` 中被调用。DID/IV/SE/Endogeneity Skills 通过 SkillX 集成。

### [L4.3] 多模态 Skills — **关键修复**

- **状态**: PASS (修复后)
- **执行时间**: 2026-05-30 00:25
- **观察结果**:
  - **发现 bug**: `_run_deep_verification_pass` 中 `TableConsistencySkill` 没有接收 PDF 路径，导致 PDF 表格无法被提取，Rule 10 (G005) 永远不会在端到端路径中触发。
  - **修复**: 在 `_run_deep_verification_pass` 中增加 `TableExtractionSkill` 前置步骤（含 PDF 路径），将提取的 `EconTable` 对象直接传给 `TableConsistencySkill`。
  - **验证**: 修复后对 paper_001.pdf 运行完整链路，成功检测到 45 个 findings，包括 G005 关键发现：`Tables 'pdf_table_18' and 'pdf_table_23' share 100% identical numeric cells` (severity=ERROR, confidence=0.95)。
  - 50 个表格处理测试全部通过。

---

## Layer 5: 训练子系统

### [L5.1-L5.5] 训练子系统全部

- **状态**: PASS
- **执行时间**: 2026-05-30 00:23
- **观察结果**:
  - 所有模块可正常导入和实例化（WeaknessAnalyzer, AdversarialGenerator, CurriculumDesigner, TrainingLoop, ArenaOrchestrator）
  - 17 维度弱点分析、19 种挑战类型、ELO 评分系统、6 种红队策略
  - Kill Switch 独立控制（4 个 env var）
  - 227 个相关测试全部通过

---

## 修复记录

### BUG-001: G005 端到端路径断裂

- **文件**: `core/agent.py` → `_run_deep_verification_pass()`
- **问题**: `TableConsistencySkill` 在 deep verification pass 中独立运行时，只使用 `TextTableParser` 从文本中提取表格。对于 PDF 论文，文本中不包含 LaTeX/Markdown 格式表格标记，导致提取结果为空，Rule 10 永远不会触发。
- **根因**: `SkillContext.parameters` 为空 dict，没有传入 `paper_path`，也没有上游 `econ_tables`。
- **修复**: 在 `_run_deep_verification_pass` 中增加 `TableExtractionSkill` 前置步骤（通过 `paper_metadata.paper_path` 触发 `PDFTableExtractor`），将提取的 `EconTable` 对象列表通过 `parameters["econ_tables"]` 传给 `TableConsistencySkill`。
- **影响**: G005 (cross-table duplication) 现在可以在端到端审稿中被正确检测。
- **回归测试**: 3091 passed (无回归)。

---

## 总结

| Layer | 验证点数 | PASS | FAIL | 修复 |
|-------|---------|------|------|------|
| L1 基础可用性 | 2 | 2 | 0 | 0 |
| L2 核心工具链 | 6 | 6 | 0 | 0 |
| L3 高级认知 | 5 | 5 | 0 | 0 |
| L4 Skills 体系 | 3 | 3 | 0 | 1 (G005 路径修复) |
| L5 训练子系统 | 5 | 5 | 0 | 0 |
| **合计** | **21** | **21** | **0** | **1** |

全部 21 个验证点 PASS。发现并修复 1 个关键 bug（G005 端到端路径断裂）。
测试套件 3091 个测试全部通过，无回归。
