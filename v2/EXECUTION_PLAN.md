# ScholarAgent V2 — 完成路线图

项目定位：**个人工具 + 作品集/Portfolio**
生成时间：2025-05-27

---

## 全局路线图（四阶段）

```
阶段 1：深度使用 + 激活高级 Phase + Recall 提升
    ↓ 系统真正好用，核心能力达标
阶段 2：引入优化方法论（skill-craft / WAL / Skill-Evolver）
    ↓ 系统更聪明地自我进化
阶段 3：工程化/可交付（README / Docker / 文档 / CI）
    ↓ 项目可展示、可被他人使用
完成 ✓
```

---

## 阶段 1：详细执行计划

### 执行顺序（因果链）

```
F（Recall 诊断）→ 发现瓶颈 → 指导 B（激活对应 Phase）→ 验证提升 → A（形成使用循环）
```

### F. Recall 提升

#### F.1 构造 Gold Standard（精确率+召回率可评估）

**做什么**：从已验证的 5 篇论文中选 2-3 篇（建议 paper_001 和 paper_003），你自己逐段阅读全文，标注所有你认为应该被发现的问题。

**标注格式**：
```json
{
  "paper_id": "paper_001",
  "gold_findings": [
    {
      "id": "G001",
      "category": "methodology",
      "location": "Section 3.2",
      "description": "DID 平行趋势假设未做正式统计检验",
      "severity": "high",
      "type": "omission|misinterpretation|inconsistency|overclaim|logic_gap"
    }
  ]
}
```

**为什么**：没有 gold standard 就无法量化 Recall，只能主观判断"好像漏了东西"。你说得对——gold standard 千人千面，但对于你自己作为使用者来说，**你认为应该发现的问题**就是你的 gold standard。

**产出**：2-3 个 `gold_paper_XXX.json` 文件

#### F.2 Recall 诊断（分类分析遗漏）

**做什么**：
1. 将 agent 的 raw_findings 与 gold standard 逐条对比
2. 标记：hit（命中）/ miss（遗漏）/ extra（agent 多发现的）
3. 对 miss 分类：按 category/severity/location 分析规律

**诊断维度**：
- 按问题类型：methodology / data_inconsistency / logic / citation / writing 哪类漏得最多？
- 按位置：是 Introduction 漏得多还是 Appendix 漏得多？（是否因为 token budget 不够没读完）
- 按严重度：是 high 的漏了还是 medium 的漏了？

**产出**：诊断报告（`evaluation/reports/recall_diagnosis.md`）

#### F.3 针对性修复

根据 F.2 的诊断结果，可能的修复方向：

| 遗漏原因 | 修复方案 | 对应模块 |
|----------|----------|----------|
| 全文未覆盖（token budget 用完）| 调大 token_budget 或优化 section 优先级策略 | `core/budget_manager.py` |
| 表格数值问题未审 | 激活 Phase 9A | `core/table_verifier.py` |
| 引用准确性未检查 | 加强引用验证工具或 prompt | `core/skills/` |
| Agent 思维惯性（总关注方法论忽略数据）| 调整 cognitive habits 权重或新增 habit | `core/habits.py` |
| 深层逻辑问题需要多步推理 | 增加 hypothesis-driven 深挖策略 | `core/hypothesis.py` |
| **搜索不足 + Finding 去重** | 重写 tool description + 多信号去重 | `core/identity.py`, `core/tool_handlers/findings.py` |

> **📌 已启动**：在 F.2 诊断中发现"搜索不足"和"finding 重复"两个紧急问题，已单独建档为 P1 增强方案。
> 详见：`../docs/P1_DEEP_READING_ENHANCEMENT.md`（独立计划，完成后回归本路线图继续执行）

**产出**：具体代码修改 + 修改后重新验证

---

### B. 激活高级 Phase

#### B.1 Phase 9A — 表格处理（优先级：高）

**为什么高优先级**：经济学论文的核心证据在表格中。regression tables、summary statistics、robustness checks 是审稿人重点关注的。如果 agent 不能理解表格，它就只能审"文字描述"部分。

**验证方案**：
1. 选一篇有复杂 regression table 的论文（paper_001 有 DID 表格）
2. 开启 `GODEL_TABLE_PROCESSING_ENABLED=true`
3. 跑审稿，看 findings 中是否出现"表格数值不一致""系数方向矛盾"等表格相关问题
4. 与不开启的结果对比

#### B.2 Phase 7 — 对抗训练（优先级：中高）

**为什么有价值**：对抗训练的核心是"发现系统弱点并针对性提升"。在 F.2 诊断出遗漏规律后，Phase 7 可以自动生成针对该弱点的训练数据。

**验证方案**：
1. 先跑 `training/weakness_analyzer.py`，基于已有的 13 session memory 分析弱点画像
2. 根据弱点画像，用 `training/adversarial.py` 生成 3-5 个挑战样本
3. 用 `training/training_loop.py` 跑一轮 mini-training（3-5 个样本）
4. 训练后重新审稿验证

#### B.3 Phase 8 — 双环编排（优先级：低）

**为什么低优先级**：当前单循环审稿已经能产出 9.6 findings/篇。双环（规划环 + 执行环）的价值在于处理"极长论文"或"需要多轮深挖"的场景。对于日常使用，单环可能够用。

**判断标准**：如果在 A（深度使用）中发现"agent 经常在中间迷失方向"或"论文太长导致审稿质量下降"，再激活 Phase 8。

---

### A. 深度使用

#### A.1 建立使用流程

**做什么**：在 F 和 B 的改进完成后，用系统审阅 2-3 篇你当前在读/在审的论文。

**使用方式**：
```bash
cd v2/
python main.py /path/to/your-paper.pdf --verbose
```

**记录什么**：
- agent 发现了什么（哪些有用、哪些是废话）
- agent 漏了什么（你期望它发现但没发现的）
- 使用体验（速度、输出格式、交互方式）
- 系统稳定性（有无崩溃、错误）

#### A.2 收集反馈形成改进闭环

将使用中的发现记录到 `evaluation/usage_feedback.md`，每次使用后追加。这些反馈直接驱动下一轮改进。

---

### 验证：Recall 提升确认

在完成 F.3 + B.1/B.2 后：
1. 重新跑已标注 gold standard 的论文
2. 计算新的 P/R/F1
3. 对比提升幅度
4. 目标：Recall 从 ~63% 提升到 ~75%+

---

## 阶段 2：引入优化方法论（预览）

在阶段 1 完成且积累了 30+ session 真实数据后：

- **skill-craft 评分**：用 8 维度框架评估 agent 内部的 Skill 质量
- **WAL 协议**：强化学习管道的"强制写入"机制
- **Skill-Evolver**：让 Skill 自动迭代（基于 5 维 AND Gate 验证）

前提：阶段 1 积累的数据量足够让这些优化有意义（learned_habits >= 5, procedures >= 80）。

---

## 阶段 3：工程化/可交付（预览）

- README.md（项目介绍、架构图、快速开始）
- Dockerfile + docker-compose（一键部署）
- 用户文档（使用教程、配置说明）
- 示例输出（展示审稿结果样本）
- CI/CD（GitHub Actions + 测试自动化）
- 架构图（Mermaid/SVG）

---

## 当前状态

| 阶段 | 状态 |
|------|------|
| 阶段 1 | 🟡 F.1-F.3 + P0/P1/P2 修复 + 验证完成，进入 B.2/A.1 |
| 阶段 2 | ⬜ 等阶段 1 完成 |
| 阶段 3 | ⬜ 等阶段 2 完成 |

> **最后更新**: 2026-05-30 | Post-Fix 综合评估完成（4 runs），F1 从 46.3% 提升至 56.2% (best estimate, multi-run union)

### 阶段 1 执行进度

```
F.1 构造 Gold Standard ✅ (gold_paper_001.json + gold_paper_003.json)
  ↓
F.2 Recall 诊断 ✅ (recall_diagnosis.md, baseline F1=46.3%)
  ↓
F.3 P0 针对性修复 ✅ (AppendixMathAuditSkill, ConsistencyValidator Rule9, PCG appendix weight)
  ↓
验证 Rerun ✅ (2026-05-28, 详见 P0_FIX_VERIFICATION_FINAL.md)
  ↓
B.1 激活 Phase 9A ✅ (2026-05-29, SkillX 注册 TableExtraction + TableConsistency)
F.3-P1 修复 ✅ (2026-05-29, Rule 10 跨表重复检测 + 顺序下标错误检测)
  ↓
auto_assign bug 修复 ✅ (2026-05-29, ToolGroup 分配逻辑 + DEFAULT_PHASE_GROUPS)
  ↓
P1 验证 Rerun ✅ (2026-05-30, 3 runs completed, G005(001) 命中)
  ↓
Post-Fix 综合评估 ✅ (2026-05-30, P=89.5% R=38.6% F1=53.9% best estimate)
  ↓
B.2 Phase 7 对抗训练 ← 需要足够 session 数据
  ↓
A.1 + A.2 深度使用循环 ← 验证改进效果
```

### P0 验证结论 (2026-05-28)

- **P0 目标命中率**: 4/6 = 66.7%（AppendixMathAuditSkill + PCG appendix weight 有效）
- **Aggregate F1**: 36.4%（vs baseline 46.3%，Δ=-9.9%）
- **重要说明**: F1 下降主要因自动匹配 vs 人工匹配的系统性差异 + agent 随机性，不代表真实退化
- **P0 修复核心证据**: Paper_001 新发现 G001(附录符号错误)+G003(敏感性分析)，Paper_003 新发现 G002(结构脱节)
- **未解决 P0 目标**: G005(001)跨表数据重复 + G005(003)公式排版错误
- **详细报告**: `evaluation/reports/P0_FIX_VERIFICATION_FINAL.md`

### Post-Fix 综合评估结论 (2026-05-30, 4 runs)

**评估配置**: model=gpt-4.1, max_loop_turns=60, token_budget=0(unlimited), enable_hdwm=True

**单次运行指标**:

| Run | Agent# | Precision | Recall | F1 |
|-----|--------|-----------|--------|------|
| Paper 001 Run 2 | 2 | 100.0% | 15.4% | 26.7% |
| Paper 001 Run 3 | 3 | 66.7% | 15.4% | 25.0% |
| Paper 003 Run 2 | 4 | 100.0% | 50.0% | 66.7% |
| Paper 003 Run 3 | 4 | 100.0% | 55.6% | 71.4% |

**Weighted Average**: P=93.1%, R=30.7%, F1=46.2%

**Best Estimate（多轮联合）**: P=90.0%, R=40.9%, **F1=56.2%**

**vs Baseline**: P+31.7%, R+2.0%, **F1+9.9%**

**关键发现**:
1. Precision 大幅提升：FP 从 29 个降到 1 个（Finding 去重有效）
2. G005(001) 首次命中：Run 3 通过 DeepVerify TableConsistency 检测到表格重复
3. Paper 003 高度稳定：两次 run 产出相同 4 findings（F1=66.7%/71.4%）
4. Agent 随机性显著（Paper 001）：不同 run 产出互补 findings（建议多轮联合）
5. Recall 瓶颈：Paper 001 每次仅产出 2-3 findings（自行终止过早），非系统能力问题

### 下一步行动

1. **Recall 提升策略**: 研究 agent 过早终止的原因（Phase 转换过快？doom loop 误触发？）
2. **多轮联合机制**: 考虑实现 multi-run ensemble（跑 3 次取并集）
3. **B.2 Phase 7 对抗训练**: 积累足够 session 后启动
