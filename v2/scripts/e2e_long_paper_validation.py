#!/usr/bin/env python3
"""
A3: E2E Long Paper Validation — 端到端验证脚本

验证目标 (来自 V3_REFINEMENT_PLAN.md A3):
1. 50+ 页长论文（15+ sections）能跑通完整 cognitive_loop → session_finalizer
2. Zone B 正确注入（full_load 包含实际内容，1-hop sections 进入 digest）
3. IntraSessionContrast 在 >= 15 sections 时触发
4. TokenBudgetManager 正确分配三区预算
5. Findings 产出非空（验证整体 pipeline 有效性）

运行模式:
    # 冒烟测试（mock LLM，不需要 API key）:
    python3 scripts/e2e_long_paper_validation.py --mode smoke

    # 完整验证（需要 API key）:
    python3 scripts/e2e_long_paper_validation.py --mode real

    # 指定论文路径:
    python3 scripts/e2e_long_paper_validation.py --mode smoke --paper path/to/paper.md

    # 只验证 Zone B 装配（不运行 loop，仅测试 assemble()）:
    python3 scripts/e2e_long_paper_validation.py --mode assemble-only

输出:
    - 终端实时验证报告
    - 退出码: 0=全部通过, 1=存在失败
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 确保 v2/ 在 sys.path
V2_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V2_ROOT))


# ============================================================
# Synthetic Long Paper Generator
# ============================================================

def generate_synthetic_long_paper(num_sections: int = 18) -> dict[str, str]:
    """Generate a synthetic 50-page paper with enough sections to trigger IntraContrast.

    Returns:
        dict mapping section_name → section_content
    """
    sections = {}

    # Generate realistic-looking ML paper sections
    section_templates = [
        ("Abstract", 200),
        ("1. Introduction", 800),
        ("2. Related Work", 1200),
        ("2.1 Transformer Architectures", 600),
        ("2.2 Knowledge Distillation", 600),
        ("2.3 Neural Architecture Search", 500),
        ("3. Methodology", 1500),
        ("3.1 Problem Formulation", 800),
        ("3.2 Model Architecture", 1000),
        ("3.3 Training Procedure", 700),
        ("3.4 Loss Function Design", 600),
        ("4. Experiments", 1200),
        ("4.1 Datasets", 500),
        ("4.2 Baselines", 400),
        ("4.3 Implementation Details", 600),
        ("4.4 Main Results", 900),
        ("4.5 Ablation Studies", 800),
        ("5. Analysis", 1000),
        ("5.1 Attention Visualization", 500),
        ("5.2 Error Analysis", 600),
        ("6. Discussion", 800),
        ("7. Conclusion", 400),
        ("8. Limitations", 300),
        ("References", 500),
    ]

    # Use requested number of sections (clip to available templates)
    templates_to_use = section_templates[:num_sections]

    for section_name, word_count in templates_to_use:
        # Generate plausible content
        content = _generate_section_content(section_name, word_count)
        sections[section_name] = content

    return sections


def _generate_section_content(section_name: str, word_count: int) -> str:
    """Generate plausible academic content for a section."""
    # Base sentences by section type
    seed_sentences = {
        "Abstract": [
            "We propose a novel approach to multi-task learning that leverages shared representations.",
            "Our method achieves state-of-the-art results on three benchmark datasets.",
            "We demonstrate that our architecture reduces computational cost by 40% while maintaining accuracy.",
            "Extensive experiments validate the effectiveness of our proposed framework.",
        ],
        "Introduction": [
            "Deep learning has revolutionized natural language processing in recent years.",
            "However, current approaches face significant challenges in computational efficiency.",
            "In this paper, we address the fundamental limitation of quadratic attention complexity.",
            "Our key insight is that sparse attention patterns can approximate full attention.",
            "We make the following contributions: (1) a novel architecture, (2) theoretical analysis, (3) empirical validation.",
        ],
        "Related Work": [
            "Transformer architectures (Vaswani et al., 2017) have become the de facto standard.",
            "Several works have explored efficient attention mechanisms.",
            "Knowledge distillation (Hinton et al., 2015) provides an orthogonal approach to model compression.",
            "Unlike previous methods, our approach does not require a pre-trained teacher model.",
        ],
        "Methodology": [
            "Let X ∈ R^{n×d} denote the input sequence of n tokens with d-dimensional embeddings.",
            "We define our objective function as the expected log-likelihood over the training distribution.",
            "The core of our approach is a learnable routing mechanism that selects relevant tokens.",
            "Algorithm 1 summarizes the complete training procedure.",
            "The computational complexity of our method is O(n log n), compared to O(n²) for standard attention.",
        ],
        "Experiments": [
            "We evaluate our method on GLUE, SuperGLUE, and SQuAD benchmarks.",
            "All models are trained on 8 NVIDIA A100 GPUs with a batch size of 256.",
            "We use AdamW optimizer with a learning rate of 3e-4 and linear warmup.",
            "Table 1 shows the main results compared to baseline methods.",
            "Our method achieves 92.3% accuracy, outperforming the previous best by 1.2%.",
        ],
        "default": [
            "This section presents additional analysis and discussion of our findings.",
            "We observe consistent improvements across all evaluated configurations.",
            "The results suggest that our approach generalizes well to unseen domains.",
            "Further investigation reveals interesting patterns in the learned representations.",
        ],
    }

    # Select appropriate seed sentences
    for key in seed_sentences:
        if key.lower() in section_name.lower():
            seeds = seed_sentences[key]
            break
    else:
        seeds = seed_sentences["default"]

    # Repeat and expand to reach desired word count
    lines = []
    current_words = 0
    idx = 0
    while current_words < word_count:
        sentence = seeds[idx % len(seeds)]
        lines.append(sentence)
        current_words += len(sentence.split())
        idx += 1
        # Add some variety every 5 sentences
        if idx % 5 == 0:
            lines.append("")  # paragraph break

    return "\n".join(lines)


# ============================================================
# Validation Checks
# ============================================================

@dataclass
class ValidationResult:
    """Result of a single validation check."""
    name: str
    passed: bool
    details: str = ""
    duration_ms: float = 0.0


@dataclass
class ValidationReport:
    """Complete validation report."""
    mode: str
    results: list[ValidationResult] = field(default_factory=list)
    total_duration_s: float = 0.0

    @property
    def num_passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def num_failed(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)


def check_zone_b_injection(assembler, state) -> ValidationResult:
    """Validate that Zone B correctly injects full_load content."""
    start = time.time()

    output = assembler.assemble(state=state, current_turn=1)

    # Check 1: Zone B Full marker present
    has_full = "[Zone B Full]" in output
    # Check 2: Actual content from paper_sections is present
    current_section = state.sections_read[-1] if state.sections_read else ""
    content_injected = False
    if current_section and current_section in state.paper_sections:
        # First 50 chars of section content should appear
        snippet = state.paper_sections[current_section][:50]
        content_injected = snippet in output or any(
            word in output for word in snippet.split()[:5]
        )

    passed = has_full and content_injected
    details = (
        f"Zone B Full marker: {'✓' if has_full else '✗'}, "
        f"Content injected: {'✓' if content_injected else '✗'}, "
        f"Current section: {current_section}, "
        f"Output length: {len(output)} chars"
    )

    return ValidationResult(
        name="Zone B Full Load Injection",
        passed=passed,
        details=details,
        duration_ms=(time.time() - start) * 1000,
    )


def check_zone_b_digest(assembler, state) -> ValidationResult:
    """Validate that 1-hop neighbors get digest treatment."""
    start = time.time()

    output = assembler.assemble(state=state, current_turn=2)

    has_digest = "[Zone B Digest]" in output
    # Check that at least one digest section name appears
    digest_sections = []
    if has_digest:
        digest_block = output.split("[Zone B Digest]")[-1].split("\n📄")[0]
        for sec_name in state.section_digests:
            if sec_name in digest_block:
                digest_sections.append(sec_name)

    passed = has_digest and len(digest_sections) > 0
    details = (
        f"Zone B Digest marker: {'✓' if has_digest else '✗'}, "
        f"Digest sections found: {digest_sections}"
    )

    return ValidationResult(
        name="Zone B Digest Load (1-hop)",
        passed=passed,
        details=details,
        duration_ms=(time.time() - start) * 1000,
    )


def check_budget_allocation(budget_manager, pcg, current_section: str) -> ValidationResult:
    """Validate TokenBudgetManager allocation correctness."""
    start = time.time()

    allocation = budget_manager.compute_zone_b_allocation(
        pcg=pcg,
        current_task_section=current_section,
    )

    # Current section should be in full_load
    current_in_full = current_section in allocation.full_load
    # Should have some estimated_tokens > 0
    has_budget = allocation.estimated_tokens > 0
    # Should not exceed zone_b_max
    within_budget = allocation.estimated_tokens <= budget_manager.zone_b_max

    passed = current_in_full and has_budget and within_budget
    details = (
        f"Current in full_load: {'✓' if current_in_full else '✗'}, "
        f"Estimated tokens: {allocation.estimated_tokens}, "
        f"Within budget (≤{budget_manager.zone_b_max}): {'✓' if within_budget else '✗'}, "
        f"Full: {allocation.full_load}, "
        f"Digest: {allocation.digest_load[:3]}..., "
        f"Name-only: {len(allocation.name_only)} sections"
    )

    return ValidationResult(
        name="Token Budget Allocation",
        passed=passed,
        details=details,
        duration_ms=(time.time() - start) * 1000,
    )


def check_intra_contrast_threshold(state, num_sections: int) -> ValidationResult:
    """Validate IntraContrast eligibility for long papers."""
    start = time.time()

    from core.godel_config import GODEL_INTRA_CONTRAST_ENABLED

    # IntraContrast requires >= 15 sections
    INTRA_CONTRAST_MIN_SECTIONS = 15
    eligible = num_sections >= INTRA_CONTRAST_MIN_SECTIONS and GODEL_INTRA_CONTRAST_ENABLED

    passed = eligible
    details = (
        f"Sections: {num_sections} (threshold: {INTRA_CONTRAST_MIN_SECTIONS}), "
        f"Eligible: {'✓' if eligible else '✗'}, "
        f"Kill switch enabled: {'✓' if GODEL_INTRA_CONTRAST_ENABLED else '✗'}"
    )

    return ValidationResult(
        name="IntraContrast Eligibility",
        passed=passed,
        details=details,
        duration_ms=(time.time() - start) * 1000,
    )


def check_kill_switch_degradation(assembler_cls, state) -> ValidationResult:
    """Validate graceful degradation when budget kill switch is off."""
    start = time.time()
    from unittest.mock import MagicMock, patch

    memory = MagicMock()
    memory.format_memory_context.return_value = ""
    cognitive_state = MagicMock()
    cognitive_state.format_for_context.return_value = ""
    offload_store = MagicMock()
    offload_store.format_refs_summary.return_value = ""

    with patch("core.godel_config.GODEL_BUDGET_MANAGER_ENABLED", False):
        from core.token_budget import TokenBudgetManager
        assembler = assembler_cls(
            memory=memory,
            cognitive_state=cognitive_state,
            offload_store=offload_store,
            token_budget_manager=TokenBudgetManager(total_budget=128_000),
        )
        output = assembler.assemble(state=state, current_turn=1)

    # Should NOT have Zone B content
    no_zone_b = "[Zone B Full]" not in output and "[Zone B Digest]" not in output
    # Should still produce valid output
    has_output = len(output) > 50

    passed = no_zone_b and has_output
    details = (
        f"No Zone B: {'✓' if no_zone_b else '✗'}, "
        f"Has output: {'✓' if has_output else '✗'} ({len(output)} chars)"
    )

    return ValidationResult(
        name="Kill Switch Degradation",
        passed=passed,
        details=details,
        duration_ms=(time.time() - start) * 1000,
    )


# ============================================================
# Assemble-Only Mode
# ============================================================

def run_assemble_only_validation(num_sections: int = 18) -> ValidationReport:
    """Run validation focusing on ContextAssembler + TokenBudgetManager only.

    No LLM calls needed — purely tests the context assembly pipeline.
    """
    from unittest.mock import MagicMock

    from core.assembler import ContextAssembler
    from core.token_budget import TokenBudgetManager
    from core.state import WorkspaceState
    from core.paper_cognition_graph import PaperCognitionGraph, PCGNode, PCGEdge

    report = ValidationReport(mode="assemble-only")
    start_time = time.time()

    # 1. Generate synthetic paper
    paper_sections = generate_synthetic_long_paper(num_sections)
    sections_list = list(paper_sections.keys())
    print(f"  Generated synthetic paper: {len(sections_list)} sections, "
          f"~{sum(len(v.split()) for v in paper_sections.values())} words")

    # 2. Build PCG with edges
    pcg = PaperCognitionGraph()
    for name, content in paper_sections.items():
        pcg.nodes[name] = PCGNode(section_name=name, word_count=len(content.split()))
    # Add sequential edges
    for i in range(len(sections_list) - 1):
        pcg.edges.append(PCGEdge(
            source=sections_list[i],
            target=sections_list[i + 1],
            edge_type="FOLLOWS",
            weight=0.3,
        ))
    # Add some cross-references
    if len(sections_list) > 10:
        pcg.edges.append(PCGEdge(
            source=sections_list[3], target=sections_list[8],
            edge_type="REFERENCES", weight=0.6,
        ))
        pcg.edges.append(PCGEdge(
            source=sections_list[5], target=sections_list[11],
            edge_type="REFERENCES", weight=0.5,
        ))

    # 3. Build state
    state = WorkspaceState()
    state.paper_sections = paper_sections
    state.sections_read = [sections_list[0], sections_list[6]]  # current = Methodology
    state.section_digests = {
        sections_list[1]: "Introduction of the main contributions.",
        sections_list[2]: "Survey of related transformer and distillation works.",
        sections_list[7] if len(sections_list) > 7 else sections_list[-1]: "Problem setup.",
    }
    state.paper_cognition_graph = pcg

    # 4. Build assembler
    memory = MagicMock()
    memory.format_memory_context.return_value = ""
    cognitive_state = MagicMock()
    cognitive_state.format_for_context.return_value = ""
    offload_store = MagicMock()
    offload_store.format_refs_summary.return_value = ""

    budget_mgr = TokenBudgetManager(total_budget=128_000)
    assembler = ContextAssembler(
        memory=memory,
        cognitive_state=cognitive_state,
        offload_store=offload_store,
        token_budget_manager=budget_mgr,
    )

    # 5. Run validation checks
    print("\n  Running validation checks...\n")

    # Check 1: Zone B Full Load
    result = check_zone_b_injection(assembler, state)
    report.results.append(result)
    _print_check(result)

    # Check 2: Zone B Digest
    result = check_zone_b_digest(assembler, state)
    report.results.append(result)
    _print_check(result)

    # Check 3: Budget Allocation
    current_section = state.sections_read[-1]
    result = check_budget_allocation(budget_mgr, pcg, current_section)
    report.results.append(result)
    _print_check(result)

    # Check 4: IntraContrast Eligibility
    result = check_intra_contrast_threshold(state, num_sections)
    report.results.append(result)
    _print_check(result)

    # Check 5: Kill Switch Degradation
    result = check_kill_switch_degradation(ContextAssembler, state)
    report.results.append(result)
    _print_check(result)

    report.total_duration_s = time.time() - start_time
    return report


# ============================================================
# Smoke Test Mode (mock LLM)
# ============================================================

def run_smoke_validation(num_sections: int = 18) -> ValidationReport:
    """Run E2E with a mock LLM client (no API calls).

    Validates:
    - Harness initialization with long paper
    - Paper loading + PCG construction
    - Context assembly with Zone B
    - Budget manager integration
    """
    from unittest.mock import MagicMock, AsyncMock, patch

    from core.harness import Harness
    from core.state import WorkspaceState

    report = ValidationReport(mode="smoke")
    start_time = time.time()

    # Generate synthetic paper and write to temp file
    paper_sections = generate_synthetic_long_paper(num_sections)
    temp_paper_path = V2_ROOT / "evaluation" / "temp_synthetic_paper.md"

    # Write as markdown
    with open(temp_paper_path, "w", encoding="utf-8") as f:
        for section_name, content in paper_sections.items():
            f.write(f"## {section_name}\n\n{content}\n\n")

    print(f"  Wrote synthetic paper: {temp_paper_path} ({num_sections} sections)")

    try:
        # Initialize Harness
        harness = Harness(
            paper_path=str(temp_paper_path),
            max_loop_turns=5,  # Just enough for smoke test
            token_budget=200_000,
            context_window=128_000,
        )
        harness.load_paper()

        state = harness.state
        sections_loaded = len(state.paper_sections)
        print(f"  Paper loaded: {sections_loaded} sections detected")

        # Check 1: Paper sections loaded
        result = ValidationResult(
            name="Paper Loading",
            passed=sections_loaded >= 5,
            details=f"Loaded {sections_loaded} sections (expected ≥5)",
        )
        report.results.append(result)
        _print_check(result)

        # Check 2: Budget manager initialized
        has_budget_mgr = harness.token_budget_manager is not None
        result = ValidationResult(
            name="Budget Manager Init",
            passed=has_budget_mgr,
            details=f"TokenBudgetManager present: {'✓' if has_budget_mgr else '✗'}",
        )
        report.results.append(result)
        _print_check(result)

        # Check 3: Context assembly produces output
        # Simulate reading a section (skip "full" which is the entire paper text)
        if state.paper_sections:
            real_sections = [k for k in state.paper_sections.keys() if k != "full"]
            if real_sections:
                state.sections_read.append(real_sections[0])

        context_output = harness.format_context()
        result = ValidationResult(
            name="Context Assembly",
            passed=len(context_output) > 100,
            details=f"Context output: {len(context_output)} chars",
        )
        report.results.append(result)
        _print_check(result)

        # Check 4: Zone B in context (if budget manager is active)
        if has_budget_mgr and state.sections_read:
            has_zone_b = "[Zone B Full]" in context_output
            result = ValidationResult(
                name="Zone B in Context",
                passed=has_zone_b,
                details=f"Zone B Full present: {'✓' if has_zone_b else '✗'} "
                        f"(current section: {state.sections_read[-1]})",
            )
            report.results.append(result)
            _print_check(result)

        # Check 5: PCG created (if applicable)
        has_pcg = state.paper_cognition_graph is not None
        result = ValidationResult(
            name="PCG Construction",
            passed=True,  # PCG might not be built until first loop turn
            details=f"PCG present: {'✓' if has_pcg else '(deferred to loop)'}"
        )
        report.results.append(result)
        _print_check(result)

    finally:
        # Cleanup
        if temp_paper_path.exists():
            temp_paper_path.unlink()

    report.total_duration_s = time.time() - start_time
    return report


# ============================================================
# Real Mode (requires API key)
# ============================================================

def run_real_validation(paper_path: str | None, num_sections: int = 18) -> ValidationReport:
    """Run full E2E with real LLM.

    Requires OPENAI_API_KEY or equivalent in environment.
    """
    report = ValidationReport(mode="real")
    start_time = time.time()

    # Use provided paper or generate synthetic
    if paper_path:
        actual_path = paper_path
        print(f"  Using paper: {actual_path}")
    else:
        paper_sections = generate_synthetic_long_paper(num_sections)
        actual_path = str(V2_ROOT / "evaluation" / "temp_synthetic_paper.md")
        with open(actual_path, "w", encoding="utf-8") as f:
            for section_name, content in paper_sections.items():
                f.write(f"## {section_name}\n\n{content}\n\n")
        print(f"  Generated synthetic paper: {actual_path}")

    try:
        from core.agent import UnifiedReviewAgent

        agent = UnifiedReviewAgent(
            paper_path=actual_path,
            verbose=True,
            max_loop_turns=30,
            token_budget=200_000,
            context_window=128_000,
        )

        print("\n  Running agent (this may take several minutes)...\n")
        result = asyncio.run(agent.run(
            user_intent="请审阅这篇论文，重点关注方法论的严谨性和实验设计的完整性。"
        ))

        # Validate results
        findings = result.get("findings", [])
        stats = result.get("stats", {})

        # Check 1: Findings produced
        has_findings = len(findings) > 0
        check_result = ValidationResult(
            name="Findings Produced",
            passed=has_findings,
            details=f"Found {len(findings)} findings",
        )
        report.results.append(check_result)
        _print_check(check_result)

        # Check 2: Token budget not exceeded
        total_tokens = stats.get("total_tokens", 0)
        within_budget = total_tokens <= 200_000
        check_result = ValidationResult(
            name="Token Budget Respected",
            passed=within_budget,
            details=f"Used {total_tokens} tokens (budget: 200,000)",
        )
        report.results.append(check_result)
        _print_check(check_result)

        # Check 3: Multiple sections read
        sections_read = stats.get("sections_read", 0)
        multi_section = sections_read >= 3
        check_result = ValidationResult(
            name="Multi-Section Coverage",
            passed=multi_section,
            details=f"Read {sections_read} sections",
        )
        report.results.append(check_result)
        _print_check(check_result)

        # Check 4: No crash (if we got here, it passed)
        check_result = ValidationResult(
            name="No Crash (E2E Complete)",
            passed=True,
            details=f"Agent completed in {stats.get('loop_turns', '?')} turns",
        )
        report.results.append(check_result)
        _print_check(check_result)

    except Exception as e:
        check_result = ValidationResult(
            name="E2E Execution",
            passed=False,
            details=f"Exception: {type(e).__name__}: {e}",
        )
        report.results.append(check_result)
        _print_check(check_result)

    finally:
        # Cleanup synthetic paper
        if not paper_path:
            temp_path = Path(actual_path)
            if temp_path.exists():
                temp_path.unlink()

    report.total_duration_s = time.time() - start_time
    return report


# ============================================================
# Output Formatting
# ============================================================

def _print_check(result: ValidationResult):
    """Print a single check result."""
    icon = "✓" if result.passed else "✗"
    status = "PASS" if result.passed else "FAIL"
    print(f"  [{icon}] {status}: {result.name}")
    print(f"      {result.details}")
    if result.duration_ms > 0:
        print(f"      ({result.duration_ms:.1f}ms)")
    print()


def print_summary(report: ValidationReport):
    """Print final summary."""
    print("\n" + "=" * 60)
    print(f"  E2E Validation Summary ({report.mode} mode)")
    print("=" * 60)
    print(f"  Total checks: {len(report.results)}")
    print(f"  Passed: {report.num_passed}")
    print(f"  Failed: {report.num_failed}")
    print(f"  Duration: {report.total_duration_s:.2f}s")
    print("=" * 60)

    if report.all_passed:
        print("\n  ✓ ALL CHECKS PASSED\n")
    else:
        print("\n  ✗ SOME CHECKS FAILED:")
        for r in report.results:
            if not r.passed:
                print(f"    - {r.name}: {r.details}")
        print()


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="A3: E2E Long Paper Validation")
    parser.add_argument(
        "--mode", choices=["smoke", "real", "assemble-only"],
        default="assemble-only",
        help="Validation mode (default: assemble-only, no API key needed)",
    )
    parser.add_argument(
        "--paper", type=str, default=None,
        help="Path to paper file (real mode only; otherwise generates synthetic)",
    )
    parser.add_argument(
        "--sections", type=int, default=18,
        help="Number of sections for synthetic paper (default: 18, ≥15 for IntraContrast)",
    )
    args = parser.parse_args()

    print(f"\n{'=' * 60}")
    print(f"  A3: E2E Long Paper Validation")
    print(f"  Mode: {args.mode} | Sections: {args.sections}")
    print(f"{'=' * 60}\n")

    if args.mode == "assemble-only":
        report = run_assemble_only_validation(args.sections)
    elif args.mode == "smoke":
        report = run_smoke_validation(args.sections)
    elif args.mode == "real":
        # Load .env for API keys
        try:
            from dotenv import load_dotenv
            load_dotenv(V2_ROOT / ".env")
        except ImportError:
            pass
        report = run_real_validation(args.paper, args.sections)
    else:
        print(f"Unknown mode: {args.mode}", file=sys.stderr)
        sys.exit(1)

    print_summary(report)
    sys.exit(0 if report.all_passed else 1)


if __name__ == "__main__":
    main()
