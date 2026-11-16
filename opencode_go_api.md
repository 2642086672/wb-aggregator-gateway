# OpenCode Go API 接口规格

OpenCode Go 是 OpenCode 的订阅制模型网关（$10/月 Go，$40/月 Go Plus）。API 走 OpenAI 兼容协议，可用任意 OpenAI SDK / 兼容客户端直接调用。

- 控制台（订阅 / 取 key / 查额度）：https://opencode.ai/console
- 文档：https://opencode.ai/docs/console/go/
- 模型目录（实时，无需认证）：https://opencode.ai/inference/go/v1/models

---

## 1. 认证

两个头缺一不可：

| 头 | 值 | 说明 |
|---|---|---|
| `Authorization` | `Bearer <GO_API_KEY>` | Go API Key，形如 `oc_sk_xxxxxxxx` |
| `x-opencode-session` | 任意稳定字符串 | 会话 ID，用于路由优化与提示缓存；同一会话保持一致即可 |

只给 `Authorization` 会返回 `400 MissingSessionID`；只给 session 会返回 `401 Go requires a credential`。

## 2. Base URL 与端点

统一前缀：`https://opencode.ai/inference/go`

| 协议 | Base URL | 端点 | 兼容 SDK |
|---|---|---|---|
| OpenAI Chat Completions | `https://opencode.ai/inference/go/openai/v1` | `POST /chat/completions` | `@ai-sdk/openai-compatible` |
| OpenAI Responses | `https://opencode.ai/inference/go/openai/v1` | `POST /responses` | `@ai-sdk/openai` |
| Anthropic Messages | `https://opencode.ai/inference/go/anthropic/v1` | `POST /messages` | `@ai-sdk/anthropic` |
| Google Generative | `https://opencode.ai/inference/go/google/v1beta` | `POST /models/{id}:generateContent` | `@ai-sdk/google` |
| 模型列表 | `https://opencode.ai/inference/go/v1` | `GET /models` | — |

**跨协议说明**：每个模型标了 native 协议，但所有文本模型都能跑其他协议（服务端翻译）。实测 `gpt-6-luna` 用 Chat Completions 和 Responses 都返回 200；`glm-5.3-flash` 用 Responses 也返回 200。

## 3. 模型 ID（2026-11-07 实时目录，共 30 个）

### Chat Completions 原生（18）
```
deepseek-v4.1-flash  deepseek-v4-pro  glm-5.2  glm-5.3  glm-5.3-flash
hy3  hy4  hy4-preview  kimi-k2.7-code  kimi-k3  kimi-k3.1-pro
mimo-v2.6-flash  mimo-v2.6-pro  minimax-m3.1-flash  longcat-2.0
step-5-preview  beam-free  iceman-contributor-free
```

### Responses 原生（6）
```
gpt-6-luna  gpt-5.6-luna  grok-4.7
muse-spark-1.2-contributor  muse-spark-1.3-contributor  crouton-alpha-free
```

### Anthropic Messages 原生（5）
```
claude-haiku-5-5  minimax-m3  qwen3.8-max  qwen3.8-flash  qwen3.7-plus
```

### Google 原生（1）
```
gemini-4-argon
```

### 地区限制
- **OpenAI 系**（`gpt-6-luna` / `gpt-5.6-luna` / `grok-4.7`）：出口 IP 必须是非受限地区（US/JP/SG/EU 等）。CN、HK、RU 会返回 `403 unsupported_country_region_territory`。
- 其余模型无地区限制。

## 4. 请求示例

### 4.1 curl — Chat Completions（最常用）

```bash
export GO_KEY="oc_sk_你的key"

curl https://opencode.ai/inference/go/openai/v1/chat/completions \
  -H "Authorization: Bearer $GO_KEY" \
  -H "x-opencode-session: my-session-001" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-6-luna",
    "messages": [{"role": "user", "content": "say OK"}]
  }'
```

成功返回（标准 OpenAI 格式）：

```json
{
  "id": "chatcmpl-e9a1783ddc814548915d37052cfc88f6",
  "object": "chat.completion",
  "created": 1794043622,
  "model": "gpt-6-luna",
  "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
  "usage": {"prompt_tokens": 8, "completion_tokens": 5, "total_tokens": 13, "cost": 0,
    "opencode": {"billing_source": "go", "list_cost": 0.0000033, "allowance": 0.0000132}}
}
```

### 4.2 curl — Responses

```bash
curl https://opencode.ai/inference/go/openai/v1/responses \
  -H "Authorization: Bearer $GO_KEY" \
  -H "x-opencode-session: my-session-001" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-6-luna", "input": "say OK"}'
```

### 4.3 Python — openai SDK

```python
from openai import OpenAI

client = OpenAI(
    api_key="oc_sk_你的key",
    base_url="https://opencode.ai/inference/go/openai/v1",
    default_headers={"x-opencode-session": "my-session-001"},
)

# Chat Completions
r = client.chat.completions.create(
    model="gpt-6-luna",
    messages=[{"role": "user", "content": "say OK"}],
)
print(r.choices[0].message.content)

# Responses
r2 = client.responses.create(model="gpt-6-luna", input="say OK")
print(r2.output_text)
```

### 4.4 第三方客户端配置（Cherry Studio / ChatBox / LobeChat 等）

| 项 | 值 |
|---|---|
| API 类型 | OpenAI 兼容 / OpenAI API |
| Base URL | `https://opencode.ai/inference/go/openai/v1` |
| API Key | `oc_sk_你的key` |
| 自定义请求头 | `x-opencode-session: my-session-001` |
| 模型 ID | 填模型目录里的 ID，如 `gpt-6-luna`、`glm-5.3-flash` |

客户端不支持自定义头时，用支持自定义头的（Cherry Studio、ChatBox 均支持），或在请求前用代理注入。

## 5. 走代理调用（OpenAI 系模型必须）

出口 IP 在非受限地区才可用 `gpt-6-luna` / `gpt-5.6-luna` / `grok-4.7`。

```bash
# curl 显式挂代理（以 Clash Verge 7897 为例）
curl -x http://127.0.0.1:7897 https://opencode.ai/inference/go/openai/v1/chat/completions \
  -H "Authorization: Bearer $GO_KEY" \
  -H "x-opencode-session: my-session-001" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-6-luna", "messages": [{"role": "user", "content": "hi"}]}'

# 验证出口
curl -s -x http://127.0.0.1:7897 https://ipinfo.io/json

# Python 走代理
# export HTTPS_PROXY=http://127.0.0.1:7897
```

节点选择：**避开 CN / HK / RU**，选 US / JP / SG / EU。

## 6. 额度与计费

- Go 订阅含月度额度（按模型不同，约 6 倍于订阅价的用量）。
- 响应 `usage.opencode` 字段实时回计量：`list_cost`（按目录价）、`allowance`（剩余额度）、`billing_source: "go"`。
- 超出月度额度后，可在控制台开启「Use balance」回退到 Zen 余额，或等下月重置。
- 免费额度用尽时，免费模型仍可在 OpenCode 客户端内使用。

## 7. 常见错误

| 状态码 | 错误 | 原因 / 处理 |
|---|---|---|
| 400 | `MissingSessionID` | 缺 `x-opencode-session` 头 |
| 401 | `authentication_error` | key 无效或未带 |
| 403 | `unsupported_country_region_territory` | 出口 IP 在受限地区，换非受限节点 |
| 429 | `FreeUsageLimitError` | Zen 免费层限流（非 Go key 场景） |
