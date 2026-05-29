# Scholar-Agent 全功能端到端测试报告

**测试时间**: 2026-05-20 21:39  
**测试环境**: macOS, Python 3.x  
**LLM 后端**: Friday One-API (deepseek-v3-friday)  
**论文样本**: thesis_english_v1.md (经济学论文, 19163 词, 51 节)

---

## 总览

| 阶段 | 通过 | 失败 | 通过率 | 说明 |
|------|------|------|--------|------|
| Phase 1: 结构分析 (零成本) | 6 | 0 | 100% | 纯本地计算，无 API 调用 |
| Phase 2: LLM 功能 | 5 | 0 | 100% | 调用 Friday API |
| Phase 3: 搜索与验证 | 7 | 1 | 87.5% | 调用外部学术 API |
| Phase 4: 安全与稳定性 (零成本) | 22 | 0 | 100% | 纯规则逻辑验证 |
| **合计** | **40** | **1** | **97.6%** | 唯一失败为外部限流 |

---

## Phase 1: 结构分析测试 (6/6 ✅)

零成本、纯本地的结构化分析，测试核心解析引擎。

| # | 测试项 | 耗时 | 结果 |
|---|--------|------|------|
| 1 | `parse_paper` — 论文解析 | 7ms | 19163 词, 51 节 ✅ |
| 2 | `presubmission_check` — 预提交检查 | 64ms | not_ready, 3/7 passed ✅ |
| 3 | `architecture_diagnosis` — 结构诊断 | 150ms | research 类型, 沙漏结构 ✅ |
| 4 | `build_voice_profile` — 语音画像 | 53ms | avg_sent=32.1, passive=6% ✅ |
| 5 | `citation_analysis` — 引文分析 | 6ms | 48 refs, 84 claims ✅ |
| 6 | `section_ops` — 节操作 | 3ms | index/read/consistency 全通过 ✅ |

---

## Phase 2: LLM 功能测试 (5/5 ✅)

调用 Friday One-API 的端到端功能验证。

| # | 测试项 | 耗时 | 结果 |
|---|--------|------|------|
| 1 | `deai_audit` — 去 AI 化评分 | 15ms | Score 0.90, Natural=True ✅ |
| 2 | `rewrite_section` — 章节改写 | 5.3s | 摘要 1197→1251c, 语气优化 ✅ |
| 3 | `agent_loop_turn1` — 交互审稿(轮1) | 26.4s | 857c 回复 + 2 工具调用 ✅ |
| 4 | `agent_loop_turn2` — 交互审稿(轮2) | 14.3s | 675c overclaim 分析 ✅ |
| 5 | `review_paper` — 完整多角色审稿 | 95.4s | 5 审稿人→合并→去重→验证 ✅ |

**review_paper 完整流水线**:
- 5 位角色审稿人并行执行
- LLM 合并产出 21 个 issues
- 规则去重 → 12 个独立问题
- Quote 验证: 12/12 全部通过
- 最终评分: 4.0 (weak_reject)

---

## Phase 3: 搜索与验证测试 (7/8, 1 外部限流)

测试三大搜索后端 + 引文验证 + 引用图谱。

| # | 测试项 | 耗时 | 结果 |
|---|--------|------|------|
| 1 | `semantic_scholar_search` | 868ms | ❌ HTTP 429 (外部限流) |
| 2 | `crossref_search` | 2.4s | 5 results, DOI 正常 ✅ |
| 3 | `unified_search` — 统一搜索 | 1.9s | CrossRef fallback 正常 ✅ |
| 4 | `intelligent_search` — 智能搜索 | 2.0s | LLM 扩展查询 + 重排 ✅ |
| 5 | `doi_lookup` — DOI 查询 | 951ms | de Chaisemartin 2020, 4276 citations ✅ |
| 6 | `search_literature_tool` — 文献搜索 | 2.3s | 格式化输出正常 ✅ |
| 7 | `citation_verification` — 引文核实 | 13.0s | 3 条引文, 批量 API 验证 ✅ |
| 8 | `citation_graph_build` — 引用图谱 | 3.4s | API 连通, 图谱构建逻辑正常 ✅ |

**关于唯一失败**: Semantic Scholar API 在测试时段返回 429 (Too Many Requests)。这是**外部服务的临时限流**，不是代码逻辑问题。代码中已实现自动 fallback 到 CrossRef（如 Test 3 所验证）。

---

## 关键架构验证

### 限流机制
- `SCHOLAR_MIN_INTERVAL = 12s` 确保请求间隔
- 429 专用退避: 30s 基础 + 10s × 重试次数
- Unified search 自动 fallback: Semantic Scholar → CrossRef

### LLM 调用稳定性
- `max_tokens=8000` 防止 JSON 截断
- 所有 LLM 调用在 Friday 限流 (~10 req/min) 下稳定工作
- Agent loop 支持多轮工具调用

### 搜索后端覆盖
- **Semantic Scholar**: 学术元数据 + 引用图谱
- **CrossRef**: DOI 解析 + 全量文献搜索
- **Intelligent Search**: LLM 查询扩展 + 结果重排序

---

## Phase 4: 安全与稳定性测试 (22/22 ✅)

零成本、纯规则逻辑的安全防线验证。

### Module: action_router (5/5 ✅)

| # | 测试项 | 结果 |
|---|--------|------|
| 1 | Red Line 1 — 论文核心论点保护 | auto_fix → guidance ✅ |
| 2 | Red Line 2 — 内容捏造风险检测 | auto_fix → confirm_fix ✅ |
| 3 | Budget Ceiling — 预算限制执行 | full→auto_fix, minimal→guidance ✅ |
| 4 | First-of-Type — 首次自动修改需确认 | 新类别→confirm, 已见→auto ✅ |
| 5 | Statistical Verification Flag | robustness→True, clarity→False ✅ |

### Module: post_edit_verify (6/6 ✅)

| # | 测试项 | 结果 |
|---|--------|------|
| 1 | 交叉引用检测 — 发现 Figure 99 | 正确识别断裂引用 ✅ |
| 2 | 交叉引用 — 有效引用通过 | 无误报 ✅ |
| 3 | 语音漂移 — 检测风格突变 | 29→3 词/句 (90% 漂移) ✅ |
| 4 | AI 回归 — 检测新 AI 模式 | 10 个新 AI 信号 ✅ |
| 5 | AI 回归 — 干净编辑通过 | 无误报 ✅ |
| 6 | 完整 verify_edit 集成 | 一致性+回归双重失败 ✅ |

### Module: reaudit (6/6 ✅)

| # | 测试项 | 结果 |
|---|--------|------|
| 1 | Root Cause Key — 确定性生成 | 同内容→同key, 异内容→异key ✅ |
| 2 | Issue Matching — 精确匹配 | 2对匹配 + 1新问题 ✅ |
| 3 | Status — FULLY_ADDRESSED | 引文被修改→完全解决 ✅ |
| 4 | Status — NOT_ADDRESSED | 同严重度持续→未解决 ✅ |
| 5 | Status — PARTIALLY_ADDRESSED | 严重度降级→部分解决 ✅ |
| 6 | Report Formatting | 903 字符完整报告 ✅ |

### Module: doom_loop (5/5 ✅)

| # | 测试项 | 结果 |
|---|--------|------|
| 1 | 正常使用不触发 | 不同调用→无循环 ✅ |
| 2 | 重复调用触发检测 | 3 次重复→LOOP DETECTED ✅ |
| 3 | 高阈值工具容忍更多重试 | deai_audit: 3次OK, 4次触发 ✅ |
| 4 | Reset 清除历史 | 重置后同调用不触发 ✅ |
| 5 | 模糊匹配 | 语义相似调用被检测 ✅ |

---

## 结论

Scholar-Agent 全部核心功能验证通过:

1. **论文解析引擎** — 可靠解析 Markdown 论文为结构化数据
2. **结构诊断** — 预提交检查、架构诊断、语音画像均正常
3. **多角色审稿** — 5 审稿人并行 → 去重 → 验证，完整流水线
4. **交互式 Agent** — 多轮对话 + 工具调用正常运作
5. **文献搜索** — 三后端搜索 + 智能扩展 + 自动 fallback
6. **引文验证** — 批量 API 验证 + 引用图谱构建
7. **改写与去 AI** — 章节改写 + AI 痕迹评分

总通过率 **97.6%** (40/41)，唯一失败为外部 API 临时限流，不影响功能完整性。

**测试覆盖的功能模块 (15/20 tools, 2/12 utils)**:
- tools: paper_parser, presubmission_check, architecture_diagnosis, section_ops, deai_engine, write_engine, review_engine, web_search, literature_verify, citation_graph, action_router, post_edit_verify, reaudit + main.agent_loop
- utils: voice_profile, doom_loop
