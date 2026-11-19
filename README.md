# WB Aggregator Gateway

Lightweight OpenAI/Anthropic-compatible multi-provider gateway with per-model routing, request sanitization, smart aliases with failover, token accounting, and a local admin UI.

Single-file Python (`stdlib` + `requests`). No database, no frontend build step.

## Features

- **Two protocol entry points** — OpenAI Chat Completions / Responses and Anthropic Messages on one port.
- **Multi-provider aggregation** — OpenCode Go, LongCat, CodingPass, and any OpenAI-compatible endpoint.
- **Per-model proxy routing** — region-restricted models go through a local proxy; everything else connects directly for speed.
- **Request sanitization** — strips client-private fields (WorkBuddy's `startsNewUserRequest`, `traceId`, etc.) that upstream rejects; also fixes protocol mismatches (`max_tokens` ↔ `max_output_tokens`, reasoning-effort levels).
- **Smart aliases + failover** — `smart` / `cheap` / `vision` / `coding` aliases pick a model by candidate order, skip cooling models, and fall back on retryable errors.
- **Token & cost accounting** — real input/output/cached token counts, cache-hit rate, live TPS, per-vendor/per-provider/per-model breakdowns.
- **Admin UI** — four switchable panels (runtime / usage / upstream / rules), plain HTML embedded in the Python file.

## Files

- `gateway.py` — gateway and embedded admin UI
- `gateway_config.example.json` — sanitized configuration template (secrets referenced as `${ENV}`)
- `requirements.txt` — Python dependency
- `gateway_dev_doc.md` — developer notes (Chinese): architecture, request path, configuration, known pitfalls
- `wb_opencode_bridge.py` — minimal single-provider bridge (v1.0.0 precursor, kept for reference)
- `opencode_go_api.md` — upstream API spec notes
- `.design/` — UI design contract and the tooling used to rewrite the admin page safely

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

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Health check |
| GET | `/v1/models` | Model list (Anthropic format when `anthropic-version` header present) |
| POST | `/v1/chat/completions` | OpenAI Chat Completions |
| POST | `/v1/responses` | OpenAI Responses |
| POST | `/v1/messages` | Anthropic Messages |
| POST | `/v1/messages/count_tokens` | Token counting |
| GET/POST | `/admin`, `/admin/api/*` | Admin UI and its JSON API |

## Configuration

Copy `gateway_config.example.json` to `gateway_config.json`, then supply secrets via environment variables. Never commit `gateway_config.json`, logs, usage records, or request captures.

- `proxy_models` lists models routed through the configured local proxy. Models not listed connect directly unless their provider sets `use_proxy: true`.
- Model prices in `model_meta.*.pricing` are USD per 1 million tokens. Cache-write prices are intentionally left unset when unknown.
- `routing.fallbacks` and `routing.smart_aliases` define candidate chains; `max_attempts` and `cooldown_seconds` control retry depth and failure cooling.
- Admin config saves use an `X-Config-Revision` optimistic lock, so a stale browser tab cannot overwrite newer config.

## Security notes

- Keep real provider keys and client keys out of source control — use `${ENV}` references only.
- The admin UI has no separate login. Remote access requires `admin.allow_remote: true` plus a non-empty `admin.token`.
- Bind to `127.0.0.1` unless you add proper network authentication and TLS.

## Requirements

- Python 3.9+
- `requests>=2.31,<3` (pulls in `certifi`, `urllib3`, `idna` — required for upstream TLS verification)
