"""
evaluation/run_adversarial_training.py — Phase 7 对抗训练集成入口

功能:
    1. 实例化 ArenaOrchestrator（含 RedTeam + BlueTeam + MatchJudge）
    2. 运行 N 局对抗赛或一个完整赛季
    3. 输出 ELO 变化、胜负统计、学习洞察
    4. 可选：接入真实 AgentExecutor（BlueTeamExecutor 协议）

使用方式:
    # 快速验证（模拟模式，无需 LLM）
    python -m evaluation.run_adversarial_training --mode quick --matches 5

    # 完整赛季（模拟模式）
    python -m evaluation.run_adversarial_training --mode season

    # 接入真实 Agent（需要 LLM 配置）
    python -m evaluation.run_adversarial_training --mode real --matches 3

Kill Switch:
    SCHOLAR_GODEL_ADVERSARIAL_TRAINING=0  → 全部 no-op
    SCHOLAR_GODEL_ADVERSARIAL_RED=0       → 红队不出题
    SCHOLAR_GODEL_ADVERSARIAL_BLUE=0      → 蓝队不防御
    SCHOLAR_GODEL_ADVERSARIAL_ELO=0       → 不更新 ELO
    SCHOLAR_GODEL_ADVERSARIAL_SEASON=0    → 不做赛季轮转
"""

from __future__ import annotations

import os
import sys
import asyncio
import argparse
import logging
import json
import time
from pathlib import Path
from typing import Optional

# Ensure project root is on path
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Ensure kill switches are ON by default for training runs
os.environ.setdefault("SCHOLAR_GODEL_ADVERSARIAL_TRAINING", "1")
os.environ.setdefault("SCHOLAR_GODEL_ADVERSARIAL_RED", "1")
os.environ.setdefault("SCHOLAR_GODEL_ADVERSARIAL_BLUE", "1")
os.environ.setdefault("SCHOLAR_GODEL_ADVERSARIAL_ELO", "1")
os.environ.setdefault("SCHOLAR_GODEL_ADVERSARIAL_SEASON", "1")

from core.godel_config import (
    GODEL_ADVERSARIAL_TRAINING_ENABLED,
    GODEL_ADVERSARIAL_RED_TEAM_ENABLED,
    GODEL_ADVERSARIAL_BLUE_TEAM_ENABLED,
    GODEL_ADVERSARIAL_ELO_ENABLED,
    GODEL_ADVERSARIAL_SEASON_ENABLED,
)
from core.event_bus import EventBus, EventType
from training.red_blue_arena import (
    RedTeam,
    BlueTeam,
    ArenaOrchestrator,
    ArenaMatch,
    EloRating,
    MatchOutcome,
    RedStrategy,
    BlueStrategy,
    MatchJudge,
    SeasonConfig,
    SeasonSummary,
)
from training.adversarial import (
    AdversarialGenerator,
    AdversarialCase,
    ChallengeType,
    DifficultyLevel,
)
from training.weakness_analyzer import WeaknessDimension

logger = logging.getLogger(__name__)


# ==============================================================
# 事件监听器（可选，用于实时输出）
# ==============================================================

class TrainingEventListener:
    """监听竞技场事件并打印实时进度。"""

    def __init__(self, verbose: bool = True):
        self._verbose = verbose
        self._match_count = 0

    def on_match_completed(self, match: ArenaMatch) -> None:
        """对局完成回调。"""
        self._match_count += 1
        if self._verbose:
            outcome_emoji = {
                MatchOutcome.RED_WIN: "🔴",
                MatchOutcome.BLUE_WIN: "🔵",
                MatchOutcome.DRAW: "⚪",
                MatchOutcome.INVALID: "⚠️",
                MatchOutcome.ERROR: "❌",
            }
            emoji = outcome_emoji.get(match.outcome, "?")
            # INVALID/ERROR 对局的 dimension 是默认值，标注 N/A 避免误导
            dim_display = (
                "N/A"
                if match.outcome in (MatchOutcome.INVALID, MatchOutcome.ERROR)
                else match.red_dimension_target.value
            )
            print(
                f"  [{self._match_count:3d}] {emoji} {match.outcome.value:10s} | "
                f"Red ELO: {match.red_elo_after:7.1f} (Δ{match.red_elo_delta:+.1f}) | "
                f"Blue ELO: {match.blue_elo_after:7.1f} (Δ{match.blue_elo_delta:+.1f}) | "
                f"Dim: {dim_display}"
            )

    def on_season_completed(self, summary: SeasonSummary) -> None:
        """赛季完成回调。"""
        if self._verbose:
            print(f"\n{'='*60}")
            print(f"  赛季 {summary.season_number} 结束")
            print(f"  总对局: {summary.total_matches} | "
                  f"红胜: {summary.red_wins} | 蓝胜: {summary.blue_wins} | "
                  f"平局: {summary.draws} | 无效: {summary.invalid_matches}")
            print(f"  Red ELO Δ: {summary.red_elo_end - summary.red_elo_start:+.1f} | "
                  f"Blue ELO Δ: {summary.blue_elo_end - summary.blue_elo_start:+.1f}")
            print(f"{'='*60}\n")


# ==============================================================
# 主运行逻辑
# ==============================================================

async def run_quick_matches(
    matches: int = 5,
    verbose: bool = True,
    seed: Optional[int] = None,
) -> dict:
    """快速运行 N 局对抗（模拟模式，无需 LLM）。

    Args:
        matches: 对局数
        verbose: 是否打印实时进度
        seed: 随机种子（仅控制 Python stdlib random 模块，
              不影响 numpy 等第三方 RNG）

    Returns:
        运行结果摘要
    """
    if not GODEL_ADVERSARIAL_TRAINING_ENABLED:
        print("[SKIP] SCHOLAR_GODEL_ADVERSARIAL_TRAINING is OFF")
        return {"status": "skipped", "reason": "kill_switch_off"}

    if seed is not None:
        import random
        random.seed(seed)

    # 创建 EventBus
    event_bus = EventBus()

    # 创建组件（模拟模式：无 LLM，无真实 executor）
    generator = AdversarialGenerator()  # 无 LLM → 使用模板生成
    red_team = RedTeam(generator=generator)
    blue_team = BlueTeam()  # 无 executor → 使用 _simulate_review
    judge = MatchJudge()
    config = SeasonConfig(matches_per_season=matches)

    # 创建编排器
    orchestrator = ArenaOrchestrator(
        red_team=red_team,
        blue_team=blue_team,
        judge=judge,
        config=config,
        event_bus=event_bus,
    )

    # 注册回调
    listener = TrainingEventListener(verbose=verbose)
    orchestrator.set_on_match_complete(listener.on_match_completed)
    orchestrator.set_on_season_complete(listener.on_season_completed)

    if verbose:
        print(f"\n{'='*60}")
        print(f"  Phase 7 对抗训练 — 快速模式 ({matches} 局)")
        print(f"  Kill Switches: Training={GODEL_ADVERSARIAL_TRAINING_ENABLED} "
              f"Red={GODEL_ADVERSARIAL_RED_TEAM_ENABLED} "
              f"Blue={GODEL_ADVERSARIAL_BLUE_TEAM_ENABLED} "
              f"ELO={GODEL_ADVERSARIAL_ELO_ENABLED}")
        print(f"{'='*60}\n")

    # 运行对局
    results: list[ArenaMatch] = []
    start_time = time.time()

    for i in range(matches):
        match = await orchestrator.run_match()
        results.append(match)

    elapsed = time.time() - start_time

    # 汇总
    stats = orchestrator.get_arena_stats()
    insights = orchestrator.get_learning_insights()

    summary = {
        "status": "completed",
        "mode": "quick",
        "total_matches": len(results),
        "elapsed_seconds": round(elapsed, 2),
        "outcomes": {
            "red_wins": sum(1 for m in results if m.outcome == MatchOutcome.RED_WIN),
            "blue_wins": sum(1 for m in results if m.outcome == MatchOutcome.BLUE_WIN),
            "draws": sum(1 for m in results if m.outcome == MatchOutcome.DRAW),
            "invalid": sum(1 for m in results if m.outcome == MatchOutcome.INVALID),
            "errors": sum(1 for m in results if m.outcome == MatchOutcome.ERROR),
        },
        "elo": {
            "red_final": round(red_team.elo.rating, 1),
            "blue_final": round(blue_team.elo.rating, 1),
            "gap": round(stats["elo_gap"], 1),
            "is_balanced": stats["is_balanced"],
        },
        "insights": insights,
        "arena_stats": stats,
    }

    if verbose:
        print(f"\n{'='*60}")
        print(f"  运行完成 ({elapsed:.1f}s)")
        print(f"  Red ELO: {summary['elo']['red_final']} | "
              f"Blue ELO: {summary['elo']['blue_final']} | "
              f"Gap: {summary['elo']['gap']}")
        print(f"  胜负: Red {summary['outcomes']['red_wins']} / "
              f"Blue {summary['outcomes']['blue_wins']} / "
              f"Draw {summary['outcomes']['draws']}")
        if insights.get("recommendations"):
            print(f"  建议: {insights['recommendations'][0]}")
        print(f"{'='*60}\n")

    return summary


async def run_season(
    matches_per_season: Optional[int] = None,
    verbose: bool = True,
) -> dict:
    """运行一个完整赛季。

    Args:
        matches_per_season: 每赛季对局数（None = 使用默认 50）
        verbose: 是否打印实时进度

    Returns:
        赛季结果摘要
    """
    if not GODEL_ADVERSARIAL_TRAINING_ENABLED:
        print("[SKIP] SCHOLAR_GODEL_ADVERSARIAL_TRAINING is OFF")
        return {"status": "skipped", "reason": "kill_switch_off"}

    # 创建 EventBus
    event_bus = EventBus()

    # 创建组件
    generator = AdversarialGenerator()
    red_team = RedTeam(generator=generator)
    blue_team = BlueTeam()
    judge = MatchJudge()
    config = SeasonConfig(
        matches_per_season=matches_per_season or 50,
        elo_reset_on_season_end=True,
    )

    orchestrator = ArenaOrchestrator(
        red_team=red_team,
        blue_team=blue_team,
        judge=judge,
        config=config,
        event_bus=event_bus,
    )

    # 注册回调
    listener = TrainingEventListener(verbose=verbose)
    orchestrator.set_on_match_complete(listener.on_match_completed)
    orchestrator.set_on_season_complete(listener.on_season_completed)

    if verbose:
        print(f"\n{'='*60}")
        print(f"  Phase 7 对抗训练 — 赛季模式 ({config.matches_per_season} 局/赛季)")
        print(f"{'='*60}\n")

    start_time = time.time()
    season_summary = await orchestrator.run_season()
    elapsed = time.time() - start_time

    result = {
        "status": "completed",
        "mode": "season",
        "season_number": season_summary.season_number,
        "total_matches": season_summary.total_matches,
        "elapsed_seconds": round(elapsed, 2),
        "outcomes": {
            "red_wins": season_summary.red_wins,
            "blue_wins": season_summary.blue_wins,
            "draws": season_summary.draws,
            "invalid": season_summary.invalid_matches,
        },
        "elo": {
            "red_start": round(season_summary.red_elo_start, 1),
            "red_end": round(season_summary.red_elo_end, 1),
            "blue_start": round(season_summary.blue_elo_start, 1),
            "blue_end": round(season_summary.blue_elo_end, 1),
        },
        "dimension_coverage": season_summary.dimension_coverage,
        "difficulty_distribution": season_summary.difficulty_distribution,
        "avg_challenge_quality": round(season_summary.avg_challenge_quality, 3),
        "insights": orchestrator.get_learning_insights(),
    }

    if verbose:
        print(f"\n  赛季完成 ({elapsed:.1f}s)")
        print(f"  ELO: Red {result['elo']['red_start']} → {result['elo']['red_end']} | "
              f"Blue {result['elo']['blue_start']} → {result['elo']['blue_end']}")
        if config.elo_reset_on_season_end:
            print(f"  注: ELO 已执行赛季回归 (regression_factor={EloRating.SEASON_REGRESSION_FACTOR})")

    return result


async def run_serialization_roundtrip(matches: int = 3, verbose: bool = True) -> dict:
    """验证 ArenaOrchestrator 的序列化/反序列化完整性。

    运行几局 → 序列化 → 反序列化 → 继续运行 → 验证状态一致。
    """
    if not GODEL_ADVERSARIAL_TRAINING_ENABLED:
        return {"status": "skipped", "reason": "kill_switch_off"}

    # Phase 1: 运行几局
    generator = AdversarialGenerator()
    red_team = RedTeam(generator=generator)
    blue_team = BlueTeam()
    orchestrator = ArenaOrchestrator(red_team=red_team, blue_team=blue_team)

    for _ in range(matches):
        await orchestrator.run_match()

    # 记录状态
    pre_serialize_stats = orchestrator.get_arena_stats()
    pre_red_elo = red_team.elo.rating
    pre_blue_elo = blue_team.elo.rating

    # Phase 2: 序列化
    serialized = orchestrator.serialize()

    # Phase 3: 反序列化
    restored = ArenaOrchestrator.from_dict(serialized)

    # Phase 4: 验证
    post_stats = restored.get_arena_stats()

    checks = {
        "total_matches_preserved": post_stats["total_matches"] == pre_serialize_stats["total_matches"],
        "season_preserved": post_stats["current_season"] == pre_serialize_stats["current_season"],
        "red_elo_preserved": abs(restored.red_team.elo.rating - pre_red_elo) < 0.01,
        "blue_elo_preserved": abs(restored.blue_team.elo.rating - pre_blue_elo) < 0.01,
    }

    # Phase 5: 继续运行（验证恢复后可继续）
    post_match = await restored.run_match()
    checks["can_continue_after_restore"] = post_match.outcome in (
        MatchOutcome.RED_WIN, MatchOutcome.BLUE_WIN, MatchOutcome.DRAW,
        MatchOutcome.INVALID, MatchOutcome.ERROR,
    )

    all_passed = all(checks.values())

    if verbose:
        status = "✅ PASS" if all_passed else "❌ FAIL"
        print(f"\n  序列化往返测试: {status}")
        for check, passed in checks.items():
            print(f"    {'✓' if passed else '✗'} {check}")

    return {
        "status": "passed" if all_passed else "failed",
        "checks": checks,
        "pre_stats": pre_serialize_stats,
        "post_stats": post_stats,
    }


# ==============================================================
# CLI 入口
# ==============================================================

def main() -> Optional[dict]:
    """CLI 入口。

    Returns:
        运行结果字典（供脚本调用时使用）。作为 CLI 入口时返回值被忽略。
    """
    parser = argparse.ArgumentParser(
        description="Phase 7 对抗训练集成入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 快速验证 5 局
  python -m evaluation.run_adversarial_training --mode quick --matches 5

  # 完整赛季
  python -m evaluation.run_adversarial_training --mode season --matches 20

  # 序列化往返测试
  python -m evaluation.run_adversarial_training --mode roundtrip

  # 静默模式（仅输出 JSON）
  python -m evaluation.run_adversarial_training --mode quick --matches 10 --quiet
        """,
    )
    parser.add_argument(
        "--mode",
        choices=["quick", "season", "roundtrip"],
        default="quick",
        help="运行模式: quick=快速N局, season=完整赛季, roundtrip=序列化验证",
    )
    parser.add_argument(
        "--matches",
        type=int,
        default=5,
        help="对局数（quick/roundtrip 模式）或每赛季对局数（season 模式）",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="随机种子（用于可复现测试）",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="静默模式，仅输出 JSON 结果",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="输出结果到 JSON 文件",
    )

    args = parser.parse_args()
    verbose = not args.quiet

    # 配置日志
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # 运行
    if args.mode == "quick":
        result = asyncio.run(run_quick_matches(
            matches=args.matches,
            verbose=verbose,
            seed=args.seed,
        ))
    elif args.mode == "season":
        result = asyncio.run(run_season(
            matches_per_season=args.matches,
            verbose=verbose,
        ))
    elif args.mode == "roundtrip":
        result = asyncio.run(run_serialization_roundtrip(
            matches=args.matches,
            verbose=verbose,
        ))
    else:
        parser.error(f"Unknown mode: {args.mode}")
        return

    # 输出
    if args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        if verbose:
            print(f"\n  结果已保存到: {output_path}")

    return result


if __name__ == "__main__":
    main()
