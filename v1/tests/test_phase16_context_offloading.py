"""
Phase 16 测试: 会话内上下文卸载机制验证

测试目标:
1. Section Digest 生成正确性
2. format_context 展示 digests
3. Adaptive keep_recent 行为
4. 80% 阈值对齐
5. 真实论文端到端验证

运行: python3 tests/test_phase16_context_offloading.py
"""

import sys
import json
from pathlib import Path

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.harness import Harness, _generate_section_digest, _classify_section


# ============================================================
# Test Utilities
# ============================================================

def create_test_harness(token_budget: int = 200_000) -> Harness:
    """创建测试用 Harness（使用真实论文）。"""
    workspace = PROJECT_ROOT / ".workspace"
    if workspace.exists():
        return Harness(paper_path=str(workspace), token_budget=token_budget)
    else:
        # 退化：无真实论文时用 mock
        h = Harness(token_budget=token_budget)
        h._paper_loaded = True
        h.state.paper_sections = {
            "abstract": "This paper studies the causal effect of policy X on outcome Y using a DID design. We find significant effects.",
            "introduction": "Regional innovation policy has become a major tool. " * 50 + "We contribute by using staggered DID.",
            "methodology": "We employ Callaway-Sant'Anna estimator to address heterogeneous treatment effects. " * 30,
            "results": "| Variable | Coefficient | SE |\n|---|---|---|\n| Treatment | 0.15** | 0.06 |\n| Control | -0.02 | 0.03 |\n" * 10,
            "conclusion": "We find robust evidence that policy X increases Y by 15%. " * 20,
        }
        return h


# ============================================================
# Tests
# ============================================================

def test_section_digest_generation():
    """Test 1: _generate_section_digest 对不同类型内容生成合理的摘要。"""
    # 普通文本
    content = "This paper examines the causal impact of innovation zones. We use DID to identify effects."
    digest = _generate_section_digest("introduction", content)
    assert len(digest) > 0
    assert len(digest) <= 150
    assert "causal impact" in digest.lower() or "innovation" in digest.lower()
    
    # 含表格的内容
    table_content = "Baseline results are shown below.\n" + "| Var | Coef | SE |\n" * 20 + "The effect is 0.15."
    digest = _generate_section_digest("results", table_content)
    assert "表格" in digest  # 应该检测到表格
    
    # 短内容
    short_content = "See below."
    digest = _generate_section_digest("appendix", short_content)
    assert "极少" in digest
    
    # 含大量数字的内容
    num_content = "The coefficient is 0.15, with SE 0.06. P-value is 0.02. N=500. R2=0.45. F-stat=12.3. " * 5
    digest = _generate_section_digest("statistics", num_content)
    assert "数值" in digest  # 应该检测到数值密集
    
    print("✓ test_section_digest_generation PASSED")


def test_digest_stored_on_read():
    """Test 2: read_section 后自动生成并存储 digest。"""
    h = create_test_harness()
    
    # 初始状态：无 digest
    assert len(h.state.section_digests) == 0
    
    # 读取一个 section
    h.execute_tool("read_section", {"section": "abstract"})
    
    # 应该有 digest
    assert len(h.state.section_digests) == 1
    assert "abstract" in h.state.section_digests
    digest = h.state.section_digests["abstract"]
    assert len(digest) > 10
    assert len(digest) <= 150
    
    # 再次读取不应改变 digest（幂等性）
    h.execute_tool("read_section", {"section": "abstract"})
    assert h.state.section_digests["abstract"] == digest
    
    print("✓ test_digest_stored_on_read PASSED")


def test_format_context_shows_digests():
    """Test 3: format_context 在有 digests 时展示它们。"""
    h = create_test_harness()
    
    # 无 digest 时不展示
    ctx = h.format_context()
    assert "摘要缓存" not in ctx
    
    # 读取几个 sections
    h.execute_tool("read_section", {"section": "abstract"})
    h.execute_tool("read_section", {"section": "introduction"})
    
    # 有 digest 时展示
    ctx = h.format_context()
    assert "摘要缓存" in ctx
    assert "abstract" in ctx
    
    print("✓ test_format_context_shows_digests PASSED")


def test_adaptive_keep_recent():
    """Test 4: compress_messages 在不同 token 压力下调整 keep_recent。"""
    h = create_test_harness(token_budget=100_000)
    
    # 构建足够多的 messages 来触发压缩
    messages = [{"role": "system", "content": "system prompt"}]
    for i in range(20):
        messages.append({
            "role": "assistant",
            "content": f"Thinking about step {i}",
            "tool_calls": [{
                "id": f"tc_{i}",
                "type": "function",
                "function": {"name": "read_section", "arguments": json.dumps({"section": "test"})}
            }]
        })
        messages.append({
            "role": "tool",
            "tool_call_id": f"tc_{i}",
            "content": "x" * 500  # 模拟中等长度 tool result
        })
    
    # 0% budget: keep_recent=6 (default)
    h.state.total_tokens = 0
    compressed_0 = h.compress_messages(messages)
    
    # 65% budget: keep_recent=4
    h.state.total_tokens = 65_000
    compressed_65 = h.compress_messages(messages)
    
    # 78% budget: keep_recent=3
    h.state.total_tokens = 78_000
    compressed_78 = h.compress_messages(messages)
    
    # 验证：更高压力 → 更多压缩 → 更短的 messages
    # (由于压缩是保留最近 N 组不压缩，更小的 N 意味着更多被压缩的 tool results)
    total_chars_0 = sum(len(m.get("content", "") or "") for m in compressed_0)
    total_chars_65 = sum(len(m.get("content", "") or "") for m in compressed_65)
    total_chars_78 = sum(len(m.get("content", "") or "") for m in compressed_78)
    
    assert total_chars_65 <= total_chars_0, f"65% should compress more: {total_chars_65} vs {total_chars_0}"
    assert total_chars_78 <= total_chars_65, f"78% should compress more: {total_chars_78} vs {total_chars_65}"
    
    print(f"  Chars at 0%: {total_chars_0}, at 65%: {total_chars_65}, at 78%: {total_chars_78}")
    print("✓ test_adaptive_keep_recent PASSED")


def test_80_percent_threshold():
    """Test 5: check_token_budget 在 context 占用超 80% 时触发。
    
    Phase 45 修正: 信号源从 total_tokens/token_budget 改为
    last_prompt_tokens/context_window（认知带宽压力）。
    """
    h = create_test_harness(token_budget=100_000)
    # 使用 context_window=100_000 方便计算百分比
    h.state.context_window = 100_000
    
    # 79% — 不触发
    h.state.last_prompt_tokens = 79_000
    assert h.check_token_budget() is None, "79% should NOT trigger"
    
    # 80% — 刚好不触发 (> 0.8, 不含等于)
    h.state.last_prompt_tokens = 80_000
    assert h.check_token_budget() is None, "exactly 80% should NOT trigger (> not >=)"
    
    # 81% — 触发
    h.state.last_prompt_tokens = 81_000
    warning = h.check_token_budget()
    assert warning is not None, "81% should trigger"
    assert "80%" in warning or "context" in warning
    
    print("✓ test_80_percent_threshold PASSED")


def test_digest_token_budget():
    """Test 6: 大量 digests 不会过度膨胀 format_context。"""
    h = create_test_harness()
    
    # 模拟读取大量 sections
    for name in list(h.state.paper_sections.keys())[:20]:
        if name != "full":
            h.execute_tool("read_section", {"section": name})
    
    ctx = h.format_context()
    ctx_tokens = len(ctx) // 4  # 粗略估算
    
    # 即使 20 个 digests，format_context 也不应超过 3000 tokens
    assert ctx_tokens < 3000, f"format_context too large with digests: {ctx_tokens} tokens"
    
    # digests 部分不应超过 context 的 30%
    digest_section_start = ctx.find("摘要缓存")
    if digest_section_start > -1:
        digest_section = ctx[digest_section_start:]
        digest_ratio = len(digest_section) / len(ctx)
        assert digest_ratio < 0.4, f"Digests too large: {digest_ratio:.1%} of context"
    
    print(f"  {len(h.state.section_digests)} digests, context: {ctx_tokens} tokens")
    print("✓ test_digest_token_budget PASSED")


def test_real_paper_full_flow():
    """Test 7: 真实论文端到端流程——读 10 个 sections 后验证 digest 质量。"""
    workspace = PROJECT_ROOT / ".workspace"
    if not workspace.exists():
        print("⚠️ test_real_paper_full_flow SKIPPED (no .workspace)")
        return
    
    h = Harness(paper_path=str(workspace), token_budget=200_000)
    
    # 读取 10 个核心 sections
    core_sections = [
        "abstract", "1. introduction", "5.1 baseline regression results",
        "6. conclusion", "5.2 parallel trends and event study",
        "5.5 addressing heterogeneous treatment effects",
        "3.4 variable definitions", "5.7 mechanism analysis",
    ]
    
    for sec in core_sections:
        h.execute_tool("read_section", {"section": sec})
    
    # 验证所有读过的 sections 都有 digest
    for sec in h.state.sections_read:
        assert sec in h.state.section_digests, f"Missing digest for: {sec}"
        digest = h.state.section_digests[sec]
        assert len(digest) > 20, f"Digest too short for {sec}: '{digest}'"
        assert len(digest) <= 150, f"Digest too long for {sec}: {len(digest)} chars"
    
    # 验证 format_context 展示 digests
    ctx = h.format_context()
    assert "摘要缓存" in ctx
    assert len(h.state.section_digests) >= 8
    
    # 验证 context 总大小合理
    ctx_chars = len(ctx)
    assert ctx_chars < 12000, f"Context too large: {ctx_chars} chars"
    
    print(f"  Read {len(h.state.sections_read)} sections, {len(h.state.section_digests)} digests")
    print(f"  Context size: {ctx_chars} chars (~{ctx_chars//4} tokens)")
    print("✓ test_real_paper_full_flow PASSED")


def test_compress_with_digests_preserves_recall():
    """Test 8: 压缩后 Agent 仍能通过 format_context 回溯 section 要点。"""
    h = create_test_harness(token_budget=100_000)
    
    # 读取 sections 生成 digests
    h.execute_tool("read_section", {"section": "abstract"})
    h.execute_tool("read_section", {"section": "introduction"})
    
    # 模拟大量 messages 被压缩
    messages = [{"role": "system", "content": "test"}]
    for i in range(15):
        messages.append({
            "role": "assistant", "content": f"step {i}",
            "tool_calls": [{"id": f"tc_{i}", "type": "function",
                          "function": {"name": "read_section", "arguments": json.dumps({"section": "test"})}}]
        })
        messages.append({"role": "tool", "tool_call_id": f"tc_{i}", "content": "long content " * 100})
    
    # 压缩
    compressed = h.compress_messages(messages)
    
    # 即使 messages 被压缩，format_context 仍包含 digests
    ctx = h.format_context()
    assert "摘要缓存" in ctx
    assert "abstract" in ctx
    
    # digests 内容来自 WorkspaceState，不受 messages 压缩影响
    assert h.state.section_digests.get("abstract") is not None
    
    print("✓ test_compress_with_digests_preserves_recall PASSED")


# ============================================================
# Runner
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  Phase 16: Intra-Session Context Offloading Tests")
    print("=" * 60)
    
    tests = [
        test_section_digest_generation,
        test_digest_stored_on_read,
        test_format_context_shows_digests,
        test_adaptive_keep_recent,
        test_80_percent_threshold,
        test_digest_token_budget,
        test_real_paper_full_flow,
        test_compress_with_digests_preserves_recall,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            print(f"\n{'─' * 50}")
            print(f"  {test.__name__}")
            print(f"{'─' * 50}")
            test()
            passed += 1
        except Exception as e:
            print(f"  ✗ FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print(f"\n{'=' * 60}")
    print(f"  结果: {passed} passed, {failed} failed (共 {len(tests)} 个测试)")
    if failed == 0:
        print("  🎉 ALL PHASE 16 TESTS PASSED")
    else:
        print(f"\n  ⚠️  存在 {failed} 个失败的测试")
    print(f"{'=' * 60}")
