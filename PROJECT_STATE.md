# PROJECT_STATE

## 目标
优化 `gateway.py` 内嵌管理端界面并修掉显示/连通问题。用户约束：**不改变原有功能和数据**；依赖与项目一律落 D 盘（C 盘禁装）。

## 判定
- 模式：Operate ｜ 档位：小组 ｜ 形态：系统模式

## 已完成
- `.design/admin_new.html`（1089 行，4 板重构）→ 已写回工作区并同步运行目录
- `.design/DESIGN.md` / `design-tokens.json` / `design-choice.json` —— 设计契约
- `.design/verify_ui.py` / `apply_ui.py` / `sync_live.py` / `probe_real.py` —— 校验、写回、同步、实测
- **用量板修复**：`.table-wrap` 去掉 `max-height:440px`；表格字号 12→11px；padding 12→4px（真实数据实测：按厂商/按 Provider 表 0px 溢出）
- **11-16 连通修复**：根因是 `.workbuddy` 内置 Python 的 `requests/certifi/urllib3/idna` 全部消失 → 出网 TLS 失败。已在 **D 盘**建 `D:\网关管理器\.venv`（requests 2.34.2），计划任务 `WBAggregatorGateway` 的 Action 改为 `D:\网关管理器\.venv\Scripts\pythonw.exe`，工作目录不变；旧进程已杀，新进程 **PID 20100** 正常服务

## 当前状态（2026-11-16）
- 8787 正常：健康 200；**出网实测成功**（glm-5.3-flash 返回 200 + usage）；管理端 4 板 + 4px padding 已生效
- 部署基准：运行目录 `C:\Users\Administrator\WorkBuddy\2026-11-07-17-11-42\gateway.py`（163,787 B，含 `_is_lan_ipv4()`、provider `Muse Ai`），**比工作区新**；工作区是旧快照
- 计划任务原 Action（备份）：`C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe`

## 风险 / 注意
- **venv 依赖基础解释器 3.13.12**：WorkBuddy 若升级或重置该 Python，venv 会失效，需重建（`python -m venv D:\网关管理器\.venv` + `pip install requests`）
- 内置 Python 仍缺 requests/certifi；任何直接用它跑的脚本会失败
- 「按模型」表 17 列仍需横向滚动 147px（未隐藏任何列）
- 门禁：`visual_metrics_check` 12 处（P0 2）；`layout_variety_check` 6 处 FAIL（`<h2>` 内 hint 被计为标题，文案未删）；`design_presence_check` 对非 section 结构除零，未执行成功

## 待决策
- 「按模型」表 17 列：接受横向滚动（推荐，不删信息）／隐藏低频列（「无用量」「错误」，CSS `display:none`）

## 下一步（≤3 条）
1. 观察 venv 方案稳定性；若 WorkBuddy 升级 Python 导致 8787 掉线，按「风险」重建 venv
2. 若用户选择隐藏低频列，改 `.design/admin_new.html` → `verify_ui.py` → `apply_ui.py` → `sync_live.py` → 重启
3. 清理 `.design` 下的诊断脚本与临时截图（probe_*、sync_stamp、_edge_* 缓存）
