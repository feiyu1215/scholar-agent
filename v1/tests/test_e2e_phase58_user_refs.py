"""
Phase 58 E2E 验证: 用户参考文献能力 (User-Provided References)

目标:
1. 验证 Harness 能正确加载用户提供的 Markdown 参考文献
2. 验证 read_reference 工具的各种调用模式（列出、按 section 读取、offset 续读）
3. 验证 format_context 正确区分用户提供的和 Agent 获取的文献
4. 验证 ScholarAgent 构造函数正确传递 reference_paths
5. 验证工具路由正确连接

验证标准:
- _load_user_references 能正确解析 Markdown 文件
- read_reference 无参数时列出所有参考文献
- read_reference 指定 ref_id 时列出 sections
- read_reference 指定 section 时返回内容（支持模糊匹配）
- read_reference 支持 offset 续读
- format_context 中用户文献和 Agent 文献分开展示
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ============================================================
# 准备测试用的参考文献文件
# ============================================================

SAMPLE_REFERENCE_MD = """# A Survey of Causal Inference Methods

## Abstract

This paper surveys modern causal inference methods including
Difference-in-Differences (DID), Instrumental Variables (IV),
Regression Discontinuity Design (RDD), and Synthetic Control.
We discuss their assumptions, limitations, and recent advances.

## Introduction

Causal inference has become central to empirical economics.
The credibility revolution (Angrist and Pischke, 2010) shifted
the field toward quasi-experimental designs that exploit natural
variation for identification. This survey covers the four main
approaches used in applied microeconomics.

## Methodology

### Difference-in-Differences

DID relies on the parallel trends assumption: in the absence of
treatment, the treated and control groups would have followed
similar trajectories. Recent work by Callaway and Sant'Anna (2021)
and Sun and Abraham (2021) has highlighted issues with staggered
adoption designs and proposed robust estimators.

### Instrumental Variables

IV estimation requires relevance (the instrument predicts treatment)
and exclusion (the instrument affects outcomes only through treatment).
Weak instruments remain a persistent challenge (Andrews et al., 2019).

### Regression Discontinuity

RDD exploits discontinuities in treatment assignment at a threshold.
Key assumptions include continuity of potential outcomes at the cutoff
and no manipulation of the running variable (McCrary, 2008).

## Results

We find that DID remains the most popular method (45% of papers in
top-5 journals 2015-2023), followed by IV (28%), RDD (18%), and
Synthetic Control (9%). However, the share of papers using robust
DID estimators has grown from 2% in 2019 to 34% in 2023.

## Conclusion

The field is moving toward more robust estimation and transparent
reporting of identifying assumptions. We recommend that researchers
always report sensitivity analyses and discuss potential violations
of their key assumptions.
"""

SAMPLE_PAPER_MD = """## Abstract

We propose a novel method for estimating treatment effects in
staggered DID designs that is robust to heterogeneous effects.

## Introduction

Difference-in-Differences is widely used but recent work shows
problems with two-way fixed effects under staggered adoption.

## Methodology

Our estimator builds on Callaway and Sant'Anna (2021) but adds
a Bayesian shrinkage component for small-sample settings.

## Results

In simulations, our method reduces MSE by 30% compared to CS(2021)
when group sizes are below 50. In our empirical application to
minimum wage effects, we find an elasticity of -0.15 (SE=0.04).

## Conclusion

We provide a practical tool for researchers facing small-sample
staggered DID settings.
"""


# ============================================================
# Test 1: 基础加载功能
# ============================================================

def test_load_user_references():
    """验证 _load_user_references 能正确解析 Markdown 文件。"""
    from core.harness import Harness

    print("\n" + "=" * 60)
    print("Test 1: _load_user_references 基础加载")
    print("=" * 60)

    # 创建临时参考文献文件
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(SAMPLE_REFERENCE_MD)
        ref_path = f.name

    # 创建临时论文文件
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(SAMPLE_PAPER_MD)
        paper_path = f.name

    harness = Harness(paper_path=paper_path, reference_paths=[ref_path])

    # 检查 user_reference_docs
    assert "ref_1" in harness.state.user_reference_docs, "ref_1 should be in user_reference_docs"
    doc = harness.state.user_reference_docs["ref_1"]
    assert "sections" in doc, "doc should have sections"
    assert len(doc["sections"]) > 1, f"Should have multiple sections, got {len(doc['sections'])}"
    assert "section_names" in doc, "doc should have section_names"
    print(f"  ✅ Loaded ref_1 with {len(doc['sections'])} sections: {doc['section_names']}")

    # 检查 reference_papers 元数据
    assert "ref_1" in harness.state.reference_papers, "ref_1 should be in reference_papers"
    meta = harness.state.reference_papers["ref_1"]
    assert meta["source"] == "user_provided", f"source should be 'user_provided', got {meta['source']}"
    assert meta["section_count"] > 0, "section_count should be > 0"
    assert meta["total_chars"] > 0, "total_chars should be > 0"
    print(f"  ✅ Metadata: source={meta['source']}, sections={meta['section_count']}, chars={meta['total_chars']}")
    print(f"  ✅ Abstract preview: {meta['abstract'][:80]}...")

    # 清理
    Path(ref_path).unlink()
    Path(paper_path).unlink()
    return True


# ============================================================
# Test 2: read_reference 工具 — 列出所有参考文献
# ============================================================

def test_read_reference_list_all():
    """验证 read_reference 无参数时列出所有可用参考文献。"""
    from core.harness import Harness

    print("\n" + "=" * 60)
    print("Test 2: read_reference — 列出所有参考文献")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(SAMPLE_REFERENCE_MD)
        ref_path = f.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(SAMPLE_PAPER_MD)
        paper_path = f.name

    harness = Harness(paper_path=paper_path, reference_paths=[ref_path])

    # 不传 ref_id — 应该列出所有
    result = harness.execute_tool("read_reference", {})
    assert "ref_1" in result, f"Should list ref_1, got: {result[:200]}"
    assert "可用的参考文献" in result, f"Should have header, got: {result[:200]}"
    print(f"  ✅ Listed all references:\n{result}")

    Path(ref_path).unlink()
    Path(paper_path).unlink()
    return True


# ============================================================
# Test 3: read_reference 工具 — 列出 sections
# ============================================================

def test_read_reference_list_sections():
    """验证 read_reference 指定 ref_id 但不指定 section 时列出 sections。"""
    from core.harness import Harness

    print("\n" + "=" * 60)
    print("Test 3: read_reference — 列出 sections")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(SAMPLE_REFERENCE_MD)
        ref_path = f.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(SAMPLE_PAPER_MD)
        paper_path = f.name

    harness = Harness(paper_path=paper_path, reference_paths=[ref_path])

    result = harness.execute_tool("read_reference", {"ref_id": "ref_1"})
    assert "可用 sections" in result, f"Should list sections, got: {result[:200]}"
    assert "字)" in result, f"Should show char counts, got: {result[:200]}"
    print(f"  ✅ Listed sections:\n{result}")

    Path(ref_path).unlink()
    Path(paper_path).unlink()
    return True


# ============================================================
# Test 4: read_reference 工具 — 读取具体 section
# ============================================================

def test_read_reference_read_section():
    """验证 read_reference 能读取具体 section 内容，支持模糊匹配。"""
    from core.harness import Harness

    print("\n" + "=" * 60)
    print("Test 4: read_reference — 读取具体 section")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(SAMPLE_REFERENCE_MD)
        ref_path = f.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(SAMPLE_PAPER_MD)
        paper_path = f.name

    harness = Harness(paper_path=paper_path, reference_paths=[ref_path])

    # 精确匹配
    result = harness.execute_tool("read_reference", {"ref_id": "ref_1", "section": "methodology"})
    assert "Difference-in-Differences" in result, f"Should contain DID content, got: {result[:200]}"
    assert "📎" in result, f"Should have header icon, got: {result[:100]}"
    print(f"  ✅ Read 'methodology' section (first 200 chars):\n{result[:200]}...")

    # 模糊匹配（大小写不敏感）
    result2 = harness.execute_tool("read_reference", {"ref_id": "ref_1", "section": "Methodology"})
    assert "Difference-in-Differences" in result2, "Case-insensitive match should work"
    print(f"  ✅ Case-insensitive match works")

    # 部分匹配
    result3 = harness.execute_tool("read_reference", {"ref_id": "ref_1", "section": "result"})
    assert "45%" in result3 or "DID remains" in result3, f"Partial match should find 'results', got: {result3[:200]}"
    print(f"  ✅ Partial match works ('result' → 'results')")

    Path(ref_path).unlink()
    Path(paper_path).unlink()
    return True


# ============================================================
# Test 5: read_reference 工具 — offset 续读
# ============================================================

def test_read_reference_offset():
    """验证 read_reference 支持 offset 续读长内容。"""
    from core.harness import Harness

    print("\n" + "=" * 60)
    print("Test 5: read_reference — offset 续读")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(SAMPLE_REFERENCE_MD)
        ref_path = f.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(SAMPLE_PAPER_MD)
        paper_path = f.name

    harness = Harness(paper_path=paper_path, reference_paths=[ref_path])

    # 用很小的 max_chars 来触发续读
    result1 = harness.execute_tool("read_reference", {
        "ref_id": "ref_1", "section": "methodology", "max_chars": 100
    })
    assert "剩余" in result1, f"Should indicate remaining content, got: {result1[:300]}"
    assert "offset=" in result1, f"Should suggest next offset, got: {result1[:300]}"
    print(f"  ✅ First chunk (100 chars) with continuation hint")

    # 续读
    result2 = harness.execute_tool("read_reference", {
        "ref_id": "ref_1", "section": "methodology", "offset": 100, "max_chars": 100
    })
    assert "offset=100" in result2, f"Should show current offset, got: {result2[:200]}"
    print(f"  ✅ Second chunk from offset=100")

    Path(ref_path).unlink()
    Path(paper_path).unlink()
    return True


# ============================================================
# Test 6: format_context 区分展示
# ============================================================

def test_format_context_distinction():
    """验证 format_context 正确区分用户提供的和 Agent 获取的文献。"""
    from core.harness import Harness

    print("\n" + "=" * 60)
    print("Test 6: format_context 区分展示")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(SAMPLE_REFERENCE_MD)
        ref_path = f.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(SAMPLE_PAPER_MD)
        paper_path = f.name

    harness = Harness(paper_path=paper_path, reference_paths=[ref_path])

    # 模拟 Agent 也获取了一篇论文
    harness.state.reference_papers["agent_paper_001"] = {
        "title": "Attention Is All You Need",
        "authors": ["Vaswani", "Shazeer"],
        "year": 2017,
        "venue": "NeurIPS",
        "abstract": "We propose a new architecture...",
        "tldr": "Transformer architecture for sequence transduction",
        "citation_count": 100000,
        "source": "semantic_scholar",  # Agent 获取的
    }

    context = harness.format_context()

    # 应该有两个分区
    assert "📎 用户提供的参考文献" in context, f"Should have user refs section, got:\n{context}"
    assert "📚 Agent 获取的外部论文" in context, f"Should have agent refs section, got:\n{context}"
    assert "read_reference" in context, f"Should mention read_reference tool, got:\n{context}"
    assert "ref_1" in context, f"Should show ref_1 ID, got:\n{context}"
    assert "Attention Is All You Need" in context, f"Should show agent paper, got:\n{context}"

    print(f"  ✅ format_context correctly distinguishes user vs agent refs")
    print(f"\n  Context excerpt (reference section):")
    # 只打印参考文献相关部分
    for line in context.split("\n"):
        if "📎" in line or "📚" in line or "ref_" in line or "Attention" in line:
            print(f"    {line}")

    Path(ref_path).unlink()
    Path(paper_path).unlink()
    return True


# ============================================================
# Test 7: ScholarAgent 构造函数传递
# ============================================================

def test_agent_constructor():
    """验证 ScholarAgent 正确接受和传递 reference_paths。"""
    from core.agent import ScholarAgent

    print("\n" + "=" * 60)
    print("Test 7: ScholarAgent 构造函数")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(SAMPLE_REFERENCE_MD)
        ref_path = f.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(SAMPLE_PAPER_MD)
        paper_path = f.name

    agent = ScholarAgent(
        paper_path=paper_path,
        reference_paths=[ref_path],
    )

    # 验证参考文献已加载
    assert "ref_1" in agent.harness.state.user_reference_docs, "Agent should have loaded refs"
    assert "ref_1" in agent.harness.state.reference_papers, "Agent should have ref metadata"
    print(f"  ✅ ScholarAgent correctly loaded reference via reference_paths")

    Path(ref_path).unlink()
    Path(paper_path).unlink()
    return True


# ============================================================
# Test 8: 错误处理
# ============================================================

def test_error_handling():
    """验证 read_reference 的各种错误情况。"""
    from core.harness import Harness

    print("\n" + "=" * 60)
    print("Test 8: read_reference 错误处理")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(SAMPLE_PAPER_MD)
        paper_path = f.name

    # 没有参考文献的情况
    harness = Harness(paper_path=paper_path)
    result = harness.execute_tool("read_reference", {})
    assert "没有用户提供的参考文献" in result, f"Should indicate no refs, got: {result}"
    print(f"  ✅ No refs: '{result[:80]}'")

    # 有参考文献但 ref_id 不存在
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(SAMPLE_REFERENCE_MD)
        ref_path = f.name

    harness2 = Harness(paper_path=paper_path, reference_paths=[ref_path])
    result2 = harness2.execute_tool("read_reference", {"ref_id": "ref_99"})
    assert "未找到" in result2, f"Should indicate not found, got: {result2}"
    print(f"  ✅ Invalid ref_id: '{result2[:80]}'")

    # section 不存在
    result3 = harness2.execute_tool("read_reference", {"ref_id": "ref_1", "section": "nonexistent_xyz"})
    assert "未找到 section" in result3, f"Should indicate section not found, got: {result3}"
    print(f"  ✅ Invalid section: '{result3[:80]}'")

    # offset 超出范围
    result4 = harness2.execute_tool("read_reference", {"ref_id": "ref_1", "section": "abstract", "offset": 999999})
    assert "超出" in result4, f"Should indicate offset out of range, got: {result4}"
    print(f"  ✅ Offset overflow: '{result4[:80]}'")

    Path(paper_path).unlink()
    Path(ref_path).unlink()
    return True


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Phase 58 E2E 验证: 用户参考文献能力")
    print("=" * 60)

    tests = [
        ("加载用户参考文献", test_load_user_references),
        ("列出所有参考文献", test_read_reference_list_all),
        ("列出 sections", test_read_reference_list_sections),
        ("读取具体 section", test_read_reference_read_section),
        ("offset 续读", test_read_reference_offset),
        ("format_context 区分展示", test_format_context_distinction),
        ("ScholarAgent 构造函数", test_agent_constructor),
        ("错误处理", test_error_handling),
    ]

    results = []
    for name, test_fn in tests:
        try:
            passed = test_fn()
            results.append((name, passed))
        except Exception as e:
            print(f"\n  ❌ EXCEPTION in '{name}': {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    # 汇总
    print("\n" + "=" * 60)
    print("Phase 58 测试汇总")
    print("=" * 60)
    passed = sum(1 for _, r in results if r)
    total = len(results)
    for name, r in results:
        icon = "✅" if r else "❌"
        print(f"  {icon} {name}")
    print(f"\n  结果: {passed}/{total} 通过")

    if passed == total:
        print("\n  🎉 Phase 58 全部验证通过！用户参考文献能力完整可用。")
    else:
        print("\n  ⚠️ 有测试未通过，需要修复。")
        sys.exit(1)
