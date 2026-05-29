"""
Phase 33: E2E 认知验证 — Phase 32 新机制的真实 LLM 使用验证

核心问题（不是"跑不跑通"，而是"用不用起来"）:
    1. Agent 在 reflect_and_plan 时是否自然携带 cognitive_update?
    2. CognitiveState 在 format_context 注入后，是否影响 Agent 行为?
    3. 长 section 被 offload 后，Agent 是否会调用 recall_context?

方法:
    用 gpt-4.1 跑一次真实论文审稿（约 15-20 轮），记录:
    - reflect_and_plan 的调用次数及 cognitive_update 内容
    - recall_context 的调用次数和召回内容
    - CognitiveState 的策略变化轨迹
    
    最后输出结构化报告，判断机制是否被"自然使用"。

使用:
    python3 core/test_e2e_phase32_cognition.py
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from core.agent import ScholarAgent


class Phase32Observer:
    """观察 Phase 32 机制使用情况的探针。"""
    
    def __init__(self):
        self.reflect_calls: list[dict] = []  # 每次 reflect 的参数
        self.recall_calls: list[dict] = []   # 每次 recall 的参数
        self.strategy_trace: list[str] = []  # 策略变化轨迹
        self.offload_count: int = 0          # 触发 offload 的次数
        self.cognitive_updates: list[dict] = []  # 所有 cognitive_update 内容
    
    def report(self) -> str:
        """生成结构化观察报告。"""
        lines = []
        lines.append("=" * 60)
        lines.append("Phase 33: E2E 认知验证报告")
        lines.append("=" * 60)
        
        # 1. reflect_and_plan 使用情况
        lines.append(f"\n[1] reflect_and_plan 调用: {len(self.reflect_calls)} 次")
        has_cognitive_update = sum(1 for r in self.reflect_calls if r.get("has_cognitive_update"))
        lines.append(f"    其中携带 cognitive_update: {has_cognitive_update} 次")
        if has_cognitive_update > 0:
            lines.append(f"    ✓ Agent 自然使用了认知状态更新")
        else:
            lines.append(f"    ✗ Agent 没有使用 cognitive_update 参数")
        
        # 2. cognitive_update 详情
        if self.cognitive_updates:
            lines.append(f"\n[2] 认知更新详情:")
            for i, cu in enumerate(self.cognitive_updates, 1):
                lines.append(f"    Update #{i}:")
                if cu.get("strategy"):
                    lines.append(f"      策略: {cu['strategy']}")
                if cu.get("hypotheses"):
                    lines.append(f"      假说: {json.dumps(cu['hypotheses'], ensure_ascii=False)[:200]}")
                if cu.get("questions"):
                    lines.append(f"      问题: {cu['questions'][:3]}")
                if cu.get("confidence") is not None:
                    lines.append(f"      信心: {cu['confidence']}")
        
        # 3. 策略变化轨迹
        lines.append(f"\n[3] 策略变化轨迹: {self.strategy_trace or ['(无变化)']}")
        if len(set(self.strategy_trace)) > 1:
            lines.append(f"    ✓ 策略发生了切换（{len(set(self.strategy_trace))} 种策略）")
        elif self.strategy_trace:
            lines.append(f"    ~ 策略固定为 {self.strategy_trace[0]}（可能论文简单不需要切换）")
        
        # 4. recall_context 使用
        lines.append(f"\n[4] recall_context 调用: {len(self.recall_calls)} 次")
        if self.recall_calls:
            lines.append(f"    ✓ Agent 使用了上下文回溯")
            for rc in self.recall_calls:
                lines.append(f"      - ref_id={rc.get('ref_id', '?')}, key={rc.get('key', '?')}")
        else:
            lines.append(f"    ~ Agent 未使用 recall（可能论文不够长/不需要回溯）")
        
        # 5. offload 触发
        lines.append(f"\n[5] Offload 触发: {self.offload_count} 次")
        if self.offload_count > 0:
            lines.append(f"    ✓ 长文本被自动卸载到外部存储")
        
        # 6. 总体判断
        lines.append(f"\n{'=' * 60}")
        lines.append("[总体判断]")
        
        score = 0
        max_score = 5
        
        if len(self.reflect_calls) >= 1:
            score += 1
            lines.append(f"  ✓ Agent 使用了 reflect_and_plan (+1)")
        if has_cognitive_update > 0:
            score += 2  # 核心指标，权重高
            lines.append(f"  ✓ cognitive_update 被自然使用 (+2)")
        if len(set(self.strategy_trace)) > 1:
            score += 1
            lines.append(f"  ✓ 策略发生了切换 (+1)")
        if self.offload_count > 0:
            score += 1
            lines.append(f"  ✓ Offload 机制工作 (+1)")
        # recall_context 是 bonus（需要 offload 先触发 + Agent 主动回溯）
        if self.recall_calls:
            score += 1
            lines.append(f"  ★ recall_context 被使用 (+1 bonus)")
            max_score = 6
        
        lines.append(f"\n  得分: {score}/{max_score}")
        if score >= 3:
            lines.append(f"  ★ Phase 32 机制被有效使用")
        elif score >= 1:
            lines.append(f"  ~ Phase 32 机制部分生效，可能需要 identity 中更明确的提示")
        else:
            lines.append(f"  ✗ Phase 32 机制未被使用，需要调整设计")
        
        lines.append("=" * 60)
        return "\n".join(lines)


def patch_harness_for_observation(agent: ScholarAgent, observer: Phase32Observer):
    """
    Monkey-patch agent 的 harness 来观察 Phase 32 机制的使用。
    
    不改变任何行为逻辑，只增加观察探针。
    """
    harness = agent.harness
    
    # 保存原始方法
    original_execute_tool = harness.execute_tool
    original_offload = None
    if hasattr(harness, 'offload_store') and harness.offload_store:
        original_offload = harness.offload_store.offload
    
    def patched_execute_tool(name: str, args: dict) -> str:
        """拦截工具调用，记录 Phase 32 相关的使用情况。"""
        
        if name == "reflect_and_plan":
            has_cu = "cognitive_update" in args and args["cognitive_update"]
            observer.reflect_calls.append({
                "trigger": args.get("trigger", ""),
                "has_cognitive_update": bool(has_cu),
            })
            if has_cu:
                observer.cognitive_updates.append(args["cognitive_update"])
                strategy = args["cognitive_update"].get("strategy")
                if strategy:
                    observer.strategy_trace.append(strategy)
        
        elif name == "recall_context":
            observer.recall_calls.append({
                "ref_id": args.get("ref_id", ""),
                "key": args.get("key", ""),
            })
        
        return original_execute_tool(name, args)
    
    harness.execute_tool = patched_execute_tool
    
    # Patch offload_store.offload 来计数
    if harness.offload_store and original_offload:
        def patched_offload(tool_name: str, key: str, content: str, summary: str, loop_turn: int = 0):
            observer.offload_count += 1
            return original_offload(tool_name, key, content, summary, loop_turn)
        harness.offload_store.offload = patched_offload


async def run_phase33_e2e():
    """运行 Phase 33 E2E 认知验证。"""
    
    paper_path = str(PROJECT_ROOT / "examples" / "sample_paper.md")
    
    print("=" * 60)
    print("Phase 33: E2E 认知验证")
    print(f"模型: gpt-4.1")
    print(f"论文: {paper_path}")
    print(f"目的: 观察 Phase 32 新机制是否被 Agent 自然使用")
    print(f"关注: cognitive_update / recall_context / strategy switching")
    print("=" * 60)
    
    # 初始化 Agent
    agent = ScholarAgent(
        paper_path=paper_path,
        verbose=True,
        max_loop_turns=20,  # 给足空间让 Agent 深入
        token_budget=80000,
    )
    
    # 安装观察探针
    observer = Phase32Observer()
    patch_harness_for_observation(agent, observer)
    
    # 记录初始 CognitiveState
    cs = agent.harness.cognitive_state
    print(f"\n[初始认知状态] strategy={cs.current_strategy}, hypotheses={len(cs.hypotheses)}, questions={len(cs.open_questions)}")
    
    # ---- 第一轮: 给 Agent 一个需要深度审阅的意图 ----
    intent = (
        "请深入审阅这篇论文的方法论和实验部分。"
        "我特别关心：(1) 研究设计是否有致命缺陷 "
        "(2) 数据分析方法是否恰当 "
        "(3) 结论是否被实验结果充分支撑。"
        "如果你发现需要验证的 claim，请搜索相关文献。"
    )
    
    print(f"\n[User] {intent}\n")
    start_time = time.time()
    
    response = await agent.start(user_intent=intent)
    
    elapsed = time.time() - start_time
    
    # ---- 收集结果 ----
    stats = agent.get_stats()
    findings = agent.get_findings()
    
    print(f"\n{'─' * 50}")
    print(f"[Agent Response]:")
    print(response[:500] if response else "(无文本回复)")
    print(f"{'─' * 50}")
    
    print(f"\n[执行统计]")
    print(f"  耗时: {elapsed:.1f}s")
    print(f"  Loop turns: {stats.get('loop_turns', '?')}")
    print(f"  Total tokens: {stats.get('total_tokens', '?')}")
    print(f"  Findings: {len(findings)}")
    
    # 最终 CognitiveState
    cs_final = agent.harness.cognitive_state
    print(f"\n[最终认知状态]")
    print(f"  strategy: {cs_final.current_strategy}")
    print(f"  hypotheses: {len(cs_final.hypotheses)}")
    print(f"  open_questions: {len(cs_final.open_questions)}")
    if cs_final.hypotheses:
        for h in cs_final.hypotheses[:3]:
            print(f"    - [{h.confidence:.1f}] {h.claim[:80]}")
    
    # Findings 摘要
    if findings:
        print(f"\n[Findings]:")
        for i, f in enumerate(findings[:5], 1):
            print(f"  {i}. [{f.get('priority','?')}] {f.get('finding','')[:100]}")
    
    # ---- 输出观察报告 ----
    report = observer.report()
    print(f"\n{report}")
    
    # 保存报告到文件
    report_path = PROJECT_ROOT / "core" / "e2e_report_phase33.json"
    report_data = {
        "phase": 33,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": "gpt-4.1",
        "elapsed_seconds": round(elapsed, 1),
        "loop_turns": stats.get("loop_turns"),
        "total_tokens": stats.get("total_tokens"),
        "findings_count": len(findings),
        "observer": {
            "reflect_calls": len(observer.reflect_calls),
            "cognitive_updates": len(observer.cognitive_updates),
            "recall_calls": len(observer.recall_calls),
            "offload_count": observer.offload_count,
            "strategy_trace": observer.strategy_trace,
        },
        "cognitive_state_final": {
            "strategy": cs_final.current_strategy,
            "hypotheses_count": len(cs_final.hypotheses),
            "open_questions_count": len(cs_final.open_questions),
        },
        "verdict": "effective" if sum([
            len(observer.reflect_calls) >= 1,
            any(r.get("has_cognitive_update") for r in observer.reflect_calls),
            observer.offload_count > 0,
        ]) >= 2 else "partial" if observer.reflect_calls else "unused",
    }
    
    with open(report_path, "w") as f:
        json.dump(report_data, f, indent=2, ensure_ascii=False)
    print(f"\n[报告已保存] {report_path}")
    
    return report_data


if __name__ == "__main__":
    result = asyncio.run(run_phase33_e2e())
    
    # Exit code 反映验证结果
    if result["verdict"] == "effective":
        print("\n✓ Phase 32 机制被有效使用")
        sys.exit(0)
    elif result["verdict"] == "partial":
        print("\n~ Phase 32 机制部分生效")
        sys.exit(0)
    else:
        print("\n✗ Phase 32 机制未被使用")
        sys.exit(1)
