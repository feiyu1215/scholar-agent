"""
tests/test_v3_godel_config.py — Gödel Agent V3 Kill Switch 配置测试

测试内容:
    1. 默认配置（所有 flag 默认开启，V2_CONTRAST 默认关闭）
    2. 设置环境变量为 "1" 启用 flag
    3. 设置环境变量为 "0" 禁用 flag
    4. 宪法层常量值正确
    5. _env_flag 在环境变量未设置时返回 default
    6. _env_flag 读取正确的环境变量名
    7. 所有 flag 可同时启用/禁用
    8. 模块级 flag 可正常访问
    9. log_config_status 正常运行
"""

import sys
import os
import importlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ============================================================
# Fixture: 重新加载模块以获取干净的环境变量状态
# ============================================================

@pytest.fixture
def reload_godel_config(monkeypatch):
    """返回一个函数，调用后重新加载 godel_config 模块以反映当前环境变量。"""
    def _reload():
        # 移除已缓存的模块
        if "core.godel_config" in sys.modules:
            del sys.modules["core.godel_config"]
        import core.godel_config as mod
        return mod
    return _reload


# ============================================================
# 测试: _env_flag 辅助函数
# ============================================================

class TestEnvFlag:
    """测试 _env_flag 辅助函数行为。"""

    def test_returns_true_when_env_is_1(self, monkeypatch):
        """环境变量为 '1' 时返回 True。"""
        monkeypatch.setenv("SCHOLAR_GODEL_TEST_FLAG", "1")
        from core.godel_config import _env_flag
        assert _env_flag("SCHOLAR_GODEL_TEST_FLAG") is True

    def test_returns_true_when_env_is_true(self, monkeypatch):
        """环境变量为 'true' 时返回 True。"""
        monkeypatch.setenv("SCHOLAR_GODEL_TEST_FLAG", "true")
        from core.godel_config import _env_flag
        assert _env_flag("SCHOLAR_GODEL_TEST_FLAG") is True

    def test_returns_true_when_env_is_yes(self, monkeypatch):
        """环境变量为 'yes' 时返回 True。"""
        monkeypatch.setenv("SCHOLAR_GODEL_TEST_FLAG", "yes")
        from core.godel_config import _env_flag
        assert _env_flag("SCHOLAR_GODEL_TEST_FLAG") is True

    def test_returns_false_when_env_is_0(self, monkeypatch):
        """环境变量为 '0' 时返回 False。"""
        monkeypatch.setenv("SCHOLAR_GODEL_TEST_FLAG", "0")
        from core.godel_config import _env_flag
        assert _env_flag("SCHOLAR_GODEL_TEST_FLAG") is False

    def test_returns_false_when_env_is_random_string(self, monkeypatch):
        """环境变量为其他字符串时返回 False。"""
        monkeypatch.setenv("SCHOLAR_GODEL_TEST_FLAG", "nope")
        from core.godel_config import _env_flag
        assert _env_flag("SCHOLAR_GODEL_TEST_FLAG") is False

    def test_returns_default_when_env_not_set(self, monkeypatch):
        """环境变量未设置时返回 default 参数对应的 bool。"""
        monkeypatch.delenv("SCHOLAR_GODEL_NONEXISTENT", raising=False)
        from core.godel_config import _env_flag
        # default="1" → True
        assert _env_flag("SCHOLAR_GODEL_NONEXISTENT", default="1") is True
        # default="0" → False
        assert _env_flag("SCHOLAR_GODEL_NONEXISTENT", default="0") is False

    def test_reads_correct_env_var_name(self, monkeypatch):
        """_env_flag 读取的是传入的 name 参数对应的环境变量。"""
        monkeypatch.setenv("SCHOLAR_GODEL_PCG", "0")
        monkeypatch.setenv("SCHOLAR_GODEL_BUDGET", "1")
        from core.godel_config import _env_flag
        assert _env_flag("SCHOLAR_GODEL_PCG") is False
        assert _env_flag("SCHOLAR_GODEL_BUDGET") is True

    def test_strips_whitespace(self, monkeypatch):
        """环境变量值有前后空格时应正确处理。"""
        monkeypatch.setenv("SCHOLAR_GODEL_TEST_FLAG", "  1  ")
        from core.godel_config import _env_flag
        assert _env_flag("SCHOLAR_GODEL_TEST_FLAG") is True

    def test_case_insensitive_value(self, monkeypatch):
        """环境变量值大小写不敏感（TRUE/Yes/1 均为 True）。"""
        from core.godel_config import _env_flag
        monkeypatch.setenv("SCHOLAR_GODEL_TEST_FLAG", "TRUE")
        assert _env_flag("SCHOLAR_GODEL_TEST_FLAG") is True
        monkeypatch.setenv("SCHOLAR_GODEL_TEST_FLAG", "Yes")
        assert _env_flag("SCHOLAR_GODEL_TEST_FLAG") is True


# ============================================================
# 测试: 默认配置（所有 V3 flag 默认开启）
# ============================================================

class TestDefaultConfig:
    """测试模块级 flag 的默认值。"""

    def test_all_v3_flags_default_enabled(self, monkeypatch, reload_godel_config):
        """所有 V3 flag 默认为 True（default='1'）。"""
        # 清除所有相关环境变量，让 default 生效
        env_vars = [
            "SCHOLAR_GODEL_PCG",
            "SCHOLAR_GODEL_BUDGET",
            "SCHOLAR_GODEL_DISPATCHER",
            "SCHOLAR_GODEL_EVIDENCE_CHAIN",
            "SCHOLAR_GODEL_SECTION_EXP",
            "SCHOLAR_GODEL_INTRA_CONTRAST",
            "SCHOLAR_GODEL_FAST_REFLECT",
            "SCHOLAR_GODEL_DEEP_REFLECT",
            "SCHOLAR_GODEL_EMERGENCY",
        ]
        for var in env_vars:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv("SCHOLAR_GODEL_V2_CONTRAST", raising=False)

        mod = reload_godel_config()

        assert mod.GODEL_PCG_ENABLED is True
        assert mod.GODEL_BUDGET_MANAGER_ENABLED is True
        assert mod.GODEL_SIGNAL_DISPATCHER_ENABLED is True
        assert mod.GODEL_EVIDENCE_CHAIN_ENABLED is True
        assert mod.GODEL_SECTION_EXPERIENCE_ENABLED is True
        assert mod.GODEL_INTRA_CONTRAST_ENABLED is True
        assert mod.GODEL_FAST_REFLECT_ENABLED is True
        assert mod.GODEL_DEEP_REFLECT_ENABLED is True
        assert mod.GODEL_EMERGENCY_REFLECT_ENABLED is True

    def test_v2_contrast_default_disabled(self, monkeypatch, reload_godel_config):
        """V2_CONTRAST 默认为 False（default='0'）。"""
        monkeypatch.delenv("SCHOLAR_GODEL_V2_CONTRAST", raising=False)
        mod = reload_godel_config()
        assert mod.GODEL_V2_CONTRAST_ENABLED is False


# ============================================================
# 测试: 设置环境变量为 "1" 启用 flag
# ============================================================

class TestEnableFlags:
    """测试通过环境变量 '1' 启用 flag。"""

    def test_pcg_enabled_via_env(self, monkeypatch, reload_godel_config):
        """SCHOLAR_GODEL_PCG=1 → GODEL_PCG_ENABLED=True。"""
        monkeypatch.setenv("SCHOLAR_GODEL_PCG", "1")
        mod = reload_godel_config()
        assert mod.GODEL_PCG_ENABLED is True

    def test_budget_enabled_via_env(self, monkeypatch, reload_godel_config):
        """SCHOLAR_GODEL_BUDGET=1 → GODEL_BUDGET_MANAGER_ENABLED=True。"""
        monkeypatch.setenv("SCHOLAR_GODEL_BUDGET", "1")
        mod = reload_godel_config()
        assert mod.GODEL_BUDGET_MANAGER_ENABLED is True

    def test_dispatcher_enabled_via_env(self, monkeypatch, reload_godel_config):
        """SCHOLAR_GODEL_DISPATCHER=1 → GODEL_SIGNAL_DISPATCHER_ENABLED=True。"""
        monkeypatch.setenv("SCHOLAR_GODEL_DISPATCHER", "1")
        mod = reload_godel_config()
        assert mod.GODEL_SIGNAL_DISPATCHER_ENABLED is True

    def test_evidence_chain_enabled_via_env(self, monkeypatch, reload_godel_config):
        """SCHOLAR_GODEL_EVIDENCE_CHAIN=1 → GODEL_EVIDENCE_CHAIN_ENABLED=True。"""
        monkeypatch.setenv("SCHOLAR_GODEL_EVIDENCE_CHAIN", "1")
        mod = reload_godel_config()
        assert mod.GODEL_EVIDENCE_CHAIN_ENABLED is True

    def test_v2_contrast_enabled_via_env(self, monkeypatch, reload_godel_config):
        """SCHOLAR_GODEL_V2_CONTRAST=1 → GODEL_V2_CONTRAST_ENABLED=True。"""
        monkeypatch.setenv("SCHOLAR_GODEL_V2_CONTRAST", "1")
        mod = reload_godel_config()
        assert mod.GODEL_V2_CONTRAST_ENABLED is True


# ============================================================
# 测试: 设置环境变量为 "0" 禁用 flag
# ============================================================

class TestDisableFlags:
    """测试通过环境变量 '0' 禁用 flag。"""

    def test_pcg_disabled_via_env(self, monkeypatch, reload_godel_config):
        """SCHOLAR_GODEL_PCG=0 → GODEL_PCG_ENABLED=False。"""
        monkeypatch.setenv("SCHOLAR_GODEL_PCG", "0")
        mod = reload_godel_config()
        assert mod.GODEL_PCG_ENABLED is False

    def test_budget_disabled_via_env(self, monkeypatch, reload_godel_config):
        """SCHOLAR_GODEL_BUDGET=0 → GODEL_BUDGET_MANAGER_ENABLED=False。"""
        monkeypatch.setenv("SCHOLAR_GODEL_BUDGET", "0")
        mod = reload_godel_config()
        assert mod.GODEL_BUDGET_MANAGER_ENABLED is False

    def test_dispatcher_disabled_via_env(self, monkeypatch, reload_godel_config):
        """SCHOLAR_GODEL_DISPATCHER=0 → GODEL_SIGNAL_DISPATCHER_ENABLED=False。"""
        monkeypatch.setenv("SCHOLAR_GODEL_DISPATCHER", "0")
        mod = reload_godel_config()
        assert mod.GODEL_SIGNAL_DISPATCHER_ENABLED is False

    def test_evidence_chain_disabled_via_env(self, monkeypatch, reload_godel_config):
        """SCHOLAR_GODEL_EVIDENCE_CHAIN=0 → GODEL_EVIDENCE_CHAIN_ENABLED=False。"""
        monkeypatch.setenv("SCHOLAR_GODEL_EVIDENCE_CHAIN", "0")
        mod = reload_godel_config()
        assert mod.GODEL_EVIDENCE_CHAIN_ENABLED is False

    def test_all_flags_disabled_simultaneously(self, monkeypatch, reload_godel_config):
        """所有 flag 同时设为 '0' 时全部禁用。"""
        env_vars = [
            "SCHOLAR_GODEL_PCG",
            "SCHOLAR_GODEL_BUDGET",
            "SCHOLAR_GODEL_DISPATCHER",
            "SCHOLAR_GODEL_EVIDENCE_CHAIN",
            "SCHOLAR_GODEL_SECTION_EXP",
            "SCHOLAR_GODEL_INTRA_CONTRAST",
            "SCHOLAR_GODEL_FAST_REFLECT",
            "SCHOLAR_GODEL_DEEP_REFLECT",
            "SCHOLAR_GODEL_EMERGENCY",
            "SCHOLAR_GODEL_V2_CONTRAST",
        ]
        for var in env_vars:
            monkeypatch.setenv(var, "0")

        mod = reload_godel_config()

        assert mod.GODEL_PCG_ENABLED is False
        assert mod.GODEL_BUDGET_MANAGER_ENABLED is False
        assert mod.GODEL_SIGNAL_DISPATCHER_ENABLED is False
        assert mod.GODEL_EVIDENCE_CHAIN_ENABLED is False
        assert mod.GODEL_SECTION_EXPERIENCE_ENABLED is False
        assert mod.GODEL_INTRA_CONTRAST_ENABLED is False
        assert mod.GODEL_FAST_REFLECT_ENABLED is False
        assert mod.GODEL_DEEP_REFLECT_ENABLED is False
        assert mod.GODEL_EMERGENCY_REFLECT_ENABLED is False
        assert mod.GODEL_V2_CONTRAST_ENABLED is False


# ============================================================
# 测试: 所有 flag 同时启用
# ============================================================

class TestAllFlagsEnabled:
    """测试所有 flag 同时启用。"""

    def test_all_flags_enabled_simultaneously(self, monkeypatch, reload_godel_config):
        """所有 flag 同时设为 '1' 时全部启用。"""
        env_vars = [
            "SCHOLAR_GODEL_PCG",
            "SCHOLAR_GODEL_BUDGET",
            "SCHOLAR_GODEL_DISPATCHER",
            "SCHOLAR_GODEL_EVIDENCE_CHAIN",
            "SCHOLAR_GODEL_SECTION_EXP",
            "SCHOLAR_GODEL_INTRA_CONTRAST",
            "SCHOLAR_GODEL_FAST_REFLECT",
            "SCHOLAR_GODEL_DEEP_REFLECT",
            "SCHOLAR_GODEL_EMERGENCY",
            "SCHOLAR_GODEL_V2_CONTRAST",
        ]
        for var in env_vars:
            monkeypatch.setenv(var, "1")

        mod = reload_godel_config()

        assert mod.GODEL_PCG_ENABLED is True
        assert mod.GODEL_BUDGET_MANAGER_ENABLED is True
        assert mod.GODEL_SIGNAL_DISPATCHER_ENABLED is True
        assert mod.GODEL_EVIDENCE_CHAIN_ENABLED is True
        assert mod.GODEL_SECTION_EXPERIENCE_ENABLED is True
        assert mod.GODEL_INTRA_CONTRAST_ENABLED is True
        assert mod.GODEL_FAST_REFLECT_ENABLED is True
        assert mod.GODEL_DEEP_REFLECT_ENABLED is True
        assert mod.GODEL_EMERGENCY_REFLECT_ENABLED is True
        assert mod.GODEL_V2_CONTRAST_ENABLED is True


# ============================================================
# 测试: 宪法层常量
# ============================================================

class TestConstitutionalConstants:
    """测试宪法层常量（Layer 0）值正确且不可被意外修改。"""

    def test_max_meta_depth(self):
        """MAX_META_DEPTH 应为 2。"""
        from core.godel_config import MAX_META_DEPTH
        assert MAX_META_DEPTH == 2

    def test_signal_dispatcher_max_per_turn(self):
        """SIGNAL_DISPATCHER_MAX_PER_TURN 应为 2。"""
        from core.godel_config import SIGNAL_DISPATCHER_MAX_PER_TURN
        assert SIGNAL_DISPATCHER_MAX_PER_TURN == 2

    def test_intra_contrast_min_sections(self):
        """INTRA_CONTRAST_MIN_SECTIONS 应为 15。"""
        from core.godel_config import INTRA_CONTRAST_MIN_SECTIONS
        assert INTRA_CONTRAST_MIN_SECTIONS == 15

    def test_evidence_chain_min_for_modify(self):
        """EVIDENCE_CHAIN_MIN_FOR_MODIFY 应为 3。"""
        from core.godel_config import EVIDENCE_CHAIN_MIN_FOR_MODIFY
        assert EVIDENCE_CHAIN_MIN_FOR_MODIFY == 3

    def test_zone_a_min_tokens(self):
        """ZONE_A_MIN_TOKENS 应为 6000。"""
        from core.godel_config import ZONE_A_MIN_TOKENS
        assert ZONE_A_MIN_TOKENS == 6000

    def test_zone_a_default_tokens(self):
        """ZONE_A_DEFAULT_TOKENS 应为 8000。"""
        from core.godel_config import ZONE_A_DEFAULT_TOKENS
        assert ZONE_A_DEFAULT_TOKENS == 8000

    def test_zone_b_max_tokens(self):
        """ZONE_B_MAX_TOKENS 应为 40000。"""
        from core.godel_config import ZONE_B_MAX_TOKENS
        assert ZONE_B_MAX_TOKENS == 40000

    def test_pcg_format_max_tokens(self):
        """PCG_FORMAT_MAX_TOKENS 应为 1500。"""
        from core.godel_config import PCG_FORMAT_MAX_TOKENS
        assert PCG_FORMAT_MAX_TOKENS == 1500

    def test_consecutive_decline_rollback(self):
        """CONSECUTIVE_DECLINE_ROLLBACK 应为 2。"""
        from core.godel_config import CONSECUTIVE_DECLINE_ROLLBACK
        assert CONSECUTIVE_DECLINE_ROLLBACK == 2

    def test_cold_start_session_threshold(self):
        """COLD_START_SESSION_THRESHOLD 应为 10。"""
        from core.godel_config import COLD_START_SESSION_THRESHOLD
        assert COLD_START_SESSION_THRESHOLD == 10

    def test_section_experience_window(self):
        """SECTION_EXPERIENCE_WINDOW 应为 500。"""
        from core.godel_config import SECTION_EXPERIENCE_WINDOW
        assert SECTION_EXPERIENCE_WINDOW == 500

    def test_signal_dedup_window(self):
        """SIGNAL_DEDUP_WINDOW 应为 3。"""
        from core.godel_config import SIGNAL_DEDUP_WINDOW
        assert SIGNAL_DEDUP_WINDOW == 3

    def test_constants_are_int(self):
        """所有宪法层常量应为 int 类型。"""
        from core.godel_config import (
            MAX_META_DEPTH,
            SIGNAL_DISPATCHER_MAX_PER_TURN,
            INTRA_CONTRAST_MIN_SECTIONS,
            EVIDENCE_CHAIN_MIN_FOR_MODIFY,
            ZONE_A_MIN_TOKENS,
            ZONE_A_DEFAULT_TOKENS,
            ZONE_B_MAX_TOKENS,
            PCG_FORMAT_MAX_TOKENS,
            CONSECUTIVE_DECLINE_ROLLBACK,
            COLD_START_SESSION_THRESHOLD,
            SECTION_EXPERIENCE_WINDOW,
            SIGNAL_DEDUP_WINDOW,
        )
        for const in [
            MAX_META_DEPTH,
            SIGNAL_DISPATCHER_MAX_PER_TURN,
            INTRA_CONTRAST_MIN_SECTIONS,
            EVIDENCE_CHAIN_MIN_FOR_MODIFY,
            ZONE_A_MIN_TOKENS,
            ZONE_A_DEFAULT_TOKENS,
            ZONE_B_MAX_TOKENS,
            PCG_FORMAT_MAX_TOKENS,
            CONSECUTIVE_DECLINE_ROLLBACK,
            COLD_START_SESSION_THRESHOLD,
            SECTION_EXPERIENCE_WINDOW,
            SIGNAL_DEDUP_WINDOW,
        ]:
            assert isinstance(const, int)


# ============================================================
# 测试: 模块级 flag 访问
# ============================================================

class TestFlagAccess:
    """测试模块级 flag 可正常访问且类型正确。"""

    def test_all_flags_are_bool(self):
        """所有 flag 应为 bool 类型。"""
        import core.godel_config as mod
        flags = [
            mod.GODEL_PCG_ENABLED,
            mod.GODEL_BUDGET_MANAGER_ENABLED,
            mod.GODEL_SIGNAL_DISPATCHER_ENABLED,
            mod.GODEL_EVIDENCE_CHAIN_ENABLED,
            mod.GODEL_SECTION_EXPERIENCE_ENABLED,
            mod.GODEL_INTRA_CONTRAST_ENABLED,
            mod.GODEL_FAST_REFLECT_ENABLED,
            mod.GODEL_DEEP_REFLECT_ENABLED,
            mod.GODEL_EMERGENCY_REFLECT_ENABLED,
            mod.GODEL_V2_CONTRAST_ENABLED,
        ]
        for flag in flags:
            assert isinstance(flag, bool)

    def test_flag_can_be_used_in_conditional(self):
        """flag 可直接用于 if 条件判断。"""
        import core.godel_config as mod
        # 不应抛出异常
        if mod.GODEL_PCG_ENABLED:
            pass
        if not mod.GODEL_V2_CONTRAST_ENABLED:
            pass


# ============================================================
# 测试: log_config_status
# ============================================================

class TestLogConfigStatus:
    """测试 log_config_status 函数。"""

    def test_log_config_status_runs_without_error(self):
        """log_config_status() 应正常执行不抛异常。"""
        from core.godel_config import log_config_status
        # 不应抛出任何异常
        log_config_status()

    def test_log_config_status_logs_message(self, caplog):
        """log_config_status() 应输出日志。"""
        import logging
        with caplog.at_level(logging.INFO, logger="core.godel_config"):
            from core.godel_config import log_config_status
            log_config_status()
        assert "GodelConfig" in caplog.text


# ============================================================
# 运行
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
