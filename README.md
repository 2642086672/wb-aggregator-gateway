# WB Aggregator Gateway

**v1.5.2** · Python 3.9+ · single file, no database, no frontend build step

A lightweight OpenAI/Anthropic-compatible multi-provider gateway: one endpoint and one key in front of several upstream providers, with per-model proxy routing, request sanitization, smart aliases with failover, token/cost accounting, and a local admin UI.

---

## 最新版本（v1.5.2）

**Token 计量改为 K / M / B**

- 小于 1000 显示实际数字；≥1000 用 `K`（千）、`M`（百万）、`B`（十亿）缩进，最多两位小数：`12.35K`、`1.05M`、`1.45B`。
- 修正四舍五入晋档：`999,999` 以前会显示成 `1000K`，现在自动变成 `1M`；`999,999,999` → `1B`。
- 覆盖范围：用量总览 5 张 KPI、按厂商 / Provider / 模型三张表、模型能力表的上下文与输出上限、近 60 秒实时 TPS 明细。

**v1.5.1 内容**

**用量统计支持时间段筛选**

- 管理端「用量」板新增 **今日 / 本周 / 本月 / 全部** 四档按钮。
- 接口：`GET /admin/api/usage?period=today|week|month|all`，返回 `period_start` / `period_end` / `period_note`。
- `gateway_usage.json` 新增按天累计（`days`）；时间段查询把对应日期的数据重新聚合，`all` 直接返回累计值。
- 按天记录从 v1.5.1 起才有，更早的历史只能用「全部」查看，页面会提示。

**局域网地址过滤**

- 新增 `_is_lan_ipv4()`：排除回环、`169.254/16`、`198.18.0.0/15`（Clash TUN），以及 `Meta` / `cfw-tap` / `vEthernet` / TAP / TUN 等虚拟网卡。
- `lan_urls` 只返回真实 WLAN / 以太网地址，避免客户端把 `198.18.0.1` 当外网走代理导致 502。

**其他**

- `.env.example` 增加 `CODINGPASS_API_KEY`；`.gitignore` 增加 `gateway_usage.json.bak*`。
- `.design/admin_source.html` 基准刷新为当前线上页面，`python .design/verify_ui.py .design/admin_new.html` 全通过。

---

## Features

- **Two protocol entry points** — OpenAI Chat Completions / Responses and Anthropic Messages on one port. Anthropic clients can point at either `/v1/messages` or `/messages`.
- **Multi-provider aggregation** — OpenCode Go, LongCat, CodingPass, and any OpenAI-compatible endpoint. Each provider keeps its own upstream key; clients only ever see the gateway key.
- **Per-model proxy routing** — region-restricted models go through a local proxy; everything else connects directly for speed.
- **Request sanitization** — strips client-private fields (WorkBuddy's `startsNewUserRequest`, `traceId`, etc.) that upstream rejects; also fixes protocol mismatches (`max_tokens` ↔ `max_output_tokens`) and clamps tiny output budgets.
- **Reasoning-effort normalization** — if a client sends `reasoning_effort: "none"` to a model that cannot disable thinking, the gateway downgrades it to the lowest supported level instead of letting upstream return 400.
- **Smart aliases + failover** — `smart` / `cheap` / `vision` / `coding` aliases pick a model by candidate order, skip cooling models, and fall back on retryable errors. Fallbacks are safe for streaming: the error body is fully read before anything is forwarded.
- **Token & cost accounting** — input/output/cached/reasoning tokens, cache-hit rate (`cached ÷ input`), real charged amount vs price-list estimate, per-vendor / per-provider / per-model breakdowns, and per-day aggregation.
- **Live TPS** — `output_tps = Σ output tokens ÷ Σ request duration` over the last 60 s, plus per-model rows and a "last request" figure.
- **Token units** — below 1,000 shows the exact number; above that switches to `K` / `M` / `B` (max two decimals, e.g. `12.35K`, `1.05M`, `1.45B`), with automatic rollover so `999,999` renders as `1M` instead of `1000K`.
- **Quota monitoring** — reads the real OpenCode Go server-side usage endpoint (`/zen/go/v1/usage`) for 5-hour rolling / weekly / monthly percentages, reset times, and an ETA estimate; optionally reads Zen prepaid balance via a Console session cookie.
- **Admin UI** — four switchable panels (runtime / usage / upstream / rules), plain HTML embedded in the Python file. No CDN, no build step.
- **Security** — inbound client-key auth, admin UI restricted to localhost by default, config saves guarded by an `X-Config-Revision` optimistic lock, and `${ENV}` indirection for all upstream secrets.

## Admin UI

| Panel | Contents |
|---|---|
| `#run` — 运行台 | Status, endpoints (local + LAN), client access keys, provider cards with model tags |
| `#usage` — 用量 | 5 KPIs (requests / total tokens / live TPS / cache-hit rate / charged), Top-5 bar chart, vendor / provider / model tables, period filter, search, vendor filter, hide-unused, pagination, expandable rows |
| `#upstream` — 上游 | Model capability table (context, output cap, images, tools, reasoning levels, price, route), Go quota progress with ETA, Zen balance |
| `#rules` — 规则 | Smart routing / fallbacks editor, cooldown list, provider add/edit/delete, live log, reload |

Model tags are colour-coded by their last test result: green = OK, red = failed, purple = untested, 🌐 = routed through the proxy.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Health check |
| GET | `/v1/models` | Model list (Anthropic format when `anthropic-version` header present) |
| POST | `/v1/chat/completions` | OpenAI Chat Completions (streaming supported) |
| POST | `/v1/responses` | OpenAI Responses (streaming supported) |
| POST | `/v1/messages` | Anthropic Messages (streaming supported) |
| POST | `/v1/messages/count_tokens` | Token counting |
| GET | `/admin` | Admin UI |
| GET/POST | `/admin/api/*` | Admin JSON API (`status`, `usage`, `quota`, `routing`, `config`, `test`, …) |

Client base URL is the root, not the full path: `http://127.0.0.1:8787/v1` for OpenAI clients, `http://127.0.0.1:8787` for Anthropic clients.

## Quick start (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item gateway_config.example.json gateway_config.json
```

Set credentials in the current session (never commit them):

```powershell
$env:OPENCODE_GO_API_KEY = "your-opencode-go-key"
$env:LONGCAT_API_KEY = "your-longcat-key"
$env:CODINGPASS_API_KEY = "your-codingpass-key"
$env:GATEWAY_CLIENT_KEYS = "your-client-key"
$env:GATEWAY_ADMIN_TOKEN = "your-admin-token"
python gateway.py
```

The gateway resolves secret references like `${OPENCODE_GO_API_KEY}` from environment variables (or a local `.env`). `GATEWAY_CLIENT_KEYS` accepts comma-separated values.

## Configuration

Copy `gateway_config.example.json` to `gateway_config.json`, then supply secrets via environment variables. Never commit `gateway_config.json`, logs, usage records, or request captures.

- `providers[]` — `name`, `base_url`, optional `anthropic_base_url`, `api_key`, `models[]`, `proxy_models[]`, `use_proxy`, `model_meta{}`.
- `model_meta.<model>` — `context`, `output`, `images`, `tools`, `reasoning[]`, `pricing{input, output, cache_read, cache_write}`, `monthly_limit`, `free`, optional `vendor`.
- `proxy_models` lists models routed through the configured local proxy. Models not listed connect directly unless their provider sets `use_proxy: true`.
- `client_auth.enabled` + `client_auth.keys[]` — inbound gateway keys. Requests without a valid key get `401`.
- `routing.fallbacks` and `routing.smart_aliases` define candidate chains; `max_attempts` and `cooldown_seconds` control retry depth and failure cooling.
- `zen_console` — optional `session_cookie` / `org_id` for reading the Zen prepaid balance; leave the cookie empty to disable.
- Model prices in `model_meta.*.pricing` are USD per 1 million tokens. Cache-write prices are intentionally left unset when unknown.
- Admin config saves use an `X-Config-Revision` optimistic lock, so a stale browser tab cannot overwrite newer config.

## Security notes

- Keep real provider keys and client keys out of source control — use `${ENV}` references only.
- The admin UI has no separate login. Remote access requires `admin.allow_remote: true` plus a non-empty `admin.token`.
- Bind to `127.0.0.1` unless you add proper network authentication and TLS.
- The gateway logs model names, token counts and error codes. It does not write request bodies to the log by default, but `gateway_last_request.json` keeps the most recent request for debugging — keep it out of the repo.

## Files

- `gateway.py` — gateway and embedded admin UI
- `gateway_config.example.json` — sanitized configuration template (secrets referenced as `${ENV}`)
- `requirements.txt` — Python dependency
- `gateway_dev_doc.md` — developer notes (Chinese): architecture, request path, configuration, known pitfalls
- `wb_opencode_bridge.py` — minimal single-provider bridge (v1.0.0 precursor, kept for reference)
- `opencode_go_api.md` — upstream API spec notes
- `.design/` — UI design contract and the tooling used to rewrite the admin page safely

## Version history

| Version | Highlights |
|---|---|
| **v1.5.2** | Token units switched to `K` / `M` / `B` with automatic rollover (`999,999` → `1M`) |
| **v1.5.1** | Period-filtered usage (today/week/month/all) with per-day storage; LAN interface filtering for virtual/TUN adapters |
| v1.5.0 | Admin UI rebuilt as four dashboards; usage KPIs, Top-5, search/filter/pagination/expand; TPS denominator fixed; "configured only" filter |
| v1.4.4 | `reasoning_effort` normalization (`none` → lowest supported); admin test budget raised; LAN/TUN address fix |
| v1.4.3 | Per-request TPS and 60-second live TPS |
| v1.4.2 | Smart routing: aliases, fallbacks, failure cooldown |
| v1.4.1 | Per-vendor token breakdown and "Go local tokens" KPI |
| v1.4.0 | Server-side Go quota monitoring with ETA; Zen balance card |
| v1.3.x | Inbound client key auth, Anthropic `/v1/messages`, model capability table, per-model pricing |
| v1.2.0 | Web admin UI, token usage accounting, model tests |
| v1.0.0 | Single-provider bridge (`wb_opencode_bridge.py`) |

## Requirements

- Python 3.9+
- `requests>=2.31,<3` (pulls in `certifi`, `urllib3`, `idna` — required for upstream TLS verification)
