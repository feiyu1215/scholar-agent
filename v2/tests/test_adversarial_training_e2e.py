"""
tests/test_adversarial_training_e2e.py — Phase 7 对抗训练端到端集成测试

验证目标:
    1. ArenaOrchestrator 完整 run_match 流程（Red→Blue→Judge→ELO）
    2. ELO 评分在对局后确实发生变化
    3. Kill Switch 正确控制各组件
    4. 序列化/反序列化往返一致性
    5. 赛季管理（run_season）
    6. EventBus 事件正确发布
    7. 模拟模式下的 BlueTeam._simulate_review 可用性
    8. run_adversarial_training.py 集成入口可调用

Target: 验证 Phase 7 从 training/ 模块到 evaluation/ 入口的完整链路。
"""

import os
import sys
import unittest
import asyncio
import random
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure kill switches are ON
os.environ["SCHOLAR_GODEL_ADVERSARIAL_TRAINING"] = "1"
os.environ["SCHOLAR_GODEL_ADVERSARIAL_RED"] = "1"
os.environ["SCHOLAR_GODEL_ADVERSARIAL_BLUE"] = "1"
os.environ["SCHOLAR_GODEL_ADVERSARIAL_ELO"] = "1"
os.environ["SCHOLAR_GODEL_ADVERSARIAL_SEASON"] = "1"

# Ensure project root on path
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

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


def _run_async(coro):
    """Helper to run async tests.

    使用 asyncio.run() 而非 get_event_loop().run_until_complete()，
    以兼容 Python 3.10+（后者在无 running loop 时会 DeprecationWarning，
    3.12+ 会直接 RuntimeError）。
    """
    return asyncio.run(coro)


class TestEloRatingBasic(unittest.TestCase):
    """ELO 评分系统基础验证。"""

    def test_initial_rating(self):
        elo = EloRating()
        self.assertEqual(elo.rating, 1500.0)
        self.assertEqual(elo.match_count, 0)
        self.assertTrue(elo.is_provisional)

    def test_update_on_win(self):
        elo = EloRating()
        old_rating = elo.rating
        elo.update(actual_score=1.0, opponent_rating=1500.0)
        # 胜利应该提升 ELO
        self.assertGreater(elo.rating, old_rating)
        self.assertEqual(elo.match_count, 1)

    def test_update_on_loss(self):
        elo = EloRating()
        old_rating = elo.rating
        elo.update(actual_score=0.0, opponent_rating=1500.0)
        # 失败应该降低 ELO
        self.assertLess(elo.rating, old_rating)

    def test_expected_score_equal_ratings(self):
        elo = EloRating()
        # 相同 ELO 的期望胜率应为 0.5
        expected = elo.expected_score(1500.0)
        self.assertAlmostEqual(expected, 0.5, places=5)

    def test_expected_score_higher_opponent(self):
        elo = EloRating(initial_rating=1400.0)
        # 对手更强时期望胜率 < 0.5
        expected = elo.expected_score(1600.0)
        self.assertLess(expected, 0.5)

    def test_serialization_roundtrip(self):
        elo = EloRating(initial_rating=1600.0, match_count=10)
        elo.update(actual_score=1.0, opponent_rating=1500.0)
        data = elo.to_dict()
        restored = EloRating.from_dict(data)
        self.assertAlmostEqual(restored.rating, elo.rating, places=2)
        self.assertEqual(restored.match_count, elo.match_count)


class TestRedTeamGeneration(unittest.TestCase):
    """红队对抗样本生成验证。"""

    def test_strategy_selection_no_profile(self):
        """无弱点画像时红队仍能选择策略。"""
        red = RedTeam()
        strategy = red.select_strategy()
        self.assertIsInstance(strategy, RedStrategy)

    def test_generate_challenge_returns_case(self):
        """红队生成挑战返回 AdversarialCase。"""
        red = RedTeam()

        async def _test():
            case, desc = await red.generate_challenge(
                strategy=RedStrategy.EXPLOIT_WEAKNESS,
            )
            return case, desc

        case, desc = _run_async(_test())
        self.assertIsInstance(case, AdversarialCase)
        self.assertIsInstance(desc, str)


class TestBlueTeamDefense(unittest.TestCase):
    """蓝队防御验证。"""

    def test_simulate_review_returns_findings(self):
        """模拟审稿返回 findings 和 score。"""
        blue = BlueTeam()
        challenge = AdversarialCase(
            paper_snippet="This paper uses DID to estimate treatment effects...",
            gold_findings=[
                {"category": "methodology", "description": "Parallel trends not verified"},
            ],
            target_dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
            difficulty=DifficultyLevel.MEDIUM,
        )
        findings, score = blue.execute_defense(challenge=challenge)
        self.assertIsInstance(findings, list)
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0.0)

    def test_strategy_selection(self):
        """蓝队能根据挑战选择策略。"""
        blue = BlueTeam()
        challenge = AdversarialCase(
            target_dimension=WeaknessDimension.STATISTICAL_REASONING,
            difficulty=DifficultyLevel.HARD,
        )
        strategy = blue.select_strategy(challenge)
        self.assertIsInstance(strategy, BlueStrategy)


class TestMatchJudge(unittest.TestCase):
    """评判器验证。"""

    def test_blue_win_on_full_detection(self):
        """蓝队检测到所有 gold findings → BLUE_WIN。

        MatchJudge._match_findings 使用关键词重叠度 >= 20% 判定匹配，
        因此 blue_findings 的 description 必须包含 gold 中的关键词。
        """
        judge = MatchJudge()
        challenge = AdversarialCase(
            gold_findings=[
                {"category": "methodology", "description": "parallel trends assumption not verified"},
                {"category": "statistics", "description": "standard errors not clustered at treatment level"},
            ],
        )
        # 蓝队返回的 findings 使用与 gold 相同的核心关键词，确保匹配
        blue_findings = [
            {"category": "methodology", "description": "parallel trends assumption is not verified in pre-period"},
            {"category": "statistics", "description": "standard errors should be clustered at treatment level"},
        ]
        outcome, blue_score, red_score, details = judge.judge_match(
            challenge=challenge,
            blue_findings=blue_findings,
            blue_score=0.9,
            challenge_quality=1.0,
        )
        # 两个 gold findings 都应被匹配 → match_ratio = 1.0 >= 0.8 → BLUE_WIN
        self.assertEqual(outcome, MatchOutcome.BLUE_WIN)
        self.assertEqual(blue_score, 1.0)
        self.assertEqual(red_score, 0.0)

    def test_invalid_on_low_quality(self):
        """红队样本质量过低 → INVALID。"""
        judge = MatchJudge()
        challenge = AdversarialCase()
        outcome, _, _, _ = judge.judge_match(
            challenge=challenge,
            blue_findings=[],
            blue_score=0.0,
            challenge_quality=0.1,  # 低于 MIN_CHALLENGE_QUALITY
        )
        self.assertEqual(outcome, MatchOutcome.INVALID)


class TestArenaOrchestratorE2E(unittest.TestCase):
    """ArenaOrchestrator 端到端集成测试。"""

    def test_single_match_completes(self):
        """单局对抗能正常完成。"""
        random.seed(42)
        orchestrator = ArenaOrchestrator()

        async def _test():
            match = await orchestrator.run_match()
            return match

        match = _run_async(_test())
        self.assertIsInstance(match, ArenaMatch)
        self.assertIn(match.outcome, list(MatchOutcome))
        self.assertEqual(orchestrator.total_matches, 1)

    def test_elo_changes_after_valid_match(self):
        """有效对局后 ELO 发生变化。"""
        random.seed(123)
        red = RedTeam()
        blue = BlueTeam()
        orchestrator = ArenaOrchestrator(red_team=red, blue_team=blue)

        initial_red_elo = red.elo.rating
        initial_blue_elo = blue.elo.rating

        async def _test():
            # 运行多局以确保至少有一局有效
            for _ in range(5):
                await orchestrator.run_match()

        _run_async(_test())

        # 至少有一方的 ELO 应该变化了（除非全部 INVALID）
        valid_matches = [
            m for m in orchestrator.match_history
            if m.outcome not in (MatchOutcome.INVALID, MatchOutcome.ERROR)
        ]
        if valid_matches:
            elo_changed = (
                abs(red.elo.rating - initial_red_elo) > 0.01 or
                abs(blue.elo.rating - initial_blue_elo) > 0.01
            )
            self.assertTrue(elo_changed, "ELO should change after valid matches")

    def test_multiple_matches_accumulate(self):
        """多局对抗正确累积统计。"""
        random.seed(7)
        orchestrator = ArenaOrchestrator()

        async def _test():
            for _ in range(10):
                await orchestrator.run_match()

        _run_async(_test())

        self.assertEqual(orchestrator.total_matches, 10)
        self.assertEqual(len(orchestrator.match_history), 10)

        stats = orchestrator.get_arena_stats()
        self.assertEqual(stats["total_matches"], 10)

    def test_event_bus_receives_events(self):
        """EventBus 正确接收对局事件。"""
        event_bus = EventBus()
        received_events = []

        def listener(event):
            received_events.append(event)

        event_bus.subscribe(EventType.ARENA_MATCH_STARTED, listener)
        event_bus.subscribe(EventType.ARENA_MATCH_COMPLETED, listener)

        orchestrator = ArenaOrchestrator(event_bus=event_bus)

        async def _test():
            await orchestrator.run_match()

        _run_async(_test())

        # 应该收到 MATCH_STARTED 和 MATCH_COMPLETED 事件
        event_types = [e.type for e in received_events]
        self.assertIn(EventType.ARENA_MATCH_STARTED, event_types)
        self.assertIn(EventType.ARENA_MATCH_COMPLETED, event_types)

    def test_season_completes(self):
        """完整赛季能正常完成。"""
        random.seed(99)
        config = SeasonConfig(matches_per_season=5)
        orchestrator = ArenaOrchestrator(config=config)

        async def _test():
            summary = await orchestrator.run_season()
            return summary

        summary = _run_async(_test())
        self.assertIsInstance(summary, SeasonSummary)
        self.assertEqual(summary.total_matches, 5)
        self.assertEqual(
            summary.red_wins + summary.blue_wins + summary.draws + summary.invalid_matches,
            5,
        )

    def test_serialization_roundtrip(self):
        """序列化/反序列化后状态一致。"""
        random.seed(55)
        orchestrator = ArenaOrchestrator()

        async def _test():
            for _ in range(3):
                await orchestrator.run_match()

        _run_async(_test())

        # 序列化
        data = orchestrator.serialize()
        self.assertIsInstance(data, dict)
        self.assertEqual(data["total_matches"], 3)

        # 反序列化
        restored = ArenaOrchestrator.from_dict(data)
        self.assertEqual(restored.total_matches, 3)
        self.assertAlmostEqual(
            restored.red_team.elo.rating,
            orchestrator.red_team.elo.rating,
            places=1,
        )

    def test_learning_insights_available(self):
        """运行后能获取学习洞察。"""
        random.seed(33)
        orchestrator = ArenaOrchestrator()

        async def _test():
            for _ in range(5):
                await orchestrator.run_match()

        _run_async(_test())

        insights = orchestrator.get_learning_insights()
        self.assertIn("insights", insights)
        self.assertIn("recommendations", insights)
        self.assertIn("red_elo", insights)
        self.assertIn("blue_elo", insights)
        self.assertIn("total_matches", insights)
        self.assertEqual(insights["total_matches"], 5)

    def test_match_callback_invoked(self):
        """对局完成回调被正确调用。"""
        orchestrator = ArenaOrchestrator()
        callback_results = []

        orchestrator.set_on_match_complete(lambda m: callback_results.append(m))

        async def _test():
            await orchestrator.run_match()

        _run_async(_test())

        self.assertEqual(len(callback_results), 1)
        self.assertIsInstance(callback_results[0], ArenaMatch)


class TestKillSwitchBehavior(unittest.TestCase):
    """Kill Switch 行为验证。

    设计约束说明:
        godel_config 中的 GODEL_ADVERSARIAL_*_ENABLED 标志在 import 时求值
        （读取 os.environ 并转为 bool），之后作为模块级常量使用。
        red_blue_arena.py 通过 `from core.godel_config import ...` 将这些值
        绑定到自己的模块命名空间。因此测试中必须直接 patch 模块级变量
        （而非仅修改 os.environ），才能影响运行时行为。

        如果未来重构为动态读取 env var（如改为函数调用），这些测试需要同步更新。
    """

    def test_training_disabled_returns_invalid(self):
        """总开关 OFF 时 run_match 返回 INVALID。"""
        with patch.dict(os.environ, {"SCHOLAR_GODEL_ADVERSARIAL_TRAINING": "0"}):
            # 需要重新导入以获取新的 flag 值
            # 但由于模块已加载，我们直接 patch 模块级变量
            import training.red_blue_arena as arena_module
            original = arena_module.GODEL_ADVERSARIAL_TRAINING_ENABLED
            arena_module.GODEL_ADVERSARIAL_TRAINING_ENABLED = False
            try:
                orchestrator = ArenaOrchestrator()

                async def _test():
                    match = await orchestrator.run_match()
                    return match

                match = _run_async(_test())
                self.assertEqual(match.outcome, MatchOutcome.INVALID)
            finally:
                arena_module.GODEL_ADVERSARIAL_TRAINING_ENABLED = original

    def test_red_disabled_returns_empty_challenge(self):
        """红队开关 OFF 时 generate_challenge 返回空。"""
        import training.red_blue_arena as arena_module
        original = arena_module.GODEL_ADVERSARIAL_RED_TEAM_ENABLED
        arena_module.GODEL_ADVERSARIAL_RED_TEAM_ENABLED = False
        try:
            red = RedTeam()

            async def _test():
                case, desc = await red.generate_challenge(
                    strategy=RedStrategy.EXPLOIT_WEAKNESS,
                )
                return case, desc

            case, desc = _run_async(_test())
            self.assertEqual(desc, "disabled")
            self.assertEqual(case.paper_snippet, "")
        finally:
            arena_module.GODEL_ADVERSARIAL_RED_TEAM_ENABLED = original

    def test_blue_disabled_returns_empty(self):
        """蓝队开关 OFF 时 execute_defense 返回空。"""
        import training.red_blue_arena as arena_module
        original = arena_module.GODEL_ADVERSARIAL_BLUE_TEAM_ENABLED
        arena_module.GODEL_ADVERSARIAL_BLUE_TEAM_ENABLED = False
        try:
            blue = BlueTeam()
            challenge = AdversarialCase(
                paper_snippet="test",
                target_dimension=WeaknessDimension.METHODOLOGY_ANALYSIS,
            )
            findings, score = blue.execute_defense(challenge=challenge)
            self.assertEqual(findings, [])
            self.assertEqual(score, 0.0)
        finally:
            arena_module.GODEL_ADVERSARIAL_BLUE_TEAM_ENABLED = original


class TestIntegrationEntryPoint(unittest.TestCase):
    """验证 evaluation/run_adversarial_training.py 入口可调用。"""

    def test_quick_matches_import_and_run(self):
        """run_quick_matches 可导入并运行。"""
        from evaluation.run_adversarial_training import run_quick_matches

        async def _test():
            result = await run_quick_matches(matches=3, verbose=False, seed=42)
            return result

        result = _run_async(_test())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["total_matches"], 3)
        self.assertIn("elo", result)
        self.assertIn("outcomes", result)

    def test_serialization_roundtrip_via_entry(self):
        """run_serialization_roundtrip 可导入并运行。"""
        from evaluation.run_adversarial_training import run_serialization_roundtrip

        async def _test():
            result = await run_serialization_roundtrip(matches=2, verbose=False)
            return result

        result = _run_async(_test())
        self.assertEqual(result["status"], "passed")
        self.assertTrue(all(result["checks"].values()))


class TestFullPipeline(unittest.TestCase):
    """完整 Pipeline 验证: WeaknessProfile → RedTeam → BlueTeam → ELO。"""

    def test_weakness_driven_attack(self):
        """红队能基于弱点画像选择攻击策略并生成挑战。"""
        random.seed(77)
        red = RedTeam()
        blue = BlueTeam()
        judge = MatchJudge()

        # 模拟蓝队在 statistical_reasoning 维度较弱
        blue._dimension_strength["statistical_reasoning"] = 0.2
        blue._dimension_strength["methodology_analysis"] = 0.8

        async def _test():
            # 红队选择策略
            strategy = red.select_strategy()

            # 红队生成挑战
            challenge, desc = await red.generate_challenge(
                strategy=strategy,
                target_dimension=WeaknessDimension.STATISTICAL_REASONING,
            )

            # 蓝队防御
            findings, score = blue.execute_defense(challenge=challenge)

            # 评判
            outcome, blue_elo_score, red_elo_score, details = judge.judge_match(
                challenge=challenge,
                blue_findings=findings,
                blue_score=score,
                challenge_quality=1.0,
            )

            return challenge, findings, score, outcome

        challenge, findings, score, outcome = _run_async(_test())

        # 验证流程完整性
        self.assertIsInstance(challenge, AdversarialCase)
        self.assertIsInstance(findings, list)
        self.assertIsInstance(score, float)
        self.assertIn(outcome, list(MatchOutcome))

    def test_elo_convergence_over_many_matches(self):
        """多局对抗后 ELO 系统趋于稳定。"""
        random.seed(2024)
        config = SeasonConfig(matches_per_season=20)
        orchestrator = ArenaOrchestrator(config=config)

        async def _test():
            for _ in range(20):
                await orchestrator.run_match()

        _run_async(_test())

        # 验证 ELO 在合理范围内
        red_elo = orchestrator.red_team.elo.rating
        blue_elo = orchestrator.blue_team.elo.rating

        # ELO 不应偏离初始值太远（模拟模式下双方实力接近）
        self.assertGreater(red_elo, 1000.0)
        self.assertLess(red_elo, 2000.0)
        self.assertGreater(blue_elo, 1000.0)
        self.assertLess(blue_elo, 2000.0)

        # 验证统计完整
        stats = orchestrator.get_arena_stats()
        self.assertEqual(stats["total_matches"], 20)


if __name__ == "__main__":
    unittest.main()
