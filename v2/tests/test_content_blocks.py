"""
tests/test_content_blocks.py — ContentBlocks 结构化输出单元测试
"""

import unittest

from core.content_blocks import (
    TextBlock,
    ThinkingBlock,
    FindingBlock,
    FigureBlock,
    TableBlock,
    HintBlock,
    CitationBlock,
    CodeBlock,
    BlockType,
    ContentBlock,
    wrap_text,
    blocks_to_string,
    blocks_to_api_parts,
    string_to_blocks,
    filter_blocks,
    extract_findings,
    extract_thinking,
)


class TestTextBlock(unittest.TestCase):
    """TextBlock 基础测试"""

    def test_creation(self):
        block = TextBlock(text="Hello world")
        self.assertEqual(block.block_type, BlockType.TEXT)
        self.assertEqual(block.text, "Hello world")

    def test_render(self):
        block = TextBlock(text="paragraph text")
        self.assertEqual(block.render(), "paragraph text")

    def test_to_api_format(self):
        block = TextBlock(text="test content")
        api = block.to_api_format()
        self.assertEqual(api["type"], "text")
        self.assertEqual(api["text"], "test content")

    def test_frozen(self):
        """frozen dataclass 不可修改"""
        block = TextBlock(text="immutable")
        with self.assertRaises(Exception):
            block.text = "changed"  # type: ignore


class TestThinkingBlock(unittest.TestCase):
    """ThinkingBlock 测试"""

    def test_creation(self):
        block = ThinkingBlock(reasoning="reasoning here", confidence=0.8)
        self.assertEqual(block.block_type, BlockType.THINKING)
        self.assertEqual(block.confidence, 0.8)
        self.assertEqual(block.reasoning_type, "general")

    def test_render_includes_confidence(self):
        block = ThinkingBlock(reasoning="my reasoning", confidence=0.75)
        rendered = block.render()
        self.assertIn("Thinking", rendered)
        self.assertIn("my reasoning", rendered)
        self.assertIn("0.75", rendered)

    def test_to_api_format_with_thinking_tag(self):
        block = ThinkingBlock(reasoning="deep thought")
        api = block.to_api_format()
        self.assertIn("<thinking>", api["text"])
        self.assertIn("deep thought", api["text"])

    def test_reasoning_type(self):
        block = ThinkingBlock(reasoning="test", reasoning_type="deductive")
        self.assertEqual(block.reasoning_type, "deductive")


class TestFindingBlock(unittest.TestCase):
    """FindingBlock 测试"""

    def test_required_fields(self):
        block = FindingBlock(
            category="methodology",
            severity="critical",
            description="DID 缺少平行趋势检验",
            evidence="Section 3 未报告处理前趋势",
            suggestion="补充 pre-trend test",
        )
        self.assertEqual(block.block_type, BlockType.FINDING)
        self.assertEqual(block.category, "methodology")
        self.assertEqual(block.severity, "critical")

    def test_render_formatting(self):
        block = FindingBlock(
            category="statistics",
            severity="major",
            description="样本量不足",
            evidence="Table 1 报告 N=50",
            suggestion="增加样本",
        )
        rendered = block.render()
        self.assertIn("MAJOR", rendered)
        self.assertIn("statistics", rendered)
        self.assertIn("样本量不足", rendered)

    def test_to_finding_dict(self):
        """转为兼容旧格式的 dict"""
        block = FindingBlock(
            category="clarity",
            severity="minor",
            description="描述不清",
            evidence="第 3 段",
            suggestion="改写",
            section="Introduction",
            status="verified",
        )
        d = block.to_finding_dict()
        self.assertEqual(d["category"], "clarity")
        self.assertEqual(d["priority"], "medium")  # minor → medium
        self.assertEqual(d["section"], "Introduction")
        self.assertEqual(d["status"], "verified")

    def test_critical_severity_maps_to_high_priority(self):
        block = FindingBlock(
            category="logic",
            severity="critical",
            description="逻辑矛盾",
            evidence="证据",
            suggestion="建议",
        )
        d = block.to_finding_dict()
        self.assertEqual(d["priority"], "high")

    def test_default_confidence_and_status(self):
        block = FindingBlock(
            category="methodology",
            severity="major",
            description="test",
            evidence="ev",
            suggestion="sug",
        )
        self.assertEqual(block.confidence, 0.8)
        self.assertEqual(block.status, "verified")


class TestFigureBlock(unittest.TestCase):
    """FigureBlock 测试"""

    def test_creation(self):
        block = FigureBlock(
            figure_id="Figure 3",
            caption="实验结果对比",
            image_path="/path/to/fig.png",
        )
        self.assertEqual(block.block_type, BlockType.FIGURE)
        self.assertEqual(block.figure_id, "Figure 3")

    def test_render(self):
        block = FigureBlock(figure_id="Figure 1", caption="结果")
        rendered = block.render()
        self.assertIn("Figure 1", rendered)
        self.assertIn("结果", rendered)

    def test_to_api_format_text_fallback(self):
        """无 image_data 时应返回 text 格式"""
        block = FigureBlock(figure_id="F1", caption="test")
        api = block.to_api_format()
        self.assertEqual(api["type"], "text")

    def test_to_api_format_with_image_data(self):
        """有 image_data 时应返回 image_url 格式"""
        block = FigureBlock(
            figure_id="F1",
            caption="test",
            image_data=b"\x89PNG\r\n",
        )
        api = block.to_api_format()
        self.assertEqual(api["type"], "image_url")
        self.assertIn("base64", api["image_url"]["url"])


class TestTableBlock(unittest.TestCase):
    """TableBlock 测试"""

    def test_creation_with_data(self):
        block = TableBlock(
            table_id="Table 1",
            caption="回归结果",
            headers=("Variable", "Coefficient", "SE"),
            rows=(("X1", "0.5", "0.1"), ("X2", "-0.3", "0.2")),
        )
        self.assertEqual(block.block_type, BlockType.TABLE)
        self.assertEqual(len(block.rows), 2)

    def test_render_table_format(self):
        block = TableBlock(
            table_id="T1",
            caption="Test",
            headers=("A", "B"),
            rows=(("1", "2"), ("3", "4")),
        )
        rendered = block.render()
        self.assertIn("|", rendered)
        self.assertIn("A", rendered)
        self.assertIn("T1", rendered)

    def test_render_truncates_long_tables(self):
        """超过 10 行应截断"""
        rows = tuple((str(i), str(i * 2)) for i in range(20))
        block = TableBlock(table_id="T2", headers=("X", "Y"), rows=rows)
        rendered = block.render()
        self.assertIn("more rows", rendered)


class TestHintBlock(unittest.TestCase):
    """HintBlock 测试"""

    def test_creation(self):
        block = HintBlock(content="考虑使用 IV 估计", hint_type="nudge")
        self.assertEqual(block.block_type, BlockType.HINT)
        self.assertEqual(block.hint_type, "nudge")

    def test_render(self):
        block = HintBlock(content="检查异方差", priority="high")
        rendered = block.render()
        self.assertIn("检查异方差", rendered)
        self.assertIn("high", rendered)

    def test_to_api_format_uses_content_only(self):
        """API 格式只传 content，不暴露 hint_type"""
        block = HintBlock(content="internal hint")
        api = block.to_api_format()
        self.assertEqual(api["text"], "internal hint")


class TestCitationBlock(unittest.TestCase):
    """CitationBlock 测试"""

    def test_creation(self):
        block = CitationBlock(
            citation_key="Angrist2009",
            authors="Angrist, Pischke",
            year="2009",
            title="Mostly Harmless Econometrics",
            venue="Princeton University Press",
        )
        self.assertEqual(block.block_type, BlockType.CITATION)
        self.assertEqual(block.year, "2009")

    def test_render(self):
        block = CitationBlock(
            citation_key="AK1991",
            authors="Abadie, Kasy",
            year="1991",
            title="Paper Title",
            venue="AER",
        )
        rendered = block.render()
        self.assertIn("AK1991", rendered)
        self.assertIn("Abadie, Kasy", rendered)
        self.assertIn("1991", rendered)


class TestCodeBlock(unittest.TestCase):
    """CodeBlock 测试"""

    def test_creation(self):
        block = CodeBlock(code="print('hello')", language="python")
        self.assertEqual(block.block_type, BlockType.CODE)
        self.assertEqual(block.language, "python")

    def test_render_fenced(self):
        block = CodeBlock(code="lm(y ~ x)", language="r")
        rendered = block.render()
        self.assertIn("```r", rendered)
        self.assertIn("lm(y ~ x)", rendered)

    def test_render_with_purpose_and_output(self):
        block = CodeBlock(
            code="x = 1 + 1",
            language="python",
            purpose="测试计算",
            output="2",
        )
        rendered = block.render()
        self.assertIn("测试计算", rendered)
        self.assertIn("Output:", rendered)


class TestUtilityFunctions(unittest.TestCase):
    """测试全局工具函数"""

    def setUp(self):
        self.blocks = [
            TextBlock(text="Introduction paragraph"),
            FindingBlock(
                category="methodology",
                severity="major",
                description="Issue found",
                evidence="Evidence here",
                suggestion="Fix this",
            ),
            TextBlock(text="Another paragraph"),
            ThinkingBlock(reasoning="I think therefore I am"),
        ]

    def test_wrap_text(self):
        block = wrap_text("hello")
        self.assertIsInstance(block, TextBlock)
        self.assertEqual(block.text, "hello")

    def test_blocks_to_string(self):
        """blocks_to_string 渲染所有 blocks"""
        result = blocks_to_string(self.blocks)
        self.assertIn("Introduction paragraph", result)
        self.assertIn("Issue found", result)
        self.assertIn("Another paragraph", result)

    def test_blocks_to_api_parts(self):
        """转为 API 格式列表"""
        parts = blocks_to_api_parts(self.blocks)
        self.assertEqual(len(parts), 4)
        self.assertEqual(parts[0]["type"], "text")

    def test_string_to_blocks(self):
        """从字符串创建 TextBlock 列表"""
        blocks = string_to_blocks("hello world")
        self.assertEqual(len(blocks), 1)
        self.assertIsInstance(blocks[0], TextBlock)
        self.assertEqual(blocks[0].text, "hello world")

    def test_string_to_blocks_empty(self):
        """空字符串返回空列表"""
        blocks = string_to_blocks("")
        self.assertEqual(blocks, [])

    def test_filter_blocks(self):
        """按 BlockType 过滤"""
        findings = filter_blocks(self.blocks, BlockType.FINDING)
        self.assertEqual(len(findings), 1)

        texts = filter_blocks(self.blocks, BlockType.TEXT)
        self.assertEqual(len(texts), 2)

        thinking = filter_blocks(self.blocks, BlockType.THINKING)
        self.assertEqual(len(thinking), 1)

    def test_extract_findings(self):
        """提取所有 FindingBlock"""
        findings = extract_findings(self.blocks)
        self.assertEqual(len(findings), 1)
        self.assertIsInstance(findings[0], FindingBlock)

    def test_extract_thinking(self):
        """提取所有 ThinkingBlock"""
        thinking = extract_thinking(self.blocks)
        self.assertEqual(len(thinking), 1)
        self.assertIsInstance(thinking[0], ThinkingBlock)

    def test_empty_blocks(self):
        """空列表处理"""
        self.assertEqual(blocks_to_string([]), "")
        self.assertEqual(blocks_to_api_parts([]), [])
        self.assertEqual(filter_blocks([], BlockType.TEXT), [])
        self.assertEqual(extract_findings([]), [])


if __name__ == "__main__":
    unittest.main()
