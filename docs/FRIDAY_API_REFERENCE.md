# Friday API 参考文档

本文档供 ScholarAgent 开发时快速查阅 Friday 大模型平台的调用配置。

## 基础配置

| 字段 | 值 |
|------|---|
| AppId (即 API Key) | `2003426817264898139` |
| Base URL | `https://aigc.sankuai.com/v1/openai/native` |
| 接口标准 | OpenAI Chat Completions 兼容 |
| 认证方式 | `Authorization: Bearer {AppId}` |

对应 `.env` 配置：
```bash
OPENAI_API_KEY=2003426817264898139
OPENAI_BASE_URL=https://aigc.sankuai.com/v1/openai/native
LLM_MODEL=gpt-4o
```

## 推荐模型

当前 AppId (2003426817264898139) 实测可用模型：

| 模型名 | 可用 | 特点 | 适用场景 |
|--------|------|------|---------|
| **gpt-4.1** | ✅ | OpenAI 最新，推理+指令遵从+FC 强 | **认知循环主力 (推荐)** |
| **gpt-4.1-mini** | ✅ | 成本低、速度快 | 快速迭代/测试 |
| gpt-4o-mini | ✅ | 轻量 | 简单任务 |
| deepseek-v3-friday | ✅ | 自部署、低成本 | 初期测试用，RPM 限制较严 |
| deepseek-r1-friday | ✅ | 有思维链 | 需要显式推理过程时 |
| glm-4.5-flash | ✅ | 免费 | 极低成本场景 |
| gpt-4o | ❌ | — | AppId 未开通 |
| claude 系列 | ❌ | — | AppId 未开通 |
| qwen3-max | ❌ | — | AppId 未开通 |

> **为什么从 deepseek-v3-friday 切到 gpt-4.1？**
> 1. RPM 限制低，频繁 429
> 2. Function calling 遵从度 gpt-4.1 明显更好
> 3. 认知循环需要模型有强自主规划能力，实测 gpt-4.1 在深度追查行为上远优于 deepseek-v3
> 4. Phase 1 实测：检出率从 4/6 → 5/6，轮次从 3 → 14（主动深入）

## 调用示例 (curl)

```bash
curl -X POST 'https://aigc.sankuai.com/v1/openai/native/chat/completions' \
  -H 'Authorization: Bearer 2003426817264898139' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gpt-4o",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "Hello"}
    ],
    "temperature": 0.1,
    "max_tokens": 4096
  }'
```

## Python 代码调用

项目中的 `llm/client.py` 已封装好，只需设置 `.env` 即可：

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    api_key="2003426817264898139",
    base_url="https://aigc.sankuai.com/v1/openai/native",
)

# Function calling 正常使用 tools 参数即可
response = await client.chat.completions.create(
    model="gpt-4o",
    messages=[...],
    tools=[...],
    tool_choice="auto",
)
```

## 注意事项

1. **RPM 限制**：个人 AppId 默认 RPM 较低，遇到 429 时客户端已有指数退避重试
2. **数据安全**：外采模型(GPT系列)数据会发送到外部，不要传入公司 C2+ 敏感信息
3. **无线下环境**：Friday 不支持 Dev/Test 环境，只能在线上调用
4. **计费**：通过 [Friday 控制台](https://friday.sankuai.com/budget/myUsage) 查看用量

## 相关文档链接

- [Friday 接口文档 (One-API)](https://km.sankuai.com/collabpage/1580139661)
- [Friday 接入流程 SOP](https://km.sankuai.com/collabpage/1560353909)
- [Friday 模型广场](https://aigc.sankuai.com/ml/modelPlaza)
- [提升 RPM 申请](https://km.sankuai.com/collabpage/1788662251)
- [Friday Responses API](https://km.sankuai.com/collabpage/2720941091) — 新一代 Agent API (未来可考虑)
