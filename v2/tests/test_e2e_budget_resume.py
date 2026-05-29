"""
端到端验收测试：Token Budget 截断 + 断点续传

验收场景:
  B: Budget 截断（budget=15000, 长论文 → LoopDoomStop + checkpoint）
  C: Resume 续传（从 B 的 checkpoint 恢复, new_token_limit=30000）
  E: 无限制模式向后兼容（默认 token_limit=0 → 不截断, max_loop_turns=3 限制）

注意:
  - 场景 B 使用较低 budget (15000) 确保快速触发截断
  - 场景 C 从 B 的 checkpoint 续传
  - 场景 E 验证不设 budget 时行为完全正常
  - 所有场景使用真实 LLM 调用

执行方式:
  cd /Users/yanfeiyu03/Downloads/scholar-agent-public
  python3 v2/tests/test_e2e_budget_resume.py
"""

import asyncio
import sys
import os
import shutil
import tempfile
from pathlib import Path

# 路径设置
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
V2_ROOT = PROJECT_ROOT / "v2"
sys.path.insert(0, str(V2_ROOT))

from dotenv import load_dotenv
load_dotenv(V2_ROOT / ".env")

from core.agent import ScholarAgent
from core.budget_policy import BudgetPolicy


# ==========================================================
# 测试配置
# ==========================================================
PAPER_PATH = str(V2_ROOT / "evaluation" / "test_papers" / "paper_001.pdf")
# 使用一个临时目录来存储 checkpoint，避免污染项目
TEMP_DIR = Path(tempfile.mkdtemp(prefix="scholar_e2e_"))

# 明确的 user_intent，让 Agent 不要询问而是直接开始深度审阅
DIRECT_REVIEW_INTENT = (
    "请直接开始完整审阅全文，无需询问我任何问题。"
    "从摘要到结论逐段阅读并记录你发现的所有问题。"
    "不要使用 talk_to_user 等待我的回复，直接自主工作。"
)


def separator(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


# ==========================================================
# 场景 B: Budget 截断
# ==========================================================
async def scenario_b() -> str:
    """Budget 截断：设置极低的 budget，验证 Agent 被硬截断并自动保存 checkpoint。"""
    separator("场景 B: Budget 截断 (token_limit=15000)")

    # 准备 checkpoint 目录（覆盖 Agent 内部的默认路径）
    ckpt_dir = TEMP_DIR / "scenario_b_checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    # 构造 Agent，使用极低 budget 确保快速截断
    # 注意：15K 可能不够触发（Agent 可能在一轮内就 talk_to_user 退出）
    # 所以我们用明确的 user_intent 防止 Agent 提问退出
    agent = ScholarAgent(
        paper_path=PAPER_PATH,
        model="gpt-4.1-mini",  # 使用更快的模型加速测试
        verbose=True,
        max_loop_turns=30,
        budget_policy=BudgetPolicy(token_limit=15000, allow_pause=True),
    )

    # 覆盖 paper_path 使 checkpoint 存储到临时目录
    agent.paper_path = str(ckpt_dir / "paper_001.pdf")
    # 复制论文到 checkpoint 目录（使 _save_budget_checkpoint 正确定位）
    shutil.copy2(PAPER_PATH, agent.paper_path)

    print(f"[B] Paper: {PAPER_PATH}")
    print(f"[B] Budget: 15,000 tokens")
    print(f"[B] Checkpoint dir: {ckpt_dir}")
    print(f"[B] Intent: DIRECT_REVIEW_INTENT (防止 Agent talk_to_user 退出)")
    print(f"[B] Starting agent...\n")

    result = await agent.start(user_intent=DIRECT_REVIEW_INTENT)

    print(f"\n[B] === Agent 输出 ===")
    print(result[:500] + ("..." if len(result) > 500 else ""))
    print(f"\n[B] === 统计 ===")
    print(f"  total_tokens: {agent.harness.state.total_tokens:,}")
    print(f"  loop_turns: {agent.harness.state.loop_turns}")
    print(f"  findings: {len(agent.harness.state.findings)}")

    # 验证
    checks = {
        "触发截断 (含'系统中断')": "[系统中断]" in result,
        "包含 budget 原因": "budget" in result.lower() or "Budget" in result,
        "包含消耗统计": "[消耗统计]" in result,
    }

    # 检查 checkpoint 是否已生成
    ckpt_search_dir = Path(agent.paper_path).parent / ".scholar_checkpoints"
    ckpt_files = list(ckpt_search_dir.glob("snap_*.json.gz")) if ckpt_search_dir.exists() else []
    checks["checkpoint 已保存"] = len(ckpt_files) > 0

    print(f"\n[B] === 验证 ===")
    all_pass = True
    for desc, passed in checks.items():
        status = "✅" if passed else "❌"
        print(f"  {status} {desc}")
        if not passed:
            all_pass = False

    if ckpt_files:
        print(f"\n[B] Checkpoint 文件: {ckpt_files[0].name}")
        print(f"    大小: {ckpt_files[0].stat().st_size:,} bytes")
        return str(ckpt_search_dir)
    else:
        print(f"\n[B] ⚠️ 未找到 checkpoint 文件")
        # 也在临时目录直接搜索
        all_gz = list(ckpt_dir.rglob("snap_*.json.gz"))
        if all_gz:
            print(f"    发现在: {all_gz[0]}")
            return str(all_gz[0].parent)
        return ""


# ==========================================================
# 场景 C: Resume 续传
# ==========================================================
async def scenario_c(checkpoint_dir: str) -> None:
    """Resume 续传：从场景 B 的 checkpoint 恢复，追加预算继续运行。"""
    separator("场景 C: Resume 续传 (new_token_limit=30000)")

    if not checkpoint_dir:
        print("[C] ⚠️ 跳过：没有可用的 checkpoint（场景 B 未生成）")
        return

    print(f"[C] Checkpoint dir: {checkpoint_dir}")
    print(f"[C] New token limit: 30,000")
    print(f"[C] Starting resume...\n")

    result = await ScholarAgent.resume(
        checkpoint_path=checkpoint_dir,
        new_token_limit=30000,
        model="gpt-4.1-mini",
        verbose=True,
    )

    print(f"\n[C] === Agent 输出 ===")
    print(result[:500] + ("..." if len(result) > 500 else ""))

    # 验证
    checks = {
        "有实际输出": len(result) > 50,
        "包含消耗统计或系统中断": "[消耗统计]" in result or "[系统中断]" in result,
    }

    # 如果再次被截断（30K 也不够用），也是合法结果
    if "[系统中断]" in result:
        checks["再次截断时包含 budget 原因"] = "budget" in result.lower() or "Budget" in result
        print(f"\n[C] 注意: Agent 再次被 budget 截断（30K 也不够完成全部工作）")
    else:
        print(f"\n[C] Agent 正常完成或达到 loop turn 限制")

    print(f"\n[C] === 验证 ===")
    for desc, passed in checks.items():
        status = "✅" if passed else "❌"
        print(f"  {status} {desc}")


# ==========================================================
# 场景 E: 无限制模式向后兼容
# ==========================================================
async def scenario_e() -> None:
    """无限制模式：不设 budget，只用 max_loop_turns 限制。验证行为与旧版完全一致。"""
    separator("场景 E: 无限制模式向后兼容 (token_limit=0, max_loop_turns=3)")

    # 不传 budget_policy → 默认 token_limit=0 → is_unlimited=True
    # 用 max_loop_turns=3 来限制运行时间（注意：hard limit = 3+2 = 5 轮）
    agent = ScholarAgent(
        paper_path=PAPER_PATH,
        model="gpt-4.1-mini",
        verbose=True,
        max_loop_turns=3,
        budget_policy=BudgetPolicy(token_limit=0),  # 无限制
    )

    print(f"[E] Paper: {PAPER_PATH}")
    print(f"[E] Budget: 无限制 (token_limit=0)")
    print(f"[E] max_loop_turns: 3 (hard_limit=5)")
    print(f"[E] Starting agent...\n")

    result = await agent.start(user_intent=DIRECT_REVIEW_INTENT)

    print(f"\n[E] === Agent 输出 ===")
    print(result[:500] + ("..." if len(result) > 500 else ""))
    print(f"\n[E] === 统计 ===")
    print(f"  total_tokens: {agent.harness.state.total_tokens:,}")
    print(f"  loop_turns: {agent.harness.state.loop_turns}")
    print(f"  findings: {len(agent.harness.state.findings)}")
    print(f"  is_budget_exceeded: {agent.harness.is_budget_exceeded()}")

    # 验证
    checks = {
        "有实际输出": len(result) > 50,
        "budget 未触发截断": "budget" not in result.lower() or "无上限" in result,
        "is_budget_exceeded = False": not agent.harness.is_budget_exceeded(),
        "正常完成 (LoopDone 或 DoomStop 因 turn)": "[系统中断]" not in result or "budget" not in result.lower(),
    }

    # 检查 checkpoint 目录是否为空（不应该生成 budget checkpoint）
    ckpt_search_dir = Path(PAPER_PATH).parent / ".scholar_checkpoints"
    budget_snaps = list(ckpt_search_dir.glob("snap_*.json.gz")) if ckpt_search_dir.exists() else []
    checks["无 budget checkpoint 生成"] = len(budget_snaps) == 0

    print(f"\n[E] === 验证 ===")
    for desc, passed in checks.items():
        status = "✅" if passed else "❌"
        print(f"  {status} {desc}")


# ==========================================================
# Main
# ==========================================================
async def main():
    print(f"ScholarAgent 端到端验收测试: Budget + Resume")
    print(f"临时目录: {TEMP_DIR}")
    print(f"论文: {PAPER_PATH}")

    try:
        # 场景 B: Budget 截断
        checkpoint_dir = await scenario_b()

        # 场景 C: Resume 续传
        await scenario_c(checkpoint_dir)

        # 场景 E: 无限制模式
        await scenario_e()

    finally:
        separator("清理")
        print(f"临时目录保留供检查: {TEMP_DIR}")
        # 不自动清理，允许用户检查结果
        # shutil.rmtree(TEMP_DIR, ignore_errors=True)

    separator("测试完成")


if __name__ == "__main__":
    asyncio.run(main())
