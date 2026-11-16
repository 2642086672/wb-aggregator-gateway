---
product: 聚合网关管理端
register: Product
mode: Operate
source: ".design/admin_source.html"
scales:
  designVariance: 3
  motionIntensity: 1
  visualDensity: 7
container: "1120px"
colors:
  bg: "#F4F6F9"
  surface: "#FFFFFF"
  raised: "#EDF1F6"
  border: "#DDE3EC"
  ink: "#16202E"
  muted: "#5B6675"
  accent: "#2563EB"
  on-accent: "#FFFFFF"
  danger: "#B42318"
  warn: "#8A5A00"
  success: "#1F7A4D"
  info: "#1D4ED8"
  log-bg: "#0F172A"
  log-fg: "#E2E8F0"
typography:
  display:
    fontSize: "32px"
    fontWeight: "500"
    lineHeight: "1.3"
    letterSpacing: "0"
  title:
    fontSize: "24px"
    fontWeight: "500"
    lineHeight: "1.3"
    letterSpacing: "0"
  body:
    fontSize: "14px"
    fontWeight: "400"
    lineHeight: "1.7"
    letterSpacing: "0"
  caption:
    fontSize: "12px"
    fontWeight: "400"
    lineHeight: "1.5"
    letterSpacing: "0"
  mono:
    fontSize: "12px"
    fontWeight: "400"
    lineHeight: "1.5"
    letterSpacing: "0"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  base: "16px"
  lg: "24px"
  xl: "32px"
  section: "48px"
  breath: "72px"
rounded:
  sm: "8px"
  md: "12px"
  lg: "16px"
components:
  buttonPrimary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
  tag:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
  log:
    backgroundColor: "{colors.log-bg}"
    textColor: "{colors.log-fg}"
---

# DESIGN.md · 聚合网关管理端视觉方向与信息架构契约

> 上方 YAML 是**机器可读契约层**（`design_doc_check.py` 校验：颜色角色、对比度、token 引用、中文行高、间距/圆角/组件刻度）；下方散文讲为什么。组件只引用 token，不写裸 hex。

| 项 | 值 |
|---|---|
| 项目 | `D:\网关管理器`（Python 单文件 LLM 聚合网关，个人自用） |
| 基准 | `.design/admin_source.html`（797 行，`gateway.py:1141-1937` 原样抽出） |
| 形态 | **系统模式 / 改既有系统的呈现层**，不是原型；交付物是多屏设计契约，不是一张首页稿 |
| 模式 | **Operate**（监控 + 配置）｜寄存器：**Product**（设计服务产品，不是品牌页） |
| 三维刻度 | DESIGN_VARIANCE **3**（可预测，不玩非对称）／MOTION_INTENSITY **1**（几乎静态）／VISUAL_DENSITY **7**（驾驶舱密度） |
| 作者 | 颜好看（UI/UX）｜日期 2026-11-13 |
| 下游 | 前端按本文产出 `.design/admin_new.html`；QA 用 `.design/verify_ui.py` / `apply_ui.py` 对账 |

> 需求方 8 项落点索引：①一句主张 §0 ②分组方案 §1 ③配色九槽 §2 ④八档字号 §3 ⑤间距+容器 §4 ⑥逐节编排（6 节）§5 ⑦两处自定决定 §6 ⑧五条自检 §7。
> 前端拿本文后**不需要再问任何一次「这里该多少 px」**：所有值都在 §2.3 `:root`、§3、§4 写死。

---

## 0. 一句主张（+ 模式判定）

> **这是给一个人每天瞟很多次的网关运行台，不是功能清单——整页应像机房里一块只报一件事的仪表板：先报「5 小时额度还剩多少、刚才有没有报错」，配置退到第二层；信息一块不删、不遮、不折叠成摘要，只把 10 块分成 4 块可切换的板，每板只准一个主数字。**

判据（写清为什么不是空话）：把行业词「LLM 网关」换成「热水器控制器」这句话就不成立——**「5 小时滚动额度 + Zen 预付费余额 + 实时请求日志 + 按模型累计 Token」是这个产品自己独有的四件事**，不是任何后台都有的通用模块。

**模式判定**：`app-screens.md §一` —— 这是「用」的界面，不是「读」的页面。因此 `design-voice.md` 的 72–96px 首屏大字、通栏色带 hero、96–240px 节距**只对 Persuade 生效，本页不套**。本页的「主角」不是英雄标题，是 `app-screens.md §四.2` 说的**回答这一屏问题的那个数字/区块**。

**方向发牌结果（三选一，三张全弃，理由逐条）**：跑 `design_query.py --deal`，得 `dense-tiles`（井然的板）／`editorial-report`（印刷年鉴）／`quiet-instrument`（安静仪器）。按三条纪律逐条判：

| 牌 | 判定 | 理由 |
|---|---|---|
| 印刷年鉴 | 弃 | 要「报头图占首屏 45%」+ 7–9vw 标题；本项目零真实照片（用户禁外部图片），且后台工具禁 >32px 标题（`app-screens.md §十三`）。第 1 条「需要的东西我有吗」直接不成立 |
| 安静仪器 | 弃 | 单列 440–560px、节距 96–140px；放不下 15 列用量表、12 行 JSON 编辑器、7 项总览。会逼用户横滚或丢字段，违反用户硬约束 |
| 井然的板 | 弃其骨架、**继承两条** | 主张「整页一块边到边的板、无过渡、外边距 8–12px」与「4 组可切换 + 单一容器宽 + 手机不挤」相冲；且 pitfalls 明写「填不满就露洞、每格同款图标卡就变模板」，而本项目恰好是 10 块体积差异极大的内容。**继承**：`12 栏井然格` 与 `禁止格内嵌套卡片` |

替代方向来自 `app-screens.md`（有数字、可执行）：**「一块可换的仪表板」**——4 块板一次一块；每板 12 栏、单一容器宽 1120px、单一组件内间距 24px、卡间 16px、1px 线分栏、一格 6 栏承载本屏答案、无英雄头、颜色只上数据与当前项。深色日志终端是**产品自带的既有事实**（源文件 L47 `#0f172a`），继承不新造，只把它从「第 9 张卡」提升为运行台的通栏压舱带。

**知识库读取清单**（ROUTER 症状行「每节长得一样，像模板」+「UI 细节改不动」+ 产品类型「后台/工具」+ 必读手艺篇，共 5 篇，符合「一次最多 5 篇」）：
`50-design-systems/design-voice.md` · `app-screens.md` · `design-tells.md` · `visual-defect-taxonomy.md` · `theme-presets.md`。另跑 `design_query.py --brief / --deal / --direction`（脚本不是「篇」，用于取有出处的 id）。**落地决定见 §10。**

---

## 1. 信息架构分组方案

### 1.1 四组分组表（10 张卡逐张归位）

| 组 | Tab 名 | 归入的卡片（源文件行号） | 回答的一个问题 | 本屏唯一主角 | 卡片数 |
|:--:|---|---|---|---|:--:|
| **① 运行台**（**默认展示**） | 运行台 | 1 总览 `L98-101` · 2 Go 服务端额度 `L102-113` · 3 Zen 余额 `L114-134` · 9 实时日志 `L201-204` | 网关活着吗、还剩多少额度、刚才发生了什么 | `#quotaSummary .quota-card:first-child .value` → **32px**（5 小时滚动额度） | 4 |
| ② 用量 | 用量 | 8 Token 用量 `L178-200` | 这段时间烧了多少、钱花在哪个模型 | `#usageTotals .stat:nth-child(6) .value` → **32px**（总 Token） | 1 |
| ③ 上游 | 上游 | 6 Providers `L170-173` · 7 模型能力 `L174-177` | 我接了谁、每个模型能干什么 | `#providers .provider:first-child` → 唯一 12 栏宽行，名称 24px | 2 |
| ④ 规则 | 规则 | 4 智能路由 `L135-153` · 5 客户端接入密钥 `L154-169` | 谁能进来、请求往哪走 | `#routingJson` 编辑器 → 唯一主操作区 | 2 |
| — 全局浮层 | 不进 tab | 10 添加/编辑 Provider 弹窗 `L207-229`（`#modalMask`） | 增改一个上游 | 保持在 `<main>` 外、原位不动 | 1 |
| — 常驻壳 | 不进 tab | `<header>` 的产品名 / 状态徽章 / 3 个全局按钮 `L88-96` | 我在哪、现在什么状态 | 不在板上 | — |

### 1.2 为什么这么分（三条依据 + 一条「不按什么分」）

1. **按「一屏一个问题」分，不按名词分类**。`app-screens.md §四.2`：块 = 对这个屏幕后续问题的回答，没有问题的块删掉。① 是「此刻」、② 是「这段时间的账」、③ 是「我接了什么」、④ 是「我定了什么规矩」。把「额度 / 余额 / 用量 / 日志」全塞进一个叫「监控」的大组是名词分类，结果只是把 10 张平铺卡变成 6 张平铺卡，用户说的「太杂」一点没解决。
2. **按刷新频率分，避免不同轮询节奏互相拖累**。源文件 `L792-793`：`setInterval(loadQuota, 60000)`、`setInterval(loadUsage, 5000)`，其余只在 `load()` 时刷新。① 的两块是 60s 轮询，② 是 5s 轮询且每次重绘 3 张 15 列表；③ ④ 只在改配置后变。5s 重绘的板独立成屏，切走时用户不必看着表格每 5 秒跳一次。
3. **按内容体积分，两张「胖卡」各占一屏**。`Token 用量` = 12 个 KPI + 实时 TPS + 3 张 15 列表；`智能路由` = 3 段说明 + 12 行 JSON textarea + 冷却状态。它们任一与其他卡同屏，都会把对方挤到折叠下方——那就等于用「折叠」降噪，违反用户「不靠隐藏/遮罩/折叠减少信息」的硬约束。**让它们独占一屏，是在不删任何信息的前提下给它们应得的空间。**

**不按什么分**：不按「读 / 写」分。Zen 余额卡里带一个写 Cookie 的 `<details>`（L118-133），路由卡和密钥卡才是纯写。若按读/写切，Zen 会被拆开——而 `app-screens.md §四.2` 要求「最频繁的任务一键可达，控件就放在答案旁边」：配置 Console Cookie 的控件必须贴着它读出来的余额，所以整卡留 ①。

### 1.3 切换方式（CSS display，隐藏的 DOM 仍在）

```html
<main>
  <section class="view is-active" id="view-run"      data-view="run"      role="tabpanel" aria-labelledby="tab-run">…4 张原卡…</section>
  <section class="view"           id="view-usage"    data-view="usage"    role="tabpanel" aria-labelledby="tab-usage" hidden>…1 张原卡…</section>
  <section class="view"           id="view-upstream" data-view="upstream" role="tabpanel" aria-labelledby="tab-upstream" hidden>…2 张原卡…</section>
  <section class="view"           id="view-rules"    data-view="rules"    role="tabpanel" aria-labelledby="tab-rules" hidden>…2 张原卡…</section>
</main>
```

```css
.view{display:none}
.view.is-active{display:grid;grid-template-columns:repeat(12,1fr);gap:16px;align-items:start}
.view .card{grid-column:span 12}          /* 默认满宽；§5 逐节给例外 */
```

**为什么是 `display` 而不是移除 / 重建 DOM**（这是本方案的命门）：
- 源文件 `L448-455 / L545-557 / L655-676 / L790` 全部用 `document.getElementById('…').innerHTML = …` 写数据。元素只要还在文档树里，`getElementById` 就永远命中；`display:none` 不摘节点、不清内容、不解绑 `onclick`。
- 因此 `load()`、`loadQuota()`、`loadUsage()`、两个 `setInterval` 会照常把最新数据写进隐藏的板；**切回来即见最新值，不需要为新组补任何一次 fetch**。
- 禁止的做法：`innerHTML=''`、`remove()`、`replaceChildren()`、`v-if` 式重建、把卡片搬到 `<template>` 里、用 `content-visibility` 替代。任何一条都会让原 JS 拿到 `null` 或直接抛错。
- `.table-wrap{max-height:440px;overflow:auto}` 在隐藏板上不计算高度，切回时浏览器重排一次即可，不影响滚动条位置以外的任何状态。

**新增的 1 个独立 `<script>`（展示层，唯一允许新增的脚本）**只做三件事，不碰业务：
1. tab 点击 / `←` `→` `Home` `End` 键切换 `.is-active` 与 `hidden`，同步 `aria-selected`；tab 用 `role="tablist"` + `role="tab"` + `aria-controls`。
2. `history.replaceState(null,'','#'+id)` 记住当前组（**用 replaceState 不用 `location.hash=`，避免触发浏览器锚点滚动**）；初始化读 `location.hash`，缺省 `#run`。
3. 展示层字符映射（见 §8.2），只改文本节点与 `title` 属性。

> **脚本位置是硬约束**：必须放在**原第 2 个 `<script>`（源文件 L794 `</script>`）之后、`</body>`（L795）之前**。`.design/apply_ui.py` L35 校验 `ns[:len(bs)] == bs`（新页面的前 N 个 script 块必须逐字等于原 N 个）；若把新脚本放进 `<head>` 或插在两块原脚本之间，写回会被中止。`.design/verify_ui.py` L48 另限「新增 script 块不超过 1 个」。

**无动效切换**：切组 = 瞬时 `display` 交换，不做淡入、不做滑入（继承 `dense-tiles` 的「无过渡」；本页 MOTION_INTENSITY=1）。`prefers-reduced-motion: reduce` 下全站 transition/animation 置 `none`。

### 1.4 与原脚本共存的完整边界

- `<script>` 两块（L232-244 错误处理、L245-794 主逻辑）**逐字节不动**；`DEFAULT_ROUTING` 的 JSON（L250）一字不改——`apply_ui.py` L49-55 会**从 `gateway.py` 读出真值**，再到新页面里反查这段 JSON，命中后才把它替换成 `{{ROUTING_DEFAULT}}`。
- 所有 **DOM id / onclick 函数名 / API 路径** 保留（清单由 `verify_ui.py` 自动对账）。卡片只**移动**进 `<section>`，不复制、不改名、不包第二层 `.card`。
- **交付文件 `admin_new.html` 保留真实值，不写占位符**：`v1.4.4</span>` 与 L250 的 `DEFAULT_ROUTING` JSON 原样留在交付文件里。`{{VERSION}}` / `{{ROUTING_DEFAULT}}` 是 `gateway.py` 里 Python 字符串格式化需要的**目标形态**，由 `apply_ui.py` L52-57 在**写回 `gateway.py` 时**反向生成（`new = new.replace(routing_json, "{{ROUTING_DEFAULT}}")` + `re.sub(r"v1\.4\.4(?=</span>)", "v{{VERSION}}", new)`），不是交付前要填的东西。若预先写进 `admin_new.html`，L53-54 的 `routing_json not in new` 会直接 `die("默认路由 JSON 不在新页面中")`，写回中止。
- 用户硬约束复述：**不隐藏、不遮罩、不折叠成摘要**。分组是「换一块板看」，不是「把内容收起来」；每一块板上的信息量与原来完全一致，只是不再 10 块同时压在一屏。

---

## 2. 配色九槽（含对比度实测值）

### 2.1 九槽表（WCAG 2.1 相对亮度公式实算，非估计）

| # | 槽 | Token | Hex | 用在哪 | 对比度实测 |
|:--:|---|---|---|---|---|
| 1 | **主色** | `--accent` | `#2563EB` | 主按钮底、当前 tab 下边线、链接、选中态（**不含进度条**，见 §2.4） | 对白面 **5.17:1**（作文字合格）；对 bg 4.77:1 |
| 2 | **主色上文字** | `--accent-on` | `#FFFFFF` | 主按钮字 | 对 `#2563EB` **5.17:1** ✅ |
| 3 | **背景** | `--bg` | `#F4F6F9` | body / 画布（浅冷灰，非纯白、非米黄） | 对 `--surface` 1.08:1（一档明度差） |
| 4 | **表面** | `--surface` | `#FFFFFF` | 卡片、板、顶栏、表格底 | 正文对其 **16.40:1** |
| 5 | **正文** | `--ink` | `#16202E` | 所有正文、数字、卡片标题、表头 | 对 bg **15.15:1** / 对 surface 16.40:1 / 对 raised 14.46:1 ✅✅✅ |
| 6 | **次要** | `--muted` | `#5B6675` | 标签、时间戳、表头、说明、mono 端点 | 对 bg **5.38:1** / 对 surface 5.83:1 / 对 raised 5.14:1 ✅✅✅ |
| 7 | **危险** | `--danger` | `#B42318` | 删除/重置按钮文字、错误提示、≥90% 额度段 | 对 surface **6.57:1** / 对 bg 6.07:1 ✅ |
| 8 | **焦点环** | `--focus` | `#2563EB`（2px 实环）+ `rgba(37,99,235,.18)`（外侧 2px 柔环） | 所有 `:focus-visible` | 实环对白面 **5.17:1**（≥3:1 非文字对比度，WCAG 1.4.11）✅ |
| 9 | **三级面** | `--raised` | `#EDF1F6` | 内嵌区、表头底、进度轨、tag 底、说明条底 | 对 `--surface` **1.13:1**；正文对其 14.46:1 ✅ |

辅助固定值（不计入九槽）：`--border #DDE3EC`（对 surface 1.29:1，作 1px 结构线）；`--log-bg #0F172A` / `--log-fg #E2E8F0`（**14.48:1**，沿用源文件 L47 的深色终端，不新造）；`--accent-hover #1D4ED8`（对白面 6.70:1）；`--accent-soft #E8F0FE`；`--ink-2 #3A4657`（对 bg 8.84:1，次级墨，只用于大段说明）。

**为什么主色是 `#2563EB`**：它是源文件 L8 的既有 VI 蓝，**从项目继承，不是从训练数据挑的 blue-600**。`app-screens.md §十`说强调色「只上数据 / 只上操作 / 完全没有」，本页取「只上数据 + 一个主操作」；`design-tells.md §三之四` 的品牌既有色例外在此登记（谁认的：用户既有界面；何时复审：换 VI 时）。**不换成紫、不换成绿、不引入渐变。**

### 2.2 语义四件套（缺一件前端就会自创「看着还行」的褐色）

提示条 / 状态 chip 一律「**浅底 ≈10% 透明 + 同色深一档文字**」，全部实算 ≥4.5:1：

| 语义 | 深字 | 浅底 | 深字/浅底 | 浅底/白面 | 用在哪 |
|---|---|---|---|---|---|
| `info` 蓝 | `#1D4ED8` | `#E8F0FE` | **5.85:1** | 1.15:1 | `code`、路由说明、一般提示 |
| `warning` 琥珀 | `#8A5A00` | `#FBF0DA` | **5.24:1** | 1.13:1 | 额度 70–89%、未配置 Zen Cookie |
| `danger` 红 | `#B42318` | `#FDECEA` | **5.75:1** | 1.14:1 | 额度 ≥90%、测试失败、删除确认 |
| `success` 绿 | `#1F7A4D` | `#E7F4EC` | **4.70:1** | 1.13:1 | 运行中徽章、测试通过、.tag.ok |

替换源文件里那三套互不搭界的浅底（`#dcfce7 / #fee2e2 / #fef3c7 / #e0f2fe / #eef2ff` 共 5 个来源），统一到这四色。**颜色表达好坏、不表达涨跌**（`app-screens.md §六`）：额度升高是坏事，用 warn/danger；Token 增长是中性，用 `--ink`。

### 2.3 完整 `:root`（整块粘贴，五段齐全，不许只抄 `--bg` 和 `--accent`）

以 `theme-presets.md` 的变量表为骨架（三级明度 / 墨三档 / 强调+派生 / 语义四色 / 阴影三档 / 焦点环 / 动效曲线，**一段不少**），选浅中性骨架、只把 `--accent` 定为产品既有 `#2563EB`；`--bg`/`--surface` 按本页「白卡浮在浅灰底」这一既有事实取值，不翻转成米黄（避开 `design-tells.md §三之二` 第三波 AI 味的奶油底）。

```css
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
  --elev-raised:0 8px 28px rgba(22,32,46,.16);   /* 只给 modal / toast / 下拉 */
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
```

### 2.4 面积与禁用

`底色 70% ｜ 墨与灰 25% ｜ 强调色 ≤5%`（`design-voice.md §五`）。`--accent` 每屏最多 **2 处可见**：当前 tab 下边线 + 主按钮（或唯一主数字）。深色只出现在日志终端一块（12 栏通栏）。

**进度条用数据色，不用 `--accent`**（前端实现反证后修订，见 §14）：`.progress>i{background:var(--ink-2)}`（正常段）、`.progress.warn>i{background:var(--warn)}`（70–89%）、`.progress.danger>i{background:var(--danger)}`（≥90%）。理由：`--accent` 在本页的语义是「当前项 / 主操作」，刷到 3 条进度条上会与 tab 选中态混淆，且突破每屏 ≤2 处的配额；`app-screens.md §七`「一个色相、状态色只给状态」也要求数据条保持墨色阶、只有告警档才上语义色。
**硬禁**：紫→粉渐变、任何 `linear-gradient` 作背景/按钮/文字、发光 `box-shadow`、毛玻璃 `backdrop-filter`、静态卡片阴影、纯黑 `#000` / 纯灰 `#808080`、组件里裸写 hex（唯一例外 `#fff` `#000`，本页连这两个都不用）。

---

## 3. 八档字号（+ 字体系统）

### 3.1 全站只允许这 8 个字号；**禁止 15 / 17 / 18 / 19 / 21 / 22px**

| 档 | Token | px | 行高 | 字重 | 用在哪 | 收编原页面的 |
|:--:|---|:--:|:--:|:--:|---|---|
| 1 | `--fs-1` | **11** | 1.5 | 400/500 | `.ver` 版本角标、表头、`.quota-text`、时间戳 | 原 11 |
| 2 | `--fs-2` | **12** | 1.5 | 400 | `.table-wrap` 单元格、`.tag`、`.hint`、`.quota-text`、mono ID / 端点 / URL | 原 12 |
| 3 | `--fs-3` | **13** | 1.5 | 500 | `.sm` 按钮、input/textarea、`.empty`、`.hint-inline`、小节标题「按厂商/按 Provider/按模型」 | 原 13 |
| 4 | `--fs-4` | **14** | **1.7** | 400 | **基准**：正文、`.card h2`、`.provider-name`、列表主行、说明文字 | 原 14 |
| 5 | `--fs-5` | **16** | 1.5 | 500 | 分组屏标题 `h1`、`.field label`、次级强调、header 产品名 | 原 15 / 17 |
| 6 | `--fs-6` | **20** | 1.3 | 500 | 次级数值（`.quota-card:not(:first-child) .value`）、面板小标题 | 原 20 / 22 |
| 7 | `--fs-7` | **24** | 1.3 | 500 | 上游板首个 Provider 名、次级大数 | 原 22 → 24 |
| 8 | `--fs-8` | **32** | 1.3 | 500 | **每屏唯一主角数字**；同时是全站标题与数字上限 | 原 22 → 32 |

**单屏 ≤5 档**（守「字号 ≤5 档」硬规则，同时满足 8 档全集要求）：

| 板 | 实际引用档位 | 数 |
|---|---|:--:|
| 运行台 | 11 / 12 / 14 / 20 / 32 | 5 |
| 用量 | 11 / 12 / 14 / 20 / 32 | 5 |
| 上游 | 12 / 13 / 14 / 16 / 24 | 5 |
| 规则 | 12 / 13 / 14 / 16 / 20 | 5 |

**主角数字 ≤32px、任何数字不得 ≥48px、任何标题不得 >32px**（`app-screens.md §十 / §十三`）。标题与正文比例 32:14 ≈ 2.3，配合色深与留白建立层级；后台工具不追营销页的 3 倍比。

### 3.2 中文字体栈、字重、行高、字距

- **字体家族 ≤2**：`--font-ui`（系统无衬线，含 5 个中文字体，`visual-defect-taxonomy.md §二.8`）+ `--font-mono`（只给 ID / 端点 / key / JSON / 日志 / 数字）。
- **无外部字体是用户硬约束，不是默认**：不引 CDN、不 `@import`、不 `@font-face`。`app-screens.md §三.2`「字体真的加载了」在此以系统栈满足，登记为约束下的唯一解。
- **字重只有 400 / 500 两档**。源文件里所有 `font-weight:600` 一律降到 500；层级靠字号和色深，不靠加粗（`ui-judgment.md` 五条红线的第 1 条）。
- **中文行高**：正文 **1.7**、标题/大数字 **1.3**、字距 **0**（不许负字距）。大字号没有例外。英文全大写不存在于本页，故无 0.06–0.1em 字距需求。
- **所有数字等宽**：`font-variant-numeric:tabular-nums`（源文件已有，保留），列内右对齐（`.table-wrap td.num` 已右对齐）。

### 3.3 行内 `font-size` 清零（与 `verify_ui.py` 对账）

源文件共 7 处 `style="…font-size…"`：
- **可改（markup，L188 / L193 / L197）**：三个 `<h2 style="font-size:13px">按厂商 / 按 Provider / 按模型</h2>` → 改为 `.sub-h{font-size:var(--fs-3)}`，行内 style 删净。
- **冻结（在 `<script>` 模板字符串内，L452-455）**：`render()` 生成 4 个 `.stat .value` 的行内字号。**不许改脚本字节**；用 CSS 覆盖并在校验器侧豁免（见 §11 阻塞项 B）：
  ```css
  #stats .stat:nth-child(-n+3) .value{font-size:var(--fs-6)}      /* 前 3 个计数 */
  #stats .stat:nth-child(n+4) .value{font-family:var(--font-mono)}
  #stats .stat:nth-child(-n+5) .value{font-size:var(--fs-4)!important}
  #stats .stat:nth-child(n+6) .value{font-size:var(--fs-2)!important}
  ```
  原脚本想给的 14 / 14 / 12 / 12 视觉结果**完全保留**，只是值改从 token 出。

### 3.4 三层交汇（design-choice）

UX（§1 分组与状态）／UI（§2 §3 §4 token）／UE（§9 反馈与无障碍）唯一交汇点是 `.design/design-choice.json`（库 id 已核对为合法值）：

```json
{
  "library":"1.0.0","product":"聚合网关管理端",
  "style":"vercel-mono","styleContrast":"terminal",
  "palette":"slate-light","paletteDark":"slate-dark","typePair":"jetbrains-inter",
  "layouts":["app-shell-tab","dashboard-grid","stat-band","list-detail","table-dense","settings-page"],
  "uxRules":["nav-01","nav-06","nav-03","form-01","nav-02"]
}
```
`palette: slate-light` 提供**三级明度与语义四色骨架**；`--accent` 按 §2.1 登记为品牌既有色 `#2563EB`（不是库里的 `#63707e`）——这是有理由的偏离，不是没做决策。`paletteDark` 只作登记，**本页不实现深色主题**（IDE 是 light，用户明确页面本来就是浅色）。

---

## 4. 间距刻度 + 容器宽（+ 圆角 / 层级）

### 4.1 间距刻度：`4 / 8 / 12 / 16 / 24 / 32 / 48 / 72` px（8 档，全为 4 的倍数）

`padding / margin / gap` **只准取这 8 个值**。禁止 `5 6 7 10 14 18 20 22 30` 等非刻度值——源文件里这些值到处都是（`padding:14px 24px`、`padding:18px 20px`、`margin-top:10px`、`gap:6px`、`padding:3px 9px`、`border-radius:10px/14px`），**全部收编**。

**组件内只用一个间距值**（`app-screens.md §五`，横竖一致）：`.card{padding:24px}`；卡片标题到内容 `16px`；`.field` 之间 `16px`；`label` 到 input `8px`。

### 4.2 容器宽与栅格

| 项 | 值 | 理由 |
|---|---|---|
| **主容器宽** | **`--container:1120px`（全站唯一值，无例外节）** | 源 `max-width:1200px` 配 10 卡平铺显得散；收窄到 `app-screens.md §五` 实测区间（1120–1312）下沿后，分组后单屏 1–4 张卡刚好填满，**宽屏不空** |
| 画布内边距 | 24px（桌面）／16px（<640） | 在刻度上；所有板共享**同一条左沿、同一条右沿** |
| 栅格 | 桌面 **12 栏** / 沟槽 16px；平板 8 栏 / 16px；手机 4 栏 / 12px | `app-screens.md §五` 栅格算术要闭合：`1120 - 2×24 = 1072`，12 栏 + 11×16 沟槽 |
| 卡片跨度 | 默认 `span 12`；主数字带 `span 6`；额度副卡 `span 3`；Zen `span 6` | 只用 12/6/3 三种跨度，不出现 5/7 栏 |
| 对齐 | `align-items:start`，卡片同排同底边不靠拉伸 | 避免 `panel-void` 与行尾参差（`app-screens.md §五`） |

### 4.3 节间距规则（后台工具专用，不套营销页的 96–240px）

| 位置 | 值 | 说明 |
|---|---|---|
| 分组内卡片之间 | **16px** | `gap`，全站统一 |
| 分组之间（切屏） | **32px** | 板顶留白 |
| 板顶到第一个块 | **24px** | — |
| 主数字带 → 紧凑列表 | **24px** | — |
| 列表 → 深色日志通栏 | **48px** | **每板最多一次**刻意呼吸点，只用在运行台 |
| 表格带内三表之间 | **16px** | 合并计为一节，不算节间 |

理由：`app-screens.md §一` 明写「页面那套规则不完全适用」，`design-voice.md §三` 的 96–112px 节距是 Persuade 页的节奏；本页 VISUAL_DENSITY=7，节距按 `app-screens.md §十一` 的「中密度」档取。

### 4.4 圆角 3 档（按角色查表，不许孤立值）

| 档 | 值 | 用在哪 |
|---|---|---|
| `--r-sm` | **8px** | 按钮、input、textarea、`.tag`、`.badge` / `.ver` 状态 chip |
| `--r-md` | **12px** | 卡片、板、`.table-wrap`、`.provider`、`.log`、进度轨 |
| `--r-lg` | **16px** | 仅弹窗 `.modal` |

禁 `6 / 10 / 14 / 999`；源文件 `.ver` 与 `.badge` 的 `border-radius:999px` 药丸**改为 8px 矩形**——`app-screens.md §十三` 药丸只许出现在行内数值旁，`design-tells.md §三之四` 把「标题上方 pill 徽标」列为硬禁。

### 4.5 z-index 七层（禁 9999 裸值，`visual-defect-taxonomy.md §一.7`）

`--z-base:0` / `--z-dropdown:100` / `--z-sticky:200`（header + sticky 表头）/ `--z-overlay:300`（modal mask）/ `--z-modal:400` / `--z-toast:500` / `--z-tooltip:600`。源文件 L70/L82 的 `z-index:100 / 200` 与 L51 的 `z-index:1` 全部映射到刻度。

---

## 5. 逐节编排（6 节；每节写清「先看到 / 次之 / 版式家族 / 节间距」）

> 版式家族词按 `design-voice.md §三` 六选一：**满幅 / 分栏 / 列表 / 表格 / 图示 / 引用**。**相邻两节不同族**；给每节写一个词，同一个词连着出现两次就是错的。

### §5.0 常驻壳 · 细线 T 形顶栏（家族：列表）
- **先看到**：左沿 24px 起，16px 的产品名「聚合网关管理端」+ 11px 版本角标；紧随 4 个文字 tab「运行台 / 用量 / 上游 / 规则」。当前 tab = `--accent` 2px 下边线 + `--ink` 字；其余 = `--muted`。tab 名全用产品自己的词（「上游 / 规则」），不叫「监控 / 设置」。
- **次之**：右沿状态徽章「运行中」（`--success-soft` 底 + `--success` 字，8px 矩形 chip）+ 3 个全局按钮（重载配置 / 一键测试 / 添加 Provider）。这 3 个动作跨所有板有效，故常驻，不归任何组。
- **版式**：`app-screens.md §五` 壳型 3「细线 T 形」——48px 高顶栏 + 1px 下边线，**无侧栏**（个人自用工具不需要常驻 230px 侧栏占宽）。
- **节距**：顶栏内 `gap:16px`，左右 `padding:24px`；tab 高 **44px**（触控达标），tab 之间 `gap:8px`；`position:sticky;top:0;z-index:200`。
- 手机（<640）：顶栏是**三行**，不是两行 —— 第 1 行产品名 + 版本角标；第 2 行 4 个 tab **四等分**、每格 ≥44px 高；第 3 行状态徽章 + 3 个全局按钮（按钮组允许再折一行）。原稿写「产品名与 3 个按钮收成一行 + tab 第二行」，真机 390 下 3 个按钮会把 tab 挤到截断，故改为三行（`frontend 实测修正`）。不横滚、不收汉堡菜单（4 项 ≤5，`app-screens.md §十二`）；`position:sticky` 保留，三行总高约 132px，仍不占满首屏。
- **手机栅格防撑破（必做）**：`.view .card, .card > *, .stat, .quota-card, .quota-cell, .quota-line, .quota-text, .tag, .table-wrap { min-width:0 }`，并且 `code, .hint-inline, .field .hint, .quota-text, .provider-meta, .log { overflow-wrap:anywhere }`。原因：CSS Grid 子项默认 `min-width:auto`，遇到不可断的 `<code>`（端点 URL、模型 ID、`/console/api/billing/status`）会把整行撑出视口——第一版 390 CSS 视口实测 `docScrollW=433`（超出 43px），加这两条后 `390/390`（见 §7 / §14）。

### §5.1 运行台（默认板）· 家族序列：满幅 → 分栏 → 列表 → 满幅
- **先看到（唯一主角）**：`#quotaSummary .quota-card:first-child`（5 小时滚动额度）单独占 **6 栏**，`.value` 由 20px 提到 **32px**，色用 `--ink`（不是蓝——蓝留给当前 tab 与主按钮）；其下 12px `--muted` 写「重置 … · 按当前速度重置前不会用完」（`app-screens.md §四.2`「答案旁边给证据」）。这是全板唯一 32px。
- **次之**：另两张额度卡（本周 / 本月）各占 3 栏，`.value` 20px；三张卡**改成 1px 竖线分列**，不是三张 `--raised` 灰底小卡（见 §10 落地决定）。
- **再次**：`#stats` 的 7 项从 7 张灰底小卡改成**一行 1px 竖线分列的紧凑列表**，占满 12 栏；标签 12px `--muted`、数值 14px `--ink`、4 个端点用 12px mono 可换行。位置在主数字下方 **24px**——静态配置快照降级为第二视线，但**一字不删**。
- **再次**：`#zenSummary` 余额占 6 栏，20px 数值；配置 Cookie 的 `<details>` 原位保留（控件贴着答案，见 §1.2）。
- **最后**：`#log` 深色终端 **12 栏满幅通栏**，高 320px，`--log-bg` 是整板唯一反白块；距上一块 **48px**（本板唯一呼吸点）。它是这个产品最像自己的东西，也是「一眼知道这是什么」的证据。
- **节距**：卡内 24px；卡间 16px；主数字→列表 24px；列表→日志 48px。四节家族依次「满幅 / 分栏 / 列表 / 满幅」——首尾同族但不相邻，合规。

### §5.2 用量（独占板）· 家族序列：分栏 → 表格
- **先看到**：`#usageTotals` 的 12 个 `.stat` 改成 **4 列 × 3 行 1px 竖线分列**（不是 12 张卡）；第 6 项「总 Token」`.value` 提到 **32px**（本板唯一主角），其余 20px。
- **次之**：`#liveTps` 一行 12px `--muted`（近 60 秒实时 TPS / 按模型），紧接主数字下方 8px。
- **再次**：三张表「按厂商 / 按 Provider / 按模型」各占 12 栏、纵向排布，合并计为**一节「表格带」**：表头 11px `--muted` 句首大写、无底色带、无斑马纹、数字右对齐等宽（`app-screens.md §八`）；`.table-wrap{overflow:auto;max-height:440px}` 保留，身份列（模型/厂商/Provider）**永不被截**。
- **节距**：数字带 → 表格带 **32px**；三表之间 16px；每表标题上方 16px、下方 8px。两节不同族。
- **不做的**：不把三张表做成三个不同花样的版式去凑「家族配额」——数据形状相同，硬做差异就是装饰。此处如实合并为一节并登记。

### §5.3 上游 · 家族序列：列表 → 表格
- **先看到**：板顶 24px 处一行 16px 说明「已接 N 个上游 · M 个模型」（复述 `#stats` 已有数字，不新增数据源）；`#providers` 的**首个 `.provider` 行**放大为 12 栏宽行，名称 24px，是本板唯一主角（「一个能点的东西」）。
- **次之**：其余 Provider 行 14px 常规，行间 12px；每个 `.tag` 保留可点击测试（原 onclick 不动），tag 底改 `--raised`、8px 圆角。
- **再次**：`#modelTable` 12 栏表格，`.table-wrap` 横向滚动；9 列中「模型」身份列固定占比 1/3，不被截。
- **节距**：板顶 24px；provider 行间 12px；`#providers` → `#modelTable` **32px**。两节不同族。

### §5.4 规则 · 家族序列：列表 → 表单
- **先看到**：智能路由卡内，3 段「智能别名 / 故障转移 / 冷却」压成**定义列表**（12px `--muted` 标签 + 14px 正文），不再用 `<b>` 加粗当层级。
- **次之（本板主角）**：`#routingJson` 编辑器 12 栏、`rows="12"` 保留、13px `--font-mono`、24px padding、`--r-sm`，它是唯一主操作区，给足宽度与高度。
- **再次**：保存路由 / 恢复默认按钮组（16px 间距，按钮高 44px）；`#routingHealth` 冷却状态用 12px `--muted`。
- **最后**：客户端接入密钥卡，与路由卡间隔 **32px**；鉴权开关 28×16（开启色用 `--success`，**不用品牌蓝**，`app-screens.md §九`）、`authKeys` textarea、3 个按钮。
- **节距**：卡内 24px；字段间 16px；label→input 8px；两卡间 32px。

### §5.5 浮层与状态 · 家族：浮层（不计入相邻族判定）
- `#modalMask` 保持 `<main>` 外原位；`.modal` `--r-lg:16px`、宽 640px、`--elev-raised`（**唯一允许投影的浮层**，`border:0`）；`--z-modal:400`。
- `#toast` 全局覆盖层 `--z-toast:500`，瞬时结果的反馈挂全局，不挂列表组件内（避免删除/保存后组件卸载导致 toast 消失）。
- 三验：可关闭（点遮罩 / 取消 / Esc）、视口内不溢出（`max-height:90vh;overflow:auto`）、不被 `overflow` 裁剪（`.view` 上**不加** `overflow:hidden`，否则会裁掉 sticky 表头与下拉）。

---

## 6. 两处「自己定的决定」（不是默认来的；换颜色/换字体不算）

### 决定 A：按「刷新频率 + 内容体积」分 4/1/2/2，并让两张胖卡各占一屏
- **我定了什么**：不按卡片数均分（3/3/2/2）、也不按名词分类（把所有「监控」塞一组），而是 **4 / 1 / 2 / 2**；`Token 用量` 和 `智能路由` 各自独占一板。依据是源文件 `L792-793` 的 60s / 5s 两套轮询，以及「12 个 KPI + 3 张 15 列表」「12 行 JSON + 3 段说明」两个明显更大的内容体积。
- **为什么不是默认**：默认动作是「把 10 张卡按标题归成 3 堆、每堆还是平铺」。那只是把 10 张变成 4 张同时可见，用户说的「太杂」没解决，而且胖卡会把同组瘦卡挤到折叠下方——那就等于用折叠降噪，踩用户硬约束。
- **代价（如实写）**：一次只能看一板，跨板对照要切一次。缓解：顶部 tab 常驻 + `location.hash` 记住位置 + 切回即见最新数据（不重取）。**没有隐藏或减少任何信息。**

### 决定 B：默认板不是「总览」，首屏主角定为「5 小时滚动额度 %」而不是「Providers 数量」
- **我定了什么**：默认展示 **① 运行台**；把源页面排第一、且 7 个 `.stat .value` 一律 22px 的静态配置快照（Providers 数 / 模型总数 / 端点 / 监听）**整体降级为 14px 一行紧凑列表**，放在主数字下方 24px；把唯一的 32px 留给每 60s 会变的「5 小时滚动额度」。
- **为什么不是默认**：原页面把「配置快照」当主角，是因为它恰好在 DOM 里排第一，不是因为它重要。对个人自用网关，一天里真正会变、真正决定「我的 agent 还能不能继续跑」的是**滚动额度**和**刚才有没有报错**，不是接了几个 Provider。
- **代价（如实写）**：端点地址不再处于第一视线，但仍完整印在同一板上（12px mono、可换行、可复制），**没有折叠、没有遮罩、没有删字段**；用户「不担心数据泄露」的约束因此被满足而不是被绕过。

> 两处都不是换色/换字体：A 改的是信息架构与切换单位，B 改的是默认状态与视觉层级的主角归属。两者共同回答「页面太杂」——**不是删东西，是决定谁先被看到**。

---

## 7. 五条自检（逐条写清怎么过的）

| # | 判据 | 怎么过的（可量） |
|:--:|---|---|
| 1 | **首屏只有一个主角** | 每板**恰好一处 32px**（运行台=5h 额度 %，用量=总 Token，上游=首个 Provider 名 24px，规则=JSON 编辑器），其余数值 ≤20px、标题 ≤16px。运行台额外把 `.quota-card:not(:first-child) .value` 从 20px 保持、把 `#stats` 从 7 张卡降到 14px 一行。眯眼判据（`design-voice.md §九.1`）：糊成灰块后只剩「一个大数 + 一条深色通栏带」两块，不是一片中景 |
| 2 | **一眼知道这是什么** | 顶栏常驻 16px「聚合网关管理端」+ 状态徽章 + 4 个产品自己的词（运行台/用量/上游/规则）；主数字旁副文「5 小时滚动额度 · 重置 14:00 · 按当前速度重置前不会用完」。**盖住产品名只读副文**，仍能判断这是看 LLM 上游额度的控制台，不是电商后台。`app-screens.md §三.5`「用行业自己的词」：额度、上游、代理、冷却、Token、TPS 全是原页面已有词，一个没造 |
| 3 | **看得出层级** | 单屏 5 档字号（11/12/14/20/32 或 12/13/14/16/24），最大:最小 = 2.9:1；三级明度（`#F4F6F9` / `#FFFFFF` / `#EDF1F6`）+ 一整块 `#0F172A`；`--accent` 面积 ≤5%，每屏 ≤2 处。缩到只剩灰块仍有 2–3 个亮度不同的大块；数左沿（`design-voice.md §九.3`）全站只有 **1 个 x 位置**（`--container:1120px` 的左沿），卡片标题、数字、表格第一列、tab 文字全部对齐 |
| 4 | **不像模板** | 无居中 hero、无三张同款卡（`.stat` 已从卡改成竖线分列）、无 01/02/03 编号、无 eyebrow/药丸标题标签、无渐变/发光/毛玻璃、无 emoji 功能图标。版式家族：运行台 4 族、用量 2 族、上游 2 族、规则 2 族，相邻不同族。签名特征（`design-tells.md §三`）**命中 0 条刻意使用**；「同款卡片成排」仅剩同级的额度副卡，且已改为 1px 竖线，按 `app-screens.md §十三` 属放宽项不算签名。**遮住 logo 测试**：深色日志终端 + 额度进度条 + 模型标签墙 + 按模型 Token 表，这四件只属于 LLM 网关；换成「热水器控制器」或「电商后台」全都不成立 |
| 5 | **宽屏不空、手机不挤** | 容器从 1200 收到 **1120**，分组后单屏 1–4 张卡，桌面 12 栏 / 16px 沟槽铺满不露半屏空白；平板（≤1024）8 栏；<640 单列、**顶栏三行**、tab 四等分每格 ≥44px、`.stats` 两列、12 个用量 KPI 两列、表格走 `.table-wrap{overflow:auto}` 且身份列不截（`app-screens.md §十二`）。触控：所有按钮/tab/tag `min-height:44px`、间距 ≥8px；`:focus-visible` 2px 实环 5.17:1；`env(safe-area-inset-bottom)` 给 toast 与底部按钮。**真渲染实测（非纸面推演）**：`visual-defect-taxonomy.md §五` 的三档改为 **390 / 834 / 1440**（390 是前端实机 CSS 视口）。第一版 390 曾测得 `docScrollW=433`（超出 43px，Grid 子项 `min-width:auto` 被不可断 `<code>` 撑破），按 §5.0 补 `min-width:0` + `overflow-wrap:anywhere` 后复测 **390 / 390**，无横向滚动、无文字截断、sticky 表头不被裁；1440 与 834 亦无溢出。截图：`.design/shot_1440.png` / `shot_834.png` / `shot_390_iframe.png` / `shot_view_*.png` |

---

## 8. 图标系统（禁 emoji）

### 8.1 锁定方案
**一套内联 SVG、1.5px 描边、`currentColor`、无 CDN、无外部图片**；尺寸只有 **16px（行内）/ 20px（按钮内）/ 24px（独立）** 三档。全项目不混第二套、不用字体图标、不用 emoji。
- header 产品标记：16px「三入一出」网关节点几何符号（三条线汇聚到一点），替代 `⚡`。
- 「一键测试」按钮：16px 闪电轮廓 `<path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z"/>`，替代 `⚡`。
- 状态徽章圆点：保留现有 7px `::before` 圆点（几何符号，不是图标）。
- `+ 添加 Provider` 的 `+` 是几何符号，保留（用户允许的「纯文字/几何符号」路径）。

### 8.2 冻结脚本内的 emoji：展示层字符映射（不修改原 script 一个字节）

源文件共 22 个 emoji 命中 `verify_ui.py` 的正则，其中 **20 个在冻结的 `<script>` 内**（L235/242/386/390/391/394/470/478/755/767/770/773/788 等），2 个 `⚡` 在可改的 `<header>`（L89/L93）。用户允许「换成内联 SVG 或纯文字/几何符号」，所以脚本注入的部分走**纯文字映射**，由 §1.3 那个新增展示层 script 完成：

| 字符 | 出现位置 | 映射为 | 说明 |
|---|---|---|---|
| `⚡` | header L89 / L93（markup） | 内联 SVG | 可改区，直接换 |
| `🎁` | 模型标签 / 模型能力表 | `·免` | 免费模型标记，纯文字 |
| `🌐` | 模型标签 / 模型能力表 | `·代理` / `代理` | 出口标记，纯文字 |
| `✅` | 表格布尔 / `el.title` / toast / alert | `是` / `通` | 布尔与测试结果 |
| `❌` | 同上 | `否` / `失败` | 同上 |
| `⏳` | `.tag.testing` 测试中 | `…` | 加载中几何符号 |
| `⚠` | 脚本错误 / Promise 错误提示 | `!` | 纯几何符号 |

实现方式：`DOMContentLoaded` 跑一遍 + `MutationObserver` 观察 `#providers / #modelTable / #log / #toast / #usageVendors / #usageProviders / #usageModels` 的 `childList` 与 `title` 属性，**只替换文本节点，绝不改 id / onclick / class / 数据结构**；带 guard 防自触。原 `testModel()` 缓存 `orig = el.innerHTML` 再恢复，映射后 `orig` 里已是纯文字，恢复幂等，不影响 `.tag.testing/.ok/.fail` 状态机。

**已知残留（需 team-lead 裁决，见 §11）**：`testProvider()` L755 的 `alert('✅ …')` 走浏览器原生对话框，不在 DOM 内；覆盖 `window.alert` 会改动业务行为，故**不改**，登记为残留。`el.title` 里的 `✅/❌` 由上面的 `MutationObserver(attributes)` 一并处理。

**P0 自查**：本设计零 emoji 功能图标、零紫粉渐变、零 `linear-gradient` 装饰、零「Welcome to / Lorem ipsum」、零编造指标（所有数字来自原 JS 的真实接口）。

---

## 9. UE：状态、反馈、可访问性、动效

- **五态**（`app-screens.md §十一` / `visual-defect-taxonomy.md §一.6`）：原页面已有 `.tag.testing`（加载）/ `.tag.ok .tag.fail`（成功/失败）/ `.empty`（空）/ `.toast`（瞬时结果）/ `#quotaError`（错误），**全部保留不改**。新增 tab 自身补齐 5 态：default / hover（150ms 变 `--ink`）/ `:focus-visible`（`--focus` 实环）/ active（当前组）/ disabled（无，本页不禁用 tab）。按钮统一补 `:disabled{opacity:.55;cursor:not-allowed}` 且禁用态对比度不作文字依赖。
- **反馈形态**：瞬时结果（保存/复制/重载/测试完成）→ **全局 Toast**（`--z-toast:500`，不挂列表组件）；绑定表面的状态（额度读取失败、用量读取失败、冷却中、测试失败）→ **就地提示**，写在失败的那块里，不用全屏横幅（`app-screens.md §十一`）。失败时用户已填的表单内容**保留在原 DOM**（不重绘整卡），可重试。
- **加载形态**：一页只有一种——列表/表格用**最终布局的灰色条**（尺寸与真实内容一致），不用闪烁渐变、不跳版；页面级不做 spinner。轮询刷新不显示加载态（数据本就在，静默替换）。
- **浮层清单**：只有 `#modalMask`（`--z-modal:400`，portal 不需要——它本来就在 `<main>` 外）、`#toast`（`--z-toast:500`）、`title` 工具提示（`--z-tooltip:600`）。**无下拉菜单**，故无「贴边翻转」问题；若前端后续加，必须验证视口内翻转有终态、可 Esc 关闭、不被 `.view` 的 overflow 裁剪（§5.5 已规定 `.view` 不加 `overflow:hidden`）。
- **键盘 / 焦点顺序**：Tab 路线 = 顶栏 4 个 tab → 3 个全局按钮 → 当前板内按 DOM 顺序的可交互元素。`←/→` 在 tablist 内换组且焦点留在 tablist；`Home/End` 到首尾。弹窗打开焦点进 `#fName`，关闭（Esc / 点遮罩 / 取消）回到触发按钮。所有图标按钮有 `aria-label`。
- **错误与必填**：除颜色外必有第二信号（文字或 `!` 几何符）；错误文案放字段下方 8px，不遮内容；`aria-invalid="true"` + `aria-describedby`。用户可见文案只说人话，不出现「403 / JWT / 同步中」这类术语（沿用原页面已有的人话文案，不新造技术腔）。
- **动效**：仅 3 处——按钮 hover 150ms、tab 与链接颜色过渡 150ms、toast 进出 200ms；切组**无动效**。`@media (prefers-reduced-motion: reduce){*{animation:none!important;transition:none!important}}`。
- **a11y 数值**：正文 15.15:1、次要 5.38:1、主色上文字 5.17:1、语义四色 4.70–5.85:1、焦点环 5.17:1、日志 14.48:1 —— **全部 ≥4.5:1 / ≥3:1（非文字）**。触控 44×44px、按钮间距 8px。

---

## 10. 知识库落地决定（说清「按哪条改了哪个值」）

> **主落地决定（可验证的一条）**：按 `app-screens.md §六`「**默认：一个容器用 1px 竖线分成等宽列，不是四张卡**」，把 `.stat` / `.quota-card` 从
> `background:var(--bg); border-radius:10px; padding:14px 16px`
> 改成
> `background:transparent; border-radius:0; border-left:1px solid var(--border); padding:4px 16px`。
> **具体改的值**：`border-radius: 10px → 0`、`background: var(--bg) → transparent`、新增 `border-left:1px`、`padding: 14px 16px → 4px 16px`（两个值 4 / 16 都落回 §4.1 刻度）。**修订**：初稿写 `4px 20px`，但 20px 不在 §4.1 的八档刻度里，前端按「间距只从刻度取」收编为 `4px 16px`，契约据此对齐（见 §14）。
> **为什么是这一条**：「10 张卡平铺」这个症状在卡片内部被复制了两遍——`#stats` 里 7 张灰底小卡、`#usageTotals` 里 12 张灰底小卡、`#quotaSummary` 里 3 张灰底小卡，共 **22 张嵌套卡**。不掐掉这一层，分了组仍然是「一组嵌套卡」。改成竖线分列后 KPI 变成一行字，32px 主数字才立得住（`design-tells.md §三之四`「卡片里再套卡片永远错」）。

其余落地条目：

| 出处 | 按哪条 | 改了哪个值 |
|---|---|---|
| `app-screens.md §十 / §十三` | 一屏 4–9 个字号；禁 >32px 标题、≥48px 数字 | 全站 8 档令牌、单屏 ≤5 档、主角数字封顶 **32px**、移除原 22px 的「准主角」 |
| `app-screens.md §五` + `visual-defect-taxonomy.md §一.1` | 只用一个间距值；间距只从刻度取 | 全站 8 档（4/8/12/16/24/32/48/72）；`.card padding` 统一 **24px**、卡间 **16px**；源文件的 14/18/10/6/3px 全部收编 |
| `design-voice.md §九.3` | 数左沿：>2 个 x 位置 = 一个个摆上去的 | 全站单一 `--container:1120px`，所有板、标题、数字、表格第一列共享一条左沿 |
| `visual-defect-taxonomy.md §一.7` | z-index 固定七层 | 新增 `--z-*` 七层，替掉源文件 `z-index:1/10/100/200` 的散值 |
| `visual-defect-taxonomy.md §二.8` | 中文字体栈 ≥2 个中文字体 + sans-serif 兜底 | `--font-ui` 显式列 5 个中文字体（源栈只有 2 个） |
| `theme-presets.md §一/§四` | 整块粘贴五段，不许只抄 `--bg`/`--accent`；语义色不许自创 | §2.3 `:root` 五段齐全；源文件 5 套互不搭界的浅底收敛到语义四色 |
| `design-tells.md §三` | 每个视觉决定必须有一句理由；单页 ≤2 个签名特征 | §2.1 主色、§4.2 容器宽、§4.4 圆角、§6 两处决定均带理由；签名特征命中 0 |
| `design-tells.md §三之四` | 药丸徽章 / 标题上方小标签 = 硬禁 | `.ver` / `.badge` 的 `999px` → **8px** 矩形；不新增任何 eyebrow |
| `app-screens.md §九` | 开关开启色用语义绿或近黑，不用品牌色 | `authEnabled` 开启色从 `--accent` 改 `--success` |
| `app-screens.md §八` | 表头 11–14px 常规字重、数字右对齐等宽、无斑马纹 | 表头 **11px** `--muted`；`.table-wrap td.num` 右对齐保留；源文件无斑马纹，不加 |

**没跑的检查不声称通过**：本轮是设计契约产出，未运行 `design_doc_check.py` / `visual_metrics_check.py` 等门禁（它们针对 `admin_new.html` 渲染结果，前端产出后由 QA 跑）。§2 与 §7 的对比度数值是用 WCAG 相对亮度公式逐个实算的，不是引用阈值表。

---

## 11. 交给前端的落地清单 + 两个阻塞项

**落地清单（按顺序）**
1. 复制 `.design/admin_source.html` → `.design/admin_new.html`；**两块 `<script>` 一个字节都不动**。
2. 替换整个 `<style>` 为 §2.3 `:root` + §4 网格 + §3 字号 + §5 各节规则；删除 L188/193/197 三处行内 `font-size`。
3. 改 `<header>`：换掉 2 个 `⚡` 为内联 SVG；加 4 个 tab（`role="tablist"/"tab"` + `aria-controls`）；状态徽章改 8px 矩形 chip；右侧 3 个按钮保留原 onclick。
4. 改 `<main>`：加 4 个 `<section class="view">` 包裹，10 张 `.card` **原样移动**进去；默认 `#view-run` 带 `is-active`，其余带 `hidden`。
5. 在原第 2 个 `<script>` 之后、`</body>` 之前加**唯一 1 个**新 `<script>`：tab 切换 + hash + 字符映射（§1.3 / §8.2）。
6. **交付文件保留真实值**：`v1.4.4</span>` 与 `DEFAULT_ROUTING` 的 JSON 逐字保留，**不要预先写 `{{VERSION}}` / `{{ROUTING_DEFAULT}}`**；这两个占位符由 `apply_ui.py` L52-57 在写回 `gateway.py` 时反向生成（详见 §1.4 与 §14 修订 1）。
7. 跑 `python3 .design/verify_ui.py .design/admin_new.html` → 再跑 `python3 .design/apply_ui.py` 原子写回。
8. **390 / 834 / 1440** 三档真渲染截图，按 §7 五条自检（含 `docScrollW` 实测）；`design-choice.json` 与 `design-tokens.json` 一起交给 QA 对账。

**阻塞项（需 team-lead 先裁决，否则前端永远拿不到全绿）**

- **B1｜`verify_ui.py` 的两条检查与「script 逐字节不变」数学上互斥**。L62-66 的「无 emoji 图标」和 L64-66 的「无行内 style 硬编码字号」都扫**整个 HTML 文件**，而：22 个 emoji 中 **20 个在冻结 `<script>` 内**（L235/242/386/390/391/394/470/478/755/767/770/773/788），7 处行内 `font-size` 中 **4 处在冻结脚本模板字符串内**（L452-455）。原脚本不动 ⇒ 这两项必然 FAIL，任何 `admin_new.html` 都不可能通过。
  **建议修法（改校验工具，不改产品）**：给 `verify_ui.py` 加一个剥离函数，这两项只扫 markup：
  ```python
  def markup_only(html):
      return re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.S | re.I)
  # emojis(markup_only(new)) ; re.search(..., markup_only(new))
  ```
  其余 6 项（script 一致性、id、onclick、API 路径、外链、script 数量）继续扫全文件。渲染层的 emoji 由 §8.2 展示层映射解决，两者合起来才真正满足用户「禁 emoji 图标」。

- **B2｜新 `<script>` 的位置被 `apply_ui.py` 隐式锁死**。L35 `ns[:len(bs)] == bs` 要求新页面的前 2 个 script 块逐字等于原 2 块，因此**新脚本只能追加在第 3 位**（L794 `</script>` 之后、L795 `</body>` 之前）。放进 `<head>` 或插在两块中间都会导致写回中止。

**建议项（不阻塞）**：`apply_ui.py` L52-57 是「从交付文件里的真值反查 → 生成占位符」，不是「校验占位符已填好」。前端改 header 时**保留 `v1.4.4` 这个文本形态与 `</span>` 紧邻关系**（L56 的正则是 `v1\.4\.4(?=</span>)`），否则版本号无法写回；`DEFAULT_ROUTING` 的 JSON 同理不能改一个字节。

---

## 12. Do's and Don'ts

**Do**
- 分组只做 `display` 交换；隐藏的 DOM、id、onclick、数据一个不少。
- 所有值从 §2.3 `:root` / §3 / §4 取；改一个值只动一处。
- KPI 用 1px 竖线分列，不用嵌套小卡；表格数字右对齐等宽、身份列不截。
- 每板只放大一个东西（32px 数字或一个主操作区），其余降级。
- 颜色只上数据、当前项与一个主操作；提示条只用语义四件套。
- 顶栏 4 个 tab 常驻、用产品自己的词；`location.hash` 记住位置。
- 所有按钮 44px 高、`:focus-visible` 2px 实环、`prefers-reduced-motion` 兜底。

**Don't**
- 不用折叠 / 遮罩 / 「查看全部」/ 摘要卡来减少信息——用户明确禁止。
- 不动两块原 `<script>`、不改 `DEFAULT_ROUTING`、不改任何 id / onclick / API 路径。
- 不引入 CDN、外部字体、外部图片、第二套图标库、emoji 图标。
- 不用紫粉渐变、渐变文字、发光、毛玻璃、静态卡片阴影、999px 药丸、eyebrow 小标签、01/02/03 编号。
- 不把 `.view` 设成 `overflow:hidden`（会裁掉 sticky 表头与浮层）。
- 不把三张同形状的用量表硬做成三种花样去凑「版式家族配额」——那是装饰。
- 不用 >32px 标题、≥48px 数字、600/700 字重、负字距、非 4 倍数的间距。
- 不用「现代 / 简洁 / 高级」当验收语；用 §7 的可量判据。

---

## 13. 门禁实测（贴真实输出，没跑的不说「应该没问题」）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 设计方向登记 | `design_choice_check.py --root D:\网关管理器` | **[PASS]** `黑白极简 / 石板灰·浅色 / JetBrains Mono+Inter / 6 种版式家族`；1 条 WARN（字体对含高频默认字体）已在 `design-choice.json → notes.deviations` 登记：用户禁外部字体，实际落系统栈，id 登记的是「等宽给数字、无衬线给界面」这组关系 |
| 设计契约格式 | `design_doc_check.py -f .design/DESIGN.md` | **[PASS]** `ink on bg = 15.15:1` / `muted on bg = 5.38:1` / `on-accent on accent = 5.17:1` / `设计契约可校验：token 齐全、引用可解析、对比度达标`。**不带 `-f` 会报 `DD00 有界面文件但没有 DESIGN.md`**，因为 `CANDS` 只找 `DESIGN.md / docs/ / design/`，不找 `.design/`——是门禁的路径假设与团队产物路径不一致，不是契约缺项；QA 用 `-f` 即可 |
| 禁物与无障碍 | `craft_rules_check.py --root D:\网关管理器` | 扫的是**改造前的 `admin_source.html`**，5 FAIL / 1 WARN，逐条见下表 |

`craft_rules_check` 命中的 5 项恰好是本次要修的东西，不是新缺陷：

| 命中 | 本设计怎么修 |
|---|---|
| CR08 字体家族 8 个 | §3.2 收敛为 `--font-ui` + `--font-mono` 两族；中文字体栈显式列 5 个（`visual-defect-taxonomy.md §二.8`） |
| CR15 无 `prefers-reduced-motion` 兜底 | §9 加 `@media (prefers-reduced-motion: reduce){*{animation:none!important;transition:none!important}}` |
| CR19 默认靛蓝 `#3730a3` | §2.2 把 5 套浅底收敛到语义四色；`.tag` 文字改 `--ink`，靛蓝彻底移除 |
| CR22 `outline:none` 无 `:focus-visible` 替代 | §9 全站 `:focus-visible` 用 `--focus` 2px 实环（对白面 5.17:1） |
| CR21 9 个输入框无可访问名称 | **需 team-lead 裁决**：源文件 `.field label` 有文字但无 `for`，修正要给弹窗区（L207-229，在 `<main>` 外）的 label 加 `for` 或给 input 加 `aria-label`。这落在「只改 `<style>` / `<header>` / `<main>`」允许范围之外。**建议**：把「补 `for` / `aria-label`」明确列为允许的第 4 类改动（不改 id、不改 name、不改值，纯可访问性属性），否则只能登记豁免 |
| CR03 WARN 发丝线 | **寄存器误报**：`app-screens.md §十三` 明确后台工具「放宽 1px 边框与行分隔线」，本页靠 1px 线分栏正是 §10 的主落地决定，不修 |

`design_doc_check.py` / `visual_metrics_check.py` 面向**渲染后的 `admin_new.html`**，前端产出后由 QA 复跑；本轮只交付设计契约，不虚报这两项通过。

---

## 14. 契约修订记录（2026-11-13 第二轮 · 前端实装反证）

> 来源：`mvp-dev-expert-team-frontend` 完成 `admin_new.html`（8 项校验全绿 + 1440/834/390 真渲染）后回传的 4 条。以下均为**契约文本错误或实现反证**，已改契约而非要求前端改代码；改完复跑 `design_doc_check` / `design_choice_check` 无回归。

| # | 原契约 | 事实 | 修订 |
|:--:|---|---|---|
| 1 | §1.4 / §11 步骤 6 写「`{{VERSION}}` / `{{ROUTING_DEFAULT}}` 必须在 `admin_new.html` 中原样还原」 | `apply_ui.py` L52-57 是**反向**的：先从 `gateway.py` 用 `eval` 读出 `DEFAULT_ROUTING` 真值 → `json.dumps` → 在 `new` 里反查并 `replace` 成 `{{ROUTING_DEFAULT}}`，再 `re.sub(r"v1\.4\.4(?=</span>)", "v{{VERSION}}", new)`。交付文件若预写占位符，L53-54 `routing_json not in new` 直接 `die` | §1.4 改为「交付文件保留真实值，占位符在**写回 `gateway.py` 时**由脚本生成」；§11 步骤 6、§11 建议项同步改；`DEFAULT_ROUTING` 逐字保留这条不变 |
| 2 | §10 把 `.stat` 改成 `padding:4px 20px`，并声称「两个值都落回 §4.1 刻度」 | §4.1 八档是 `4/8/12/16/24/32/48/72`，**没有 20**；前端按「间距只从刻度取」落地为 `padding:var(--s-1) var(--s-4)` = `4px 16px` | §10 改为 `4px 16px`，并标注这是修订。竖线分列与主数字 32px 均不受影响（1440 截图已验证） |
| 3 | §5.0 写「手机顶栏两行」；§7 写「375/834/1440 三档无横向滚动」（纸面推演） | 真 390 CSS 视口首版 `docScrollW=433`：Grid 子项 `min-width:auto` 被不可断 `<code>`（端点 URL / 模型 ID / API 路径）撑破；且两行顶栏下 3 个全局按钮会把 4 个 tab 挤压截断 | §5.0 改**三行顶栏** + 新增「手机栅格防撑破」必做两条（`min-width:0` + `overflow-wrap:anywhere`）；§7 第 5 条改为**真渲染实测 390/834/1440**、如实记录首版 433→390/390 的修复过程；§11 步骤 8 同步改档位 |
| 4 | §2.1 把「进度条已用段」列为 `--accent` 用途，与 §2.4「每屏 ≤2 处 `--accent`」自相矛盾 | 前端实现 `.progress>i{background:var(--ink-2)}` / `.warn>i{background:var(--warn)}` / `.danger>i{background:var(--danger)}`。截图里墨色段读成中性数据、琥珀段读成告警，语义正确；若刷蓝会与 tab 选中态混淆并突破 2 处配额 | §2.1 从主色用途中删去进度条；§2.4 新增「进度条用数据色、不用 `--accent`」并给 `app-screens.md §七` 依据。**这是实现比契约更对，改契约对齐实现** |

**未改的**：九槽配色与全部对比度、八档字号、间距/圆角/层级刻度、4 组信息架构与 `display` 切换机制、两处自定决定、五条自检的判据本体、B1/B2 阻塞项、CR21 建议项。前端的 `admin_new.html` 与本契约现在一致。

---

## 签字

- 设计契约：**颜好看（UI/UX）** · 2026-11-13（第二轮修订同日）
- 上游消费：`PROJECT_STATE.md` 目标与硬约束 · `.design/admin_source.html`
- 下游交接：前端（`admin_new.html`）· QA（`verify_ui.py` / `apply_ui.py` / 三档截图）
- 机器可读伴生产物：`.design/design-tokens.json` · `.design/design-choice.json`
- 未决：B1 / B2 待 team-lead 裁决；用户尚未对第一版界面确认（`PROJECT_STATE.md` §待决策）
