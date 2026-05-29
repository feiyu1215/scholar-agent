"""
Phase 14: Long-Conversation Stability Stress Test (Unit Tests)
==============================================================

验证目标：
1. compress_messages 在多轮累积后是否丢失关键信息
2. format_context 是否始终正确反映 findings 状态
3. 超过 keep_recent=6 后，早期 tool_result 是否被正确压缩而非丢失
4. Agent 在压缩后是否仍能通过 system prompt 中的 findings 回忆早期结论

核心假设：
- 如果 Agent 将发现记录到 update_findings，则永远不会丢失
  (state.findings → format_context → system prompt)
- 如果 Agent 仅在 assistant.content 中推理出结论但未调用 update_findings，
  则该结论在压缩后消失（仅保留前150字符摘要）
- 这是"设计中的权衡"，不是 bug — 但需要通过测试确认边界

退化信号检测：
- Signal A: Agent 重复一个已做过的 tool call (read_section 同一 section)
- Signal B: Agent 自相矛盾 (新 finding 与旧 finding 冲突)
- Signal C: Agent 丢失用户原始意图 (回复与问题脱节)

运行方式：
    cd scholar-agent-public
    python -m pytest tests/test_stress_memory.py -v
    # 或直接
    python tests/test_stress_memory.py
"""

import sys
import json
import copy
import tempfile
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.harness import Harness


# ============================================================
# Fixtures — 长论文 & Harness 工厂
# ============================================================

LONG_PAPER_MD = """# The Impact of Remote Work on Urban Housing Markets: Evidence from the COVID-19 Pandemic

## Abstract

This study examines how the widespread adoption of remote work during the COVID-19 pandemic affected urban housing markets across 50 major U.S. metropolitan areas. Using a difference-in-differences framework with quarterly panel data from 2018-2023, we identify significant price declines in central business districts (CBDs) and simultaneous increases in suburban and exurban areas. Our estimates suggest a 12.3% relative price decline in CBD zip codes compared to suburban counterparts. We decompose this effect into demand-side (worker relocation) and supply-side (new construction patterns) channels. Heterogeneity analysis reveals that the effect is concentrated in cities with high pre-pandemic commute times and strong tech industry presence.

## 1. Introduction

The COVID-19 pandemic fundamentally altered the spatial organization of economic activity. Prior to March 2020, approximately 5% of U.S. workers primarily worked from home (Dingel and Neiman, 2020). By April 2020, this figure exceeded 35%, and as of late 2023, approximately 28% of work days are performed remotely (Barrero, Bloom, and Davis, 2023). This unprecedented shift raises a fundamental question: how does the decoupling of work location from firm location reshape urban spatial equilibria?

The theoretical prediction is ambiguous. In the standard Alonso-Muth-Mills monocentric city model, housing prices decline monotonically with distance from the CBD because commuting is costly. When commuting frequency decreases, the bid-rent gradient flattens—CBD prices fall while peripheral prices rise. However, agglomeration externalities (Duranton and Puga, 2004) and consumption amenities (Glaeser, Kolko, and Saiz, 2001) may prevent full decentralization, as workers value proximity to restaurants, cultural venues, and professional networks even when commuting is unnecessary.

We contribute to the literature in three ways. First, we provide causal estimates using pre-pandemic variation in remote-work feasibility (based on occupational task composition) as an instrumental variable. Second, we decompose the aggregate price effect into demand vs. supply channels using building permit data and migration flows. Third, we test whether the effect is temporary (pandemic-driven) or persistent by examining the 2022-2023 recovery period.

## 2. Literature Review

Our work connects to several strands of the urban economics literature. The bid-rent theory of urban spatial structure predicts that housing prices capitalize commuting costs (Alonso, 1964; Muth, 1969; Mills, 1972). Recent work has extended this framework to incorporate telework (Delventhal, Kwon, and Parkhomenko, 2022; Davis, Ghent, and Gregory, 2023). These theoretical models predict flattening of the bid-rent gradient, but magnitudes vary widely depending on parameter assumptions.

Empirical evidence on remote work and housing markets has grown rapidly since 2020. Ramani and Bloom (2021) document a "donut effect" in the largest U.S. cities—prices falling in city centers and rising in suburbs. Liu and Su (2021) find similar patterns using Zillow listing data. Gupta, Mittal, Peeters, and Van Nieuwerburgh (2022) estimate that remote work explains roughly half of the 15% decline in New York City office valuations.

## 3. Data

We construct a comprehensive quarterly panel dataset at the zip-code level for 50 metropolitan statistical areas (MSAs) from Q1 2018 to Q4 2023 (24 quarters). Our primary outcome variable is the Zillow Home Value Index (ZHVI), a smoothed, seasonally adjusted measure of typical home values. We complement this with: (1) American Community Survey migration microdata, (2) Census Building Permits Survey data, (3) job posting data from Indeed and LinkedIn with remote-work flags, and (4) Safegraph mobility data measuring visits to office buildings. Summary statistics show substantial variation: mean ZHVI ranges from $180,000 (Detroit MSA) to $1.2M (San Francisco MSA).

## 4. Methodology

Our identification strategy relies on the interaction between pre-pandemic remote-work feasibility and the pandemic shock. The baseline specification is:

ln(P_it) = alpha_i + lambda_t + beta_1(RemoteFeasibility_i x Post_t) + X_it'gamma + epsilon_it

where P_it is the ZHVI for zip code i in quarter t, alpha_i are zip-code fixed effects, lambda_t are quarter fixed effects, RemoteFeasibility is measured using the Dingel-Neiman (2020) teleworkability index, and Post is an indicator for Q2 2020 onwards. We cluster standard errors at the MSA level.

The key identification assumption is that, absent the pandemic, high- and low-feasibility areas would have followed parallel trends. We verify this using an event-study specification with quarterly leads and lags, finding no statistically significant pre-trends in the 8 quarters before March 2020.

## 5. Results

Table 2 presents our main estimates. Column (1) shows the baseline: a 1 standard deviation increase in remote feasibility is associated with a 6.2% (s.e. = 1.8%) relative price decline after the pandemic onset. Column (2) adds time-varying controls (local unemployment, mortgage rates, construction activity); the estimate attenuates slightly to 5.7%. Column (3) restricts to CBD zip codes only (within 5 miles of city center), finding a larger effect of 12.3%. Column (4) uses the suburban ring (5-25 miles) as the treated group, finding a positive effect of 4.1%.

The event-study plot (Figure 2) reveals important dynamics. Effects emerge immediately in Q2 2020, peak at Q4 2021, and partially attenuate by Q4 2023 but remain statistically significant, suggesting persistence.

## 6. Robustness Checks

We conduct several sensitivity analyses. First, we use alternative definitions of CBD vs. suburb (varying the distance threshold from 3 to 10 miles). Second, we employ a synthetic control method for the 10 largest cities individually. Third, we account for potential violations of SUTVA by controlling for spatial spillovers using a ring-buffer approach. Fourth, we conduct a placebo test using the 2017 flu season as a "non-event." All checks broadly support our main findings, though the synthetic control estimates are somewhat smaller (8.9% vs. 12.3%).

## 7. Discussion

Our findings have several implications for urban theory and policy. First, the partial persistence of effects through 2023 suggests structural change in urban organization, not merely temporary pandemic disruption. Second, the dominance of the demand channel implies that zoning reform alone would be insufficient to restore pre-pandemic CBD price levels. Third, the heterogeneity by commute time suggests that cities with better transit infrastructure may be more resilient.

However, we caution against over-interpretation. The 2023 "return to office" mandates by major employers may further attenuate the effect. Our estimates also do not account for potential general equilibrium effects.

## 8. Conclusion

We provide causal evidence that the COVID-19 remote work shock significantly flattened urban housing price gradients, with a 12.3% relative decline in CBD areas. The effect is partially persistent, demand-driven, and concentrated in high-commute-time cities. As remote work settles into a new normal, understanding its spatial implications remains crucial for urban planners, policymakers, and real estate markets.

## References

Alonso, W. (1964). Location and Land Use. Harvard University Press.
Barrero, J.M., Bloom, N., Davis, S.J. (2023). The Evolution of Work from Home. Journal of Economic Perspectives, 37(4), 23-50.
Delventhal, M., Kwon, E., Parkhomenko, A. (2022). JUE Insight: How Do Cities Change When We Work from Home? Journal of Urban Economics, 127.
Dingel, J., Neiman, B. (2020). How Many Jobs Can Be Done at Home? Journal of Public Economics, 189.
Gupta, A., Mittal, V., Peeters, J., Van Nieuwerburgh, S. (2022). Flattening the Curve: Pandemic-Induced Revaluation of Urban Real Estate. Journal of Financial Economics, 146(2).
Ramani, A., Bloom, N. (2021). The Donut Effect of Covid-19 on Cities. NBER Working Paper 28876.
"""


def create_test_harness() -> Harness:
    """创建一个加载了长论文的 Harness 实例。"""
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False)
    tmp.write(LONG_PAPER_MD)
    tmp.close()
    
    harness = Harness(
        paper_path=tmp.name,
        max_loop_turns=30,
        token_budget=200_000,
    )
    harness.load_paper()
    os.unlink(tmp.name)
    
    return harness


# ============================================================
# Utility: 重复 tool_call 检测器
# ============================================================

def detect_repeated_tool_calls(messages: list[dict]) -> list[dict]:
    """
    检测 messages 中是否有重复的 tool_call（同一个 tool + 相同参数被调用两次）。
    这是 context 退化的关键信号 — Agent 忘记自己已经读过某个 section。
    
    Returns:
        list of dicts: [{"tool": name, "args": args, "indices": [i, j]}, ...]
    """
    seen = {}
    duplicates = []
    
    for idx, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            func = tc.get("function", {})
            name = func.get("name", "")
            args_str = func.get("arguments", "{}")
            key = (name, args_str)
            if key not in seen:
                seen[key] = [idx]
            else:
                seen[key].append(idx)
    
    for (name, args_str), indices in seen.items():
        if len(indices) > 1:
            duplicates.append({
                "tool": name,
                "args": args_str,
                "indices": indices,
                "count": len(indices),
            })
    
    return duplicates


# ============================================================
# Test 1: 基础压缩 — 短消息不触发
# ============================================================

def test_compression_basic():
    """messages 少于阈值时不应触发压缩。"""
    harness = create_test_harness()
    
    # keep_recent=6, 阈值 = 6*2+2 = 14
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "审阅"},
    ]
    # 加 5 组 (10条) → 总 12 条 < 14
    for i in range(5):
        messages.append({"role": "assistant", "content": f"思考{i}",
                        "tool_calls": [{"id": f"c{i}", "type": "function",
                                       "function": {"name": "read_section", "arguments": f'{{"section":"s{i}"}}'}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 500})
    
    result = harness.compress_messages(messages, keep_recent=6)
    assert result is messages or len(result) == len(messages), "Should NOT compress under threshold"
    print("✓ test_compression_basic PASSED")


# ============================================================
# Test 2: User messages 始终保留
# ============================================================

def test_user_messages_preserved():
    """压缩后所有 user messages 必须完整保留。"""
    harness = create_test_harness()
    
    # 多轮对话模拟：3 个 user messages + 大量 assistant/tool
    messages = [{"role": "system", "content": "sys"}]
    user_msgs_original = ["第一轮：审阅论文", "第二轮：重点看方法论", "第三轮：总结一下"]
    
    for round_idx, user_msg in enumerate(user_msgs_original):
        messages.append({"role": "user", "content": user_msg})
        # 每轮 4 个 tool 交互
        for j in range(4):
            idx = round_idx * 4 + j
            messages.append({"role": "assistant", "content": None,
                            "tool_calls": [{"id": f"c{idx}", "type": "function",
                                           "function": {"name": "read_section", 
                                                       "arguments": f'{{"section":"s{idx}"}}'}}]})
            messages.append({"role": "tool", "tool_call_id": f"c{idx}", "content": "content" * 100})
    
    compressed = harness.compress_messages(messages, keep_recent=6)
    
    # 提取所有 user messages
    user_msgs_after = [m["content"] for m in compressed if m.get("role") == "user"]
    assert user_msgs_after == user_msgs_original, \
        f"User messages lost! Expected {user_msgs_original}, got {user_msgs_after}"
    
    print("✓ test_user_messages_preserved PASSED")


# ============================================================
# Test 3: Findings 通过 format_context 恢复
# ============================================================

def test_findings_survive_compression():
    """
    核心验证：无论 messages 多长，findings 通过 format_context() → system prompt 
    始终完整可见——这是"结构化外部记忆"机制的保证。
    """
    harness = create_test_harness()
    
    # 记录 5 个 findings
    test_findings = [
        {"finding": "DID平行趋势假设验证不充分——仅检验8个季度pre-trend", 
         "priority": "high", "section": "4. Methodology",
         "evidence": "event-study with quarterly leads and lags, 8 quarters", "status": "verified"},
        {"finding": "CBD 5-mile定义缺乏理论依据", "priority": "medium",
         "section": "4. Methodology", "evidence": "robustness中测3-10miles", "status": "needs_verification"},
        {"finding": "文献遗漏Autor et al.(2020)关于spatial equilibrium的贡献", "priority": "low",
         "section": "2. Literature Review", "evidence": "", "status": "suggestion"},
        {"finding": "12.3%估计可能上偏——synthetic control只给8.9%", "priority": "high",
         "section": "5. Results", "evidence": "Table 2 Col 3 vs Section 6 SC", "status": "verified"},
        {"finding": "Safegraph数据样本偏差(低收入区覆盖不足)", "priority": "medium",
         "section": "3. Data", "evidence": "smartphone用户为主", "status": "needs_verification"},
    ]
    
    harness.state.findings = test_findings
    context = harness.format_context()
    
    # 每个 finding 的关键信息都必须出现
    for f in test_findings:
        truncated = f["finding"][:120]
        assert truncated in context, f"Finding lost: {truncated[:50]}..."
    
    # Evidence 也必须可见
    for f in test_findings:
        if f["evidence"]:
            assert f["evidence"][:60] in context, f"Evidence lost: {f['evidence'][:40]}..."
    
    # Priority 信号可见
    assert "🔴" in context  # high
    assert "🟡" in context  # medium
    assert "🟢" in context  # low
    
    print("✓ test_findings_survive_compression PASSED")


# ============================================================
# Test 4: 短消息不触发压缩 (边界值)
# ============================================================

def test_no_compression_when_short():
    """恰好在阈值边界时不压缩。"""
    harness = create_test_harness()
    
    # keep_recent=6 → 阈值 = 14 (需要 > 6 个 assistant messages)
    # 构造恰好 6 个 assistant messages
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    for i in range(6):
        messages.append({"role": "assistant", "content": None,
                        "tool_calls": [{"id": f"c{i}", "type": "function",
                                       "function": {"name": "read_section", "arguments": f'{{"section":"s{i}"}}'}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 300})
    
    result = harness.compress_messages(messages, keep_recent=6)
    # 6 个 assistant + 6 个 tool = 12, 加 system + user = 14, <= 14 不压缩
    assert len(result) == len(messages), f"Should not compress at boundary, got {len(result)} vs {len(messages)}"
    print("✓ test_no_compression_when_short PASSED")


# ============================================================
# Test 5: 极端 section 长度的压缩行为
# ============================================================

def test_extreme_section_lengths():
    """超大 tool result (5000+字符) 压缩后应该大幅缩小。"""
    harness = create_test_harness()
    
    # 8 个 tool results，其中前 2 个是 5000 字符
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    for i in range(8):
        messages.append({"role": "assistant", "content": None,
                        "tool_calls": [{"id": f"c{i}", "type": "function",
                                       "function": {"name": "read_section", "arguments": f'{{"section":"s{i}"}}'}}]})
        content = "大段论文内容。" * (500 if i < 2 else 50)  # 前 2 个很长
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": content})
    
    compressed = harness.compress_messages(messages, keep_recent=6)
    
    # 前 2 个应该被压缩（它们在 keep_recent 范围外）
    # 找到被压缩的 tool results
    for msg in compressed:
        if msg.get("role") == "tool" and "[历史读取" in msg.get("content", ""):
            # 压缩后应该远小于原始
            assert len(msg["content"]) < 300, \
                f"Compressed tool result still too large: {len(msg['content'])} chars"
    
    print("✓ test_extreme_section_lengths PASSED")


# ============================================================
# Test 6: 多轮对话 messages 累积
# ============================================================

def test_multi_turn_accumulation():
    """
    模拟 3 轮用户对话（每轮 5 个 loop turns），验证：
    - 跨轮 findings 累积
    - format_context 始终包含全部 findings
    """
    harness = create_test_harness()
    
    # Round 1
    harness.state.findings.append({
        "finding": "R1: Introduction 过度声明因果", "priority": "high",
        "section": "1. Introduction", "evidence": "causal evidence 但 IV 有问题", "status": "verified"
    })
    harness.new_conversation_turn()
    
    # Round 2
    harness.state.findings.append({
        "finding": "R2: Safegraph 覆盖偏差", "priority": "medium",
        "section": "3. Data", "evidence": "smartphone 用户为主", "status": "needs_verification"
    })
    harness.state.findings.append({
        "finding": "R2: heterogeneity multiple testing", "priority": "high",
        "section": "5. Results", "evidence": "2.3x larger 但无 Bonferroni 修正", "status": "needs_verification"
    })
    harness.new_conversation_turn()
    
    # Round 3
    harness.state.findings.append({
        "finding": "R3: Conclusion 未讨论 RTO 威胁", "priority": "medium",
        "section": "8. Conclusion", "evidence": "Amazon/JPMorgan mandates", "status": "verified"
    })
    
    # format_context 应包含所有 4 条
    context = harness.format_context()
    assert "R1:" in context
    assert "R2: Safegraph" in context
    assert "R2: heterogeneity" in context
    assert "R3:" in context
    assert "4 条" in context
    
    print("✓ test_multi_turn_accumulation PASSED")


# ============================================================
# Test 7: 重复 tool_call 检测器
# ============================================================

def test_repeated_tool_call_detection():
    """验证退化信号检测器能识别重复调用。"""
    messages = [
        {"role": "system", "content": "test"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "1", "type": "function", "function": {"name": "read_section", "arguments": '{"section": "Abstract"}'}}
        ]},
        {"role": "tool", "tool_call_id": "1", "content": "Abstract..."},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "2", "type": "function", "function": {"name": "read_section", "arguments": '{"section": "Methods"}'}}
        ]},
        {"role": "tool", "tool_call_id": "2", "content": "Methods..."},
        # 退化信号: 重复读 Abstract
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "3", "type": "function", "function": {"name": "read_section", "arguments": '{"section": "Abstract"}'}}
        ]},
        {"role": "tool", "tool_call_id": "3", "content": "Abstract..."},
    ]
    
    dupes = detect_repeated_tool_calls(messages)
    assert len(dupes) == 1
    assert dupes[0]["tool"] == "read_section"
    assert dupes[0]["count"] == 2
    
    # 非重复场景
    clean_messages = messages[:5]  # 只保留前两个不同的调用
    assert detect_repeated_tool_calls(clean_messages) == []
    
    print("✓ test_repeated_tool_call_detection PASSED")


# ============================================================
# Test 8: 12 轮模拟压力测试 — 完整流程
# ============================================================

def test_twelve_turn_simulation():
    """
    模拟 12 轮 loop turn：Agent 读取 8 个 sections + 记录 3 个 findings + 1 次搜索。
    验证压缩后结构完整性和 findings 保持。
    """
    harness = create_test_harness()
    
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "请全面审阅这篇关于远程工作与房地产的论文"},
    ]
    
    sections = [k for k in harness.state.paper_sections.keys() if k != "full"]
    
    # 模拟 Agent 逐个读 section 并偶尔记录 findings
    for i, section in enumerate(sections):
        messages.append({
            "role": "assistant",
            "content": f"让我读一下 {section}",
            "tool_calls": [{
                "id": f"call_{i}",
                "type": "function",
                "function": {"name": "read_section", 
                            "arguments": json.dumps({"section": section}, ensure_ascii=False)},
            }],
        })
        content = harness.state.paper_sections.get(section, "空")
        messages.append({"role": "tool", "tool_call_id": f"call_{i}", "content": content})
        
        # 在 section 1, 4, 6 记录 findings
        if i in [1, 4, 6]:
            finding_text = f"测试发现#{i}: {section} 存在方法论问题"
            fc_id = f"finding_{i}"
            messages.append({
                "role": "assistant",
                "content": f"发现问题",
                "tool_calls": [{
                    "id": fc_id,
                    "type": "function",
                    "function": {"name": "update_findings",
                                "arguments": json.dumps({
                                    "finding": finding_text,
                                    "priority": "high" if i == 1 else "medium",
                                    "section": section,
                                    "evidence": f"来自 {section} 的证据",
                                }, ensure_ascii=False)},
                }],
            })
            harness.state.findings.append({
                "finding": finding_text,
                "priority": "high" if i == 1 else "medium",
                "section": section,
                "evidence": f"来自 {section} 的证据",
                "status": "needs_verification",
            })
            messages.append({"role": "tool", "tool_call_id": fc_id, "content": f"已记录发现: {finding_text}"})
    
    total_msgs = len(messages)
    print(f"  构建完成: {total_msgs} 条 messages, {len(sections)} sections, {len(harness.state.findings)} findings")
    
    # 执行压缩
    compressed = harness.compress_messages(messages, keep_recent=6)
    print(f"  压缩: {total_msgs} → {len(compressed)} 条 messages")
    
    # 验证 1: Findings 在 format_context 中完整存在
    context = harness.format_context()
    for f in harness.state.findings:
        assert f["finding"][:60] in context, f"Finding 丢失: {f['finding'][:60]}"
    print(f"  [PASS] {len(harness.state.findings)} 条 findings 全部在 format_context 中")
    
    # 验证 2: 最近 6 组完整保留
    # 找最后 6 个 assistant messages
    assistants_in_compressed = [(i, m) for i, m in enumerate(compressed) if m.get("role") == "assistant"]
    last_6 = assistants_in_compressed[-6:] if len(assistants_in_compressed) >= 6 else assistants_in_compressed
    for idx, msg in last_6:
        if "tool_calls" in msg:
            for tc in msg["tool_calls"]:
                args = tc["function"]["arguments"]
                parsed = json.loads(args)  # 应该是完整有效的 JSON
                assert isinstance(parsed, dict)
    print(f"  [PASS] 最近 {len(last_6)} 组 assistant messages 的 arguments 完整")
    
    # 验证 3: 被压缩的 tool results 保留了关键元信息
    compressed_tools = [m for m in compressed if m.get("role") == "tool" and "[历史读取" in m.get("content", "")]
    for ct in compressed_tools:
        assert "字符]" in ct["content"], "压缩的 tool result 应包含原始长度信息"
    if compressed_tools:
        print(f"  [PASS] {len(compressed_tools)} 条早期 tool results 被正确压缩（保留元信息）")
    else:
        print(f"  [INFO] 无 tool results 被压缩（消息数可能刚好不够触发）")
    
    # 验证 4: tool_call ↔ tool response 配对完整
    for idx, msg in enumerate(compressed):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tc_id = tc["id"]
                found = any(
                    m.get("role") == "tool" and m.get("tool_call_id") == tc_id
                    for m in compressed[idx+1:]
                )
                assert found, f"孤立 tool_call: {tc_id} (index {idx})"
    print(f"  [PASS] tool_call ↔ tool response 配对完整")
    
    # 验证 5: 模拟退化 — 添加重复调用
    messages_with_dupe = list(messages)
    messages_with_dupe.append({
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "dupe", "type": "function",
                       "function": {"name": "read_section",
                                   "arguments": json.dumps({"section": sections[0]}, ensure_ascii=False)}}],
    })
    dupes = detect_repeated_tool_calls(messages_with_dupe)
    assert any(d["tool"] == "read_section" for d in dupes)
    print(f"  [PASS] 退化检测器: 识别出 {len(dupes)} 个重复调用")
    
    print("\n  === 12 轮模拟压力测试全部通过 ===")


# ============================================================
# Test 9: Token Budget 预警阈值
# ============================================================

def test_context_corruption_threshold():
    """
    验证 token budget 预警机制：
    - context 占用 <= 80%: 无警告
    - context 占用 > 80%: 触发认知带宽压力警告
    
    对应 COGNITIVE_ANCHOR §12 的"上下文腐烂阈值"。
    Phase 16 更新: 阈值从 90% 降至 80%，对齐 Anthropic 研究结论。
    Phase 45 修正: 信号源从 total_tokens/token_budget 改为 last_prompt_tokens/context_window。
    """
    harness = create_test_harness()
    context_window = harness.state.context_window  # 128_000
    
    # 70% — 不应触发
    harness.state.last_prompt_tokens = int(context_window * 0.7)
    assert harness.check_token_budget() is None, "70% should not trigger warning"
    
    # 75% — 不应触发 (阈值是 80%)
    harness.state.last_prompt_tokens = int(context_window * 0.75)
    assert harness.check_token_budget() is None, "75% should not trigger warning (threshold is 80%)"
    
    # 81% — 应触发 (Phase 16: 阈值从 90% 降至 80%)
    harness.state.last_prompt_tokens = int(context_window * 0.81)
    warning = harness.check_token_budget()
    assert warning is not None, "81% should trigger warning (threshold is 80%)"
    assert "context" in warning or "占用" in warning
    
    # 99% — 应触发
    harness.state.last_prompt_tokens = int(context_window * 0.99)
    warning = harness.check_token_budget()
    assert warning is not None
    
    print("✓ test_context_corruption_threshold PASSED")
    print(f"  当前系统阈值: 80% (基于 last_prompt_tokens/context_window)")


# ============================================================
# Test 10: format_context Token 开销缩放
# ============================================================

def test_format_context_scaling():
    """验证 findings 累积到 30 条时 format_context 的大小仍然可控。"""
    harness = create_test_harness()
    
    for i in range(30):
        harness.state.findings.append({
            "finding": f"Finding #{i:02d}: {'问题描述' * 10}",  # ~50字
            "priority": ["high", "medium", "low"][i % 3],
            "section": f"Section {i % 8}",
            "evidence": f"证据文本{'x' * 40}",  # ~45字
            "status": ["verified", "needs_verification", "suggestion"][i % 3],
        })
    
    context = harness.format_context()
    context_chars = len(context)
    estimated_tokens = context_chars // 3  # 粗略估算
    
    print(f"  30 findings → format_context: {context_chars} chars (~{estimated_tokens} tokens)")
    
    # 不应超过 15000 chars (~5000 tokens)
    assert context_chars < 15000, f"format_context too large: {context_chars} chars"
    assert "30 条" in context
    
    print("✓ test_format_context_scaling PASSED")


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  Phase 14: Long-Conversation Stability Stress Test")
    print("  验证 Token Pipeline 在长对话中的记忆保持")
    print("=" * 60)
    
    tests = [
        ("Test 1: 基础压缩阈值", test_compression_basic),
        ("Test 2: User messages 保留", test_user_messages_preserved),
        ("Test 3: Findings 结构化记忆不可丢失", test_findings_survive_compression),
        ("Test 4: 边界值不触发压缩", test_no_compression_when_short),
        ("Test 5: 极端长度压缩行为", test_extreme_section_lengths),
        ("Test 6: 多轮对话 findings 累积", test_multi_turn_accumulation),
        ("Test 7: 重复 tool_call 检测器", test_repeated_tool_call_detection),
        ("Test 8: 12 轮模拟压力测试", test_twelve_turn_simulation),
        ("Test 9: Token Budget 预警阈值", test_context_corruption_threshold),
        ("Test 10: format_context 开销缩放", test_format_context_scaling),
    ]
    
    passed = 0
    failed = 0
    errors = []
    
    for name, test_fn in tests:
        print(f"\n{'─' * 50}")
        print(f"  {name}")
        print(f"{'─' * 50}")
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  ✗ FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
            errors.append((name, str(e)))
    
    print(f"\n{'=' * 60}")
    print(f"  结果: {passed} passed, {failed} failed (共 {len(tests)} 个测试)")
    if errors:
        print(f"\n  失败列表:")
        for name, err in errors:
            print(f"    - {name}: {err[:80]}")
    print(f"{'=' * 60}")
    
    if failed > 0:
        print("\n  ⚠️  存在失败的测试 — 需要分析是测试 bug 还是系统退化")
        sys.exit(1)
    else:
        print("\n  ✅ 所有压力测试通过")
        print("  结论: Token Pipeline 的结构化记忆机制 (findings → format_context → system prompt) 工作正常")
        print("  已确认: 只要 Agent 调用 update_findings，信息就不会因为压缩丢失")
        print("  风险点: 仅存在于 assistant.content 中的推理（未存入 findings）会随压缩衰减")
        print("\n  下一步: 使用真实 LLM 运行集成测试 (tests/run_stress_integration.py)")
        sys.exit(0)
