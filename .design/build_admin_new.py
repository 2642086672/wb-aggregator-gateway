#!/usr/bin/env python3
"""从 admin_source.html 精确切片生成 admin_new.html。

只改呈现层：style / header / main 分组包裹 / 纯 a11y 属性 / 追加 1 个展示层 script。
两块冻结 <script> 以「不参与任何替换」的方式原样保留，逐字节不变。
"""
import re
import pathlib
import hashlib

ROOT = pathlib.Path(__file__).resolve().parent
SRC = ROOT / "admin_source.html"
OUT = ROOT / "admin_new.html"

src = SRC.read_text(encoding="utf-8")

CSS = r"""
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
.table-wrap{overflow:auto;max-height:440px;border:1px solid var(--border);border-radius:var(--r-md)}
.table-wrap table{width:100%;border-collapse:collapse;font-size:var(--fs-2)}
.table-wrap th,.table-wrap td{padding:var(--s-2) var(--s-3);border-bottom:1px solid var(--border);text-align:left;white-space:nowrap;line-height:1.5}
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
"""

DISPLAY_JS = r"""
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
"""

SVG_MARK = (
    '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" '
    'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M2 3.5 8 8M2 8h6M2 12.5 8 8M8 8h6"/></svg>'
)
SVG_BOLT = (
    '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" '
    'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z"/></svg>'
)

TABS = "\n".join([
    '        <button type="button" class="tab is-active" id="tab-run" role="tab" aria-selected="true" aria-controls="view-run" data-view="run" tabindex="0">运行台</button>',
    '        <button type="button" class="tab" id="tab-usage" role="tab" aria-selected="false" aria-controls="view-usage" data-view="usage" tabindex="-1">用量</button>',
    '        <button type="button" class="tab" id="tab-upstream" role="tab" aria-selected="false" aria-controls="view-upstream" data-view="upstream" tabindex="-1">上游</button>',
    '        <button type="button" class="tab" id="tab-rules" role="tab" aria-selected="false" aria-controls="view-rules" data-view="rules" tabindex="-1">规则</button>',
])

NEW_HEADER = """<header>
  <div class="shell">
    <div class="header-left">
      <h1>%s聚合网关管理端 <span class="ver">v1.4.4</span></h1>
      <nav class="tabs" role="tablist" aria-label="仪表板分组">
%s
      </nav>
    </div>
    <div class="header-right">
      <span class="badge" id="statusBadge">运行中</span>
      <button class="sm" onclick="reload()">重载配置</button>
      <button class="sm" onclick="testAll()">%s一键测试</button>
      <button class="sm primary" onclick="openAdd()">+ 添加 Provider</button>
    </div>
  </div>
</header>""" % (SVG_MARK, TABS, SVG_BOLT)


def build():
    html = src

    # 1) 整块替换 <style>
    m = re.search(r"<style>\n[\s\S]*?\n</style>", html)
    assert m, "style block not found"
    html = html.replace(m.group(0), "<style>\n" + CSS.strip("\n") + "\n</style>", 1)

    # 2) 替换 <header>（保留 3 个 onclick 与 v1.4.4</span> 文本形态）
    m = re.search(r"<header>\n[\s\S]*?\n</header>", html)
    assert m, "header not found"
    html = html.replace(m.group(0), NEW_HEADER, 1)

    # 3) 切出 9 张卡，按 <h2> 标题定位，只移动不复制
    m = re.search(r"(<main>\n)([\s\S]*?)(\n</main>)", html)
    assert m, "main not found"
    inner = m.group(2)
    parts = inner.split('  <div class="card">')
    cards = ['  <div class="card">' + p for p in parts[1:]]
    assert len(cards) == 9, "expected 9 cards, got %d" % len(cards)

    def one(needle):
        hits = [c for c in cards if needle in c]
        assert len(hits) == 1, "needle %r matched %d cards" % (needle, len(hits))
        return hits[0]

    c_overview = one('id="stats"')
    c_quota = one('id="quotaSummary"')
    c_zen = one('id="zenSummary"')
    c_routing = one('id="routingJson"')
    c_auth = one('id="authKeys"')
    c_providers = one('id="providers"')
    c_models = one('id="modelTable"')
    c_usage = one('id="usageTotals"').replace('<h2 style="font-size:13px">', '<h2 class="sub-h">')
    c_log = one('id="log"')
    assert c_usage.count('<h2 class="sub-h">') == 3, "inline font-size not fully removed"

    def section(name, group, active=False):
        cls = "view is-active" if active else "view"
        hid = "" if active else " hidden"
        head = ('  <section class="%s" id="view-%s" data-view="%s" role="tabpanel" '
                'aria-labelledby="tab-%s"%s>') % (cls, name, name, name, hid)
        return head + "\n" + "\n".join(group) + "\n  </section>"

    sections = [
        section("run", [c_quota, c_overview, c_zen, c_log], True),
        section("usage", [c_usage]),
        section("upstream", [c_providers, c_models]),
        section("rules", [c_routing, c_auth]),
    ]
    html = html.replace(m.group(0), "<main>\n" + "\n".join(sections) + "\n</main>", 1)

    # 4) 追加唯一 1 个展示层 script：第 2 个 </script> 之后、</body> 之前
    assert html.count('<script>') == 2, "unexpected extra script before append"
    new_script = "<script>\n" + DISPLAY_JS.strip("\n") + "\n</script>\n"
    html = html.replace("</body>", new_script + "</body>", 1)

    # 5) 纯可访问性属性：给 label 补 for（不改 id/name/value/功能），只扫 markup 段
    body_at = html.index("<body>")
    script_at = html.index("<script>", body_at)
    markup = html[body_at:script_at]
    pat = re.compile(r'<label>(?P<text>[^<]*)</label>(?P<ws>\s*)(?P<tag><input|<textarea) id="(?P<id>[^"]+)"')

    def add_for(mm):
        return '<label for="%s">%s</label>%s%s id="%s"' % (
            mm.group("id"), mm.group("text"), mm.group("ws"), mm.group("tag"), mm.group("id"))

    markup2, n = pat.subn(add_for, markup)
    html = html[:body_at] + markup2 + html[script_at:]

    # ---- 硬约束自检 ----
    bs = re.findall(r"<script\b[^>]*>(.*?)</script>", src, re.S | re.I)
    ns = re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.S | re.I)
    assert ns[:len(bs)] == bs, "frozen scripts changed!"
    assert len(ns) - len(bs) == 1, "exactly 1 new script required"
    for label, pattern in (
        ("DOM id", r'\bid=["\']([^"\']+)["\']'),
        ("onclick", r'onclick=["\']([^"\']+)["\']'),
        ("API path", r"['\"](/[A-Za-z0-9_./?=&-]+)['\"]"),
    ):
        miss = set(re.findall(pattern, src)) - set(re.findall(pattern, html))
        assert not miss, "%s missing: %s" % (label, sorted(miss)[:8])
    assert '"""' not in html, "triple quote would break apply_ui.py raw string"
    assert "v1.4.4</span>" in html, "version text shape changed (apply_ui.py L56)"
    assert not re.search(r'(src|href)=["\']https?://', html, re.I), "external resource"
    mk = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    assert not re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", mk), "emoji in markup"
    assert not re.search(r'style=["\'][^"\']*font-size', mk, re.I), "inline font-size in markup"

    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)

    print("wrote:", OUT)
    print("bytes:", len(html.encode("utf-8")), "lines:", html.count("\n") + 1)
    print("script blocks:", len(ns), "frozen sha256:",
          [hashlib.sha256(x.encode()).hexdigest()[:16] for x in ns[:2]])
    print("label for= added:", n)
    print("cards per view: run=%d usage=%d upstream=%d rules=%d" % (4, 1, 2, 2))
    for s in sections:
        print("  ", re.search(r'data-view="([^"]+)"', s).group(1),
              "cards=", s.count('  <div class="card">'),
              "hidden=", ' hidden>' in s)


if __name__ == "__main__":
    build()
