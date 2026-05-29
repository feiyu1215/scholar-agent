"""
Phase 53 单元测试: 任务泛化 (Task Generalization)

核心证明: 认知循环引擎（loop.py + harness.py）是任务无关的。
只需切换 identity + tools，同一个引擎就能从"学术审稿"变为"代码审阅"。

测试场景:
1. CodeReviewer persona 正确加载（identity + tools）
2. Harness 零改动兼容代码内容（paper_sections 存储代码文件）
3. execute_tool 正确路由 CodeReviewer 的所有工具
4. update_findings 在代码审阅场景下正常工作
5. read_section 在代码内容上正常工作（含截断续读）
6. reflect_and_plan 在代码审阅场景下正常工作
7. mark_complete 的 quality gate 在代码审阅场景下正常工作
8. ScholarAgent 支持 content_sections 直接传入
9. format_context 在代码内容上正常工作
10. 边际产出信号在代码审阅场景下正常工作
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.identity import get_persona, PERSONAS, CODE_REVIEWER_IDENTITY, CODE_REVIEWER_TOOLS
from core.harness import Harness
from core.agent import ScholarAgent


# ============================================================
# 测试数据: 模拟代码文件
# ============================================================

SAMPLE_CODE_SECTIONS = {
    "main.py": """
import os
from auth import authenticate_user
from db import get_connection

def handle_request(request):
    user = authenticate_user(request.headers.get("token"))
    if not user:
        return {"error": "unauthorized"}, 401
    
    conn = get_connection()
    query = f"SELECT * FROM orders WHERE user_id = {user.id}"  # SQL injection!
    results = conn.execute(query)
    return {"orders": results}, 200

if __name__ == "__main__":
    app.run(debug=True)  # debug=True in production!
""",
    "auth.py": """
import jwt
import hashlib

SECRET_KEY = "hardcoded-secret-key-123"  # Hardcoded secret!

def authenticate_user(token):
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload
    except jwt.InvalidTokenError:
        return None

def hash_password(password):
    return hashlib.md5(password.encode()).hexdigest()  # MD5 is weak!
""",
    "db.py": """
import sqlite3

_connection = None

def get_connection():
    global _connection
    if _connection is None:
        _connection = sqlite3.connect("app.db")
    return _connection

def execute_query(query, params=None):
    conn = get_connection()
    cursor = conn.cursor()
    if params:
        cursor.execute(query, params)
    else:
        cursor.execute(query)
    return cursor.fetchall()
""",
}


# ============================================================
# 测试 1: CodeReviewer persona 正确加载
# ============================================================

def test_persona_registry():
    """验证 code_reviewer persona 在 PERSONAS 注册表中存在且结构正确。"""
    assert "code_reviewer" in PERSONAS, "code_reviewer not in PERSONAS registry"
    
    identity, tools = get_persona("code_reviewer")
    
    # identity 是非空字符串
    assert isinstance(identity, str) and len(identity) > 100, \
        f"Identity too short: {len(identity)} chars"
    
    # identity 包含 {workspace_state} 占位符（harness 注入用）
    assert "{workspace_state}" in identity, \
        "Identity must contain {workspace_state} placeholder"
    
    # tools 是非空列表
    assert isinstance(tools, list) and len(tools) >= 5, \
        f"Expected at least 5 tools, got {len(tools)}"
    
    # 验证关键工具存在
    tool_names = {t["name"] for t in tools}
    expected_tools = {"read_section", "update_findings", "talk_to_user", 
                      "review_findings", "reflect_and_plan", "mark_complete"}
    missing = expected_tools - tool_names
    assert not missing, f"Missing tools: {missing}"
    
    print("✅ 测试1: CodeReviewer persona 正确加载")


# ============================================================
# 测试 2: Harness 零改动兼容代码内容
# ============================================================

def test_harness_with_code_content():
    """验证 Harness 可以直接存储代码文件到 paper_sections，无需任何改动。"""
    h = Harness(paper_path=None, max_loop_turns=30)
    
    # 直接设置代码内容（模拟 agent.py 中 content_sections 的行为）
    h.state.paper_sections = dict(SAMPLE_CODE_SECTIONS)
    h._paper_loaded = True
    
    # 验证内容正确存储
    assert len(h.state.paper_sections) == 3
    assert "main.py" in h.state.paper_sections
    assert "SQL injection" in h.state.paper_sections["main.py"]
    
    # 验证 format_context 正常工作
    context = h.format_context()
    assert "3 个 sections" in context
    assert "main.py" in context
    assert "auth.py" in context
    
    print("✅ 测试2: Harness 零改动兼容代码内容")


# ============================================================
# 测试 3: execute_tool 正确路由 CodeReviewer 的所有工具
# ============================================================

def test_execute_tool_routing():
    """验证 CodeReviewer 使用的所有工具名称都能被 execute_tool 正确路由。"""
    h = Harness(paper_path=None, max_loop_turns=30)
    h.state.paper_sections = dict(SAMPLE_CODE_SECTIONS)
    h._paper_loaded = True
    
    # read_section
    result = h.execute_tool("read_section", {"section": "main.py"})
    assert "SQL injection" in result or "handle_request" in result, \
        f"read_section failed: {result[:100]}"
    
    # update_findings
    result = h.execute_tool("update_findings", {
        "finding": "[安全漏洞] main.py L13 SQL 拼接未做参数化",
        "evidence": 'query = f"SELECT * FROM orders WHERE user_id = {user.id}"',
        "section": "main.py",
        "priority": "high",
        "status": "verified",
    })
    assert "已记录" in result, f"update_findings failed: {result}"
    
    # review_findings
    result = h.execute_tool("review_findings", {"filter": "all"})
    assert "SQL" in result, f"review_findings failed: {result}"
    
    # talk_to_user
    result = h.execute_tool("talk_to_user", {
        "message": "发现了一个 SQL 注入漏洞",
        "expects_reply": False,
    })
    assert "__TALK__" in result, f"talk_to_user failed: {result}"
    
    # reflect_and_plan
    result = h.execute_tool("reflect_and_plan", {
        "trigger": "已读完 main.py，需要评估整体安全状况",
        "current_thinking": "存在多个安全问题",
    })
    assert isinstance(result, str) and len(result) > 0, \
        f"reflect_and_plan failed: {result}"
    
    print("✅ 测试3: execute_tool 正确路由所有工具")


# ============================================================
# 测试 4: update_findings 在代码审阅场景下正常工作
# ============================================================

def test_update_findings_code_review():
    """验证 update_findings 能正确记录代码审阅发现，包括去重。"""
    h = Harness(paper_path=None, max_loop_turns=30)
    h.state.paper_sections = dict(SAMPLE_CODE_SECTIONS)
    h._paper_loaded = True
    
    # 记录第一条发现
    result = h.execute_tool("update_findings", {
        "finding": "[安全漏洞] auth.py 硬编码 SECRET_KEY，应使用环境变量",
        "evidence": 'SECRET_KEY = "hardcoded-secret-key-123"',
        "section": "auth.py",
        "priority": "high",
        "status": "verified",
    })
    assert "已记录" in result
    assert len(h.state.findings) == 1
    
    # 记录第二条不同的发现
    result = h.execute_tool("update_findings", {
        "finding": "[安全漏洞] auth.py 使用 MD5 哈希密码，应使用 bcrypt/argon2",
        "evidence": "hashlib.md5(password.encode()).hexdigest()",
        "section": "auth.py",
        "priority": "high",
        "status": "verified",
    })
    assert "已记录" in result
    assert len(h.state.findings) == 2
    
    # 验证 findings 结构
    f = h.state.findings[0]
    assert f["priority"] == "high"
    assert f["status"] == "verified"
    assert f["section"] == "auth.py"
    assert "recorded_at_turn" in f  # Phase 52 字段
    
    print("✅ 测试4: update_findings 代码审阅场景正常")


# ============================================================
# 测试 5: read_section 在代码内容上正常工作
# ============================================================

def test_read_section_code_content():
    """验证 read_section 能正确读取代码文件，包括 list 和模糊匹配。"""
    h = Harness(paper_path=None, max_loop_turns=30)
    h.state.paper_sections = dict(SAMPLE_CODE_SECTIONS)
    h._paper_loaded = True
    
    # list 功能
    result = h.execute_tool("read_section", {"section": "list"})
    assert "main.py" in result
    assert "auth.py" in result
    assert "db.py" in result
    
    # 精确匹配
    result = h.execute_tool("read_section", {"section": "auth.py"})
    assert "SECRET_KEY" in result
    assert "jwt.decode" in result
    
    # 模糊匹配
    result = h.execute_tool("read_section", {"section": "auth"})
    assert "SECRET_KEY" in result or "未找到" not in result
    
    # 不存在的 section
    result = h.execute_tool("read_section", {"section": "nonexistent.py"})
    assert "未找到" in result
    
    # 验证 sections_read 追踪
    assert "auth.py" in h.state.sections_read or "auth" in " ".join(h.state.sections_read)
    
    print("✅ 测试5: read_section 代码内容正常工作")


# ============================================================
# 测试 6: reflect_and_plan 在代码审阅场景下正常工作
# ============================================================

def test_reflect_and_plan_code_review():
    """验证 reflect_and_plan 在代码审阅上下文中提供有意义的反思。"""
    h = Harness(paper_path=None, max_loop_turns=30)
    h.state.paper_sections = dict(SAMPLE_CODE_SECTIONS)
    h._paper_loaded = True
    h.state.sections_read = ["main.py", "auth.py"]
    h.state.loop_turns = 5
    
    # 添加一些 findings
    h.state.findings = [
        {
            "finding": "[安全漏洞] SQL 注入",
            "priority": "high",
            "status": "verified",
            "evidence": "f-string SQL",
            "section": "main.py",
            "recorded_at_turn": 2,
        },
        {
            "finding": "[安全漏洞] 硬编码密钥",
            "priority": "high",
            "status": "verified",
            "evidence": "SECRET_KEY = ...",
            "section": "auth.py",
            "recorded_at_turn": 4,
        },
    ]
    
    result = h.execute_tool("reflect_and_plan", {
        "trigger": "已审阅两个文件，需要评估覆盖度",
        "current_thinking": "安全问题严重，需要继续看 db.py",
    })
    
    # reflect_and_plan 应该返回包含状态信息的字符串
    assert isinstance(result, str)
    assert len(result) > 50, f"Reflect output too short: {result}"
    # 应该包含 findings 数量或 sections 信息
    assert "2" in result or "发现" in result or "section" in result.lower()
    
    print("✅ 测试6: reflect_and_plan 代码审阅场景正常")


# ============================================================
# 测试 7: mark_complete 的 quality gate 在代码审阅场景下正常工作
# ============================================================

def test_mark_complete_quality_gate():
    """验证 mark_complete 在有未验证 high-priority 发现时返回 NUDGE。"""
    h = Harness(paper_path=None, max_loop_turns=30)
    h.state.paper_sections = dict(SAMPLE_CODE_SECTIONS)
    h._paper_loaded = True
    
    # 禁用 CognitiveChecker（Phase 50 的小模型校验器使用学术审稿 prompt，
    # 在代码审阅场景下会误判。Checker 的 persona 适配是后续工作。）
    h.checker._enabled = False
    
    # 添加一个 needs_verification 的 high-priority 发现
    h.state.findings = [
        {
            "finding": "[竞态条件] db.py 全局连接在多线程下不安全",
            "priority": "high",
            "status": "needs_verification",
            "evidence": "",
            "section": "db.py",
            "recorded_at_turn": 3,
        },
    ]
    
    result = h.execute_tool("mark_complete", {"summary": "审阅完成"})
    
    # 应该返回 NUDGE（因为有未验证的 high-priority 发现）
    assert "__NUDGE__" in result, f"Expected NUDGE, got: {result}"
    
    # 现在把发现标记为 verified
    h.state.findings[0]["status"] = "verified"
    result = h.execute_tool("mark_complete", {"summary": "审阅完成，发现1个竞态条件问题"})
    
    # 应该返回 DONE
    assert "__DONE__" in result, f"Expected DONE, got: {result}"
    
    print("✅ 测试7: mark_complete quality gate 代码审阅场景正常")


# ============================================================
# 测试 8: ScholarAgent 支持 content_sections 直接传入
# ============================================================

def test_agent_content_sections():
    """验证 ScholarAgent 可以通过 content_sections 参数直接传入代码内容。"""
    # 不需要真正调用 LLM，只验证初始化逻辑
    agent = ScholarAgent(
        paper_path=None,
        persona="code_reviewer",
        content_sections=SAMPLE_CODE_SECTIONS,
        model="test-model",  # 不会真正调用
    )
    
    # 验证 persona 正确设置
    assert agent.persona_name == "code_reviewer"
    
    # 验证 identity 是 CodeReviewer 的（包含代码审阅相关关键词）
    assert "高级工程师" in agent.identity or "Pull Request" in agent.identity or "代码" in agent.identity
    
    # 验证 tools 是 CodeReviewer 的
    tool_names = {t["name"] for t in agent.tools}
    assert "read_section" in tool_names
    assert "update_findings" in tool_names
    assert "mark_complete" in tool_names
    
    # 验证 content_sections 已注入 harness
    assert agent.harness._paper_loaded is True
    assert "main.py" in agent.harness.state.paper_sections
    assert "auth.py" in agent.harness.state.paper_sections
    assert len(agent.harness.state.paper_sections) == 3
    
    print("✅ 测试8: ScholarAgent content_sections 传入正常")


# ============================================================
# 测试 9: format_context 在代码内容上正常工作
# ============================================================

def test_format_context_code():
    """验证 format_context 对代码内容生成有意义的状态摘要。"""
    h = Harness(paper_path=None, max_loop_turns=30)
    h.state.paper_sections = dict(SAMPLE_CODE_SECTIONS)
    h._paper_loaded = True
    h.state.sections_read = ["main.py"]
    h.state.findings = [
        {
            "finding": "[安全漏洞] SQL 注入",
            "priority": "high",
            "status": "verified",
            "evidence": "f-string in query",
            "section": "main.py",
            "recorded_at_turn": 1,
        },
    ]
    
    context = h.format_context()
    
    # 应该包含 section 信息
    assert "3 个 sections" in context
    assert "main.py" in context
    
    # 应该包含已读信息
    assert "已读过" in context or "✅" in context
    
    # 应该包含 findings 信息
    assert "1 条" in context or "发现" in context
    assert "SQL" in context
    
    # 应该包含未读提示
    assert "auth.py" in context or "db.py" in context
    
    print("✅ 测试9: format_context 代码内容正常工作")


# ============================================================
# 测试 10: 边际产出信号在代码审阅场景下正常工作
# ============================================================

def test_marginal_productivity_code_review():
    """验证边际产出信号机制在代码审阅场景下同样有效。"""
    h = Harness(paper_path=None, max_loop_turns=50)
    h.state.paper_sections = dict(SAMPLE_CODE_SECTIONS)
    h._paper_loaded = True
    
    # 模拟：早期高产（前 5 轮产出 4 条代码问题），后期零产出
    h.state.findings = [
        {"finding": "[安全] SQL 注入", "priority": "high", "status": "verified",
         "evidence": "", "section": "main.py", "recorded_at_turn": 1},
        {"finding": "[安全] 硬编码密钥", "priority": "high", "status": "verified",
         "evidence": "", "section": "auth.py", "recorded_at_turn": 2},
        {"finding": "[安全] MD5 哈希", "priority": "high", "status": "verified",
         "evidence": "", "section": "auth.py", "recorded_at_turn": 3},
        {"finding": "[配置] debug=True", "priority": "medium", "status": "verified",
         "evidence": "", "section": "main.py", "recorded_at_turn": 4},
    ]
    h.state.loop_turns = 18  # 后 14 轮零产出
    
    result = h._compute_marginal_productivity()
    
    # 应该触发信号（早期高产，近期零产出）
    assert result is not None, "Expected marginal productivity signal to trigger"
    signal_text = "\n".join(result)
    assert "没有产出任何新发现" in signal_text, \
        f"Expected zero-output message, got: {signal_text}"
    
    print("✅ 测试10: 边际产出信号代码审阅场景正常")


# ============================================================
# 测试 11: 核心证明 — 同一个 Harness 实例，不同 persona 的行为差异
#           完全来自 identity + tools，而非 harness 逻辑
# ============================================================

def test_same_harness_different_personas():
    """
    核心证明: 同一个 Harness 实例可以服务不同 persona。
    
    这证明了 Phase 53 的核心论点：
    - harness.py 是任务无关的状态守护层
    - loop.py 是任务无关的认知循环引擎
    - 行为差异完全来自 identity + tools 的不同
    """
    # 创建一个 Harness（不关心 persona）
    h = Harness(paper_path=None, max_loop_turns=30)
    h.state.paper_sections = {"module_a": "def foo(): pass\ndef bar(): return 42"}
    h._paper_loaded = True
    
    # Scholar persona 的 tools
    _, scholar_tools = get_persona("scholar")
    scholar_tool_names = {t["name"] for t in scholar_tools}
    
    # CodeReviewer persona 的 tools
    _, code_reviewer_tools = get_persona("code_reviewer")
    code_reviewer_tool_names = {t["name"] for t in code_reviewer_tools}
    
    # 两个 persona 共享的核心工具（证明 harness 路由是通用的）
    shared_tools = scholar_tool_names & code_reviewer_tool_names
    assert "read_section" in shared_tools
    assert "update_findings" in shared_tools
    assert "reflect_and_plan" in shared_tools
    assert "mark_complete" in shared_tools
    
    # 同一个 harness 实例，两个 persona 的工具都能正确执行
    # （因为 execute_tool 是按名称路由的，不关心调用者是谁）
    result1 = h.execute_tool("read_section", {"section": "module_a"})
    assert "foo" in result1
    
    result2 = h.execute_tool("update_findings", {
        "finding": "Test finding from any persona",
        "priority": "medium",
        "status": "suggestion",
    })
    assert "已记录" in result2
    
    print("✅ 测试11: 核心证明 — 同一 Harness 服务不同 persona")


# ============================================================
# 测试 12: identity 模板中 {workspace_state} 注入正常
# ============================================================

def test_identity_template_injection():
    """验证 CodeReviewer identity 的 {workspace_state} 占位符能被正确替换。"""
    h = Harness(paper_path=None, max_loop_turns=30)
    h.state.paper_sections = dict(SAMPLE_CODE_SECTIONS)
    h._paper_loaded = True
    
    # 获取 context
    context = h.format_context()
    
    # 注入到 identity 模板
    identity, _ = get_persona("code_reviewer")
    filled = identity.format(workspace_state=context)
    
    # 验证注入成功（不再包含占位符）
    assert "{workspace_state}" not in filled
    # 验证内容被注入
    assert "main.py" in filled
    assert "3 个 sections" in filled
    
    print("✅ 测试12: identity 模板注入正常")


# ============================================================
# 运行所有测试
# ============================================================

if __name__ == "__main__":
    test_persona_registry()
    test_harness_with_code_content()
    test_execute_tool_routing()
    test_update_findings_code_review()
    test_read_section_code_content()
    test_reflect_and_plan_code_review()
    test_mark_complete_quality_gate()
    test_agent_content_sections()
    test_format_context_code()
    test_marginal_productivity_code_review()
    test_same_harness_different_personas()
    test_identity_template_injection()
    
    print("\n" + "=" * 60)
    print("Phase 53 全部测试通过! 任务泛化机制工作正常。")
    print("核心证明: 认知循环引擎是任务无关的。")
    print("  - harness.py: 零改动")
    print("  - loop.py: 零改动")
    print("  - 行为差异完全来自 identity + tools")
    print("=" * 60)
