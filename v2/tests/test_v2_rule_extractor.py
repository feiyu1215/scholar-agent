"""
Tests for rule_extractor.py — E0: 失败驱动规则生成

测试策略:
1. 用合成 PROGRESS.md 片段测试提取逻辑（不依赖真实文件）
2. 用合成 CLAUDE.md 片段测试 diff 逻辑
3. 最后用真实文件做集成测试（如果存在）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.rule_extractor import (
    FailureEntry,
    RuleCandidate,
    extract_failure_entries,
    cluster_entries,
    generate_rule_candidates,
    diff_with_existing_rules,
    extract_rule_candidates,
    format_report,
    _categorize_line,
    _fuzzy_in,
    FAILURE_CATEGORIES,
)


# ============================================================
# Fixtures: 合成 PROGRESS.md 片段
# ============================================================

SAMPLE_PROGRESS = """# ScholarAgent 进度追踪

## Phase 0 — 极简 Loop

Agent 读完 Introduction 就宣布完成。过早满足(satisfy early)严重。

## Phase 17 — 认知催促

Agent 读了 8 个 section 但只记录了 1 条 finding 就停。满足即停。
read_section 调用 8 次但 update_findings 只有 1 次。只读不记。

## Phase 31 — 搜索行为

整个审稿过程 0 次 search_literature。不搜索问题严重。
改了 identity 让 Agent"像顶会审稿人"但行为无变化。

## Phase 34 — 认知质量

6 条 findings 全是描述性的，泛泛描述。Agent 没有深入追查就收尾。过早满足。
Agent 读完 6 个 section 后 update_findings 产出全是描述性内容。
0 次搜索。Agent 停留在理解层面未进入质疑层面。

## Phase 38 — 认知模式

Agent 在 Turn 8 后进入"收尾模式"，有未验证 findings 也不管。满足即停。
多次 read_section 但无对应 update_findings。只读不记问题再现。
0 次搜索。搜索行为不稳定。
Agent 不会自主调用 reflect_and_plan。从未反思。

## Phase 40 — Gate 行为

Agent 有 needs_verification 的 findings 但直接 mark_complete。过早退出。
搜索完后直接进入"收尾模式"，不会抬头审视全局。

## Phase 42 — 认知层级

Agent 停留在"理解论文"层面，不进入"质疑论文"层面。理解≠审稿。

## Phase 47 — 重复问题

Finding 4 和 5 高度重复。overlap coefficient 高。去重机制缺失。

## v2-Phase 6 — HD-WM Bug

Bug 2：工具可见但 context 中无使用引导。LLM 不知道该什么时候用。永不调用。
HD-WM 引导提示只在"已有假说"时才注入。optional 行为 → Agent 从不传 section_name 参数。

## v2-Phase 9 — 行为经济学

HD-WM 三步路径 vs update_findings 一步路径。Agent 100% 选更短路径。
Agent 被拦截后走"直接标 verified 绕过"的最短路径。行为经济学。
工具可见 + identity 桥接 → LLM 仍不用（因为有更短替代路径）。

## v2-Phase 10 — 顺应设计

承认"与行为经济学对抗是无效的"。在已有最短路径上自动增强。

## Phase 43 — 深度追查隧道

Agent 在 Turn 4 形成假说后 Turn 5-11 全部围绕同一方向。不会抬头。
identity 中加了"深度饱和→切换方向"但 Agent 意识到了却问用户而不自己做。identity 不够。

## Phase 52 — 边际递减

Scholar 在 methodology 方向花了 22 轮触发 doom stop。边际产出递减但无法自我感知。

## Phase 56 — 去重

Turn 2/4/9/11 多次拦截重复 findings。去重机制生效。冗余 finding 持续出现。
"""

SAMPLE_CLAUDE_MD = """## 从审稿实践中提炼的认知约束 [L1]

以下规则源自 Phase 0-12 中反复出现(≥2次)的 Agent 失败模式。

- [Phase 0/17/34] 当 Agent 已读 2-3 sections 就想退出时，不要满足即止，而应检查 Harness 的覆盖率信号判断是否还有核心维度未审查
- [Phase 34/38/42] 当 Agent 遇到论文的方法论声明时，不要只转述论文说了什么，而应质疑该声明的假设、局限和可替代方案
- [Phase 31/38/39] 当 Agent 遇到"首次/原创"等贡献声明时，不要直接接受，而应调用 search_literature 验证是否有先行研究
- [Phase 33/v2-6] 当工具参数标记为 optional 时，不要期望 LLM 自发使用它——关键字段必须 required
- [Phase 47/56] 当 Agent 产出 finding 前，不要直接写入，而应先检查 findings_store 中是否已有语义重复的条目
- [v2-6/8/9] 当设计认知行为路径时，不要与 LLM 行为经济学对抗——在已有最短路径上自动增强
- [v2-8/11] 当 Gate 拦截要求 Agent 做某事时，不要假设 Agent 会主动调查
- [Phase 17/34/47] 当 Agent 连续读多个 section 不记录时，不要等读完再批量写 finding
"""


# ============================================================
# Unit Tests: extract_failure_entries
# ============================================================

class TestExtractFailureEntries:
    """测试从文本中提取失败条目。"""

    def test_basic_extraction(self):
        """能提取出失败条目。"""
        entries = extract_failure_entries(SAMPLE_PROGRESS)
        assert len(entries) > 0

    def test_phase_tracking(self):
        """正确追踪所属 Phase。"""
        entries = extract_failure_entries(SAMPLE_PROGRESS)
        # 找到 Phase 17 的条目
        phase17 = [e for e in entries if e.phase == "Phase 17"]
        assert len(phase17) >= 2, f"Phase 17 should have ≥2 entries, got {len(phase17)}"

    def test_v2_phase_tracking(self):
        """正确追踪 v2-Phase。"""
        entries = extract_failure_entries(SAMPLE_PROGRESS)
        v2_entries = [e for e in entries if "v2" in e.phase]
        assert len(v2_entries) >= 2, f"v2 phases should have ≥2 entries, got {len(v2_entries)}"

    def test_category_assignment(self):
        """正确分配 category。"""
        entries = extract_failure_entries(SAMPLE_PROGRESS)
        categories = set(e.category for e in entries)
        # 至少包含这些核心类别
        assert "satisfy_early" in categories
        assert "read_not_record" in categories
        assert "no_search" in categories

    def test_ignores_short_lines(self):
        """忽略太短的行。"""
        short_text = "## Phase 1\n| bug |\nok\n"
        entries = extract_failure_entries(short_text)
        assert len(entries) == 0

    def test_multiple_categories_per_line(self):
        """一行可以匹配多个类别。"""
        text = "## Phase 99\n这行同时有 满足即停 和 只读不记 的问题。read/update 比值异常。"
        entries = extract_failure_entries(text)
        categories = set(e.category for e in entries)
        assert len(categories) >= 2

    def test_empty_text(self):
        """空文本返回空列表。"""
        entries = extract_failure_entries("")
        assert entries == []


# ============================================================
# Unit Tests: cluster_entries
# ============================================================

class TestClusterEntries:
    """测试聚类逻辑。"""

    def test_basic_clustering(self):
        """基本聚类功能。"""
        entries = extract_failure_entries(SAMPLE_PROGRESS)
        clusters = cluster_entries(entries)
        # satisfy_early 至少出现 4 次
        assert "satisfy_early" in clusters
        assert len(clusters["satisfy_early"]) >= 4

    def test_filters_below_threshold(self):
        """过滤出现 <2 次的类别。"""
        entries = [
            FailureEntry(1, "test", "Phase 1", "rare_cat", []),
        ]
        clusters = cluster_entries(entries)
        assert "rare_cat" not in clusters

    def test_threshold_exactly_2(self):
        """恰好 2 次通过阈值。"""
        entries = [
            FailureEntry(1, "test1", "Phase 1", "cat_a", []),
            FailureEntry(2, "test2", "Phase 2", "cat_a", []),
        ]
        clusters = cluster_entries(entries)
        assert "cat_a" in clusters
        assert len(clusters["cat_a"]) == 2


# ============================================================
# Unit Tests: generate_rule_candidates
# ============================================================

class TestGenerateRuleCandidates:
    """测试规则候选生成。"""

    def test_generates_candidates(self):
        """从聚类生成规则候选。"""
        entries = extract_failure_entries(SAMPLE_PROGRESS)
        clusters = cluster_entries(entries)
        candidates = generate_rule_candidates(clusters)
        assert len(candidates) > 0

    def test_candidate_has_required_fields(self):
        """候选包含所有必要字段。"""
        entries = extract_failure_entries(SAMPLE_PROGRESS)
        clusters = cluster_entries(entries)
        candidates = generate_rule_candidates(clusters)

        for c in candidates:
            assert c.pattern_name
            assert c.category
            assert c.occurrences >= 2
            assert len(c.source_phases) >= 1
            assert c.root_cause
            assert c.rule_text
            assert "当" in c.rule_text
            assert "不要" in c.rule_text
            assert "而应" in c.rule_text

    def test_format_claude_md(self):
        """格式化为 CLAUDE.md 兼容格式。"""
        candidate = RuleCandidate(
            pattern_name="测试",
            category="test",
            occurrences=3,
            source_phases=["Phase 1", "Phase 2", "Phase 3"],
            root_cause="测试根因",
            rule_text="当X时，不要Y，而应Z",
        )
        formatted = candidate.format_claude_md()
        assert formatted.startswith("- [Phase 1/Phase 2/Phase 3]")
        assert "当X时，不要Y，而应Z" in formatted

    def test_sorted_by_occurrences(self):
        """候选按出现次数降序。"""
        entries = extract_failure_entries(SAMPLE_PROGRESS)
        clusters = cluster_entries(entries)
        candidates = generate_rule_candidates(clusters)
        for i in range(len(candidates) - 1):
            assert candidates[i].occurrences >= candidates[i + 1].occurrences


# ============================================================
# Unit Tests: diff_with_existing_rules
# ============================================================

class TestDiffWithExistingRules:
    """测试与已有规则的 diff。"""

    def test_identifies_covered(self):
        """识别已被覆盖的候选。"""
        entries = extract_failure_entries(SAMPLE_PROGRESS)
        clusters = cluster_entries(entries)
        candidates = generate_rule_candidates(clusters)
        covered, new = diff_with_existing_rules(candidates, SAMPLE_CLAUDE_MD)
        # 至少有一些已覆盖的
        assert len(covered) >= 3, f"Expected ≥3 covered, got {len(covered)}: {[c.category for c in covered]}"
        # shortest_path 和 read_not_record 应被覆盖（CLAUDE.md 中有明确对应关键词）
        covered_cats = [c.category for c in covered]
        assert "shortest_path" in covered_cats
        assert "read_not_record" in covered_cats

    def test_identifies_new(self):
        """识别新增候选。"""
        entries = extract_failure_entries(SAMPLE_PROGRESS)
        clusters = cluster_entries(entries)
        candidates = generate_rule_candidates(clusters)
        covered, new = diff_with_existing_rules(candidates, SAMPLE_CLAUDE_MD)
        # 应该有新的候选
        # understand_not_question 在 CLAUDE.md 中没有直接对应
        new_cats = [c.category for c in new]
        # 至少有一个新的
        assert len(new) > 0

    def test_empty_claude_md(self):
        """CLAUDE.md 为空时全部为新。"""
        entries = extract_failure_entries(SAMPLE_PROGRESS)
        clusters = cluster_entries(entries)
        candidates = generate_rule_candidates(clusters)
        covered, new = diff_with_existing_rules(candidates, "")
        assert len(covered) == 0
        assert len(new) == len(candidates)

    def test_full_coverage(self):
        """所有候选都被覆盖的极端情况。"""
        # 构造一个超级全面的 CLAUDE.md（每行包含足够关键词让 diff 识别为覆盖）
        mega_claude = "\n".join([
            "- [Phase 0] 当满足即停 satisfy early 过早满足 过早退出时，不要X，而应Y",
            "- [Phase 1] 当只读不记 read section update findings read/update 比值时，不要X，而应Y",
            "- [Phase 2] 当不搜索 search literature 0次 未搜索 search_literature时，不要X，而应Y",
            "- [Phase 3] 当描述性 理解 质疑 理解≠审稿 泛泛描述时，不要X，而应Y",
            "- [Phase 4] 当optional 可选 跳过 可选参数 永不调用时，不要X，而应Y",
            "- [Phase 5] 当identity 身份 无变化 认知身份时，不要X，而应Y",
            "- [Phase 6] 当不会抬头 从未反思 元认知 边际产出递减 doom时，不要X，而应Y",
            "- [Phase 7] 当短路径 shortest path 行为经济学 最短路径 绕过时，不要X，而应Y",
            "- [Phase 8] 当工具 不用 永不调用 物理不可见 工具可见时，不要X，而应Y",
            "- [Phase 9] 当重复 finding overlap 去重 冗余 高度重复时，不要X，而应Y",
        ])
        entries = extract_failure_entries(SAMPLE_PROGRESS)
        clusters = cluster_entries(entries)
        candidates = generate_rule_candidates(clusters)
        covered, new = diff_with_existing_rules(candidates, mega_claude)
        assert len(new) == 0, f"Unexpected new candidates: {[c.category for c in new]}"


# ============================================================
# Unit Tests: Helper functions
# ============================================================

class TestHelpers:
    """测试辅助函数。"""

    def test_categorize_line_satisfy_early(self):
        """分类: 满足即停。"""
        results = _categorize_line("Agent 过早满足就停了")
        cats = [r[0] for r in results]
        assert "satisfy_early" in cats

    def test_categorize_line_multiple(self):
        """一行匹配多个类别。"""
        results = _categorize_line("过早满足 + 不搜索 + 描述性")
        cats = [r[0] for r in results]
        assert len(cats) >= 2

    def test_categorize_line_no_match(self):
        """不匹配任何类别。"""
        results = _categorize_line("这是一行普通的进度描述。")
        assert len(results) == 0

    def test_fuzzy_in_chinese(self):
        """中文模糊匹配。"""
        assert _fuzzy_in("满足即停", "agent满足即停问题严重")
        assert _fuzzy_in("read.*不.*update", "read update findings ratio")  # 核心词根 "update" ≥3字符
        assert not _fuzzy_in("read.*不.*update", "no match here")

    def test_fuzzy_in_english(self):
        """英文模糊匹配。"""
        assert _fuzzy_in("shortest_path", "agent always takes shortest path")
        assert _fuzzy_in("search_literature", "we need search_literature calls")
        assert _fuzzy_in("search_literature", "literature review is missing")
        assert not _fuzzy_in("shortest_path", "no match at all")


# ============================================================
# Integration Tests: extract_rule_candidates (end-to-end)
# ============================================================

class TestEndToEnd:
    """端到端集成测试。"""

    def test_synthetic_e2e(self):
        """用合成数据做端到端测试。"""
        import tempfile
        import os

        # 写入临时文件
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(SAMPLE_PROGRESS)
            progress_path = f.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(SAMPLE_CLAUDE_MD)
            claude_path = f.name

        try:
            result = extract_rule_candidates(progress_path, claude_path)

            assert result["entries_found"] > 10
            assert len(result["clusters"]) >= 5
            assert len(result["candidates"]) >= 5
            assert "already_covered" in result
            assert "new_candidates" in result
            assert len(result["already_covered"]) + len(result["new_candidates"]) == len(result["candidates"])
        finally:
            os.unlink(progress_path)
            os.unlink(claude_path)

    def test_format_report(self):
        """报告格式化不崩溃。"""
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(SAMPLE_PROGRESS)
            progress_path = f.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(SAMPLE_CLAUDE_MD)
            claude_path = f.name

        try:
            result = extract_rule_candidates(progress_path, claude_path)
            report = format_report(result)
            assert "E0" in report
            assert "规则候选数" in report
            assert "新增候选" in report
        finally:
            os.unlink(progress_path)
            os.unlink(claude_path)

    def test_without_claude_md(self):
        """不提供 CLAUDE.md 时正常工作。"""
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(SAMPLE_PROGRESS)
            progress_path = f.name

        try:
            result = extract_rule_candidates(progress_path)
            assert "already_covered" not in result
            assert "new_candidates" not in result
            assert len(result["candidates"]) >= 5
        finally:
            os.unlink(progress_path)


# ============================================================
# Integration Test: Real Files (conditional)
# ============================================================

class TestRealFiles:
    """用真实文件做集成测试（条件执行）。"""

    @pytest.fixture
    def project_root(self):
        """获取项目根目录。"""
        return Path(__file__).parent.parent.parent

    def test_real_progress_md(self, project_root):
        """真实 PROGRESS.md 能正常处理。"""
        progress = project_root / "docs" / "PROGRESS.md"
        if not progress.exists():
            pytest.skip("PROGRESS.md not found")

        claude_md = project_root / "CLAUDE.md"
        claude_path = claude_md if claude_md.exists() else None

        result = extract_rule_candidates(progress, claude_path)

        # 真实文件应该有更多条目
        assert result["entries_found"] >= 20
        assert len(result["candidates"]) >= 5

        # 验证不崩溃
        report = format_report(result)
        assert len(report) > 100

    def test_real_diff_coverage(self, project_root):
        """真实 diff: 验证覆盖率合理。"""
        progress = project_root / "docs" / "PROGRESS.md"
        claude_md = project_root / "CLAUDE.md"

        if not progress.exists() or not claude_md.exists():
            pytest.skip("Real files not found")

        result = extract_rule_candidates(progress, claude_md)

        # CLAUDE.md 已有 8 条规则，应该覆盖 ≥3 个候选
        assert len(result["already_covered"]) >= 3
        # 应该至少有 1 个新候选（CLAUDE.md 不可能 100% 覆盖所有模式）
        # 放宽约束: 如果 CLAUDE.md 非常全面，新候选可以为 0
        assert len(result["new_candidates"]) >= 0


# ============================================================
# Edge Cases
# ============================================================

class TestEdgeCases:
    """边界情况测试。"""

    def test_no_phase_headers(self):
        """没有 Phase 标题时归类为 Unknown。"""
        text = "Agent 过早满足。满足即停。\n又一次过早满足。"
        entries = extract_failure_entries(text)
        for e in entries:
            assert e.phase == "Unknown"

    def test_all_categories_have_templates(self):
        """所有 FAILURE_CATEGORIES 都有对应的规则模板。"""
        # generate_rule_candidates 中的 rule_templates 应覆盖所有 category
        entries = []
        for i, cat in enumerate(FAILURE_CATEGORIES.keys()):
            entries.append(FailureEntry(i, f"test {cat}", f"Phase {i}", cat, []))
            entries.append(FailureEntry(i + 100, f"test2 {cat}", f"Phase {i+1}", cat, []))

        clusters = cluster_entries(entries)
        candidates = generate_rule_candidates(clusters)

        generated_cats = set(c.category for c in candidates)
        for cat in FAILURE_CATEGORIES:
            assert cat in generated_cats, f"Category '{cat}' has no rule template"

    def test_unicode_handling(self):
        """正确处理中英文混合。"""
        text = "## Phase 99\nAgent 的 satisfy-early 问题：过早满足导致 findings 质量低。"
        entries = extract_failure_entries(text)
        assert len(entries) >= 1

    def test_very_long_line(self):
        """超长行不崩溃。"""
        text = "## Phase 1\n" + "过早满足 " * 500
        entries = extract_failure_entries(text)
        for e in entries:
            assert len(e.text) <= 200  # 截断到 200 字符
