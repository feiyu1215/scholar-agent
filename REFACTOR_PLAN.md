# ScholarAgent V2 — 代码修复执行计划

**创建日期**: 2026-05-31  
**基于**: CODE_REVIEW_REPORT.md（外部审查）+ FULL_CODE_AUDIT_PLAN.md（内部审计）  
**核心原则**: 最小风险修改 → 逐步推进 → 每步可验证 → 不破坏端到端

---

## 校准说明

CODE_REVIEW_REPORT（5/31 外部审查）与 FULL_CODE_AUDIT_PLAN（此前内部审计）存在部分重叠和矛盾。
经交叉验证代码实际状态后，以下问题已确认**不需要修复**：

| CODE_REVIEW_REPORT 编号 | 原始严重性 | 实际状态 | 原因 |
|--------------------------|-----------|---------|------|
| #1 (loop.py asyncio.gather) | Critical | **已有正确防护** | `isinstance(res, BaseException)` 过滤完整存在，审查误判 |
| #5 (compaction.py tokenizer) | Required | **非问题** | compaction 实际用 `len(text)//3` 估算，不依赖 tiktoken |
| #7 (llm/client.py 返回空串) | Required | **不可达代码** | 最后一次 retry 失败时 raise，`return ""` 永远不执行 |
| #11 (mcl.py gate_fired) | Required | **设计正确** | 已有 `_gate_fired` 标记 + 第二次直接放行 |
| compaction.py deepcopy | Required | **不存在** | compaction 不使用 deepcopy |

**实际需修复问题：13 项**（原报告 18 项中减去 5 项误判/已修复）

---

## 修复分区与依赖图

```
Phase 0 (独立/无依赖) ─── CI/Docker 安全加固 ──── 0 风险（不碰核心代码）
      │
Phase 1 (工具链/基础) ─── text_utils 提取 + event_bus 日志 ──── 低风险
      │
Phase 2 (认知核心) ──── identity format + signal_dispatcher + agent timeout ──── 中风险
      │
Phase 3 (重构级) ───── loop 拆分 + llm DRY + 配置分文件 ──── 高风险（大面积变更）
      │
Phase 4 (架构级) ───── loop↔harness 解耦 + 信号 structured output ──── 极高风险
```

---

## Phase 0：零风险加固（CI / Docker / 配置）

### 0-1. ci.yml placeholder key → GitHub Secrets

**文件**: `.github/workflows/ci.yml`  
**行号**: 40, 58, 75  
**当前代码**:
```yaml
env:
  OPENAI_API_KEY: "sk-test-placeholder"
```

**修改方案**:
```yaml
env:
  OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY_TEST }}
```

**注意事项**:
- 需要在 GitHub repo Settings → Secrets 中设置 `OPENAI_API_KEY_TEST`
- 对于不需要真实 API 调用的 job（syntax check / import check），可改为检测 `OPENAI_API_KEY` 是否存在并跳过 API 相关测试
- 如果 CI 中确实不需要调用 API，更安全的做法是：`OPENAI_API_KEY: "test"` + 在代码中添加 mock 层

**推荐方案（渐进式）**:
```yaml
# 对 syntax-check 和 import-check job：不需要 key
# 对 test job：使用 secrets（无 key 时 skip API 测试）
# 对 e2e-smoke：使用 secrets（无 key 时标记为 skip 而非 fail）
```

**验证**: `git push` 后观察 CI 是否 green

---

### 0-2. ci.yml test job `|| echo` → 正确的失败处理

**文件**: `.github/workflows/ci.yml`  
**行号**: 56  
**当前代码**:
```yaml
run: cd v2 && pytest tests/ -m "not e2e" --tb=short -q 2>/dev/null || echo "No tests found"
```

**修改方案**:
```yaml
run: |
  cd v2
  if find tests/ -name "test_*.py" | head -1 | grep -q .; then
    pytest tests/ -m "not e2e" --tb=short -q
  else
    echo "::notice::No test files found, skipping"
  fi
```

**风险**: 极低。仅改变 CI 行为，不影响代码。  
**验证**: 本地 `pytest tests/ -m "not e2e"` 确认通过

---

### 0-3. docker-compose.yml env_file → 显式变量列表

**文件**: `docker-compose.yml`  
**当前代码**:
```yaml
env_file: .env
```

**修改方案**:
```yaml
environment:
  - OPENAI_API_KEY=${OPENAI_API_KEY}
  - OPENAI_BASE_URL=${OPENAI_BASE_URL:-}
  - LLM_MODEL=${LLM_MODEL:-gpt-4.1}
  - SCHOLAR_LOG_LEVEL=${SCHOLAR_LOG_LEVEL:-INFO}
```

**风险**: 低。若遗漏某个需要的变量，容器内报错即可发现。  
**验证**: `docker compose up` 后检查 agent 是否正常启动

---

### 0-4. .dockerignore 加固

**文件**: 新建 `.dockerignore`（如不存在）  
**内容**:
```
.env
.env.*
.git
__pycache__
*.pyc
.pytest_cache
evaluation/
docs/
*.md
```

**风险**: 零  
**验证**: `docker build .` 检查镜像大小是否减小

---

## Phase 1：低风险基础修复

### 1-1. 提取 `_extract_terms` 为共享模块

**问题**: `findings.py` 中 `_extract_terms` 定义 3 次，分词策略和 stopwords 不一致。  
**影响范围**: `tool_handlers/findings.py` 内部  
**牵连分析**: 仅内部调用，无跨模块依赖

**新建文件**: `v2/core/text_utils.py`

```python
"""
core/text_utils.py — 共享文本处理工具

提供统一的分词、相似度计算等基础函数。
"""

import re
from typing import Set

# 英文停用词（统一集合）
_EN_STOPWORDS: set[str] = {
    'this', 'that', 'with', 'from', 'have', 'been', 'were', 'will',
    'would', 'could', 'should', 'their', 'which', 'about', 'other',
    'than', 'then', 'also', 'into', 'more', 'some', 'such', 'only',
    'over', 'when', 'does', 'most', 'very', 'each', 'both', 'they',
    'these', 'those', 'between', 'through', 'after', 'before',
}

# 中文停用词
_CJK_STOPWORDS: set[str] = {
    '的', '了', '是', '在', '和', '有', '与', '对', '为', '中',
    '不', '我', '他', '她', '它', '们', '这', '那', '个', '于',
    '而', '但', '也', '就', '都', '把', '被', '让', '给', '从',
}


def extract_terms(text: str, include_cjk: bool = True) -> set[str]:
    """统一的术语提取函数。

    Args:
        text: 输入文本
        include_cjk: 是否包含中文 bigram 术语（默认 True）

    Returns:
        术语集合（英文小写词 + 可选的 CJK bigrams）
    """
    # 英文 4+ 字母词
    en_words = set(re.findall(r'[a-zA-Z]{4,}', text.lower()))
    terms = {w for w in en_words if w not in _EN_STOPWORDS}

    # CJK bigrams
    if include_cjk:
        cjk_chars = re.findall(r'[\u4e00-\u9fff]', text)
        cjk_filtered = [c for c in cjk_chars if c not in _CJK_STOPWORDS]
        for i in range(len(cjk_filtered) - 1):
            terms.add(f"cjk_{cjk_filtered[i]}{cjk_filtered[i+1]}")

    return terms
```

**修改文件**: `v2/core/tool_handlers/findings.py`

- 删除行 167-173、271-277、335-354 的三处本地 `_extract_terms` 定义
- 顶部添加 `from core.text_utils import extract_terms`
- 所有调用处替换为 `extract_terms(text)`

**牵连风险**: 
- `check_verification_integrity` 和 `hdwm_match_and_resolve` 之前不支持 CJK，统一后会新增 CJK 术语——语义上更正确，但 Jaccard 相似度数值会变化
- **缓解措施**: 保持阈值不变（0.7），因为加入 CJK 术语只会让匹配更精确

**验证**: `pytest v2/tests/ -k "finding" -v`

---

### 1-2. event_bus.py `_safe_call` 添加日志

**文件**: `v2/core/event_bus.py`  
**行号**: 398-401  
**当前代码**:
```python
except Exception:
    # 订阅者异常不影响事件分发
    pass
```

**修改方案**:
```python
except Exception:
    logger.exception(
        "[EventBus] Subscriber %s raised exception for event %s",
        sub.handler.__qualname__,
        event.name,
    )
```

**牵连分析**: 纯增量变更，不改变控制流。异常仍被隔离，只是增加可观测性。  
**风险**: 极低  
**验证**: 故意在某个 subscriber 中 raise，观察日志输出

---

### 1-3. llm/client.py 清理死代码

**文件**: `v2/llm/client.py`  
**行号**: 177-182

**修改方案**: 删除 `core.state.session_model` 的 try/except 块

```python
# 删除这段：
# try:
#     from core.state import session_model as _sm
#     model = _sm
# except (ImportError, AttributeError):
#     pass
```

**同时清理**: 第 295 行和第 459 行的不可达 `return ""`——虽然不影响运行，但误导读者。改为：
```python
# 不可达：最后一次 attempt 失败时已 raise
raise RuntimeError("Unreachable: all retries should have raised")
```

**风险**: 极低（删除死代码不改变行为）  
**验证**: `pytest v2/tests/ -k "llm or client" -v`

---

### 1-4. godel_config.py 启动时类型校验

**文件**: `v2/core/godel_config.py`  
**修改位置**: 文件末尾添加

```python
def _validate_config() -> None:
    """启动时 fail-fast：校验所有数值型配置。"""
    numeric_configs = {
        'SIGNAL_DISPATCHER_MAX_PER_TURN': SIGNAL_DISPATCHER_MAX_PER_TURN,
        'SIGNAL_DEDUP_WINDOW': SIGNAL_DEDUP_WINDOW,
        'MAX_META_DEPTH': MAX_META_DEPTH,
        'EVIDENCE_CHAIN_MIN_FOR_MODIFY': EVIDENCE_CHAIN_MIN_FOR_MODIFY,
        'ZONE_A_MIN_TOKENS': ZONE_A_MIN_TOKENS,
    }
    for name, value in numeric_configs.items():
        if not isinstance(value, int) or value < 0:
            raise ValueError(
                f"Config error: {name}={value!r} must be a non-negative int. "
                f"Check environment variable SCHOLAR_GODEL_{name}."
            )

_validate_config()
```

**牵连分析**: 仅在 import 时执行一次。如果环境变量被注入非法值，会在启动时立即报错而非运行中崩溃。  
**风险**: 低（但需确认 `_env_flag` 对数值配置返回的类型是 int）  
**验证**: 设置 `SCHOLAR_GODEL_SIGNAL_DEDUP_WINDOW=abc` → 确认启动时 crash

---

## Phase 2：中风险认知核心修复

### 2-1. identity.py `build_sub_perspective_prompt` 修复 str.format

**文件**: `v2/core/identity.py`  
**行号**: 1001-1013  
**当前代码**:
```python
return SUB_PERSPECTIVE_IDENTITY.format(
    lens=lens,
    focus=focus,
    question=question,
    workspace_state=workspace_state,
)
```

**问题**: `workspace_state` 中若包含花括号（如 JSON findings `{"category": "..."}`），会触发 `KeyError` 或格式错误。

**修改方案**:
```python
result = SUB_PERSPECTIVE_IDENTITY
result = result.replace("{lens}", lens)
result = result.replace("{focus}", focus)
result = result.replace("{question}", question)
result = result.replace("{workspace_state}", workspace_state)
return result
```

**同时修改**: `SUB_PERSPECTIVE_IDENTITY` 模板（行 888-934）中的 `{lens}` 等占位符保持不变（`str.replace` 也是匹配这些字面字符串）。

**牵连分析**: 
- `build_system_prompt` 已用此方案修复（有先例）
- 唯一风险：如果模板中 `{lens}` 出现多次，`str.replace` 会全部替换（`.format()` 也会）——行为一致
- 需要确认 `SUB_PERSPECTIVE_IDENTITY` 中没有需要保留的 `{` 字面字符

**验证**: 在子视角执行时输入包含 `{` 的 workspace_state，确认不 crash

---

### 2-2. signal_dispatcher.py 历史裁剪逻辑修复

**文件**: `v2/core/signal_dispatcher.py`  
**行号**: 147-150  
**当前代码**:
```python
if len(self._history) > 60:
    cutoff = current_turn - 20
    self._history = [(t, s) for t, s in self._history if t >= cutoff]
```

**问题**: 裁剪阈值 60 和保留窗口 20 都是硬编码，与 `DEDUP_WINDOW`（可配置）不协调。如果 `DEDUP_WINDOW > 20`，裁剪可能删除仍在去重窗口内的记录。

**修改方案**:
```python
# 裁剪触发阈值 = DEDUP_WINDOW * 3（留足余量）
if len(self._history) > self.DEDUP_WINDOW * 3:
    cutoff = current_turn - self.DEDUP_WINDOW
    self._history = [(t, s) for t, s in self._history if t >= cutoff]
```

**牵连分析**: 仅影响历史清理频率。`DEDUP_WINDOW` 默认值 5 → 裁剪触发阈值从 60 降为 15，更积极清理（内存更友好）。不影响去重正确性。

**风险**: 低  
**验证**: 运行长论文（40+ 轮），观察信号去重行为是否正常

---

### 2-3. agent.py 添加外层超时保护

**文件**: `v2/core/agent.py`  
**修改位置**: `start()` 和 `chat()` 方法

**当前代码**（以 `start()` 为例）:
```python
async def start(self):
    ...
    await cognitive_loop(...)
```

**修改方案**:
```python
import asyncio

# 在 godel_config.py 中添加
SESSION_TIMEOUT_SECONDS = int(os.environ.get("SCHOLAR_SESSION_TIMEOUT", "3600"))  # 默认 1 小时

# 在 agent.py start() 中
async def start(self):
    ...
    try:
        await asyncio.wait_for(
            cognitive_loop(...),
            timeout=SESSION_TIMEOUT_SECONDS if SESSION_TIMEOUT_SECONDS > 0 else None,
        )
    except asyncio.TimeoutError:
        logger.error("[Agent] Session timed out after %d seconds", SESSION_TIMEOUT_SECONDS)
        # 优雅终止：保存当前状态
        self.harness.state.session_terminated = True
        self.harness.state.termination_reason = "timeout"
```

**牵连分析**:
- `cognitive_loop` 本身有 `max_loop_turns` 硬限制（轮次保护），但如果单次 LLM 调用 hang 住，轮次保护无法生效
- 超时后需要保存状态（findings 不丢失）——需确认 state 此时的一致性
- `SESSION_TIMEOUT_SECONDS=0` 表示不限制（保持向后兼容）

**风险**: 中。需要验证：
1. 超时后 `state` 是否可被正确序列化
2. `asyncio.wait_for` 对正在执行的 `asyncio.gather`（子视角并行）的取消行为
3. LLM client 的 httpx 连接是否被正确清理

**验证**: 设置 `SCHOLAR_SESSION_TIMEOUT=10` 后运行长论文，确认超时后 findings 仍可导出

---

### 2-4. harness/paper_loader 路径遍历防御

**文件**: `v2/core/paper_loader.py`  
**修改位置**: `load_paper()` 函数入口

**修改方案**:
```python
from pathlib import Path

# 在 load_paper 函数开头添加
def load_paper(path: str, state, allowed_base: str | None = None) -> None:
    """加载论文。

    Args:
        path: 论文文件/目录路径
        allowed_base: 允许的基础目录（安全沙箱）。若为 None，不限制。
    """
    resolved = Path(path).resolve()

    # 路径遍历防御
    if allowed_base:
        base = Path(allowed_base).resolve()
        if not resolved.is_relative_to(base):
            raise ValueError(
                f"Security: paper path '{resolved}' is outside allowed base '{base}'"
            )
    ...
```

**同时修改**: `harness.py` 中调用 `load_paper` 时传入 `allowed_base`

**牵连分析**:
- `allowed_base` 默认 None = 不限制（向后兼容）
- 交互模式下用户可能传入任意路径——需要在 `main.py` 中设置 `allowed_base` 为工作目录
- Docker 环境中 `allowed_base` 应为 `/app/papers/` 或挂载目录

**风险**: 低（增量添加，default 为 None 不破坏现有行为）  
**验证**: 尝试 `../../../etc/passwd` 作为论文路径，确认被拒绝

---

### 2-5. skill_registry.py 路径遍历防御

**文件**: `v2/core/skill_registry.py`（或实际文件名）  
**修改方案**: 与 2-4 同模式——在加载 external skill 时验证 `meta.path.resolve().is_relative_to(skills_base_dir)`

**风险**: 低  
**验证**: 构造恶意 skill meta 测试拒绝行为

---

## Phase 3：高风险重构（需要专项分支）

> **重要**: Phase 3 的每个修改都应在独立 git 分支上进行，完成后跑全量测试再合入 main。

### 3-1. llm/client.py 重试逻辑 DRY

**文件**: `v2/llm/client.py`  
**目标**: 将 `chat()`、`chat_with_tools()`、`chat_messages()` 中重复的 ~23 行重试 boilerplate 提取为 `_retry_call()` helper。

**设计方案**:
```python
async def _retry_call(
    self,
    operation: Callable[..., Awaitable[T]],
    *args,
    retries: int | None = None,
    **kwargs,
) -> T:
    """通用重试逻辑。

    处理：total_timeout guard、semaphore、rate_limit_wait、
    transient/permanent 错误分类、backoff、日志。

    Raises:
        最后一次 attempt 的原始异常
    """
    retries = retries or self.max_retries
    start_time = time.time()

    for attempt in range(retries):
        # total timeout guard
        if time.time() - start_time > self.total_timeout:
            raise TimeoutError(f"Total timeout ({self.total_timeout}s) exceeded")

        # semaphore + rate limit
        await self._semaphore.acquire()
        try:
            if self._rate_limit_wait:
                await asyncio.sleep(self._rate_limit_wait)
                self._rate_limit_wait = 0

            return await asyncio.wait_for(operation(*args, **kwargs), timeout=self.timeout)

        except Exception as e:
            if attempt == retries - 1:
                raise
            if not self._is_transient_error(e):
                raise
            self.total_retries += 1
            wait = self._compute_backoff(attempt, e)
            logger.warning("[LLM] Attempt %d/%d failed: %s. Retrying in %.1fs",
                          attempt + 1, retries, str(e)[:100], wait)
            await asyncio.sleep(wait)
        finally:
            self._semaphore.release()

    raise RuntimeError("Unreachable")
```

**牵连分析**:
- 三个主方法变为：准备参数 → `_retry_call(self._do_chat, messages, ...)` → 解析结果
- token 统计逻辑需要从循环内提取到 `operation` 的返回值中一并返回
- 流式模式有特殊处理（不能简单 await），需要额外的 `_retry_stream_call`

**风险**: 高——触及所有 LLM 调用路径  
**验证**: 全量测试 + 注入网络错误模拟 retry 路径

---

### 3-2. loop.py 提取信号解析模块

**文件**: `v2/core/loop.py`  
**目标**: 将 `cognitive_loop()` 中的信号解析（`__DONE__`/`__SPAWN__`/`__SWITCH__` 等）提取为独立模块。

**设计方案**:

新建 `v2/core/signal_parser.py`:
```python
"""
core/signal_parser.py — 信号协议解析

从 LLM 响应中提取结构化信号。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

class SignalType(Enum):
    NONE = "none"
    DONE = "done"
    SPAWN = "spawn"
    SWITCH = "switch"
    TALK = "talk"
    NUDGE = "nudge"

@dataclass
class ParsedSignal:
    signal_type: SignalType
    payload: str = ""  # SPAWN 的 lens 名、SWITCH 的 persona 名等

def parse_signal(response_text: str) -> ParsedSignal:
    """从 LLM 响应的最后一行解析信号。

    只检查最后一行，避免 LLM 讨论信号时误触发。
    """
    if not response_text:
        return ParsedSignal(SignalType.NONE)

    last_line = response_text.strip().split('\n')[-1].strip()

    if last_line.startswith("__DONE__"):
        return ParsedSignal(SignalType.DONE)
    if last_line.startswith("__SPAWN__"):
        payload = last_line.replace("__SPAWN__", "").strip()
        return ParsedSignal(SignalType.SPAWN, payload)
    # ... 其他信号
    return ParsedSignal(SignalType.NONE)
```

**牵连分析**:
- loop.py 中目前是内联字符串匹配，分散在 400 行函数的多个位置
- 提取后 loop.py 变为：`signal = parse_signal(response)` → `if signal.type == SignalType.DONE: ...`
- **关键风险**: 需要完整映射当前所有信号匹配逻辑，任何遗漏都会导致 agent 行为改变

**风险**: 高  
**验证**: 
1. 对比重构前后 `cognitive_loop` 在相同输入下的行为
2. 全量测试 + 手动跑 1 篇论文对比 findings

---

### 3-3. identity.py 工具 schema 中心注册表

**文件**: `v2/core/identity.py`（1559 行 → 目标 ~500 行）  
**目标**: 将 ~1000 行工具 schema dict literal 提取为独立注册表。

**设计方案**:

新建 `v2/core/tool_schemas.py`:
```python
"""
core/tool_schemas.py — 工具 Schema 中心注册表

所有工具的 JSON Schema 定义集中管理，各 Persona 通过名称列表引用。
"""

TOOL_SCHEMAS: dict[str, dict] = {
    "read_section": {
        "name": "read_section",
        "description": "...",
        "parameters": { ... },
    },
    "submit_finding": { ... },
    # ...
}

# 各 Persona 的工具集（名称列表）
PERSONA_TOOLS = {
    "reviewer": ["read_section", "submit_finding", "search_literature", ...],
    "writer": ["read_section", "generate_edit_plan", ...],
    "meta": ["read_section", "submit_finding", "spawn_perspective", ...],
}

def get_tools_for_persona(persona: str) -> list[dict]:
    """返回指定 Persona 的完整工具 schema 列表。"""
    names = PERSONA_TOOLS.get(persona, [])
    return [TOOL_SCHEMAS[n] for n in names if n in TOOL_SCHEMAS]
```

**牵连分析**:
- identity.py 中三个 persona 的 `_build_tools()` 方法简化为 `get_tools_for_persona("reviewer")`
- 任何工具定义变更只需修改 `tool_schemas.py` 一处
- **数据验证需求**: 添加 startup check 确认 PERSONA_TOOLS 中的所有名称都在 TOOL_SCHEMAS 中存在

**风险**: 中高（大面积文件变更，但逻辑不变）  
**验证**: 对比提取前后 `get_tools_for_persona` 返回内容完全一致

---

### 3-4. main.py 命令解析重构

**文件**: `v2/main.py`  
**当前代码**: if-elif 链

**修改方案**:
```python
COMMANDS: dict[str, Callable] = {
    "/help": cmd_help,
    "/status": cmd_status,
    "/findings": cmd_findings,
    "/quit": cmd_quit,
    "/export": cmd_export,
    # ...
}

def handle_command(user_input: str, agent) -> bool:
    """处理命令。返回 True 表示应继续循环。"""
    cmd = user_input.split()[0].lower()
    handler = COMMANDS.get(cmd)
    if handler:
        return handler(user_input, agent)
    return True  # 未知命令，继续
```

**风险**: 低（纯重构，行为不变）  
**验证**: 逐个命令测试

---

## Phase 4：架构级演进（长期规划，不急于实施）

### 4-1. loop ↔ harness 解耦（LoopContext Protocol）

**目标**: loop.py 不再直接操作 `harness.state` 的内部属性，改为通过 Protocol 接口交互。

**设计思路**:
```python
class LoopContext(Protocol):
    """认知循环所需的上下文接口。"""
    def get_context(self) -> str: ...
    def execute_tool(self, name: str, args: dict) -> str: ...
    def submit_finding(self, finding: dict) -> str: ...
    def is_budget_exceeded(self) -> bool: ...
    def get_turn_count(self) -> int: ...
    @property
    def state(self) -> WorkspaceState: ...
```

**实施路径**: 逐步迁移，先定义 Protocol → harness 实现 Protocol → loop 改为接收 `ctx: LoopContext` → 验证 → 最后移除直接 import。

**风险**: 极高——涉及 loop 和 harness 的所有交互点  
**建议**: 等 Phase 1-3 稳定后再考虑

---

### 4-2. 信号协议 Structured Output 化

**目标**: 将 `__DONE__`/`__SPAWN__` 等字符串信号改为 tool_call 信号。

**设计思路**:
```python
# Agent 通过 tool_call 发出信号：
{"name": "signal_done", "arguments": {"reason": "review complete"}}
{"name": "signal_spawn", "arguments": {"lens": "methodology", "focus": "..."}}
```

**优势**:
- 消除字符串匹配误触发风险
- LLM 讨论信号本身时不会意外触发
- 结构化参数传递更可靠

**风险**: 极高——改变 Agent 的核心行为协议，需要重新调整 system prompt  
**建议**: 作为 V3 特性规划

---

## 执行顺序建议

```
Week 1: Phase 0 全部（4 项）+ Phase 1 全部（4 项） → 合入 main
Week 2: Phase 2-1 ~ 2-5（5 项） → feature branch → 全量测试 → 合入
Week 3: Phase 3-1（llm DRY）→ 独立 branch → 全量测试
Week 4: Phase 3-2 ~ 3-4 → 逐个 branch
未来: Phase 4 纳入 V3 路线图
```

---

## 验证策略

每个 Phase 完成后的验证清单：

1. **语法检查**: `find v2 -name "*.py" -exec python -m py_compile {} \;`
2. **单元测试**: `cd v2 && pytest tests/ -m "not e2e" --tb=short`
3. **端到端冒烟**: `cd v2 && python -m evaluation.run_recall_verification --paper gold_paper_1 --max-turns 5`
4. **Docker 构建**: `docker build -t scholar-agent:test .`
5. **CI 验证**: Push 到 feature branch → 观察 GitHub Actions

---

## 风险矩阵

| 修改项 | 文件数 | 改动行 | 可能破坏 | 回滚难度 |
|--------|--------|--------|----------|----------|
| Phase 0 (CI/Docker) | 3 | ~30 | CI 状态 | 极易 |
| Phase 1-1 (text_utils) | 2 | ~80 | findings 去重精度 | 易 |
| Phase 1-2 (event_bus) | 1 | ~5 | 无 | 极易 |
| Phase 1-3 (llm 死代码) | 1 | ~10 | 无 | 极易 |
| Phase 1-4 (config 校验) | 1 | ~20 | 启动失败（如配置有误） | 易 |
| Phase 2-1 (identity format) | 1 | ~10 | 子视角 prompt 异常 | 易 |
| Phase 2-2 (signal trim) | 1 | ~5 | 信号去重行为 | 易 |
| Phase 2-3 (timeout) | 2 | ~20 | Agent 超时终止 | 中 |
| Phase 2-4 (path defense) | 2 | ~15 | 合法路径被拒 | 易 |
| Phase 3-1 (llm DRY) | 1 | ~100 | 所有 LLM 调用 | 高 |
| Phase 3-2 (signal parser) | 2 | ~200 | Agent 信号识别 | 高 |
| Phase 3-3 (schema registry) | 2 | ~500 | 工具注册 | 中高 |

---

*计划结束。建议从 Phase 0 开始逐步推进，每个 Phase 完成后全量验证再继续下一阶段。*
