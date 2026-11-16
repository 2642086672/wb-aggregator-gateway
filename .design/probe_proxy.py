#!/usr/bin/env python3
"""临时反向代理：把 8788 的 /admin 响应注入探针脚本，用于实测盒模型。

只读诊断用，测完即关。不修改任何项目文件。
"""
import http.server
import urllib.request

UP = "http://127.0.0.1:8789"
PORT = 8791

PROBE = """
<script>
window.addEventListener('load',function(){
  setTimeout(function(){
    try{
      var q=function(s){return document.querySelector(s)};
      var v0=q('#view-usage'); if(v0){v0.removeAttribute('hidden');v0.classList.add('is-active');v0.style.setProperty('display','grid','important');}
      var w=function(e){return e?Math.round(e.getBoundingClientRect().width):null};
      var cs=function(e){return e?getComputedStyle(e):null};
      var tw=q('#usageVendors .table-wrap');
      var card=q('#view-usage .card');
      var r={
        win:window.innerWidth,
        docScrollW:document.documentElement.scrollWidth,
        main:w(q('main')),
        view:w(q('#view-usage')),
        viewDisplay:cs(q('#view-usage'))&&cs(q('#view-usage')).display,
        viewCols:cs(q('#view-usage'))&&cs(q('#view-usage')).gridTemplateColumns,
        card:w(card),
        cardMinW:cs(card)&&cs(card).minWidth,
        vendors:w(q('#usageVendors')),
        tw:w(tw),
        twOverflow:cs(tw)&&cs(tw).overflowX,
        twMaxW:cs(tw)&&cs(tw).maxWidth,
        twMinW:cs(tw)&&cs(tw).minWidth,
        tbl:w(q('#usageVendors table')),
        tblW:cs(q('#usageVendors table'))&&cs(q('#usageVendors table')).width,
        twClientW:tw&&tw.clientWidth,
        twScrollW:tw&&tw.scrollWidth,
        at1300:(function(){var e=document.elementFromPoint(1300,500);return e?(e.tagName+'.'+(e.className||'')):null;})(),
        at1100:(function(){var e=document.elementFromPoint(1100,500);return e?(e.tagName+'.'+(e.className||'')):null;})(),
        at960:(function(){var e=document.elementFromPoint(960,500);return e?(e.tagName+'.'+(e.className||'')):null;})(),
        cardRect:card&&JSON.stringify(card.getBoundingClientRect()),
        twRect:tw&&JSON.stringify(tw.getBoundingClientRect())
      };
      var box=document.createElement('div');
      box.style.cssText='position:fixed;top:0;left:0;right:0;z-index:99999;background:#ffe600;color:#000;font:11px monospace;padding:6px;white-space:pre-wrap';
      box.textContent='PROBE '+JSON.stringify(r);
      document.body.appendChild(box);
      document.title='PROBE'+JSON.stringify(r);
    }catch(e){document.title='PROBEERR:'+e.message;}
  },4000);
});
</script>
"""


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _proxy(self):
        url = UP + self.path
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(url, data=body, method=self.command)
        for k, v in self.headers.items():
            if k.lower() not in ("host", "connection", "content-length"):
                req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read()
                ctype = r.headers.get("Content-Type", "")
                if "/admin" in self.path and "text/html" in ctype:
                    txt = data.decode("utf-8")
                    txt = txt.replace("</body>", PROBE + "</body>")
                    data = txt.encode("utf-8")
                self.send_response(r.status)
                self.send_header("Content-Type", ctype or "text/html")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except Exception as e:
            payload = ("probe proxy error: %s" % e).encode()
            self.send_response(502)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    do_GET = _proxy
    do_POST = _proxy


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
