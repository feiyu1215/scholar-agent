# ScholarAgent V2 — 端到端深度验证计划 (A.1)

> 目标：通过 6 个精心设计的场景，验证 16 个功能子系统在真实审稿中的协作是否正常。
> 不是逐模块单测——每个场景覆盖一组功能在特定条件下的完整运行时行为。
>
> 创建日期：2025-07

---

## 一、已验证 vs 未验证功能

### 已有 E2E 验证覆盖的功能（来自 walkthrough_e2e_output.log）

| 功能 | 验证来源 | 观测到的行为 |
|------|---------|-------------|
| Phase 自动转换 (initial_scan → deep_review) | Turn 1 | `sections_read=6 → auto transition` |
| Spawn Parallel Readers | Turn 2, 6 | 3 子视角并行，MCL 路由 low/medium/high |
| MCL Stagnation Detection | Turn 3 | `核心章节覆盖率不足` 诊断 + 建议 |
| MCL Completion Block + Auto-Spawn | Turn 6 | `mark_complete` 被拦截 + 自动 spawn 3 视角 |
| Finding 去重（三信号） | Turn 5 | `术语重合 100%` 拒绝重复 |
| Compaction Engine | Turn 8+ | `压缩 76234 → 43362 chars (44% saved)` 反复触发 |
| Signal Dispatcher | Turn 2,6,10 | 多种信号按优先级注入 |
| Doom Loop 硬终止 | Turn 17 | `已达到硬性上限，强制结束` |
| Boundary Guard 认知产出监控 | Turn 10 | `连续读了 3 轮未记录发现` |
| Finding 状态更新 (verify) | Turn 14 | `needs_verification → verified` |

### 本计划需要验证的功能（从未或仅部分验证过）

| 功能子系统 | 未验证内容 | 风险等级 |
|-----------|-----------|---------|
| **HD-WM 假说生命周期** | 假说生成 → 证据积累 → 解决的完整周期 | 中 |
| **HD-WM Auto-Enhance** | `update_findings(needs_verification)` 自动生成假说 | 中 |
| **Writer Persona** | 编辑工具链（edit_paragraph, reword_sentence, insert_content） | 高 |
| **Edit 验证闭环 (EDIT-5)** | `verify_edit()` + PASS/WARN/FAIL 三级反馈 | 高 |
| **CollaborativeReview** | Scholar→Writer→Scholar 三阶段协作 | 高 |
| **Persona Switch** | Agent 自主调用 `switch_persona` 切换认知身份 | 中 |
| **Kill Switch 降级** | 关闭 MCL/PCG/BudgetManager 时的优雅降级 | 低 |
| **Intent 参数** | `--intent` 对审稿策略聚焦的影响 | 低 |
| **Skill System** | economics skills 实际注入 + 模板匹配（DID/RCT） | 中 |
| **Adaptive Config** | 温度/max_nudges 随 phase 动态变化 | 低 |
| **Evolution Engine** | HabitLearner + EditExperienceInjector | 低 |
| **训练子系统** | WeaknessAnalyzer + AdversarialGenerator + Curriculum | 高 |
| **Token Budget 3-Zone** | Zone B 动态分配 + LRU 降级 | 低 |
| **Progressive Habit Loading** | 22 条习惯的 full→summary→name 渐进 | 低 |
| **DeAI 检测** | `detect_ai_signals` 工具在 writer 中的实际使用 | 中 |
| **多轮对话** | `agent.chat()` 路径 + context 累积 | 中 |

---

## 二、场景设计矩阵

### 场景 S1：HD-WM 假说驱动审稿

**配置**：
```bash
python v2/main.py v2/evaluation/test_papers/paper_003.pdf \
  --hdwm --max-turns 25 --persona scholar --verbose \
  --intent "重点检验核心理论假设的敏感性和稳健性"
```

**验证目标**：
- HD-WM 假说生成（LLM 主动调用 `generate_hypothesis` 或 auto-enhance 触发）
- 假说状态在 prompt 中的持续注入
- 证据收集（`add_evidence`）改变假说方向
- 假说饱和触发方向切换建议
- HD-WM readiness 作为完成门控条件
- Intent 参数对审稿策略聚焦的引导效果
- Economics skills 的自动加载（paper_003 是国际贸易理论）

**成功标志**：
- [ ] 日志出现 `HD-WM: 产生假说 H001` 至少 2 次
- [ ] 日志出现 `add_evidence` 调用（for/against）
- [ ] 日志出现 `resolve_hypothesis` 至少 1 次
- [ ] `hypothesis_status` section 内容随 turn 变化（在 system prompt 中可见）
- [ ] Agent 的 findings 中至少有 1 条引用假说编号
- [ ] 审稿聚焦在"理论假设敏感性"方向（intent 生效）

**失败定位**：
| 症状 | 排查入口 |
|------|---------|
| 假说工具不可见 | `phases.py` → PHASE_TOOL_MAP 中 deep_review 是否含 hypothesis 工具 |
| Auto-enhance 不触发 | `tool_handlers/findings.py` → `hdwm_auto_enhance()` 函数 |
| 假说 section 不注入 | `assembler.py` → `hypothesis_status` section 的 condition_fn |
| 饱和信号不触发 | `hypothesis.py` → `tick()` 是否在 loop.py 每轮被调用 |
| Skills 不加载 | `skill_registry.py` → `query()` 的 paper_type 匹配 |

---

### 场景 S2：Writer 编辑 + 验证闭环

**配置**：
```bash
python v2/main.py v2/evaluation/test_papers/paper_001.pdf \
  --persona writer --max-turns 20 --verbose
```

**验证目标**：
- Writer identity 正确加载（不同于 Scholar）
- Agent 使用 `edit_paragraph` / `reword_sentence` / `insert_content` 工具
- EDIT-5 验证闭环触发（每次编辑后 verify_edit）
- `detect_ai_signals` 工具可用并被调用
- Voice Profile 正确提取
- 编辑结果存入 `state.edits`

**成功标志**：
- [ ] 日志显示 Writer identity 被加载（tools 列表包含 edit_section 等）
- [ ] 至少 1 次成功的 `edit_paragraph` / `reword_sentence` 调用
- [ ] 日志出现 `[EDIT-PASS]` 或 `[EDIT-WARN]` 验证反馈
- [ ] `agent.get_stats()` 中 `edits_count > 0`
- [ ] Voice Profile 提取成功（不报 fallback 或 empty）

**失败定位**：
| 症状 | 排查入口 |
|------|---------|
| Writer tools 不可见 | `identity.py` → `PERSONAS["writer"]["tools"]` 定义 |
| edit_paragraph 失败 "section not found" | `tool_handlers/editing.py` → `resolve_section_key()` 模糊匹配 |
| reword_sentence 失败 "sentence not found" | LLM 给出的 sentence_match 与原文不完全一致 |
| EDIT-FAIL 循环 | `run_edit_verification()` → checker 的判断阈值 |
| Voice Profile 为空 | `voice_fingerprint.py` → `extract_voice()` 输入文本太短 |

---

### 场景 S3：完整协作链 (Scholar → Writer → Scholar)

**配置**：
```bash
python v2/main.py v2/evaluation/test_papers/paper_001.pdf \
  --mode full --max-turns 15 --verbose
```

**验证目标**：
- CollaborativeReview 三阶段串联执行
- Phase 1 Scholar 初审产出 findings
- Phase 2 Writer 基于 findings 修改论文
- Phase 3 Scholar 复审检验修改质量
- 跨阶段上下文传递（findings 从 Phase 1 到 Phase 2）
- 总体输出结构：`{"review", "revision", "re_review", "findings", "edits", "stats"}`

**成功标志**：
- [ ] `result["review"]` 非空且含实质审稿内容
- [ ] `result["revision"]` 非空且含编辑记录
- [ ] `result["re_review"]` 非空且引用了 Phase 1 的 findings
- [ ] `result["findings"]` 列表长度 ≥ 3
- [ ] `result["edits"]` 列表长度 ≥ 1
- [ ] 无 uncaught exception（httpx RuntimeError 可忽略）

**失败定位**：
| 症状 | 排查入口 |
|------|---------|
| Phase 2 不启动 | `agent.py` → `CollaborativeReview.run()` 状态机 |
| Writer 不使用 Phase 1 findings | 跨阶段 context 传递逻辑（findings 是否注入 Writer prompt） |
| 复审不引用初审发现 | Phase 3 的 system prompt 是否包含 Phase 1 findings |
| stats 中 phases 为空列表 | 已知 issue：`self.phases` 不再被填充（不影响功能） |

---

### 场景 S4：Kill Switch 降级对比

**配置 A（Full 功能）**：
```bash
SCHOLAR_GODEL_MCL=1 SCHOLAR_GODEL_PCG=1 SCHOLAR_GODEL_BUDGET_MANAGER=1 \
python v2/main.py v2/evaluation/test_papers/paper_001.pdf \
  --max-turns 15 --verbose 2>&1 | tee logs/s4_full.log
```

**配置 B（核心降级）**：
```bash
SCHOLAR_GODEL_MCL=0 SCHOLAR_GODEL_PCG=0 SCHOLAR_GODEL_BUDGET_MANAGER=0 \
SCHOLAR_GODEL_SIGNAL_DISPATCHER=0 SCHOLAR_GODEL_FAST_REFLECT=0 \
python v2/main.py v2/evaluation/test_papers/paper_001.pdf \
  --max-turns 15 --verbose 2>&1 | tee logs/s4_degraded.log
```

**验证目标**：
- 配置 B 能正常运行到结束（不崩溃）
- 配置 B 缺少 MCL/Signal/PCG 时行为优雅降级
- 对比 A vs B：Finding 数量差异、Phase 转换时机差异、Token 消耗差异

**成功标志**：
- [ ] 配置 B 正常退出（无 crash/traceback，httpx 除外）
- [ ] 配置 B 仍能产出 findings（可能少于 A）
- [ ] 配置 B 无 MCL stagnation/completion block 日志
- [ ] 配置 B 无 Signal dispatch 日志
- [ ] A 和 B 的 token 消耗有可观测差异

**失败定位**：
| 症状 | 排查入口 |
|------|---------|
| 关闭 MCL 后 crash | `meta_cognition_layer.py` → 是否有 `if not enabled: return None` 守卫 |
| 关闭 BudgetManager 后 token 无限增长 | `token_budget.py` → disabled 时是否 fallback 到 passive compaction |
| 关闭 SignalDispatcher 后 Agent 陷入死循环 | `signal_dispatcher.py` → disabled 时 boundary_guard 是否直接注入 |

---

### 场景 S5：多轮对话 + Context 累积

**配置**：通过 Python 脚本模拟（因为需要程序化发送 chat 消息）

```python
# 见 scripts/s5_multi_turn.py
agent = ScholarAgent(paper_path="paper_001.pdf", max_loop_turns=10, verbose=True)
r1 = await agent.start()
r2 = await agent.chat("你认为 DID 估计的平行趋势假设检验够充分吗？")
r3 = await agent.chat("Table 2 和 Table A.5 的数据是否一致？")
stats = agent.get_stats()
findings = agent.get_findings()
```

**验证目标**：
- `agent.chat()` 多轮调用正常（context 累积不崩溃）
- 后续 chat 能引用前面审稿产出的 findings
- Compaction 在多轮 chat 后正确触发
- `get_stats()` 和 `get_findings()` 返回累计结果

**成功标志**：
- [ ] 三轮 chat 均正常返回（无 crash）
- [ ] r2 或 r3 的回复引用了 r1 审稿中的发现
- [ ] `findings_count` 随 chat 轮次递增或不变（不丢失）
- [ ] `conversation_turns` 计数正确（= 2，start 不算）
- [ ] 若 context 超限，compaction 正常触发（日志可见）

**失败定位**：
| 症状 | 排查入口 |
|------|---------|
| chat() 抛异常 | `agent.py` → `chat()` 方法实现 |
| 前轮 findings 丢失 | `compaction.py` → `format_for_restoration()` 是否保留 findings |
| context 持续增长不压缩 | `compaction.py` → `should_compact()` 阈值 |
| Agent "忘记"前面的讨论 | compaction 后恢复文本是否含对话历史摘要 |

---

### 场景 S6：训练子系统集成验证

**配置**：通过 Python 脚本模拟（需要构造 AgentExecutor 适配器）

```python
# 见 scripts/s6_training.py
from v2.training import WeaknessAnalyzer, AdversarialGenerator, TrainingLoop, TrainingConfig
from v2.training.weakness_analyzer import WeaknessDimension

# Step 1: WeaknessAnalyzer 独立验证
analyzer = WeaknessAnalyzer()
analyzer.ingest_manual(
    dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
    description="Agent 未检出 DID 平行趋势假设问题",
    severity=0.8
)
profile = analyzer.build_profile()

# Step 2: AdversarialGenerator 验证
generator = AdversarialGenerator(client=llm_client)
case = await generator.generate_challenge(
    dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
    difficulty="medium"
)

# Step 3: CurriculumDesigner 验证
from v2.training.curriculum import CurriculumDesigner
designer = CurriculumDesigner()
curriculum = designer.design_curriculum(profile)

# Step 4 (可选): 完整 TrainingLoop（需要 AgentExecutor 适配器）
```

**验证目标**：
- WeaknessAnalyzer 能 ingest + build_profile（不依赖历史数据）
- AdversarialGenerator 能生成结构化对抗样本（需要 LLM）
- CurriculumDesigner 能从 profile 设计课程
- 各组件类型签名正确（无 TypeError）

**成功标志**：
- [ ] `profile.dimensions` 非空，含 METHODOLOGY_ANALYSIS 维度
- [ ] `case` 对象有 `paper_snippet`, `target_dimension`, `difficulty` 字段
- [ ] `curriculum.stages` 非空，stage 难度递增
- [ ] 全程无 import/type/attribute error

**失败定位**：
| 症状 | 排查入口 |
|------|---------|
| `WeaknessDimension.METHODOLOGY` 不存在 | 正确名称是 `METHODOLOGY_ANALYSIS` |
| `severity` TypeError | 必须传 float 不是 str |
| Generator 需要 LLM client | 需要构造 httpx 基础的 OpenAI compatible client |
| Curriculum 为空 | `profile.dimensions` 为空 → ingest 没成功 |

---

## 三、执行顺序与依赖关系

```
S6 (训练子系统) ─── 独立，无 LLM 依赖（除 Generator），优先执行
     ↓ 验证基础组件可用
S1 (HD-WM) ─── 需要 LLM，~25 turns，较长
     ↓ 确认高级认知功能
S2 (Writer) ─── 需要 LLM，~20 turns
     ↓ 确认编辑工具链
S3 (协作链) ─── 需要 LLM，3 阶段串联，最长
     ↓ 确认端到端协作
S4 (降级对比) ─── 需要跑 2 次，对比输出
     ↓ 确认容错性
S5 (多轮对话) ─── 需要 LLM，3 轮 chat
     ↓ 确认交互模式
```

推荐执行策略：S6 → S1 → S2 → S5 → S3 → S4

理由：S6 无 LLM 费用（除 Generator），快速验证基础。S1/S2 验证两个核心未测功能。S5 较轻量。S3 最重（三阶段）。S4 需要两次完整运行做对比。

---

## 四、日志保留规范

所有验证日志统一存放在：
```
v2/evaluation/reports/a1_deep_use/
├── s1_hdwm_paper003.log        ← 完整 stderr + stdout
├── s1_hdwm_paper003.json       ← agent.get_stats() + get_findings()
├── s2_writer_paper001.log
├── s2_writer_paper001.json
├── s3_full_collab.log
├── s3_full_collab.json
├── s4_full.log
├── s4_degraded.log
├── s4_comparison.md            ← A vs B 对比分析
├── s5_multi_turn.log
├── s5_multi_turn.json
├── s6_training_components.log
├── s6_training_components.json
└── VERIFICATION_SUMMARY.md     ← 最终汇总
```

---

## 五、验证执行脚本

统一执行脚本位于 `v2/evaluation/scripts/run_a1_verification.py`（见下一节）。

核心功能：
- 按场景顺序执行
- 自动捕获 stdout/stderr 到日志文件
- 提取关键指标（findings_count, edits_count, tool_calls 分布, phases, errors）
- 生成 VERIFICATION_SUMMARY.md

---

## 六、调试路径速查表（出错时从哪里入手）

| 你看到的问题 | 第一步排查 | 第二步排查 | 第三步排查 |
|-------------|-----------|-----------|-----------|
| Agent 启动后直接退出 | `agent.py` → `start()` 的 paper loading | `.env` API 配置是否正确 | `paper_loader.py` 的返回值 |
| Phase 不转换 | `phases.py` → `should_auto_transition()` 条件 | `boundary_guard.py` → phase 相关 nudge | 检查 `sections_read` 计数 |
| 工具调用返回 "工具不存在" | `phases.py` → `PHASE_TOOL_MAP[current_phase]` | `identity.py` → 当前 persona 的 tools 列表 | `harness.py` → `_init_tool_registry()` |
| HD-WM 假说不生成 | `hypothesis.py` → `generate()` 入口 | `tool_handlers/hypothesis.py` 是否被注册 | `enable_hdwm` 是否传到 harness |
| Edit 失败 "section not found" | `tool_handlers/editing.py` → `resolve_section_key()` | 打印 `state.paper_sections.keys()` 看实际 key | LLM 给的 section 名 vs 实际 key |
| Edit 失败 "sentence not found" | `reword_sentence` 的 `sentence_match` 参数 | 原文中该句的精确文本（含标点） | 换用 `edit_paragraph` 替代 |
| MCL 不触发 | `meta_cognition_layer.py` → `_has_blocked_once` 状态 | `SCHOLAR_GODEL_MCL` 环境变量 | `check_stagnation()` 的条件（≥3 轮无 finding） |
| Compaction 不触发 | `compaction.py` → `should_compact()` 阈值 | 当前 context 占比（看 capacity signal） | `min_messages=14` 条件 |
| Finding 全被拒 | `findings.py` → 三信号去重逻辑 | 已有 findings 列表与新 finding 的文本重叠度 | 去重阈值是否过严 |
| Token 超限 crash | `token_budget.py` → zone 分配 | `compaction.py` 的 aggressive 模式 | `context_window` 参数是否正确 |
| LLM 返回空/异常 | API 网络连通性（curl 测试） | `.env` 中 API key 有效性 | `client_stats.total_permanent_failures` |
| Signal 过多干扰 Agent | `signal_dispatcher.py` → `SIGNAL_MAX_PER_TURN=2` | `DEDUP_WINDOW=3` 是否生效 | 各 signal source 的触发频率 |
| 训练循环立即退出 | `SCHOLAR_GODEL_ADVERSARIAL_TRAINING` 环境变量 | `WeaknessProfile` 是否为空 | `AgentExecutor` 是否正确实现 |
| 多轮 chat 后 Agent 忘记上文 | `compaction.py` → `format_for_restoration()` | findings/hypothesis 是否在恢复层中 | `Layered Restoration` 的 priority 配置 |

---

## 七、预期产出物

每个场景完成后产出：
1. **PASS/FAIL 判定** — 基于成功标志 checklist
2. **关键指标快照** — turns, findings, edits, tokens, tool_calls 分布
3. **异常记录** — 任何 traceback/unexpected behavior（含复现步骤）
4. **改进建议** — 如果功能有问题，给出修复方向

最终产出 `VERIFICATION_SUMMARY.md`：
- 6 个场景的 PASS/FAIL 汇总
- 功能覆盖率统计（60+ feature points 中实际被触发的比例）
- P0 问题清单（必须修复才能继续）
- P1 问题清单（值得修复但不阻塞）
- 推荐的后续工作优先级调整

---

*End of Verification Plan*
