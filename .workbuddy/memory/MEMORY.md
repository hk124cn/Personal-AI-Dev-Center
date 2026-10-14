# Personal AI Dev Center — 项目长期记忆

## 定位
本地「多服务器/多项目管理面板」：Electron 33 + FastAPI(127.0.0.1:8765) + 纯静态 SPA(index.html，无构建) + Paramiko SSH 同步 + 可选 LLM(商汤 SenseNova)。owner=千问老大(qw_20)，2026-07-26 由 WorkBuddy 接手。

## ⚠️ 本目录入库且仓库 public → 禁写真实基础设施信息
`.workbuddy/memory/*.md` 被 git 跟踪，远程仓库是 **public**。**严禁**写真实 IP / SSH 用户名 / 私钥路径 / API Key / 密码；服务器用别名（「腾讯云_上海」），IP 一律掩码 `47.xx.xx.xx`。
2026-09-13 误推真实 IP：脱敏 + filter-branch + 强推**清不掉 GitHub 未引用对象**（旧 SHA 的 API/raw 仍可访问）；2026-09-19 靠**删库重建**才彻底解决。**同类事件直接删库重建**，别求 Support。判读懂看**具体 SHA**：脱敏**之后**的提交本该 200（是 main 祖先），只有被丢掉的旧对象必须 422/404。
扫全历史用 `git log --all -p --no-color -- .workbuddy/memory docs` 一次性拿 diff 再正则（别逐 commit 读对象，会超时）；改史前 `cp -r .git` 备份。

## 推送 / 构建
- **推送规则**：不主动推 GitHub，用户明说才推；本地 commit 照常做、做完告知。
- Electron **优先 spawn `resources/backend/devcenter-backend.exe`**（PyInstaller 自包含，免装 Python），否则回退 `python backend/app.py`。真实 `config.json` 不进包（运行时读 `%APPDATA%/Personal AI Dev Center/config.json`）。
- **换包姿势（别直接删 dist）**：**直接跑 `python tools/swap_dist.py --yes --keep 1 [--html-needle X --exe-needle Y]`**（v1.4.24 新增，一条龙：守卫 8765+进程名 → 同盘改名 `dist`→`dist_prev_<旧版>`、`dist_new`→`dist` → 清理旧包 → 自动调 `verify_build.py` **完整模式**，真启动 exe 探 `/openapi.json`，46 路由 + 6 核心路由）。app 运行时它**只检测不杀进程**，rc=3 退出。备份目录名带点号：`dist_prev_1.4.23`。
- **构建坑**：孤儿 `node.exe` 会锁 `dist/*.exe`，重建前清残留 + 关 live app；**app 运行时锁 dist/** → `build.directories.output` 临时改 `dist_new`，**完事必须还原 `dist`**。先独立命令 rm 旧 exe，再单独 `NODE_OPTIONS= npm run build`（`rm && build` 同行会被 safe-delete 拦）。
- 校验：`win-unpacked/resources/` 是未压缩真源，核对 index.html 新代码、backend exe **MD5 与 `backend/dist/` 一致**、无真实 config.json / data 泄漏。
- **本机 shell**：bash PATH 偶发整段丢失 → 一律**绝对路径**；构建命令**不接管道**；`wmic` 不存在。**`grep -o "a|b"` 的 alternation 在这台机不可靠**（管道符会被吃掉、返回假空），要 alternation 就跑多个 `grep -n <单个词>` 或用 Grep 工具 —— 曾因此差点把「已实现」误判成「未实现」去做重复劳动。
- **正常进程树（勿当残留杀）**：启动器 `Personal-AI-Dev-Center.exe` → 主 `Personal AI Dev Center.exe` → 同名 `devcenter-backend.exe` 两段（onefile 引导父 + 真实后端 LISTENING 8765）。

## 启动/退出硬约束（便携版）
- **绝不**启动时按镜像名 `taskkill`：便携版进程树＝启动器(父)→主(子)，按启动器名 `/T` 会顺树杀回自身（自杀根因，已踩）。只**检测**：`netstat -ano | findstr ":8765"` + `tasklist` → `showErrorBox('已在运行')` → `app.quit()`，清进程交使用者。
- 退出清理：`killBackend()` 同步 `taskkill /PID <pid> /T /F` 杀自己 spawn 的后端树，挂 `before-quit`/`quit`/`exit` 三处兜底。只杀自己 spawn 的子进程。

## 前端硬约束
- **弹窗模型**：正文取自 `state.modal.body`(HTML 字符串)，`render()` 整页重绘。① 弹窗 HTML 必须**同步写回** `state.modal.body`；② `render()` 必须按 `editMode.type` **分派**，`renderEditModal()` 只认 server/project、其它 type 直接 return —— 历史事故：它曾无条件被调，`type='asset'` 掉进 else，资产弹窗变「新增项目」且**输入全丢**（2026-09-19 用户报）；③ 后台 `fetchAPIData()` 在 `state.modal` 非空时**跳过 render**。`_formDraft` 草稿（input/change 按 `name` 快照 + 重绘后回填）兜底。回归 `tools/test_asset_modal.py`。
- `escapeHtml` 把 `'`→`&#39;`，onclick 里被解析回 `'` 会截断 JS → 文本参数**不要**塞 onclick；lucide 图标由 `createIcons()` 替换 `<i data-lucide>`，覆写 `innerHTML` 会让图标消失 → 只改内部 `<span>` 文字。
- 加 CSS 必须在 `<style>...</style>` **之内**（曾插到外面被当正文渲染，整页走形）；UI 异常先 `grep -c` 查 style 配平。
- 首页同步状态条已按要求**不再展示**（`viewDashboard()` 不调 `renderLastSyncBar`），但 `fetchAPIData()` 仍写 `state.lastSync`，「定时同步」页形态不变。改首页别动管理页。

## 关键业务约束
- `config.json` = 服务器/项目 source of truth；`backend/data/latest.json` = 同步结果。同步**手动触发**，无自动调度；`sync_all` 绕过全部过滤。
- **⚠️ 「定时同步」≠「下载文件」**（两个常被混淆的概念）：`scheduler.run_schedule()` 只调 `sync_all/sync_single`，它们对每个项目**只跑 `find <remote_path> -maxdepth 1 -name '*.md'`** → 解析 TODO/进度/问题 + LLM 分析 → 写 `latest.json` 喂首页卡片与预警；**子目录一个都不进、不产生任何本地文件、也从不写项目的 `lastUpdate`**。文件下载必须**手动**触发 `POST /api/sync-download/{id}`（`all=true` 才不过滤），跑在 daemon 线程 + SSE 推进度，可取消，**不占 `_write_lock`**，流式解包不随包大小吃内存。默认过滤 `DEFAULT_SYNC_IGNORE`(含 .git/node_modules/venv) + `_EXT`(.csv/.log/.pyc/.exe/.zip…) 能砍掉 90%+ 量级。
- **cron**：`backend/cron_manager.py` 持一个已连接 SSHClient；写回护栏＝备份→校验→回写→读回验证→失败回滚。⚠️ `_spool_users` 必须用 `id -u` 复核（Debian `/var/spool/cron` 只有 crontabs/atjobs/atspool，直接 `ls` 会造出假用户），且**哨兵在就必须完全信任结果（哪怕为空）**，写成「空就退回」bug 会复发。前端入口＝服务器详情卡「计划任务」(`openServerCron`)。
- **知识库**：`%APPDATA%/…/knowledge/<分类>/<id>.md`，API `/api/kb/*`；AI 可写(`author:"ai"`)，但**绝不自动灌**，经用户点头才写。
- **资产**：四类存 `config.json.assets`，跨类关联 `linked_phone_id`/`linked_email_id`（删时清悬空）；点击出 `viewAsset` 详情卡而非直接编辑。
- **主机监控**：`_MONITOR_HISTORY[server_id]` 上限 60 点，`/api/monitor/{id}` 带 `history`；v1.4.22 起 `drawMonChart()` 多曲线（CPU/内存默认开、磁盘默认关），颜色走 `cssVar()` 读 CSS 变量以适配浅色主题。
- R1–R10 风险项**全部完成**（绑定收紧 / 流式 tar / shlex.quote / 上传取消 / 内置 exe / 原子写 / sanitize_html / 超时杀进程树 / 扫描可取消 / 取消表加锁），详见 `docs/system-review.md`。
- **LLM 同步提速三件套（v1.4.24）**：① `llm_analyzer.analyze_project` 用 `ThreadPoolExecutor` + `future.result(timeout=LLM_HARD_TIMEOUT=180)` 硬超时，`pool.shutdown(wait=False)` **不 join**（join 等于没超时）；`requests` 的 timeout 只管连接建立/两次读取间隔，对 DNS/TLS/代理下半开挂死不生效。② `sync._md_fingerprint`（md 内容 md5）+ `_llm_signature`（`provider|model|base_url`）都没变且上轮 `llm_analyzed` → 复用结果 `llm_status='reused'`，**一个请求都不发**（换模型/改 md 自动重算）。③ `_update_llm_breaker`：一次 `sync_all` 内连续 `api_failed` 达 2 → 熔断，后续 `skipped_breaker`；**`reused` 不算成功**（没发请求不能证明 provider 恢复），只有真 `analyzed` 才归零并撤销熔断。**仅 `sync_all` 熔断，`sync_single` 不熔断。**
- **LLM provider 分发（`call_llm_api`）**：`anthropic` → 原生协议；`qwen` → 阿里云百炼；**其余任意值 → OpenAI 兼容**（`base_url.rstrip('/') + '/chat/completions'`）→ 接任何付费模型后端早就支持，缺的只是 UI 入口（v1.4.24 加了 `LLM_PRESETS` 预设下拉）。⚠️ 商汤预设的 provider 必须填 `商汤科技`（与现有配置一致），否则签名变了触发一轮全量重算。
- **⚠️ 429 限流 ≠ provider 故障**（2026-10-14 真实事故）：v1.4.24 上线后第一次同步 15 个项目里 2 个 `api_failed` + 13 个 `skipped_breaker`，看似熔断误伤。真因是商汤 `HTTP 429 inference exceeds tpm/rpm limit`（AUTO-STOCK 46k 字符 / 29k token 一次撞 TPM 上限）。修法三件：① `_call_openai` 遇 429 退避重试（`LLM_RATE_LIMIT_RETRIES=3` / `LLM_RATE_LIMIT_BACKOFF=20`，读服务端 `Retry-After`）；② `_update_llm_breaker` 增加第 4 参 `error`，含 `429`+`limit` 时**不计入熔断**（原来只看 status，任何 api_failed 一律熔断）；③ `sync_all` 项目间节流 `LLM_PROJECT_SPACING=8` 秒，且只对真发过请求的（`analyzed`/`api_failed`）等，`reused` 不等。
- **`sync_all` 的 project_entry 曾漏 `llm_error`**（`sync_single` 一直有）→ 失败原因被吞，只剩光秃秃的 `api_failed`，排查时看不出哪一步挂的。已补，注释标注别再删。
- **下载 ≠ 同步**：浮窗 `sync_phase_transferring` = 「正在传输文件」，与同步提速无关。**实测 AUTO-STOCK 远端 95,728 文件 / 3,191 MB，其中 `node_modules` 35,977 + `venv` 8,282 + `.git` 6,474 + `__pycache__` 3,731 + `dist` 3,517**；默认过滤后只剩 **2,329 个 / 212.7 MB（砍 98%）**。所以下载时**别勾「全部」**（`all=true`），否则在 SSH 上跑很久。扫描本身很快（find 3.26s / 本地 0s），慢的是传输。
- **过滤清单漏了一批构建目录**：`DEFAULT_SYNC_IGNORE` 只有 9 项，缺 `.next`(Next.js)/`target`(Java)/`.pytest_cache`/`.mypy_cache`/`.tox`/`coverage`/`.cache` 等。AUTO-STOCK 里有 **249 个 `dist` 目录**、11 个 `build` 都没被忽略；补齐可从 2,329 再降到 2,122。⚠️ `dist`/`build`/`out` 名字太通用，加进清单有「源码真的放在 src/dist 下被误伤」的边缘风险，**未改，等用户拍板**。
- **LLM token 消耗实测**（`tools/estimate_llm_tokens.py`）：15 个项目全量重算 = **127,434 输入 + 30,000 输出上限** ≈ **¥0.27/次**，每周一次约 ¥1/月；上了增量跳过后常态更低。

## 验证与工具
- **截图别用 Electron**（沙箱 GPU 崩、`file://` ERR_FAILED、退出不干净）→ 用**「变体页 + 系统 Chrome 无头」**：`--headless=new --disable-gpu --no-sandbox --allow-file-access-from-files --force-device-scale-factor=1.5 --virtual-time-budget=9000`。注入前必须**冻结页面定时器**、**禁动画**、**清 modal/toast**。
- `tools/`：`verify_build.py`（打包校验，前后端关键字**分开传**）｜`swap_dist.py`（**换包一条龙**，守卫+改名+清旧包+调 verify 完整模式）｜`test_asset_modal.py`、`test_cron_realmachine.py`、`test_syncbar.mjs`、`test_llm_speedup.py`（LLM 提速 29 项）｜`probe_sync_timing.py`（只读分段计时，区分 SSH 慢 vs LLM 慢）｜`estimate_llm_tokens.py`｜`gen_*_variants.py`（变体页，`--source` 可传 `git show HEAD:index.html` 对照）｜`run_checks.py`（一键静态+回归，13 项）｜`recreate_repo.py`（删库重建）。多数需 `backend/build_venv`。
- **managed node 别写死小版本号**（`versions/` 从 `22.22.2-3` 升到 `22.22.2-6` 过）→ 一律用 `versions/current/node.exe` 软链。
- `腾讯云_上海` SSH 握手偏慢(4~12s) + 偶发 `AuthenticationException` —— 连接稳定性问题，非程序 bug。
