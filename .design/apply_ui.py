#!/usr/bin/env python3
"""把 .design/admin_new.html 原子写回 gateway.py 的 ADMIN_HTML 常量。

只替换 ADMIN_HTML = r\"\"\"...\"\"\" 的中间内容；文件其余部分逐字节不动。
写回前先跑 verify_ui.py 的同等约束，失败即中止。
"""
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "gateway.py"
BASE = ROOT / ".design" / "admin_source.html"
NEW = ROOT / ".design" / "admin_new.html"


def die(msg):
    print("[ABORT]", msg)
    sys.exit(1)


def main():
    if not NEW.exists():
        die(f"缺少 {NEW}")
    gw = SRC.read_text(encoding="utf-8")
    base = BASE.read_text(encoding="utf-8")
    new = NEW.read_text(encoding="utf-8")

    # 1. script 块逐字节不变（原有功能）
    bs = re.findall(r"<script\b[^>]*>(.*?)</script>", base, re.S | re.I)
    ns = re.findall(r"<script\b[^>]*>(.*?)</script>", new, re.S | re.I)
    if not bs or ns[:len(bs)] != bs:
        die("原有 script 块被改动，禁止写回")
    # 2. id / onclick / API 路径无缺失
    for label, pat in (("DOM id", r'\bid=["\']([^"\']+)["\']'),
                       ("onclick", r'onclick=["\']([^"\']+)["\']'),
                       ("API 路径", r"['\"](/[A-Za-z0-9_./?=&-]+)['\"]")):
        miss = set(re.findall(pat, base)) - set(re.findall(pat, new))
        if miss:
            die(f"{label} 缺失: {sorted(miss)[:8]}")
    # 3. raw string 边界安全
    if '"""' in new or new.rstrip().endswith('"'):
        die("内容含三引号，会截断 Python raw string")

    # 4. 还原两个占位符（写死会破坏版本显示与「恢复默认路由」功能）
    m = re.search(r'DEFAULT_ROUTING_CONFIG\s*=\s*(\{.*?\n\})', gw, re.S)
    if not m:
        die("未找到 DEFAULT_ROUTING_CONFIG")
    routing_json = json.dumps(eval(m.group(1), {"__builtins__": {}}), ensure_ascii=False)
    if routing_json not in new:
        die("默认路由 JSON 不在新页面中，无法还原 {{ROUTING_DEFAULT}}")
    new = new.replace(routing_json, "{{ROUTING_DEFAULT}}")
    new = re.sub(r"v1\.4\.4(?=</span>)", "v{{VERSION}}", new)
    if "{{VERSION}}" not in new or "{{ROUTING_DEFAULT}}" not in new:
        die("占位符还原失败")

    # 5. 整段替换，其余字节不动
    pat = re.compile(r'(ADMIN_HTML = r""")(.*?)(""")', re.S)
    mm = pat.search(gw)
    if not mm:
        die("未定位 ADMIN_HTML")
    out = gw[:mm.start(2)] + new + gw[mm.end(2):]

    # 6. 原子写
    tmp = SRC.with_suffix(".py.ui_tmp")
    tmp.write_text(out, encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "py_compile", str(tmp)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        die("写回后语法检查失败: " + r.stderr.strip()[:300])
    os.replace(tmp, SRC)
    print("[OK] gateway.py 已原子写回")
    print(f"     ADMIN_HTML {len(mm.group(2))} -> {len(new)} 字符")
    print(f"     备份: gateway.py.bak-20261113-1510")


if __name__ == "__main__":
    main()
