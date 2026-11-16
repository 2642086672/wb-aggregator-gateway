#!/usr/bin/env python3
"""用真实用量数据离线渲染 admin_new.html，实测表格是否还需要横向滚动。

只读诊断，不改任何交付物。桩服务器用随机端口，跑完即关。
"""
import sys
import html as H
import http.server
import json
import pathlib
import re
import socketserver
import subprocess
import threading
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import gateway  # 工作区模块，读工作区 config / usage

ROOT = pathlib.Path(__file__).resolve().parent
HTML = (ROOT / "admin_new.html").read_text(encoding="utf-8")

USAGE = gateway.usage_snapshot()
CFG = gateway.CFG
LOGS = {"lines": gateway.read_logs(120)}

PROBE = r"""
setTimeout(function(){
  var q=function(s){return document.querySelector(s)};
  var run=q('#view-run'); if(run){run.style.setProperty('display','none','important');}
  var v0=q('#view-usage');
  if(v0){v0.removeAttribute('hidden');v0.classList.add('is-active');
         v0.style.setProperty('display','grid','important');}
  var tw=q('#usageVendors .table-wrap');
  var card=q('#view-usage .card');
  var tbl=q('#usageVendors table');
  var r={
    win: window.innerWidth,
    docScrollW: document.documentElement.scrollWidth,
    card: card?Math.round(card.getBoundingClientRect().width):null,
    tw: tw?Math.round(tw.getBoundingClientRect().width):null,
    twClient: tw?tw.clientWidth:null,
    twScroll: tw?tw.scrollWidth:null,
    needScroll: tw?(tw.scrollWidth-tw.clientWidth):null,
    tbl: tbl?Math.round(tbl.getBoundingClientRect().width):null,
    modelTw: (function(){var t=q('#usageModels .table-wrap');
      return t?{c:t.clientWidth,s:t.scrollWidth}:null;})()
  };
  var p=document.createElement('pre'); p.id='__probe'; p.style.display='none';
  p.textContent='__PROBE__'+JSON.stringify(r)+'__END__';
  document.body.appendChild(p);
}, 4000);
"""
PAGE = HTML.replace("</body>", "<script>" + PROBE + "</script>\n</body>", 1)

QUOTA_LOCAL = {
    "ok": True, "plan": "Go",
    "usage": {
        "rolling": {"percent": 0.0, "resetsAt": "-", "status": "ok"},
        "weekly": {"percent": 100.0, "resetsAt": "-", "status": "rate-limited"},
        "monthly": {"percent": 54.0, "resetsAt": "-", "status": "ok"},
    },
    "eta": {},
    "zen": {"configured": False, "ok": False, "mode": "prepaid"},
    "local": {"models": []},
}

ROUTES = {
    "/admin/api/config": CFG,
    "/admin/api/status": {"ok": True, "version": gateway.GW_VERSION,
                          "providers": len(gateway.PROVIDERS), "models": 27,
                          "proxy_models": 8, "proxy": gateway.DEFAULT_PROXY,
                          "listen": "0.0.0.0:0", "lan_urls": [], "uptime": 1.0},
    "/admin/api/logs": LOGS,
    "/admin/api/usage": USAGE,
    "/admin/api/quota": QUOTA_LOCAL,
    "/admin/api/routing": {"config": gateway.routing_config(), "health": [],
                            "chains": {}},
    "/admin/api/test": {"ok": True},
    "/admin/api/reload": {"ok": True},
    "/admin/api/usage/reset": {"ok": True},
}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, obj, ctype="application/json; charset=utf-8"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/admin":
            return self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if path in ROUTES:
            return self._send(ROUTES[path])
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        self.do_GET()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    srv = Server(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/admin" % port
    print("真实数据桩服务器:", url)
    print("真实 usage: requests=%s vendors=%s models=%s" % (
        (USAGE.get("totals") or {}).get("requests"),
        len(USAGE.get("vendors") or []), len(USAGE.get("models") or [])))

    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    dom = subprocess.run(
        [edge, "--headless=new", "--disable-gpu", "--no-first-run",
         "--disable-extensions", "--hide-scrollbars", "--force-device-scale-factor=1",
         "--user-data-dir=%s" % (ROOT / "_edge_real"),
         "--window-size=1440,1400", "--virtual-time-budget=12000",
         "--dump-dom", url],
        capture_output=True, text=True, timeout=120).stdout
    m = re.search(r'<pre id="__probe"[^>]*>__PROBE__(.*?)__END__</pre>', dom, re.S)
    if not m:
        print("探针未返回；dump 长度", len(dom))
    else:
        r = json.loads(H.unescape(m.group(1)))
        print("\n=== 真实数据实测 ===")
        for k in ("win", "docScrollW", "card", "tw", "twClient", "twScroll",
                  "needScroll", "tbl", "modelTw"):
            print("  %-10s %s" % (k, r.get(k)))
        ok = r.get("needScroll") is not None and r["needScroll"] <= 0
        print("\n结论:", "表格已放得下，无需横向滚动" if ok
              else "仍需横向滚动 %s px" % r.get("needScroll"))
        print("文档横向溢出:", r["docScrollW"] > r["win"] + 1)

    shot = ROOT / "probe_real_usage.png"
    subprocess.run(
        [edge, "--headless=new", "--disable-gpu", "--no-first-run",
         "--disable-extensions", "--hide-scrollbars", "--force-device-scale-factor=1",
         "--user-data-dir=%s" % (ROOT / "_edge_real_shot"),
         "--window-size=1440,1400", "--virtual-time-budget=12000",
         "--screenshot=%s" % shot, url],
        capture_output=True, text=True, timeout=120)
    print("截图:", shot, shot.stat().st_size if shot.exists() else "缺失")
    srv.shutdown()


if __name__ == "__main__":
    main()
