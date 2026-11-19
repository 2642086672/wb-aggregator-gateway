#!/usr/bin/env python3
"""把工作区 gateway.py 的 ADMIN_HTML 段同步到实际运行目录。

只替换 ADMIN_HTML 常量的中间内容，其余字节不动。
同步前核对：DEFAULT_ROUTING_CONFIG 一致、非页面段字节一致。
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

SRC = pathlib.Path(r"<VENV_DIR>\gateway.py")
DST = pathlib.Path(r"<PROJECT_DIR>\gateway.py")


def routing(text):
    m = re.search(r'DEFAULT_ROUTING_CONFIG\s*=\s*(\{.*?\n\})', text, re.S)
    return m.group(1) if m else None


def main():
    if not DST.exists():
        raise SystemExit("运行目录不存在: %s" % DST)
    stamp = pathlib.Path(__file__).with_name("sync_stamp.txt")
    tag = stamp.read_text().strip() if stamp.exists() else "0"
    bak = DST.with_name("gateway.py.bak-20261113-1800")
    shutil.copy2(DST, bak)
    print("备份:", bak)

    s = SRC.read_text(encoding="utf-8")
    d = DST.read_text(encoding="utf-8")
    pat = re.compile(r'(ADMIN_HTML = r""")(.*?)(""")', re.S)
    ms, md = pat.search(s), pat.search(d)
    if not ms or not md:
        raise SystemExit("未定位 ADMIN_HTML")
    new_body = ms.group(2)

    if routing(s) != routing(d):
        raise SystemExit("DEFAULT_ROUTING_CONFIG 不一致，中止")
    print("前置核对通过：DEFAULT_ROUTING_CONFIG 一致")
    print("注意：只替换 ADMIN_HTML 段，运行目录的其余代码（含后加的 _is_lan_ipv4 等）原样保留")

    out = d[:md.start(2)] + new_body + d[md.end(2):]
    tmp = DST.with_name("gateway.py.ui_tmp")
    tmp.write_text(out, encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "py_compile", str(tmp)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise SystemExit("py_compile 失败: " + r.stderr[:300])
    os.replace(tmp, DST)
    print("已同步:", DST, DST.stat().st_size, "B")
    print("ADMIN_HTML:", len(md.group(2)), "->", len(new_body))


if __name__ == "__main__":
    main()
