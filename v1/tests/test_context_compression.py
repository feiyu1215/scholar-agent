"""
core/test_context_compression.py — Context Window 压缩机制验证

验证目标:
1. compress_messages 对短对话不做任何改动（幂等）
2. compress_messages 对长对话能显著压缩 token（目标 >50%）
3. 压缩后保留了 system/user 完整内容
4. 压缩后最近 K 轮的 tool_result 完整
5. 压缩后早期的 tool_result 被缩短
6. 与真实 E2E 对比: 模拟 24 轮 loop 的 messages, 计算压缩比
"""

import json
import sys
sys.path.insert(0, "/Users/yanfeiyu03/Downloads/scholar-agent-public")

from core.harness import Harness


def build_mock_messages(n_turns: int) -> list[dict]:
    """构造模拟 N 轮 loop 的 messages 列表。
    
    模拟真实场景:
    - system (长 prompt ~2000 chars)
    - user (短 ~50 chars)
    - 每轮: assistant(tool_call) + tool(result)
    - tool_result 内容: section 文本 (~2000-5000 chars) 或 短确认 (<100 chars)
    """
    messages = []
    
    # System prompt
    messages.append({
        "role": "system",
        "content": "你是一个经验丰富的学术审稿人..." + "x" * 2000,
    })
    
    # User message
    messages.append({
        "role": "user",
        "content": "请审阅这篇论文。",
    })
    
    for turn in range(n_turns):
        tool_call_id = f"call_{turn:03d}"
        
        if turn % 3 == 0:
            # read_section 调用 → 长 tool_result
            name = "read_section"
            args = {"section": f"section_{turn}"}
            result_content = f"## Section {turn}\n" + "这是论文的第{}部分内容，包含大量的学术文本...".format(turn) * 100
        elif turn % 3 == 1:
            # update_findings 调用 → 短确认
            name = "update_findings"
            args = {"finding": f"发现#{turn}: 数据不一致问题...", "priority": "high", "status": "verified", "evidence": "原文:" + "x"*200}
            result_content = f"已记录发现 (当前共 {turn} 条)"
        else:
            # search_literature → 中等长度结果
            name = "search_literature"
            args = {"query": "some search query", "reason": "验证某个claim"}
            result_content = f"搜索 'some search query' 的结果 (来源: CrossRef, 共 5 条):\n" + "  [1] Paper title...\n" * 20 + "摘要内容" * 50
        
        # assistant message with tool_call
        messages.append({
            "role": "assistant",
            "content": f"让我读取 section {turn} 来验证..." if turn % 3 == 0 else None,
            "tool_calls": [{
                "id": tool_call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                }
            }]
        })
        
        # tool result
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result_content,
        })
    
    return messages


def char_count(messages: list[dict]) -> int:
    """计算 messages 列表的总字符数（近似 token 代理）。"""
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        total += len(content)
        if "tool_calls" in msg:
            for tc in msg["tool_calls"]:
                total += len(tc.get("function", {}).get("arguments", ""))
    return total


def test_short_messages_unchanged():
    """短对话不应被改动。"""
    harness = Harness()
    messages = build_mock_messages(4)
    compressed = harness.compress_messages(messages)
    assert compressed == messages, "短对话应原封不动"
    print("✅ test_short_messages_unchanged")


def test_long_messages_compressed():
    """长对话应被显著压缩。"""
    harness = Harness()
    messages = build_mock_messages(24)
    compressed = harness.compress_messages(messages)
    
    orig_chars = char_count(messages)
    comp_chars = char_count(compressed)
    ratio = comp_chars / orig_chars
    
    print(f"   Original: {orig_chars:,} chars ({len(messages)} msgs)")
    print(f"   Compressed: {comp_chars:,} chars ({len(compressed)} msgs)")
    print(f"   Ratio: {ratio:.2%} (saved {100-ratio*100:.1f}%)")
    
    assert ratio < 0.7, f"压缩比应该 <70%, 实际 {ratio:.2%}"
    print("✅ test_long_messages_compressed")


def test_system_and_user_preserved():
    """system 和 user messages 必须完整保留。"""
    harness = Harness()
    messages = build_mock_messages(20)
    compressed = harness.compress_messages(messages)
    
    orig_system = [m for m in messages if m["role"] == "system"]
    comp_system = [m for m in compressed if m["role"] == "system"]
    assert orig_system == comp_system, "system messages 应完整保留"
    
    orig_user = [m for m in messages if m["role"] == "user"]
    comp_user = [m for m in compressed if m["role"] == "user"]
    assert orig_user == comp_user, "user messages 应完整保留"
    
    print("✅ test_system_and_user_preserved")


def test_recent_turns_complete():
    """最近 6 轮的 tool_result 应完整。"""
    harness = Harness()
    messages = build_mock_messages(20)
    compressed = harness.compress_messages(messages, keep_recent=6)
    
    # 找到最后 6 个 tool messages（它们应该完整）
    orig_tools = [m for m in messages if m["role"] == "tool"]
    comp_tools = [m for m in compressed if m["role"] == "tool"]
    
    # 最后 6 个 tool result 应该完整
    for i in range(-6, 0):
        orig_content = orig_tools[i]["content"]
        comp_content = comp_tools[i]["content"]
        assert orig_content == comp_content, f"最近第 {-i} 条 tool result 应完整保留，但被改动了"
    
    print("✅ test_recent_turns_complete")


def test_old_section_reads_compressed():
    """早期的 read_section 结果应被压缩。"""
    harness = Harness()
    messages = build_mock_messages(20)
    compressed = harness.compress_messages(messages, keep_recent=6)
    
    # 第一个 tool result（来自 turn 0 的 read_section）应被压缩
    first_tool_orig = next(m for m in messages if m["role"] == "tool")
    first_tool_comp = next(m for m in compressed if m["role"] == "tool")
    
    # 原文应该很长（>1000 chars）
    assert len(first_tool_orig["content"]) > 1000, "原文应该很长"
    # 压缩后应该很短（<300 chars）
    assert len(first_tool_comp["content"]) < 300, f"压缩后应很短，实际 {len(first_tool_comp['content'])} chars"
    
    print("✅ test_old_section_reads_compressed")


def test_realistic_scenario():
    """模拟 Phase 7 的真实场景: 24 轮 loop, 预期从 ~278k → <120k。"""
    harness = Harness()
    
    # 更真实的 messages: 混合长短内容
    messages = [
        {"role": "system", "content": "x" * 3000},  # system prompt ~3k
        {"role": "user", "content": "请审阅这篇论文"},
    ]
    
    # 模拟 24 轮: 交替读 section (~4000 chars each) 和短操作
    for i in range(24):
        tc_id = f"call_{i:03d}"
        if i < 6:
            # 前 6 轮: 读取核心 sections (每个 ~4000 chars)
            messages.append({
                "role": "assistant", "content": None,
                "tool_calls": [{"id": tc_id, "type": "function",
                    "function": {"name": "read_section", "arguments": json.dumps({"section": f"sec{i}"})}}]
            })
            messages.append({
                "role": "tool", "tool_call_id": tc_id,
                "content": "x" * 4000  # 模拟 4000 字符 section
            })
        elif i < 12:
            # 中间 6 轮: update_findings (短)
            messages.append({
                "role": "assistant", "content": "分析结果...",
                "tool_calls": [{"id": tc_id, "type": "function",
                    "function": {"name": "update_findings", "arguments": json.dumps({"finding": "xxx", "priority": "high", "status": "verified"})}}]
            })
            messages.append({
                "role": "tool", "tool_call_id": tc_id,
                "content": "已记录发现 (当前共 X 条)"
            })
        elif i < 18:
            # 后面 6 轮: 更多 read_section
            messages.append({
                "role": "assistant", "content": None,
                "tool_calls": [{"id": tc_id, "type": "function",
                    "function": {"name": "read_section", "arguments": json.dumps({"section": f"sec{i}"})}}]
            })
            messages.append({
                "role": "tool", "tool_call_id": tc_id,
                "content": "y" * 5000  # 较长 section
            })
        else:
            # 最后 6 轮: review_findings + talk_to_user
            messages.append({
                "role": "assistant", "content": "让我回顾一下...",
                "tool_calls": [{"id": tc_id, "type": "function",
                    "function": {"name": "review_findings", "arguments": json.dumps({"filter": "all"})}}]
            })
            messages.append({
                "role": "tool", "tool_call_id": tc_id,
                "content": "发现回顾 ..." + "z" * 2000
            })
    
    compressed = harness.compress_messages(messages, keep_recent=6)
    
    orig_chars = char_count(messages)
    comp_chars = char_count(compressed)
    ratio = comp_chars / orig_chars
    
    print(f"   真实场景模拟:")
    print(f"   Original: {orig_chars:,} chars (≈{orig_chars//4:,} tokens)")
    print(f"   Compressed: {comp_chars:,} chars (≈{comp_chars//4:,} tokens)")
    print(f"   节省: {100-ratio*100:.1f}%")
    
    # 预期节省 >40%
    assert ratio < 0.6, f"真实场景应节省 >40%, 实际 {100-ratio*100:.1f}%"
    print("✅ test_realistic_scenario")


if __name__ == "__main__":
    print("=" * 60)
    print("Context Window 压缩机制验证")
    print("=" * 60)
    print()
    
    test_short_messages_unchanged()
    test_long_messages_compressed()
    test_system_and_user_preserved()
    test_recent_turns_complete()
    test_old_section_reads_compressed()
    test_realistic_scenario()
    
    print()
    print("=" * 60)
    print("ALL TESTS PASSED ✅")
    print("=" * 60)
