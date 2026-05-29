# Eval 闭环实现计划（P0 具体执行方案）

> 基于 EXECUTION_PLAN.md P0，拆解为可逐步执行的步骤。

---

## 现状盘点

| 组件 | 状态 | 缺什么 |
|------|------|--------|
| `run_eval.py` 框架 | ✅ 有 CLI、数据类、加载/报告逻辑 | 缺 `evaluate_case()` 和 `run_level()` 实际执行 |
| Judge Prompts (4个) | ✅ review/rewrite/deai/search 各有完整 5 维度评分 | 已覆盖主要场景，暂不需要新增 |
| Rubrics (3个) | ✅ review/rewrite/deai YAML | 缺 search rubric（非阻塞） |
| Benchmark Cases | ❌ 4 个目录全空 | **核心缺口** |
| `eval/reports/` | 目录不存在 | 自动创建（代码已有 mkdir） |

---

## 步骤 1：填充 Benchmark Cases（~30 分钟）

### 设计原则

1. **全部虚构** — 不使用任何真实论文内容，开源安全
2. **经济学风格** — 用 DID/IV/RDD 等计量经济学术语，贴合你的领域
3. **缺陷精确** — 每个 case 有 1~3 个明确的 expected_issues，便于自动评分
4. **短而聚焦** — 每段 200~500 词，够触发检测但不浪费 token

### L1_format — 格式层（3 cases）

调用工具：`run_presubmission_checks(input_text)` （纯规则，零 LLM 成本）

| Case ID | 注入缺陷 | Expected Issues |
|---------|----------|-----------------|
| `L1_format_001` | 引用格式混乱：同一段内混用 (Author, Year) 和 [1] | `citation_format_inconsistent` |
| `L1_format_002` | 图表编号断裂：正文引用 Figure 3 但只有 Figure 1,2,4 | `figure_reference_undefined`, `figure_numbering_gap` |
| `L1_format_003` | 标题层级错误：从 ## 直接跳到 #### + 摘要超长 (400 词) | `heading_level_skip`, `abstract_too_long` |

### L2_logic — 逻辑层（3 cases）

调用工具：`review_paper()` 的单 reviewer 模式（需要 LLM，但单次调用）

| Case ID | 注入缺陷 | Expected Issues |
|---------|----------|-----------------|
| `L2_logic_001` | 因果跳跃：相关性直接当因果结论，无识别策略 | `causal_claim_unsupported`, `missing_identification` |
| `L2_logic_002` | 数据矛盾：Table 2 报告 N=500，正文说 "our 350 observations" | `data_inconsistency`, `sample_size_mismatch` |
| `L2_logic_003` | 结论超出证据：局部实验结果外推到全国政策建议 | `overgeneralization`, `external_validity_unaddressed` |

### L3_academic — 学术规范层（3 cases）

调用工具：`review_paper()` (full) + `verify_citations` 概念

| Case ID | 注入缺陷 | Expected Issues |
|---------|----------|-----------------|
| `L3_academic_001` | 引文捏造：引用 "Zhang et al. (2021)" 但这篇论文实际不存在 | `citation_unverifiable`, `potential_fabrication` |
| `L3_academic_002` | 方法不透明：只说 "we use standard methods" 无任何细节 | `methodology_opaque`, `replicability_concern` |
| `L3_academic_003` | 数据来源不透明 + 样本选择偏差未讨论 | `data_source_unclear`, `selection_bias_unaddressed` |

### L4_domain — 领域深度层（3 cases）

调用工具：`review_paper()` (full) — 测试领域专业度

| Case ID | 注入缺陷 | Expected Issues |
|---------|----------|-----------------|
| `L4_domain_001` | DID 平行趋势缺失：2017 政策冲击但不报告 pre-trend 检验 | `parallel_trends_missing`, `did_validity_threatened` |
| `L4_domain_002` | IV 排他性疑问：用"距港口距离"作工具变量但不讨论 exclusion | `exclusion_restriction_undefended`, `iv_validity_concern` |
| `L4_domain_003` | RDD 带宽选择不当：只报告一个 bandwidth 无 sensitivity | `bandwidth_sensitivity_missing`, `rdd_robustness_lacking` |

---

## 步骤 2：补全 run_eval.py 执行逻辑（~20 分钟）

需要新增的核心函数：

```python
async def evaluate_case(case: BenchmarkCase, client: LLMClient) -> JudgeScore:
    """
    1. 根据 case.level 选择工具：
       - L1 → run_presubmission_checks(case.input_text)
       - L2/L3/L4 → 单次 review（用 case.input_text 作为论文内容）
    2. 获取工具输出
    3. 加载对应 judge_prompt
    4. 将 {case.input_text, tool_output, expected_issues} 发给 LLM 评分
    5. 解析 LLM 返回的 JSON 评分
    6. 返回 JudgeScore
    """

async def run_level(level: str, client: LLMClient) -> EvalReport:
    """
    1. load_benchmarks(level)
    2. 对每个 case: await evaluate_case(case, client)
    3. generate_report(scores, level)
    4. save_report(report)
    5. 打印 format_report_summary(report)
    """
```

### L1 的特殊处理

L1 用 `run_presubmission_checks()` — 这是零 LLM 的纯规则检测。但评分仍需要 judge：
- 工具输出 = presubmission report（哪些 check 通过/失败）
- Judge 评估的是：工具是否正确检出了 expected_issues（precision + recall）
- 对于 L1，可以用规则评分（不需要 LLM judge）— **可选优化**

### L2/L3/L4 的处理

这些需要 LLM 审稿。问题是 `review_paper()` 当前从 `.workspace/paper/` 读取解析后的文件。

**方案**：不走完整的 review_paper() 流程，而是做 "mini review"：
```python
async def _mini_review(text: str, client: LLMClient, depth: str = "single") -> str:
    """用 review 的 system prompt 但直接传入文本，不依赖 .workspace"""
```

这样 eval 不依赖全局状态，每个 case 独立。

---

## 步骤 3：验证闭环可跑（~10 分钟）

```bash
# 1. dry-run: 确认 case 加载成功
python3 -m eval.run_eval --level L1 --dry-run

# 2. 实际运行 L1（应该很快，因为 presubmission_check 是零 LLM）
python3 -m eval.run_eval --level L1

# 3. 运行 L2（需要 LLM，1 case = 1~2 次调用）
python3 -m eval.run_eval --level L2

# 4. 查看报告
cat eval/reports/eval_*.json
```

验收标准：
- [ ] `--dry-run` 列出 12 个 case（4 层 × 3 个）
- [ ] L1 跑完生成 JSON 报告，有 per-case 分数
- [ ] L2 跑完（~3 次 LLM 调用 + 3 次 judge 调用 ≈ 6 次 Friday 调用）
- [ ] 改一个 prompt 词，重跑，分数有变化

---

## 步骤 4：首轮 Judge Prompt 校准（后续迭代）

1. 跑完 12 个 case
2. 人工看哪些打分离谱（模型认为 5 分但明显有问题，或反过来）
3. 针对性调 judge prompt（加 few-shot 例子 / 调维度权重）
4. 重跑，对比分数变化

这一步**不阻塞**步骤 1~3 的完成，可以后续迭代。

---

## 执行顺序

```
步骤 1.1: 写 L1 的 3 个 case JSON     ← 最简单，验证格式
步骤 1.2: 改 run_eval.py 补 L1 评测逻辑 ← L1 不需要 LLM，快速验证管道
步骤 1.3: 跑通 L1 dry-run + 实际执行    ← 确认端到端可行
步骤 1.4: 写 L2~L4 的 9 个 case JSON
步骤 1.5: 补 run_eval.py 的 L2~L4 评测逻辑（mini_review + judge）
步骤 1.6: 全量执行 + 报告
```

**预估 LLM 调用量**：
- L1: 0 次工具调用 + 3 次 judge = 3 次 LLM
- L2: 3 次 mini_review + 3 次 judge = 6 次 LLM
- L3: 3 次 mini_review + 3 次 judge = 6 次 LLM
- L4: 3 次 mini_review + 3 次 judge = 6 次 LLM
- 总计：~21 次 Friday 调用，按 12s 间隔 ≈ 4~5 分钟

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| Friday 429 限流 | 12s 间隔 + 指数退避重试 |
| judge 输出不是有效 JSON | parse 失败时记录 raw + 标记 parse_error |
| mini_review 输出格式与 judge 期望不匹配 | judge prompt 中明确说明输入格式 |
| L1 规则检测 false positive/negative | 这恰恰是 eval 要暴露的——基线数据 |

---

## 一句话决策点

要开始执行吗？如果是，我从步骤 1.1 开始（写 L1 的 3 个 benchmark case JSON 文件）。
