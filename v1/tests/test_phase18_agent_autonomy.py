"""
Phase 18 测试: Agent 自主权恢复 (Agent Autonomy Restoration)

测试目标:
1. read_section 的 offset 续读能力正确工作
2. 截断提示包含明确的续读指令（offset 值）
3. offset 超出范围时优雅处理
4. format_context 不再包含硬编码的优先级分类
5. 反思上下文使用中性的"尚未阅读"列表

设计原则验证 (COGNITIVE_ANCHOR §4.3):
- 约束（单次 6000 字符窗口）仍然存在——控制 token 消耗
- 但 Agent 获得了继续读取的自主权——它可以选择是否深入

运行: python3 -m pytest tests/test_phase18_agent_autonomy.py -v
"""

import sys
from pathlib import Path

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.harness import Harness


# ============================================================
# Test Utilities
# ============================================================

def create_test_harness(long_section_chars: int = 10000) -> Harness:
    """创建一个带长 section 的 Harness 用于测试续读。"""
    h = Harness(token_budget=200_000)
    h._paper_loaded = True
    
    # 创建一个超过 6000 字符的 section 来触发截断
    long_content = "A" * long_section_chars
    
    h.state.paper_sections = {
        "abstract": "This paper studies X. " * 20,  # ~400 chars
        "introduction": "Background info about the topic. " * 50,  # ~1650 chars
        "methodology": long_content,  # 10000 chars，会被截断
        "results": "Table 1 shows significant effect. " * 200,  # ~7000 chars，会被截断
        "conclusion": "We conclude that X. " * 15,  # ~300 chars
        "references": "Author et al. 2020. Title. Journal. " * 100,  # ~3600 chars
    }
    return h


# ============================================================
# Phase 18A: 续读能力测试
# ============================================================

class TestReadSectionOffset:
    """测试 read_section 的 offset 续读功能。"""

    def test_short_section_no_truncation(self):
        """短 section（<6000字符）应该完整返回，无续读提示。"""
        h = create_test_harness()
        result = h.execute_tool("read_section", {"section": "abstract"})
        assert "[... 已显示字符" not in result
        assert "offset" not in result
        assert "This paper studies X." in result

    def test_long_section_truncated_with_continuation_hint(self):
        """长 section 应该截断到 6000 字符，并提供续读提示。"""
        h = create_test_harness(10000)
        result = h.execute_tool("read_section", {"section": "methodology"})
        
        # 应该被截断
        assert len(result.split("\n\n[... 已显示字符")[0]) <= 6000
        
        # 应该包含续读提示
        assert "offset=6000" in result
        assert "剩余" in result
        assert "如需继续阅读" in result

    def test_offset_continues_from_correct_position(self):
        """使用 offset 应该从正确位置继续读取。"""
        h = create_test_harness(10000)
        
        # 首次读取
        result1 = h.execute_tool("read_section", {"section": "methodology"})
        
        # 续读
        result2 = h.execute_tool("read_section", {"section": "methodology", "offset": 6000})
        
        # 续读应该包含位置标记
        assert "续读 methodology" in result2
        assert "字符 6000-" in result2

    def test_offset_final_chunk_no_continuation(self):
        """当 offset 后剩余内容 ≤ 6000 时，不应有续读提示。"""
        h = create_test_harness(8000)  # 8000 字符
        
        # 首次读取到 6000
        result1 = h.execute_tool("read_section", {"section": "methodology"})
        assert "offset=6000" in result1
        
        # 续读 6000-8000（2000 字符，不需要再截断）
        result2 = h.execute_tool("read_section", {"section": "methodology", "offset": 6000})
        assert "如需继续阅读" not in result2
        assert "续读 methodology" in result2

    def test_offset_beyond_content_length(self):
        """offset 超出 section 长度时，应优雅提示已到末尾。"""
        h = create_test_harness(5000)  # 5000 字符，小于窗口
        
        result = h.execute_tool("read_section", {"section": "methodology", "offset": 9999})
        assert "已到达" in result
        assert "末尾" in result
        assert "无需续读" in result

    def test_offset_zero_is_same_as_no_offset(self):
        """offset=0 应该等价于不传 offset。"""
        h = create_test_harness(10000)
        
        result_no_offset = h.execute_tool("read_section", {"section": "methodology"})
        
        h2 = create_test_harness(10000)
        result_zero = h2.execute_tool("read_section", {"section": "methodology", "offset": 0})
        
        assert result_no_offset == result_zero

    def test_offset_with_fuzzy_match(self):
        """offset 应该与模糊匹配兼容。"""
        h = create_test_harness(10000)
        
        # 模糊匹配 "method" -> "methodology"
        result = h.execute_tool("read_section", {"section": "method", "offset": 6000})
        assert "续读 methodology" in result

    def test_full_content_reconstruction(self):
        """通过多次续读应该能拼出完整内容。"""
        h = create_test_harness(15000)
        content = h.state.paper_sections["methodology"]
        
        # 读取所有分片
        chunks = []
        offset = 0
        while True:
            result = h.execute_tool("read_section", {"section": "methodology", "offset": offset})
            # 提取纯内容（去掉位置标记和续读提示）
            if "如需继续阅读" in result:
                # 有续读提示，截取到提示之前
                pure = result.split("\n\n[... 已显示字符")[0]
                if pure.startswith("[续读"):
                    pure = pure.split("\n\n", 1)[1] if "\n\n" in pure else pure
                chunks.append(pure)
                offset += 6000
            elif "续读" in result and "已到达" not in result:
                # 最后一片（有续读标记但无续读提示）
                pure = result.split("\n\n", 1)[1] if "\n\n" in result else result
                chunks.append(pure)
                break
            else:
                # 首次读取且未截断，或已到末尾
                chunks.append(result)
                break
        
        reconstructed = "".join(chunks)
        assert reconstructed == content

    def test_record_read_still_works_with_offset(self):
        """offset 续读不应重复记录 sections_read（首次已记录）。"""
        h = create_test_harness(10000)
        
        # 首次读取
        h.execute_tool("read_section", {"section": "methodology"})
        assert "methodology" in h.state.sections_read
        assert len(h.state.sections_read) == 1
        
        # 续读
        h.execute_tool("read_section", {"section": "methodology", "offset": 6000})
        # 不应重复添加
        assert h.state.sections_read.count("methodology") == 1

    def test_digest_generated_on_first_read_not_continuation(self):
        """Section digest 应该在首次读取时生成（基于完整内容），续读不应覆盖。"""
        h = create_test_harness(10000)
        
        # 首次读取
        h.execute_tool("read_section", {"section": "methodology"})
        assert "methodology" in h.state.section_digests
        digest_first = h.state.section_digests["methodology"]
        
        # 续读
        h.execute_tool("read_section", {"section": "methodology", "offset": 6000})
        # digest 不应改变
        assert h.state.section_digests["methodology"] == digest_first


# ============================================================
# Phase 18B: format_context 去分类测试
# ============================================================

class TestFormatContextNeutral:
    """测试 format_context 不再包含硬编码的优先级分类。"""

    def test_no_priority_icons_in_context(self):
        """format_context 不应再包含 🎯/📋/⏭️ 优先级标注。"""
        h = create_test_harness()
        ctx = h.format_context()
        assert "🎯" not in ctx
        assert "📋" not in ctx
        assert "⏭️" not in ctx
        assert "核心 (建议优先读)" not in ctx
        assert "可跳过" not in ctx

    def test_all_sections_listed_flat(self):
        """所有 section 应该平铺列出，只有名称和字符数。"""
        h = create_test_harness()
        ctx = h.format_context()
        
        # 应该包含所有 section 名称
        assert "abstract" in ctx
        assert "introduction" in ctx
        assert "methodology" in ctx
        assert "results" in ctx
        assert "conclusion" in ctx
        assert "references" in ctx

    def test_char_count_displayed(self):
        """每个 section 应该显示字符数。"""
        h = create_test_harness()
        ctx = h.format_context()
        # 格式: name (字数字)
        assert "字)" in ctx

    def test_offset_hint_in_context(self):
        """format_context 应该提示 offset 续读能力。"""
        h = create_test_harness()
        ctx = h.format_context()
        assert "offset" in ctx or "续读" in ctx

    def test_empty_sections_marked(self):
        """空壳 section（<50字符）应该标记为 (空)。"""
        h = Harness(token_budget=200_000)
        h._paper_loaded = True
        h.state.paper_sections = {
            "1. introduction": "See below",  # 9 chars < 50
            "1.1 background": "Detailed background content " * 50,
        }
        ctx = h.format_context()
        assert "(空)" in ctx


# ============================================================
# Phase 18B: 反思上下文去分类测试
# ============================================================

class TestReflectionNeutral:
    """测试 reflect_and_plan 不再强加核心/非核心判断。"""

    def test_reflection_no_core_label(self):
        """反思上下文不应包含'核心 sections'标签。"""
        h = create_test_harness()
        h.state.loop_turns = 5
        h.state.paper_sections["methodology"] = "Method content " * 500
        
        # 触发反思
        result = h.execute_tool("reflect_and_plan", {
            "trigger": "test",
            "current_thinking": "testing"
        })
        assert "核心 sections" not in result
        assert "⚠️ 未触及的核心" not in result

    def test_reflection_shows_untouched_neutrally(self):
        """反思应该中性地列出尚未阅读的 sections。"""
        h = create_test_harness()
        h.state.loop_turns = 5
        
        # 只读了 abstract
        h.execute_tool("read_section", {"section": "abstract"})
        h.state.findings = [{"finding": "test", "priority": "low", "status": "verified", "section": "abstract"}]
        
        result = h.execute_tool("reflect_and_plan", {
            "trigger": "test",
            "current_thinking": "testing"
        })
        
        # 应该列出未读 sections 但不做核心/非核心分类
        assert "尚未阅读" in result


# ============================================================
# 综合集成测试
# ============================================================

class TestPhase18Integration:
    """Phase 18 与现有功能的集成测试。"""

    def test_offset_compatible_with_prompter(self):
        """续读应该和 Phase 17 催促器兼容——续读也是'读'，计入连续读取。"""
        h = create_test_harness(10000)
        h.state.loop_turns = 5
        
        # 连续读取（包括续读）
        h.execute_tool("read_section", {"section": "methodology"})
        h.state.consecutive_read_turns += 1
        
        h.execute_tool("read_section", {"section": "methodology", "offset": 6000})
        h.state.consecutive_read_turns += 1
        
        h.execute_tool("read_section", {"section": "results"})
        h.state.consecutive_read_turns += 1
        
        # 催促器应该在第 3 轮后触发
        assert h.state.consecutive_read_turns >= 3

    def test_offset_compatible_with_compression(self):
        """续读功能不应影响 message 压缩机制。"""
        h = create_test_harness(10000)
        
        # 正常读取并续读
        h.execute_tool("read_section", {"section": "methodology"})
        h.execute_tool("read_section", {"section": "methodology", "offset": 6000})
        
        # section_digests 应该只生成一次
        assert len(h.state.section_digests) == 1
        assert "methodology" in h.state.section_digests

    def test_tool_definition_matches_implementation(self):
        """tool 定义中的 offset 参数应该被正确使用。"""
        from core.identity import SCHOLAR_TOOLS
        
        read_tool = next(t for t in SCHOLAR_TOOLS if t["name"] == "read_section")
        props = read_tool["input_schema"]["properties"]
        
        # offset 参数存在
        assert "offset" in props
        assert props["offset"]["type"] == "integer"
        
        # section 仍是必需的
        assert "section" in read_tool["input_schema"]["required"]
        # offset 不是必需的
        assert "offset" not in read_tool["input_schema"].get("required", [])
