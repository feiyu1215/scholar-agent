# 会话交接文档

> **最后更新**：2026-05-23，Phase 34 完成后
> **用途**：新会话启动时，先读本文件获取完整上下文。

---

## 一、项目概要

**项目名称**：ScholarAgent  
**项目路径**：`/Users/yanfeiyu03/Downloads/scholar-agent-public`  
**本质**：一个真正能像人类专家一样思考的认知 Agent——不是 workflow engine，不是工具编排器。第一个人格实例是「学术审稿人」。

**核心哲学**（COGNITIVE_ANCHOR）：
- Agent = 认知（cognition），不是管道（plumbing）
- LLM = 无状态 CPU；Harness = 寄存器 + 内存 + 总线
- 深度是自主涌现的，不是配置的
- 流程从目标中涌现，不是预设的

**API 配置**（`.env`）：
```
OPENAI_API_KEY=2003426817264898139
OPENAI_BASE_URL=https://aigc.sankuai.com/v1/openai/native
LLM_MODEL=gpt-4.1
```

---

## 二、当前进度坐标

**已完成到 Phase 34**。完成了 34 个迭代 Phase，268+ 单元测试全绿。

### Phase 34 结论（最新）

用真实 QJE 论文（Chan, Gentzkow & Yu 2022, "Selection with Variation in Diagnostic Skill"）对 Agent 做方法论深度压力测试：

| 维度 | 结果 | 判定 |
|------|------|------|
| 方法论深度 | 2/7 核心追问方向命中 | ❌ FAIL |
| 认知行为 | 反思1次 + 高优发现5条 + 非线性阅读 | ✓ PASS |
| 战略性阅读 | 选择性 76% (只读6/25 sections) | ✓ PASS |

**根因**：Agent 是"优秀的读者"而非"挑剔的审稿人"——理解能力强但批判性追问不够。具体表现是 Agent 会总结"论文的 quasi-random assignment 假设有较充分检验"，但不会追问"如果轻症肺炎自愈不回来，会怎样影响 miss rate 的测量？"

---

## 三、Phase 35 待执行方向

**目标**：让 Agent 从"理解模式"进入"质疑模式"。

**方案（Option A，Identity 层）**：在 `core/identity.py` 的认知身份中加入"假设边界审视"的认知习惯。核心理念：当你理解了论文的核心假设后，你的下一步不是验证论文是否正确地实现了它的方法，而是追问这些假设本身可能在哪里不成立。

**为什么不选其他方案**：
- Option B（Tool 层加 `challenge_assumptions`）= Theater Code（§3.5 反模式）
- Option C（Harness 层注入催促）= 控制而非认知（违反 §4.3）

**验证方法**：修改 identity.py → 重跑 `test_e2e_phase34_methodology.py` → 目标从 2/7 提升到 ≥3/7。

---

## 四、另一个待处理问题：PDF 解析能力

**问题**：经济学论文五六十页是常态，当前 `core/pdf_loader.py` 的 pymupdf + regex heading 方案局限性：
- 有些 PDF 提取出 0 字符（下载不完整/格式问题）
- section heading 识别不精确（重复 key、内容截断）
- 数学公式/脚注区域干扰分割
- 长 PDF 下载可能超时

**可能方向**：
1. 改进 regex 匹配逻辑的鲁棒性
2. 引入更强的 PDF 解析库（如 docling、marker-pdf）
3. LLM 辅助分段（用 LLM 判断 section 边界）
4. 支持用户手动上传已分好段的 markdown

---

## 五、核心文件地图

### 必读文件（开始工作前先读）

| 文件 | 用途 | 优先级 |
|------|------|--------|
| `docs/COGNITIVE_ANCHOR.md` | 第一性原理锚点，所有设计决策的参照系 | ★★★ |
| `docs/PROGRESS.md` | 完整进度追踪，Phase 1-34 的实验结果和结论 | ★★★ |
| `docs/REFERENCES.md` | 6篇Agent参考文章 + 1篇学术测试论文的完整记录 | ★★ |
| `.env` | API 配置（Friday/gpt-4.1） | ★★ |

### 正在修改的文件（Phase 35 会改动）

| 文件 | 说明 |
|------|------|
| `core/identity.py` | Agent 的认知身份定义 + 工具 schema。Phase 35 将在此加入"假设边界审视"习惯 |
| `core/test_e2e_phase34_methodology.py` | 方法论深度评估测试脚本（Phase 35 验证用） |

### 核心代码模块

| 文件 | 职责 |
|------|------|
| `core/agent.py` | Agent 入口，组装 identity + harness + loop |
| `core/loop.py` | 认知循环引擎（while loop + LLM call + tool exec） |
| `core/harness.py` | 状态守护层：WorkspaceState + 工具执行 + context 压缩 |
| `core/identity.py` | 认知身份 SCHOLAR_IDENTITY + 工具定义 SCHOLAR_TOOLS |
| `core/metacognition.py` | CognitiveState（策略/假说/置信度/开放问题） |
| `core/offload.py` | OffloadStore（无损存储 + 按需召回） |
| `core/memory.py` | 跨会话记忆（Session Memory） |
| `core/pdf_loader.py` | PDF → sections 转换器 |
| `llm/client.py` | LLM API 客户端（OpenAI compatible） |

### 测试文件

| 文件 | 说明 |
|------|------|
| `core/test_phase32_metacognition.py` | Phase 32 元认知单元测试（11 tests） |
| `core/test_e2e_phase32_cognition.py` | Phase 33 E2E 真实 LLM 认知验证 |
| `core/test_e2e_phase34_methodology.py` | Phase 34 方法论深度评估（7维标准） |
| `tests/` | 161+ 个单元测试（全绿） |
| `tests/papers/radiology_selection.pdf` | Chan et al. 测试论文 PDF |

### 测试报告

| 文件 | 说明 |
|------|------|
| `core/e2e_report_phase33.json` | Phase 33 结果（得分 4/5） |
| `core/e2e_report_phase34.json` | Phase 34 结果（方法论 2/7，认知行为 PASS） |

---

## 六、关键设计决策回顾

### Phase 33 发现的核心模式

**"Optional = 不用" 的 LLM 行为模式**：
- 工具 schema 中 `required` vs `optional` 对 LLM 行为有显著影响
- `cognitive_update` 从 optional → required 后，使用率从 0% → 100%
- 工具描述的 framing 极其重要——"核心产出" vs "可选附加"

### Phase 34 发现的核心模式

**"理解 ≠ 质疑" 的认知层次差异**：
- Agent 能准确理解论文做了什么（描述性总结）
- 但缺乏"即使论文做对了，还有什么假设边界值得追问"的深层思维
- 这是认知身份层面的问题，不是工具或基础设施问题

### 架构选择（不可妥协）

- **不做 workflow routing**：Agent 在每一步思考"基于当前理解，我下一步最应该做什么？"
- **不做 theater code**：LLM 能推理的事不包装成 Tool
- **Harness 只约束不控制**：约束边界（token budget、doom loop guard），不决定流程
- **Schema 引导 > 代码强制**：用 schema 设计引导 LLM 行为，而非 if-else 逻辑控制

---

## 七、注意事项

1. **每次开始工作前**：先读 `COGNITIVE_ANCHOR.md`（确认设计方向没偏离），再读 `PROGRESS.md` 末尾（确认当前位置）

2. **不要陷入单一模块的执念**：用户明确说过"该深入的就深入，差不多了就可以换一个地方做工"。Phase 32-33 在 metacognition 模块上已经深入够了，Phase 34 换到了端到端认知质量方向。

3. **做认知不做管道**：任何新功能先问"这是增强了 Agent 的认知能力，还是只是在搭水管？"

4. **测试验证闭环**：每次改 identity.py → 跑 E2E 测试 → 记录到 PROGRESS.md。不凭感觉判断"改好了"。

5. **PDF 问题是已知的**：暂时不影响主线开发（有 radiology_selection.pdf 能用），但长期需要改进。

6. **Skill 使用**：项目本身不使用外部 skill，它就是在从零构建一个 cognitive agent。但"serious-mode" skill 的精神需要贯穿始终——认真执行，不偷懒降级。

---

## 八、用户偏好

- 用户是经济学背景的研究者，关注方法论严谨性
- 用户强调 Agent = cognition not plumbing
- 用户讨厌过度工程化和 theater code
- 用户要求"该深入就深入，差不多了换方向"——保持全局视角
- 用户希望看到真实的进步（E2E 测试分数提升），不要虚假的"已改进"
- 沟通语言：中文为主，技术术语保持英文

---

## 九、快速启动命令

```bash
# 进入项目
cd /Users/yanfeiyu03/Downloads/scholar-agent-public

# 跑单元测试（确认代码没坏）
python3 -m pytest core/test_phase32_metacognition.py -x -q  # 11 tests, <1s

# 跑 Phase 34 E2E 测试（需要 API，~90s，~180k tokens）
python3 -m core.test_e2e_phase34_methodology

# 交互式审稿（手动测试）
python3 -m core.agent tests/papers/radiology_selection.pdf
```

---

## 十、下一步具体行动（Phase 35 开工清单）

1. 读 `docs/COGNITIVE_ANCHOR.md` §4.3（约束而非控制）
2. 读 `core/identity.py` 中 SCHOLAR_IDENTITY 的完整内容
3. 在认知习惯中加入一条新的"假设边界审视"习惯（§ 编号建议放在现有第 4 条"方法论审视"之后）
4. 核心内容方向：
   - "当你理解了一个方法论假设并确认论文做了检验后，你不会停在'检验通过了'——你会追问'这个假设在什么条件下可能不成立？论文的检验是否能覆盖所有失效模式？'"
   - "对每一个关键识别假设，你会想象一个怀疑者的视角：如果我想推翻这个假设，我会从哪个角度进攻？"
5. 跑 `python3 -m core.test_e2e_phase34_methodology` 验证提升
6. 记录结果到 `docs/PROGRESS.md`
