# ScholarAgent v4 — Enhancement Backlog

> **Note**: The "100% Agent" upgrade (UPGRADE_PLAN_100_AGENT.md) was completed in July 2025.
> All 4 Waves implemented: Infrastructure (Wave 1), Agent Core (Wave 2), Quality Assurance (Wave 3), Advanced Capabilities (Wave 4).
> D4 (Streaming/Pause/Resume) completed 2025-07. main.py God File refactor into core/ + handlers/ also completed.
> Remaining open item: Web UI with split-pane.

---

## 借鉴来源：xiaotuan 项目 (eval/00-standards/)

以下增强项参考了 `~lidanhua02/xiaotuan` 仓库中的门控策略 (gate-policy.md) 和评测标准 (V5-evaluation-standard.md)。
学习笔记见 `/Users/yanfeiyu03/Downloads/xiaotuan-eval-study-notes.md`。

---

### ✅ TODO-1: DeAI PEV Loop 引入分层信号容忍度（参考 gate-policy "双门"设计）——已完成 P4

**现状**：当前 `tools/deai_engine.py` 使用平坦的 `PASS_THRESHOLD = 0.7` 判定 de-AI 是否通过。`utils/doom_loop.py` 控制重试次数。

**借鉴点**：xiaotuan 的 gate-policy 采用 L1-L4 通用层 + L5 行业层"双门"机制——两门都必须 PASS 才算通过。每层有独立的 PASS 条件，使用 baseline-relative 阈值（≥ baseline - 5%），并对安全红线 (L3) 设置零容忍。

**增强方向**：
- 将 `overall_score >= 0.7` 替换为分层判定：
  - 关键信号（inflated_symbolism, rule_of_three 等高置信度 AI 特征）→ 零容忍，检出即 FAIL
  - 一般信号（em_dash_overuse, filler_phrases）→ 容忍度相对于 baseline 计算
- 引入 baseline 概念：先对原文做一次 AI 信号扫描得到 base_score，修改后的 score 不得 > base_score（即不能比原文更 AI）
- 参考"紧急通过"机制：当 doom_loop max retries 用尽但分数已在 baseline ± 5% 范围内，可判为 conditional PASS
- 与 `tools/deai_precheck.py` (L1 gate) 配合——precheck 判定"是否需要 de-AI"，本 TODO 增强"de-AI 后是否合格"

**实现位置**：`tools/deai_engine.py` (SIGNAL_TOLERANCE_TIERS, apply_tiered_judgment, deai_audit_and_fix)
**状态**：✅ 已完成（P4，2026-05-21）

---

### ✅ TODO-2: 引入多维诊断评分体系（参考 V5.4.1 评测标准）——已完成 P4

**现状**：`deai_engine.py` 的 `deai_audit()` 输出单一 `overall_score: float`，维度信息只在 `signals` 列表中隐含。

**借鉴点**：xiaotuan V5.4.1 标准设计了 100 分制诊断维度打分，包含 6 个加权维度（任务理解/决策/专家判断/证据嵌入/决策闭环/表达安全感）。

**增强方向（适配到 De-AI Audit 场景）**：
- 将 `overall_score` 细化为多维度评分：
  - 词汇自然度（25%）：inflated_symbolism, promotional_language
  - 句式多样性（20%）：rule_of_three, parallel_structure_overuse, burstiness
  - 连接逻辑（20%）：formulaic_transitions, filler_phrases
  - 标点/格式（15%）：em_dash_overuse, colon_overuse
  - 风格一致性（20%）：voice_drift, register_shift
- 各维度独立计分，加权汇总为 overall_score，但单维度极低也可触发 FAIL
- 输出结构化诊断报告，让用户知道"哪个方面最 AI"
- 诊断报告通过 `utils/trace.py` 的 span 系统记录，便于回溯

**实现位置**：`tools/deai_engine.py` (DimensionScores, compute_dimension_scores, SIGNAL_TO_DIMENSION, DIMENSION_WEIGHTS)
**状态**：✅ 已完成（P4，2026-05-21）

---

### ✅ TODO-3: Hard Caps 机制（参考 V5.4.1 的分值硬上限）——已完成 P5

**现状**：`deai_engine.py` 的 `fix_ai_signals` 逐句修复后重新 audit，无条件判分。

**借鉴点**：xiaotuan V5.4.1 设定了 Hard Caps——特定严重问题直接限制最高分。

**实现内容**：
- `HardCapResult` dataclass + `detect_hard_caps()` 函数（纯 programmatic，零 LLM 成本）
- HC-1：21 个 AI 套话 regex 模式（2+ 匹配 → vocabulary capped at 60%）
- HC-2：连续同 2-word opener 检测（3+ 连续 → rhythm capped at 50%）
- HC-3：零 burstiness 检测（句长 CV < 0.20 → rhythm capped at 40%）
- Hard Cap 在 `deai_audit()` 中独立于 LLM 运行，应用于维度分数后进入 tiered judgment
- 若 Hard Cap 压维度至 floor 以下 → 自动升级为 FAIL
- `TieredJudgment.hard_caps_triggered` + `DeAIVerdict.hard_caps` 完整传播
- `format_deai_result()` 展示 ⛔ Hard Cap 告警

**实现位置**：`tools/deai_engine.py` (HardCapResult, detect_hard_caps, AI_CLICHE_PATTERNS, HC_* constants)
**状态**：✅ 已完成（P5，2026-05-21）

---

### TODO-4: 评测 Benchmark 设计（参考 xiaotuan 评测体系整体思路）

**现状**：De-AI audit 的效果评估无标准化 benchmark，`utils/gold_standard.py` 存在但内容有限。

**借鉴点**：xiaotuan 项目有完整的评测流程设计（标准化 case schema → baseline 管理 → 分层评测）。

**增强方向**：
- 建立 De-AI Gold Test Set：收集 10-20 组 (AI_rewrite, human_reference) pair
  - 覆盖 S1/S2/S3 三个场景
  - 每组标注：原文 → AI 改写 → 期望的 de-AI 版本
  - 覆盖不同信号类型（词汇/句式/连接/标点各占比）
- 定义 baseline scoring：每次迭代 deai_rules 后跑一次 gold test set
- 分维度追踪：哪些维度在提升，哪些退化
- 利用 `utils/gold_standard.py` 作为基础框架扩展

**实现位置**：`utils/gold_standard.py` (框架), `examples/` (test cases)
**优先级**：Medium-High（对规则迭代的质量保障至关重要）

---

### ✅ TODO-5: Review Engine 多维 Severity 判定增强——已完成 P6

**现状**：`tools/review_engine.py` 的 issue severity 为 major/minor/critical 三级。

**借鉴点**：V5.4.1 的 0-4 总分 + 100 分维度细分 + Hard Caps 三层结构。

**实现内容**：
- `SEVERITY_DIMENSIONS`: 5 个加权维度（argumentation_logic 0.25, methodology_rigor 0.25, expression_clarity 0.15, academic_integrity 0.20, completeness 0.15）
- `COMMENT_TYPE_DIMENSION_MAP`: comment_type → 各维度 base impact
- `SeverityAssessment` dataclass + `to_dict()` 序列化
- `compute_impact_dimensions()`: confidence scaling + detail bonus
- `assess_severity()`: weighted score → threshold severity + auto-upgrade via HIGH_WEIGHT_DIMENSIONS
- `reconcile_severity()`: upgrade-only policy（不下调 LLM 判定，只在结构化分析发现更严重时升级）
- `ReviewIssue` 新增 `impact_dimensions` + `severity_assessment` 字段
- `from_llm_issue()` 内自动调用 assess + reconcile，升级时同步更新 `gate_blocker`
- 28 个单元测试全部通过（`test_severity.py`）

**实现位置**：`tools/review_engine.py` (SeverityAssessment, assess_severity, reconcile_severity, compute_impact_dimensions)
**状态**：✅ 已完成（P6，2026-05-21）

---

## 实施建议

1. ✅ **短期**（v3 完善）：TODO-4 建立 gold test set ——已完成 (P3)
2. ✅ **中期**（v3.1）：TODO-1 + TODO-2 分层评分 + baseline-relative 判定 ——已完成 (P4)
3. ✅ **中期**（v3.1）：TODO-3 Hard Caps ——已完成 (P5)
4. ✅ **中期**（v3.2）：TODO-5 Review 多维 Severity ——已完成 (P6)
5. ✅ **中期**（v3.2）：Agent 架构重构 ——已完成 (2025-05-21)
   - 统一声纹检测（消除 3 处重复实现）
   - Author Profile → DeAI 联动注入
   - 结构化 warnings 回传（错误不再静默）
   - 集中阈值管理（config/thresholds.yaml）
   - 拆 closed_loop_fix 为 4 独立工具（deai_pipeline.py）
   - Review Engine 可配置（reviewer_count/focus_dimensions）
   - 引用协同层（citation_synergy.py）
   - 拆 God module deai_engine.py → tools/deai/ 子包
   - Agent 智能：Planning Protocol + Intent Disambiguation + Impact Estimation
6. ✅ **中期**（v3.2+）：Career-Copilot 借鉴功能 ——已完成 (2025-05-22)
   - 四层 JSON 解析恢复（utils/json_repair.py）
   - Checkpoint + Resume 长流水线断点续跑（utils/checkpoint.py）
   - Listwise 对比评分（Review Engine 分数聚集时强制排序拉开区分度）
   - Dry Run 预估（tools/dry_run.py — 执行前预估 API/Token/耗时/费用）
   - 动态辨别知识（tools/focus_generator.py — 论文级审稿焦点注入各角色）

---

## 新工具开发

### ✅ figure_analyzer.py — 图表分析工具 (已完成)
- Vision model figure review, FigureContract claim-alignment, archetype classification
- 状态：已完成并注册

### ✅ literature_verify.py — 文献验证工具 (已完成)
- 引用存在性/年份/刊物验证 + inline consistency + overclaim detection
- v3增强：claim-citation alignment scoring (5维度: specificity/temporal/hedging/type-fit/proximity)
- 状态：已完成并注册 (verify_citations + check_citation_content + check_citation_alignment)

### ✅ presubmission_check.py — 预提交机械检查 (已完成)
- 零LLM成本 desk-reject 预防层
- 检查：引用格式一致性、图表交叉引用、摘要结构、必要章节、交叉引用完整性、格式异常、致谢/基金声明
- 状态：已完成并注册

### ✅ section_responsibility.md — 章节-审稿人职责矩阵 (已完成)
- 定义每个论文章节由哪个 reviewer role 负责审查、关注点
- 涵盖盲区检测（Author Contributions, Data Availability, Ethics 等）
- 状态：已创建于 skills/

### ✅ reaudit.py — 修订对比 Re-audit 模块 (已完成)
- 结构化版本比较：root_cause_key 匹配 + fuzzy fallback
- 四种状态判定：FULLY_ADDRESSED / PARTIALLY_ADDRESSED / NOT_ADDRESSED / NEW
- improvement_rate 量化修订进度
- 状态：已完成并注册 (reaudit + save_previous_issues)

### ✅ architecture_diagnosis.py — 论文结构架构诊断 (已完成)
- 6 种结构性失败模式检测（missing_gap, claim_without_evidence 等）
- 论文类型自动识别 + section type 分类
- 沙漏结构验证 + 修复优先级排序
- 零 LLM 成本，纯规则引擎
- 状态：已完成并注册

### ✅ web_search.py — 学术搜索能力 (已完成)
- Semantic Scholar + CrossRef 双后端，自动降级
- 速率限制 + 会话缓存
- search_fn_adapter() 注入 literature_verify 验证流
- 独立工具：search_literature + verify_doi
- 状态：已完成并注册

### ✅ chinese_academic_standards.md — 中文论文规范 skill (已完成)
- GB/T 7714-2015 参考文献格式规则
- 学位论文结构要求 + 中文学术写作风格检查
- 状态：已创建于 skills/

### ✅ data_availability.md — Data Availability 审计 skill (已完成)
- 3 层审计：存在性 → 完整性 → FAIR 合规
- 检测规则 + 声明模板 + 推荐仓库
- 状态：已创建于 skills/

### ✅ deai_engine.py 增强 — 四步闭环 + 四层自检 (已完成)
- 四步闭环 (Closed Loop): detect → diagnose → rewrite → verify
  - diagnose_signals(): 信号根因分析 + 修复策略推断
  - closed_loop_fix(): 策略感知修复 + 自动验证
- 四层自检协议:
  - L1 Structure: 宏观结构指纹检测（tricolon, paragraph uniformity）
  - L2 Rhythm: 句长变异系数 + 句首重复检测
  - L3 Forbidden: 零容忍禁用词/模式
  - L4 Voice: 作者声纹漂移检测
- 状态：已完成并注册 (deai_closed_loop)
