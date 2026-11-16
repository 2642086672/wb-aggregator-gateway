#!/usr/bin/env python3
"""
WorkBuddy -> OpenCode Go 本地桥
作用：接收 WorkBuddy 的 OpenAI 兼容请求，补上 x-opencode-session 头后转发到 OpenCode Go。
WorkBuddy 本身不发 x-opencode-session（只发 x-conversation-id），直连 Go 会 400 MissingSessionID。

用法：
  python wb_opencode_bridge.py
  WB_UPSTREAM_PROXY=http://127.0.0.1:7897 python wb_opencode_bridge.py   # 走本地代理（OpenAI 系模型必需）

WorkBuddy 配置 url 填：http://127.0.0.1:8787/v1
"""
import json
import os
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

LISTEN_HOST = os.environ.get("WB_BRIDGE_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("WB_BRIDGE_PORT", "8787"))
UPSTREAM = os.environ.get("WB_UPSTREAM", "https://opencode.ai/inference/go/openai/v1").rstrip("/")
# 上游代理：OpenAI 系模型（gpt-6-luna 等）必须非受限地区出口，默认走 Clash Verge 7897
UPSTREAM_PROXY = os.environ.get("WB_UPSTREAM_PROXY", "http://127.0.0.1:7897")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wb_bridge.log")


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


class BridgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # WorkBuddy 模型 id -> OpenCode Go 真实 id
    MODEL_MAP = {
        "gpt-6-luna-responses": "gpt-6-luna",
        "gpt-6-luna": "gpt-6-luna",
        "glm-5.3-flash": "glm-5.3-flash",
        "deepseek-v4.1-flash": "deepseek-v4.1-flash",
    }
    # 受地区限制、必须走代理出口的模型（OpenAI / xAI / Meta 系）
    PROXY_MODELS = {
        "gpt-6-luna", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-6-astra",
        "grok-4.7", "grok-4.6",
        "muse-spark-1.2-contributor", "muse-spark-1.3-contributor",
        "crouton-alpha-free",
    }
    # 标准 OpenAI message 字段（其余全部丢弃）
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

    def log_message(self, *args):  # 静音默认访问日志
        pass

    def _sanitize(self, body: bytes, path: str) -> bytes:
        """剥离 WorkBuddy 私有字段，映射模型 id。解析失败则原样返回。"""
        if not body:
            return body
        try:
            j = json.loads(body)
        except Exception:
            return body
        if not isinstance(j, dict):
            return body

        # 模型 id 映射
        mid = j.get("model")
        if isinstance(mid, str) and mid in self.MODEL_MAP:
            j["model"] = self.MODEL_MAP[mid]

        # chat/completions：清洗 messages
        msgs = j.get("messages")
        if isinstance(msgs, list):
            for m in msgs:
                if isinstance(m, dict):
                    for k in list(m.keys()):
                        if k not in self.STD_MSG_KEYS:
                            del m[k]

        # responses：清洗 input
        inp = j.get("input")
        if isinstance(inp, list):
            for m in inp:
                if isinstance(m, dict):
                    for k in list(m.keys()):
                        if k not in self.STD_INPUT_KEYS:
                            del m[k]

        # chat 顶层可能带私有字段，只保留标准字段
        std_top = {"model", "messages", "tools", "tool_choice", "max_tokens",
                   "max_completion_tokens", "stream", "stream_options",
                   "temperature", "top_p", "n", "stop", "presence_penalty",
                   "frequency_penalty", "logit_bias", "user", "response_format",
                   "seed", "parallel_tool_calls", "metadata", "store",
                   "reasoning", "effort"}
        for k in list(j.keys()):
            if k not in std_top:
                del j[k]

        return json.dumps(j, ensure_ascii=False).encode("utf-8")

    # ---- 公共转发逻辑 ----
    def _proxy(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        path = self.path or ""

        # 协议格式检测：WorkBuddy 对 openai-responses 条目会构造 Responses 格式 body，
        # 但可能仍请求 /chat/completions 路径，这里按 body 实际格式纠正目标端点。
        is_responses = False
        if body:
            try:
                j = json.loads(body)
                if isinstance(j, dict) and ("input" in j or "instructions" in j) and "messages" not in j:
                    is_responses = True
            except Exception:
                pass

        if path.endswith("/responses") or is_responses:
            upstream_path = "/responses"
        else:
            upstream_path = "/chat/completions"

        url = UPSTREAM + upstream_path

        # 透传客户端请求头（去掉 Hop-by-Hop 与需要重算的字段）
        fwd = {}
        for k, v in self.headers.items():
            lk = k.lower()
            if lk in ("host", "content-length", "connection", "accept-encoding"):
                continue
            fwd[k] = v

        # 关键：补 x-opencode-session。优先复用 WorkBuddy 的 x-conversation-id（UUID v4，会话内固定）
        sess = self.headers.get("x-conversation-id") or self.headers.get("x-opencode-session")
        if not sess:
            sess = "wb-" + str(uuid.uuid4())
        fwd["x-opencode-session"] = sess

        auth = self.headers.get("Authorization") or self.headers.get("authorization") or ""
        log(f"-> {upstream_path} sess={sess[:12]} auth={'yes' if auth else 'no'} clen={length}")

        # 抓包：把 WorkBuddy 原始请求体落盘，用于分析私有字段
        if body:
            try:
                with open(os.path.join(os.path.dirname(LOG_PATH), "wb_last_request.json"), "w", encoding="utf-8") as f:
                    f.write(body.decode("utf-8", errors="replace"))
            except Exception:
                pass

        # ---- 清洗 WorkBuddy 私有字段 ----
        # WorkBuddy 会在 message 里塞 startsNewUserRequest/agent/conversationRequestId/messageId/
        # stepSeq/traceId/rawUsage/usage/requestModelId 等私有字段，OpenCode 见到就拒。
        # 只保留标准 OpenAI 字段；同时把 WorkBuddy 的模型 id 映射成 OpenCode 真实 id。
        body = self._sanitize(body, path)

        proxies = {"http": UPSTREAM_PROXY, "https": UPSTREAM_PROXY} if UPSTREAM_PROXY else None
        # 按模型决定出口：OpenAI/xAI/Meta 系受地区限制必须走代理，其余直连（更快）
        model_id = None
        try:
            _j = json.loads(body) if body else {}
            if isinstance(_j, dict):
                model_id = _j.get("model")
        except Exception:
            pass
        if model_id:
            if model_id in self.PROXY_MODELS:
                proxies = {"http": UPSTREAM_PROXY, "https": UPSTREAM_PROXY} if UPSTREAM_PROXY else None
                log(f"   route: PROXY ({model_id})")
            else:
                proxies = None
                log(f"   route: DIRECT ({model_id})")
        try:
            resp = requests.post(url, data=body, headers=fwd, stream=True,
                                 timeout=900, proxies=proxies)
        except Exception as e:
            log(f"ERR upstream: {e}")
            payload = json.dumps({"error": {"message": f"bridge upstream error: {e}"}}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        # 回写状态与响应头
        self.send_response(resp.status_code)
        hop = {"content-length", "connection", "transfer-encoding", "content-encoding", "keep-alive"}
        for k, v in resp.headers.items():
            if k.lower() in hop:
                continue
            self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        try:
            for chunk in resp.iter_content(chunk_size=4096):
                if not chunk:
                    continue
                self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except Exception as e:
            log(f"ERR stream: {e}")

        log(f"<- {resp.status_code} ({upstream_path})")

    do_POST = _proxy

    def do_GET(self):  # 健康检查
        payload = json.dumps({"ok": True, "service": "wb-opencode-bridge",
                              "upstream": UPSTREAM, "proxy": UPSTREAM_PROXY}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    srv = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), BridgeHandler)
    log(f"bridge listening on http://{LISTEN_HOST}:{LISTEN_PORT} -> {UPSTREAM} (proxy={UPSTREAM_PROXY})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("bridge stopped")


if __name__ == "__main__":
    main()
