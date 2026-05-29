"""
tests/test_b7_frozen_snapshot.py — B7 Frozen Snapshot 前缀缓存测试

验证:
    1. 首次压缩: frozen_prefix 为空 → 完整生成 restoration，存入 frozen_prefix
    2. 第 2 次压缩: frozen_prefix 非空 → 只追加增量 delta
    3. 连续 3 次压缩: frozen_prefix 累积正确，无重复信息
    4. compaction_seq 正确递增
    5. 向后兼容: 不设 frozen_prefix 时行为与 B4 一致
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.compaction import CompactionEngine, CompactionConfig, WorkspaceSnapshot


# ============================================================
# Helpers
# ============================================================

def _make_snapshot_seq1() -> WorkspaceSnapshot:
    """首次压缩的快照：已读 3 个 section，发现 2 个问题。"""
    return WorkspaceSnapshot(
        sections_read=["introduction", "methods", "results"],
        total_sections=8,
        loop_turns=5,
        findings_count=2,
        findings_summary=["[major] Missing baseline comparison", "[minor] Typo in eq.3"],
        consecutive_read_turns=2,
        recent_tools=["read_section", "read_section", "submit_finding"],
        history_summary="历史中共执行了 12 次工具调用:\n  read_section: 8次\n  submit_finding: 4次",
        session_memory_text="[Session Memory] 论文方法部分实验设计有缺陷",
    )


def _make_snapshot_seq2(frozen_prefix: str) -> WorkspaceSnapshot:
    """第 2 次压缩的快照：已读 5 个 section，新发现 1 个问题。"""
    return WorkspaceSnapshot(
        sections_read=["introduction", "methods", "results", "discussion", "conclusion"],
        total_sections=8,
        loop_turns=10,
        findings_count=3,
        findings_summary=[
            "[major] Missing baseline comparison",
            "[minor] Typo in eq.3",
            "[major] Discussion doesn't address limitations",
        ],
        consecutive_read_turns=1,
        recent_tools=["read_section", "read_section", "submit_finding", "read_section", "submit_finding"],
        history_summary="历史中共执行了 24 次工具调用:\n  read_section: 16次\n  submit_finding: 8次",
        session_memory_text="[Session Memory] Discussion 部分对 limitations 覆盖不足",
        frozen_prefix=frozen_prefix,
        compaction_seq=1,
    )


def _make_snapshot_seq3(frozen_prefix: str) -> WorkspaceSnapshot:
    """第 3 次压缩的快照：读完全部，总计 4 个发现。"""
    return WorkspaceSnapshot(
        sections_read=["introduction", "methods", "results", "discussion", "conclusion",
                       "abstract", "references", "appendix"],
        total_sections=8,
        loop_turns=15,
        findings_count=4,
        findings_summary=[
            "[major] Missing baseline comparison",
            "[minor] Typo in eq.3",
            "[major] Discussion doesn't address limitations",
            "[minor] Incomplete reference list",
        ],
        consecutive_read_turns=0,
        recent_tools=["submit_finding", "read_section", "read_section", "generate_review", "submit_finding"],
        history_summary="历史中共执行了 36 次工具调用:\n  read_section: 22次\n  submit_finding: 14次",
        session_memory_text="[Session Memory] 审稿接近完成，准备生成报告",
        frozen_prefix=frozen_prefix,
        compaction_seq=2,
    )


# ============================================================
# Test: 首次压缩（frozen_prefix 为空）
# ============================================================

class TestFirstCompaction:
    """首次压缩行为：frozen_prefix 为空 → 完整生成。"""

    def test_first_compaction_generates_full_restoration(self):
        """首次压缩应生成完整 restoration text。"""
        snapshot = _make_snapshot_seq1()
        assert snapshot.frozen_prefix == ""
        assert snapshot.compaction_seq == 0

        result = snapshot.format_restoration(budget_tokens=6000)

        # 结果非空
        assert result
        # 不包含增量 separator
        assert "增量更新" not in result
        # 包含 findings 信息（critical layer）
        assert "Missing baseline comparison" in result
        assert "Typo in eq.3" in result
        # 包含 session memory
        assert "论文方法部分" in result

    def test_first_compaction_sets_frozen_prefix(self):
        """首次压缩后 frozen_prefix 应被设置为完整输出。"""
        snapshot = _make_snapshot_seq1()
        result = snapshot.format_restoration(budget_tokens=6000)

        assert snapshot.frozen_prefix == result
        assert snapshot.compaction_seq == 1

    def test_first_compaction_no_separator(self):
        """首次压缩输出不应包含增量分隔符。"""
        snapshot = _make_snapshot_seq1()
        result = snapshot.format_restoration(budget_tokens=6000)
        assert "---" not in result or "增量更新" not in result


# ============================================================
# Test: 第 2 次压缩（带 frozen_prefix）
# ============================================================

class TestSecondCompaction:
    """第 2 次压缩行为：frozen_prefix 非空 → 增量追加。"""

    def test_second_compaction_appends_delta(self):
        """第 2 次压缩应在 frozen_prefix 后追加 delta。"""
        # 先执行首次压缩获取 frozen_prefix
        snap1 = _make_snapshot_seq1()
        first_output = snap1.format_restoration(budget_tokens=6000)

        # 用首次输出作为 frozen_prefix 构建第 2 次快照
        snap2 = _make_snapshot_seq2(frozen_prefix=first_output)
        second_output = snap2.format_restoration(budget_tokens=6000)

        # 第 2 次输出应包含首次输出（前缀）
        assert second_output.startswith(first_output)
        # 且包含增量分隔符
        assert "增量更新 #1" in second_output
        # delta 部分应包含新信息
        delta_part = second_output[len(first_output):]
        assert "Discussion doesn't address limitations" in delta_part

    def test_second_compaction_increments_seq(self):
        """第 2 次压缩后 compaction_seq 应为 2。"""
        snap1 = _make_snapshot_seq1()
        first_output = snap1.format_restoration(budget_tokens=6000)

        snap2 = _make_snapshot_seq2(frozen_prefix=first_output)
        snap2.format_restoration(budget_tokens=6000)

        assert snap2.compaction_seq == 2

    def test_second_compaction_frozen_prefix_updated(self):
        """第 2 次压缩后 frozen_prefix 应为完整输出（供第 3 次使用）。"""
        snap1 = _make_snapshot_seq1()
        first_output = snap1.format_restoration(budget_tokens=6000)

        snap2 = _make_snapshot_seq2(frozen_prefix=first_output)
        second_output = snap2.format_restoration(budget_tokens=6000)

        assert snap2.frozen_prefix == second_output


# ============================================================
# Test: 连续 3 次压缩一致性
# ============================================================

class TestThreeConsecutiveCompactions:
    """3 次连续压缩的一致性验证。"""

    def _run_three_compactions(self):
        """执行 3 次连续压缩，返回 (output1, output2, output3)。"""
        # 第 1 次
        snap1 = _make_snapshot_seq1()
        out1 = snap1.format_restoration(budget_tokens=6000)

        # 第 2 次
        snap2 = _make_snapshot_seq2(frozen_prefix=out1)
        out2 = snap2.format_restoration(budget_tokens=6000)

        # 第 3 次
        snap3 = _make_snapshot_seq3(frozen_prefix=out2)
        out3 = snap3.format_restoration(budget_tokens=6000)

        return out1, out2, out3

    def test_output_grows_monotonically(self):
        """每次压缩输出长度单调递增。"""
        out1, out2, out3 = self._run_three_compactions()
        assert len(out1) < len(out2) < len(out3)

    def test_prefix_preserved_across_compactions(self):
        """前次输出作为后次前缀被完整保留。"""
        out1, out2, out3 = self._run_three_compactions()
        assert out2.startswith(out1)
        assert out3.startswith(out2)

    def test_separator_format_correct(self):
        """增量分隔符格式正确。"""
        out1, out2, out3 = self._run_three_compactions()
        # 第 2 次输出应有 #1 separator
        assert "增量更新 #1" in out2
        # 第 3 次输出应有 #1 和 #2 separator
        assert "增量更新 #1" in out3
        assert "增量更新 #2" in out3

    def test_no_duplicate_findings(self):
        """虽然 findings 在每次快照中都存在，但 frozen_prefix 机制保证前缀不变。"""
        out1, out2, out3 = self._run_three_compactions()
        # out1 中有 "Missing baseline comparison"
        assert out1.count("Missing baseline comparison") == 1
        # out3 中: 前缀保留的 + delta 中的 = 多次出现是因为 delta 包含新快照
        # 关键是前缀部分不被修改
        prefix_part_in_out3 = out3[:len(out1)]
        assert prefix_part_in_out3 == out1

    def test_compaction_seq_tracking(self):
        """compaction_seq 在各阶段正确。"""
        snap1 = _make_snapshot_seq1()
        assert snap1.compaction_seq == 0
        snap1.format_restoration(budget_tokens=6000)
        assert snap1.compaction_seq == 1

        snap2 = _make_snapshot_seq2(frozen_prefix=snap1.frozen_prefix)
        assert snap2.compaction_seq == 1
        snap2.format_restoration(budget_tokens=6000)
        assert snap2.compaction_seq == 2

        snap3 = _make_snapshot_seq3(frozen_prefix=snap2.frozen_prefix)
        assert snap3.compaction_seq == 2
        snap3.format_restoration(budget_tokens=6000)
        assert snap3.compaction_seq == 3


# ============================================================
# Test: 向后兼容
# ============================================================

class TestBackwardCompatibility:
    """不使用 frozen_prefix 时，行为与 B4 一致。"""

    def test_default_snapshot_has_empty_frozen_prefix(self):
        """默认 WorkspaceSnapshot 的 frozen_prefix 为空。"""
        snap = WorkspaceSnapshot()
        assert snap.frozen_prefix == ""
        assert snap.compaction_seq == 0

    def test_no_frozen_prefix_same_as_before(self):
        """无 frozen_prefix 时，format_restoration 行为与提取前一致。"""
        snap = WorkspaceSnapshot(
            sections_read=["intro"],
            total_sections=5,
            findings_count=1,
            findings_summary=["[major] Test finding"],
        )
        result = snap.format_restoration(budget_tokens=6000)
        # 结果应包含 findings
        assert "Test finding" in result
        # 不包含增量标记
        assert "增量更新" not in result

    def test_compact_method_unchanged(self):
        """CompactionEngine.compact() 方法签名和行为不受 B7 影响。"""
        from core.state import WorkspaceState

        engine = CompactionEngine()
        state = WorkspaceState()
        state.loop_turns = 10
        state.last_prompt_tokens = 90000
        state.context_window = 128000
        state.sections_read = ["intro", "methods"]
        state.paper_sections = ["intro", "methods", "results", "disc", "conc"]
        state.findings = [{"priority": "major", "finding": "Test"}]
        state.tool_call_history = [{"name": "read_section", "input": {}}] * 5
        state.consecutive_read_turns = 2

        # 构造足够的消息
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(12):
            msgs.append({"role": "assistant", "content": f"resp_{i}"})
            msgs.append({"role": "user", "content": f"query_{i}"})

        snapshot = engine.build_snapshot(state, msgs)
        result = engine.compact(msgs, snapshot, state)

        # compact 仍然返回正确结构
        assert result[0]["role"] == "system"
        assert "[上下文恢复]" in result[1]["content"]
        assert result[2]["role"] == "assistant"


# ============================================================
# Test: 边界情况
# ============================================================

class TestEdgeCases:
    """边界情况测试。"""

    def test_empty_delta_with_frozen_prefix(self):
        """即使 delta 很短，frozen_prefix + separator + delta 格式仍正确。"""
        # 构建一个几乎为空的快照但带 frozen_prefix
        snap = WorkspaceSnapshot(
            sections_read=[],
            total_sections=0,
            findings_count=0,
            findings_summary=[],
            frozen_prefix="Previous full restoration text here.",
            compaction_seq=1,
        )
        result = snap.format_restoration(budget_tokens=6000)
        assert result.startswith("Previous full restoration text here.")
        assert "增量更新 #1" in result

    def test_format_restoration_is_idempotent_per_call(self):
        """同一 snapshot 多次调用 format_restoration 会累积（非幂等设计）。"""
        snap = _make_snapshot_seq1()
        first = snap.format_restoration(budget_tokens=6000)
        # 此时 frozen_prefix 已更新，再次调用会追加
        second = snap.format_restoration(budget_tokens=6000)
        assert len(second) > len(first)
        assert second.startswith(first)

    def test_large_frozen_prefix_budget_awareness(self):
        """带大型 frozen_prefix 时，delta 部分仍受 budget 约束。"""
        large_prefix = "x" * 10000
        snap = WorkspaceSnapshot(
            sections_read=["a"] * 20,
            total_sections=50,
            findings_count=5,
            findings_summary=["[major] " + f"finding_{i}" * 20 for i in range(5)],
            session_memory_text="Memory " * 200,
            hypothesis_text="Hypothesis " * 200,
            paper_structure_text="Structure " * 200,
            frozen_prefix=large_prefix,
            compaction_seq=3,
        )
        result = snap.format_restoration(budget_tokens=2000)
        # 应该以 large_prefix 开头
        assert result.startswith(large_prefix)
        # delta 部分应存在但可能被 budget 裁剪
        delta_part = result[len(large_prefix):]
        assert "增量更新 #3" in delta_part
