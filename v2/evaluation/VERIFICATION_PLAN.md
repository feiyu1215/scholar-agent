# ScholarAgent V2 — 端到端验证方案

## 背景与目的

12 个自进化管道断裂点已全部修复，2777 单测通过。但单测验证的是"代码不报错"，不是"系统能干活"。本方案定义两条验证线，回答两个问题：

1. **系统此刻的审稿质量基线是多少？**（F1 多少、漏了什么类型的问题）
2. **学习管道在真实使用中是否真的"通"？**（经验是否积累、反思是否触发、后续 session 是否可观测地变好）

方案的消费者是执行验证的 agent 或人——读到本文档后应能自主执行，无需额外解释。

---

## 验证线 A：审稿质量基线

### 目标

用真实 LLM（gpt-4.1）跑 5 篇 gold standard 论文的完整审稿，得到真实 P/R/F1，建立"系统当前水平"的锚点。

### 前置条件

- API Key 已配置（环境变量 `OPENAI_API_KEY` 或项目配置文件）
- gold standard 文件：`v2/evaluation/gold_standard/paper_001.json` ~ `paper_005.json` 已就绪
- 评估框架：`v2/evaluation/run_eval.py --mode real` 可跑通

### 执行步骤

1. **环境确认**：确认 API Key 可用，模型为 gpt-4.1，跑一篇（paper_001）确认流程通畅
2. **全量运行**：`python3 -m evaluation.run_eval --mode real` 跑完全部 5 篇
3. **收集原始数据**：每篇论文保存 agent 产出的 findings 原文（JSON），便于后续人工审查
4. **计算指标**：Aggregate P/R/F1 + 每篇 per-paper + 每类 per-category
5. **人工抽检**：随机抽 2 篇，人工对比 agent findings vs gold findings，判断：
   - 被 gold 漏掉但 agent 发现的"真阳性"（gold 不完美的情况）
   - 被 gold 标为问题但 agent 没发现的"假阴性"（系统盲区）

### 判定标准

| 指标 | 预期下限 | 说明 |
|------|---------|------|
| F1 | ≥ 0.45 | Mock 模式 V2 baseline 为 0.56，真实 LLM 首次跑预期略低 |
| Recall (high/critical) | ≥ 0.50 | 严重问题至少发现一半 |
| 无崩溃 | 5/5 完成 | 无 exception 导致中途退出 |

若 F1 < 0.30 或任一论文崩溃 → 说明存在集成级 bug，优先修复而非继续。

### 产出物

- `evaluation/reports/eval_real_baseline_<timestamp>.md` — 自动生成的报告
- `evaluation/reports/raw_findings/` — 每篇的 agent 原始产出 JSON
- 人工抽检笔记（2-3 段文字，记录发现）

---

## 验证线 B：学习管道端到端贯通

### 目标

验证经过连续 N 次审稿后，自进化管道的每个环节是否真正工作：

- Phase 1（经验沉淀）：`MemoryState.procedures` 有新 pattern 产生且 evidence 累积
- Phase 2（元反思）：FastReflector / DeepReflector 在条件满足时真正触发
- Phase 3（习惯学习）：HabitLearner 产出 LearnedHabit，注入后续 session
- Phase 4（可观测进步）：后续 session 的 Findings 质量/数量有改善

### 前置条件

- 验证线 A 已通过（系统至少能正常审稿）
- 需要一个**隔离的 memory 目录**（不污染正式数据）
- 准备 3-5 篇**同类型**论文（建议经济学/计量经济学，确保 pattern 可积累）

### 实验设计

```
Session 1-3:   基础审稿，观察经验沉淀
Session 4-9:   观察 pattern evidence 是否增长
Session 10:    触发 FastReflector（COLD_START_SESSION_THRESHOLD = 10）
Session 11-12: 观察 DeepReflector 触发、习惯产出
Session 13+:   对比：带 LearnedHabit 的审稿 vs Session 1 的审稿质量
```

### 关键观测点（每个 session 结束后 dump）

每次 session 结束后，从 `memory.json` 中提取以下指标：

```python
{
    "session_id": N,
    "procedures_count": len(state.procedures),
    "procedures_with_evidence_ge_3": count(p for p in procedures if p.evidence_count >= 3),
    "anti_patterns_count": count(p for p in procedures if p.category == "anti_pattern"),
    "learned_habits_count": len(evolution_engine.learn()),
    "combination_log_entries": len(state.combination_log),
    "evolution_stats_entries": len(state.evolution_stats),
    "fast_reflect_triggered": bool,  # 从 session 日志判断
    "deep_reflect_triggered": bool,
    "maturity_levels": state.maturity_levels,
    "findings_count": N,  # 本次审稿产出
    "findings_f1": float,  # 对比 gold standard
}
```

### 判定标准

| 检查点 | 预期 | 失败含义 |
|--------|------|---------|
| Session 3 后 procedures 数量 > 0 | 经验沉淀正常 | extract_procedural_patterns 或 add_or_reinforce_procedure 有问题 |
| Session 5 后存在 evidence_count >= 2 的 pattern | 相似 pattern 正确合并 | `_is_similar()` 在真实数据上不工作 |
| Session 10 后 FastReflector 触发 | 冷启动守卫正确放行 | COLD_START_SESSION_THRESHOLD 逻辑有误 |
| Session 11-12 DeepReflector 触发 | LLM 反思链路通畅 | llm_call_fn 传递有问题或 prompt 解析失败 |
| 任意 session 后 HabitLearner 有产出 | 习惯生成正常 | 阈值太严或 pattern 数据不满足条件 |
| Session 13 的 F1 > Session 1 的 F1 | 系统在变好 | 进化管道虽然"通了"但产出的习惯没有价值 |

### 具体操作方式

有两种执行策略，执行者按实际条件选择：

**策略 A：真实 LLM 多 session（推荐但昂贵）**

- 用 gpt-4.1 跑 13+ session，每次审一篇不同的论文
- 所有 session 共享同一个 memory 目录
- 需要准备 13 篇论文（可以是同一篇用不同 section 审、或准备真实论文库）
- 预估成本：~$5-15（每 session ~$0.5-1）

**策略 B：混合模式（经济但部分可信度降低）**

- Session 1-9 用 mock + 人工注入合理的 procedures（模拟 9 次审稿的积累效果）
- Session 10-13 用真实 LLM，观察 meta-reflect 触发和习惯学习
- 优点：成本低（~$2-4），仍能验证 Phase 2-4
- 缺点：Phase 1 的沉淀质量未经真实验证

**策略 C：纯单测级模拟（零成本但可信度最低）**

- 构造一个 `test_evolution_e2e.py` 集成测试
- 模拟 13 个 session 的完整生命周期（mock LLM response）
- 验证每个环节在代码路径上被执行
- 不能验证"LLM 产出的反思质量"，但能验证管道连通性

### 产出物

- `evaluation/reports/evolution_pipeline_<timestamp>.json` — 每个 session 的指标 dump
- `evaluation/reports/evolution_pipeline_<timestamp>.md` — 人可读的进化轨迹报告
- 明确结论："管道通/不通"，如果不通，卡在哪个环节

---

## 执行顺序

```
1. 先跑验证线 A（1-2 小时，含 API 调用等待）
   → 如果失败：修复集成 bug 后重跑
   → 如果通过：记录 baseline 数字

2. 再跑验证线 B（策略选择取决于时间/预算）
   → 如果 Session 1-3 就卡住：说明经验沉淀有问题
   → 如果 Session 10 之后不触发 reflect：检查冷启动逻辑
   → 如果全通但 Session 13 没变好：进化产物质量问题，需要调整 prompt/阈值

3. 根据 A+B 的结果，产出「下一步优化优先级清单」
```

---

## 不做什么（边界）

- 不做"理想的评估框架"——不搞 cross-validation、不做统计显著性检验、不构建完美的 eval set
- 不做 UI/demo——纯命令行验证
- 不优化任何东西——验证阶段只观测、不改代码
- 不追求论文级严谨——这是工程验证，不是学术实验

---

## 风险与预案

| 风险 | 应对 |
|------|------|
| API 调用超时或限流 | 加 retry + exponential backoff，单篇失败不阻塞全量 |
| 论文内容太长超出 context window | 现有 harness 有压缩机制，观察是否生效；如不生效则缩短测试论文 |
| Gold standard 质量本身有问题 | 人工抽检时标注，作为"gold 修正建议"记录，不影响验证流程 |
| 多 session 共享 memory 时序列化出错 | 每个 session 结束后 backup memory.json，出错可追溯 |
| 进化管道"通了"但没效果 | 这本身就是一个重要发现——说明习惯设计/prompt 需要迭代，不是代码 bug |
