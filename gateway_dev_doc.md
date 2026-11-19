# WB Aggregator Gateway Developer Notes

> Sanitized repository copy. Credentials must be supplied through environment variables; do not commit local runtime configuration.

> 轻量化 OpenAI + Anthropic 兼容聚合网关：多 provider 聚合、按模型自动代理/直连、带网页管理端。
> 单文件 Python（标准库 + requests），无数据库、无前端构建。
> **当前版本：v1.5.2（2026-11-19）**

---

## 1. 项目概述

**目标**：给 WorkBuddy / 任意 OpenAI 或 Anthropic 兼容客户端提供统一入口，背后聚合多个 provider（OpenCode Go、LongCat 等），并解决三类问题：

1. **地区限制**：OpenAI/xAI/Meta 系模型（gpt-6-luna、grok-4.7 等）需要非受限地区出口；国产模型（glm/kimi/deepseek）直连更快。
2. **协议差异**：WorkBuddy 等客户端会在请求里塞私有字段（`startsNewUserRequest`、`traceId`、`messageId` 等），OpenCode Go 会拒绝；需要在网关层清洗。
3. **双协议入口**：同时提供 OpenAI（Chat Completions / Responses）和 Anthropic（Messages）端点；OpenCode Go 支持跨协议翻译，同一模型可以用任一协议调用。
4. **入站鉴权**：客户端用你自己的网关端点 + 网关 key 接入；各 provider 的真实 key 只保存在网关配置里，不下发给客户端。

**形态**：单文件 `gateway.py` + 配置 `gateway_config.json`，默认监听 `0.0.0.0:8787`（本机 + 局域网可访问，见 §3.9）。

### 1.1 客户端怎么填（快速接入）

| 客户端类型 | Base URL / API 地址 | API Key | 模型 |
|---|---|---|---|
| OpenAI 兼容（WorkBuddy、Cherry Studio、OpenAI SDK） | `http://127.0.0.1:8787/v1`；局域网用 `http://192.168.1.100:8787/v1` | 管理端「客户端接入密钥」里的网关 key（`sk-gw-...`） | `/v1/models` 返回的 id，如 `gpt-6-luna`、`glm-5.3-flash` |
| Anthropic 兼容（Claude Code、Anthropic SDK） | `http://127.0.0.1:8787`；局域网同上换成主机 IP | 同上 | 同上（OpenCode 支持跨协议） |

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

**思考强度纠正（1.4.4）**：WorkBuddy 在「思考=关」时会发送 `reasoning_effort: "none"`，但部分模型（如 `muse-ember-1.0`）不支持 `none`，上游会直接 400：

```text
reasoning_effort 'none' is not supported ... Supported values: [minimal, low, medium, high, xhigh, max]
```

网关按 `model_meta[model].reasoning` 修正：

- 值在支持列表内 → 原样透传
- 值为 `none/off/disabled/false/0/no` 且模型不能关思考 → 降到列表最低档（如 `minimal`）
- 其他不支持的值 → 直接删除，让上游用默认档

Chat Completions 处理顶层 `reasoning_effort`，Responses 处理 `reasoning.effort`。日志会打印 `reasoning_effort 'none' -> 'minimal'`。

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

**管理**：管理端「客户端接入密钥」卡片可启停鉴权、增删 key、生成随机 key、复制第一个 key。管理端 `/admin*` 不校验该 key，默认只允许本机访问（见 §3.9）。

### 3.7 Go 额度进度（服务端真实额度）

OpenCode 提供真实额度接口：

```http
GET https://opencode.ai/zen/go/v1/usage
Authorization: Bearer <OpenCode Go API key>
```

返回：

```json
{"usage": {
  "rolling": {"status": "ok", "percent": 71, "resetsAt": "2026-11-09T05:33:24.000Z"},
  "weekly":  {"status": "ok", "percent": 28, "resetsAt": "2026-11-16T00:00:00.000Z"},
  "monthly": {"status": "ok", "percent": 18, "resetsAt": "2026-12-07T08:57:57.000Z"}
}}
```

- `rolling` = 5 小时滚动窗口，`weekly` = 周（UTC），`monthly` = 计费月。
- `percent` 是服务端计算的真实使用百分比，`resetsAt` 是重置时间。
- 该接口是**账号级**的，Go key 和 Zen key 查到的窗口相同，不能拆分 Zen/Go；Zen 预付费余额见 §3.10。
- 网关 `GET /admin/api/quota` 直接读取该接口，30 秒缓存；`?refresh=1` 强制刷新。
- 管理端「Go 服务端额度」卡片展示三个真实进度条和重置时间；70% 黄、90% 红。

**本地参考**：响应的 `usage.opencode.allowance` 仍会按小时累计到 `gateway_usage.json` 的 `quota.buckets`，用于「本地按模型累计」明细。它只统计经过本网关的流量，不能代替服务端口径。

**预计用完时间（ETA）**：后台每 5 分钟采样一次服务端百分比，写入 `gateway_usage.json` 的 `quota_history`（保留 8 天）。ETA 计算：

1. 优先用近 1 小时、至少 30 分钟样本的线性回归斜率（`basis: recent`），避免整数百分比在 5 分钟短样本下产生噪声。
2. 样本不足或近期没有增长时，用“当前百分比 ÷ 本窗口已用时间”的平均速率（`basis: average`）。
3. 若预计用完时间晚于 `resetsAt`，显示“重置前不会用完”，不再给具体时间。
4. API 的 `eta` 字段包含 `rate_per_hour`、`hours`、`at`、`before_reset`、`basis`。

> 服务端百分比是整数，低频使用时短时间内可能不变，因此会回退到窗口平均速度；重置后百分比下降会自动丢弃重置前的采样。

配置：

```json
"usage_url": "https://opencode.ai/zen/go/v1/usage"
```

- 该字段在 `opencode-go` provider 上配置；默认就是官方地址。
- `model_meta.monthly_limit` / `unlimited` 只用于本地按模型参考表；服务端进度不需要它们。
- 免费/无限模型（`beam-free`、`crouton-alpha-free`）设置 `"unlimited": true`。

> **统计范围**：服务端进度是账号真实值，包含 OpenCode 客户端和其他客户端的用量；本地按模型明细只统计经过本网关的请求。

### 3.8 Zen 模型（按量付费）

OpenCode 有两套模型目录，网关按 provider 分开：

| 目录 | Base URL | 计费 | 模型数 |
|---|---|---|---|
| Go | `https://opencode.ai/inference/go/openai/v1` | Go 订阅额度 | 30 |
| Zen | `https://opencode.ai/inference/openai/v1` | 账户余额（按量） | 84 |

`muse-ember-1.0` 不在 **Go** 目录里，用 Go 的 base 请求会返回 `Model is unavailable`，所以网关新增了 `opencode-zen` provider：

```json
{
  "name": "opencode-zen",
  "base_url": "https://opencode.ai/inference/openai/v1",
  "anthropic_base_url": "https://opencode.ai/inference/anthropic/v1",
  "api_key": "oc_sk_...",
  "needs_session": true,
  "headers": {"x-opencode-session": "{session}"},
  "proxy_models": ["muse-ember-1.0", "deepseek-v4.1-flash-free"],
  "models": ["muse-ember-1.0", "deepseek-v4.1-flash-free"]
}
```

关键点：**Zen base 和 Go key 是两套认证**。Go key（`opencode-go.api_key`）在 Zen base 上会返回 `401 Invalid credential`；`opencode-zen.api_key` 必须填同一个有效的 OpenCode key。若两个 provider 用同一把 key，直接复制 `opencode-go.api_key` 即可。

`muse-ember-1.0` 的实测结果：

- 直连：`403 RegionError`（Meta 模型地区限制）
- 走代理 + Go key：`401 Invalid credential`
- 走代理 + 正确的 OpenCode key：`200 OK`，扣费走账户余额（`billing_source: balance`）
- 价格：$0.10 输入 / $0.50 输出 / $0.004 缓存读（每 1M tokens）
- 上下文 1,048,576，输出上限 270,000，支持图片/视频/PDF/工具/思考

`deepseek-v4.1-flash-free` 是 Zen 免费模型，外部直连返回 `429 FreeUsageLimitError`，只在 OpenCode 客户端内可用；网关里建议保持关闭。

> Go 额度卡片只监控 `opencode-go`；Zen 模型的请求仍会计入 Token 用量和成本估算。`opencode-zen` 的 key 失效时，`/admin/api/test?model=muse-ember-1.0` 会返回 401，把 `opencode-go.api_key` 复制到 `opencode-zen.api_key` 后重载即可。

### 3.9 局域网访问

默认配置 `"listen": {"host": "0.0.0.0", "port": 8787}`，本机和同一局域网设备都能访问：

```text
OpenAI:     http://192.168.1.100:8787/v1
Anthropic:  http://192.168.1.100:8787
```

管理端「概览」的「监听」和端点卡片会直接用服务端枚举到的局域网 IP 显示，不需要手改配置。`GET /admin/api/status` 返回 `lan_urls`。

要点：

0. **不要用 TUN/虚拟网卡 IP 填模型地址**（1.4.4 修复）：开了 Clash Meta / cfw-tap 后，`198.18.0.1` 这类地址会被客户端代理（如 `127.0.0.1:7897`）接管，最终返回 `502 Server error (proxy: ... -> http://198.18.0.1:8788)`。网关现在枚举 `lan_urls` 时会跳过 `Meta`、`cfw-tap`、`vEthernet` 等虚拟网卡和 `198.18.0.0/15` 网段，只显示真实 WLAN/以太网地址（如 `192.168.1.100`）。本机客户端一律用 `127.0.0.1`。
1. **改 `listen.host` 后必须重启进程**（HTTP 服务只在启动时 bind），热重载不会改监听地址。
2. **`/v1/*` 仍需要网关 key**：局域网客户端填 `client_auth.keys` 里的 `sk-gw-...`，不是上游 provider key。
3. **管理端默认只允许本机**：非回环地址访问 `/admin*` 返回 403，避免同网段的人看到配置里的 provider key。
4. **远程管理（可选）**：在配置里加

   ```json
   "admin": {"allow_remote": true, "token": "换成一串随机值"}
   ```

   然后访问 `http://192.168.1.100:8787/admin?token=...`，验证通过后会种一个 HttpOnly cookie，后续页面和 API 自动带上；也可以直接带 `X-Admin-Token` 请求头。`allow_remote=true` 但 `token` 为空时仍然拒绝远程访问。
5. **Windows 防火墙**：需要放行 TCP 8787 入站（本机已建规则 `WorkBuddy Gateway 8787`，仅 Domain/Private 网络）：

   ```powershell
   New-NetFirewallRule -DisplayName 'WorkBuddy Gateway 8787' -Direction Inbound `
     -Protocol TCP -LocalPort 8787 -Action Allow -Profile Private,Domain
   ```

6. 手机/其他电脑访问不通时，先确认两台设备在同一网段、网关进程监听的是 `0.0.0.0`（管理端「监听」卡片会显示“局域网”），再用 `Test-NetConnection <IP> -Port 8787` 排查。

### 3.10 Zen 余额监控（服务端）

OpenCode 的 Go 额度和 Zen 余额来自两套不同的服务端数据：

| 项目 | 接口 | 认证 | 返回 |
|---|---|---|---|
| Go 订阅窗口 | `GET /zen/go/v1/usage` | API key | rolling / weekly / monthly 百分比 + `resetsAt` |
| Zen 预付费余额 | `GET /console/api/billing/status` | Console 会话 Cookie + `x-org-id` | `balanceMicroCents`、`availableMicroCents`、`mode` |
| Zen 月度预算 | `GET /console/api/budgets/org` | 同上 | `limitMicroCents`、`spentMicroCents`、`resetsAt` |
| Go 精确金额（可选） | `GET /console/api/go/status` | 同上 | 三个窗口的 used/limit micro-cents |

> 注意：`/zen/go/v1/usage` 是**账号级**窗口，无论用 Go key 还是 Zen key 查询结果都一样，无法拆分“Zen 用了多少、Go 用了多少”。Zen 是预付费钱包，官方目前没有 API-key 认证的余额接口（opencode#44189 / #10448 仍未提供），只能通过 Console 会话读取。金额单位是 micro-cents，`100000000 = $1`。

**配置**（也可以直接在管理端「Zen 余额」卡片里填）：

```json
"zen_console": {
  "enabled": true,
  "session_cookie": "__Host-console_session=...; auth=...",
  "org_id": ""
}
```

- `session_cookie`：浏览器登录 `https://opencode.ai` 后，从 DevTools → Application → Cookies 复制整段 Cookie（至少包含 `__Host-console_session`）。
- `org_id`：工作区 ID（`org_...`），留空时网关调用 `/console/api/orgs` 自动发现。
- Cookie 会过期；卡片报 401/403 时重新登录并更新即可。
- Cookie 只保存在本机 `gateway_config.json`，不会下发给客户端；管理端默认也只允许本机访问（远程需 `admin.token`）。

**管理端展示**：Zen 余额、可用额度、本月支出/预算与重置时间；未配置或读取失败时显示配置提示和原始错误。

**API**：`GET /admin/api/quota` 的响应新增 `zen` 字段：

```json
"zen": {
  "configured": true, "ok": true, "org_id": "org_...",
  "balance_usd": 12.34, "available_usd": 0.0,
  "mode": "pay-as-you-go", "billing_mode": "prepaid",
  "monthly_spent_usd": 5.67, "monthly_limit_usd": 60.0,
  "resets_at": "2026-12-01T00:00:00.000Z",
  "go_meters": {"rolling": {"used_usd": 1.2, "limit_usd": 10.0, "percent": 12.0}},
  "fetched_at": "2026-11-10T15:00:00", "error": ""
}
```

### 3.11 智能路由（智能别名 / 故障转移 / 失败冷却）

配置根字段 `routing`（管理端「智能路由」卡片可编辑）：

```json
"routing": {
  "enabled": true,
  "fallbacks": {
    "gpt-6-luna": ["gpt-6-luna", "gpt-5.6-luna", "glm-5.3-flash", "LongCat-2.5-Preview"]
  },
  "smart_aliases": {
    "smart": ["gpt-6-luna", "glm-5.3-flash", "deepseek-v4.1-flash", "LongCat-2.5-Preview"],
    "cheap": ["glm-5.3-flash", "deepseek-v4.1-flash", "LongCat-2.5-Preview"]
  },
  "retry_on": [400, 402, 403, 404, 408, 429, 500, 502, 503, 504, 529],
  "max_attempts": 4,
  "cooldown_seconds": 300
}
```

三种机制：

1. **智能别名**：客户端请求 `smart` / `cheap` / `vision` / `coding` 时，按 `smart_aliases` 顺序选第一个可用候选；别名会出现在 `/v1/models` 里（`root` 指向第一个候选）。
2. **故障转移**：请求主模型后，如果上游返回 `retry_on` 中的状态码，网关会读取完整错误体，然后尝试候选列表里的下一个模型；全部失败后把最后一次上游错误原样返回。因为错误响应是在转发给客户端之前完整读取的，所以流式请求也能安全降级。
3. **失败冷却**：模型失败后在 `cooldown_seconds` 内不再优先选它；成功后自动清除。冷却状态可在管理端「智能路由」查看，日志里会打印 `SKIP cooling ...` / `FALLBACK ...`。

其他细节：

- 每次尝试都会重新解析 provider、`model_map`、代理策略，并把请求体里的 `model` 改写成实际候选模型（客户端仍然看到自己请求的别名）。
- `max_attempts` 限制单次请求最多尝试几个候选，避免无限重试。
- 候选模型可以跨 provider（例如 `opencode-go` → `longcat`），响应里的 `model` 字段会是实际使用的模型。
- API：`GET /admin/api/routing` 返回 `{config, health, chains}`；修改配置走 `POST /admin/api/config`（带 `X-Config-Revision`）。

---

## 4. 配置格式（gateway_config.json）

```json
{
  "listen": {"host": "0.0.0.0", "port": 8787},
  "admin": {"allow_remote": false, "token": "${GATEWAY_ADMIN_TOKEN}"},
  "zen_console": {"enabled": false, "session_cookie": "", "org_id": ""},
  "routing": {
    "enabled": true,
    "fallbacks": {"gpt-6-luna": ["gpt-6-luna", "gpt-5.6-luna", "glm-5.3-flash"]},
    "smart_aliases": {"smart": ["gpt-6-luna", "glm-5.3-flash", "deepseek-v4.1-flash"]},
    "retry_on": [400, 402, 403, 404, 408, 429, 500, 502, 503, 504, 529],
    "max_attempts": 4,
    "cooldown_seconds": 300
  },
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
| `listen.host` | 监听地址；`127.0.0.1` 仅本机，`0.0.0.0` 允许局域网；修改后需重启 |
| `admin.allow_remote` | 是否允许非本机访问 `/admin*`，默认 `false` |
| `admin.token` | 远程管理令牌；配合 `allow_remote` 使用，通过 `?token=`、Cookie 或 `X-Admin-Token` 校验 |
| `zen_console.enabled` | 是否启用 Zen 余额监控（需 Console 会话 Cookie） |
| `zen_console.session_cookie` | `opencode.ai` 的浏览器会话 Cookie，用于调用 Console 余额接口 |
| `zen_console.org_id` | 工作区 ID；留空自动通过 `/console/api/orgs` 发现 |
| `routing.enabled` | 是否启用智能路由（别名/故障转移/冷却） |
| `routing.smart_aliases` | 逻辑模型名 → 候选模型列表，按顺序选第一个可用候选 |
| `routing.fallbacks` | 模型 → 候选列表；主模型返回 `retry_on` 错误码时依次尝试 |
| `routing.retry_on` | 触发故障转移的上游状态码，默认含 400/402/403/404/408/429/5xx |
| `routing.max_attempts` | 单次请求最多尝试的候选数，默认 4 |
| `routing.cooldown_seconds` | 模型失败后的冷却时间，默认 300 秒 |
| `client_auth.enabled` | 是否校验客户端请求的网关 key；`true` 时 `/v1/*` 必须带 `client_auth.keys` 里的 key |
| `client_auth.keys` | 允许接入的网关 key 列表；支持 `${GATEWAY_CLIENT_KEYS}`，环境变量可用逗号分隔多把 key |
| `proxy` | 全局代理地址（Clash 混合端口），留空则全部直连 |
| `providers[].base_url` | OpenAI 兼容端点，**不带** `/chat/completions` |
| `providers[].anthropic_base_url` | Anthropic Messages 端点，**不带** `/messages`；不配置则不支持该 provider 的 `/v1/messages`（opencode.ai 可自动推导） |
| `providers[].usage_url` | Go 额度接口，默认 `https://opencode.ai/zen/go/v1/usage` |
| `providers[].api_key` | OpenAI 上游 key；使用 `${OPENCODE_GO_API_KEY}` / `${LONGCAT_API_KEY}` 环境变量引用 |
| `providers[].anthropic_api_key` | Anthropic `/v1/messages` 专用 key；支持环境变量引用，留空则回落到 `api_key` |
| `providers[].pass_client_auth` | 默认 `false`：主转发用配置 key 覆盖客户端 Authorization；`true` 则保留客户端鉴权 |
| `providers[].needs_session` | 是否强制注入 `x-opencode-session` |
| `providers[].headers` | 自定义头，`{session}` 占位符会被替换 |
| `providers[].model_map` | WorkBuddy 模型 id → 真实模型 id 映射 |
| `providers[].proxy_models` | 这些模型走代理；其余直连 |
| `providers[].use_proxy` | 该 provider 全部模型走代理 |
| `providers[].model_meta` | 模型能力元数据（上下文/多模态/思考/价格），管理端展示用 |
| `providers[].model_meta.<model>.monthly_limit` | Go 套餐该模型的月度美元上限，用于额度进度 |
| `providers[].model_meta.<model>.unlimited` | `true` 表示免费/无限额度模型（如 Beam Free、Crouton Free） |

### 4.1 模型能力与价格清单（2026-11-09 更新）

> 数据来源：`gateway_config.json` 的 `model_meta`；管理端「模型能力」表与 `/v1/models` 同样返回这些字段。
> 价格为 **USD / 1M tokens**（`gpt-live-1` 例外，按分钟）。🎁 = 免费模型。
> 2026-11-09 已删除一批上游返回 `Model is unavailable` / 403 的模型，下表只保留当前可用的模型。

| 模型 | Provider | 上下文 | 输出上限 | 多模态 | 工具 | 思考强度 | 输入/1M | 输出/1M | 缓存读 | 缓存写 | 出口 |
|---|---|---:|---:|:---:|:---:|---|---:|---:|---:|---:|---|
| `gpt-6-luna` | opencode-go | 1,050,000 | 128,000 | ✅ | ✅ | low / medium / high / xhigh / max | $0.100 | $0.500 | $0.010 | $0.125 | 🌐 代理 |
| `gpt-5.6-luna` | opencode-go | 1,050,000 | 128,000 | ✅ | ✅ | low / medium / high / xhigh / max | — | — | — | — | 🌐 代理 |
| `grok-4.7` | opencode-go | 500,000 | 500,000 | ✅ | ✅ | low / medium / high / xhigh | $2.00 | $6.00 | $0.500 | — | 🌐 代理 |
| `glm-5.3` | opencode-go | 1,000,000 | 131,072 | ❌ | ✅ | low / high / max | $1.40 | $4.40 | $0.260 | — | 直连 |
| `glm-5.3-flash` | opencode-go | 1,000,000 | 131,072 | ✅ | ✅ | low / high / max | $0.150 | $0.500 | $0.030 | — | 直连 |
| `glm-5.2` | opencode-go | 1,000,000 | 131,072 | ❌ | ✅ | high / max | — | — | — | — | 直连 |
| `kimi-k3` | opencode-go | 1,048,576 | 131,072 | ✅ | ✅ | max | $3.00 | $15.00 | $0.300 | $3.00 | 直连 |
| `kimi-k3.1-pro` | opencode-go | 1,048,576 | 131,072 | ✅ | ✅ | low / high / max | — | — | — | — | 直连 |
| `kimi-k2.7-code` | opencode-go | 262,144 | 262,144 | ✅ | ✅ | — | $0.950 | $4.00 | $0.190 | — | 直连 |
| `deepseek-v4-pro` | opencode-go | 1,000,000 | 384,000 | ❌ | ✅ | high / max | $1.74 | $3.48 | $0.145 | — | 直连 |
| `deepseek-v4.1-flash` | opencode-go | 1,000,000 | 384,000 | ✅ | ✅ | low / high / max | $0.300 | $1.20 | $0.0060 | — | 直连 |
| `minimax-m3.1-flash` | opencode-go | 1,000,000 | 512,000 | ✅ | ✅ | low / medium / high / xhigh / max | — | — | — | — | 直连 |
| `longcat-2.0` | opencode-go | 1,000,000 | 131,072 | ❌ | ✅ | — | — | — | — | — | 直连 |
| `step-5-preview` | opencode-go | 1,000,000 | 65,536 | ✅ | ✅ | low / medium / high | — | — | — | — | 直连 |
| `hy3` | opencode-go | 256,000 | 128,000 | ❌ | ✅ | low / high | — | — | — | — | 直连 |
| `hy4` | opencode-go | 1,048,576 | 65,536 | ✅ | ✅ | high | — | — | — | — | 直连 |
| `mimo-v2.6-pro` | opencode-go | 1,048,576 | 131,072 | ✅ | ✅ | — | — | — | — | — | 直连 |
| `mimo-v2.6-flash` | opencode-go | 1,048,576 | 131,072 | ✅ | ✅ | — | — | — | — | — | 直连 |
| `beam-free` 🎁 | opencode-go | 1,048,576 | 131,072 | ❌ | ✅ | low / medium / high / xhigh / max | $0.00 | $0.00 | $0.00 | — | 🌐 代理 |
| `crouton-alpha-free` 🎁 | opencode-go | — | — | — | — | — | $0.00 | $0.00 | $0.00 | — | 🌐 代理 |
| `claude-haiku-5-5` | opencode-go | — | — | — | — | — | $0.100 | $0.500 | $0.010 | $0.125 | 直连 |
| `qwen3.8-flash` | opencode-go | — | — | — | — | — | $0.150 | $0.470 | $0.016 | $0.200 | 直连 |
| `minimax-m3` | opencode-go | — | — | — | — | — | $0.300 | $1.20 | $0.060 | — | 直连 |
| `gemini-4-argon` | opencode-go | — | — | — | — | — | $2.00 | $10.00 | $0.100 | — | 直连 |
| `LongCat-2.5-Preview` | longcat | 1,000,000 | 128,000 | ✅ | ✅ | low / medium / high | — | — | — | — | 直连 |

**说明**：
- 上下文/输出上限/多模态/思考强度为已知元数据；新模型未提供这些信息时显示 `—`，可在管理端编辑 Provider 的「模型元数据（JSON）」补充。
- `beam-free` 有地区限制，已加入 `proxy_models`；`crouton-alpha-free` 同样走代理。
- `/v1/models` 还会额外返回 `gpt-6-luna-responses` 别名（`root: gpt-6-luna`），所以条目数比上表多 1。
- 历史 Token 用量仍保留在 `gateway_usage.json`；如需清零，用管理端「Token 用量 → 重置」。

### 4.2 按厂商统计 Token（Go 本地 Token）

**重要限制**：OpenCode 服务端 Go 接口（`/zen/go/v1/usage`）只返回百分比，**不返回 token 数**；服务端也没有公开的 Zen 消费金额/接口。因此「Go 用了多少 token」只能通过网关本地记录估算，即经过 `opencode-go` provider 的请求。OpenCode CLI、其它客户端、其它机器的用量不会出现在网关统计里。

实现：

1. 每个模型按 `model_meta.vendor`（可选）或模型名前缀推断厂商：
   - `claude*` → Anthropic，`gpt*`/`codex` → OpenAI，`gemini*` → Google，`grok*` → xAI
   - `deepseek*` → DeepSeek，`glm*` → 智谱 AI，`kimi*` → Moonshot，`qwen*` → 阿里云（通义）
   - `minimax*` → MiniMax，`mimo*` → 小米（MiMo），`hy3/hy4` → 腾讯（混元），`step-*` → 阶跃星辰
   - `longcat*` → 美团 LongCat，`muse*`/`meta/*` → Meta，`mistral*` → Mistral，`nemotron*` → NVIDIA
   - `big-pickle`/`crouton`/`flint`/`iceman`/`inkling`/`jev-`/`beam` → OpenCode；其余 → 其他
2. 管理端「Token 用量」新增：
   - 总览卡片「Go 本地 Token」= `opencode-go` provider 的 `total_tokens`
   - 「按厂商」表（厂商、请求、输入/输出/总 Token、缓存读、缓存命中率、推理、成本、延迟）
   - 「按模型」表增加「厂商」列
3. `/admin/api/usage` 返回 `vendors`，且 `models[]` 含 `vendor`。

### 4.3 每秒 Token（TPS）

每条请求结束时记录本请求的输出速度：

```text
单请求 output_tps = output_tokens / (latency_ms / 1000)
```

- 统计字段：`tps_sum` / `tps_count` / `tps_max`，管理端表格显示「平均 TPS」；日志会打印 `tps=...`。
- 实时窗口：内存中保留最近 60 秒完成的请求（`LIVE_USAGE`），`/admin/api/usage` 返回 `live`：

```json
"live": {
  "window_seconds": 60,
  "span_seconds": 48.1,
  "active_seconds": 29.4,
  "requests": 3,
  "output_tokens": 587,
  "input_tokens": 340166,
  "output_tps": 19.97,
  "last_output_tps": 24.5,
  "by_model": [{"model": "claude-haiku-5-5", "provider": "opencode-go",
                "output_tokens": 587, "input_tokens": 340166,
                "requests": 3, "active_seconds": 29.4,
                "output_tps": 19.97, "last_output_tps": 24.5}]
}
```

- **计算口径（v1.5.0 修复）**：`output_tps = Σ输出 token ÷ Σ请求耗时`，分母是 `active_seconds`。不能再用「距最早一次请求完成的秒数」做分母，否则刚完成的请求会虚高（120/s）、窗口拖长后又会被摊薄（12/s）。
- `span_seconds` 只用于界面显示「近 X 秒」，`active_seconds` 才是计算分母。
- `last_output_tps` 是最近一次请求的单请求速度，界面显示为「最近一次」。
- `input_tps` / `total_tps` 仍按同一分母给出，仅作参考；输入 token 是提示词，不是生成量。
- 注意：TPS 用的是**请求总耗时**（含排队、失败重试），不是纯生成时间；智能路由触发降级时，耗时包含前面失败尝试的时间。
- 用量按实际生效模型统计，智能别名（如 `cheap`）会归入真实模型（如 `glm-5.3-flash`）。

- 管理端「用量」板：5 个主 KPI（请求数 / 总 Token / 实时 TPS / 缓存命中率 / 实扣），实时 TPS 卡片显示输出平均和最近一次；下方「按厂商 / 按 Provider / 按模型」三张表，模型表支持搜索、厂商筛选、隐藏未使用、分页和行展开详情。

---

## 5. 管理端 API

| 端点 | 方法 | 说明 |
|---|---|---|
| `/admin/api/config` | GET | 返回完整配置 JSON |
| `/admin/api/config` | POST | 保存配置（body 为完整配置 JSON），写文件 + 热重载 |
| `/admin/api/status` | GET | `{ok, version, providers, models, proxy_models, proxy, listen, lan_urls, uptime}` |
| `/admin/api/logs?lines=150` | GET | `{lines: [...]}` 日志行数组 |
| `/admin/api/routing` | GET | 智能路由状态：`{config, health, chains}`（冷却中的模型、别名解析链） |
| `/admin/api/usage` | GET | Token 用量快照：`{since, updated, totals, providers, vendors, models, live}`，含 `cache_hit_rate`、模型 `vendor`、`configured`（是否在当前配置中）、`avg_tps`、近 60 秒实时 TPS |
| `/admin/api/quota` | GET | Go 服务端真实额度 `{usage.rolling/weekly/monthly}` + `eta` 预计用完 + `zen` 余额/预算 + `local` 本地按模型参考；`?refresh=1` 强刷 |
| `/admin/api/usage/reprice` | POST | 按当前 `model_meta.pricing` 重算历史 `list_cost` |
| `/admin/api/usage/reset` | POST | 清零 Token 用量统计 |
| `/admin/api/test?model=xxx` | GET | `{ok, model, provider, route, reply}` 或 `{ok:false, error}` |
| `/admin/api/reload` | POST | 热重载配置 |

**保存配置**（POST `/admin/api/config`）：
> 需要带 `X-Config-Revision` 请求头（GET `/admin/api/config` 的响应头）。缺失返回 400，版本不一致返回 409，避免旧管理端页面覆盖新配置。

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

**形态**：单 HTML 字符串内嵌在 `gateway.py` 里（`ADMIN_HTML` 常量），原生 HTML/CSS/JS，无外部依赖。2026-11-16 版按 `.design/DESIGN.md` 重构为 **4 块可切换仪表板**：

- **运行（`#run`）**：总览、客户端接入密钥、Provider 卡片
- **用量（`#usage`）**：KPI、实时 TPS、按厂商 / Provider / 模型表
- **上游（`#upstream`）**：模型能力表、Go 服务端额度、Zen 余额
- **规则（`#rules`）**：智能路由、Provider 管理、日志与重载

顶部 tab 支持 `#hash` 深链和左右方向键 / Home / End 切换；同一时间只显示一块，DOM 不销毁。设计 token 见 `.design/design-tokens.json`，构建、写回、校验脚本在 `.design/`。

**功能**：
- **总览**：Provider 数、模型总数、走代理模型数、代理地址、监听地址、OpenAI 端点、Anthropic 端点、运行时长
- **客户端接入密钥**：启停鉴权、每行一个 key、生成随机 key、复制第一个 key
- **Provider 卡片**：名称、base_url、key（脱敏）、模型标签（点击测试）
- **模型标签**：hover 显示 tooltip（上下文/多模态/工具/思考强度）；走代理的带 🌐
- **模型能力表**：页面直接展示每个模型的上下文、输出上限、多模态、工具、思考强度、价格、出口，数据来自 `model_meta`
- **Token 用量**：按 **厂商 / Provider / 模型**统计请求数、输入/输出/总 Token、缓存读、**缓存命中率**（缓存读 ÷ 输入）、推理 Token、上游实扣、按价格估算成本、错误数、平均延迟；模型表支持搜索、厂商筛选、隐藏未使用、**仅当前配置**（默认开启，隐藏已删除 Provider/模型的历史行，取消勾选可看全部）、每页 10 行分页，点击模型行展开完整明细（缓存读、推理 Token、平均 TPS、标价估算、错误、无用量、平均延迟、最近调用）；支持刷新和重置。总览含「Go 本地 Token」（`opencode-go` provider 的累计）
- **Token 计量**：小于 1000 显示实际数字（如 `999`）；≥1000 自动用 `K / M / B`（千 / 百万 / 十亿，保留最多两位小数，如 `12.35K`、`1.05M`、`1.45B`）；四舍五入会晋档的情况自动升一级（`999,999` → `1M`，不会出现 `1000K`）
- **Go 服务端额度**：直接读 `/zen/go/v1/usage`，展示 5 小时滚动 / 本周 / 本月真实百分比和重置时间；下方「本地按模型累计」作为参考；70% 黄、90% 红，60 秒自动刷新
- **测试**：点击单个模型测试；「⚡ 一键测试」批量（并发 5 路）
- **红绿标记**：`.tag.ok`（绿）/`.tag.fail`（红）/`.tag.testing`（灰）
- **Provider 管理**：添加/编辑/删除（模态框表单）
- **双协议配置**：表单可填 OpenAI Base URL/API Key 和 Anthropic Base URL/API Key；Anthropic key 留空时复用 OpenAI key
- **模型元数据编辑**：表单里的「模型元数据（JSON）」可直接维护 `model_meta`；编辑 Provider 时保留原有 `model_meta`/未知字段，不再丢失
- **保存校验**：JS 侧拦截非法 JSON 并 toast；服务端返回的校验错误也会显示
- **版本徽标**：标题旁显示 `v1.5.0`，用于确认页面是否为新版本
- **实时日志**：底部面板，可刷新
- **重载配置**：一键热重载
- **错误兜底**：`window.onerror` / `unhandledrejection` 把错误显示在页面上
- **不缓存**：页面响应 `Cache-Control: no-store`；所有 `fetch` 也带 `cache: 'no-store'`

**关键 JS 函数**：
```js
async function load()             // 拉配置 + 渲染 + 日志
function render()                 // 渲染总览 + provider 卡片
async function loadUsage()        // 拉 /admin/api/usage
function renderUsageTable(items, type)  // 厂商/Provider 表；模型表被新块覆写
function bindFilters()            // 模型表搜索、厂商筛选、隐藏未使用
function renderTop5(models)       // 用量 Top5 条形
function refreshModelTable()      // 分页/筛选后重绘模型表
async function testModel(el)      // 测试单个模型，更新标签颜色
async function testAll()          // 批量测试（并发 5）
async function saveProvider()     // 保存/编辑 provider
async function delProvider(i)     // 删除
async function reload()           // 热重载
```

**样式**：`.design/DESIGN.md` 的 slate-light token（`--bg`/`--surface`/`--border`/`--ink`/`--accent` 等），4 板 + 12 栏网格 + 密集表格，响应式；系统字体栈，不加载外部字体。

---

## 7. 开机自启

**双保险**（都用 `pythonw.exe`，无窗口）：

1. **计划任务**（推荐，可配崩溃重试）：
```powershell
$py = "<HOME>\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe"
$script = "<PROJECT_DIR>\gateway.py"
$wd = "<PROJECT_DIR>"
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
**WorkBuddy 配置路径**：`<HOME>\.workbuddy\models.json`

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

# Go 额度进度（5 小时 / 近 7 天 / 本月）
curl http://127.0.0.1:8787/admin/api/quota

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

1. **HTTPS**：局域网已支持明文访问；如需跨公网，建议前面套 Nginx/Caddy 加 TLS。`listen` 修改后需要重启进程（热重载只更新 provider/proxy）。
2. **管理端认证**：已支持 `admin.allow_remote` + `admin.token`（query/Cookie/Header）保护远程管理；如需更细粒度权限可再加多用户/Basic Auth。
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

### v1.5.2（2026-11-19）

- Token 计量单位从中文大单位（百万 / 千万 / 亿 / 十亿 / …）改为 `K / M / B`：小于 1000 显示实际数字，≥1000 按千 / 百万 / 十亿缩进，最多两位小数（`12.35K`、`1.05M`、`1.45B`）。
- 修正四舍五入晋档：`999,999` 以前会显示成 `1000K`，现在自动变成 `1M`；`999,999,999` → `1B`。
- 影响范围：Token 用量总览 5 张 KPI、按厂商 / Provider / 模型三张表、模型能力表的上下文与输出上限、近 60 秒实时 TPS 明细。

### v1.5.1（2026-11-17）

- 用量统计支持时间段筛选：`GET /admin/api/usage?period=today|week|month|all`，返回 `period`、`period_start`、`period_end`、`period_note`。管理端「用量」板新增 今日 / 本周 / 本月 / 全部 四档按钮。
- `gateway_usage.json` 新增 `days` 按天累计（输入/输出/缓存/推理 token、实扣、估算成本、请求/错误数、TPS 累加）。时间段查询把 `days` 里对应日期的数据重新聚合；`all` 直接返回累计值。
- 注意：按天记录从 v1.5.1 起才有，之前的历史只能通过「全部」查看；`period_note` 会提示这一点。
- 局域网地址枚举新增 `_is_lan_ipv4()`：排除回环、`169.254/16`、`198.18.0.0/15`（Clash TUN）、以及 `Meta`/`cfw-tap`/`vEthernet`/TAP/TUN 等虚拟网卡，`lan_urls` 只返回真实 WLAN/以太网地址。
- 同步 `.env.example` 增加 `CODINGPASS_API_KEY`；`.gitignore` 增加 `gateway_usage.json.bak*`。
- 文档：版本号更新为 v1.5.1；`.design/admin_source.html` 基准刷新为当前线上页面，`python .design/verify_ui.py .design/admin_new.html` 恢复全通过。

### v1.5.0（2026-11-16）

- 管理端按 `.design/DESIGN.md` 重构为 4 块仪表板：运行 `#run` / 用量 `#usage` / 上游 `#upstream` / 规则 `#rules`；支持 hash 深链和方向键切换。
- 用量板改为 5 个主 KPI（请求数 / 总 Token / 实时 TPS / 缓存命中率 / 实扣），新增 Top5、模型表搜索、厂商筛选、隐藏未使用、分页和行展开明细。
- 修复实时 TPS 口径：`output_tps = Σ输出 token ÷ Σ请求耗时`（`active_seconds`），不再用「距最早完成秒数」做分母；新增 `last_output_tps`，保留 `span_seconds` 仅用于显示。
- 修复 5 秒定时器捕获旧 `loadUsage` 的问题：改为 `setInterval(function(){ loadUsage(); }, 5000)`，新用量板不会被旧布局覆盖。
- 用量 API 增加 `configured` 标记；管理端默认勾选「仅当前配置」，按厂商 / Provider / 模型表只显示当前 `gateway_config.json` 里的条目，历史数据保留，取消勾选即可查看全部。
- 示例配置与 `.env.example` 对齐当前运行配置：删除 `opencode-zen` provider 和 `META_MODEL_API_KEY`。
- 同步更新 README、`.env.example`、`.gitignore` 和 `.design/` 设计契约。

### v1.4.4（2026-11-13）

- 修复 `muse-ember-1.0`「能连上但没回复」：WorkBuddy 发 `reasoning_effort: "none"`，上游只支持 `minimal/low/medium/high/xhigh/max`，直接 400。网关现在按 `model_meta.reasoning` 把 `none` 降到最低档（`minimal`），其他不支持的值删除。
- 同时处理 Responses 的 `reasoning.effort`。
- 管理端模型测试 `max_tokens` 从 64 提到 512，避免思考模型把输出配额耗光导致回复为空；空回复会显示原因。
- `models.json` 的 `muse-ember-1.0` 地址从已失效的 `192.168.1.101` 改为 `127.0.0.1`。
- 修复局域网 IP 误选 TUN 网卡：`lan_urls` 现在跳过 `Meta`/`cfw-tap`/`vEthernet` 等虚拟网卡和 `198.18.0.0/15`，避免客户端把 `198.18.0.1` 走代理导致 502。`models.json` 里的 `198.18.0.1:8788` 已改回 `127.0.0.1:8788`。

### v1.4.3（2026-11-13）

- 新增每秒 Token（TPS）：按请求输出 token ÷ 耗时计算，统计 `tps_sum/tps_count/tps_max`，管理端显示「平均 TPS」。
- 新增近 60 秒实时 TPS：`/admin/api/usage` 返回 `live`（输出/输入/合计 TPS、按模型明细），管理端每 5 秒刷新。
- 用量按实际生效模型统计，智能别名请求归入真实模型；日志打印 `tps=...`。
- 修复 `muse-ember-1.0` 401：`opencode-zen` 改用与 `opencode-go` 相同的 OpenCode key（Zen base 不认 Go key 之外的旧 key），现在代理下 `200 OK`，扣费走账户余额。
- `gateway_config.example.json` 中 `opencode-zen.api_key` 改为 `${OPENCODE_GO_API_KEY}`。

### v1.4.2（2026-11-11）

- 新增智能路由：`smart_aliases`（智能别名）、`fallbacks`（故障转移）、失败冷却（`cooldown_seconds`）。
- 请求别名（如 `cheap`）或主模型失败时，自动切换候选模型，并同步改写请求体 `model`；别名出现在 `/v1/models`。
- 管理端新增「智能路由」卡片，可编辑路由 JSON、查看冷却中的模型；`GET /admin/api/routing` 返回配置/健康/别名链。

### v1.4.1（2026-11-10）

- Token 用量新增「按厂商」汇总（按模型名前缀推断厂商，可用 `model_meta.vendor` 覆盖），模型表增加厂商列。
- 总览新增「Go 本地 Token」：`opencode-go` provider 累计 token；服务端只提供百分比，无法提供全量 token。
- `/admin/api/usage` 返回 `vendors`，`models[]` 含 `vendor`。

### v1.4.0（2026-11-10）

- 新增 Zen 服务端余额监控：`zen_console` 配置 Console 会话 Cookie 后，网关读取 `/console/api/billing/status`、`/console/api/budgets/org`、`/console/api/go/status`，管理端展示真实余额、可用额度、本月支出/预算；`/admin/api/quota` 返回 `zen` 字段。
- 管理端「Zen 余额」卡片支持直接填写/清除 Console Cookie；热重载会清空 Zen/Console 缓存，避免配置改动后仍显示旧状态。
- 明确 Go 与 Zen 的监控边界：Go 使用 `/zen/go/v1/usage`（API key），Zen 余额只能通过 Console 会话读取（官方暂无 API-key 余额接口）。

### v1.3.0（2026-11-10）

- 局域网访问：默认监听 `0.0.0.0:8787`，管理端显示局域网 IP 的 OpenAI/Anthropic 端点；`/admin/api/status` 返回 `lan_urls`。
- 管理端访问控制：`/admin*` 默认仅本机；新增 `admin.allow_remote` + `admin.token`，远程通过 `?token=`、Cookie 或 `X-Admin-Token` 访问。
- 恢复 `opencode-zen` provider（`muse-ember-1.0`、`deepseek-v4.1-flash-free`）。

### v1.2.0（2026-11-07）

- 新增 Go 额度监控：直接读取服务端 `GET /zen/go/v1/usage`，展示 5 小时滚动 / 本周 / 本月真实百分比与重置时间；后台每 5 分钟采样并估算“预计用完时间”（近 1 小时回归速度，样本不足用窗口平均）；本地 `allowance` 累计作为按模型参考；API 为 `GET /admin/api/quota`。
- 配置保存增加 `X-Config-Revision` 版本校验：旧管理端页面再保存会返回 409，避免覆盖新模型/额度配置。
- Token 用量统一按中文大单位显示：<100 万显示实际数字，≥100 万用百万/千万/亿/十亿/百亿/千亿/万亿。
- 新增缓存命中率：`缓存读 Token ÷ 输入 Token`，在总览和按 Provider/模型表中展示，API 返回 `cache_hit_rate`。
- 新增 `opencode-zen` provider：Go / Zen 目录分开，`muse-ember-1.0` 走 Zen base；Meta 模型需代理，使用前需 Zen 余额。
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
