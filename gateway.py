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
import hashlib
import hmac
import ipaddress
import json
import os
import re
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import requests

GW_VERSION = "1.5.1"
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


def config_revision(cfg):
    raw = json.dumps(cfg, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


load_dotenv()
CFG = load_config()
CFG_REVISION = config_revision(CFG)
START_TIME = time.time()
LISTEN_HOST = CFG.get("listen", {}).get("host", "127.0.0.1")
LISTEN_PORT = int(CFG.get("listen", {}).get("port", 8787))
PROVIDERS = CFG.get("providers", [])
DEFAULT_PROXY = CFG.get("proxy", "")
IS_WILDCARD = LISTEN_HOST in ("0.0.0.0", "::", "")


# 虚拟网卡名片段：TUN / Hyper-V / 容器 / VPN 等，不该当作局域网地址对外展示
VIRTUAL_IF_MARKERS = (
    "meta", "clash", "cfw-tap", "tap", "tun", "vethernet", "loopback",
    "virtualbox", "vmware", "hyper-v", "wsl", "docker", "teredo",
    "isatap", "bluetooth", "zerotier", "tailscale",
)


def _virtual_if(name):
    low = str(name or "").lower()
    return any(m in low for m in VIRTUAL_IF_MARKERS)


def _is_lan_ipv4(ip):
    """是否是可对外展示的局域网 IPv4（排除回环、链路本地、TUN/基准测试网段）。"""
    try:
        parts = [int(x) for x in str(ip).split(".")]
    except (TypeError, ValueError):
        return False
    if len(parts) != 4 or any(p < 0 or p > 255 for p in parts):
        return False
    a, b = parts[0], parts[1]
    if a in (0, 127) or a >= 224:
        return False
    if a == 169 and b == 254:          # 链路本地
        return False
    if a == 198 and b in (18, 19):     # 198.18.0.0/15：Clash Meta 等 TUN 常用
        return False
    return True


def lan_ipv4_addresses():
    """本机可用于局域网访问的 IPv4 地址（去重，排除回环/链路本地/虚拟网卡）。

    psutil 可选：装了就能按网卡名排除 TUN/Hyper-V；没装则只靠网段判断。
    """
    found = []
    try:
        import psutil
        for name, addrs in psutil.net_if_addrs().items():
            if _virtual_if(name):
                continue
            for addr in addrs:
                if getattr(addr, "family", None) == socket.AF_INET:
                    found.append(addr.address)
    except Exception:
        pass
    if not found:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                found.append(s.getsockname()[0])
            finally:
                s.close()
        except OSError:
            pass
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                found.append(info[4][0])
        except OSError:
            pass
    result = []
    for ip in found:
        if not ip or ip in result or not _is_lan_ipv4(ip):
            continue
        result.append(ip)
    return result


def admin_policy():
    """管理端访问策略：默认只允许本机，远程需要 allow_remote + token。"""
    cfg = CFG.get("admin") or {}
    return {
        "allow_remote": bool(cfg.get("allow_remote")),
        "token": str(cfg.get("token") or ""),
    }


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
    global CFG, PROVIDERS, DEFAULT_PROXY, CFG_REVISION
    CFG = load_config()
    PROVIDERS = CFG.get("providers", [])
    DEFAULT_PROXY = CFG.get("proxy", "")
    CFG_REVISION = config_revision(CFG)
    console_cache = globals().get("CONSOLE_CACHE")
    if isinstance(console_cache, dict):
        console_cache.update({"ts": 0.0, "data": None})


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
LIVE_LOCK = threading.Lock()
LIVE_USAGE = []
LIVE_WINDOW = 60


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _blank_usage():
    return {
        "requests": 0, "ok": 0, "errors": 0,
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        "cached_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
        "usage_missing": 0,
        "cost": 0.0, "list_cost": 0.0, "allowance": 0.0,
        "latency_ms": 0, "last_latency_ms": 0, "last_used": "",
        "tps_sum": 0.0, "tps_count": 0, "tps_max": 0.0,
    }


DAYS_KEEP = 90


def _prune_days(days):
    """删除超过 90 天的按天统计（按日期字符串比较）；顺带清理结构损坏的桶。"""
    if not isinstance(days, dict):
        return
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - DAYS_KEEP * 86400))
    for key in list(days.keys()):
        bucket = days.get(key)
        if not isinstance(key, str) or key < cutoff or not isinstance(bucket, dict):
            del days[key]


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
        quota = data.get("quota")
        if not isinstance(quota, dict):
            quota = {}
            data["quota"] = quota
        quota.setdefault("since", _now_iso())
        quota.setdefault("billing_source", "")
        if not isinstance(quota.get("buckets"), dict):
            quota["buckets"] = {}
        if not isinstance(data.get("quota_history"), list):
            data["quota_history"] = []
        days = data.get("days")
        if not isinstance(days, dict):
            days = {}
            data["days"] = days
        _prune_days(days)
        return data
    except Exception:
        return {"since": _now_iso(), "updated": "", "totals": _blank_usage(),
                "providers": {}, "models": {}, "days": {},
                "quota": {"since": _now_iso(), "billing_source": "", "buckets": {}},
                "quota_history": []}


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
    for key in ("cost", "list_cost", "allowance"):
        stat[key] = round(float(stat.get(key, 0)) + float(usage.get(key, 0) or 0), 8)
    latency = max(0, int(latency_ms or 0))
    stat["latency_ms"] = int(stat.get("latency_ms", 0)) + latency
    stat["last_latency_ms"] = latency
    stat["last_used"] = _now_iso()
    tps = usage.get("output_tps")
    if tps is not None:
        try:
            tps = max(0.0, float(tps))
        except (TypeError, ValueError):
            tps = 0.0
        if tps > 0:
            stat["tps_sum"] = float(stat.get("tps_sum", 0) or 0) + tps
            stat["tps_count"] = int(stat.get("tps_count", 0) or 0) + 1
            stat["tps_max"] = max(float(stat.get("tps_max", 0) or 0), tps)


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

    # 每秒输出 token（TPS）：用本次请求耗时估算生成速度
    latency_ms_int = max(0, int(latency_ms or 0))
    out_tokens = int(usage.get("output_tokens", 0) or 0)
    if out_tokens > 0 and latency_ms_int > 0:
        usage["output_tps"] = round(out_tokens / (latency_ms_int / 1000.0), 2)
        with LIVE_LOCK:
            LIVE_USAGE.append((time.time(), provider_name, model,
                               out_tokens, int(usage.get("input_tokens", 0) or 0),
                               latency_ms_int))
            cutoff = time.time() - LIVE_WINDOW
            while LIVE_USAGE and LIVE_USAGE[0][0] < cutoff:
                LIVE_USAGE.pop(0)

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

        # 按天累计：供 今日 / 本周 / 本月 时间段统计使用（仅从本次更新后开始记录）
        days = USAGE.setdefault("days", {})
        day_key = time.strftime("%Y-%m-%d", time.localtime())
        bucket = days.setdefault(day_key, {"totals": _blank_usage(),
                                           "providers": {}, "models": {}})
        if not isinstance(bucket.get("totals"), dict):
            bucket["totals"] = _blank_usage()
        if not isinstance(bucket.get("providers"), dict):
            bucket["providers"] = {}
        if not isinstance(bucket.get("models"), dict):
            bucket["models"] = {}
        _bump_usage(bucket["totals"], usage, ok, latency_ms)
        dstat = bucket["providers"].setdefault(provider_name, _blank_usage())
        dstat["name"] = provider_name
        _bump_usage(dstat, usage, ok, latency_ms)
        dmstat = bucket["models"].setdefault(key, _blank_usage())
        dmstat["model"] = model
        dmstat["provider"] = provider_name
        dmstat["upstream_model"] = upstream_model or model
        _bump_usage(dmstat, usage, ok, latency_ms)
        _prune_days(days)

        # Go 额度：按小时累计 opencode.allowance，用于 5 小时/周/月进度
        allowance = float(usage.get("allowance") or 0)
        if allowance > 0:
            quota = USAGE.setdefault("quota", {"since": _now_iso(),
                                               "billing_source": "", "buckets": {}})
            quota.setdefault("since", _now_iso())
            buckets = quota.setdefault("buckets", {})
            hour_key = time.strftime("%Y-%m-%dT%H", time.localtime())
            quota_model = f"{provider_name}::{upstream_model or model}"
            bucket = buckets.setdefault(hour_key, {})
            bucket[quota_model] = round(float(bucket.get(quota_model, 0)) + allowance, 8)
            if usage.get("billing_source"):
                quota["billing_source"] = str(usage["billing_source"])
            quota["updated"] = _now_iso()
            cutoff = time.strftime("%Y-%m-%dT%H", time.localtime(time.time() - 32 * 86400))
            for key in list(buckets.keys()):
                if key < cutoff:
                    del buckets[key]

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
        USAGE["days"] = {}
        try:
            _save_usage_locked()
        except Exception as e:
            log(f"ERR usage save: {e}")


def _cache_hit_rate(stat):
    try:
        inp = int(stat.get("input_tokens", 0) or 0)
        cached = int(stat.get("cached_tokens", 0) or 0)
    except (TypeError, ValueError):
        return None
    if inp <= 0:
        return None
    return round(min(100.0, cached / inp * 100), 2)


VENDOR_RULES = (
    ("Anthropic", ("claude",)),
    ("OpenAI", ("gpt", "codex", "davinci", "o1", "o3", "o4")),
    ("Google", ("gemini", "gemma")),
    ("xAI", ("grok",)),
    ("DeepSeek", ("deepseek",)),
    ("智谱 AI", ("glm", "chatglm")),
    ("Moonshot", ("kimi", "moonshot")),
    ("阿里云（通义）", ("qwen", "tongyi")),
    ("MiniMax", ("minimax",)),
    ("小米（MiMo）", ("mimo",)),
    ("腾讯（混元）", ("hy3", "hy4", "hunyuan")),
    ("阶跃星辰", ("step-",)),
    ("美团 LongCat", ("longcat",)),
    ("Meta", ("muse", "llama", "meta/")),
    ("Mistral", ("mistral", "mixtral")),
    ("NVIDIA", ("nemotron",)),
    ("OpenCode", ("big-pickle", "crouton", "flint", "iceman", "inkling",
                  "jev-", "beam", "pickle")),
)
VENDOR_PREFIXES = {
    "meta": "Meta", "openai": "OpenAI", "anthropic": "Anthropic",
    "google": "Google", "deepseek": "DeepSeek", "qwen": "阿里云（通义）",
    "moonshotai": "Moonshot", "x-ai": "xAI", "mistralai": "Mistral",
    "nvidia": "NVIDIA",
}


def infer_vendor(model):
    """按模型 id 推断厂商（用于按厂商汇总用量）。"""
    name = str(model or "").strip().lower()
    if "/" in name:
        mapped = VENDOR_PREFIXES.get(name.split("/", 1)[0])
        if mapped:
            return mapped
    for vendor, prefixes in VENDOR_RULES:
        if any(name.startswith(p) for p in prefixes):
            return vendor
    return "其他"


def model_vendor(model, provider_name=""):
    """优先使用 model_meta.vendor，其次按模型名推断。"""
    for p in PROVIDERS:
        if provider_name and p.get("name") != provider_name:
            continue
        meta = (p.get("model_meta") or {}).get(model)
        if isinstance(meta, dict) and meta.get("vendor"):
            return str(meta["vendor"])
    return infer_vendor(model)


def _merge_usage(dst, src):
    for key in ("requests", "ok", "errors", "usage_missing") + USAGE_FIELDS:
        dst[key] = int(dst.get(key, 0)) + int(src.get(key, 0) or 0)
    for key in ("cost", "list_cost", "allowance"):
        dst[key] = round(float(dst.get(key, 0)) + float(src.get(key, 0) or 0), 8)
    dst["latency_ms"] = int(dst.get("latency_ms", 0)) + int(src.get("latency_ms", 0) or 0)
    dst["tps_sum"] = float(dst.get("tps_sum", 0) or 0) + float(src.get("tps_sum", 0) or 0)
    dst["tps_count"] = int(dst.get("tps_count", 0) or 0) + int(src.get("tps_count", 0) or 0)
    dst["tps_max"] = max(float(dst.get("tps_max", 0) or 0),
                         float(src.get("tps_max", 0) or 0))
    last = str(src.get("last_used") or "")
    if last > str(dst.get("last_used") or ""):
        dst["last_used"] = last


def _avg_tps(stat):
    count = int(stat.get("tps_count", 0) or 0)
    if count <= 0:
        return None
    return round(float(stat.get("tps_sum", 0) or 0) / count, 2)


USAGE_PERIODS = ("today", "week", "month", "all")
PERIOD_LABELS = {"today": "今日", "week": "本周", "month": "本月", "all": "全部"}
PERIOD_NOTE = "按天统计自本次更新后开始，之前的数据无按天记录。"


def _period_date_list(period):
    """返回时间段包含的本地日期（YYYY-MM-DD）列表；all 返回 None。

    today：当天；week：本周一到今天；month：本月 1 号到今天。
    """
    today = datetime.now().date()
    if period == "today":
        start = today
    elif period == "week":
        start = today - timedelta(days=today.weekday())
    elif period == "month":
        start = today.replace(day=1)
    else:
        return None
    dates = []
    cur = start
    while cur <= today:
        dates.append(cur.isoformat())
        cur += timedelta(days=1)
    return dates


def _merge_usage_once(dst, src):
    """按 _merge_usage 合并，但 usage_missing 只加一次。

    注：_merge_usage 会把 usage_missing 加两次——它既在
    ("requests", "ok", "errors", "usage_missing") 里，又在 USAGE_FIELDS 里。
    厂商汇总沿用旧行为不动；时间段汇总用这里修正后的版本。
    """
    _merge_usage(dst, src)
    dst["usage_missing"] = int(dst.get("usage_missing", 0)) - int(src.get("usage_missing", 0) or 0)


def _aggregate_period(period):
    """汇总指定时间段的 totals / providers / models（与累计数据同形）。

    period 为 "all" 时直接返回累计数据；否则把 USAGE["days"] 里
    对应日期的按天桶相加。返回 (totals, providers_dict, models_dict)。
    """
    if period == "all":
        return (dict(USAGE.get("totals", {}) or {}),
                {k: dict(v) for k, v in (USAGE.get("providers", {}) or {}).items()},
                {k: dict(v) for k, v in (USAGE.get("models", {}) or {}).items()})
    dates = _period_date_list(period) or []
    totals = _blank_usage()
    providers = {}
    models = {}
    days = USAGE.get("days") or {}
    for ds in dates:
        bucket = days.get(ds)
        if not isinstance(bucket, dict):
            continue
        _merge_usage_once(totals, bucket.get("totals") or {})
        for name, stat in (bucket.get("providers") or {}).items():
            if not isinstance(stat, dict):
                continue
            item = providers.setdefault(name, _blank_usage())
            item["name"] = name
            _merge_usage_once(item, stat)
        for key, stat in (bucket.get("models") or {}).items():
            if not isinstance(stat, dict):
                continue
            item = models.setdefault(key, _blank_usage())
            item["model"] = stat.get("model") or key.partition("::")[2]
            item["provider"] = stat.get("provider") or key.partition("::")[0]
            item["upstream_model"] = stat.get("upstream_model") or item["model"]
            _merge_usage_once(item, stat)
    return totals, providers, models


def _build_usage_lists(totals, providers, models):
    """由 totals / providers / models 生成快照里的列表（含厂商汇总与排序）。"""
    providers_list = []
    for name, stat in providers.items():
        item = {"name": name}
        item.update(stat)
        item["cache_hit_rate"] = _cache_hit_rate(item)
        item["avg_tps"] = _avg_tps(item)
        providers_list.append(item)

    vendors = {}
    models_list = []
    for key, stat in models.items():
        provider_name, _, model = key.partition("::")
        vendor = model_vendor(model, provider_name)
        item = {"key": key, "vendor": vendor}
        item.update(stat)
        item["cache_hit_rate"] = _cache_hit_rate(item)
        item["avg_tps"] = _avg_tps(item)
        models_list.append(item)

        agg = vendors.setdefault(vendor, _blank_usage())
        agg["vendor"] = vendor
        _merge_usage(agg, stat)

    for item in vendors.values():
        item["cache_hit_rate"] = _cache_hit_rate(item)
        item["avg_tps"] = _avg_tps(item)

    providers_list.sort(key=lambda x: int(x.get("total_tokens", 0)), reverse=True)
    models_list.sort(key=lambda x: int(x.get("total_tokens", 0)), reverse=True)
    vendor_list = sorted(vendors.values(),
                         key=lambda x: int(x.get("total_tokens", 0)), reverse=True)
    totals = dict(totals)
    totals["cache_hit_rate"] = _cache_hit_rate(totals)
    totals["avg_tps"] = _avg_tps(totals)
    return totals, providers_list, vendor_list, models_list


def usage_snapshot(period="all"):
    if period not in USAGE_PERIODS:
        period = "all"
    with USAGE_LOCK:
        totals, providers, models = _aggregate_period(period)
        since = USAGE.get("since", "")
        updated = USAGE.get("updated", "")
        days_kept = len(USAGE.get("days") or {})
    totals, providers, vendors, models = _build_usage_lists(totals, providers, models)

    now = time.time()
    with LIVE_LOCK:
        live = [x for x in LIVE_USAGE if now - x[0] <= LIVE_WINDOW]
    out_sum = sum(int(x[3]) for x in live)
    in_sum = sum(int(x[4]) for x in live)
    span = float(LIVE_WINDOW)
    if live:
        span = max(1.0, min(float(LIVE_WINDOW), now - float(live[0][0])))
    by_model = {}
    for ts, provider_name, model, out_tokens, in_tokens, lat in live:
        row = by_model.setdefault(model, {"model": model, "provider": provider_name,
                                          "output_tokens": 0, "input_tokens": 0,
                                          "requests": 0})
        row["output_tokens"] += int(out_tokens)
        row["input_tokens"] += int(in_tokens)
        row["requests"] += 1
    for row in by_model.values():
        row["output_tps"] = round(row["output_tokens"] / span, 2)
    live_by_model = sorted(by_model.values(),
                           key=lambda x: x["output_tokens"], reverse=True)[:10]

    dates = _period_date_list(period)
    if period == "all":
        period_note = ""
        period_start = ""
        period_end = ""
    else:
        period_note = PERIOD_NOTE
        period_start = dates[0] if dates else ""
        period_end = dates[-1] if dates else ""
    return {
        "since": since,
        "updated": updated,
        "period": period,
        "period_label": PERIOD_LABELS.get(period, "全部"),
        "period_start": period_start,
        "period_end": period_end,
        "period_note": period_note,
        "days_kept": days_kept,
        "totals": totals,
        "providers": providers,
        "vendors": vendors,
        "models": models,
        "live": {
            "window_seconds": LIVE_WINDOW,
            "span_seconds": round(span, 1),
            "requests": len(live),
            "output_tokens": out_sum,
            "input_tokens": in_sum,
            "output_tps": round(out_sum / span, 2),
            "input_tps": round(in_sum / span, 2),
            "total_tps": round((out_sum + in_sum) / span, 2),
            "by_model": live_by_model,
        },
    }


QUOTA_CACHE_LOCK = threading.Lock()
QUOTA_CACHE = {"ts": 0.0, "ok": False, "usage": None,
               "error": "", "fetched_at": "", "url": ""}
QUOTA_CACHE_TTL = 30

CONSOLE_CACHE_LOCK = threading.Lock()
CONSOLE_CACHE = {"ts": 0.0, "data": None}
CONSOLE_CACHE_TTL = 120
CONSOLE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")


def append_quota_history(usage):
    """把服务端百分比采样写入 quota_history（用于估算消耗速度）。"""
    if not isinstance(usage, dict):
        return
    values = {}
    for key in ("rolling", "weekly", "monthly"):
        item = usage.get(key)
        if isinstance(item, dict) and isinstance(item.get("percent"), (int, float)):
            values[key] = float(item["percent"])
    if not values:
        return
    now = time.time()
    with USAGE_LOCK:
        history = USAGE.setdefault("quota_history", [])
        if history:
            last = history[-1]
            try:
                same = (now - float(last.get("ts", 0)) < 30 and
                        all(abs(float(last.get(k, -999)) - v) < 0.01 for k, v in values.items()))
            except (TypeError, ValueError):
                same = False
            if same:
                return
        entry = {"ts": round(now, 3)}
        entry.update(values)
        history.append(entry)
        cutoff = now - 8 * 86400
        USAGE["quota_history"] = [x for x in history if float(x.get("ts", 0) or 0) >= cutoff]
        try:
            _save_usage_locked()
        except Exception as e:
            log(f"ERR quota history save: {e}")


def _iso_epoch(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0


def quota_eta(usage, history):
    """按服务端百分比变化估算各窗口用完时间。"""
    now = time.time()
    windows = {"rolling": 5 * 3600, "weekly": 7 * 86400, "monthly": 30 * 86400}
    result = {}
    for key, window_seconds in windows.items():
        item = usage.get(key) if isinstance(usage, dict) else None
        if not isinstance(item, dict):
            result[key] = {"status": "unknown"}
            continue
        try:
            pct = float(item.get("percent") or 0)
        except (TypeError, ValueError):
            pct = 0.0
        reset_ts = _iso_epoch(item.get("resetsAt"))
        reset_in = (reset_ts - now) / 3600 if reset_ts else None
        base = {
            "percent": round(pct, 2),
            "reset_in_hours": round(reset_in, 2) if reset_in is not None else None,
        }
        if pct >= 100:
            base["status"] = "exhausted"
            result[key] = base
            continue

        points = []
        for row in history:
            try:
                t = float(row.get("ts"))
                v = float(row.get(key))
            except (TypeError, ValueError):
                continue
            points.append((t, v))
        points.sort()
        # 百分比下降视为窗口重置，丢掉重置前的点
        kept = []
        for t, v in points:
            if kept and v < kept[-1][1] - 1.0:
                kept = []
            kept.append((t, v))

        recent = [(t, v) for t, v in kept if t >= now - 3600]
        if reset_ts:
            start = reset_ts - window_seconds
            elapsed = (now - start) / 3600
        else:
            elapsed = window_seconds / 3600
        avg_rate = (pct / elapsed) if elapsed > 0 and pct > 0 else 0.0
        recent_rate = 0.0
        # 至少 30 分钟、3 个点才用近期速度；对整数百分比做线性回归平滑
        if len(recent) >= 3 and recent[-1][0] - recent[0][0] >= 1800:
            t0 = recent[0][0]
            xs = [t - t0 for t, _ in recent]
            ys = [v for _, v in recent]
            n = len(xs)
            sx = sum(xs)
            sy = sum(ys)
            sxx = sum(x * x for x in xs)
            sxy = sum(x * y for x, y in zip(xs, ys))
            den = n * sxx - sx * sx
            if den > 0:
                recent_rate = ((n * sxy - sx * sy) / den) * 3600

        if recent_rate > 0:
            rate = recent_rate
            basis = "recent"
        elif avg_rate > 0:
            rate = avg_rate
            basis = "average"
        else:
            rate = 0.0
            basis = "none"

        base["basis"] = basis
        base["rate_per_hour"] = round(rate, 4)
        if rate <= 0:
            base["status"] = "flat"
            result[key] = base
            continue

        hours = (100 - pct) / rate
        base["status"] = "ok"
        base["hours"] = round(hours, 2)
        base["at"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(now + hours * 3600))
        base["before_reset"] = None if reset_in is None else (hours < reset_in)
        result[key] = base
    return result


def fetch_server_quota(force=False):
    """读取 OpenCode Go 服务端额度（/zen/go/v1/usage）。"""
    now = time.time()
    with QUOTA_CACHE_LOCK:
        if (not force and now - QUOTA_CACHE["ts"] < QUOTA_CACHE_TTL
                and (QUOTA_CACHE["usage"] or QUOTA_CACHE["error"])):
            return dict(QUOTA_CACHE)

    provider = next((p for p in PROVIDERS if p.get("name") == "opencode-go"), None)
    url = ""
    result = None
    if not provider:
        result = {"ok": False, "usage": None,
                  "error": "opencode-go provider not found", "fetched_at": _now_iso(), "url": ""}
    else:
        key = str(provider.get("api_key") or "").strip()
        url = str(provider.get("usage_url") or "https://opencode.ai/zen/go/v1/usage").strip()
        if not key:
            result = {"ok": False, "usage": None,
                      "error": "opencode-go api_key is empty", "fetched_at": _now_iso(), "url": url}
        else:
            headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
            attempts = [None]
            if DEFAULT_PROXY:
                attempts.append({"http": DEFAULT_PROXY, "https": DEFAULT_PROXY})
            last_error = ""
            for proxies in attempts:
                try:
                    r = requests.get(url, headers=headers, timeout=20, proxies=proxies)
                    if r.status_code == 200:
                        data = r.json()
                        usage = data.get("usage") if isinstance(data, dict) else None
                        if not isinstance(usage, dict):
                            raise ValueError("invalid usage response")
                        result = {"ok": True, "usage": usage, "error": "",
                                  "fetched_at": _now_iso(), "url": url}
                        append_quota_history(usage)
                        break
                    last_error = f"HTTP {r.status_code}: {r.text[:200]}"
                    if r.status_code in (401, 403):
                        break
                except Exception as e:
                    last_error = str(e)
            if result is None:
                result = {"ok": False, "usage": None, "error": last_error or "unknown error",
                          "fetched_at": _now_iso(), "url": url}

    with QUOTA_CACHE_LOCK:
        QUOTA_CACHE.update(result)
        QUOTA_CACHE["ts"] = time.time()
    return dict(QUOTA_CACHE)


def _micro_usd(value):
    """Console 金额单位：1e8 micro-cents = 1 USD。"""
    if value is None or value == "":
        return None
    try:
        return round(int(str(value).strip()) / 100000000, 4)
    except (TypeError, ValueError):
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            return None


def _pick(obj, *names):
    if not isinstance(obj, dict):
        return None
    lowered = {str(k).lower(): v for k, v in obj.items()}
    for name in names:
        if name in obj:
            return obj[name]
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _console_request(path, cookie, org_id=""):
    """访问 OpenCode Console API（需要浏览器会话 Cookie）。"""
    url = "https://opencode.ai" + path
    headers = {
        "Accept": "application/json",
        "Cookie": cookie,
        "User-Agent": CONSOLE_UA,
        "Referer": "https://opencode.ai/",
        "Origin": "https://opencode.ai",
    }
    if org_id:
        headers["x-org-id"] = org_id
    attempts = [None]
    if DEFAULT_PROXY:
        attempts.append({"http": DEFAULT_PROXY, "https": DEFAULT_PROXY})
    status, body = 0, ""
    for proxies in attempts:
        try:
            r = requests.get(url, headers=headers, timeout=20, proxies=proxies)
            status = r.status_code
            try:
                body = r.json()
            except ValueError:
                body = r.text[:300]
            if status == 200 or status in (401, 403):
                return status, body
        except Exception as e:
            status, body = 0, str(e)
    return status, body


def _extract_org_id(data):
    found = []

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                if isinstance(value, str) and re.fullmatch(r"org_[A-Za-z0-9]+", value):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    if found:
        return found[0]
    match = re.search(r"org_[A-Za-z0-9]+", str(data))
    return match.group(0) if match else ""


def fetch_zen_console(force=False):
    """读取 Zen 预付费余额/月度预算（Console 会话 Cookie），可选。"""
    cfg = CFG.get("zen_console") or {}
    now = time.time()
    with CONSOLE_CACHE_LOCK:
        if (not force and now - CONSOLE_CACHE["ts"] < CONSOLE_CACHE_TTL
                and CONSOLE_CACHE["data"]):
            return dict(CONSOLE_CACHE["data"])

    result = {
        "configured": bool(cfg.get("enabled")),
        "ok": False,
        "fetched_at": _now_iso(),
        "error": "",
        "org_id": "",
        "balance_usd": None,
        "available_usd": None,
        "billing_mode": "",
        "mode": "",
        "monthly_limit_usd": None,
        "monthly_spent_usd": None,
        "resets_at": "",
        "go_meters": {},
    }

    if not cfg.get("enabled"):
        result["error"] = "zen_console.enabled is false"
    elif not str(cfg.get("session_cookie") or "").strip():
        result["error"] = "zen_console.session_cookie is empty"
    else:
        cookie = str(cfg.get("session_cookie") or "").strip()
        org = str(cfg.get("org_id") or "").strip()
        status, data = _console_request("/console/api/orgs", cookie)
        if status != 200:
            result["error"] = f"console /orgs HTTP {status}: {str(data)[:200]}"
        else:
            if not org:
                org = _extract_org_id(data)
            result["org_id"] = org
            if not org:
                result["error"] = "cannot discover org_id (set zen_console.org_id)"
            else:
                st, billing = _console_request(
                    "/console/api/billing/status", cookie, org)
                if st == 200 and isinstance(billing, dict):
                    result["ok"] = True
                    result["balance_usd"] = _micro_usd(
                        _pick(billing, "balanceMicroCents", "balance"))
                    result["available_usd"] = _micro_usd(
                        _pick(billing, "availableMicroCents", "available"))
                    result["billing_mode"] = str(
                        _pick(billing, "billingMode", "billing_mode") or "")
                    result["mode"] = str(_pick(billing, "mode") or "")
                else:
                    result["error"] = (
                        f"console billing/status HTTP {st}: {str(billing)[:200]}")

                st, budgets = _console_request(
                    "/console/api/budgets/org", cookie, org)
                if st == 200 and isinstance(budgets, dict):
                    result["monthly_limit_usd"] = _micro_usd(
                        _pick(budgets, "limitMicroCents", "limit"))
                    result["monthly_spent_usd"] = _micro_usd(
                        _pick(budgets, "spentMicroCents", "spent"))
                    result["resets_at"] = str(
                        _pick(budgets, "resetsAt", "resets_at") or "")

                st, go_status = _console_request(
                    "/console/api/go/status", cookie, org)
                if st == 200 and isinstance(go_status, dict):
                    access = go_status.get("access") or go_status
                    meters = access.get("meters") or {}
                    for src, dst in (("fiveHour", "rolling"),
                                     ("week", "weekly"),
                                     ("month", "monthly")):
                        meter = meters.get(src)
                        if not isinstance(meter, dict):
                            continue
                        used = _micro_usd(_pick(meter, "usedMicroCents", "used"))
                        limit = _micro_usd(_pick(meter, "limitMicroCents", "limit"))
                        percent = (round(used / limit * 100, 1)
                                   if used is not None and limit else None)
                        result["go_meters"][dst] = {
                            "used_usd": used,
                            "limit_usd": limit,
                            "percent": percent,
                        }
                    result["go_resets_at"] = str(
                        access.get("endsAt") or go_status.get("endsAt") or "")

    with CONSOLE_CACHE_LOCK:
        CONSOLE_CACHE.update({"ts": time.time(), "data": result})
    return dict(result)


def quota_snapshot(force=False):
    """服务端真实额度 + 本地按模型参考。"""
    server = fetch_server_quota(force=force)
    local = local_quota_snapshot()
    with USAGE_LOCK:
        history = [dict(x) for x in USAGE.get("quota_history", [])]
    usage = server.get("usage") or {}
    plan = (CFG.get("go_plan") or {}).get("name") or "Go"
    return {
        "ok": bool(server.get("ok")),
        "source": "opencode-server",
        "plan": plan,
        "fetched_at": server.get("fetched_at", ""),
        "url": server.get("url", ""),
        "error": server.get("error", ""),
        "usage": usage,
        "eta": quota_eta(usage, history),
        "zen": fetch_zen_console(force=force),
        "local": local,
    }


def local_quota_snapshot():
    """按 opencode.allowance 汇总的本地按模型额度（参考）。"""
    now = time.time()
    lt = time.localtime(now)
    month_start = time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))
    five_start = now - 5 * 3600
    week_start = now - 7 * 86400

    def hour_epoch(key):
        try:
            return time.mktime(time.strptime(key, "%Y-%m-%dT%H"))
        except Exception:
            return 0

    with USAGE_LOCK:
        quota = USAGE.get("quota") or {}
        buckets = quota.get("buckets") or {}
        sums = {}
        for hour_key, bucket in buckets.items():
            if not isinstance(bucket, dict):
                continue
            epoch = hour_epoch(hour_key)
            if epoch <= 0:
                continue
            in_five = epoch >= five_start - 3600
            in_week = epoch >= week_start - 3600
            in_month = epoch >= month_start - 3600
            for model_key, value in bucket.items():
                try:
                    amount = float(value or 0)
                except (TypeError, ValueError):
                    continue
                stat = sums.setdefault(model_key, {"five_hour": 0.0, "week": 0.0, "month": 0.0})
                if in_five:
                    stat["five_hour"] += amount
                if in_week:
                    stat["week"] += amount
                if in_month:
                    stat["month"] += amount
        since = quota.get("since", "")
        billing = quota.get("billing_source", "") or "go"

    plan_name = (CFG.get("go_plan") or {}).get("name") or \
        {"go": "Go", "go-plus": "Go Plus", "go-teams": "Go Teams"}.get(billing, "Go")
    totals = {
        "five_hour": {"used": 0.0, "limit": 0.0},
        "week": {"used": 0.0, "limit": 0.0},
        "month": {"used": 0.0, "limit": 0.0},
    }
    models = []
    for p in PROVIDERS:
        metas = p.get("model_meta") or {}
        provider_name = p.get("name", "")
        for model in p.get("models", []) or []:
            meta = metas.get(model) or {}
            limit = meta.get("monthly_limit")
            unlimited = bool(meta.get("unlimited"))
            if limit is None and not unlimited:
                continue
            key = f"{provider_name}::{model}"
            used = sums.get(key, {})
            item = {
                "provider": provider_name,
                "model": model,
                "monthly_limit": limit,
                "unlimited": unlimited,
                "free": bool(meta.get("free")),
            }
            if isinstance(limit, (int, float)) and limit > 0:
                for window, factor in (("five_hour", 0.2), ("week", 0.5), ("month", 1.0)):
                    window_limit = float(limit) * factor
                    window_used = float(used.get(window, 0) or 0)
                    item[window] = {
                        "used": round(window_used, 6),
                        "limit": round(window_limit, 6),
                        "percent": round(window_used / window_limit * 100, 2) if window_limit else 0,
                    }
                    totals[window]["used"] += window_used
                    totals[window]["limit"] += window_limit
            else:
                for window in ("five_hour", "week", "month"):
                    item[window] = {"used": 0.0, "limit": None, "percent": 0}
            models.append(item)

    for window in totals:
        used = round(totals[window]["used"], 6)
        limit = round(totals[window]["limit"], 6)
        totals[window] = {"used": used, "limit": limit,
                          "percent": round(used / limit * 100, 2) if limit else 0}

    models.sort(key=lambda x: x.get("month", {}).get("percent", 0), reverse=True)
    return {
        "plan": plan_name,
        "billing_source": billing,
        "since": since,
        "updated": _now_iso(),
        "windows": {
            "five_hour": {"label": "5 小时", "factor": 0.2},
            "week": {"label": "近 7 天", "factor": 0.5},
            "month": {"label": "本月", "factor": 1.0},
        },
        "totals": totals,
        "models": models,
        "source": "gateway",
    }


DEFAULT_ROUTING_CONFIG = {
    "enabled": True,
    "fallbacks": {
        "gpt-6-luna": ["gpt-6-luna", "gpt-5.6-luna", "glm-5.3-flash", "LongCat-2.5-Preview"],
        "gpt-5.6-luna": ["gpt-5.6-luna", "glm-5.3-flash", "LongCat-2.5-Preview"],
        "deepseek-v4.1-flash": ["deepseek-v4.1-flash", "glm-5.3-flash", "LongCat-2.5-Preview"],
        "claude-haiku-5-5": ["claude-haiku-5-5", "glm-5.3-flash"],
        "glm-5.3-flash": ["glm-5.3-flash", "deepseek-v4.1-flash", "LongCat-2.5-Preview"],
    },
    "smart_aliases": {
        "smart": ["gpt-6-luna", "glm-5.3-flash", "deepseek-v4.1-flash", "LongCat-2.5-Preview"],
        "cheap": ["glm-5.3-flash", "deepseek-v4.1-flash", "LongCat-2.5-Preview"],
        "vision": ["gpt-6-luna", "gemini-4-argon", "claude-haiku-5-5"],
        "coding": ["gpt-6-luna", "deepseek-v4.1-flash", "kimi-k3.1-pro"],
    },
    "retry_on": [400, 402, 403, 404, 408, 429, 500, 502, 503, 504, 529],
    "max_attempts": 4,
    "cooldown_seconds": 300,
}


def admin_html():
    """管理端 HTML（把版本号占位符替换掉，方便确认页面是否更新）。"""
    return (ADMIN_HTML
            .replace("{{VERSION}}", GW_VERSION)
            .replace("{{ROUTING_DEFAULT}}", json.dumps(DEFAULT_ROUTING_CONFIG,
                                                       ensure_ascii=False)))


ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>聚合网关管理端</title>
<style>
/* ===== DESIGN.md §2.3 完整 :root —— 整块粘贴，五段齐全 ===== */
:root{
  /* ① 面：三级明度 */
  --bg:#F4F6F9; --surface:#FFFFFF; --raised:#EDF1F6; --border:#DDE3EC;
  /* ② 字：正文用满色墨，次要才降灰 */
  --ink:#16202E; --ink-2:#3A4657; --muted:#5B6675;
  /* ③ 强调：只上按钮 / 当前项 / 关键数字 */
  --accent:#2563EB; --accent-on:#FFFFFF; --accent-hover:#1D4ED8; --accent-soft:#E8F0FE;
  /* ④ 语义四件套（深字）；浅底另列 */
  --info:#1D4ED8; --warn:#8A5A00; --danger:#B42318; --success:#1F7A4D;
  --info-soft:#E8F0FE; --warn-soft:#FBF0DA; --danger-soft:#FDECEA; --success-soft:#E7F4EC;
  /* ⑤ 深色终端（产品自带，继承） */
  --log-bg:#0F172A; --log-fg:#E2E8F0;
  /* ⑥ 高度二选一：平面表面=发丝线，浮起表面=阴影，永不叠加 */
  --elev-flat:none;
  --elev-ring:0 0 0 1px var(--border);
  --elev-raised:0 8px 28px rgba(22,32,46,.16);
  --focus:0 0 0 2px var(--accent), 0 0 0 4px rgba(37,99,235,.18);
  /* ⑦ 动效：三处就够 */
  --motion-fast:150ms; --motion-base:200ms; --ease:cubic-bezier(.2,0,0,1);
  /* ⑧ 八档字号（§3） */
  --fs-1:11px; --fs-2:12px; --fs-3:13px; --fs-4:14px;
  --fs-5:16px; --fs-6:20px; --fs-7:24px; --fs-8:32px;
  /* ⑨ 间距 8 档（§4） */
  --s-1:4px; --s-2:8px; --s-3:12px; --s-4:16px;
  --s-6:24px; --s-8:32px; --s-12:48px; --s-18:72px;
  /* ⑩ 圆角 3 档 / 容器 / 层级 */
  --r-sm:8px; --r-md:12px; --r-lg:16px;
  --container:1120px;
  --z-base:0; --z-dropdown:100; --z-sticky:200; --z-overlay:300;
  --z-modal:400; --z-toast:500; --z-tooltip:600;
  /* ⑪ 字体（系统栈，用户禁外部字体） */
  --font-ui:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC","Source Han Sans SC",sans-serif;
  --font-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
}
/* 冻结脚本内联样式仍引用旧变量名：只做别名映射，值不新造 */
:root{
  --mono:var(--font-mono); --card:var(--surface); --text:var(--ink);
  --primary:var(--accent); --primary-h:var(--accent-hover);
  --ok:var(--success); --tag:var(--raised); --mask:rgba(15,23,42,.45);
}

/* ===== 基础 ===== */
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--font-ui);background:var(--bg);color:var(--ink);font-size:var(--fs-4);line-height:1.7;font-weight:400;-webkit-font-smoothing:antialiased}
code,kbd,pre{font-family:var(--font-mono)}
code{font-size:var(--fs-2);background:var(--raised);color:var(--ink-2);padding:0 var(--s-1);border-radius:var(--r-sm)}
small{font-size:var(--fs-2)}
a{color:var(--accent)}
[hidden]{display:none!important}
::selection{background:var(--accent-soft);color:var(--ink)}
::placeholder{color:var(--muted);opacity:1}
*{scrollbar-color:var(--border) transparent;scrollbar-width:thin}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:var(--r-sm)}
::-webkit-scrollbar-track{background:transparent}
:focus-visible{outline:none;box-shadow:var(--focus)}
input,textarea{caret-color:var(--accent)}
.tab,.stat .value,.quota-card .value,.table-wrap td.num{font-variant-numeric:tabular-nums}

/* ===== 常驻壳：细线 T 形 48px 顶栏（§5.0） ===== */
header{background:var(--surface);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:var(--z-sticky)}
.shell{max-width:var(--container);margin:0 auto;padding:0 var(--s-6);display:flex;align-items:center;justify-content:space-between;gap:var(--s-6);min-height:48px}
.header-left{display:flex;align-items:center;gap:var(--s-6);min-width:0}
header h1{font-size:var(--fs-5);font-weight:500;display:flex;align-items:center;gap:var(--s-2);white-space:nowrap;line-height:1.3}
header h1 svg{flex:none}
.ver{font-size:var(--fs-1);font-weight:500;color:var(--muted);background:var(--raised);border:1px solid var(--border);padding:0 var(--s-2);border-radius:var(--r-sm)}
.header-right{display:flex;align-items:center;gap:var(--s-2);flex:none}
.badge{display:inline-flex;align-items:center;gap:var(--s-2);padding:var(--s-1) var(--s-3);border-radius:var(--r-sm);font-size:var(--fs-2);font-weight:500;background:var(--success-soft);color:var(--success)}
.badge::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--success)}
.badge.off{background:var(--danger-soft);color:var(--danger)}
.badge.off::before{background:var(--danger)}
.tabs{display:flex;align-items:stretch;gap:var(--s-2);min-height:48px}
.tab{appearance:none;cursor:pointer;background:transparent;border:0;border-bottom:2px solid transparent;border-radius:0;color:var(--muted);font-family:inherit;font-size:var(--fs-4);font-weight:400;line-height:1.3;padding:0 var(--s-3);min-height:44px;display:inline-flex;align-items:center;white-space:nowrap;transition:color var(--motion-fast) var(--ease),border-color var(--motion-fast) var(--ease)}
.tab:hover{color:var(--ink)}
.tab.is-active{color:var(--ink);font-weight:500;border-bottom-color:var(--accent)}

/* ===== 控件 ===== */
button{cursor:pointer;font-family:inherit;border:1px solid var(--border);background:var(--surface);color:var(--ink);padding:var(--s-2) var(--s-4);border-radius:var(--r-sm);font-size:var(--fs-3);font-weight:500;line-height:1.5;min-height:44px;transition:background-color var(--motion-fast) var(--ease),border-color var(--motion-fast) var(--ease),color var(--motion-fast) var(--ease)}
button:hover{border-color:var(--muted);background:var(--raised)}
button:active{background:var(--raised)}
button.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-on)}
button.primary:hover{background:var(--accent-hover);border-color:var(--accent-hover)}
button.danger{color:var(--danger);border-color:var(--danger-soft);background:var(--danger-soft)}
button.danger:hover{background:var(--danger-soft);border-color:var(--danger)}
button.sm{padding:var(--s-1) var(--s-3);font-size:var(--fs-2);min-height:44px}
button:disabled,input:disabled,textarea:disabled{opacity:.55;cursor:not-allowed}

/* ===== 容器 / 4 块板（只改 display，不摘 DOM） ===== */
main{max-width:var(--container);margin:0 auto;padding:var(--s-6) var(--s-6) var(--s-12)}
.view{display:none}
.view.is-active{display:grid;grid-template-columns:repeat(12,1fr);gap:var(--s-4);align-items:start}
.view .card{grid-column:span 12}
/* 防 min-content 撑破 12 栏栅格（真 390px 实测：栅格项默认 min-width:auto 会把卡片撑到 416px） */
.view .card,.card > *,.stat,.quota-card,.quota-cell,.quota-line,.quota-text,.tag,.table-wrap{min-width:0}
.table-wrap{max-width:100%}
code,.hint-inline,.field .hint,.quota-text,.provider-meta,.log{overflow-wrap:anywhere}
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--r-md);padding:var(--s-6)}
.card h2{font-size:var(--fs-4);font-weight:500;line-height:1.3;margin-bottom:var(--s-4);display:flex;align-items:center;justify-content:space-between;gap:var(--s-2);flex-wrap:wrap}
.card h2.sub-h{font-size:var(--fs-3);font-weight:500;color:var(--muted);display:block;margin-bottom:var(--s-2)!important}
.card h2 .hint-inline{font-weight:400}
.row{display:flex;gap:var(--s-2);flex-wrap:wrap;align-items:center}

/* ===== KPI：1px 竖线分列，不再嵌套小卡（§10 主落地决定） ===== */
.stats{display:grid;grid-template-columns:repeat(12,1fr);gap:0}
.stat,.quota-card{background:transparent;border-radius:0;border-left:1px solid var(--border);padding:var(--s-1) var(--s-4)}
.stat .label{font-size:var(--fs-2);color:var(--muted);margin-bottom:var(--s-1);line-height:1.5}
.stat .value{font-size:var(--fs-6);font-weight:500;line-height:1.3;font-variant-numeric:tabular-nums;color:var(--ink);word-break:break-word}
.stat .value small{font-size:var(--fs-2);font-weight:400;color:var(--muted)}
#stats{display:flex;flex-wrap:wrap;gap:0}
#stats .stat{flex:1 1 140px;min-width:0;border-bottom:1px solid var(--border)}
#stats .stat:first-child{border-left:0}
#usageTotals{display:grid;grid-template-columns:repeat(4,1fr);gap:0}
#usageTotals .stat{border-bottom:1px solid var(--border)}
#usageTotals .stat:nth-child(4n+1){border-left:0}
/* 冻结脚本 4 处行内 font-size（L452-455）不许改字节，用 !important 覆盖 */
#stats .stat:nth-child(-n+3) .value{font-size:var(--fs-6)}
#stats .stat:nth-child(n+4) .value{font-family:var(--font-mono)}
#stats .stat:nth-child(-n+5) .value{font-size:var(--fs-4)!important}
#stats .stat:nth-child(n+6) .value{font-size:var(--fs-2)!important}
/* 每屏唯一 32px 主角数字 */
#quotaSummary .quota-card:first-child .value{font-size:var(--fs-8);color:var(--ink);line-height:1.3}
#usageTotals .stat:nth-child(6) .value{font-size:var(--fs-8);color:var(--ink);line-height:1.3}

/* ===== 额度 / 进度 ===== */
.quota-summary{display:grid;grid-template-columns:repeat(12,1fr);gap:var(--s-4);align-items:start}
.quota-card{grid-column:span 3}
#quotaSummary .quota-card:first-child{grid-column:span 6}
#zenSummary .quota-card{grid-column:span 6}
.quota-card .label{font-size:var(--fs-2);color:var(--muted);display:flex;justify-content:space-between;gap:var(--s-2);line-height:1.5}
.quota-card .value{font-size:var(--fs-6);font-weight:500;font-variant-numeric:tabular-nums;margin:var(--s-2) 0;line-height:1.3;color:var(--ink)}
.quota-card .progress{margin-bottom:var(--s-2)}
.quota-cell{min-width:170px}
.quota-line{display:flex;align-items:center;gap:var(--s-2)}
.quota-text{font-family:var(--font-mono);font-size:var(--fs-1);color:var(--muted);white-space:nowrap}
.progress{height:8px;background:var(--raised);border-radius:var(--r-md);overflow:hidden;min-width:70px}
.progress>i{display:block;height:100%;background:var(--ink-2);transition:width var(--motion-base) var(--ease)}
.progress.warn>i{background:var(--warn)}
.progress.danger>i{background:var(--danger)}

/* ===== Provider / 标签 ===== */
.provider{border:1px solid var(--border);border-radius:var(--r-md);padding:var(--s-4);margin-bottom:var(--s-3)}
.provider:last-child{margin-bottom:0}
.provider-head{display:flex;align-items:center;justify-content:space-between;gap:var(--s-3);flex-wrap:wrap}
.provider-name{font-weight:500;font-size:var(--fs-4);display:flex;align-items:center;gap:var(--s-2);color:var(--ink);line-height:1.3}
.provider-name .dot{width:8px;height:8px;border-radius:50%;background:var(--success);flex:none}
.provider-meta{font-size:var(--fs-2);color:var(--muted);margin-top:var(--s-2);word-break:break-all;font-family:var(--font-mono)}
.tags{display:flex;flex-wrap:wrap;gap:var(--s-2);margin-top:var(--s-3)}
.tag{background:var(--raised);color:var(--ink);padding:var(--s-1) var(--s-2);border-radius:var(--r-sm);font-size:var(--fs-2);font-family:var(--font-mono);cursor:pointer;min-height:44px;display:inline-flex;align-items:center;border:1px solid transparent;transition:background-color var(--motion-fast) var(--ease),border-color var(--motion-fast) var(--ease)}
.tag:hover{border-color:var(--muted)}
.tag.proxy{background:var(--warn-soft);color:var(--warn)}
.tag.testing{background:var(--raised);color:var(--muted);cursor:wait}
.tag.ok{background:var(--success-soft);color:var(--success);border-color:var(--success)}
.tag.fail{background:var(--danger-soft);color:var(--danger);border-color:var(--danger)}
.tag.pill{background:var(--info-soft);color:var(--info);cursor:default}
#providers .provider:first-child .provider-name{font-size:var(--fs-7)}

/* ===== 日志终端（整板唯一深色通栏）/ 表格 ===== */
.log{background:var(--log-bg);color:var(--log-fg);border-radius:var(--r-md);padding:var(--s-4);font-family:var(--font-mono);font-size:var(--fs-2);line-height:1.7;height:320px;overflow:auto;white-space:pre-wrap;word-break:break-all}
.log .t{color:var(--muted)}
.table-wrap{overflow:auto;border:1px solid var(--border);border-radius:var(--r-md)}
.table-wrap table{width:100%;border-collapse:collapse;font-size:var(--fs-1)}
.table-wrap th,.table-wrap td{padding:var(--s-1) var(--s-1);border-bottom:1px solid var(--border);text-align:left;white-space:nowrap;line-height:1.5}
.table-wrap th{position:sticky;top:0;background:var(--surface);font-weight:500;font-size:var(--fs-1);color:var(--muted);z-index:var(--z-sticky)}
.table-wrap tr:last-child td{border-bottom:none}
.table-wrap td.num{text-align:right;font-variant-numeric:tabular-nums;font-family:var(--font-mono)}
.table-wrap td.center{text-align:center}
.table-wrap td.mono{font-family:var(--font-mono)}
.hint-inline{font-size:var(--fs-2);font-weight:400;color:var(--muted);line-height:1.7}
.empty{color:var(--muted);font-size:var(--fs-3);padding:var(--s-2) 0}

/* ===== 表单 / 浮层 / 反馈 ===== */
details{margin-top:var(--s-4)}
summary{cursor:pointer;font-size:var(--fs-2);color:var(--info);padding:var(--s-1) 0;list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"+ ";font-family:var(--font-mono);color:var(--muted)}
details[open] summary::before{content:"- "}
.modal-mask{position:fixed;inset:0;background:var(--mask);display:none;align-items:center;justify-content:center;z-index:var(--z-overlay);padding:var(--s-4)}
.modal-mask.show{display:flex}
.modal{background:var(--surface);border:0;border-radius:var(--r-lg);padding:var(--s-6);width:100%;max-width:640px;max-height:90vh;overflow:auto;box-shadow:var(--elev-raised);z-index:var(--z-modal);position:relative}
.modal h3{font-size:var(--fs-5);font-weight:500;margin-bottom:var(--s-4)}
.field{margin-bottom:var(--s-4)}
.field label{display:block;font-size:var(--fs-3);font-weight:500;color:var(--ink-2);margin-bottom:var(--s-2)}
.field input,.field textarea{width:100%;padding:var(--s-2) var(--s-3);border:1px solid var(--border);border-radius:var(--r-sm);font-size:var(--fs-3);font-family:inherit;background:var(--surface);color:var(--ink);min-height:44px;line-height:1.5}
.field textarea{font-family:var(--font-mono);font-size:var(--fs-2);resize:vertical}
.field input:focus,.field textarea:focus{outline:none;border-color:var(--accent);box-shadow:var(--focus)}
.field .hint{font-size:var(--fs-1);color:var(--muted);margin-top:var(--s-1)}
#routingJson{padding:var(--s-6);font-size:var(--fs-3);min-height:280px}
.checks{display:flex;flex-wrap:wrap;gap:var(--s-4)}
.checks label{display:flex;align-items:center;gap:var(--s-2);font-size:var(--fs-3);font-weight:400;color:var(--ink);cursor:pointer}
.checks input[type=checkbox]{width:auto;min-height:0;accent-color:var(--success)}
.modal-foot{display:flex;justify-content:flex-end;gap:var(--s-2);margin-top:var(--s-4)}
.toast{position:fixed;bottom:calc(var(--s-6) + env(safe-area-inset-bottom));left:50%;transform:translateX(-50%);background:var(--surface);color:var(--ink);border:1px solid var(--border);box-shadow:var(--elev-raised);padding:var(--s-3) var(--s-6);border-radius:var(--r-sm);font-size:var(--fs-3);opacity:0;transition:opacity var(--motion-base) var(--ease);z-index:var(--z-toast);pointer-events:none}
.toast.show{opacity:1}

/* ===== 逐板编排（§5）：DOM 顺序与视觉/焦点顺序一致，不用 order ===== */
/* 运行台：Go 额度(1) → 总览(2) → Zen(3) → 日志(4) */
#view-run > .card:nth-child(2){margin-top:var(--s-2)}          /* gap16 + 8 = 24 */
#view-run > .card:nth-child(4){margin-top:var(--s-8);padding:0;overflow:hidden} /* gap16 + 32 = 48 */
#view-run > .card:nth-child(4) h2{padding:var(--s-6) var(--s-6) 0}
#view-run > .card:nth-child(4) .log{border-radius:0;height:320px}
/* 用量：数字带 → 表格带 32px，三表之间 16px */
#liveTps{margin-top:var(--s-2)!important}
#view-usage .card > div[style*="margin-top:18px"]{margin-top:var(--s-4)!important}
#view-usage .card > div[style*="margin-top:18px"]:has(#usageVendors){margin-top:var(--s-8)!important}
/* 上游 / 规则：两卡之间 32px */
#view-upstream > .card:nth-child(2),#view-rules > .card:nth-child(2){margin-top:var(--s-4)}


/* ===== 用量页重构（ui/usage-redesign）：KPI 精简 + Top 5 + 筛选分页 ===== */
#usageTotals{grid-template-columns:repeat(5,1fr)}
#usageTotals .stat:nth-child(4n+1){border-left:1px solid var(--border)}
#usageTotals .stat:nth-child(5n+1){border-left:0}
#usageTotals .stat .value{font-size:var(--fs-6)}
#usageTotals .stat.hero .value{font-size:var(--fs-8);color:var(--accent)}
#usageTotals .stat .sub{font-size:var(--fs-2);color:var(--muted);margin-top:var(--s-1);line-height:1.6}
#view-usage .table-wrap table{font-size:var(--fs-3)}
#view-usage .table-wrap th,#view-usage .table-wrap td{padding:var(--s-2)}
.usage-filter{display:flex;gap:var(--s-2);flex-wrap:wrap;align-items:center;margin:var(--s-4) 0 var(--s-3)}
.usage-filter input[type=text],.usage-filter select{padding:var(--s-2) var(--s-3);border:1px solid var(--border);border-radius:var(--r-sm);font-size:var(--fs-3);font-family:inherit;background:var(--surface);color:var(--ink);min-height:44px}
.usage-filter input[type=text]{flex:1 1 220px;min-width:180px}
.usage-filter label.toggle{display:inline-flex;align-items:center;gap:var(--s-2);font-size:var(--fs-3);color:var(--ink-2);cursor:pointer;min-height:44px}
.usage-filter input[type=checkbox]{accent-color:var(--success);width:18px;height:18px}
.usage-period{display:flex;gap:var(--s-2);flex-wrap:wrap;align-items:center;margin:0 0 var(--s-2)}
.usage-period button{min-height:36px;padding:var(--s-1) var(--s-3)}
.usage-period button.is-active{background:var(--accent);border-color:var(--accent);color:var(--accent-on)}
#usagePeriodNote{margin-bottom:var(--s-3)}
.top5-row{display:grid;grid-template-columns:minmax(140px,240px) 1fr 110px;gap:var(--s-3);align-items:center;padding:var(--s-1) 0;font-size:var(--fs-3)}
.top5-row .m{font-family:var(--font-mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.top5-row .n{text-align:right;font-variant-numeric:tabular-nums;font-family:var(--font-mono)}
.top5-bar{height:8px;background:var(--raised);border-radius:var(--r-md);overflow:hidden}
.top5-bar>i{display:block;height:100%;background:var(--accent)}
.pager{display:flex;gap:var(--s-2);align-items:center;justify-content:flex-end;margin-top:var(--s-3);font-size:var(--fs-3);color:var(--ink-2)}
.pager button{min-height:36px;padding:var(--s-1) var(--s-3)}
tr.detail-row td{background:var(--raised);font-size:var(--fs-2);white-space:normal;line-height:1.7;color:var(--ink-2)}
td .row-toggle{cursor:pointer;color:var(--accent)}

/* ===== 平板 8 栏 / 手机 4 栏（§4.2 / §7） ===== */
@media (max-width:1024px){
  .view.is-active{grid-template-columns:repeat(8,1fr);gap:var(--s-4)}
  .view .card{grid-column:1/-1}
  .quota-summary{grid-template-columns:repeat(8,1fr)}
  #quotaSummary .quota-card:first-child{grid-column:span 4}
  #quotaSummary .quota-card{grid-column:span 2}
  #zenSummary .quota-card{grid-column:span 4}
  #usageTotals{grid-template-columns:repeat(2,1fr)}
  #usageTotals .stat:nth-child(4n+1){border-left:1px solid var(--border)}
  #usageTotals .stat:nth-child(2n+1){border-left:0}
}
@media (max-width:640px){
  .shell{flex-direction:column;align-items:stretch;gap:var(--s-3);padding:var(--s-3) var(--s-4)}
  .header-left{width:100%;flex-direction:column;align-items:stretch;gap:var(--s-2)}
  .header-right{width:100%;justify-content:flex-start;flex-wrap:wrap;gap:var(--s-2)}
  header h1{font-size:var(--fs-4)}
  .tabs{width:100%;min-height:44px;gap:0}
  .tab{flex:1 1 0;justify-content:center;padding:0 var(--s-1);font-size:var(--fs-3)}
  main{padding:var(--s-4) var(--s-4) var(--s-12)}
  .view.is-active{grid-template-columns:repeat(4,1fr);gap:var(--s-3)}
  .view .card{grid-column:1/-1;padding:var(--s-4)}
  .quota-summary{grid-template-columns:1fr;gap:0}
  #quotaSummary .quota-card,#quotaSummary .quota-card:first-child,#zenSummary .quota-card{grid-column:1/-1;border-left:0;border-top:1px solid var(--border)}
  .quota-summary .quota-card:first-child{border-top:0}
  #stats{display:grid;grid-template-columns:repeat(2,1fr);gap:0}
  #stats .stat{flex:none;border-left:1px solid var(--border)}
  #stats .stat:nth-child(2n+1){border-left:0}
  #usageTotals{grid-template-columns:repeat(2,1fr)}
  .table-wrap{max-height:none}
  .quota-text{white-space:normal}
  .log{height:280px}
  #view-run > .card:nth-child(4) h2{padding:var(--s-4) var(--s-4) 0}
  button,.tab,.tag{min-height:44px}
}
@media (prefers-reduced-motion: reduce){
  *,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}
}
</style>
</head>
<body>
<header>
  <div class="shell">
    <div class="header-left">
      <h1><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 3.5 8 8M2 8h6M2 12.5 8 8M8 8h6"/></svg>聚合网关管理端 <span class="ver">v{{VERSION}}</span></h1>
      <nav class="tabs" role="tablist" aria-label="仪表板分组">
        <button type="button" class="tab is-active" id="tab-run" role="tab" aria-selected="true" aria-controls="view-run" data-view="run" tabindex="0">运行台</button>
        <button type="button" class="tab" id="tab-usage" role="tab" aria-selected="false" aria-controls="view-usage" data-view="usage" tabindex="-1">用量</button>
        <button type="button" class="tab" id="tab-upstream" role="tab" aria-selected="false" aria-controls="view-upstream" data-view="upstream" tabindex="-1">上游</button>
        <button type="button" class="tab" id="tab-rules" role="tab" aria-selected="false" aria-controls="view-rules" data-view="rules" tabindex="-1">规则</button>
      </nav>
    </div>
    <div class="header-right">
      <span class="badge" id="statusBadge">运行中</span>
      <button class="sm" onclick="reload()">重载配置</button>
      <button class="sm" onclick="testAll()"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z"/></svg>一键测试</button>
      <button class="sm primary" onclick="openAdd()">+ 添加 Provider</button>
    </div>
  </div>
</header>
<main>
  <section class="view is-active" id="view-run" data-view="run" role="tabpanel" aria-labelledby="tab-run">
  <div class="card">
    <h2>Go 服务端额度 <span class="hint-inline" id="quotaPlan"></span>
      <span><button class="sm" onclick="loadQuota(true)">刷新</button></span>
    </h2>
    <div class="hint-inline" style="margin-bottom:12px">实时读取 <code>GET /zen/go/v1/usage</code>（使用 opencode-go 的 API key），显示服务端 rolling / weekly / monthly 百分比和重置时间。下方「本地按模型累计」仅统计本网关流量，仅供参考。</div>
    <div class="quota-summary" id="quotaSummary"></div>
    <div id="quotaError" class="empty" style="margin-top:10px"></div>
    <details style="margin-top:16px">
      <summary class="hint-inline" style="cursor:pointer">本地按模型累计（参考）</summary>
      <div id="quotaTable" style="margin-top:10px"></div>
    </details>
  </div>

  <div class="card">
    <h2>总览</h2>
    <div class="stats" id="stats"></div>
  </div>

  <div class="card">
    <h2>Zen 余额（服务端） <span class="hint-inline" id="zenSource"></span></h2>
    <div class="hint-inline" style="margin-bottom:12px">读取 OpenCode Console 的真实预付费余额和月度预算（<code>/console/api/billing/status</code>、<code>/console/api/budgets/org</code>）。Go 额度接口只返回订阅窗口，不返回 Zen 余额，所以此项需要配置 Console 会话 Cookie。</div>
    <div class="quota-summary" id="zenSummary"></div>
    <details style="margin-top:14px">
      <summary class="hint-inline" style="cursor:pointer">配置 Console 会话 Cookie</summary>
      <div class="field" style="margin-top:10px">
        <label for="zenCookie">会话 Cookie</label>
        <textarea id="zenCookie" rows="3" placeholder="__Host-console_session=...; auth=..."></textarea>
        <div class="hint">浏览器登录 opencode.ai 后，从 DevTools → Application → Cookies → https://opencode.ai 复制；只保存在本机 gateway_config.json，不会发送给客户端。</div>
      </div>
      <div class="field">
        <label for="zenOrg">org_id（可选，留空自动发现）</label>
        <input id="zenOrg" placeholder="org_...">
      </div>
      <div class="row">
        <button class="sm primary" onclick="saveZen()">保存并刷新</button>
        <button class="sm" onclick="clearZen()">清除 Cookie</button>
      </div>
    </details>
  </div>

  <div class="card">
    <h2>实时日志 <button class="sm" onclick="loadLogs()">刷新</button></h2>
    <div class="log" id="log"></div>
  </div>
  </section>
  <section class="view" id="view-usage" data-view="usage" role="tabpanel" aria-labelledby="tab-usage" hidden>
  <div class="card">
    <h2>Token 用量 <span class="hint-inline" id="usageSince"></span>
      <span>
        <button class="sm" onclick="loadUsage()">刷新</button>
        <button class="sm danger" onclick="resetUsage()">重置</button>
      </span>
    </h2>
    <div class="usage-period" id="usagePeriod" role="group" aria-label="用量时间段">
      <button type="button" class="sm" data-period="today">今日</button>
      <button type="button" class="sm" data-period="week">本周</button>
      <button type="button" class="sm" data-period="month">本月</button>
      <button type="button" class="sm is-active" data-period="all">全部</button>
    </div>
    <div class="hint-inline" id="usagePeriodNote"></div>
    <div class="stats" id="usageTotals"></div>
    <div id="liveTps" class="hint-inline" style="margin-top:10px"></div>
    <div style="margin-top:18px">
      <h2 class="sub-h">用量 Top 5 模型</h2>
      <div id="usageTop5"><div class="empty">暂无用量记录</div></div>
    </div>
    <div style="margin-top:18px">
      <h2 class="sub-h">按厂商</h2>
      <div id="usageVendors"></div>
      <div class="hint-inline" style="margin-top:6px">厂商按模型名推断（claude→Anthropic、glm→智谱、gemini→Google…）；只统计经过网关的流量，OpenCode 其它客户端的用量无法计入 token。</div>
    </div>
    <div style="margin-top:18px">
      <h2 class="sub-h">按 Provider</h2>
      <div id="usageProviders"></div>
    </div>
    <div class="usage-filter" id="usageFilter">
      <input type="text" id="usageSearch" placeholder="搜索模型…" aria-label="搜索模型">
      <select id="usageVendorFilter" aria-label="按厂商筛选"><option value="">全部厂商</option></select>
      <label class="toggle"><input type="checkbox" id="usageHideUnused" checked> 隐藏未使用模型</label>
      <span class="hint-inline" id="usageHiddenNote"></span>
    </div>
    <div style="margin-top:18px">
      <h2 class="sub-h">按模型</h2>
      <div id="usageModels"></div>
      <div class="pager" id="usagePager"></div>
    </div>
  </div>

  </section>
  <section class="view" id="view-upstream" data-view="upstream" role="tabpanel" aria-labelledby="tab-upstream" hidden>
  <div class="card">
    <h2>Providers</h2>
    <div id="providers"></div>
  </div>

  <div class="card">
    <h2>模型能力 <span class="hint-inline">来自 model_meta，可在编辑 Provider 中修改</span></h2>
    <div id="modelTable"></div>
  </div>

  </section>
  <section class="view" id="view-rules" data-view="rules" role="tabpanel" aria-labelledby="tab-rules" hidden>
  <div class="card">
    <h2>智能路由 <span class="hint-inline">智能别名 / 故障转移 / 失败冷却</span>
      <span><button class="sm" onclick="loadRouting()">刷新</button></span>
    </h2>
    <div class="hint-inline" style="margin-bottom:10px">
      <b>智能别名</b>：客户端请求 <code>smart</code> / <code>cheap</code> / <code>vision</code> / <code>coding</code> 时，按候选顺序自动选路，跳过冷却中的模型。<br>
      <b>故障转移</b>：主模型返回 <code>retry_on</code> 中的错误码时，自动尝试候选列表里的下一个模型；全部失败后返回最后一次上游错误。<br>
      <b>冷却</b>：模型失败后 <code>cooldown_seconds</code> 秒内不再优先选它，成功后自动恢复。
    </div>
    <div class="field">
      <label for="routingJson">路由配置（JSON）</label>
      <textarea id="routingJson" rows="12"></textarea>
    </div>
    <div class="row">
      <button class="sm primary" onclick="saveRouting()">保存路由</button>
      <button class="sm" onclick="resetRouting()">恢复默认</button>
    </div>
    <div id="routingHealth" style="margin-top:12px"></div>
  </div>

  <div class="card">
    <h2>客户端接入密钥 <span class="hint-inline">客户端连网关用这个 key；上游 provider key 由网关管理</span></h2>
    <div class="field checks">
      <label><input type="checkbox" id="authEnabled"> 启用鉴权（关闭后任何人可访问）</label>
    </div>
    <div class="field">
      <label for="authKeys">允许的 key（每行一个）</label>
      <textarea id="authKeys" rows="3" placeholder="sk-gw-..."></textarea>
      <div class="hint">OpenAI 客户端：Authorization: Bearer &lt;key&gt; · Anthropic 客户端：x-api-key: &lt;key&gt;</div>
    </div>
    <div class="row">
      <button class="sm primary" onclick="saveAuth()">保存密钥</button>
      <button class="sm" onclick="genKey()">生成随机 key</button>
      <button class="sm" onclick="copyKey()">复制第一个</button>
    </div>
  </div>

  </section>
</main>

<div class="modal-mask" id="modalMask">
  <div class="modal">
    <h3 id="modalTitle">添加 Provider</h3>
    <div class="field"><label for="fName">名称</label><input id="fName" placeholder="opencode-go"></div>
    <div class="field"><label for="fUrl">Base URL（OpenAI）</label><input id="fUrl" placeholder="https://api.example.com/v1"><div class="hint">OpenAI 兼容端点，不带 /chat/completions</div></div>
    <div class="field"><label for="fAnthropicUrl">Anthropic Base URL（可选）</label><input id="fAnthropicUrl" placeholder="https://api.example.com/anthropic/v1"><div class="hint">不带 /messages；留空则不支持该 provider 的 /v1/messages（opencode.ai 可自动推导）</div></div>
    <div class="field"><label for="fKey">API Key（OpenAI）</label><input id="fKey" placeholder="sk-..."></div>
    <div class="field"><label for="fAnthropicKey">Anthropic API Key（可选）</label><input id="fAnthropicKey" placeholder="留空则复用上面的 API Key"><div class="hint">/v1/messages 优先使用这个 key；留空则回落到 OpenAI API Key</div></div>
    <div class="field"><label for="fModels">模型列表（每行一个）</label><textarea id="fModels" rows="5" placeholder="gpt-6-luna&#10;glm-5.3-flash"></textarea></div>
    <div class="field"><label for="fProxyModels">走代理的模型（每行一个，留空则全部直连）</label><textarea id="fProxyModels" rows="3" placeholder="gpt-6-luna"></textarea></div>
    <div class="field"><label for="fHeaders">自定义请求头（JSON，可选）</label><textarea id="fHeaders" rows="2" placeholder='{"x-opencode-session":"{session}"}'></textarea><div class="hint">{session} 会自动替换为会话 ID</div></div>
    <div class="field checks">
      <label><input type="checkbox" id="fNeedsSession"> 需要 session 头（opencode）</label>
      <label><input type="checkbox" id="fUseProxy"> 全部模型走代理</label>
    </div>
    <div class="field"><label for="fModelMap">模型 ID 映射（JSON，可选）</label><textarea id="fModelMap" rows="2" placeholder='{"gpt-6-luna-responses":"gpt-6-luna"}'></textarea></div>
    <div class="field"><label for="fMeta">模型元数据（JSON，可选）</label><textarea id="fMeta" rows="4" placeholder='{"gpt-6-luna":{"context":1050000,"input":922000,"output":128000,"images":true,"tools":true,"reasoning":["low","high"]}}'></textarea><div class="hint">用于管理端展示上下文 / 多模态 / 工具 / 思考强度；编辑 Provider 时会保留已有元数据</div></div>
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
let CFG_REVISION = '';
let STATUS = null;
let editingIndex = -1;
const DEFAULT_ROUTING = {{ROUTING_DEFAULT}};

async function api(path, opts){
  const r = await fetch(path, Object.assign({cache:'no-store'}, opts||{}));
  if(path === '/admin/api/config' && (!opts || !opts.method || opts.method === 'GET')){
    const rev = r.headers.get('X-Config-Revision');
    if(rev) CFG_REVISION = rev;
  }
  const t = await r.text();
  try{ return JSON.parse(t); }catch(e){ return {raw:t}; }
}
function toast(msg){
  const el = document.getElementById('toast');
  el.textContent = msg; el.classList.add('show');
  setTimeout(()=>el.classList.remove('show'), 2200);
}
function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

function fillZenForm(){
  const z = (CFG && CFG.zen_console) || {};
  const cookie = document.getElementById('zenCookie');
  const org = document.getElementById('zenOrg');
  if(cookie) cookie.value = z.session_cookie || '';
  if(org) org.value = z.org_id || '';
}
function fillRoutingForm(){
  const ta = document.getElementById('routingJson');
  if(ta) ta.value = JSON.stringify((CFG && CFG.routing) || {}, null, 2);
}
async function loadRouting(){
  try{
    const d = await api('/admin/api/routing');
    const box = document.getElementById('routingHealth');
    if(!box) return;
    const h = d.health || [];
    box.innerHTML = h.length
      ? '<div class="hint-inline">冷却中：'+h.map(x=>esc(x.key)+'（'+esc(x.status)+'，'+Math.max(0,Math.round(Date.now()/1000 - Number(x.ts)))+'s 前）').join('、')+'</div>'
      : '<div class="hint-inline">当前没有模型处于冷却期。</div>';
  }catch(e){}
}
async function saveRouting(){
  let val;
  try{ val = JSON.parse(document.getElementById('routingJson').value); }
  catch(e){ toast('JSON 格式错误：'+(e.message||e)); return; }
  CFG.routing = val;
  const r = await api('/admin/api/config',{method:'POST',headers:{'Content-Type':'application/json','X-Config-Revision':CFG_REVISION},body:JSON.stringify(CFG)});
  if(!r || r.ok === false){ toast('保存失败：'+((r&&r.error)||'未知错误')); return; }
  await api('/admin/api/reload',{method:'POST'});
  await load();
  await loadRouting();
  toast('路由配置已保存');
}
async function resetRouting(){
  if(!confirm('恢复默认路由配置？')) return;
  CFG.routing = JSON.parse(JSON.stringify(DEFAULT_ROUTING));
  const r = await api('/admin/api/config',{method:'POST',headers:{'Content-Type':'application/json','X-Config-Revision':CFG_REVISION},body:JSON.stringify(CFG)});
  if(!r || r.ok === false){ toast('保存失败：'+((r&&r.error)||'未知错误')); return; }
  await api('/admin/api/reload',{method:'POST'});
  await load();
  await loadRouting();
  toast('已恢复默认路由');
}
async function saveZen(){
  const cookie = document.getElementById('zenCookie').value.trim();
  const org = document.getElementById('zenOrg').value.trim();
  CFG.zen_console = {enabled: !!cookie, session_cookie: cookie, org_id: org};
  const r = await api('/admin/api/config',{method:'POST',headers:{'Content-Type':'application/json','X-Config-Revision':CFG_REVISION},body:JSON.stringify(CFG)});
  if(!r || r.ok === false){ toast('保存失败：'+((r&&r.error)||'未知错误')); return; }
  await api('/admin/api/reload',{method:'POST'});
  await load();
  await loadQuota(true);
  toast('Zen 配置已保存');
}
async function clearZen(){
  CFG.zen_console = {enabled:false, session_cookie:'', org_id:''};
  const r = await api('/admin/api/config',{method:'POST',headers:{'Content-Type':'application/json','X-Config-Revision':CFG_REVISION},body:JSON.stringify(CFG)});
  if(!r || r.ok === false){ toast('清除失败：'+((r&&r.error)||'未知错误')); return; }
  await api('/admin/api/reload',{method:'POST'});
  await load();
  await loadQuota(true);
  toast('已清除 Zen Cookie');
}
async function load(){
  const cfg = await api('/admin/api/config');
  CFG = cfg;
  try{ STATUS = await api('/admin/api/status'); }catch(e){ STATUS = null; }
  fillZenForm();
  fillRoutingForm();
  loadRouting();
  render();
  loadLogs();
  loadUsage();
  loadQuota();
}
function fmtCount(v){ return Number(v||0).toLocaleString('en-US'); }
function fmtRate(v){ return v == null ? '—' : Number(v).toFixed(1)+'%'; }
function fmtTps(v){ return v == null ? '—' : Number(v).toFixed(1)+' /s'; }
function fmtTokens(v){
  if(v == null || v === '') return '—';
  const n = Number(v);
  if(!isFinite(n)) return '—';
  if(Math.abs(n) < 1000000) return Math.round(n).toLocaleString('en-US');
  const units = [
    [1e12, '万亿'], [1e11, '千亿'], [1e10, '百亿'],
    [1e9, '十亿'], [1e8, '亿'], [1e7, '千万'], [1e6, '百万']
  ];
  for(const unit of units){
    if(Math.abs(n) >= unit[0]){
      const text = (n/unit[0]).toFixed(2).replace(/\.00$/,'').replace(/(\.\d)0$/,'$1');
      return text+' '+unit[1];
    }
  }
  return Math.round(n).toLocaleString('en-US');
}
function money(v){
  if(v == null) return '—';
  const n = Number(v);
  if(n === 0) return '$0.00';
  if(n < 0.0001) return '$'+n.toFixed(6);
  if(n < 0.01) return '$'+n.toFixed(4);
  if(n < 1) return '$'+n.toFixed(3);
  return '$'+n.toFixed(2);
}
function renderPrice(p){
  if(!p) return '<span class="empty">未录</span>';
  if(p.unit === 'per_minute') return '<span title="按分钟计费">'+money(p.per_minute)+'/min</span>';
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
        '<td class="mono">'+esc(m)+(mt.free?' 🎁':'')+'</td>'+
        '<td>'+esc(p.name)+'</td>'+
        '<td class="num">'+fmtTokens(mt.context||mt.input)+'</td>'+
        '<td class="num">'+fmtTokens(mt.output)+'</td>'+
        '<td class="center">'+(mt.images===true?'✅':mt.images===false?'❌':'—')+'</td>'+
        '<td class="center">'+(mt.tools===true?'✅':mt.tools===false?'❌':'—')+'</td>'+
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
  const r = await api('/admin/api/config',{method:'POST',headers:{'Content-Type':'application/json','X-Config-Revision':CFG_REVISION},body:JSON.stringify(CFG)});
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
  const lan = (STATUS && STATUS.lan_urls) || [];
  const listenHost = (CFG.listen||{}).host || '127.0.0.1';
  const listenPort = (CFG.listen||{}).port || 8787;
  const localBase = 'http://' + (listenHost==='0.0.0.0'?'127.0.0.1':listenHost) + ':' + listenPort;
  const bases = lan.length ? lan : [localBase];
  const endpointLines = (suffix) => bases.map(b=>esc(b+suffix)).join('<br>');
  document.getElementById('stats').innerHTML = `
    <div class="stat"><div class="label">Providers</div><div class="value">${providers.length}</div></div>
    <div class="stat"><div class="label">模型总数</div><div class="value">${totalModels}</div></div>
    <div class="stat"><div class="label">走代理模型</div><div class="value">${proxyModels} <small>/ ${totalModels}</small></div></div>
    <div class="stat"><div class="label">代理地址</div><div class="value" style="font-size:14px;font-family:var(--mono)">${esc(CFG.proxy||'（直连）')}</div></div>
    <div class="stat"><div class="label">监听</div><div class="value" style="font-size:14px;font-family:var(--mono)">${esc(listenHost)}:${esc(listenPort)}${lan.length?' <small>局域网</small>':' <small>仅本机</small>'}</div></div>
    <div class="stat"><div class="label">OpenAI 端点</div><div class="value" style="font-size:12px;font-family:var(--mono);word-break:break-all">${endpointLines('/v1')}</div></div>
    <div class="stat"><div class="label">Anthropic 端点</div><div class="value" style="font-size:12px;font-family:var(--mono);word-break:break-all">${endpointLines('')}</div></div>
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
        tip.push(`多模态 ${mt.images===true?'✅':mt.images===false?'❌':'—'}   工具 ${mt.tools===true?'✅':mt.tools===false?'❌':'—'}`);
        if(mt.reasoning && mt.reasoning.length) tip.push(`思考 ${mt.reasoning.join(' / ')}`);
        if(mt.pricing){
          if(mt.pricing.unit==='per_minute') tip.push(`价格 $${mt.pricing.per_minute}/分钟`);
          else tip.push(`价格 输入 $${mt.pricing.input}/1M · 输出 $${mt.pricing.output}/1M`);
        }
        if(mt.free) tip.push('免费模型');
      }
      return `<span class="tag${px.has(m)?' proxy':''}" data-model="${esc(m)}" onclick="testModel(this)" title="${esc(tip.join('\n'))}">${esc(m)}${mt&&mt.free?' 🎁':''}${px.has(m)?' 🌐':''}</span>`;
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
  const isVendor = type === 'vendor';
  const columns = isModel
    ? '<th>模型</th><th>厂商</th><th>Provider</th>'
    : isVendor
      ? '<th>厂商</th>'
      : '<th>Provider</th>';
  const rows = items.map(x=>{
    const identity = isModel
      ? '<td class="mono">'+esc(x.model||'')+'</td><td>'+esc(x.vendor||'')+'</td><td>'+esc(x.provider||'')+'</td>'
      : isVendor
        ? '<td>'+esc(x.vendor||'')+'</td>'
        : '<td>'+esc(x.name||'')+'</td>';
    return '<tr>'+identity+
      '<td class="num">'+fmtCount(x.requests)+'</td>'+ 
      '<td class="num">'+fmtTokens(x.input_tokens)+'</td>'+ 
      '<td class="num">'+fmtTokens(x.output_tokens)+'</td>'+ 
      '<td class="num">'+fmtTokens(x.total_tokens)+'</td>'+ 
      '<td class="num">'+fmtTokens(x.cached_tokens)+'</td>'+ 
      '<td class="num">'+fmtRate(x.cache_hit_rate)+'</td>'+ 
      '<td class="num">'+fmtTokens(x.reasoning_tokens)+'</td>'+ 
      '<td class="num">'+fmtTps(x.avg_tps)+'</td>'+
      '<td class="num">$'+(Number(x.cost||0).toFixed(6))+'</td>'+ 
      '<td class="num">$'+(Number(x.list_cost||0).toFixed(6))+'</td>'+ 
      '<td class="num">'+fmtCount(x.errors)+'</td>'+  
      '<td class="num">'+fmtCount(x.usage_missing)+'</td>'+ 
      '<td class="num">'+(x.requests?Math.round(Number(x.latency_ms||0)/Number(x.requests||1))+' ms':'—')+'</td>'+ 
      '<td>'+esc(x.last_used||'—')+'</td></tr>';
  }).join('');
  return '<div class="table-wrap"><table><thead><tr>'+columns+
    '<th>请求</th><th>输入 Token</th><th>输出 Token</th><th>总 Token</th>'+ 
    '<th>缓存读</th><th>缓存命中率</th><th>推理 Token</th><th>平均 TPS</th><th>实扣（$）</th><th>标价估算（$）</th><th>错误</th><th>无用量</th><th>平均延迟</th><th>最近调用</th>'+ 
    '</tr></thead><tbody>'+rows+'</tbody></table></div>';
}
async function loadUsage(){
  try{
    const d = await api('/admin/api/usage');
    const t = d.totals || {};
    const live = d.live || {};
    const goStat = (d.providers||[]).find(p=>p.name==='opencode-go') || {};
    document.getElementById('usageSince').textContent = d.since ? ('统计起始 '+d.since+' · 更新 '+(d.updated||'—')) : '';
    document.getElementById('usageTotals').innerHTML = `
      <div class="stat"><div class="label">请求数</div><div class="value">${fmtCount(t.requests)}</div></div>
      <div class="stat"><div class="label">Go 本地 Token</div><div class="value">${fmtTokens(goStat.total_tokens)}</div></div>
      <div class="stat"><div class="label">实时 TPS</div><div class="value">${fmtTps(live.output_tps)} <small>输出 / ${live.window_seconds||60}s</small></div></div>
      <div class="stat"><div class="label">输入 Token</div><div class="value">${fmtTokens(t.input_tokens)}</div></div>
      <div class="stat"><div class="label">输出 Token</div><div class="value">${fmtTokens(t.output_tokens)}</div></div>
      <div class="stat"><div class="label">总 Token</div><div class="value">${fmtTokens(t.total_tokens)}</div></div>
      <div class="stat"><div class="label">缓存读 Token</div><div class="value">${fmtTokens(t.cached_tokens)}</div></div>
      <div class="stat"><div class="label">缓存命中率</div><div class="value">${fmtRate(t.cache_hit_rate)}</div></div>
      <div class="stat"><div class="label">推理 Token</div><div class="value">${fmtTokens(t.reasoning_tokens)}</div></div>
      <div class="stat"><div class="label">上游实扣</div><div class="value">$${Number(t.cost||0).toFixed(6)}</div></div>
      <div class="stat"><div class="label">按模型价估算</div><div class="value">$${Number(t.list_cost||0).toFixed(6)}</div></div>
      <div class="stat"><div class="label">用量缺失请求</div><div class="value">${fmtCount(t.usage_missing)}</div></div>`;
    const liveRows = (live.by_model||[]).map(x=>esc(x.model)+' '+fmtTps(x.output_tps)).join(' · ');
    document.getElementById('liveTps').innerHTML = Number(live.requests||0) > 0
      ? '近 '+(live.span_seconds||live.window_seconds||60)+' 秒：输出 '+fmtTps(live.output_tps)
        +' · 输入 '+fmtTps(live.input_tps)+' · 合计 '+fmtTps(live.total_tps)
        +'（'+fmtCount(live.requests)+' 个请求）'
        + (liveRows ? '<br>按模型：'+liveRows : '')
      : '近 '+(live.window_seconds||60)+' 秒暂无 agent 请求。';
    document.getElementById('usageVendors').innerHTML = renderUsageTable(d.vendors||[], 'vendor');
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
function quotaBar(used, limit, percent){
  const pct = Math.max(0, Math.min(100, Number(percent||0)));
  let cls = 'progress';
  if(percent >= 90) cls += ' danger'; else if(percent >= 70) cls += ' warn';
  const text = (limit == null) ? '∞' : (money(used)+' / '+money(limit)+' · '+Number(percent||0).toFixed(1)+'%');
  return '<div class="quota-line"><div class="'+cls+'" style="flex:1"><i style="width:'+pct+'%"></i></div><span class="quota-text">'+text+'</span></div>';
}
function fmtReset(s){
  if(!s) return '—';
  const d = new Date(s);
  if(isNaN(d.getTime())) return s;
  return d.toLocaleString('zh-CN',{hour12:false});
}
function fmtHours(h){
  h = Number(h||0);
  if(h >= 48) return (h/24).toFixed(1)+' 天';
  if(h >= 1) return h.toFixed(1)+' 小时';
  return Math.max(1, Math.round(h*60))+' 分钟';
}
function serverQuotaCard(label, data, eta){
  data = data || {};
  const pct = Math.max(0, Math.min(100, Number(data.percent||0)));
  let cls = 'progress';
  if(data.percent >= 90) cls += ' danger'; else if(data.percent >= 70) cls += ' warn';
  const status = (data.status && data.status !== 'ok') ? data.status : '';
  let etaText = '正在采样当前速度…';
  if(eta){
    if(eta.status === 'exhausted') etaText = '已用完';
    else if(eta.status === 'flat') etaText = '用量未增长，暂无预计';
    else if(eta.status === 'ok'){
      const speed = eta.basis === 'recent' ? '按近期速度' : '按本窗口平均速度';
      if(eta.before_reset === false){
        etaText = '按当前速度，重置前不会用完（'+speed+'）';
      }else{
        etaText = '预计 '+fmtHours(eta.hours)+' 后用完 · '+esc(eta.at||'')+'（'+speed+'）';
      }
    }
  }
  const rateText = (eta && eta.rate_per_hour > 0)
    ? ' · +'+Number(eta.rate_per_hour).toFixed(2)+'%/h' : '';
  return '<div class="quota-card"><div class="label"><span>'+esc(label)+'</span><span>'+esc(status)+'</span></div>'+
    '<div class="value">'+Number(data.percent||0)+'%</div>'+
    '<div class="'+cls+'"><i style="width:'+pct+'%"></i></div>'+
    '<div class="hint-inline" style="margin-top:6px">重置：'+esc(fmtReset(data.resetsAt))+'</div>'+
    '<div class="hint-inline" style="margin-top:3px">'+esc(etaText)+rateText+'</div></div>';
}
function fmtUsd(v){ return v == null ? '—' : '$'+Number(v).toFixed(2); }
function zenCard(z){
  if(!z || !z.configured){
    return '<div class="empty" style="grid-column:1/-1">未启用 Zen 余额监控。在 <code>gateway_config.json</code> 添加 <code>zen_console.enabled=true</code> 并填入 Console 会话 Cookie 后即可显示真实余额。</div>';
  }
  if(!z.ok){
    return '<div class="empty" style="grid-column:1/-1">Zen 余额读取失败：'+esc(z.error||'未知错误')+
      '<br>如果提示 401/403，通常是 Console 会话过期：重新登录 opencode.ai，复制新的 Cookie 更新 <code>zen_console.session_cookie</code>。</div>';
  }
  const spent = z.monthly_spent_usd, limit = z.monthly_limit_usd;
  const pct = (spent != null && limit) ? Math.max(0, Math.min(100, spent/limit*100)) : null;
  let budget = '';
  if(limit != null){
    let cls = 'progress';
    if(pct >= 90) cls += ' danger'; else if(pct >= 70) cls += ' warn';
    budget = '<div class="quota-card"><div class="label"><span>本月支出</span><span>'+fmtUsd(spent)+' / '+fmtUsd(limit)+'</span></div>'+
      '<div class="value">'+(pct==null?'—':pct.toFixed(1)+'%')+'</div>'+
      '<div class="'+cls+'"><i style="width:'+(pct||0)+'%"></i></div>'+
      '<div class="hint-inline" style="margin-top:6px">重置：'+esc(fmtReset(z.resets_at))+'</div></div>';
  }
  return '<div class="quota-card"><div class="label"><span>Zen 余额</span><span>'+esc(z.mode||z.billing_mode||'')+'</span></div>'+
    '<div class="value">'+fmtUsd(z.balance_usd)+'</div>'+
    '<div class="hint-inline" style="margin-top:6px">可用：'+fmtUsd(z.available_usd)+' · 更新：'+esc((z.fetched_at||'').replace('T',' '))+'</div></div>'+budget;
}
async function loadQuota(force){
  try{
    const d = await api('/admin/api/quota'+(force?'?refresh=1':''));
    const s = d.usage || {};
    document.getElementById('quotaPlan').textContent =
      (d.plan||'Go') + (d.fetched_at ? ' · '+d.fetched_at.replace('T',' ') : '');
    const eta = d.eta || {};
    document.getElementById('quotaSummary').innerHTML =
      serverQuotaCard('5 小时滚动', s.rolling, eta.rolling) +
      serverQuotaCard('本周（UTC）', s.weekly, eta.weekly) +
      serverQuotaCard('本月', s.monthly, eta.monthly);
    document.getElementById('quotaError').textContent = d.ok ? '' :
      ('服务端额度读取失败：'+(d.error||'未知错误')+'（下方为本地参考）');
    document.getElementById('zenSummary').innerHTML = zenCard(d.zen);

    const local = d.local || {};
    const rows = (local.models||[]).map(x=>{
      if(x.unlimited){
        return '<tr><td class="mono">'+esc(x.model)+'</td><td>'+esc(x.provider)+'</td>'+
          '<td colspan="3" class="center">∞ 无限额度</td></tr>';
      }
      return '<tr><td class="mono">'+esc(x.model)+'</td><td>'+esc(x.provider)+'</td>'+
        '<td class="quota-cell">'+quotaBar(x.five_hour.used,x.five_hour.limit,x.five_hour.percent)+'</td>'+
        '<td class="quota-cell">'+quotaBar(x.week.used,x.week.limit,x.week.percent)+'</td>'+
        '<td class="quota-cell">'+quotaBar(x.month.used,x.month.limit,x.month.percent)+'</td></tr>';
    }).join('');
    document.getElementById('quotaTable').innerHTML = rows
      ? '<div class="table-wrap"><table><thead><tr><th>模型</th><th>Provider</th><th>5 小时</th><th>近 7 天</th><th>本月</th></tr></thead><tbody>'+rows+'</tbody></table></div>'
      : '<div class="empty">暂无本地额度数据。发起一次 OpenCode Go 请求后会自动累计。</div>';
  }catch(e){
    document.getElementById('quotaSummary').innerHTML = '<div class="empty">读取额度失败：'+esc(e.message||e)+'</div>';
  }
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
  const r = await api('/admin/api/config',{method:'POST',headers:{'Content-Type':'application/json','X-Config-Revision':CFG_REVISION},body:JSON.stringify(CFG)});
  if(!r.ok){ toast('保存失败：'+(r.error||'未知错误')); await load(); return; }
  closeModal(); await load();
  toast('已保存');
}
async function delProvider(i){
  if(CFG.providers.length<=1){ toast('至少保留一个 Provider'); return; }
  if(!confirm('确定删除该 Provider？')) return;
  CFG.providers.splice(i,1);
  const r = await api('/admin/api/config',{method:'POST',headers:{'Content-Type':'application/json','X-Config-Revision':CFG_REVISION},body:JSON.stringify(CFG)});
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
setInterval(loadQuota, 60000);
setInterval(function(){ window.loadUsage(); }, 5000); /* 经 window 分发：用量页第 4 块覆盖了 loadUsage（时间段切换 + 5 个主数字），直接传函数引用会绕开覆盖，每 5 秒把 KPI 打回旧版累计视图 */
</script>
<script>
(function(){
  'use strict';
  /* ---- 1) tab：只改 display/hidden/aria，绝不摘 DOM 节点 ---- */
  var ORDER = ['run','usage','upstream','rules'];
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.tabs [role="tab"]'));
  var panels = Array.prototype.slice.call(document.querySelectorAll('.view'));
  var list = document.querySelector('.tabs');
  function fromHash(){
    var h = String(location.hash || '').replace(/^#/, '');
    return ORDER.indexOf(h) >= 0 ? h : 'run';
  }
  function activate(name, moveFocus){
    if (ORDER.indexOf(name) < 0) name = 'run';
    var idx = ORDER.indexOf(name);
    tabs.forEach(function(t){
      var on = t.getAttribute('data-view') === name;
      t.classList.toggle('is-active', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
    });
    panels.forEach(function(p){
      var on = p.getAttribute('data-view') === name;
      p.classList.toggle('is-active', on);
      if (on) p.removeAttribute('hidden'); else p.setAttribute('hidden', '');
    });
    if (location.hash !== '#' + name){
      try { history.replaceState(null, '', '#' + name); } catch (e) {}
    }
    if (moveFocus) tabs[idx].focus();
  }
  tabs.forEach(function(t){
    t.addEventListener('click', function(){ activate(t.getAttribute('data-view'), false); });
  });
  if (list) list.addEventListener('keydown', function(e){
    var i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    var n = -1;
    switch (e.key){
      case 'ArrowRight': case 'Right': n = (i + 1) % tabs.length; break;
      case 'ArrowLeft':  case 'Left':  n = (i - 1 + tabs.length) % tabs.length; break;
      case 'Home': n = 0; break;
      case 'End':  n = tabs.length - 1; break;
      default: return;
    }
    e.preventDefault();
    activate(ORDER[n], true);
  });
  window.addEventListener('hashchange', function(){ activate(fromHash(), false); });
  activate(fromHash(), false);

  /* ---- 2) emoji 文本映射：只替换文本节点与 title 属性 ---- */
  /* 冻结 <script> 动态注入的字符；用 \u 转义写，避免任何字面 emoji 进入交付物。
     alert('...') 是浏览器原生对话框、不在 DOM，登记为已知残留，不覆盖 window.alert。 */
  var RE = /(\uD83C\uDF81|\uD83C\uDF10|\u2705|\u274C|\u23F3|\u26A0)\uFE0F?/g;
  var MAP = {
    '\uD83C\uDF81': '\u00B7\u514D',        /* 礼物 -> ·免 */
    '\uD83C\uDF10': '\u00B7\u4EE3\u7406',  /* 地球 -> ·代理 */
    '\u2705': '\u662F',                    /* 勾   -> 是 */
    '\u274C': '\u5426',                    /* 叉   -> 否 */
    '\u23F3': '\u2026',                    /* 沙漏 -> … */
    '\u26A0': '!'                          /* 警告 -> ! */
  };
  function scrub(s){ return s.replace(RE, function(m, c){ return MAP[c] || m; }); }
  var busy = false;
  function scrubNode(n){
    if (n.nodeType === 3){
      var s = n.nodeValue, o = scrub(s);
      if (o !== s) n.nodeValue = o;       /* 只在真变了才写，guard 防自触 */
      return;
    }
    if (n.nodeType !== 1) return;
    if (n.nodeName === 'SCRIPT' || n.nodeName === 'STYLE') return;
    if (n.hasAttribute && n.hasAttribute('title')){
      var v = n.getAttribute('title'), t = scrub(v);
      if (t !== v) n.setAttribute('title', t);
    }
    var kids = n.childNodes;
    for (var i = 0; i < kids.length; i++) scrubNode(kids[i]);
  }
  scrubNode(document.body);
  var obs = new MutationObserver(function(records){
    if (busy) return;
    busy = true;
    try {
      records.forEach(function(r){
        if (r.type === 'childList'){
          for (var i = 0; i < r.addedNodes.length; i++) scrubNode(r.addedNodes[i]);
        } else if (r.type === 'attributes'){
          scrubNode(r.target);
        }
      });
    } finally { busy = false; }
  });
  obs.observe(document.body, {childList:true, subtree:true, attributes:true, attributeFilter:['title']});
})();
</script>
<script>
/* 用量页重构（ui/usage-redesign）+ 时间段切换。
   本块覆盖 window.loadUsage 与 window.renderUsageTable（仅按模型表），
   新增今日 / 本周 / 本月 / 全部切换（默认全部），KPI、Top 5、厂商 /
   Provider / 模型表都跟随时间段；搜索、厂商筛选、隐藏未使用、分页状态
   在切换与 5 秒自动刷新时保留。另：第 2 块的 setInterval(loadUsage, 5000)
   已改为经 window.loadUsage 分发，否则定时器会绕开本块的覆盖，
   每 5 秒把用量页打回旧版累计视图。其它 Tab、后端、/admin/api/* 均不动。 */
(function(){
  'use strict';
  var _origRenderUsageTable = window.renderUsageTable;
  var PAGE_SIZE = 10;
  var state = { models: [], page: 1, search: '', vendor: '', hideUnused: true, expanded: {}, period: 'all' };
  var listenersBound = false;

  function filteredModels(){
    var q = state.search.trim().toLowerCase();
    return state.models.filter(function(x){
      if (state.hideUnused && Number(x.requests || 0) === 0) return false;
      if (state.vendor && String(x.vendor || '') !== state.vendor) return false;
      if (q && String(x.model || '').toLowerCase().indexOf(q) === -1) return false;
      return true;
    });
  }
  function hiddenCount(){
    return state.models.filter(function(x){ return Number(x.requests || 0) === 0; }).length;
  }
  function detailHtml(x){
    var avgLatency = x.requests ? Math.round(Number(x.latency_ms || 0) / Number(x.requests || 1)) + ' ms' : '—';
    return '厂商 ' + esc(x.vendor || '—') + ' · Provider ' + esc(x.provider || '—')
      + ' · 总 Token ' + fmtTokens(x.total_tokens) + ' · 缓存读 ' + fmtTokens(x.cached_tokens)
      + ' · 推理 Token ' + fmtTokens(x.reasoning_tokens) + ' · 平均 TPS ' + fmtTps(x.avg_tps)
      + ' · 标价估算 $' + Number(x.list_cost || 0).toFixed(6)
      + ' · 错误 ' + fmtCount(x.errors) + ' · 无用量 ' + fmtCount(x.usage_missing)
      + ' · 平均延迟 ' + avgLatency + ' · 最近调用 ' + esc(x.last_used || '—');
  }
  function renderModelTable(items){
    state.models = items || [];
    var list = filteredModels();
    var pages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
    if (state.page > pages) state.page = pages;
    if (state.page < 1) state.page = 1;
    var start = (state.page - 1) * PAGE_SIZE;
    var pageItems = list.slice(start, start + PAGE_SIZE);
    var note = document.getElementById('usageHiddenNote');
    if (note) {
      var hc = hiddenCount();
      note.textContent = state.hideUnused && hc > 0
        ? '已隐藏 ' + hc + ' 个未使用模型'
        : (list.length + ' 个模型');
    }
    var pager = document.getElementById('usagePager');
    if (pager) {
      pager.innerHTML = '<button type="button" class="sm" id="usagePrev"' + (state.page <= 1 ? ' disabled' : '') + '>上一页</button>'
        + '<span>第 ' + state.page + ' / ' + pages + ' 页 · 共 ' + list.length + ' 个模型</span>'
        + '<button type="button" class="sm" id="usageNext"' + (state.page >= pages ? ' disabled' : '') + '>下一页</button>';
      var prev = document.getElementById('usagePrev');
      var next = document.getElementById('usageNext');
      if (prev) prev.onclick = function(){ if (state.page > 1) { state.page--; refreshModelTable(); } };
      if (next) next.onclick = function(){ if (state.page < pages) { state.page++; refreshModelTable(); } };
    }
    if (!pageItems.length) return '<div class="empty">暂无用量记录</div>';
    var rows = pageItems.map(function(x){
      var key = String(x.model || '');
      var main = '<tr><td class="mono"><span class="row-toggle" data-model="' + esc(key) + '">' + esc(key) + '</span></td>'
        + '<td class="num">' + fmtCount(x.requests) + '</td>'
        + '<td class="num">' + fmtTokens(x.input_tokens) + '</td>'
        + '<td class="num">' + fmtTokens(x.output_tokens) + '</td>'
        + '<td class="num">' + fmtRate(x.cache_hit_rate) + '</td>'
        + '<td class="num">$' + Number(x.cost || 0).toFixed(6) + '</td></tr>';
      if (state.expanded[key]) {
        main += '<tr class="detail-row"><td colspan="6">' + detailHtml(x) + '</td></tr>';
      }
      return main;
    }).join('');
    return '<div class="table-wrap"><table><thead><tr>'
      + '<th>模型</th><th>请求</th><th>输入 Token</th><th>输出 Token</th><th>缓存命中率</th><th>实扣（$）</th>'
      + '</tr></thead><tbody>' + rows + '</tbody></table></div>';
  }
  function renderTop5(models){
    var el = document.getElementById('usageTop5');
    if (!el) return;
    var top = (models || []).slice().sort(function(a, b){
      return Number(b.total_tokens || 0) - Number(a.total_tokens || 0);
    }).slice(0, 5);
    if (!top.length) { el.innerHTML = '<div class="empty">暂无用量记录</div>'; return; }
    var max = Math.max.apply(null, top.map(function(x){ return Number(x.total_tokens || 0); })) || 1;
    el.innerHTML = top.map(function(x){
      var pct = Math.max(2, Math.round(Number(x.total_tokens || 0) / max * 100));
      return '<div class="top5-row"><span class="m">' + esc(x.model || '') + '</span>'
        + '<span class="top5-bar"><i style="width:' + pct + '%"></i></span>'
        + '<span class="n">' + fmtTokens(x.total_tokens) + '</span></div>';
    }).join('');
  }
  function refreshModelTable(){
    var wrap = document.getElementById('usageModels');
    if (wrap) wrap.innerHTML = renderModelTable(state.models);
  }
  function updatePeriodButtons(){
    var btns = document.querySelectorAll('#usagePeriod button[data-period]');
    Array.prototype.forEach.call(btns, function(btn){
      if (btn.getAttribute('data-period') === state.period) btn.classList.add('is-active');
      else btn.classList.remove('is-active');
    });
  }
  function setPeriod(p){
    if (['today', 'week', 'month', 'all'].indexOf(p) === -1) p = 'all';
    if (state.period === p) return;
    state.period = p;
    state.page = 1;
    updatePeriodButtons();
    window.loadUsage();
  }
  function bindFilters(){
    if (listenersBound) return;
    listenersBound = true;
    var periodBtns = document.querySelectorAll('#usagePeriod button[data-period]');
    Array.prototype.forEach.call(periodBtns, function(btn){
      btn.addEventListener('click', function(){ setPeriod(btn.getAttribute('data-period')); });
    });
    var search = document.getElementById('usageSearch');
    var vendorSel = document.getElementById('usageVendorFilter');
    var hideBox = document.getElementById('usageHideUnused');
    if (search) search.addEventListener('input', function(){ state.search = search.value; state.page = 1; refreshModelTable(); });
    if (vendorSel) vendorSel.addEventListener('change', function(){ state.vendor = vendorSel.value; state.page = 1; refreshModelTable(); });
    if (hideBox) hideBox.addEventListener('change', function(){ state.hideUnused = hideBox.checked; state.page = 1; refreshModelTable(); });
    var wrap = document.getElementById('usageModels');
    if (wrap) wrap.addEventListener('click', function(e){
      var t = e.target && e.target.closest ? e.target.closest('.row-toggle') : null;
      if (!t) return;
      var key = t.getAttribute('data-model');
      if (state.expanded[key]) delete state.expanded[key]; else state.expanded[key] = true;
      refreshModelTable();
    });
  }
  function fillVendorOptions(models){
    var sel = document.getElementById('usageVendorFilter');
    if (!sel) return;
    var seen = {};
    (models || []).forEach(function(x){ if (x.vendor) seen[x.vendor] = true; });
    var cur = sel.value;
    sel.innerHTML = '<option value="">全部厂商</option>' + Object.keys(seen).sort().map(function(v){
      return '<option value="' + esc(v) + '">' + esc(v) + '</option>';
    }).join('');
    sel.value = cur;
    if (cur && sel.value !== cur) { state.vendor = ''; sel.value = ''; }
  }

  window.renderUsageTable = function(items, type){
    if (type !== 'model') return _origRenderUsageTable(items, type);
    bindFilters();
    renderTop5(items);
    return renderModelTable(items);
  };

  window.loadUsage = async function(){
    try {
      var d = await api('/admin/api/usage?period=' + state.period);
      var t = d.totals || {};
      var live = d.live || {};
      var goStat = (d.providers || []).find(function(p){ return p.name === 'opencode-go'; }) || {};
      document.getElementById('usageSince').textContent = d.since ? ('统计起始 ' + d.since + ' · 更新 ' + (d.updated || '—')) : '';
      var periodNote = document.getElementById('usagePeriodNote');
      if (periodNote) {
        var range = (d.period_start && d.period_end) ? ('（' + d.period_start + ' ~ ' + d.period_end + '）') : '';
        periodNote.textContent = (d.period_note || '') + (range ? ' ' + range : '');
      }
      /* 12 个 KPI → 5 个主数字（实时 TPS 提回主 KPI）；其余 7 个收进小字，数值与文案与原版一致 */
      document.getElementById('usageTotals').innerHTML =
        '<div class="stat"><div class="label">请求数</div><div class="value">' + fmtCount(t.requests) + '</div>'
        + '<div class="sub">Go 本地 Token ' + fmtTokens(goStat.total_tokens) + ' · 用量缺失请求 ' + fmtCount(t.usage_missing) + '</div></div>'
        + '<div class="stat hero"><div class="label">总 Token</div><div class="value">' + fmtTokens(t.total_tokens) + '</div>'
        + '<div class="sub">输入 ' + fmtTokens(t.input_tokens) + ' · 输出 ' + fmtTokens(t.output_tokens) + ' · 推理 ' + fmtTokens(t.reasoning_tokens) + '</div></div>'
        + '<div class="stat"><div class="label">实时 TPS</div><div class="value">' + fmtTps(live.output_tps) + ' <small>输出 / ' + (live.window_seconds||60) + 's</small></div>'
        + '<div class="sub">输入 ' + fmtTps(live.input_tps) + ' · 合计 ' + fmtTps(live.total_tps) + '</div></div>'
        + '<div class="stat"><div class="label">缓存命中率</div><div class="value">' + fmtRate(t.cache_hit_rate) + '</div>'
        + '<div class="sub">缓存读 Token ' + fmtTokens(t.cached_tokens) + '</div></div>'
        + '<div class="stat"><div class="label">实扣</div><div class="value">$' + Number(t.cost || 0).toFixed(6) + '</div>'
        + '<div class="sub">按模型价估算 $' + Number(t.list_cost || 0).toFixed(6) + '</div></div>';
      var liveRows = (live.by_model || []).map(function(x){ return esc(x.model) + ' ' + fmtTps(x.output_tps); }).join(' · ');
      document.getElementById('liveTps').innerHTML = Number(live.requests || 0) > 0
        ? '近 ' + (live.span_seconds || live.window_seconds || 60) + ' 秒：输出 ' + fmtTps(live.output_tps)
          + ' · 输入 ' + fmtTps(live.input_tps) + ' · 合计 ' + fmtTps(live.total_tps)
          + '（' + fmtCount(live.requests) + ' 个请求）'
          + (liveRows ? '<br>按模型：' + liveRows : '')
        : '近 ' + (live.window_seconds || 60) + ' 秒暂无 agent 请求。';
      document.getElementById('usageVendors').innerHTML = _origRenderUsageTable(d.vendors || [], 'vendor');
      document.getElementById('usageProviders').innerHTML = _origRenderUsageTable(d.providers || [], 'provider');
      fillVendorOptions(d.models || []);
      renderTop5(d.models || []);
      document.getElementById('usageModels').innerHTML = window.renderUsageTable(d.models || [], 'model');
    } catch (e) {
      document.getElementById('usageTotals').innerHTML = '<div class="empty">读取用量失败：' + esc(e.message || e) + '</div>';
    }
  };

  bindFilters();
  loadUsage();
})();
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


# 智能路由：候选链 + 失败冷却
DEFAULT_RETRY_ON = (400, 402, 403, 404, 408, 429, 500, 502, 503, 504, 529)
HEALTH_LOCK = threading.Lock()
HEALTH = {}


def routing_config():
    return CFG.get("routing") or {}


def routing_chain(model_id, rconf=None):
    """把请求模型展开成候选模型链（智能别名 / 故障转移）。"""
    if not isinstance(model_id, str) or not model_id:
        return []
    rconf = rconf if rconf is not None else routing_config()
    smart = rconf.get("smart_aliases") or {}
    fallbacks = rconf.get("fallbacks") or {}
    if isinstance(smart.get(model_id), list):
        chain = [str(x) for x in smart[model_id]]
    elif isinstance(fallbacks.get(model_id), list):
        chain = [str(x) for x in fallbacks[model_id]]
    else:
        chain = [model_id]
    out = []
    for item in chain:
        if item and item not in out:
            out.append(item)
    return out


def mark_health(key, status, retry):
    """记录/清除模型健康状态（失败冷却）。"""
    with HEALTH_LOCK:
        if retry and status:
            old = HEALTH.get(key) or {}
            HEALTH[key] = {
                "ts": time.time(),
                "status": int(status),
                "count": int(old.get("count", 0)) + 1,
            }
        else:
            HEALTH.pop(key, None)


def health_state(key, cooldown):
    """返回仍在冷却期的健康记录，否则 None。"""
    with HEALTH_LOCK:
        state = HEALTH.get(key)
    if not state:
        return None
    if cooldown and time.time() - state["ts"] < cooldown:
        return state
    return None


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


def normalize_max_tokens(j, upstream_path):
    """按上游协议纠正输出上限字段名。

    Chat Completions 只认 max_tokens/max_completion_tokens；
    Responses 只认 max_output_tokens。客户端混用时在这里互转。
    """
    if upstream_path == "/chat/completions":
        if "max_output_tokens" in j:
            value = j.pop("max_output_tokens")
            if "max_tokens" not in j and "max_completion_tokens" not in j:
                j["max_tokens"] = value
    elif upstream_path == "/responses":
        if "max_tokens" in j:
            value = j.pop("max_tokens")
            if "max_output_tokens" not in j:
                j["max_output_tokens"] = value
        if "max_completion_tokens" in j:
            value = j.pop("max_completion_tokens")
            if "max_output_tokens" not in j:
                j["max_output_tokens"] = value


def normalize_max_tokens_body(body, upstream_path, provider):
    if not body:
        return body
    try:
        j = json.loads(body)
    except Exception:
        return body
    if not isinstance(j, dict):
        return body
    normalize_max_tokens(j, upstream_path)
    clamp_output_tokens(j, provider)
    return json.dumps(j, ensure_ascii=False).encode("utf-8")


THINK_OFF_VALUES = {"none", "off", "disabled", "false", "0", "no"}


def _supported_efforts(provider, model):
    """读取 model_meta 里声明的 reasoning 档位列表。"""
    meta = (provider.get("model_meta") or {}).get(model) or {}
    efforts = meta.get("reasoning")
    if isinstance(efforts, list) and efforts:
        return [str(x) for x in efforts]
    return None


def _fix_effort(value, supported, model):
    if not isinstance(value, str):
        return value
    low = value.strip().lower()
    if supported:
        allowed = {x.lower() for x in supported}
        if low in allowed:
            return value
        if low in THINK_OFF_VALUES:
            # 模型不支持关闭思考：降到最低档，而不是让上游 400
            log(f"reasoning_effort {value!r} -> {supported[0]!r} "
                f"(model {model} cannot disable thinking)")
            return supported[0]
        log(f"drop unsupported reasoning_effort {value!r} (model {model}, "
            f"supported={supported})")
        return None
    if low in THINK_OFF_VALUES:
        return None
    return value


def normalize_reasoning_effort(j, provider, model):
    """按 model_meta 修正 reasoning_effort，避免上游 400。"""
    supported = _supported_efforts(provider, model)
    if "reasoning_effort" in j:
        value = _fix_effort(j.get("reasoning_effort"), supported, model)
        if value is None:
            j.pop("reasoning_effort", None)
        else:
            j["reasoning_effort"] = value
    reasoning = j.get("reasoning")
    if isinstance(reasoning, dict) and "effort" in reasoning:
        value = _fix_effort(reasoning.get("effort"), supported, model)
        if value is None:
            reasoning.pop("effort", None)
            if not reasoning:
                j.pop("reasoning", None)
        else:
            reasoning["effort"] = value


def normalize_reasoning_body(body, provider, model):
    if not body:
        return body
    try:
        j = json.loads(body)
    except Exception:
        return body
    if not isinstance(j, dict):
        return body
    normalize_reasoning_effort(j, provider, model)
    return json.dumps(j, ensure_ascii=False).encode("utf-8")


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
        if meta.get("pricing"):
            entry["pricing"] = meta["pricing"]
        if "free" in meta:
            entry["free"] = bool(meta["free"])
    return entry


def _smart_alias_entries():
    """智能路由别名 -> (provider, meta, first candidate)。"""
    rconf = routing_config()
    if not rconf.get("enabled", True):
        return []
    out = []
    for alias, candidates in (rconf.get("smart_aliases") or {}).items():
        if not isinstance(candidates, list) or not candidates:
            continue
        first = str(candidates[0])
        provider = find_provider(first)
        meta = None
        if provider:
            meta = (provider.get("model_meta") or {}).get(first)
        out.append((str(alias), provider, meta, first))
    return out


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
    fallback_provider = PROVIDERS[0] if PROVIDERS else {"name": "gateway"}
    for alias, provider, meta, first in _smart_alias_entries():
        if alias in seen:
            continue
        seen.add(alias)
        data.append(model_entry(alias, provider or fallback_provider, meta, root=first))
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
    for alias, provider, meta, first in _smart_alias_entries():
        if alias in seen:
            continue
        seen.add(alias)
        data.append({
            "type": "model",
            "id": alias,
            "display_name": alias,
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
        self.billing_source = ""

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
            allowance = float(op.get("allowance") or 0) if isinstance(op, dict) else 0.0
            if isinstance(op, dict) and op.get("billing_source"):
                self.billing_source = str(op["billing_source"])
            values = {
                "input_tokens": inp,
                "output_tokens": out,
                "total_tokens": total,
                "cached_tokens": cached,
                "cache_write_tokens": cache_write,
                "reasoning_tokens": reasoning,
                "cost": cost,
                "list_cost": list_cost,
                "allowance": allowance,
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
            if self.billing_source:
                self.usage["billing_source"] = self.billing_source
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
        raw_body = self.rfile.read(length) if length else b""

        # 抓包（调试用）
        if raw_body:
            try:
                with open(os.path.join(BASE_DIR, "gateway_last_request.json"), "w", encoding="utf-8") as f:
                    f.write(raw_body.decode("utf-8", errors="replace"))
            except Exception:
                pass

        # 解析模型与协议格式
        model_id = None
        is_responses = False
        if raw_body:
            try:
                j = json.loads(raw_body)
                if isinstance(j, dict):
                    model_id = j.get("model")
                    if (not anthropic and ("input" in j or "instructions" in j)
                            and "messages" not in j):
                        is_responses = True
            except Exception:
                pass

        if not PROVIDERS:
            self._error(400, "no provider configured")
            return

        # 智能路由：候选链（智能别名 / 故障转移）
        rconf = routing_config()
        chain = routing_chain(model_id, rconf) if rconf.get("enabled", True) else []
        if not chain:
            chain = [model_id or (PROVIDERS[0].get("models") or [""])[0]]
        try:
            max_attempts = max(1, int(rconf.get("max_attempts") or len(chain)))
        except (TypeError, ValueError):
            max_attempts = len(chain)
        retry_on = set()
        for code in (rconf.get("retry_on") or DEFAULT_RETRY_ON):
            try:
                retry_on.add(int(code))
            except (TypeError, ValueError):
                pass
        try:
            cooldown = float(rconf.get("cooldown_seconds") or 300)
        except (TypeError, ValueError):
            cooldown = 300.0

        sess = (self.headers.get("x-conversation-id")
                or self.headers.get("x-opencode-session")
                or f"gw-{uuid.uuid4()}")

        attempts = 0
        last_status = 0
        last_error_body = b""
        for target in chain:
            if attempts >= max_attempts:
                break
            attempts += 1
            info = self._build_attempt(target, anthropic, path, is_responses, raw_body, sess)
            if "error" in info:
                self._error(400, info["error"])
                return
            provider = info["provider"]
            effective_model = info["effective_model"]
            health_key = f"{provider['name']}::{effective_model}"
            state = health_state(health_key, cooldown)
            if state and attempts < max_attempts:
                log(f"SKIP cooling {target} (status {state['status']}, "
                    f"{int(time.time() - state['ts'])}s ago)")
                continue
            log(f"-> {provider['name']}{info['upstream_path']} proto={info['proto']} "
                f"model={model_id} -> {effective_model} route={info['route']} "
                f"clen={length} attempt={attempts}/{max_attempts}")
            try:
                resp = requests.post(info["url"], data=info["body"], headers=info["fwd"],
                                     stream=True, timeout=900, proxies=info["proxies"])
            except Exception as e:
                log(f"ERR upstream: {e}")
                cost = int((time.time() - t0) * 1000)
                record_usage(provider["name"], effective_model or "unknown",
                             {}, 502, cost, effective_model)
                mark_health(health_key, 502, retry=True)
                last_status = 502
                last_error_body = json.dumps(
                    {"error": {"message": f"gateway upstream error: {e}"}},
                    ensure_ascii=False).encode("utf-8")
                continue

            status = resp.status_code
            if status >= 400 and status in retry_on and attempts < max_attempts:
                err_body = b"".join(resp.iter_content(chunk_size=4096))
                try:
                    resp.close()
                except Exception:
                    pass
                snippet = err_body[:300].decode("utf-8", errors="replace").replace("\n", " ")
                log(f"ERR upstream {status}: {snippet}")
                record_usage(provider["name"], effective_model or "unknown",
                             {}, status, int((time.time() - t0) * 1000), effective_model)
                mark_health(health_key, status, retry=True)
                last_status = status
                last_error_body = err_body
                log(f"FALLBACK {target} -> next attempt {attempts + 1}/{max_attempts}")
                continue

            # 用量按实际生效模型统计（智能别名/故障转移后才是真实模型）
            self._send_upstream(resp, info, provider["name"], effective_model,
                                effective_model, t0)
            return

        if last_status:
            self._send_error_body(last_status, last_error_body)
        else:
            self._error(502, "all routing attempts failed")

    def _build_attempt(self, target, anthropic, path, is_responses, raw_body, sess):
        """按候选模型构建一次上游请求（provider/端点/请求体/头/代理）。"""
        provider = find_provider(target)
        if provider is None:
            # 未知模型：回落到第一个 provider（便于 WorkBuddy 新增模型后立即可用）
            provider = PROVIDERS[0] if PROVIDERS else None
            if provider is None:
                return {"error": "no provider configured"}
            log(f"WARN unknown model {target!r}, fallback to {provider['name']}")
        model_map = provider.get("model_map") or {}
        effective_model = model_map.get(target, target) if isinstance(target, str) else target

        if anthropic:
            body = sanitize_anthropic(raw_body, provider)
        else:
            body = sanitize_body(raw_body, provider)

        # 智能路由可能换成别的候选模型：请求体里的 model 也要同步替换
        if body:
            try:
                j = json.loads(body)
                if isinstance(j, dict) and j.get("model") != effective_model:
                    j["model"] = effective_model
                    body = json.dumps(j, ensure_ascii=False).encode("utf-8")
            except Exception:
                pass

        if anthropic:
            base = anthropic_base(provider)
            if not base:
                return {"error": f"provider {provider['name']} does not support Anthropic protocol "
                                 f"(set anthropic_base_url)"}
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
            body = normalize_max_tokens_body(body, upstream_path, provider)
            body = normalize_reasoning_body(body, provider, effective_model)
            url = provider["base_url"].rstrip("/") + upstream_path

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

        provider_headers = provider.get("headers") or {}
        for k, v in provider_headers.items():
            set_header(fwd, k, str(v).replace("{session}", sess))

        custom_auth = any(k.lower() in ("authorization", "api-key", "x-api-key")
                          for k in provider_headers)
        if not custom_auth and not provider.get("pass_client_auth"):
            api_key = provider_api_key(provider, anthropic=anthropic)
            auth = auth_value(api_key)
            if auth:
                set_header(fwd, "Authorization", auth)
            if anthropic and api_key:
                set_header(fwd, "x-api-key", api_key)

        if anthropic and not any(k.lower() == "anthropic-version" for k in fwd):
            set_header(fwd, "anthropic-version", "2023-06-01")

        if provider.get("needs_session"):
            if not any(k.lower() == "x-opencode-session" for k in fwd):
                set_header(fwd, "x-opencode-session", sess)

        use_px = bool(should_proxy(provider, effective_model) and DEFAULT_PROXY)
        proxies = {"http": DEFAULT_PROXY, "https": DEFAULT_PROXY} if use_px else None
        route = "PROXY" if use_px else "DIRECT"
        proto = ("anthropic" if anthropic
                 else ("responses" if upstream_path == "/responses" else "openai"))
        return {"provider": provider, "effective_model": effective_model, "url": url,
                "body": body, "fwd": fwd, "upstream_path": upstream_path,
                "proxies": proxies, "route": route, "proto": proto}

    def _send_upstream(self, resp, info, provider_name, requested_model,
                       effective_model, t0):
        """把上游响应（流式）转发给客户端，并记录用量。"""
        status = resp.status_code
        upstream_path = info["upstream_path"]
        self.send_response(status)
        hop = {"content-length", "connection", "transfer-encoding", "content-encoding", "keep-alive"}
        for k, v in resp.headers.items():
            if k.lower() in hop:
                continue
            self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        collector = UsageCollector(anthropic=(info["proto"] == "anthropic"),
                                   content_type=resp.headers.get("Content-Type", ""))
        first = True
        try:
            for chunk in resp.iter_content(chunk_size=4096):
                if not chunk:
                    continue
                collector.feed(chunk)
                if first:
                    first = False
                    if status >= 400:
                        snippet = chunk[:300].decode("utf-8", errors="replace").replace("\n", " ")
                        log(f"ERR upstream {status}: {snippet}")
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
            record_usage(provider_name, requested_model or effective_model or "unknown",
                         usage, status, cost, effective_model)
        mark_health(f"{provider_name}::{effective_model}", status, retry=False)
        tps = usage.get("output_tps")
        log(f"<- {status} {cost}ms usage={usage or {}}"
            + (f" tps={tps}" if tps else ""))

    def _send_error_body(self, status, body):
        """发送上游错误体（故障转移全部失败后）。"""
        if not body:
            body = json.dumps({"error": {"message": f"upstream error {status}"}},
                              ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

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

    def _json(self, code, obj, headers=None):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _client_ip(self):
        return (self.client_address or ("",))[0]

    def _is_loopback_client(self):
        try:
            return ipaddress.ip_address(self._client_ip()).is_loopback
        except ValueError:
            return False

    def _cookie_value(self, name):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value
        return ""

    def _admin_access(self):
        """管理端访问控制：默认仅本机；远程需 admin.allow_remote + admin.token。"""
        if self._is_loopback_client():
            return True
        policy = admin_policy()
        if not policy["allow_remote"]:
            self._json(403, {"ok": False,
                             "error": "admin UI is local-only; set admin.allow_remote=true and admin.token to allow remote access"})
            return False
        if not policy["token"]:
            self._json(403, {"ok": False,
                             "error": "admin.token is required when admin.allow_remote is enabled"})
            return False
        supplied = (self.headers.get("X-Admin-Token") or "").strip()
        if not supplied:
            supplied = self._cookie_value("gw_admin_token")
        if not supplied:
            supplied = (parse_qs(urlparse(self.path).query).get("token") or [""])[0]
        if not hmac.compare_digest(supplied, policy["token"]):
            self._json(401, {"ok": False, "error": "invalid admin token"})
            return False
        self._admin_set_cookie = True
        return True

    def _admin_get(self):
        path = self.path.split("?")[0]
        if path in ("/admin", "/admin/"):
            payload = admin_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            if getattr(self, "_admin_set_cookie", False):
                self.send_header("Set-Cookie",
                                 f"gw_admin_token={admin_policy()['token']}; Path=/; HttpOnly; SameSite=Lax")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif path == "/admin/api/config":
            self._json(200, CFG, {"X-Config-Revision": CFG_REVISION})
        elif path == "/admin/api/status":
            total = sum(len(p.get("models", [])) for p in PROVIDERS)
            proxy = sum(len(p.get("proxy_models", [])) for p in PROVIDERS)
            lan = lan_ipv4_addresses() if IS_WILDCARD else []
            self._json(200, {"ok": True, "version": GW_VERSION,
                             "providers": len(PROVIDERS), "models": total,
                             "proxy_models": proxy, "proxy": DEFAULT_PROXY,
                             "listen": f"{LISTEN_HOST}:{LISTEN_PORT}",
                             "lan_urls": [f"http://{ip}:{LISTEN_PORT}" for ip in lan],
                             "uptime": time.time() - START_TIME})
        elif path == "/admin/api/routing":
            with HEALTH_LOCK:
                health = [dict(state, key=key) for key, state in list(HEALTH.items())]
            health.sort(key=lambda x: x.get("ts", 0), reverse=True)
            self._json(200, {"config": routing_config(), "health": health,
                             "chains": {k: routing_chain(k)
                                        for k in ((routing_config().get("smart_aliases") or {})
                                                  .keys())}})
        elif path == "/admin/api/usage":
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            period = q.get("period", ["all"])[0]
            self._json(200, usage_snapshot(period))
        elif path == "/admin/api/quota":
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            force = q.get("refresh", ["0"])[0].lower() in ("1", "true", "yes")
            self._json(200, quota_snapshot(force=force))
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
                           "max_tokens": 512}).encode()

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
                finish = ""
                try:
                    j = r.json()
                    choice = (j.get("choices") or [{}])[0]
                    msg = choice.get("message") or {}
                    finish = choice.get("finish_reason") or ""
                    reply = (msg.get("content") or msg.get("reasoning_content")
                             or choice.get("text") or "")
                except Exception:
                    pass
                if not reply:
                    reply = ("(无内容：输出上限被思考 token 用完)"
                             if finish == "length" else "(空回复)")
                result = {"ok": True, "model": model, "provider": provider["name"],
                          "route": route, "reply": reply}
                if finish:
                    result["finish_reason"] = finish
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
                client_rev = self.headers.get("X-Config-Revision")
                if not client_rev:
                    self._json(400, {"ok": False, "error": "missing config revision; refresh admin page"})
                    return
                if client_rev != CFG_REVISION:
                    self._json(409, {"ok": False, "error": "config changed elsewhere; refresh admin page"})
                    return
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
            if not self._admin_access():
                return
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
            if not self._admin_access():
                return
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
            if not self._admin_access():
                return
            self._admin_post()
        else:
            self._handle()



def quota_poll_loop():
    """后台每 5 分钟采样一次服务端额度，用于估算消耗速度。"""
    while True:
        try:
            fetch_server_quota(force=True)
        except Exception as e:
            log(f"ERR quota poll: {e}")
        time.sleep(300)


def main():
    srv = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), GatewayHandler)
    log(f"gateway listening on http://{LISTEN_HOST}:{LISTEN_PORT} providers={[p['name'] for p in PROVIDERS]} proxy={DEFAULT_PROXY}")
    threading.Thread(target=quota_poll_loop, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("gateway stopped")


if __name__ == "__main__":
    main()

