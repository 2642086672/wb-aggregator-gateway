#!/usr/bin/env python3
"""
聚合网关（OpenAI 兼容）
- 多 provider 聚合：opencode go / longcat / 其他，统一入口
- 按模型自动选路：地区受限模型走代理，其余直连
- 自动清洗 WorkBuddy 私有字段（startsNewUserRequest/traceId 等）
- 自动注入 opencode 需要的 x-opencode-session 头

配置文件：gateway_config.json（同目录）
监听：默认 127.0.0.1:8787
端点：POST /v1/chat/completions、POST /v1/responses、GET /v1/models
"""
import hmac
import json
import os
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

GW_VERSION = "1.2.0"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "gateway_config.json")
LOG_PATH = os.path.join(BASE_DIR, "gateway.log")
USAGE_PATH = os.path.join(BASE_DIR, "gateway_usage.json")


def load_dotenv():
    """Load simple KEY=VALUE entries from an ignored local .env file; existing env wins."""
    env_path = os.path.join(BASE_DIR, ".env")
    try:
        with open(env_path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                name = name.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if name:
                    os.environ.setdefault(name, value)
    except FileNotFoundError:
        pass
    except OSError as e:
        print(f"warning: unable to read .env: {e}", file=sys.stderr)


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


load_dotenv()
CFG = load_config()
START_TIME = time.time()
LISTEN_HOST = CFG.get("listen", {}).get("host", "127.0.0.1")
LISTEN_PORT = int(CFG.get("listen", {}).get("port", 8787))
PROVIDERS = CFG.get("providers", [])
DEFAULT_PROXY = CFG.get("proxy", "")


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def reload_config():
    """重新读取配置文件（热重载）。"""
    global CFG, PROVIDERS, DEFAULT_PROXY
    CFG = load_config()
    PROVIDERS = CFG.get("providers", [])
    DEFAULT_PROXY = CFG.get("proxy", "")


def validate_config(cfg):
    """校验配置结构，返回错误字符串；合法时返回 None。"""
    if not isinstance(cfg, dict):
        return "config must be a JSON object"
    providers = cfg.get("providers")
    if not isinstance(providers, list):
        return "providers must be a list"
    if not providers:
        return "at least one provider is required"

    names = set()
    for i, p in enumerate(providers):
        if not isinstance(p, dict):
            return f"providers[{i}] must be an object"
        name = str(p.get("name") or "").strip()
        if not name:
            return f"providers[{i}].name is required"
        if name in names:
            return f"duplicate provider name: {name}"
        names.add(name)

        base_url = str(p.get("base_url") or "").strip()
        if not re.match(r"^https?://", base_url, re.I):
            return f"providers[{i}].base_url must start with http:// or https://"
        anthropic_url = str(p.get("anthropic_base_url") or "").strip()
        if anthropic_url and not re.match(r"^https?://", anthropic_url, re.I):
            return f"providers[{i}].anthropic_base_url must start with http:// or https://"

        models = p.get("models")
        if not isinstance(models, list):
            return f"providers[{i}].models must be a list"
        if not all(isinstance(m, str) and m.strip() for m in models):
            return f"providers[{i}].models must contain non-empty strings"

        for key in ("proxy_models",):
            val = p.get(key, [])
            if not isinstance(val, list) or not all(isinstance(m, str) for m in val):
                return f"providers[{i}].{key} must be a list of strings"
        for key in ("headers", "model_map", "model_meta"):
            if p.get(key) is not None and not isinstance(p.get(key), dict):
                return f"providers[{i}].{key} must be an object"

        for key in ("api_key", "anthropic_api_key"):
            val = p.get(key, "")
            if val is not None and not isinstance(val, str):
                return f"providers[{i}].{key} must be a string"

        min_out = p.get("min_output_tokens")
        if min_out is not None and (not isinstance(min_out, int) or min_out < 0):
            return f"providers[{i}].min_output_tokens must be a non-negative integer"

    proxy = cfg.get("proxy", "")
    if proxy and not re.match(r"^https?://", str(proxy), re.I):
        return "proxy must start with http:// or https://"

    client_auth = cfg.get("client_auth")
    if client_auth is not None:
        if not isinstance(client_auth, dict):
            return "client_auth must be an object"
        enabled = client_auth.get("enabled", False)
        if not isinstance(enabled, bool):
            return "client_auth.enabled must be a boolean"
        keys = client_auth.get("keys", [])
        if not isinstance(keys, list) or not all(isinstance(k, str) and k.strip() for k in keys):
            return "client_auth.keys must be a list of non-empty strings"
        if enabled and not keys:
            return "client_auth.keys cannot be empty when client_auth.enabled is true"

    listen = cfg.get("listen")
    if listen is not None:
        if not isinstance(listen, dict):
            return "listen must be an object"
        port = listen.get("port", 8787)
        if not isinstance(port, int) or not (1 <= port <= 65535):
            return "listen.port must be an integer between 1 and 65535"
    return None


def save_config(new_cfg):
    """保存配置到文件并热重载（先写临时文件，避免写入中断损坏配置）。"""
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(new_cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)
    reload_config()


def read_logs(lines=200):
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()[-lines:]
    except Exception:
        return []


USAGE_LOCK = threading.Lock()
USAGE_FIELDS = ("input_tokens", "output_tokens", "total_tokens",
                "cached_tokens", "cache_write_tokens", "reasoning_tokens",
                "usage_missing")


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _blank_usage():
    return {
        "requests": 0, "ok": 0, "errors": 0,
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        "cached_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
        "usage_missing": 0,
        "cost": 0.0, "list_cost": 0.0,
        "latency_ms": 0, "last_latency_ms": 0, "last_used": "",
    }


def _load_usage():
    try:
        with open(USAGE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("usage file must be an object")
        data.setdefault("since", _now_iso())
        data.setdefault("updated", "")
        totals = data.get("totals")
        data["totals"] = totals if isinstance(totals, dict) else _blank_usage()
        for key in ("providers", "models"):
            if not isinstance(data.get(key), dict):
                data[key] = {}
        return data
    except Exception:
        return {"since": _now_iso(), "updated": "", "totals": _blank_usage(),
                "providers": {}, "models": {}}


USAGE = _load_usage()


def _save_usage_locked():
    USAGE["updated"] = _now_iso()
    tmp = USAGE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(USAGE, f, ensure_ascii=False, indent=2)
    os.replace(tmp, USAGE_PATH)


def _bump_usage(stat, usage, ok, latency_ms):
    stat["requests"] = int(stat.get("requests", 0)) + 1
    stat["ok"] = int(stat.get("ok", 0)) + (1 if ok else 0)
    stat["errors"] = int(stat.get("errors", 0)) + (0 if ok else 1)
    for key in USAGE_FIELDS:
        stat[key] = int(stat.get(key, 0)) + int(usage.get(key, 0) or 0)
    if not usage:
        stat["usage_missing"] = int(stat.get("usage_missing", 0)) + 1
    for key in ("cost", "list_cost"):
        stat[key] = round(float(stat.get(key, 0)) + float(usage.get(key, 0) or 0), 8)
    latency = max(0, int(latency_ms or 0))
    stat["latency_ms"] = int(stat.get("latency_ms", 0)) + latency
    stat["last_latency_ms"] = latency
    stat["last_used"] = _now_iso()


def model_pricing(provider_name, model):
    for p in PROVIDERS:
        if p.get("name") != provider_name:
            continue
        meta = p.get("model_meta") or {}
        entry = meta.get(model) or {}
        pricing = entry.get("pricing") or {}
        return pricing if isinstance(pricing, dict) else {}
    return {}


def estimate_list_cost(provider_name, model, usage):
    """按 gateway_config 中 USD / 1M token 单价估算标价成本。"""
    price = model_pricing(provider_name, model)
    if not price:
        return None
    try:
        inp = max(0, int(usage.get("input_tokens", 0) or 0))
        out = max(0, int(usage.get("output_tokens", 0) or 0))
        cached = min(inp, max(0, int(usage.get("cached_tokens", 0) or 0)))
        cache_write = min(max(0, inp-cached), max(0, int(usage.get("cache_write_tokens", 0) or 0)))
        normal_in = max(0, inp-cached-cache_write)
        in_price = float(price.get("input") or 0)
        out_price = float(price.get("output") or 0)
        read_price = price.get("cache_read")
        write_price = price.get("cache_write")
        read_price = in_price if read_price is None else float(read_price)
        write_price = in_price if write_price is None else float(write_price)
        return round((normal_in*in_price + cached*read_price + cache_write*write_price + out*out_price)/1_000_000, 8)
    except (TypeError, ValueError):
        return None


def record_usage(provider_name, model, usage, status, latency_ms, upstream_model=None):
    """记录一次请求的 token 用量（按 provider 和模型累计）。"""
    if not provider_name or not model:
        return
    ok = 200 <= int(status or 0) < 300
    usage = dict(usage or {})
    # 上游 cost 保留为实扣；配置里的截图价格作为标价估算，优先于上游 list_cost。
    estimate = estimate_list_cost(provider_name, upstream_model or model, usage)
    if estimate is not None:
        usage["list_cost"] = estimate
    with USAGE_LOCK:
        _bump_usage(USAGE["totals"], usage, ok, latency_ms)
        pstat = USAGE["providers"].setdefault(provider_name, _blank_usage())
        pstat["name"] = provider_name
        _bump_usage(pstat, usage, ok, latency_ms)
        key = f"{provider_name}::{model}"
        mstat = USAGE["models"].setdefault(key, _blank_usage())
        mstat["model"] = model
        mstat["provider"] = provider_name
        mstat["upstream_model"] = upstream_model or model
        _bump_usage(mstat, usage, ok, latency_ms)
        try:
            _save_usage_locked()
        except Exception as e:
            log(f"ERR usage save: {e}")


def reprice_usage():
    """用当前模型价对历史累计 token 做一次标价成本重算。"""
    with USAGE_LOCK:
        for stat in USAGE.get("models", {}).values():
            estimate = estimate_list_cost(stat.get("provider", ""),
                                          stat.get("upstream_model") or stat.get("model", ""), stat)
            if estimate is not None:
                stat["list_cost"] = estimate
        for provider_name, pstat in USAGE.get("providers", {}).items():
            pstat["list_cost"] = round(sum(float(m.get("list_cost", 0) or 0)
                                             for m in USAGE.get("models", {}).values()
                                             if m.get("provider") == provider_name), 8)
        USAGE["totals"]["list_cost"] = round(sum(float(m.get("list_cost", 0) or 0)
                                                   for m in USAGE.get("models", {}).values()), 8)
        try:
            _save_usage_locked()
        except Exception as e:
            log(f"ERR usage reprice save: {e}")


def reset_usage():
    with USAGE_LOCK:
        USAGE["since"] = _now_iso()
        USAGE["updated"] = ""
        USAGE["totals"] = _blank_usage()
        USAGE["providers"] = {}
        USAGE["models"] = {}
        try:
            _save_usage_locked()
        except Exception as e:
            log(f"ERR usage save: {e}")


def usage_snapshot():
    with USAGE_LOCK:
        providers = []
        for name, stat in USAGE.get("providers", {}).items():
            item = {"name": name}
            item.update(stat)
            providers.append(item)
        models = []
        for key, stat in USAGE.get("models", {}).items():
            item = {"key": key}
            item.update(stat)
            models.append(item)
        providers.sort(key=lambda x: int(x.get("total_tokens", 0)), reverse=True)
        models.sort(key=lambda x: int(x.get("total_tokens", 0)), reverse=True)
        return {
            "since": USAGE.get("since", ""),
            "updated": USAGE.get("updated", ""),
            "totals": dict(USAGE.get("totals", {})),
            "providers": providers,
            "models": models,
        }


def admin_html():
    """管理端 HTML（把版本号占位符替换掉，方便确认页面是否更新）。"""
    return ADMIN_HTML.replace("{{VERSION}}", GW_VERSION)


ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>聚合网关管理端</title>
<style>
:root{--bg:#f5f6f8;--card:#fff;--border:#e3e6ea;--text:#1f2329;--muted:#6b7280;--primary:#2563eb;--primary-h:#1d4ed8;--danger:#dc2626;--ok:#16a34a;--warn:#d97706;--tag:#eef2ff;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text);font-size:14px;line-height:1.5}
header{background:var(--card);border-bottom:1px solid var(--border);padding:14px 24px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10}
header h1{font-size:17px;font-weight:600;display:flex;align-items:center;gap:8px}
.ver{font-size:11px;font-weight:500;color:var(--muted);background:var(--bg);border:1px solid var(--border);padding:1px 7px;border-radius:999px}
.header-right{display:flex;align-items:center;gap:10px}
.badge{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:500;background:#dcfce7;color:#166534}
.badge::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--ok)}
.badge.off{background:#fee2e2;color:#991b1b}.badge.off::before{background:var(--danger)}
button{cursor:pointer;border:1px solid var(--border);background:var(--card);color:var(--text);padding:7px 14px;border-radius:8px;font-size:13px;font-weight:500;transition:.15s}
button:hover{border-color:#c7ccd3;background:#f9fafb}
button.primary{background:var(--primary);border-color:var(--primary);color:#fff}
button.primary:hover{background:var(--primary-h)}
button.danger{color:var(--danger);border-color:#fecaca}
button.danger:hover{background:#fef2f2}
button.sm{padding:4px 10px;font-size:12px;border-radius:6px}
main{max-width:1200px;margin:0 auto;padding:20px 24px;display:grid;gap:16px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px 20px}
.card h2{font-size:14px;font-weight:600;margin-bottom:14px;display:flex;align-items:center;justify-content:space-between}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.stat{background:var(--bg);border-radius:10px;padding:14px 16px}
.stat .label{font-size:12px;color:var(--muted);margin-bottom:6px}
.stat .value{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}
.stat .value small{font-size:12px;font-weight:400;color:var(--muted)}
.provider{border:1px solid var(--border);border-radius:10px;padding:14px 16px;margin-bottom:10px}
.provider-head{display:flex;align-items:center;justify-content:space-between;gap:10px}
.provider-name{font-weight:600;font-size:14px;display:flex;align-items:center;gap:8px}
.provider-name .dot{width:8px;height:8px;border-radius:50%;background:var(--ok)}
.provider-meta{font-size:12px;color:var(--muted);margin-top:6px;word-break:break-all;font-family:var(--mono)}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.tag{background:var(--tag);color:#3730a3;padding:3px 9px;border-radius:6px;font-size:12px;font-family:var(--mono);cursor:pointer;transition:.15s;border:1px solid transparent}
.tag:hover{border-color:#a5b4fc}
.tag.proxy{background:#fef3c7;color:#92400e}
.tag.testing{background:#e5e7eb;color:#6b7280;cursor:wait}
.tag.ok{background:#dcfce7;color:#166534;border-color:#86efac}
.tag.fail{background:#fee2e2;color:#991b1b;border-color:#fca5a5}
.tag.pill{background:#e0f2fe;color:#075985;cursor:default}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.log{background:#0f172a;color:#e2e8f0;border-radius:10px;padding:14px 16px;font-family:var(--mono);font-size:12px;line-height:1.7;height:320px;overflow:auto;white-space:pre-wrap;word-break:break-all}
.table-wrap{overflow:auto;max-height:440px;border:1px solid var(--border);border-radius:10px}
.table-wrap table{width:100%;border-collapse:collapse;font-size:12px}
.table-wrap th,.table-wrap td{padding:8px 10px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap}
.table-wrap th{position:sticky;top:0;background:var(--bg);font-weight:600;color:var(--muted);z-index:1}
.table-wrap tr:last-child td{border-bottom:none}
.table-wrap td.num{text-align:right;font-variant-numeric:tabular-nums;font-family:var(--mono)}
.table-wrap td.center{text-align:center}
.table-wrap td.mono{font-family:var(--mono)}
.hint-inline{font-size:12px;font-weight:400;color:var(--muted)}
.log .t{color:#64748b}
.empty{color:var(--muted);font-size:13px;padding:8px 0}
.modal-mask{position:fixed;inset:0;background:rgba(15,23,42,.45);display:none;align-items:center;justify-content:center;z-index:100;padding:20px}
.modal-mask.show{display:flex}
.modal{background:var(--card);border-radius:14px;padding:22px;width:100%;max-width:640px;max-height:90vh;overflow:auto;box-shadow:0 20px 50px rgba(0,0,0,.2)}
.modal h3{font-size:15px;margin-bottom:16px}
.field{margin-bottom:14px}
.field label{display:block;font-size:12px;font-weight:500;color:var(--muted);margin-bottom:6px}
.field input,.field textarea{width:100%;padding:8px 11px;border:1px solid var(--border);border-radius:8px;font-size:13px;font-family:inherit;background:#fff;color:var(--text)}
.field input:focus,.field textarea:focus{outline:none;border-color:var(--primary);box-shadow:0 0 0 3px rgba(37,99,235,.12)}
.field .hint{font-size:11px;color:var(--muted);margin-top:5px}
.checks{display:flex;flex-wrap:wrap;gap:14px}
.checks label{display:flex;align-items:center;gap:6px;font-size:13px;font-weight:400;color:var(--text);cursor:pointer}
.modal-foot{display:flex;justify-content:flex-end;gap:10px;margin-top:18px}
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1f2937;color:#fff;padding:10px 18px;border-radius:10px;font-size:13px;opacity:0;transition:.25s;z-index:200;pointer-events:none}
.toast.show{opacity:1}
@media(max-width:720px){main{padding:14px}.stats{grid-template-columns:repeat(2,1fr)}}
</style>
</head>
<body>
<header>
  <h1>⚡ 聚合网关管理端 <span class="ver">v{{VERSION}}</span></h1>
  <div class="header-right">
    <span class="badge" id="statusBadge">运行中</span>
    <button class="sm" onclick="reload()">重载配置</button>
    <button class="sm" onclick="testAll()">⚡ 一键测试</button>
    <button class="sm primary" onclick="openAdd()">+ 添加 Provider</button>
  </div>
</header>
<main>
  <div class="card">
    <h2>总览</h2>
    <div class="stats" id="stats"></div>
  </div>
  <div class="card">
    <h2>客户端接入密钥 <span class="hint-inline">客户端连网关用这个 key；上游 provider key 由网关管理</span></h2>
    <div class="field checks">
      <label><input type="checkbox" id="authEnabled"> 启用鉴权（关闭后任何人可访问）</label>
    </div>
    <div class="field">
      <label>允许的 key（每行一个）</label>
      <textarea id="authKeys" rows="3" placeholder="sk-gw-..."></textarea>
      <div class="hint">OpenAI 客户端：Authorization: Bearer &lt;key&gt; · Anthropic 客户端：x-api-key: &lt;key&gt;</div>
    </div>
    <div class="row">
      <button class="sm primary" onclick="saveAuth()">保存密钥</button>
      <button class="sm" onclick="genKey()">生成随机 key</button>
      <button class="sm" onclick="copyKey()">复制第一个</button>
    </div>
  </div>
  <div class="card">
    <h2>Providers</h2>
    <div id="providers"></div>
  </div>
  <div class="card">
    <h2>模型能力 <span class="hint-inline">来自 model_meta，可在编辑 Provider 中修改</span></h2>
    <div id="modelTable"></div>
  </div>
  <div class="card">
    <h2>Token 用量 <span class="hint-inline" id="usageSince"></span>
      <span>
        <button class="sm" onclick="loadUsage()">刷新</button>
        <button class="sm danger" onclick="resetUsage()">重置</button>
      </span>
    </h2>
    <div class="stats" id="usageTotals"></div>
    <div style="margin-top:18px">
      <h2 style="font-size:13px">按 Provider</h2>
      <div id="usageProviders"></div>
    </div>
    <div style="margin-top:18px">
      <h2 style="font-size:13px">按模型</h2>
      <div id="usageModels"></div>
    </div>
  </div>
  <div class="card">
    <h2>实时日志 <button class="sm" onclick="loadLogs()">刷新</button></h2>
    <div class="log" id="log"></div>
  </div>
</main>

<div class="modal-mask" id="modalMask">
  <div class="modal">
    <h3 id="modalTitle">添加 Provider</h3>
    <div class="field"><label>名称</label><input id="fName" placeholder="opencode-go"></div>
    <div class="field"><label>Base URL（OpenAI）</label><input id="fUrl" placeholder="https://api.example.com/v1"><div class="hint">OpenAI 兼容端点，不带 /chat/completions</div></div>
    <div class="field"><label>Anthropic Base URL（可选）</label><input id="fAnthropicUrl" placeholder="https://api.example.com/anthropic/v1"><div class="hint">不带 /messages；留空则不支持该 provider 的 /v1/messages（opencode.ai 可自动推导）</div></div>
    <div class="field"><label>API Key（OpenAI）</label><input id="fKey" placeholder="sk-..."></div>
    <div class="field"><label>Anthropic API Key（可选）</label><input id="fAnthropicKey" placeholder="留空则复用上面的 API Key"><div class="hint">/v1/messages 优先使用这个 key；留空则回落到 OpenAI API Key</div></div>
    <div class="field"><label>模型列表（每行一个）</label><textarea id="fModels" rows="5" placeholder="gpt-6-luna&#10;glm-5.3-flash"></textarea></div>
    <div class="field"><label>走代理的模型（每行一个，留空则全部直连）</label><textarea id="fProxyModels" rows="3" placeholder="gpt-6-luna"></textarea></div>
    <div class="field"><label>自定义请求头（JSON，可选）</label><textarea id="fHeaders" rows="2" placeholder='{"x-opencode-session":"{session}"}'></textarea><div class="hint">{session} 会自动替换为会话 ID</div></div>
    <div class="field checks">
      <label><input type="checkbox" id="fNeedsSession"> 需要 session 头（opencode）</label>
      <label><input type="checkbox" id="fUseProxy"> 全部模型走代理</label>
    </div>
    <div class="field"><label>模型 ID 映射（JSON，可选）</label><textarea id="fModelMap" rows="2" placeholder='{"gpt-6-luna-responses":"gpt-6-luna"}'></textarea></div>
    <div class="field"><label>模型元数据（JSON，可选）</label><textarea id="fMeta" rows="4" placeholder='{"gpt-6-luna":{"context":1050000,"input":922000,"output":128000,"images":true,"tools":true,"reasoning":["low","high"]}}'></textarea><div class="hint">用于管理端展示上下文 / 多模态 / 工具 / 思考强度；编辑 Provider 时会保留已有元数据</div></div>
    <div class="modal-foot">
      <button onclick="closeModal()">取消</button>
      <button class="primary" onclick="saveProvider()">保存</button>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
window.onerror = function(msg, url, line, col, err){
  var box = document.getElementById('errbox') || document.body;
  box.innerHTML = '<pre style="padding:24px;color:#dc2626;font:13px/1.6 monospace;white-space:pre-wrap;word-break:break-all">⚠ 脚本错误\n' +
    'message: ' + msg + '\nline: ' + line + ':' + (col||0) +
    (err && err.stack ? '\n\nstack:\n' + err.stack : '') + '</pre>';
  return false;
};
window.addEventListener('unhandledrejection', function(e){
  var box = document.getElementById('errbox') || document.body;
  box.innerHTML = '<pre style="padding:24px;color:#d97706;font:13px/1.6 monospace;white-space:pre-wrap">⚠ 未捕获的 Promise 错误\n' + (e.reason && (e.reason.stack || e.reason.message || String(e.reason))) + '</pre>';
});
</script>
<script>
let CFG = null;
let editingIndex = -1;

async function api(path, opts){
  const r = await fetch(path, Object.assign({cache:'no-store'}, opts||{}));
  const t = await r.text();
  try{ return JSON.parse(t); }catch(e){ return {raw:t}; }
}
function toast(msg){
  const el = document.getElementById('toast');
  el.textContent = msg; el.classList.add('show');
  setTimeout(()=>el.classList.remove('show'), 2200);
}
function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

async function load(){
  CFG = await api('/admin/api/config');
  render();
  loadLogs();
  loadUsage();
}
function fmtNum(v){ return v ? Number(v).toLocaleString('en-US') : '—'; }
function money(v){ return v == null ? '—' : '$'+Number(v).toFixed(Number(v)%1===0?2:3); }
function renderPrice(p){
  if(!p) return '<span class="empty">未录</span>';
  return '<span title="每 1M tokens · USD">I '+money(p.input)+' / O '+money(p.output)+'<br>R '+money(p.cache_read)+' / W '+money(p.cache_write)+'</span>';
}
function renderModelTable(providers){
  const rows = [];
  (providers||[]).forEach(p=>{
    const metas = p.model_meta || {};
    const px = new Set(p.proxy_models||[]);
    (p.models||[]).forEach(m=>{
      const mt = metas[m] || {};
      rows.push('<tr>'+
        '<td class="mono">'+esc(m)+'</td>'+
        '<td>'+esc(p.name)+'</td>'+
        '<td class="num">'+fmtNum(mt.context||mt.input)+'</td>'+
        '<td class="num">'+fmtNum(mt.output)+'</td>'+
        '<td class="center">'+(mt.images?'✅':'❌')+'</td>'+
        '<td class="center">'+(mt.tools?'✅':'❌')+'</td>'+
        '<td>'+esc((mt.reasoning||[]).join(' / ')||'—')+'</td>'+ 
        '<td>'+renderPrice(mt.pricing)+'</td>'+ 
        '<td>'+((px.has(m)||p.use_proxy)?'🌐 代理':'直连')+'</td>'+
      '</tr>');
    });
  });
  const box = document.getElementById('modelTable');
  if(!rows.length){ box.innerHTML = '<div class="empty">暂无模型元数据</div>'; return; }
  box.innerHTML = '<div class="table-wrap"><table><thead><tr>'+
    '<th>模型</th><th>Provider</th><th>上下文</th><th>输出上限</th>'+
    '<th>多模态</th><th>工具</th><th>思考强度</th><th>价格/1M Token<br><small>I/O · Cache R/W</small></th><th>出口</th>'+ 
    '</tr></thead><tbody>'+rows.join('')+'</tbody></table></div>';
}
function renderAuth(){
  const a = CFG.client_auth || {};
  document.getElementById('authEnabled').checked = !!a.enabled;
  document.getElementById('authKeys').value = (a.keys||[]).join('\n');
}
async function saveAuth(){
  const enabled = document.getElementById('authEnabled').checked;
  const keys = parseLines(document.getElementById('authKeys').value);
  if(enabled && !keys.length){ toast('启用鉴权时至少填一个 key'); return; }
  CFG.client_auth = {enabled: enabled, keys: keys};
  const r = await api('/admin/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(CFG)});
  if(!r.ok){ toast('保存失败：'+(r.error||'未知错误')); await load(); return; }
  await load(); toast('接入密钥已保存');
}
function genKey(){
  let hex;
  try{
    const arr = new Uint8Array(16); crypto.getRandomValues(arr);
    hex = Array.prototype.map.call(arr, b=>b.toString(16).padStart(2,'0')).join('');
  }catch(e){
    hex = Math.random().toString(16).slice(2).padEnd(32,'0').slice(0,32);
  }
  document.getElementById('authKeys').value = 'sk-gw-'+hex;
  document.getElementById('authEnabled').checked = true;
  toast('已生成，记得点保存');
}
async function copyKey(){
  const key = parseLines(document.getElementById('authKeys').value)[0];
  if(!key){ toast('没有可复制的 key'); return; }
  try{ await navigator.clipboard.writeText(key); toast('已复制'); }
  catch(e){ toast('复制失败，请手动选择'); }
}
function render(){
  renderAuth();
  const providers = CFG.providers||[];
  const totalModels = providers.reduce((a,p)=>a+(p.models||[]).length,0);
  const proxyModels = providers.reduce((a,p)=>a+(p.proxy_models||[]).length,0);
  document.getElementById('stats').innerHTML = `
    <div class="stat"><div class="label">Providers</div><div class="value">${providers.length}</div></div>
    <div class="stat"><div class="label">模型总数</div><div class="value">${totalModels}</div></div>
    <div class="stat"><div class="label">走代理模型</div><div class="value">${proxyModels} <small>/ ${totalModels}</small></div></div>
    <div class="stat"><div class="label">代理地址</div><div class="value" style="font-size:14px;font-family:var(--mono)">${esc(CFG.proxy||'（直连）')}</div></div>
    <div class="stat"><div class="label">监听</div><div class="value" style="font-size:14px;font-family:var(--mono)">${esc((CFG.listen||{}).host||'127.0.0.1')}:${esc((CFG.listen||{}).port||8787)}</div></div>
    <div class="stat"><div class="label">OpenAI 端点</div><div class="value" style="font-size:12px;font-family:var(--mono);word-break:break-all">http://${esc((CFG.listen||{}).host||'127.0.0.1')}:${esc((CFG.listen||{}).port||8787)}/v1</div></div>
    <div class="stat"><div class="label">Anthropic 端点</div><div class="value" style="font-size:12px;font-family:var(--mono);word-break:break-all">http://${esc((CFG.listen||{}).host||'127.0.0.1')}:${esc((CFG.listen||{}).port||8787)}</div></div>
  `;
  renderModelTable(providers);
  const box = document.getElementById('providers');
  if(!providers.length){ box.innerHTML = '<div class="empty">还没有 Provider，点右上角添加。</div>'; return; }
  box.innerHTML = providers.map((p,i)=>{
    const models = p.models||[];
    const px = new Set(p.proxy_models||[]);
    const metas = p.model_meta || {};
    const tags = models.map(m=>{
      const mt = metas[m];
      const tip = ['点击测试该模型'];
      if(mt){
        const ctxIn = mt.input || mt.context || '?';
        tip.push(`上下文 ${ctxIn} / ${mt.output || '?'}`);
        tip.push(`多模态 ${mt.images?'✅':'❌'}   工具 ${mt.tools?'✅':'❌'}`);
        if(mt.reasoning && mt.reasoning.length) tip.push(`思考 ${mt.reasoning.join(' / ')}`);
      }
      return `<span class="tag${px.has(m)?' proxy':''}" data-model="${esc(m)}" onclick="testModel(this)" title="${esc(tip.join('\n'))}">${esc(m)}${px.has(m)?' 🌐':''}</span>`;
    }).join('');
    return `<div class="provider">
      <div class="provider-head">
        <div class="provider-name"><span class="dot"></span>${esc(p.name)}</div>
        <div class="row">
          <button class="sm" onclick="testProvider(${i})">测试</button>
          <button class="sm" onclick="openEdit(${i})">编辑</button>
          <button class="sm danger" onclick="delProvider(${i})">删除</button>
        </div>
      </div>
      <div class="provider-meta">openai: ${esc(p.base_url)}</div>
      ${p.anthropic_base_url?`<div class="provider-meta">anthropic: ${esc(p.anthropic_base_url)} · key: ${p.anthropic_api_key?esc((p.anthropic_api_key||'').slice(0,10))+'…':'复用 OpenAI key'}</div>`:''}
      <div class="provider-meta">key: ${esc((p.api_key||'').slice(0,10))}…${p.needs_session?' · needs_session':''}${p.use_proxy?' · 全代理':''}</div>
      <div class="tags">${tags||'<span class="empty">无模型</span>'}</div>
    </div>`;
  }).join('');
}
async function loadLogs(){
  const d = await api('/admin/api/logs?lines=150');
  const el = document.getElementById('log');
  el.textContent = (d.lines||[]).join('');
  el.scrollTop = el.scrollHeight;
}
function renderUsageTable(items, type){
  if(!items || !items.length) return '<div class="empty">暂无用量记录</div>';
  const isModel = type === 'model';
  const columns = isModel
    ? '<th>模型</th><th>Provider</th>'
    : '<th>Provider</th>';
  const rows = items.map(x=>{
    const identity = isModel
      ? '<td class="mono">'+esc(x.model||'')+'</td><td>'+esc(x.provider||'')+'</td>'
      : '<td>'+esc(x.name||'')+'</td>';
    return '<tr>'+identity+
      '<td class="num">'+fmtNum(x.requests)+'</td>'+ 
      '<td class="num">'+fmtNum(x.input_tokens)+'</td>'+ 
      '<td class="num">'+fmtNum(x.output_tokens)+'</td>'+ 
      '<td class="num">'+fmtNum(x.total_tokens)+'</td>'+ 
      '<td class="num">'+fmtNum(x.cached_tokens)+'</td>'+ 
      '<td class="num">'+fmtNum(x.reasoning_tokens)+'</td>'+ 
      '<td class="num">$'+(Number(x.cost||0).toFixed(6))+'</td>'+ 
      '<td class="num">$'+(Number(x.list_cost||0).toFixed(6))+'</td>'+ 
      '<td class="num">'+fmtNum(x.errors)+'</td>'+  
      '<td class="num">'+fmtNum(x.usage_missing)+'</td>'+ 
      '<td class="num">'+(x.requests?Math.round(Number(x.latency_ms||0)/Number(x.requests||1))+' ms':'—')+'</td>'+ 
      '<td>'+esc(x.last_used||'—')+'</td></tr>';
  }).join('');
  return '<div class="table-wrap"><table><thead><tr>'+columns+
    '<th>请求</th><th>输入 Token</th><th>输出 Token</th><th>总 Token</th>'+ 
    '<th>缓存读</th><th>推理 Token</th><th>实扣（$）</th><th>标价估算（$）</th><th>错误</th><th>无用量</th><th>平均延迟</th><th>最近调用</th>'+ 
    '</tr></thead><tbody>'+rows+'</tbody></table></div>';
}
async function loadUsage(){
  try{
    const d = await api('/admin/api/usage');
    const t = d.totals || {};
    document.getElementById('usageSince').textContent = d.since ? ('统计起始 '+d.since+' · 更新 '+(d.updated||'—')) : '';
    document.getElementById('usageTotals').innerHTML = `
      <div class="stat"><div class="label">请求数</div><div class="value">${fmtNum(t.requests)}</div></div>
      <div class="stat"><div class="label">输入 Token</div><div class="value">${fmtNum(t.input_tokens)}</div></div>
      <div class="stat"><div class="label">输出 Token</div><div class="value">${fmtNum(t.output_tokens)}</div></div>
      <div class="stat"><div class="label">总 Token</div><div class="value">${fmtNum(t.total_tokens)}</div></div>
      <div class="stat"><div class="label">缓存读 Token</div><div class="value">${fmtNum(t.cached_tokens)}</div></div>
      <div class="stat"><div class="label">推理 Token</div><div class="value">${fmtNum(t.reasoning_tokens)}</div></div>
      <div class="stat"><div class="label">上游实扣</div><div class="value">$${Number(t.cost||0).toFixed(6)}</div></div>
      <div class="stat"><div class="label">按模型价估算</div><div class="value">$${Number(t.list_cost||0).toFixed(6)}</div></div>
      <div class="stat"><div class="label">用量缺失请求</div><div class="value">${fmtNum(t.usage_missing)}</div></div>`;
    document.getElementById('usageProviders').innerHTML = renderUsageTable(d.providers||[], 'provider');
    document.getElementById('usageModels').innerHTML = renderUsageTable(d.models||[], 'model');
  }catch(e){
    document.getElementById('usageTotals').innerHTML = '<div class="empty">读取用量失败：'+esc(e.message||e)+'</div>';
  }
}
async function resetUsage(){
  if(!confirm('确定重置所有 Token 用量统计？此操作不可撤销。')) return;
  const r = await api('/admin/api/usage/reset',{method:'POST'});
  if(!r.ok){ toast('重置失败'); return; }
  await loadUsage(); toast('用量统计已重置');
}
async function reload(){
  await api('/admin/api/reload',{method:'POST'});
  await load();
  toast('配置已重载');
}
function openAdd(){ editingIndex=-1; document.getElementById('modalTitle').textContent='添加 Provider';
  ['fName','fUrl','fAnthropicUrl','fKey','fAnthropicKey','fModels','fProxyModels','fHeaders','fModelMap','fMeta'].forEach(id=>document.getElementById(id).value='');
  document.getElementById('fNeedsSession').checked=false; document.getElementById('fUseProxy').checked=false;
  document.getElementById('modalMask').classList.add('show');
}
function openEdit(i){ const p=CFG.providers[i]; editingIndex=i; document.getElementById('modalTitle').textContent='编辑 Provider';
  document.getElementById('fName').value=p.name||'';
  document.getElementById('fUrl').value=p.base_url||'';
  document.getElementById('fAnthropicUrl').value=p.anthropic_base_url||'';
  document.getElementById('fKey').value=p.api_key||'';
  document.getElementById('fAnthropicKey').value=p.anthropic_api_key||'';
  document.getElementById('fModels').value=(p.models||[]).join('\n');
  document.getElementById('fProxyModels').value=(p.proxy_models||[]).join('\n');
  document.getElementById('fHeaders').value=p.headers?JSON.stringify(p.headers,null,2):'';
  document.getElementById('fNeedsSession').checked=!!p.needs_session;
  document.getElementById('fUseProxy').checked=!!p.use_proxy;
  document.getElementById('fModelMap').value=p.model_map?JSON.stringify(p.model_map,null,2):'';
  document.getElementById('fMeta').value=p.model_meta?JSON.stringify(p.model_meta,null,2):'';
  document.getElementById('modalMask').classList.add('show');
}
function closeModal(){ document.getElementById('modalMask').classList.remove('show'); }
function parseLines(s){ return (s||'').split('\n').map(x=>x.trim()).filter(Boolean); }
function parseJSON(s){ s=(s||'').trim(); if(!s) return undefined; return JSON.parse(s); }
async function saveProvider(){
  let headers, modelMap, meta;
  try{
    headers = parseJSON(document.getElementById('fHeaders').value);
    modelMap = parseJSON(document.getElementById('fModelMap').value);
    meta = parseJSON(document.getElementById('fMeta').value);
  }catch(e){ toast('JSON 格式错误：'+e.message); return; }

  // 保留旧对象里的未知字段（尤其是 model_meta），只覆盖表单里可编辑的项
  const p = Object.assign({}, editingIndex>=0 ? CFG.providers[editingIndex] : {});
  p.name = document.getElementById('fName').value.trim();
  p.base_url = document.getElementById('fUrl').value.trim();
  const anthropicUrl = document.getElementById('fAnthropicUrl').value.trim();
  if(anthropicUrl) p.anthropic_base_url = anthropicUrl; else delete p.anthropic_base_url;
  p.api_key = document.getElementById('fKey').value.trim();
  const anthropicKey = document.getElementById('fAnthropicKey').value.trim();
  if(anthropicKey) p.anthropic_api_key = anthropicKey; else delete p.anthropic_api_key;
  p.models = parseLines(document.getElementById('fModels').value);
  p.proxy_models = parseLines(document.getElementById('fProxyModels').value);
  p.needs_session = document.getElementById('fNeedsSession').checked;
  p.use_proxy = document.getElementById('fUseProxy').checked;
  if(headers) p.headers = headers; else delete p.headers;
  if(modelMap) p.model_map = modelMap; else delete p.model_map;
  if(meta) p.model_meta = meta; else delete p.model_meta;

  if(!p.name || !p.base_url){ toast('名称和 Base URL 必填'); return; }
  if(!p.models.length){ toast('至少填一个模型'); return; }
  if(editingIndex>=0) CFG.providers[editingIndex]=p; else CFG.providers.push(p);
  const r = await api('/admin/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(CFG)});
  if(!r.ok){ toast('保存失败：'+(r.error||'未知错误')); await load(); return; }
  closeModal(); await load();
  toast('已保存');
}
async function delProvider(i){
  if(CFG.providers.length<=1){ toast('至少保留一个 Provider'); return; }
  if(!confirm('确定删除该 Provider？')) return;
  CFG.providers.splice(i,1);
  const r = await api('/admin/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(CFG)});
  if(!r.ok){ toast('删除失败：'+(r.error||'未知错误')); await load(); return; }
  await load(); toast('已删除');
}
async function testProvider(i){
  const p=CFG.providers[i];
  const model=(p.models||[])[0];
  toast('测试 '+model+' …');
  const r=await api('/admin/api/test?model='+encodeURIComponent(model));
  alert(r.ok?('✅ '+model+' 通了\n\n'+(r.reply||'').slice(0,500)):('❌ '+model+' 失败\n\n'+(r.error||'').slice(0,500)));
}
async function testModel(el){
  const model = el.dataset.model;
  if(el.classList.contains('testing')) return;
  const orig = el.innerHTML;
  el.classList.remove('ok','fail'); el.classList.add('testing');
  el.textContent = model + ' ⏳';
  try{
    const r = await api('/admin/api/test?model='+encodeURIComponent(model));
    if(r.ok){
      el.classList.add('ok');
      el.title = '✅ '+model+' ['+r.route+'] '+(r.reply||'').slice(0,200);
    }else{
      el.classList.add('fail');
      el.title = '❌ '+model+' '+((r.error||'')+(r.status?(' HTTP '+r.status):'')).slice(0,300);
    }
  }catch(e){
    el.classList.add('fail'); el.title = '❌ '+model+' '+e;
  }
  el.classList.remove('testing');
  el.innerHTML = orig;
}
async function testAll(){
  const tags = Array.from(document.querySelectorAll('.provider .tag[data-model]'));
  if(!tags.length){ toast('没有可测试的模型'); return; }
  toast('开始测试 '+tags.length+' 个模型…');
  const CONCURRENCY = 5;
  for(let i=0;i<tags.length;i+=CONCURRENCY){
    await Promise.all(tags.slice(i,i+CONCURRENCY).map(t=>testModel(t)));
  }
  const ok = document.querySelectorAll('.tag.ok').length;
  const fail = document.querySelectorAll('.tag.fail').length;
  toast('测试完成：✅ '+ok+' 通，❌ '+fail+' 失败');
}
document.getElementById('modalMask').addEventListener('click',e=>{ if(e.target.id==='modalMask') closeModal(); });
load();
</script>
</body>
</html>
"""


def find_provider(model_id):
    """按模型 id 找 provider（精确匹配优先，再前缀匹配）。"""
    for p in PROVIDERS:
        if model_id in p.get("models", []):
            return p
    for p in PROVIDERS:
        for m in p.get("models", []):
            if model_id.startswith(m) or m.startswith(model_id):
                return p
    return None


def should_proxy(provider, model_id):
    """判断该模型是否走代理。"""
    if model_id in provider.get("proxy_models", []):
        return True
    return bool(provider.get("use_proxy", False))


# 标准 OpenAI message 字段（其余丢弃）
STD_MSG_KEYS = {"role", "content", "name", "tool_calls", "tool_call_id",
                "function_call", "audio", "refusal"}
# 标准 Responses input item 字段
STD_INPUT_KEYS = {"type", "role", "content", "name", "call_id", "arguments",
                  "output", "status", "id", "summary", "encrypted_content",
                  "annotations", "text", "logprobs", "detail", "refusal",
                  "usage", "error", "index", "delta", "finish_reason",
                  "input_tokens", "output_tokens", "total_tokens",
                  "input_tokens_details", "output_tokens_details",
                  "cached_tokens", "reasoning_tokens", "message"}
STD_TOP_KEYS = {
    # Chat Completions 标准字段
    "model", "messages", "tools", "tool_choice", "max_tokens",
    "max_completion_tokens", "stream", "stream_options", "temperature",
    "top_p", "n", "stop", "presence_penalty", "frequency_penalty",
    "logit_bias", "logprobs", "top_logprobs", "user", "response_format",
    "seed", "parallel_tool_calls", "metadata", "store", "reasoning_effort",
    "verbosity", "service_tier", "prediction", "modalities", "audio",
    "web_search_options", "functions", "function_call",
    # Responses 标准字段
    "instructions", "input", "include", "max_output_tokens", "text",
    "truncation", "previous_response_id", "background", "conversation",
    "max_tool_calls", "prompt_cache_key", "safety_identifier",
    "reasoning", "effort",
}


# 标准 Anthropic Messages 顶层字段（其余丢弃）
ANTHROPIC_TOP_KEYS = {
    "model", "messages", "system", "max_tokens", "metadata", "stop_sequences",
    "stream", "temperature", "top_k", "top_p", "tools", "tool_choice",
    "thinking", "service_tier", "container", "mcp_servers", "context_management",
}


def anthropic_base(provider):
    """provider 的 Anthropic Messages base URL。

    优先用显式配置 anthropic_base_url；仅对 opencode.ai 的 OpenAI base
    做路径推导（/openai/ -> /anthropic/），避免把不支持的 provider 猜错。
    """
    base = str(provider.get("anthropic_base_url") or "").strip()
    if base:
        return base.rstrip("/")
    openai_base = str(provider.get("base_url") or "").rstrip("/")
    if "opencode.ai" in openai_base and "/openai/" in openai_base:
        return openai_base.replace("/openai/", "/anthropic/", 1)
    return ""


def is_anthropic_path(path):
    """判断是否是 Anthropic Messages 端点（/v1/messages、/messages、count_tokens）。"""
    p = (path or "").split("?")[0].rstrip("/")
    return p.endswith("/messages") or p.endswith("/messages/count_tokens")


def sanitize_anthropic(body, provider):
    """清洗 Anthropic Messages 请求：模型映射 + 只保留标准顶层/消息字段。"""
    if not body:
        return body
    try:
        j = json.loads(body)
    except Exception:
        return body
    if not isinstance(j, dict):
        return body

    mmap = provider.get("model_map", {}) if provider else {}
    mid = j.get("model")
    if isinstance(mid, str) and mid in mmap:
        j["model"] = mmap[mid]

    msgs = j.get("messages")
    if isinstance(msgs, list):
        for m in msgs:
            if isinstance(m, dict):
                for k in list(m.keys()):
                    if k not in ("role", "content"):
                        del m[k]

    for k in list(j.keys()):
        if k not in ANTHROPIC_TOP_KEYS:
            del j[k]

    clamp_output_tokens(j, provider)
    return json.dumps(j, ensure_ascii=False).encode("utf-8")


def clamp_output_tokens(j, provider):
    """把客户端传入的过小输出上限抬到 provider 允许的最小值。

    OpenCode Go 对 max_output_tokens 要求 >=16；有些客户端默认填 1，
    不处理会在上游直接 400。
    """
    min_out = provider.get("min_output_tokens")
    if not isinstance(min_out, int) or min_out <= 0:
        return
    for key in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
        value = j.get(key)
        if isinstance(value, int) and value < min_out:
            j[key] = min_out
            log(f"clamp {key} {value} -> {min_out}")


def sanitize_body(body, provider):
    """剥离 WorkBuddy 私有字段；如 provider 声明 model_map 则映射模型 id。"""
    if not body:
        return body
    try:
        j = json.loads(body)
    except Exception:
        return body
    if not isinstance(j, dict):
        return body

    # 模型 id 映射（WorkBuddy 自定义 id -> 真实 id）
    mmap = provider.get("model_map", {}) if provider else {}
    mid = j.get("model")
    if isinstance(mid, str) and mid in mmap:
        j["model"] = mmap[mid]

    msgs = j.get("messages")
    if isinstance(msgs, list):
        for m in msgs:
            if isinstance(m, dict):
                for k in list(m.keys()):
                    if k not in STD_MSG_KEYS:
                        del m[k]

    inp = j.get("input")
    if isinstance(inp, list):
        for m in inp:
            if isinstance(m, dict):
                for k in list(m.keys()):
                    if k not in STD_INPUT_KEYS:
                        del m[k]

    for k in list(j.keys()):
        if k not in STD_TOP_KEYS:
            del j[k]

    clamp_output_tokens(j, provider)
    return json.dumps(j, ensure_ascii=False).encode("utf-8")


def set_header(headers, name, value):
    """大小写不敏感地设置请求头（HTTP 头名不区分大小写）。"""
    for k in list(headers.keys()):
        if k.lower() == name.lower():
            del headers[k]
    headers[name] = value


def auth_value(api_key):
    """把配置里的 api_key 规范成 Authorization 头的值。"""
    key = str(api_key or "").strip()
    if not key:
        return ""
    if key.lower().startswith(("bearer ", "basic ")):
        return key
    return "Bearer " + key


def resolve_secret(value):
    """支持把 ${ENV_NAME} 格式的配置值从环境变量读取。"""
    value = str(value or "").strip()
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", value)
    if match:
        return os.environ.get(match.group(1), "").strip()
    return value


def provider_api_key(provider, anthropic=False):
    """取该协议使用的 key；支持 ${ENV_NAME} 环境变量引用。

    Anthropic 优先用 provider.anthropic_api_key；留空则回落到 api_key。
    OpenAI 一直用 provider.api_key。
    """
    if anthropic:
        key = resolve_secret(provider.get("anthropic_api_key"))
        if key:
            return key
    return resolve_secret(provider.get("api_key"))


def client_auth_keys():
    """网关对外（客户端 -> 网关）允许的 key 列表；未启用鉴权时返回空列表。"""
    auth = CFG.get("client_auth") or {}
    if not auth.get("enabled"):
        return []
    values = []
    for item in (auth.get("keys") or []):
        value = resolve_secret(item)
        if value:
            values.extend([x.strip() for x in value.split(",") if x.strip()])
    return values


def client_key_candidates(headers):
    """从请求头提取客户端带来的网关 key（OpenAI 用 Bearer，Anthropic 用 x-api-key）。"""
    values = []
    auth = headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        values.append(auth[7:].strip())
    for name in ("x-api-key", "api-key"):
        value = headers.get(name)
        if value:
            values.append(value.strip())
    return [v for v in values if v]


def client_auth_ok(headers):
    """校验客户端是否持有网关访问 key；未启用鉴权时直接放行。"""
    auth = CFG.get("client_auth") or {}
    if not auth.get("enabled"):
        return True
    keys = client_auth_keys()
    if not keys:
        # enabled=true 但没配 key：拒绝所有请求，避免误开成公开网关
        return False
    candidates = client_key_candidates(headers)
    return any(hmac.compare_digest(c, k) for c in candidates for k in keys)


def model_entry(model_id, provider, meta=None, root=None):
    """构造 OpenAI /v1/models 条目，附带网关已知的能力元数据。"""
    entry = {
        "id": model_id,
        "object": "model",
        "created": 0,
        "owned_by": provider.get("name", "gateway"),
    }
    if root:
        entry["root"] = root
    if isinstance(meta, dict):
        ctx = meta.get("context") or meta.get("input")
        if ctx:
            entry["context_length"] = ctx
        out = meta.get("output")
        if out:
            entry["max_output_tokens"] = out
        entry["capabilities"] = {
            "vision": bool(meta.get("images")),
            "tools": bool(meta.get("tools")),
            "reasoning": meta.get("reasoning") or [],
        }
    return entry


def build_model_list():
    """聚合所有 provider 的模型，并把 model_map 里的别名也暴露出来。"""
    data = []
    seen = set()
    for p in PROVIDERS:
        metas = p.get("model_meta") or {}
        for m in p.get("models", []) or []:
            if m in seen:
                continue
            seen.add(m)
            data.append(model_entry(m, p, metas.get(m)))
        for alias, real in (p.get("model_map") or {}).items():
            if alias in seen:
                continue
            seen.add(alias)
            data.append(model_entry(alias, p, metas.get(real), root=real))
    return data


def build_anthropic_model_list():
    """Anthropic Models API 格式的模型列表（带 anthropic-version 头时返回）。"""
    data = []
    seen = set()
    for p in PROVIDERS:
        for m in (p.get("models", []) or []) + list((p.get("model_map") or {}).keys()):
            if m in seen:
                continue
            seen.add(m)
            data.append({
                "type": "model",
                "id": m,
                "display_name": m,
                "created_at": "2026-11-07T00:00:00Z",
            })
    return {
        "data": data,
        "has_more": False,
        "first_id": data[0]["id"] if data else None,
        "last_id": data[-1]["id"] if data else None,
    }


class UsageCollector:
    """从上游响应里提取 token 用量，兼容 OpenAI Chat/Responses 和 Anthropic SSE。"""

    MAX_RAW = 4 * 1024 * 1024

    def __init__(self, anthropic=False, content_type=""):
        self.anthropic = bool(anthropic)
        self.sse = "text/event-stream" in (content_type or "").lower()
        self.buf = ""
        self.raw = bytearray()
        self.usage = {}

    def feed(self, chunk):
        if not chunk:
            return
        if not self.sse and chunk.lstrip().startswith(b"data:"):
            self.sse = True
        if self.sse:
            self.buf += chunk.decode("utf-8", errors="ignore")
            while "\n" in self.buf:
                line, self.buf = self.buf.split("\n", 1)
                self._line(line.strip())
        elif len(self.raw) < self.MAX_RAW:
            self.raw.extend(chunk[: self.MAX_RAW - len(self.raw)])

    def _line(self, line):
        if not line.startswith("data:"):
            return
        data = line[5:].strip()
        if not data or data == "[DONE]":
            return
        try:
            obj = json.loads(data)
        except Exception:
            return
        self._extract(obj)

    def _extract(self, obj):
        if not isinstance(obj, dict):
            return
        usage = None
        if obj.get("type") == "message_start":
            usage = (obj.get("message") or {}).get("usage")
        elif obj.get("type") == "response.completed":
            usage = (obj.get("response") or {}).get("usage")
        if not isinstance(usage, dict):
            candidate = obj.get("usage")
            if isinstance(candidate, dict):
                usage = candidate
        if isinstance(usage, dict):
            self._merge(usage)

    def _merge(self, u):
        try:
            raw_in = int(u.get("input_tokens") or u.get("prompt_tokens") or 0)
            out = int(u.get("output_tokens") or u.get("completion_tokens") or 0)
            cached = int(u.get("cache_read_input_tokens") or 0)
            cache_write = int(u.get("cache_creation_input_tokens") or 0)

            details = u.get("prompt_tokens_details") or {}
            if isinstance(details, dict):
                cached = max(cached, int(details.get("cached_tokens") or 0))
            cdetails = u.get("completion_tokens_details") or {}
            reasoning = int(cdetails.get("reasoning_tokens") or 0) if isinstance(cdetails, dict) else 0

            op = u.get("opencode") or {}
            tok = op.get("tokens") or {}
            if isinstance(tok, dict):
                if not raw_in:
                    raw_in = int(tok.get("input") or 0)
                if not out:
                    out = int(tok.get("output") or 0)
                if not reasoning:
                    reasoning = int(tok.get("reasoning") or 0)
                if not cached:
                    cached = int(tok.get("cache_read") or 0)
                if not cache_write:
                    cache_write = (int(tok.get("cache_write_5m") or 0)
                                   + int(tok.get("cache_write_1h") or 0))

            total = int(u.get("total_tokens") or 0)
            # Anthropic 的 input_tokens 不含缓存读写，补进输入总量
            inp = raw_in + (cached + cache_write if self.anthropic else 0)
            if not total:
                total = inp + out

            direct_cost = float(u.get("cost") or 0)
            cost = float(op.get("cost") or direct_cost) if isinstance(op, dict) else direct_cost
            list_cost = float(op.get("list_cost") or direct_cost) if isinstance(op, dict) else direct_cost
            values = {
                "input_tokens": inp,
                "output_tokens": out,
                "total_tokens": total,
                "cached_tokens": cached,
                "cache_write_tokens": cache_write,
                "reasoning_tokens": reasoning,
                "cost": cost,
                "list_cost": list_cost,
            }
            for key, value in values.items():
                if value > self.usage.get(key, 0):
                    self.usage[key] = value
        except Exception:
            pass

    def finalize(self):
        if not self.sse and self.raw:
            try:
                obj = json.loads(bytes(self.raw).decode("utf-8", errors="ignore"))
                self._extract(obj)
            except Exception:
                pass
        if self.usage:
            # 流式 Anthropic 的 input/output 在不同事件里，按最终值重算总数
            self.usage["total_tokens"] = max(
                int(self.usage.get("total_tokens", 0)),
                int(self.usage.get("input_tokens", 0)) + int(self.usage.get("output_tokens", 0)),
            )
        return dict(self.usage)


class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _handle(self):
        t0 = time.time()
        path = self.path or ""
        anthropic = is_anthropic_path(path)

        # 客户端 -> 网关的入站鉴权（上游 provider key 由网关另外注入）
        if not client_auth_ok(self.headers):
            log(f"401 unauthorized {path}")
            self._unauthorized(anthropic)
            return

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        # 抓包（调试用）
        if body:
            try:
                with open(os.path.join(BASE_DIR, "gateway_last_request.json"), "w", encoding="utf-8") as f:
                    f.write(body.decode("utf-8", errors="replace"))
            except Exception:
                pass

        # 解析模型与协议格式
        model_id = None
        is_responses = False
        if body:
            try:
                j = json.loads(body)
                if isinstance(j, dict):
                    model_id = j.get("model")
                    if (not anthropic and ("input" in j or "instructions" in j)
                            and "messages" not in j):
                        is_responses = True
            except Exception:
                pass

        provider = find_provider(model_id) if model_id else None
        if provider is None:
            # 未知模型：回落到第一个 provider（便于 WorkBuddy 新增模型后立即可用）
            provider = PROVIDERS[0] if PROVIDERS else None
            if provider and model_id:
                log(f"WARN unknown model {model_id!r}, fallback to {provider['name']}")
        if provider is None:
            self._error(400, "no provider configured")
            return

        # 模型映射后的真实 id：路由/代理判断/上游请求都应以它为准
        model_map = provider.get("model_map") or {}
        effective_model = model_map.get(model_id, model_id) if isinstance(model_id, str) else model_id

        # 清洗 + 模型映射
        if anthropic:
            body = sanitize_anthropic(body, provider)
        else:
            body = sanitize_body(body, provider)

        # 目标端点：Anthropic Messages vs OpenAI Chat/Responses
        if anthropic:
            base = anthropic_base(provider)
            if not base:
                self._error(400, f"provider {provider['name']} does not support Anthropic protocol "
                                 f"(set anthropic_base_url)")
                return
            if path.split("?")[0].rstrip("/").endswith("/count_tokens"):
                upstream_path = "/messages/count_tokens"
            else:
                upstream_path = "/messages"
            url = base + upstream_path
        else:
            if path.endswith("/responses") or is_responses:
                upstream_path = "/responses"
            else:
                upstream_path = "/chat/completions"
            url = provider["base_url"].rstrip("/") + upstream_path

        # 组头：透传 + 注入 provider 自定义头
        fwd = {}
        for k, v in self.headers.items():
            lk = k.lower()
            if lk in ("host", "content-length", "connection", "accept-encoding"):
                continue
            fwd[k] = v

        # Anthropic 客户端通常带 x-api-key：默认不把客户端的占位 key 传给上游
        if anthropic and not provider.get("pass_client_auth"):
            for k in list(fwd.keys()):
                if k.lower() in ("authorization", "x-api-key"):
                    del fwd[k]

        # provider 自定义头（如 x-opencode-session），支持 {session} 占位符
        sess = (self.headers.get("x-conversation-id")
                or self.headers.get("x-opencode-session")
                or f"gw-{uuid.uuid4()}")
        provider_headers = provider.get("headers") or {}
        for k, v in provider_headers.items():
            set_header(fwd, k, str(v).replace("{session}", sess))

        # 认证：默认使用配置里的 api_key，不再透传客户端的 key
        # - provider.headers 里显式配了 Authorization/api-key 时尊重自定义头
        # - pass_client_auth=true 时保留客户端自带的鉴权
        custom_auth = any(k.lower() in ("authorization", "api-key", "x-api-key")
                          for k in provider_headers)
        if not custom_auth and not provider.get("pass_client_auth"):
            api_key = provider_api_key(provider, anthropic=anthropic)
            auth = auth_value(api_key)
            if auth:
                set_header(fwd, "Authorization", auth)
            # Anthropic 原生入口同时补 x-api-key（OpenCode Go 两种都认）
            if anthropic and api_key:
                set_header(fwd, "x-api-key", api_key)

        # Anthropic 协议版本：客户端没带则补默认值
        if anthropic and not any(k.lower() == "anthropic-version" for k in fwd):
            set_header(fwd, "anthropic-version", "2023-06-01")

        # 若 provider 需要 session 头但没配（opencode），补一个
        if provider.get("needs_session"):
            if not any(k.lower() == "x-opencode-session" for k in fwd):
                set_header(fwd, "x-opencode-session", sess)

        # 代理决策（用映射后的真实模型 id）
        use_px = bool(should_proxy(provider, effective_model) and DEFAULT_PROXY)
        proxies = {"http": DEFAULT_PROXY, "https": DEFAULT_PROXY} if use_px else None
        route = "PROXY" if use_px else "DIRECT"
        proto = "anthropic" if anthropic else ("responses" if upstream_path == "/responses" else "openai")
        mapped = f" -> {effective_model}" if effective_model and effective_model != model_id else ""
        log(f"-> {provider['name']}{upstream_path} proto={proto} model={model_id}{mapped} "
            f"route={route} clen={length}")

        try:
            resp = requests.post(url, data=body, headers=fwd, stream=True,
                                 timeout=900, proxies=proxies)
        except Exception as e:
            log(f"ERR upstream: {e}")
            record_usage(provider["name"], model_id or effective_model or "unknown",
                         {}, 502, int((time.time() - t0) * 1000), effective_model)
            self._error(502, f"gateway upstream error: {e}")
            return

        self.send_response(resp.status_code)
        hop = {"content-length", "connection", "transfer-encoding", "content-encoding", "keep-alive"}
        for k, v in resp.headers.items():
            if k.lower() in hop:
                continue
            self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        collector = UsageCollector(anthropic=anthropic,
                                   content_type=resp.headers.get("Content-Type", ""))
        first = True
        try:
            for chunk in resp.iter_content(chunk_size=4096):
                if not chunk:
                    continue
                collector.feed(chunk)
                if first:
                    first = False
                    if resp.status_code >= 400:
                        snippet = chunk[:300].decode("utf-8", errors="replace").replace("\n", " ")
                        log(f"ERR upstream {resp.status_code}: {snippet}")
                self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except OSError as e:
            # WinError 10053/10054：客户端主动断开，不是网关错误，避免日志刷屏
            if getattr(e, "winerror", None) in (10053, 10054):
                log("client disconnected during stream")
            else:
                log(f"ERR stream: {e}")
        except Exception as e:
            log(f"ERR stream: {e}")
        finally:
            try:
                resp.close()
            except Exception:
                pass

        cost = int((time.time() - t0) * 1000)
        usage = collector.finalize()
        if not upstream_path.endswith("/count_tokens"):
            record_usage(provider["name"], model_id or effective_model or "unknown",
                         usage, resp.status_code, cost, effective_model)
        log(f"<- {resp.status_code} {cost}ms usage={usage or {}}")

    def _unauthorized(self, anthropic=False):
        """返回对应协议的 401 错误格式。"""
        if anthropic:
            payload = {"type": "error",
                       "error": {"type": "authentication_error",
                                 "message": "invalid x-api-key"}}
        else:
            payload = {"error": {"message": "Invalid API key",
                                 "type": "invalid_request_error",
                                 "code": "invalid_api_key"}}
        self._json(401, payload)

    def _json(self, code, obj):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _admin_get(self):
        path = self.path.split("?")[0]
        if path in ("/admin", "/admin/"):
            payload = admin_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif path == "/admin/api/config":
            self._json(200, CFG)
        elif path == "/admin/api/status":
            total = sum(len(p.get("models", [])) for p in PROVIDERS)
            proxy = sum(len(p.get("proxy_models", [])) for p in PROVIDERS)
            self._json(200, {"ok": True, "version": GW_VERSION,
                             "providers": len(PROVIDERS), "models": total,
                             "proxy_models": proxy, "proxy": DEFAULT_PROXY,
                             "listen": f"{LISTEN_HOST}:{LISTEN_PORT}",
                             "uptime": time.time() - START_TIME})
        elif path == "/admin/api/usage":
            self._json(200, usage_snapshot())
        elif path == "/admin/api/usage/reprice":
            reprice_usage()
            self._json(200, usage_snapshot())
        elif path == "/admin/api/logs":
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            lines = int(q.get("lines", ["150"])[0])
            self._json(200, {"lines": read_logs(lines)})
        elif path == "/admin/api/test":
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            model = q.get("model", [""])[0]
            self._test_model(model)
        else:
            self._json(404, {"error": "not found"})

    def _test_model(self, model):
        """测试某个模型是否可用（实际调用一次，并将消耗计入统计）。"""
        if not model:
            self._json(400, {"ok": False, "error": "model required"})
            return
        provider = find_provider(model) or (PROVIDERS[0] if PROVIDERS else None)
        if not provider:
            self._json(400, {"ok": False, "error": "no provider"})
            return

        # 与主转发路径保持一致：先做 model_map 映射，再用真实 id 判断代理
        model_map = provider.get("model_map") or {}
        upstream_model = model_map.get(model, model)
        url = provider["base_url"].rstrip("/") + "/chat/completions"
        body = json.dumps({"model": upstream_model,
                           "messages": [{"role": "user", "content": "ping"}],
                           "max_tokens": 64}).encode()

        headers = {"Content-Type": "application/json"}
        sess = f"test-{uuid.uuid4()}"
        for k, v in (provider.get("headers") or {}).items():
            set_header(headers, k, str(v).replace("{session}", sess))
        custom_auth = any(k.lower() in ("authorization", "api-key", "x-api-key")
                          for k in (provider.get("headers") or {}))
        if not custom_auth:
            auth = auth_value(provider.get("api_key"))
            if auth:
                set_header(headers, "Authorization", auth)
        if provider.get("needs_session"):
            set_header(headers, "x-opencode-session", sess)

        use_px = bool(should_proxy(provider, upstream_model) and DEFAULT_PROXY)
        proxies = {"http": DEFAULT_PROXY, "https": DEFAULT_PROXY} if use_px else None
        route = "PROXY" if use_px else "DIRECT"
        try:
            test_started = time.time()
            r = requests.post(url, data=body, headers=headers, timeout=90, proxies=proxies)
            test_collector = UsageCollector(content_type=r.headers.get("Content-Type", ""))
            test_collector.feed(r.content)
            test_usage = test_collector.finalize()
            record_usage(provider["name"], model, test_usage, r.status_code,
                         int((time.time() - test_started) * 1000), upstream_model)
            if r.status_code == 200:
                reply = ""
                try:
                    j = r.json()
                    choice = (j.get("choices") or [{}])[0]
                    msg = choice.get("message") or {}
                    reply = msg.get("content") or msg.get("reasoning_content") or choice.get("text") or ""
                except Exception:
                    pass
                result = {"ok": True, "model": model, "provider": provider["name"],
                          "route": route, "reply": reply}
                if upstream_model != model:
                    result["upstream_model"] = upstream_model
                log(f"test {model} [{provider['name']}/{route}] OK")
                self._json(200, result)
            else:
                log(f"test {model} [{provider['name']}/{route}] HTTP {r.status_code}: {r.text[:200]}")
                self._json(200, {"ok": False, "model": model, "provider": provider["name"],
                                 "route": route, "status": r.status_code,
                                 "error": r.text[:300]})
        except Exception as e:
            log(f"test {model} [{provider['name']}/{route}] ERR: {e}")
            self._json(200, {"ok": False, "model": model, "provider": provider["name"],
                             "route": route, "error": str(e)})

    def _admin_post(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if path == "/admin/api/config":
            try:
                new_cfg = json.loads(raw)
                err = validate_config(new_cfg)
                if err:
                    self._json(400, {"ok": False, "error": err})
                    return
                save_config(new_cfg)
                log("config updated via admin UI")
                self._json(200, {"ok": True})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
        elif path == "/admin/api/usage/reprice":
            reprice_usage()
            self._json(200, usage_snapshot())
        elif path == "/admin/api/usage/reset":
            reset_usage()
            log("usage stats reset")
            self._json(200, {"ok": True})
        elif path == "/admin/api/reload":
            reload_config()
            log("config reloaded")
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def _error(self, code, msg):
        payload = json.dumps({"error": {"message": msg}}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        """GET 路由：/admin 管理端、/v1/models 模型列表、/ 健康检查。"""
        if self.path.startswith("/admin"):
            self._admin_get()
            return
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path in ("/v1/models", "/models", "/v1/models-info") and not client_auth_ok(self.headers):
            self._unauthorized(bool(self.headers.get("anthropic-version")))
            return
        if path in ("/v1/models", "/models"):
            if self.headers.get("anthropic-version"):
                payload = json.dumps(build_anthropic_model_list(),
                                     ensure_ascii=False).encode("utf-8")
            else:
                payload = json.dumps({"object": "list", "data": build_model_list()},
                                     ensure_ascii=False).encode("utf-8")
        elif path == "/v1/models-info":
            # 调试端点：带完整 model_meta，便于排查
            payload = json.dumps({"models": build_model_list()},
                                 ensure_ascii=False).encode("utf-8")
        else:
            payload = json.dumps({"ok": True, "service": "wb-aggregator-gateway",
                                  "version": GW_VERSION,
                                  "providers": [p["name"] for p in PROVIDERS],
                                  "proxy": DEFAULT_PROXY},
                                 ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_HEAD(self):
        """HEAD：返回头，无 body（健康检查用）。"""
        if self.path.startswith("/admin"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Content-Length", str(len(admin_html().encode("utf-8"))))
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_POST(self):
        if self.path.startswith("/admin"):
            self._admin_post()
        else:
            self._handle()



def main():
    srv = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), GatewayHandler)
    log(f"gateway listening on http://{LISTEN_HOST}:{LISTEN_PORT} providers={[p['name'] for p in PROVIDERS]} proxy={DEFAULT_PROXY}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("gateway stopped")


if __name__ == "__main__":
    main()
