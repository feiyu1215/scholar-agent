# ScholarAgent V3 完善计划

> 基于 Phase 3 完成后的全局深度审视报告制定。  
> 优先级逻辑：先让核心链路真正闭合 → 再补可靠性 → 最后考虑可度量性和产品化。

---

## 背景：深度审视发现的关键 Gap

| Gap | 性质 | 影响 |
|-----|------|------|
| Zone B 未接入 assembler | 功能性 | PCG "按需加载"停留在理论，长论文仍需手动逐 section 读取 |
| LLM client 缺 timeout + 错误分类 | 可靠性 | 50 轮审阅中 API 偶尔 5xx 导致整个 session 崩溃 |
| IntraContrast 无真实验证 | 价值证明 | 代码存在但缺乏"真实运行后证明有效"的证据 |
| 无 Mock-LLM 中间测试层 | 可测试性 | cognitive_loop 行为在 CI 中无法自动验证 |
| 无 Evaluation 基准 | 可度量性 | evolution 系统的"有效性"无法被量化 |

---

## Phase A：核心价值闭合（Short-term）

### A1. Zone B 动态加载接入 Assembler

**预计工时**：~2h  
**优先级**：P0  
**前置**：无

#### 现状

`TokenBudgetManager.compute_zone_b_allocation()` 已实现——能计算哪些 section 以 full/digest/name_only 粒度加载。但 `assembler.assemble()` 和 `tool_handlers/reading.py` 均未消费此分配结果。长论文下 Agent 仍需手动逐个 `read_section`，PCG 的"按需加载"停留在理论。

#### 实施步骤

1. **Harness 层**：在 `harness.format_context()` 中，调用 `assembler.assemble()` 之前，先调用 `token_budget_manager.compute_zone_b_allocation(pcg, current_task_section)` 获取分配方案，传入 assembler context dict

2. **Assembler 层**：新增 priority=77 的 section `"zone_b_paper_content"`：
   - `full_load` sections → 注入完整原文（从 `state.paper_sections` 取）
   - `digest_load` sections → 注入已有 digest（`state.section_digests` 优先，无则用 PCG `node.digest`）
   - `name_only` → 不注入（已由 `paper_overview` / `pcg_navigation` 覆盖名称列表）

3. **current_task_section 确定逻辑**：
   - 优先取 `state.sections_read[-1]`（最近读取的 section）
   - 无则取 PCG `coverage_gaps()` 第一个
   - 都无则传空字符串（ZoneBAllocation 返回空，不影响现有行为）

4. **Kill switch 守卫**：受 `GODEL_BUDGET_MANAGER_ENABLED` 控制，OFF 时完全跳过

5. **Token 溢出保护**：Zone B 注入后总 token 超限 → 按 LRU 降级 full→digest→name_only

#### 测试要求

- 单元测试：ZoneBAllocation 正确生成（含降级场景）
- 集成测试：`assemble()` 输出中包含 full_load section 原文内容，不超 `ZONE_B_MAX_TOKENS`
- 降级测试：`GODEL_BUDGET_MANAGER_ENABLED=0` 时输出与改动前一致

---

### A2. LLM Client Retry + Timeout 强化

**预计工时**：~1h  
**优先级**：P0  
**前置**：无（与 A1 独立并行）

#### 现状

`llm/client.py` 已有 5 次 exponential backoff retry，但缺少：
- per-request timeout（挂起请求可能无限等待）
- 针对不同 HTTP 状态码的分类处理
- 结构化日志（当前 print 到 stderr）

#### 实施步骤

1. 给 `client.chat.completions.create()` 添加 `timeout=httpx.Timeout(connect=10, read=120)`

2. retry 逻辑中区分异常类型：
   - `RateLimitError (429)` → 使用 response header `Retry-After` 秒数，否则 exponential
   - `APIConnectionError / Timeout` → 正常 exponential backoff
   - `BadRequestError (400)` → **不 retry**，直接抛出（prompt 层面问题）
   - `AuthenticationError (401)` → **不 retry**，直接抛出

3. 将 `print(file=sys.stderr)` 替换为 `logging.warning`

4. 新增构造参数 `total_timeout_seconds: int = 180`，单次 `chat()` 调用总耗时超过此限则放弃

#### 测试要求

- Mock OpenAI client，验证 429 → retry 正确等待
- 验证 400 → 立即抛出，不 retry
- 验证 total_timeout 超限 → raise TimeoutError

---

### A3. 真实长论文 E2E 验证 + IntraContrast 数据产出

**预计工时**：~4h  
**优先级**：P0  
**前置**：A1、A2 完成

#### 现状

IntraSessionContrastManager 存在且有 unit test，但缺乏"真实 50 页论文跑一次完整 session"的验证数据。Zone B 接入后需要实际验证效果。

#### 实施步骤

1. 准备一篇 50+ 页真实 ML 论文（section >= 15 以触发 IntraContrast 的 `INTRA_CONTRAST_MIN_SECTIONS=15` 阈值）

2. 编写 `scripts/e2e_long_paper_validation.py`：
   - 调用完整 `cognitive_loop` → `session_finalizer` 流程
   - 收集并输出：
     - `session_experiences_v3`（L1 经验记录）
     - `contrast_results`（IntraContrast A/B 对比数据）
     - `evidence_chains`（推理链追踪记录）
     - `zone_b_allocation_log`（每轮 Zone B 分配决策）

3. 输出验证报告（markdown）：
   - IntraContrast 是否产生了 delta 数据？
   - Evolution confidence 是否发生有意义的变化？
   - Zone B 集成后 Agent 是否减少了手动 `read_section` 调用次数？

#### 验收标准

- [ ] IntraContrast 产出至少 1 组有效 delta（treatment vs control）
- [ ] Zone B 每轮自动注入至少 1 个 full_load section 的原文
- [ ] session 正常结束，无崩溃

---

## Phase B：可靠性补全（Medium-term）

### B1. Mock-LLM 集成测试层

**预计工时**：~1d  
**优先级**：P1  
**前置**：无

#### 现状

测试要么不涉及 LLM（纯逻辑 992 tests），要么需要真实 API key（E2E scripts）。cognitive_loop 的信号响应、phase 转换、hypothesis 生成在 CI 中无法自动验证。

#### 实施步骤

1. 创建 `v2/tests/mock_llm.py`：
   - `MockLLMClient` 类：基于预定义 response sequence 返回模拟 tool_calls
   - `Scenario` dataclass：描述"第 N 轮返回什么 response"
   - 支持两种模式：严格顺序模式（按轮次） + 正则匹配模式（按 system prompt 内容决定）

2. 创建 3 个场景测试文件：

   | 文件 | 验证目标 |
   |------|---------|
   | `test_cognitive_loop_basic.py` | INITIAL_SCAN → read 3 sections → update_findings → done |
   | `test_cognitive_loop_signals.py` | signal dispatcher nudge + phase FSM 转换时机 |
   | `test_cognitive_loop_hypothesis.py` | generate_hypothesis → add_evidence → resolve |

3. 每个测试的关键断言：
   - Phase 转换时机正确
   - Doom loop guard 在超限时触发
   - Findings quality gate 拦截不合格 finding
   - Zone B 分配在有 PCG 时被调用

---

### B2. Evaluation Framework

**预计工时**：~2d  
**优先级**：P1  
**前置**：A3 完成（需要 E2E 可跑通）

#### 实施步骤

1. 创建 `evaluation/` 目录结构：
   ```
   evaluation/
   ├── gold_standard/          # 人工标注的理想审稿意见
   │   ├── paper_001.json      # {paper_path, findings: [{text, section, priority, category}]}
   │   └── ...
   ├── run_eval.py             # 评估主脚本
   ├── metrics.py              # precision/recall/F1 计算
   └── reports/                # 自动生成的评估报告
   ```

2. Gold-standard 数据集准备：
   - 5-10 篇论文，每篇附人工标注 findings（含 section、priority、category）
   - 格式固定为 JSON，方便脚本读取

3. 评估脚本 `run_eval.py`：
   - 对每篇论文跑 Agent → 产出 findings
   - 用语义相似度匹配（embedding cosine or fuzzy string）计算 precision/recall/F1
   - 对比维度：V2（kill switches 全关） vs V3（全开）
   - 输出 markdown 表格报告

---

### B3. AdaptiveConfig — 脚手架参数纳入 Evolution 可调范围

**预计工时**：~1d  
**优先级**：P2  
**前置**：A3 验证 evolution 系统确实产生了有意义的数据

#### 设计目标

将深度审视中标记为"第三类可商量的脚手架"的参数，从 hardcode 转为 evolution 系统可调（但有界约束）。

#### 实施步骤

1. 创建 `v2/core/adaptive_config.py`：
   ```python
   @dataclass
   class AdaptiveParam:
       name: str
       current_value: float
       min_bound: float      # 宪法层约束
       max_bound: float      # 宪法层约束
       evidence_count: int = 0

   @dataclass
   class AdaptiveConfig:
       params: dict[str, AdaptiveParam]
       # 包含: TRIGGER_INTERVAL, READINESS_THRESHOLD, phase transition thresholds 等
   ```

2. 让 `DeepReflector.apply_decisions_v3()` 支持新 decision type `"adjust_config"`：
   - 需要 evidence >= `EVIDENCE_CHAIN_MIN_FOR_MODIFY`（宪法层 = 3）
   - 每次调整幅度 clamp 到 ±20%
   - 不可突破 min/max bound

3. 持久化到 `memory.json`，session 启动时加载覆盖默认值

---

## Phase C：产品化基础（Long-term）

> 注：涉及 GitHub 上传的部分暂时 hold，先在本地完成可用。

### C1. 结构化日志 + Metrics Export

**预计工时**：~4h  
**状态**：待定

- Evolution confidence 变化、IntraContrast delta、DeepReflector decisions
- 统一输出到 `.workspace/metrics/` 目录
- JSON Lines 格式，每条带 `timestamp` + `session_id`
- 目标：人可以肉眼看到"系统在变好还是变差"

### C2. ~~CI/CD Pipeline~~

**状态**：HOLD（涉及 GitHub 上传）

~~GitHub Actions + pre-commit hook + type checking~~

### C3. PDF Ingestion 加固

**预计工时**：~4h  
**状态**：待定

- pymupdf → 增加 fallback（pdfplumber for tables）
- 论文 section 自动检测优化（two-column PDF 场景）
- 错误容忍：某个 section 解析失败不阻塞全局

### C4. Kill Switch 降级完整性验证

**预计工时**：~2h  
**状态**：待定

- 自动化测试：所有 V3 kill switches 设为 "0"
- 跑完整 mock-LLM 集成测试
- 验证行为等价 V2 且不崩溃
- 验证无 import error、无运行时异常

---

## 执行顺序与依赖图

```
A1 (Zone B) ────────────────────┐
                                ├──→ A3 (E2E Validation) ──→ B2 (Eval Framework)
A2 (LLM Retry) ── 独立并行 ─────┘                               │
                                                                 ↓
B1 (Mock-LLM) ── 独立 ──────────────────────────────→ C4 (Kill Switch Test)
                                                                 │
B3 (AdaptiveConfig) ── 依赖 A3 产出数据 ───────────→ C1 (Metrics)
```

**建议执行批次**：
- **Batch 1**（可并行）：A1 + A2
- **Batch 2**（依赖 Batch 1）：A3
- **Batch 3**（可并行）：B1 + B3
- **Batch 4**（依赖 B1 + A3）：B2 + C4
- **Batch 5**（按需）：C1 + C3

---

## 验收标准总表

| # | 检查项 | Phase | 状态 |
|---|--------|-------|------|
| 1 | Zone B 动态加载在 `assemble()` 路径中被实际消费 | A1 | ☐ |
| 2 | LLM 429/5xx 时自动 retry，400/401 时快速失败 | A2 | ☐ |
| 3 | 50+ 页真实论文 E2E 运行成功，产出 IntraContrast delta | A3 | ☐ |
| 4 | Mock-LLM 下 cognitive_loop 的 phase 转换可被自动验证 | B1 | ☐ |
| 5 | Evaluation 报告量化 V3 vs V2 的 finding precision/recall | B2 | ☐ |
| 6 | DeepReflector 可调整 AdaptiveConfig 参数（有界） | B3 | ☐ |
| 7 | 所有 V3 kill switches = 0 时系统退化为 V2 且不崩溃 | C4 | ☐ |
| 8 | 结构化 metrics 日志可观测 evolution 趋势 | C1 | ☐ |

---

## 根本性提醒（来自审视报告）

> 这个项目的价值更多在**架构思想和方法论**（认知循环、进化引擎、有界递归的设计模式），而不是作为"永远需要的产品"。当 LLM context window → 1M+ 且推理能力进一步提升时，Agent 架构的边际价值会递减。但在当前 128K window + GPT-4 级别能力下，这个架构确实有不可替代的价值。

保持这个认知清醒度：我们在完善的是一个**有时间窗口的最佳实践**，不是永恒的产品。
