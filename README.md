# WB Aggregator Gateway

Lightweight OpenAI/Anthropic-compatible multi-provider gateway with per-model routing, request sanitization, token accounting, and a local admin UI.

## Files

- `gateway.py` — gateway and embedded admin UI
- `gateway_config.example.json` — sanitized configuration template
- `requirements.txt` — Python dependency

## Quick start (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item gateway_config.example.json gateway_config.json
```

Set credentials in the current PowerShell session (do not commit them):

```powershell
$env:OPENCODE_GO_API_KEY = "your-opencode-go-key"
$env:LONGCAT_API_KEY = "your-longcat-key"
$env:GATEWAY_CLIENT_KEYS = "your-client-key"
python gateway.py
```

The gateway reads secret references like `${OPENCODE_GO_API_KEY}` from environment variables. `GATEWAY_CLIENT_KEYS` can contain comma-separated client keys.

## Endpoints

- Admin UI: `http://127.0.0.1:8787/admin`
- OpenAI Base URL: `http://127.0.0.1:8787/v1`
- OpenAI chat completions: `POST /v1/chat/completions`
- OpenAI responses: `POST /v1/responses`
- Anthropic messages: `POST /v1/messages`
- Models: `GET /v1/models`

## Configuration

Copy `gateway_config.example.json` to `gateway_config.json`, then set environment variables. Do not commit `gateway_config.json`, logs, usage records, or request captures.

`proxy_models` lists models routed through the configured Clash proxy. Models not in the list use a direct connection unless their provider has `use_proxy: true`.

Model prices in `model_meta.*.pricing` are USD per 1 million tokens. Missing cache-write prices are deliberately left unset.

## Security notes

- Keep real provider keys and client keys out of source control.
- The admin UI is intended for local use. It has no separate admin login.
- Bind to `127.0.0.1` unless you add proper network authentication and TLS.
