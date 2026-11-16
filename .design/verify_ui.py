#!/usr/bin/env python3
"""界面改动的回归校验：确保只动呈现层，功能与数据链路零改动。

用法：python .design/verify_ui.py <新页面.html>
对比基准固定为 .design/admin_source.html（从 gateway.py 原样抽出）。
"""
import re
import sys
import pathlib

BASE = pathlib.Path(__file__).with_name("admin_source.html")


def scripts(html):
    return re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.S | re.I)


def ids(html):
    return set(re.findall(r'\bid=["\']([^"\']+)["\']', html))


def handlers(html):
    return set(re.findall(r"onclick=[\"']([^\"']+)[\"']", html))


def urls(html):
    return set(re.findall(r"['\"](/[A-Za-z0-9_./?=&-]+)['\"]", html))


def emojis(html):
    return sorted(set(re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", html)))


def markup_only(html):
    """剥离冻结的 <script> 块再扫渲染层。

    源页面 22 个 emoji 里 20 个、7 处行内 font-size 里 4 处都在必须逐字节
    保持的 JS 里，全文件扫描与「script 不动」数学上互斥；这两项只判 markup。
    """
    return re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S | re.I)


def main():
    if len(sys.argv) < 2:
        print("usage: python .design/verify_ui.py <new.html>")
        return 2
    new_path = pathlib.Path(sys.argv[1])
    base = BASE.read_text(encoding="utf-8")
    new = new_path.read_text(encoding="utf-8")

    checks = []
    bs, ns = scripts(base), scripts(new)
    checks.append(("原有 script 块逐字节不变",
                   bool(bs) and len(ns) >= len(bs) and ns[:len(bs)] == bs,
                   f"base={len(bs)} new={len(ns)}"))
    checks.append(("新增 script 块不超过 1 个",
                   len(ns) - len(bs) <= 1,
                   f"新增 {len(ns)-len(bs)} 个"))
    missing_ids = ids(base) - ids(new)
    checks.append(("DOM id 无缺失", not missing_ids,
                   f"缺失 {sorted(missing_ids)[:8]}"))
    missing_h = handlers(base) - handlers(new)
    checks.append(("onclick 处理器无缺失", not missing_h,
                   f"缺失 {sorted(missing_h)[:8]}"))
    missing_u = urls(base) - urls(new)
    checks.append(("API 路径无缺失", not missing_u,
                   f"缺失 {sorted(missing_u)[:8]}"))
    checks.append(("无外部资源引用",
                   not re.search(r'(src|href)=["\']https?://', new, re.I),
                   "无 CDN / 外链"))
    mk = markup_only(new)
    left = emojis(mk)
    checks.append(("渲染层无 emoji 图标", not left, f"残留 {left}"))
    checks.append(("渲染层无行内 style 硬编码字号",
                   not re.search(r'style=["\'][^"\']*font-size', mk, re.I),
                   "字号走 CSS 档位；冻结 script 内 4 处按 B1 豁免"))

    ok = True
    for name, passed, detail in checks:
        print(f"[{'PASS' if passed else 'FAIL'}] {name} — {detail}")
        ok &= passed
    print("\n结论:", "全部通过，可以写回 gateway.py" if ok else "有失败项，禁止写回")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
