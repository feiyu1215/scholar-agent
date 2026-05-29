# ScholarAgent 用户指南

本文档面向使用者——你只需要知道如何运行 Agent、如何配置参数、如何理解输出。不需要了解内部实现。

---

## 目录

1. [安装与配置](#1-安装与配置)
2. [基本使用](#2-基本使用)
3. [运行模式](#3-运行模式)
4. [Persona 切换](#4-persona-切换)
5. [Token Budget 控制](#5-token-budget-控制)
6. [Kill Switch 配置](#6-kill-switch-配置)
7. [评估系统](#7-评估系统)
8. [API 参考](#8-api-参考)
9. [常见问题](#9-常见问题)

---

## 1. 安装与配置

### 环境要求

- Python ≥ 3.10
- 一个 OpenAI-compatible API key（OpenAI / Together / Groq / Ollama / vLLM 均可）

### 安装步骤

```bash
git clone https://github.com/your-username/scholar-agent.git
cd scholar-agent
pip install -r v2/requirements.txt
```

依赖极简：仅 `openai`、`pymupdf`、`python-dotenv` 三个包。

### 配置 API

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```bash
# 必填：API Key
OPENAI_API_KEY=your-api-key-here

# 可选：自定义 endpoint（默认 https://api.openai.com/v1）
OPENAI_BASE_URL=https://api.openai.com/v1

# 可选：模型选择（默认 gpt-4.1-mini）
LLM_MODEL=gpt-4.1
LLM_MODEL_HIGH=gpt-4.1          # 深度推理任务
LLM_MODEL_MEDIUM=gpt-4.1-mini   # 结构化任务（consolidation、routing）
LLM_MODEL_LOW=gpt-4.1-mini      # 简单提取
```

支持任何 OpenAI-compatible 接口。如果你用 Ollama 本地部署：

```bash
OPENAI_BASE_URL=http://localhost:11434/v1
LLM_MODEL=llama3.1:70b
```

---

## 2. 基本使用

### 审阅一篇论文

```bash
python v2/main.py path/to/paper.pdf
```

Agent 会自动：
1. 解析 PDF 为 section 级别的结构化文本
2. 生成审稿策略（cognitive hints）
3. 进入认知循环：自主阅读、推理、记录发现
4. 完成后输出结构化的审稿意见

### 交互式追问

审稿完成后，你可以继续对话：

```
> 你觉得这篇论文的 DID 设计有什么问题？
> 帮我看看 Table 3 的数据是否一致
> 总结一下所有 high priority 的问题
```

### 交互命令

在交互模式中，以下命令可用：

| 命令 | 功能 |
|------|------|
| `quit` / `exit` | 退出 |
| `stats` | 显示当前 token 消耗统计 |
| `findings` | 列出所有已发现的问题 |
| `models` | 显示当前模型配置 |
| `switch <model>` | 切换 LLM 模型 |
| `budget` | 显示 token budget 使用情况 |

---

## 3. 运行模式

### Interactive 模式（默认）

```bash
python v2/main.py paper.pdf --mode interactive
```

Agent 自主审阅后进入对话模式，你可以追问、要求深挖、或请求修改建议。

### Full 模式（三阶段协作）

```bash
python v2/main.py paper.pdf --mode full
```

自动执行三阶段流程：
1. **Scholar 初审**：以审稿人身份发现问题
2. **Writer 修改**：以作者身份提出修改方案
3. **Scholar 复审**：验证修改是否解决了问题

适合需要完整审稿+修改建议的场景。

---

## 4. Persona 切换

```bash
python v2/main.py paper.pdf --persona scholar   # 默认：审稿人视角
python v2/main.py paper.pdf --persona writer     # 作者视角（侧重修改建议）
python v2/main.py paper.pdf --persona code_reviewer  # 代码审查视角
```

不同 persona 影响：
- Agent 的认知身份和关注点
- 可用工具集（writer 有更多编辑工具）
- 输出风格（scholar 侧重问题发现，writer 侧重解决方案）

---

## 5. Token Budget 控制

### 基本用法

```bash
# 默认 100K token budget
python v2/main.py paper.pdf

# 大预算（深度审阅）
python v2/main.py paper.pdf --budget 500000

# 小预算（快速扫描）
python v2/main.py paper.pdf --budget 30000

# 无限制（不推荐，可能很贵）
python v2/main.py paper.pdf --budget 0
```

### Budget 工作原理

Budget 是**安全网**，不是行为引导：
- Agent 在运行期间**完全不知道** budget 存在
- Agent 自由运行，直到 token 消耗达到上限
- 达到上限时，Agent 被硬截断，状态自动保存为 checkpoint
- 你可以用更多 budget 恢复（断点续传）

### 断点续传

```python
import asyncio
from v2.core.agent import ScholarAgent

async def resume():
    result = await ScholarAgent.resume(
        checkpoint_path=".checkpoints/",
        new_token_limit=100000  # 追加预算
    )
    print(result)

asyncio.run(resume())
```

### 推荐 Budget 设置

| 场景 | 推荐 Budget | 预期轮次 | 预期 Findings |
|------|------------|---------|--------------|
| 快速扫描 | 30,000 | 5-8 | 2-4 |
| 标准审阅 | 100,000 | 15-25 | 5-10 |
| 深度审阅 | 300,000 | 30-50 | 8-15 |
| 完整审阅 + HD-WM | 500,000+ | 40-60 | 10-20 |

---

## 6. Kill Switch 配置

所有高级功能都可以通过环境变量独立开关。默认全部 ON（除 Streaming 和 V2Contrast）。

### 常用配置

```bash
# 关闭表格处理（如果论文没有复杂表格）
export SCHOLAR_GODEL_TABLE_PROCESSING=0

# 关闭对抗训练（节省资源）
export SCHOLAR_GODEL_ADVERSARIAL_TRAINING=0

# 关闭双环架构（简化执行）
export SCHOLAR_GODEL_DUAL_LOOP=0

# 开启流式输出
export SCHOLAR_GODEL_STREAMING=1
```

### 完整 Kill Switch 列表

| 环境变量 | 默认 | 功能 |
|----------|------|------|
| `SCHOLAR_GODEL_PCG` | ON | Paper Cognition Graph 构建 |
| `SCHOLAR_GODEL_BUDGET` | ON | Token Budget Manager |
| `SCHOLAR_GODEL_DISPATCHER` | ON | 统一信号调度器 |
| `SCHOLAR_GODEL_EVIDENCE_CHAIN` | ON | 证据链追踪 |
| `SCHOLAR_GODEL_SECTION_EXP` | ON | Section 级经验记录 |
| `SCHOLAR_GODEL_INTRA_CONTRAST` | ON | 会话内 A/B 对比 |
| `SCHOLAR_GODEL_FAST_REFLECT` | ON | 快速反思（零 LLM） |
| `SCHOLAR_GODEL_DEEP_REFLECT` | ON | 深度反思（LLM 驱动） |
| `SCHOLAR_GODEL_EMERGENCY` | ON | 紧急反思（实时） |
| `SCHOLAR_GODEL_SKILL_LOADING` | ON | Skill 动态加载 |
| `SCHOLAR_GODEL_SKILLX` | ON | SkillX 三层技能体系 |
| `SCHOLAR_GODEL_DEEP_VERIFY` | ON | 深度验证自动触发 |
| `SCHOLAR_GODEL_LOOP_GUARD` | ON | 循环模式检测 + 恢复 |
| `SCHOLAR_GODEL_TABLE_PROCESSING` | ON | 表格处理与数值验证 |
| `SCHOLAR_GODEL_FIGURE_SEMANTIC` | ON | 图表语义理解 |
| `SCHOLAR_GODEL_DUAL_LOOP` | ON | 双环架构编排 |
| `SCHOLAR_GODEL_ADVERSARIAL_TRAINING` | ON | 对抗自训练 |
| `SCHOLAR_GODEL_ADVERSARIAL_RED` | ON | Red Team |
| `SCHOLAR_GODEL_ADVERSARIAL_BLUE` | ON | Blue Team |
| `SCHOLAR_GODEL_ADVERSARIAL_ELO` | ON | ELO 评分 |
| `SCHOLAR_GODEL_ADVERSARIAL_SEASON` | ON | 赛季管理 |
| `SCHOLAR_GODEL_STREAMING` | **OFF** | 流式输出 |
| `SCHOLAR_GODEL_V2_CONTRAST` | **OFF** | V2 随机对比 |
| `SCHOLAR_GODEL_SUB_READER_ROUTING` | ON | 子视角模型路由 |
| `SCHOLAR_GODEL_HABIT_PROGRESSIVE` | ON | 认知习惯渐进加载 |
| `SCHOLAR_GODEL_META_HARNESS` | ON | Meta-Harness 评估 |
| `SCHOLAR_GODEL_SKILL_SYNTHESIS` | ON | 运行时 Skill 合成 |
| `SCHOLAR_GODEL_REFLECTION_ADAPTIVE_DEPTH` | ON | 反思深度自适应 |
| `SCHOLAR_GODEL_REFLECTION_COMPARATIVE` | ON | 对比反思 |

### 推荐配置组合

**最小配置**（快速、低成本）：
```bash
SCHOLAR_GODEL_DUAL_LOOP=0
SCHOLAR_GODEL_ADVERSARIAL_TRAINING=0
SCHOLAR_GODEL_FIGURE_SEMANTIC=0
SCHOLAR_GODEL_DEEP_REFLECT=0
```

**完整配置**（最高质量）：
```bash
# 所有默认 ON，额外开启：
SCHOLAR_GODEL_STREAMING=1
```

---

## 7. 评估系统

### 运行评估

```bash
cd v2/

# 评估所有 gold standard 论文
python -m evaluation.run_recall_verification

# 只评估某篇
python -m evaluation.run_recall_verification --paper paper_001

# 使用不同模型
python -m evaluation.run_recall_verification --model gpt-4.1-mini
```

### Gold Standard 格式

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
      "type": "omission"
    }
  ]
}
```

### 评估指标

- **Precision**：Agent 报告的 findings 中，有多少是真实问题
- **Recall**：Gold standard 中的问题，Agent 发现了多少
- **F1**：Precision 和 Recall 的调和平均

匹配算法使用 Jaccard 相似度 + section 位置加权，阈值 0.4。

---

## 8. API 参考

### ScholarAgent 构造函数

```python
from v2.core.agent import ScholarAgent

agent = ScholarAgent(
    paper_path="paper.pdf",       # 论文路径（PDF/Markdown）
    model="gpt-4.1",              # LLM 模型名
    verbose=True,                 # 详细日志
    max_loop_turns=30,            # 最大循环轮次
    token_budget=100000,          # Token 预算
    context_window=128000,        # Context window 大小
    persona="scholar",            # 认知身份
    enable_hdwm=False,            # 假说驱动工作记忆
    reference_paths=None,         # 参考文献路径列表
    content_sections=None,        # 预加载的 sections（跳过解析）
    on_stream=None,               # 流式回调函数
    budget_policy=None,           # BudgetPolicy 对象（覆盖 token_budget）
)
```

### 运行审阅

```python
import asyncio

async def review():
    agent = ScholarAgent(
        paper_path="my_paper.pdf",
        model="gpt-4.1",
        max_loop_turns=30,
        token_budget=200000,
        enable_hdwm=True
    )
    result = await agent.start()
    
    # 获取 findings
    findings = agent.get_findings()
    for f in findings:
        print(f"[{f['priority']}] {f['title']}")
        print(f"  Section: {f.get('section', 'N/A')}")
        print(f"  Evidence: {f.get('evidence', 'N/A')}")
    
    return result

asyncio.run(review())
```

### 追问（多轮对话）

```python
async def review_and_chat():
    agent = ScholarAgent(paper_path="paper.pdf", model="gpt-4.1")
    await agent.start()
    
    # 追问
    response = await agent.chat("请详细解释 Finding #3 的证据")
    print(response)
    
    response = await agent.chat("Table 2 的数据有没有不一致的地方？")
    print(response)
```

### BudgetPolicy（高级）

```python
from v2.core.budget_policy import BudgetPolicy

policy = BudgetPolicy(
    token_limit=200000,
    allow_pause=True  # 允许断点续传
)

agent = ScholarAgent(
    paper_path="paper.pdf",
    model="gpt-4.1",
    budget_policy=policy
)
```

---

## 9. 常见问题

**Q: 支持哪些论文格式？**

PDF 和 Markdown。PDF 通过 pymupdf 解析为 section 级文本。Markdown 直接按标题分割。

**Q: 一次审阅大概花多少钱？**

取决于模型和论文长度。以 gpt-4.1-mini 审阅一篇 20 页论文为例，标准审阅（100K budget）大约消耗 $0.5-1.0。用 gpt-4.1 则约 $3-5。

**Q: 可以审阅中文论文吗？**

可以。Agent 的认知循环和工具链对语言无感知。Skills 中包含 `chinese_academic_standards.md` 提供中文学术规范知识。

**Q: 为什么有时候 Agent 发现的问题很少？**

Agent 有随机性。不同 run 可能发现不同的问题。建议：(1) 增大 `--max-turns` 和 `--budget`；(2) 开启 `--hdwm` 激活假说驱动模式；(3) 多跑几次取并集。

**Q: 如何只做审阅不做修改？**

默认的 `scholar` persona 就是纯审阅模式。只有 `--mode full` 或 `--persona writer` 才会产出修改建议。

**Q: 支持 Ollama / 本地模型吗？**

支持。设置 `OPENAI_BASE_URL=http://localhost:11434/v1` 和 `LLM_MODEL=your-model-name`。但注意：本地模型的推理能力可能不足以支撑深度审阅，建议至少使用 70B 参数级别的模型。

**Q: Kill Switch 关闭某个功能会影响其他功能吗？**

不会。每个 Kill Switch 独立控制一个子系统，关闭后该子系统优雅降级（跳过），不影响其他模块。这是设计原则之一。

---

*End of User Guide*
