# WB Aggregator Gateway Developer Notes

> Sanitized repository copy. Credentials must be supplied through environment variables; do not commit local runtime configuration.

> 轻量化 OpenAI + Anthropic 兼容聚合网关：多 provider 聚合、按模型自动代理/直连、带网页管理端。
> 单文件 Python（标准库 + requests），无数据库、无前端构建。
> **当前版本：v1.2.0（2026-11-07）**

---

## 1. 项目概述

**目标**：给 WorkBuddy / 任意 OpenAI 或 Anthropic 兼容客户端提供统一入口，背后聚合多个 provider（OpenCode Go、LongCat 等），并解决三类问题：

1. **地区限制**：OpenAI/xAI/Meta 系模型（gpt-6-luna、grok-4.7 等）需要非受限地区出口；国产模型（glm/kimi/deepseek）直连更快。
2. **协议差异**：WorkBuddy 等客户端会在请求里塞私有字段（`startsNewUserRequest`、`traceId`、`messageId` 等），OpenCode Go 会拒绝；需要在网关层清洗。
3. **双协议入口**：同时提供 OpenAI（Chat Completions / Responses）和 Anthropic（Messages）端点；OpenCode Go 支持跨协议翻译，同一模型可以用任一协议调用。
4. **入站鉴权**：客户端用你自己的网关端点 + 网关 key 接入；各 provider 的真实 key 只保存在网关配置里，不下发给客户端。

**形态**：单文件 `gateway.py` + 配置 `gateway_config.json`，监听 `127.0.0.1:8787`。

### 1.1 客户端怎么填（快速接入）

| 客户端类型 | Base URL / API 地址 | API Key | 模型 |
|---|---|---|---|
| OpenAI 兼容（WorkBuddy、Cherry Studio、OpenAI SDK） | `http://127.0.0.1:8787/v1` | 管理端「客户端接入密钥」里的网关 key（`sk-gw-...`） | `/v1/models` 返回的 id，如 `gpt-6-luna`、`glm-5.3-flash` |
| Anthropic 兼容（Claude Code、Anthropic SDK） | `http://127.0.0.1:8787` | 同上 | 同上（OpenCode 支持跨协议） |

- **只填 Base URL，不要填完整的 `/v1/chat/completions`**；客户端会自己拼路径。如果某个客户端把字段叫「接口地址」且要求完整路径，再填 `http://127.0.0.1:8787/v1/chat/completions`。
- 如果 Anthropic 客户端强制要求 Base URL 带 `/v1`，填 `http://127.0.0.1:8787/v1`，网关同时支持 `/messages`。
- 客户端只拿网关 key；不要填 `oc_sk_...` / `ak_...` 等上游 provider key。

---

## 2. 文件结构

```
.
├── gateway.py                  # 网关主程序（含管理端前端）
├── gateway_config.example.json # 脱敏配置模板
├── requirements.txt            # Python 依赖
├── .env.example                # 环境变量模板（不含值）
├── .gitignore                  # 排除本地配置、密钥、日志和运行数据
└── README.md                   # 快速启动
```

运行时在本地复制 `gateway_config.example.json` 为 `gateway_config.json`，并设置 `.env` 或进程环境变量。不要提交 `gateway_config.json`、`.env`、`gateway_usage.json`、日志或请求抓包。

**依赖**：Python 3.10+、`requests`（`pip install -r requirements.txt`）。

**启动**：
```powershell
Copy-Item gateway_config.example.json gateway_config.json
Copy-Item .env.example .env
# 编辑 .env 填入本机密钥
pip install -r requirements.txt
python gateway.py
```

`.env` 示例（真实值只放在本机）：
```dotenv
OPENCODE_GO_API_KEY=your-opencode-go-key
LONGCAT_API_KEY=your-longcat-key
GATEWAY_CLIENT_KEYS=your-client-key
```
网关启动时读取 `.env`；已有系统环境变量优先。客户端 key 环境变量支持逗号分隔多把。

---

## 3. 架构与核心逻辑

### 3.1 服务骨架

```python
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import requests

CFG = load_config()          # 全局配置
PROVIDERS = CFG["providers"] # provider 列表
DEFAULT_PROXY = CFG["proxy"] # 全局代理（Clash）

class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def _handle(self): ...        # OpenAI 兼容转发
    def _admin_get(self): ...     # 管理端 GET
    def _admin_post(self): ...    # 管理端 POST
    def _test_model(self, model): ...  # 连通性测试
    do_GET = ...   # 路由分发
    do_POST = ...
    do_HEAD = ...
```

### 3.2 路由表

| 路径 | 方法 | 功能 |
|---|---|---|
| `/v1/chat/completions` | POST | OpenAI Chat Completions（自动识别 Responses 格式并纠正） |
| `/v1/responses` | POST | OpenAI Responses |
| `/v1/messages` | POST | **Anthropic Messages**（同样支持 `/messages`、`/anthropic/v1/messages`） |
| `/v1/messages/count_tokens` | POST | **Anthropic token 计数**（转发到上游 `/messages/count_tokens`） |
| `/v1/models` | GET | 聚合模型列表；默认 OpenAI 格式，带 `anthropic-version` 头时返回 Anthropic Models 格式 |
| `/v1/models-info` | GET | 调试用模型元数据列表 |
| `/admin` | GET | 管理端网页（HTML） |
| `/admin/api/config` | GET/POST | 读取/保存配置 |
| `/admin/api/status` | GET | 运行状态 |
| `/admin/api/logs` | GET | 日志（`?lines=N`） |
| `/admin/api/test` | GET | 测试单个模型（`?model=xxx`） |
| `/admin/api/reload` | POST | 热重载配置 |
| `/` | GET | 健康检查 |

### 3.3 核心流程（`_handle`）

```
0. 校验客户端网关 key（client_auth）；未通过直接 401（OpenAI/Anthropic 各自错误格式）
1. 读 body → 解析 model
2. 判断协议：
   - path 以 /messages 或 /messages/count_tokens 结尾 → Anthropic
   - 否则看 body 有 input/instructions 且无 messages → OpenAI Responses
   - 其余 → OpenAI Chat Completions
3. find_provider(model) → 找 provider
4. model_map 映射出 effective_model（路由/代理/上游都用它）
5. 清洗：Anthropic → sanitize_anthropic()；OpenAI → sanitize_body()
6. 决定上游端点：
   - Anthropic → {anthropic_base_url}/messages 或 /messages/count_tokens
   - Responses → /responses
   - Chat → /chat/completions
7. 组头：透传客户端头 + provider 自定义头（{session} 替换）+ 补 x-opencode-session
8. 注入 provider 配置的 api_key（默认覆盖客户端 Authorization；Anthropic 同时补 x-api-key；见 pass_client_auth）
9. should_proxy(provider, effective_model) → 决定走代理还是直连
10. requests.post(upstream, stream=True) → 流式转发
11. chunked 编码回写客户端；上游 4xx/5xx 会记录响应片段
```

### 3.4 关键函数

**模型路由**：
```python
def find_provider(model_id):
    for p in PROVIDERS:
        if model_id in p.get("models", []):
            return p
    # 前缀匹配兜底
    for p in PROVIDERS:
        for m in p.get("models", []):
            if model_id.startswith(m) or m.startswith(model_id):
                return p
    return None
```

**代理决策**（必须使用 `model_map` 映射后的真实 id）：
```python
def should_proxy(provider, model_id):
    if model_id in provider.get("proxy_models", []):
        return True
    return bool(provider.get("use_proxy", False))

# _handle 中：
effective_model = (provider.get("model_map") or {}).get(model_id, model_id)
use_px = should_proxy(provider, effective_model)
proxies = {"http": DEFAULT_PROXY, "https": DEFAULT_PROXY} if use_px else None
```
> 1.2.0 修复：早期版本用映射前的 id 判断，`gpt-6-luna-responses -> gpt-6-luna` 这类别名会误走直连。

**字段清洗**（关键！WorkBuddy 兼容）：
```python
STD_MSG_KEYS = {"role","content","name","tool_calls","tool_call_id","function_call","audio","refusal"}
STD_INPUT_KEYS = {"type","role","content","name","call_id","arguments","output","status","id",
                  "summary","encrypted_content","annotations","text","logprobs","detail","refusal",
                  "usage","error","index","delta","finish_reason","input_tokens","output_tokens",
                  "total_tokens","input_tokens_details","output_tokens_details","cached_tokens",
                  "reasoning_tokens","message"}
STD_TOP_KEYS = {
    # Chat Completions
    "model", "messages", "tools", "tool_choice", "max_tokens", "max_completion_tokens",
    "stream", "stream_options", "temperature", "top_p", "n", "stop", "presence_penalty",
    "frequency_penalty", "logit_bias", "logprobs", "top_logprobs", "user",
    "response_format", "seed", "parallel_tool_calls", "metadata", "store",
    "reasoning_effort", "verbosity", "service_tier", "prediction", "modalities",
    "audio", "web_search_options", "functions", "function_call",
    # Responses
    "instructions", "input", "include", "max_output_tokens", "text", "truncation",
    "previous_response_id", "background", "conversation", "max_tool_calls",
    "prompt_cache_key", "safety_identifier", "reasoning", "effort",
}

def sanitize_body(body, provider):
    j = json.loads(body)
    # 模型 id 映射（WorkBuddy 自定义 id -> 真实 id）
    if j.get("model") in provider.get("model_map", {}):
        j["model"] = provider["model_map"][j["model"]]
    # 删 messages 里的非标准字段
    for m in j.get("messages", []):
        for k in list(m.keys()):
            if k not in STD_MSG_KEYS: del m[k]
    # 删 input 里的非标准字段（Responses 格式）
    for m in j.get("input", []):
        for k in list(m.keys()):
            if k not in STD_INPUT_KEYS: del m[k]
    # 删顶层非标准字段
    for k in list(j.keys()):
        if k not in STD_TOP_KEYS: del j[k]
    return json.dumps(j, ensure_ascii=False).encode()
```

**注意（1.2.0 修复）**：早期 `STD_TOP_KEYS` 漏了 `max_output_tokens`，WorkBuddy 的 Responses 请求会被静默截断成默认输出上限。现已补齐 Responses 全套顶层字段，并增加 `reasoning_effort`、`logprobs` 等 Chat Completions 字段。

**协议格式自动纠正**：WorkBuddy 对 `openai-responses` 条目会构造 Responses 格式 body（含 `input`/`instructions`），但仍请求 `/chat/completions` 路径。网关检测 body 格式，自动转发到 `/responses`：
```python
is_responses = ("input" in j or "instructions" in j) and "messages" not in j
upstream_path = "/responses" if is_responses else "/chat/completions"
```

**会话头注入**（OpenCode Go 必需）：
```python
# provider.headers 里配 {"x-opencode-session": "{session}"}
# {session} 替换为客户端 x-conversation-id（WorkBuddy 会带，UUID v4，会话内固定）
sess = self.headers.get("x-conversation-id") or self.headers.get("x-opencode-session") or f"gw-{uuid.uuid4()}"
```

**API Key 注入**（1.2.0 新增）：
```python
# 默认：用配置里的 key 覆盖客户端带来的 Authorization
# - OpenAI 端点用 provider.api_key
# - Anthropic 端点优先用 provider.anthropic_api_key，留空回落 api_key
# 例外：
#   1) provider.headers 里显式配置了 Authorization / api-key / x-api-key → 尊重自定义头
#   2) provider.pass_client_auth = true → 保留客户端的鉴权
key = provider_api_key(provider, anthropic=is_anthropic)
auth = auth_value(key)   # 自动补 "Bearer " 前缀
if auth:
    set_header(fwd, "Authorization", auth)
```
> 这样 WorkBuddy 里填的是 `client_auth.keys` 里的**网关 key**，真实 provider key 全部集中在 `gateway_config.json`，多 provider 聚合才不会串 key。之前主转发路径只透传客户端 Authorization，配置里的 `api_key` 仅用于管理端测试，和 opencode-go 直连测试会 401。

### 3.5 Anthropic Messages 端点

**客户端配置（两套端点）**：

| 客户端 | Base URL | 实际请求 |
|---|---|---|
| OpenAI SDK / WorkBuddy / Cherry Studio | `http://127.0.0.1:8787/v1` | `/v1/chat/completions`、`/v1/responses` |
| Anthropic SDK / Claude Code | `http://127.0.0.1:8787` | `/v1/messages` |
| 兼容 `/v1` 前缀的 Anthropic 客户端 | `http://127.0.0.1:8787/v1` | `/messages` |

```bash
# 你自己的网关 key（管理端「客户端接入密钥」里查看/修改）
export GW_KEY="sk-gw-..."

# OpenAI 兼容
export OPENAI_BASE_URL=http://127.0.0.1:8787/v1
export OPENAI_API_KEY="$GW_KEY"

# Anthropic 兼容
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
export ANTHROPIC_API_KEY="$GW_KEY"
```

**入口**：

| 端点 | 说明 |
|---|---|
| `POST /v1/messages` | Anthropic Messages（Claude SDK / Claude Code 默认路径） |
| `POST /messages` | 同上，兼容 base_url 填 `http://127.0.0.1:8787/v1` 的客户端 |
| `POST /anthropic/v1/messages` | 同上，显式协议前缀 |
| `POST /v1/messages/count_tokens` | 转发到上游 `/messages/count_tokens` |

**配置**：provider 增加 `anthropic_base_url` 和可选的 `anthropic_api_key`：
```json
{
  "name": "opencode-go",
  "base_url": "https://opencode.ai/inference/go/openai/v1",
  "anthropic_base_url": "https://opencode.ai/inference/go/anthropic/v1",
  "api_key": "${OPENCODE_GO_API_KEY}",
  "anthropic_api_key": "${OPENCODE_GO_API_KEY}"
}
```
未配置时，仅对 `opencode.ai` 的 `/openai/` base 自动推导 `/anthropic/`；其他 provider 会返回 400，避免猜错端点。

**请求清洗**：`sanitize_anthropic()` 只保留标准顶层字段（`model`/`messages`/`system`/`max_tokens`/`stream`/`tools`/`tool_choice`/`thinking` 等），消息只保留 `role`/`content`，模型 id 仍走 `model_map`。

**鉴权**：
- 默认注入 `x-api-key: <key>` 和 `Authorization: Bearer <key>`（OpenCode Go 两种都认）。
- `<key>` 优先取 `provider.anthropic_api_key`；留空则回落到 `provider.api_key`。这样 Anthropic 可以单独配一把 key。
- 客户端带来的 key 只用于网关鉴权，随后丢弃并换成 provider key；`pass_client_auth=true` 或 `provider.headers` 显式配置鉴权时保留客户端 key。
- 客户端没带 `anthropic-version` 时补 `2023-06-01`。

**响应**：JSON 和 SSE（`event: ...` / `data: ...`）都透明转发；手写 chunked 只负责分块，不改变事件内容。

**模型列表**：`GET /v1/models` 带 `anthropic-version` 头时返回 Anthropic Models 格式（`data` / `has_more` / `first_id` / `last_id`），否则返回 OpenAI 格式。

**跨协议**：OpenCode Go 会把文本模型在 Chat/Responses/Anthropic 之间翻译，所以已配置的模型（如 `glm-5.3-flash`、`gpt-6-luna`）都能通过 `/v1/messages` 调用；Anthropic 原生模型（`claude-haiku-5-5` 等）加进 `models` 即可。

### 3.6 客户端接入鉴权（网关自己的 key）

**核心模型**：模型在网关内部聚合，**上游 provider key 留在网关里**；客户端只用**你自己的网关端点 + 你自己的网关 key**。

| 客户端 | 带 key 的方式 |
|---|---|
| OpenAI SDK / WorkBuddy / Cherry Studio | `Authorization: Bearer <网关 key>` |
| Anthropic SDK / Claude Code | `x-api-key: <网关 key>` |
| 其他兼容客户端 | 也接受 `api-key: <网关 key>` |

网关校验通过后，会把客户端的 key 丢弃，换成对应 provider 的 `api_key` / `anthropic_api_key` 再请求上游。

**配置**：
```json
"client_auth": {
  "enabled": true,
  "keys": ["sk-gw-your-own-key"]
}
```
- `enabled: false`：不校验（仅本地调试用）。
- `enabled: true` 且 `keys` 为空：保存配置会被拒绝，避免误开成公开网关。
- `keys` 可以放多把，便于轮换；任意一把匹配即放行。

**401 响应格式**：
```json
// OpenAI 端点
{"error":{"message":"Invalid API key","type":"invalid_request_error","code":"invalid_api_key"}}

// Anthropic 端点
{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}
```

**管理**：管理端「客户端接入密钥」卡片可启停鉴权、增删 key、生成随机 key、复制第一个 key。管理端 `/admin*` 仍只监听 `127.0.0.1`，不校验该 key。

---

## 4. 配置格式（gateway_config.json）

```json
{
  "listen": {"host": "127.0.0.1", "port": 8787},
  "proxy": "http://127.0.0.1:7897",
  "client_auth": {
    "enabled": true,
    "keys": ["${GATEWAY_CLIENT_KEYS}"]
  }, 
  "providers": [
    {
      "name": "opencode-go",
      "base_url": "https://opencode.ai/inference/go/openai/v1",
      "anthropic_base_url": "https://opencode.ai/inference/go/anthropic/v1",
      "api_key": "${OPENCODE_GO_API_KEY}",
      "anthropic_api_key": "",
      "needs_session": true,
      "headers": {"x-opencode-session": "{session}"},
      "model_map": {"gpt-6-luna-responses": "gpt-6-luna"},
      "proxy_models": ["gpt-6-luna", "gpt-5.6-luna", "grok-4.7"],
      "models": ["gpt-6-luna", "glm-5.3-flash", "..."],
      "model_meta": {
        "gpt-6-luna": {
          "context": 1050000, "input": 922000, "output": 128000,
          "images": true, "tools": true,
          "reasoning": ["low","medium","high","xhigh","max"]
        }
      }
    },
    {
      "name": "longcat",
      "base_url": "https://api.longcat.chat/openai/v1",
      "api_key": "${LONGCAT_API_KEY}",
      "needs_session": false,
      "use_proxy": false,
      "proxy_models": [],
      "models": ["LongCat-2.5-Preview"]
    }
  ]
}
```

**字段说明**：

| 字段 | 说明 |
|---|---|
| `client_auth.enabled` | 是否校验客户端请求的网关 key；`true` 时 `/v1/*` 必须带 `client_auth.keys` 里的 key |
| `client_auth.keys` | 允许接入的网关 key 列表；支持 `${GATEWAY_CLIENT_KEYS}`，环境变量可用逗号分隔多把 key |
| `proxy` | 全局代理地址（Clash 混合端口），留空则全部直连 |
| `providers[].base_url` | OpenAI 兼容端点，**不带** `/chat/completions` |
| `providers[].anthropic_base_url` | Anthropic Messages 端点，**不带** `/messages`；不配置则不支持该 provider 的 `/v1/messages`（opencode.ai 可自动推导） |
| `providers[].api_key` | OpenAI 上游 key；使用 `${OPENCODE_GO_API_KEY}` / `${LONGCAT_API_KEY}` 环境变量引用 |
| `providers[].anthropic_api_key` | Anthropic `/v1/messages` 专用 key；支持环境变量引用，留空则回落到 `api_key` |
| `providers[].pass_client_auth` | 默认 `false`：主转发用配置 key 覆盖客户端 Authorization；`true` 则保留客户端鉴权 |
| `providers[].needs_session` | 是否强制注入 `x-opencode-session` |
| `providers[].headers` | 自定义头，`{session}` 占位符会被替换 |
| `providers[].model_map` | WorkBuddy 模型 id → 真实模型 id 映射 |
| `providers[].proxy_models` | 这些模型走代理；其余直连 |
| `providers[].use_proxy` | 该 provider 全部模型走代理 |
| `providers[].model_meta` | 模型能力元数据（上下文/多模态/思考），管理端展示用 |

### 4.1 模型能力清单（2026-11-07 快照）

> 数据来源：`gateway_config.json` 的 `model_meta`；管理端模型标签 hover 也会显示同样内容。
>
> - **上下文**：总窗口大小；**输入上限**仅在 provider 明确给出时列出，`—` 表示未单独标注（按上下文窗口计）。
> - **多模态**：是否支持图片输入；**工具**：是否支持 function calling。
> - **思考强度**：客户端可传的 `reasoning_effort` 档位；`—` 表示未声明可调档位，不代表模型内部不思考。
> - **出口**：网关按 `proxy_models` / `use_proxy` 实际选择的线路。

| 模型 | Provider | 上下文 | 输入上限 | 输出上限 | 多模态 | 工具 | 思考强度 | 出口 |
|---|---|---:|---:|---:|:---:|:---:|---|---|
| `gpt-6-luna` | opencode-go | 1,050,000 | 922,000 | 128,000 | ✅ | ✅ | low / medium / high / xhigh / max | 🌐 代理 |
| `gpt-5.6-luna` | opencode-go | 1,050,000 | 922,000 | 128,000 | ✅ | ✅ | low / medium / high / xhigh / max | 🌐 代理 |
| `grok-4.7` | opencode-go | 500,000 | — | 500,000 | ✅ | ✅ | low / medium / high / xhigh | 🌐 代理 |
| `glm-5.3` | opencode-go | 1,000,000 | — | 131,072 | ❌ | ✅ | low / high / max | 直连 |
| `glm-5.3-flash` | opencode-go | 1,000,000 | — | 131,072 | ✅ | ✅ | low / high / max | 直连 |
| `glm-5.2` | opencode-go | 1,000,000 | — | 131,072 | ❌ | ✅ | high / max | 直连 |
| `kimi-k3` | opencode-go | 1,048,576 | — | 131,072 | ✅ | ✅ | max | 直连 |
| `kimi-k3.1-pro` | opencode-go | 1,048,576 | — | 131,072 | ✅ | ✅ | low / high / max | 直连 |
| `kimi-k2.7-code` | opencode-go | 262,144 | — | 262,144 | ✅ | ✅ | — | 直连 |
| `deepseek-v4-pro` | opencode-go | 1,000,000 | — | 384,000 | ❌ | ✅ | high / max | 直连 |
| `deepseek-v4.1-flash` | opencode-go | 1,000,000 | — | 384,000 | ✅ | ✅ | low / high / max | 直连 |
| `minimax-m3.1-flash` | opencode-go | 1,000,000 | — | 512,000 | ✅ | ✅ | low / medium / high / xhigh / max | 直连 |
| `longcat-2.0` | opencode-go | 1,000,000 | — | 131,072 | ❌ | ✅ | — | 直连 |
| `step-5-preview` | opencode-go | 1,000,000 | 1,000,000 | 65,536 | ✅ | ✅ | low / medium / high | 直连 |
| `hy3` | opencode-go | 256,000 | 192,000 | 128,000 | ❌ | ✅ | low / high | 直连 |
| `hy4` | opencode-go | 1,048,576 | 983,010 | 65,536 | ✅ | ✅ | high | 直连 |
| `mimo-v2.6-pro` | opencode-go | 1,048,576 | — | 131,072 | ✅ | ✅ | — | 直连 |
| `mimo-v2.6-flash` | opencode-go | 1,048,576 | — | 131,072 | ✅ | ✅ | — | 直连 |
| `beam-free` | opencode-go | 1,048,576 | — | 131,072 | ❌ | ✅ | low / medium / high / xhigh / max | 直连 |
| `iceman-contributor-free` | opencode-go | 262,144 | — | 131,072 | ❌ | ✅ | — | 直连 |
| `LongCat-2.5-Preview` | longcat | 1,000,000 | 1,000,000 | 128,000 | ✅ | ✅ | low / medium / high | 直连 |

**补充**：`proxy_models` 里还配置了 `muse-spark-1.2-contributor`、`muse-spark-1.3-contributor`、`crouton-alpha-free` 三个走代理的模型，但当前没有加入 `models` 列表，所以 `/v1/models` 不会显示。需要使用它们时，把模型名补进对应 provider 的 `models` 和 `model_meta` 即可，网关无需改代码。

**协议可用性**：`opencode-go` 同时支持 OpenAI 和 Anthropic 两套端点；`longcat` 目前只配置了 OpenAI base，其模型走 `/v1/messages` 会返回 400。要给某个 provider 开 Anthropic，补 `anthropic_base_url`（以及可选的 `anthropic_api_key`）即可。

---

## 5. 管理端 API

| 端点 | 方法 | 说明 |
|---|---|---|
| `/admin/api/config` | GET | 返回完整配置 JSON |
| `/admin/api/config` | POST | 保存配置（body 为完整配置 JSON），写文件 + 热重载 |
| `/admin/api/status` | GET | `{ok, version, providers, models, proxy_models, proxy, listen, uptime}` |
| `/admin/api/logs?lines=150` | GET | `{lines: [...]}` 日志行数组 |
| `/admin/api/test?model=xxx` | GET | `{ok, model, provider, route, reply}` 或 `{ok:false, error}` |
| `/admin/api/reload` | POST | 热重载配置 |

**保存配置**（POST `/admin/api/config`）：
```python
new_cfg = json.loads(raw)
err = validate_config(new_cfg)   # 结构校验，返回错误字符串或 None
if err:
    return 400, {"ok": False, "error": err}
tmp = CONFIG_PATH + ".tmp"       # 原子写入，避免写一半损坏配置
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(new_cfg, f, ensure_ascii=False, indent=2)
os.replace(tmp, CONFIG_PATH)
reload_config()                  # 重新读取 CFG/PROVIDERS/DEFAULT_PROXY
```
校验内容：`providers` 非空、name 唯一且必填、`base_url`/`anthropic_base_url` 必须 `http(s)://`、`models` 字符串数组、`proxy_models` 字符串数组、`headers`/`model_map`/`model_meta` 必须是对象、`listen.port` 1–65535。

---

## 6. 管理端前端（ADMIN_HTML）

**形态**：单 HTML 字符串内嵌在 `gateway.py` 里（`ADMIN_HTML` 常量），原生 HTML/CSS/JS，无外部依赖。

**功能**：
- **总览**：Provider 数、模型总数、走代理模型数、代理地址、监听地址、OpenAI 端点、Anthropic 端点、运行时长
- **客户端接入密钥**：启停鉴权、每行一个 key、生成随机 key、复制第一个 key
- **Provider 卡片**：名称、base_url、key（脱敏）、模型标签（点击测试）
- **模型标签**：hover 显示 tooltip（上下文/多模态/工具/思考强度）；走代理的带 🌐
- **模型能力表**：页面直接展示每个模型的上下文、输出上限、多模态、工具、思考强度、出口，数据来自 `model_meta`
- **测试**：点击单个模型测试；「⚡ 一键测试」批量（并发 5 路）
- **红绿标记**：`.tag.ok`（绿）/`.tag.fail`（红）/`.tag.testing`（灰）
- **Provider 管理**：添加/编辑/删除（模态框表单）
- **双协议配置**：表单可填 OpenAI Base URL/API Key 和 Anthropic Base URL/API Key；Anthropic key 留空时复用 OpenAI key
- **模型元数据编辑**：表单里的「模型元数据（JSON）」可直接维护 `model_meta`；编辑 Provider 时保留原有 `model_meta`/未知字段，不再丢失
- **保存校验**：JS 侧拦截非法 JSON 并 toast；服务端返回的校验错误也会显示
- **版本徽标**：标题旁显示 `v1.2.0`，用于确认页面是否为新版本
- **实时日志**：底部面板，可刷新
- **重载配置**：一键热重载
- **错误兜底**：`window.onerror` / `unhandledrejection` 把错误显示在页面上
- **不缓存**：页面响应 `Cache-Control: no-store`；所有 `fetch` 也带 `cache: 'no-store'`

**关键 JS 函数**：
```js
async function load()      // 拉配置 + 渲染 + 日志
function render()          // 渲染总览 + provider 卡片
async function testModel(el)  // 测试单个模型，更新标签颜色
async function testAll()   // 批量测试（并发 5）
async function saveProvider() // 保存/编辑 provider
async function delProvider(i) // 删除
async function reload()    // 热重载
```

**样式**：CSS 变量主题（`--bg`/`--card`/`--primary` 等），卡片式布局，响应式。

---

## 7. 开机自启

**双保险**（都用 `pythonw.exe`，无窗口）：

1. **计划任务**（推荐，可配崩溃重试）：
```powershell
$py = "C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe"
$script = "C:\Users\Administrator\WorkBuddy\2026-11-07-17-11-42\gateway.py"
$wd = "C:\Users\Administrator\WorkBuddy\2026-11-07-17-11-42"
$action = New-ScheduledTaskAction -Execute $py -Argument $script -WorkingDirectory $wd
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit 0 -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "WBAggregatorGateway" -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force
```

2. **注册表 Run 键**（免管理员兜底）：
```powershell
$val = '"C:\...\pythonw.exe" "C:\...\gateway.py"'
Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "WBAggregatorGateway" -Value $val
```

**手动控制**：
```powershell
Start-ScheduledTask -TaskName "WBAggregatorGateway"   # 启动
Stop-ScheduledTask  -TaskName "WBAggregatorGateway"    # 停止
```

---

## 8. 已知问题与坑

### 8.1 WorkBuddy 字段清洗（已解决）
WorkBuddy 的 message 里塞了 `startsNewUserRequest`、`agent`、`conversationRequestId`、`messageId`、`stepSeq`、`model`、`requestModelId`、`traceId`、`rawUsage`、`usage`、`argumentsDisplayText` 等私有字段，OpenCode Go 见到 `startsNewUserRequest` 直接 400。
**解决**：`sanitize_body` 只保留标准字段（见 3.4）。

### 8.2 WorkBuddy 协议错配（已解决）
WorkBuddy 对 `api: "openai-responses"` 的条目构造 Responses 格式 body（`input`/`instructions`/`store`），但请求 `/chat/completions` 路径。
**解决**：网关检测 body 格式，自动转发到 `/responses`。

### 8.3 管理端空白（1.2.0 找到真因）
**真因**：`ADMIN_HTML` 是普通 Python 字符串，源码里 JS 的 `'\n'` 在运行时被 Python 解释成真实换行，插进单引号字符串/`join('\n')` 后导致 `<script>` 语法错误，整个管理端脚本不执行，所以只有空白页。之前用 Node 跑的是手工抽出的脚本，才出现“检查脚本正常、浏览器空白”的假象。
**解决**：`ADMIN_HTML` 改为 raw 字符串 `r"""..."""`，并统一 JS 转义；新增 `/admin` 版本徽标（`v1.2.0`）和 `Cache-Control: no-store`，便于确认页面已更新。
**排查方法**：`curl http://127.0.0.1:8787/admin` 抽出 `<script>` 后用 `node --check` 检查；或浏览器 DevTools Console 看 SyntaxError。

### 8.4 地区限制
OpenAI/xAI/Meta 系模型（gpt-6-luna、gpt-5.6-luna、grok-4.7、muse-spark 等）在非受限地区外返回 `403 unsupported_country_region_territory`。
**解决**：`proxy_models` 配置走 Clash（127.0.0.1:7897），节点选 US/JP/SG/EU（避开 CN/HK/RU）。

### 8.5 流式转发
用 `requests.post(stream=True)` + `iter_content`，手写 chunked 编码回写。注意 `protocol_version = "HTTP/1.1"` 必须配 chunked 或 content-length。

### 8.6 管理端模型列表不显示（WorkBuddy 侧问题）
WorkBuddy 只加载通过 UI 添加的自定义模型，外部写入 `~/.workbuddy/models.json` 的条目会被忽略/覆盖（WorkBuddy 启动时用内部状态写回文件）。
**现状**：WorkBuddy 里需手动添加模型条目（指向 `http://127.0.0.1:8787/v1`），或通过 UI 添加。
**WorkBuddy 配置路径**：`C:\Users\Administrator\.workbuddy\models.json`

### 8.7 主转发不透传 provider key（1.2.0 已修）
早期 `_handle` 只透传客户端的 `Authorization`，配置里的 `api_key` 只在管理端测试用到。这样 WorkBuddy 必须直接填 provider key（暴露上游 key），多 provider 也无法各用各的 key。
**解决**：默认用 `provider.api_key` 覆盖 `Authorization`；`provider.headers` 里手写鉴权或 `pass_client_auth=true` 时例外。实测用 `Authorization: Bearer bogus` 调 `glm-5.3-flash` 仍返回 200。

### 8.8 model_map 后代理判断错误（1.2.0 已修）
`gpt-6-luna-responses` 映射成 `gpt-6-luna` 后，`should_proxy` 仍拿旧 id 判断，导致该别名误走直连并触发 403。
**解决**：先算 `effective_model`，路由、代理、管理端测试统一用它。日志会打印 `model=gpt-6-luna-responses -> gpt-6-luna route=PROXY`。

### 8.9 Responses 参数被清洗（1.2.0 已修）
`STD_TOP_KEYS` 漏了 `max_output_tokens`，WorkBuddy 传 128000 会被删掉，模型按默认上限输出。
**解决**：补齐 Responses 字段（`max_output_tokens`/`text`/`truncation`/`previous_response_id`/`background` 等）和 Chat 字段（`reasoning_effort`/`logprobs`/`service_tier` 等）。

### 8.10 编辑 Provider 丢失 model_meta（1.2.0 已修）
管理端 `saveProvider()` 以前重新构造对象，保存时会把 `model_meta` 等未在表单里的字段丢掉。
**解决**：以旧对象为基底覆盖表单字段；新增「模型元数据（JSON）」编辑框，保存前校验 JSON。

### 8.11 客户端入站没有鉴权（1.2.0 新增）
早期网关对客户端请求不校验 key，任何人都能通过 `127.0.0.1:8787` 调用（本机其他程序/浏览器页面）。另外如果客户端填的是 provider key，会直接暴露上游 key。
**解决**：`client_auth` 支持多把网关 key；OpenAI 用 `Authorization: Bearer`、Anthropic 用 `x-api-key`。校验通过后丢弃客户端 key，换 provider key 发上游。管理端可启停/生成/复制 key。

---

## 9. 测试方法

> 客户端必须带**网关自己的 key**（管理端「客户端接入密钥」里查看/修改）；provider 的真实 key 由网关替换，不要把它填到客户端。
> Windows PowerShell 下建议用 `curl.exe`，或把 JSON body 写进文件后 `-d @body.json`，避免 `\"` 被 shell 吞掉。

```bash
# 你自己的网关 key
export GW_KEY="sk-gw-..."

# 健康检查（含版本号，不需要 key）
curl http://127.0.0.1:8787/

# 模型列表（含上下文/多模态/工具/思考强度）
curl -H "Authorization: Bearer $GW_KEY" http://127.0.0.1:8787/v1/models

# 走代理的模型（gpt-6-luna）
curl -N http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $GW_KEY" \
  -H "x-conversation-id: test-1" \
  -d '{"model":"gpt-6-luna","messages":[{"role":"user","content":"hi"}],"stream":true}'

# 直连模型（glm-5.3-flash）
curl -N http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $GW_KEY" \
  -H "x-conversation-id: test-2" \
  -d '{"model":"glm-5.3-flash","messages":[{"role":"user","content":"hi"}],"stream":true}'

# 别名 + Responses 格式（WorkBuddy 场景：body 是 Responses，路径仍是 /chat/completions）
curl http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $GW_KEY" \
  -H "x-conversation-id: test-3" \
  -d '{"model":"gpt-6-luna-responses","input":"say OK","max_output_tokens":32,"stream":false}'

# Anthropic Messages（非流式）
curl http://127.0.0.1:8787/v1/messages \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -H "x-api-key: $GW_KEY" \
  -H "x-conversation-id: test-4" \
  -d '{"model":"glm-5.3-flash","max_tokens":32,"messages":[{"role":"user","content":"say OK"}]}'

# Anthropic Messages（SSE 流式）
curl -N http://127.0.0.1:8787/v1/messages \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -H "x-api-key: $GW_KEY" \
  -d '{"model":"glm-5.3-flash","max_tokens":32,"stream":true,"messages":[{"role":"user","content":"hi"}]}'

# Anthropic token 计数
curl http://127.0.0.1:8787/v1/messages/count_tokens \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -H "x-api-key: $GW_KEY" \
  -d '{"model":"glm-5.3-flash","messages":[{"role":"user","content":"hello world"}]}'

# Anthropic 格式模型列表
curl -H "anthropic-version: 2023-06-01" -H "x-api-key: $GW_KEY" http://127.0.0.1:8787/v1/models

# 管理端状态
curl http://127.0.0.1:8787/admin/api/status

# 管理端测试（会自动做 model_map 映射）
curl "http://127.0.0.1:8787/admin/api/test?model=gpt-6-luna-responses"

# 鉴权失败示例：不带网关 key → 401
curl -i http://127.0.0.1:8787/v1/models
```

**预期**：
- 不带 key 或 key 错误 → 401（OpenAI 错误格式；Anthropic 路径是 `authentication_error`）
- 带正确 `GW_KEY` → 200
- gpt-6-luna → `route=PROXY` → 200
- glm-5.3-flash → `route=DIRECT` → 200
- gpt-6-luna-responses → 日志 `-> gpt-6-luna route=PROXY` → 200
- `/v1/messages` → 200，返回 Anthropic `{"type":"message","content":[...]}`；流式返回 `event: message_start` 等 SSE 事件
- `/v1/messages/count_tokens` → 200 `{"input_tokens": N}`
- LongCat 走 `/v1/messages` → 400（未配置 `anthropic_base_url`）
- `/v1/models` 里能看到 `gpt-6-luna-responses`（`root: gpt-6-luna`）及 capability 字段
- `/v1/models` + `anthropic-version` 头 → Anthropic Models 格式（`data`/`has_more`/`first_id`/`last_id`）

---

## 10. 可改进点

1. **多端口/HTTPS**：如需远程访问，加 TLS；`listen` 修改后需要重启进程（热重载只更新 provider/proxy）。
2. **管理端认证**：客户端入站鉴权已支持；管理端 `/admin*` 仍无认证（仅本地 127.0.0.1），如需远程需加 token 或 Basic Auth。
3. **WorkBuddy 模型自动同步**：写脚本把网关模型列表推送到 WorkBuddy（需研究 WorkBuddy 内部存储）。
4. **模型元数据自动刷新**：定期从 provider 拉取模型能力（OpenCode Go 有 `/v1/models` 端点），自动更新 `model_meta`。
5. **用量统计**：记录每个模型的调用次数/延迟/错误率，管理端展示。
6. **流式工具调用兼容**：目前是透明转发；如遇到客户端对 SSE 事件格式挑剔，可加协议转换层。

---

## 11. 关键代码位置（gateway.py）

| 功能 | 位置 |
|---|---|
| 配置加载/热重载 | `load_config()` / `reload_config()` / `save_config()` |
| 配置校验 | `validate_config()` |
| 客户端入站鉴权 | `client_auth_keys()` / `client_key_candidates()` / `client_auth_ok()` / `GatewayHandler._unauthorized()` |
| 模型路由 | `find_provider()` |
| 代理决策 | `should_proxy()` |
| OpenAI 字段清洗 | `sanitize_body()` |
| Anthropic 字段清洗/端点 | `sanitize_anthropic()` / `anthropic_base()` / `is_anthropic_path()` |
| API Key 选择/规范化/注入 | `provider_api_key()` / `auth_value()` / `set_header()` |
| 模型列表（含别名/元数据） | `build_model_list()` / `model_entry()` |
| Anthropic 模型列表 | `build_anthropic_model_list()` |
| 请求转发 | `GatewayHandler._handle()` |
| 管理端 API | `GatewayHandler._admin_get()` / `_admin_post()` |
| 模型测试 | `GatewayHandler._test_model()` |
| 管理端页面 | `ADMIN_HTML` 常量（raw string） |
| 版本号 | `GW_VERSION` |
| 启动入口 | `main()` |

---

## 12. 变更记录

### v1.2.0（2026-11-07）

- 新增客户端入站鉴权 `client_auth`：客户端用网关自己的端点 + 网关 key 接入（OpenAI `Authorization: Bearer` / Anthropic `x-api-key`）；上游 provider key 只保存在网关配置里，不下发。
- 新增 Anthropic Messages 端点：`/v1/messages`、`/messages`、`/anthropic/v1/messages`、`/v1/messages/count_tokens`；支持 SSE 透明转发；`/v1/models` 支持 Anthropic Models 格式；config 新增 `anthropic_base_url` 和独立的 `anthropic_api_key`（留空回落 `api_key`）。
- 修复管理端空白：`ADMIN_HTML` 改 raw string，JS 转义统一；加版本徽标、`no-store`、`node --check` 验证流程。
- 主转发默认注入 `provider.api_key`，不再依赖客户端 Authorization；新增 `pass_client_auth`。
- `model_map` 映射后的真实 id 用于代理判断和管理端测试，修复别名误走直连。
- 补齐 `max_output_tokens` 等 Responses/Chat 顶层字段，避免 WorkBuddy 参数被清洗。
- `/v1/models` 增加上下文/输出上限/多模态/工具/思考强度，并暴露 `model_map` 别名（`root` 指向真实模型）。
- 管理端编辑 Provider 保留 `model_meta` 等未知字段；新增模型元数据 JSON 编辑框；非法 JSON 和服务端校验错误会提示。
- 配置保存增加结构校验和原子写入（`.tmp` + `os.replace`）。
- 流式转发：上游 4xx/5xx 记录响应片段；客户端断开（WinError 10053/10054）不再刷 ERR；补充耗时日志。

### v1.1.0

- 多 provider 聚合、按模型代理/直连、WorkBuddy 字段清洗、协议格式自动纠正、管理端增删改查/测试/日志。

### v1.0.0

- 单 provider 桥接（`wb_opencode_bridge.py`），补 `x-opencode-session` 头。
