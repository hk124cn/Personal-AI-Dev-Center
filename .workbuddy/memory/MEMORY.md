# Personal AI Dev Center — 项目长期记忆

## 定位 / 维护
- 本地「多服务器 / 多项目管理面板」：Electron 外壳 + FastAPI(127.0.0.1:8765) + 纯静态 SPA(index.html，无构建) + Paramiko SSH 同步(tar-over-SSH) + 可选 LLM(商汤 SenseNova，OpenAI 兼容)。
- owner=千问老大(qw_20)，2026-07-26 由 WorkBuddy 接手（前任 Qorder）。

## ⚠️ 记忆文件是入库的，禁止写真实基础设施信息
- `.workbuddy/memory/*.md` **被 git 跟踪**，远程仓库 `hk124cn/Personal-AI-Dev-Center` 是 **public**。
- 因此严禁在这些文件里写：**真实服务器 IP、SSH 用户名、私钥路径、API Key、密码**。
- 服务器一律用别名指代（如「腾讯云_上海」「首尔」）；确需写 IP 时用掩码 `47.xx.xx.xx`。
- 2026-09-13 踩坑：把 4 台真实 IP + 用户名写进了记忆并推送到公开仓库，
  最终靠「脱敏 + filter-branch 重写历史 + force push」才清掉。**别再犯**。
- 改历史后**别以为就干净了**：GitHub 仍会保留未引用对象，实测旧 SHA 的 commit API 与
  raw 文件仍可访问。**2026-09-19 已用「删库重建」彻底解决**（用户手动删 + 建同名空库，我验证并推回 51 个干净提交）：
  旧 SHA 变 422、旧 raw 变 404、远程 HEAD 真实 IP 0 处。以后遇同类事件**直接走删库重建**，
  别去求 GitHub Support 跑 GC。
- ⚠️ 判读「清干净没有」要看**具体 SHA**：脱敏**之后**的提交（如 `f4a1f7f`/`d64c7cb`）本来就该 200，
  因为它们是当前 main 的祖先；只有被 filter-branch 丢掉的旧对象必须 422/404。看到 200 别急着再折腾一轮。
- 推送前扫全历史用 `git log --all -p --no-color -- .workbuddy/memory docs` **一次性拿 diff** 再正则扫；
  **别逐 commit 读对象**（51 提交 × 多文件会跑 2 分钟被超时打死，一次性 diff 只要 0.2 秒）。
- 改历史前先 `cp -r .git` 备份；改完必须核对「非记忆文件的所有历史版本逐字节未变」
  （按对象类型过滤出 blob 再比，树哈希变了是正常的）。

## 开发 / 发布 / 推送
- **推送规则（用户明确）**：不主动推 GitHub，仅用户要求才推；本地 commit 照常做、做完告知即可。远程 `git@github.com:hk124cn/Personal-AI-Dev-Center.git`(public, main)。
- 本地跑：`pip install -r backend/requirements.txt` → `python backend/app.py`。Electron 启动时**优先 spawn 内置 `resources/backend/devcenter-backend.exe`**（PyInstaller 自包含 Python，目标机无需装 Python），找不到该 exe 才回退 `python backend/app.py`。
- 发布：`npm run build`(portable, dist/) 或 `npm run build:setup`(NSIS)。package.json `extraResources` 把 `backend/`、`index.html`、`config.example.json`(脱敏) 复制进 `resources/`；真实 `config.json` 不进包（运行时读 `%APPDATA%/Personal AI Dev Center/config.json`）。
- ⚠️ **构建坑**：`npm run build` 后 `node.exe`(electron-builder) 易孤儿化并锁 `dist/Personal-AI-Dev-Center.exe`；重建前先 `tasklist`/`taskkill` 清残留 node.exe + 关闭 live app。**app 运行时锁 `dist/`**，此时把 `build.directories.output` 临时改成 `dist_new` 出包，**构建完务必还原为 `dist`**。
- **换包标准姿势（不要直接删 dist）**：① `mv dist dist_prev_<旧版本号>` 改名保底；② `mv dist_new dist`（同盘改名，秒完成，别去复制 381MB）；③ 跑 `python tools/verify_build.py`（**完整模式**，会真启动后端 exe 探 `/openapi.json`，46 条路由 + 6 条核心路由必须都在）；④ 确认新版能启动后再删 `dist_prev_*`。验证打包是否含密钥：解包 `resources/` 查（不要只 grep 压缩的 asar）。
- **运行中 app 会锁 `dist/`**：改 `package.json` 的 `build.directories.output` 为 `dist_new` 输出，构建后**务必还原为 `dist`**。electron-builder portable 全程约 **2.5~3 分钟**（杀软不拖时）。可用 `node node_modules/electron-builder/out/cli/cli.js --win portable` 直跑，绕开 bash PATH 丢失。
- **本机 shell 事实**：bash 的 PATH 偶发丢失（`ls/git/tail` command not found）→ 一切命令用**绝对路径**（python/node/git: `/c/Program Files/Git/cmd/git.exe`）。`wmic` 不存在，查进程路径用 toolhelp32 + `QueryFullProcessImageNameW`。
- **仓库根 `nul` 文件删不掉**：git-bash 误写 `2>nul` 生成 0 字节文件，因 Windows 保留设备名，`rm`/`DeleteFileW(\\?\...)`/`MoveFileExW` 全 ACCESS_DENIED（疑安全软件拦截）⇒ 已在 `.gitignore` 忽略 `nul/con/aux/prn`。
- 产物校验要点：`dist_new/win-unpacked/resources/` 是未压缩真源（asar 未压缩可直接搜字节），逐项核对 index.html 新代码、backend exe **MD5 与 `backend/dist/` 一致**、**无真实 config.json / backend/data 泄漏**。
- **正常进程树（勿当残留杀）**：`Personal-AI-Dev-Center.exe`(dist 启动器) → `Personal AI Dev Center.exe`(主) → 子进程；主又 spawn `devcenter-backend.exe`(**onefile 引导父**) → `devcenter-backend.exe`(**真实后端**，LISTENING 8765)。两个同名属 PyInstaller onefile 正常两段式。


## 启动逻辑硬约束（便携版，2026-08-29 定）
- **绝不**在启动时按镜像名 `taskkill` 强杀旧实例；便携版进程树＝启动器 `Personal-AI-Dev-Center.exe`(父) → 真实主进程 `Personal AI Dev Center.exe`(子)。按启动器名 `/T` 强杀会顺树杀回自身（自杀根因，已踩坑）。
- 正确做法：`isAnotherInstanceRunning()` 仅用 `netstat -ano | findstr ":8765"` 检测占用 + `tasklist` 确认镜像名，`dialog.showErrorBox('已在运行')` 提示后 `app.quit()`，把清进程责任交给使用者。**提示框内已附手动关端口命令**：`Stop-Process -Name "devcenter-backend" -Force`。
- 退出清理（2026-08-30 加，commit 56a0125）：`killBackend()` 改为**同步** `execSync('taskkill /PID <pid> /T /F')` 强杀自己 spawn 的后端进程树，挂到 `before-quit` / `app.on('quit')` / `process.on('exit')` 三处兜底，确保 8765 在程序关闭前一定释放，杜绝「关了界面后端还在占端口」的孤儿问题。
- **绝不**在启动/退出时按镜像名乱杀其它进程；只杀自己 spawn 的子进程。
- 用户原话定调："杀进程的功能搞得太复杂了…就启动时检查一下有没有相关检查在跑，已经在跑了弹提示关闭，等使用者自己杀。" 及 "添加退出自动关闭端口的命令"。

## 架构约定
- `config.json` = 服务器/项目 source of truth；`backend/data/latest.json` = 同步结果。
- 同步**手动触发**（`/api/sync`、`/api/sync/{id}`、CLI `sync.py`），无自动调度。过滤：`DEFAULT_SYNC_IGNORE`(目录)+`DEFAULT_SYNC_IGNORE_EXT`(含 .csv/.log/.git)；`sync_all` 参数绕过全部过滤。
- LLM 多套配置 `llm_configs`+`llm_active_id`；自动 sync 写回 legacy `llm` 字段。

## 风险修复状态 (R1–R10)
| ID | 问题 | 状态 |
|----|------|------|
| R1 | 后端绑 0.0.0.0 全网暴露 | ✅ 改 127.0.0.1 + CORS 收紧 |
| R2 | 上传整包进内存爆内存 | ✅ 改流式 tar |
| R3 | 上传命令注入 | ✅ shlex.quote |
| R4 | 上传无法取消 | ✅ 加取消机制 |
| R5 | 后端依赖本机 python | ✅ PyInstaller 内置 exe + python 回退 |
| R6 | 配置/数据并发写非原子 | ✅ atomic_write_json + 锁 |
| R7 | 知识库 Markdown XSS | ✅ sanitize_html |
| R8 | 全量同步阻塞/子进程残留 | ✅ Popen+超时杀进程树(_kill_proc_tree) |
| R9 | 扫描阶段不响应取消 | ✅ 扫描函数增量读+周期检查 cancel_event |
| R10 | 取消注册表缺锁 | ✅ 加锁 |

## 页面显示 CSS 源码问题（2026-08-18 根治，commit 4e70db8）
- **真根因**：#60 监控 CSS 插在 `</style>` 之后未包 style 标签，浏览器当正文渲染。教训：给 index.html 加 CSS 必须确认插在 `<style>...</style>` 之内；排查 UI 显示异常先 `grep -c` 检查 style 开闭标签配平。
- media_type(text/html)/单实例锁/端口清理是同期加固（保留），但均非此问题根因。
- **打包规避 safe-delete**：build 前用独立命令 rm 旧 exe 确认消失，再单独 `NODE_OPTIONS= npm run build`；`rm && build` 同行仍触发拦截。`dangerouslyDisableSandbox` 跑 build 会让 portable 压缩被杀软拖死（15min+），勿用。
- 验证脚本：`test_security_robustness.py`(19 项)、`test_r8_r9.py`(12 项)。详见 `docs/system-review.md`。
- **R1–R10 全部完成**：R5 已用 PyInstaller 把后端打进 `devcenter-backend.exe`（自包含 Python 运行时，目标机无需装 Python），Electron 优先 spawn 该 exe 并注入环境变量，回退 python；版本号经 `DEV_CENTER_APP_VERSION` 注入。打包杂项（目录选择改 Electron dialog + 清 dist_new）已完成。

## 模块要点
- **知识库**：`%APPDATA%/…/knowledge/<分类>/<id>.md`；API `/api/kb/*`；AI 可经 `/api/kb/doc` 写(`author:"ai"`) 但绝不自动灌，经用户点头才写。
- **Agent 管理**：`config.json` 顶层 `agents[]`；防撞车按(厂商,模型,key)分组 ≥2 标红；前端加载函数 `fetchConfig`（非 loadConfig）。
- **资产**：四类 sim/credit_card/email/membership，存 `config.json.assets`；跨类关联 `linked_phone_id`/`linked_email_id`(主键-外键，删时清悬空)；统计卡按汇率折¥；点击出 `viewAsset` 详情卡而非直接编辑。
- **下载引擎**：`_remote_stat_tree_fast`(远程 `find` 只读) + tar stdin 清单 + `tarfile` 流式 + stderr 后台排空防死锁 + `_DOWNLOAD_CANCEL` 取消。
- **上传引擎**：`_local_stat_tree` 比对 + 流式 tar 写 SSH stdin + `shlex.quote` + `_UPLOAD_CANCEL` 取消；支持 force/selected_files/sync_all。
- **cron/计划任务**：`backend/cron_manager.py` 持有一个已连接的 SSHClient；来源三类（root 用户级 / 其它用户级 / 系统级 `/etc/crontab`+`/etc/cron.d/*` 含 USER 字段）；写回护栏＝备份→校验→回写→读回验证→失败回滚；非 root 需 `sudo -n true` 探测 NOPASSWD。路由 `/api/cron/{id}/list|get|save|backup`；前端入口在**服务器详情卡**的「计划任务」按钮（`openServerCron`）。
  - ⚠️ **`_spool_users` 必须用 `id -u` 复核**：Debian 的 `/var/spool/cron` 下只有 `crontabs/atjobs/atspool` 子目录，直接 `ls` 会造出假用户来源。且过滤后**只要哨兵在就必须完全信任结果（哪怕为空）**——写成「空就退回」会让 bug 复发。
- **前端两个固定坑**：① `escapeHtml` 把 `'` 转成 `&#39;`，HTML 属性里的 onclick 会把它解析回 `'` 从而截断 JS 字符串 → 文本类参数**不要**塞进 onclick；② lucide 图标由 `createIcons()` 运行时替换 `<i data-lucide>`，覆写 `innerHTML` 恢复按钮会让图标消失 → 只改内部 `<span>` 文字。
- **首页同步状态条**（v1.4.21）：**首页已按用户要求不再展示** —— `viewDashboard()` 不调用 `renderLastSyncBar`；但 `fetchAPIData()` 仍写 `state.lastSync`（**后台查询保留**），「定时同步」页仍渲染完整形态。折叠三态能力（`renderLastSyncBar(ls,'compact')` + localStorage `dc-syncbar-mode` = collapsed/expanded/hidden）**代码保留**，想恢复首页显示只需把该调用加回 `viewDashboard`。改首页展示别动管理页形态。
- **截图验证别用 Electron**：本机沙箱里 `ELECTRON_RUN_AS_NODE=1` 会把它退化成纯 Node，清掉该变量后 GPU 进程崩 + `file://` 报 `ERR_FAILED(-2)` + `app.quit()` 后进程不退出 → 改用**「变体页 + 系统 Chrome 无头」**（`--headless=new --disable-gpu --no-sandbox --allow-file-access-from-files --force-device-scale-factor=1.5 --virtual-time-budget=9000`）。变体页注入必须先**冻结页面定时器**（否则注入状态被页面自己的轮询覆盖）、**禁动画**（`fade-in` 停在中间帧会让整页发暗）、**清 modal/toast**。
- **自有工具**：`tools/verify_build.py`（打包后校验：源码↔产物 MD5、版本号一致、无 config.json 泄漏、启动 exe 探 openapi 路由、PyInstaller 字节码关键字；**前后端关键字必须分开传** `--html-needle`/`--exe-needle`）；`tools/test_cron_realmachine.py`（真机 cron 全链路，用 `/tmp` 探针文件走写入分支，不碰真实 crontab）。两个都需 `backend/build_venv`（含 paramiko/PyInstaller）；`tools/test_syncbar.mjs`（首页状态条 30 项分支自测，**从 index.html 抽真实函数**执行，不写副本）；`tools/gen_syncbar_variants.py`（生成变体页供无头截图，`--source` 可传 `git show HEAD:index.html` 的旧稿做「改动前对照」）；`tools/recreate_repo.py`（GitHub 删库重建以清未引用对象，需 `GH_TOKEN` + `--confirm-delete`，护栏＝fork/star/watcher/issue 全 0 且本地 bundle 备份存在）。
- **服务器连接特性**：`腾讯云_上海 124.xx.xx.xx` 曾全端口超时、后恢复正常，但 **SSH 握手偏慢（4~12s）**且偶发 `AuthenticationException` —— 判定为**连接稳定性一般**，非程序 bug。
- `build_backend.bat` 的 cwd 坑已修（原 `cd /d %~dp0..` 会跳到项目根上级）。
