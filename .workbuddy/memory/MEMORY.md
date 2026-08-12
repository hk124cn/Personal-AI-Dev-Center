# Personal AI Dev Center — 项目长期记忆

## 项目定位
本地「多服务器 / 多项目管理面板」。技术栈：Electron 外壳 + FastAPI 后端（localhost:8765）+ 纯静态 SPA（index.html，无构建步骤）+ SSH/Paramiko 同步引擎 + 可选 LLM 分析。
用途：管理 4 台服务器、十几个远程项目的 Markdown 文档（TODO/PROGRESS/ISSUES）同步、双向文件同步（tar-over-SSH）、LLM 智能分析、域名/资产台账。

## 维护交接
- 前任维护者：**Qorder**。2026-07-26 起由我（WorkBuddy）接手，owner=千问老大(qw_20)。

## 开发与发布流程
- **⚠️ 推送规则（2026-08-10 用户明确要求）**：**不主动推 GitHub，只有用户明确说推送才推**。本地 commit 照常做，完成后告知用户即可；项目目前主要本地使用。远程：`git@github.com:hk124cn/Personal-AI-Dev-Center.git`（public，本地 main ↔ origin/main）。
- **已初始化本地 git 仓库**（2026-07-26）：`.gitignore` 忽略 `config.json`(真实密钥)/`dist`/`dist_new`/`node_modules`/`backend/data`/`*.exe`。源码、`index.html`、`config.example.json` 入库。
- 本地运行：`pip install -r backend/requirements.txt` → `python backend/app.py`。Electron 启动时会 spawn 该 python。
- 发布：`npm run build`（portable exe，dist/）→ 或 `npm run build:setup`（NSIS）。产物约 75MB，会把 config.json/index.html/backend 一起打进 exe。
- 打包后首次运行：把随包 config.json 复制到 `%APPDATA%/Personal AI Dev Center/config.json`，之后以 APPDATA 版本为准。

## 关键架构约定
- config.json = 服务器/项目清单的 source of truth；backend/data/latest.json = 同步结果。
- 同步是**手动触发**（UI 按钮 /api/sync、单项目 /api/sync/{id}、CLI sync.py），**没有自动调度**。
- LLM 配置支持多套（llm_configs + llm_active_id），自动 sync 时把激活配置写回 legacy `llm` 字段。
- 当前 LLM 用商汤 SenseNova（OpenAI 兼容），非 docs 里写的 Anthropic/Qwen。

## 已知风险 / 待修（状态）
- ✅ **密钥随 exe 分发（原安全债）已解决**：发布包改用 `config.example.json`（脱敏），`package.json` extraResources 打它；`app.py` 兜底（本地有 config.json 用本地，无则示例）。真实 config.json 不进包、不入库。
- ✅ **版本号漂移已解决**：当前 `1.4.16`，`app.py`(FastAPI+health)、`electron/main.js`(关于)、`index.html`(APP_VERSION) 三处统一读 `package.json`。版本 bump 只改 `package.json` + `index.html` 两处即可。
- ⏸ **latest.json（07-20）比 config 多 3 个项目**：用户选择手动同步（因项目同步方向各异：云端开发→下载、本地开发→上传），暂不自动处理。
- ✅ **docs 与现状不符已解决**：3 份 docs + `config.llm.example.json` 已对齐商汤，并修正不存在的 `sync.py --project` 命令。
- ✅ **安全/健壮四项已修（2026-08-11，v1.4.17 补丁，commit ede8d8a）**：
  - **R1 后端暴露**：`uvicorn` 由 `0.0.0.0` 改为 `127.0.0.1`（仅本机回环）；CORS `allow_origins` 由 `*` 收紧为 `http://localhost:8765 / http://127.0.0.1:8765 / app://. / file:// / null`。前端用 `loadURL('http://localhost:8765')` 同源加载，不受限。
  - **R2 上传爆内存**：`sync_upload` 由 `io.BytesIO` 整包进内存改为 `tarfile mode='w|'` 流式写 SSH stdin（与下载对称），大项目不再爆内存。
  - **R3 上传命令注入**：`tar xf - -C {remote_path}` 由单引号改为 `shlex.quote(remote_path)`。
  - **R4 上传无法取消**：新增 `_UPLOAD_CANCEL` 注册表 + `POST /api/sync-upload-cancel/{id}` + 前端 `closeSyncProgress` 对 upload 对称触发；`sync_upload` 加 `cancel_event` 与 `phase='cancelled'`。
  - ⚠️ **生效前提**：用户正在运行的 live app 是从 `%TEMP%` 解包旧 exe（绑 `0.0.0.0`），须**重建 exe 并重启 app** 才能吃到 R1；源码改动已本地提交未推送（2026-08-11 已重建 exe 验证）。
- ✅ **安全/健壮补充三项已修（2026-08-12）**：R6/R7/R10。
  - **R7 知识库 XSS**：`/api/kb/render` 渲染后过 `sanitize_html()` 白名单消毒（stdlib `html.parser` 自写，零新增依赖）；阻断 `<script>`/`<iframe>`/`on*` 属性/`javascript:` 等，脚本/样式内部文本一并丢弃。
  - **R6 原子写入**：新增 `atomic_write_json()`（唯一临时文件 + `os.replace` + 对 `os.replace` 重试吸收 Windows 并发“拒绝访问”）；`save_config`(app.py/sync.py) 与两处 `latest.json` 写入均走它，并加 `_write_lock` 串行化。
  - **R10 取消注册表加锁**：新增 `_cancel_lock`，`_DOWNLOAD_CANCEL`/`_UPLOAD_CANCEL` 的 get/set/pop 八处访问全加锁。
  - 验证：`test_security_robustness.py`（19 项全过：R7 注入清除+合法 Markdown 保留、R6 8×50 并发写仍为合法 JSON、R10 6×200 并发无异常）+ HTTP 冒烟（kb/render 线上消毒、assets/config/full 200）。
  - 待处理剩余：R5（PyInstaller 打包后端）、R8（全量同步改 SSE 流式）、R9（扫描期响应取消）、打包杂项（目录选择改 Electron dialog、清理 dist/dist_new）。详见 `docs/system-review.md`。

## 知识库（2026-07-31 MVP）
- 本地知识库 **`%APPDATA%/Personal AI Dev Center/knowledge/<分类>/<id>.md`**（即 `USER_DATA_DIR/knowledge`，2026-08-01 从 `backend/data` 迁出，保证打包 exe 也能读写且不被更新包清除）。frontmatter: title/category/tags/author/created/updated。
- 前端：左栏分类 + 搜索 + 文档列表 + 弹窗编辑器(左写右预览) + 导出/导入 JSON 备份。
- 后端 API：`/api/kb/categories`(含计数), `/api/kb/docs`(按分类/搜索过滤), `/api/kb/doc`(CRUD), `/api/kb/render`(Markdown→HTML), `/api/kb/export`, `/api/kb/import`。
- **AI 维护约定**：agent 可经同一 `/api/kb/doc` 写入（`author: "ai"`），但绝不自动灌，只在用户点头时写。文档上 `author` 显示来源（human/ai），用户可随时分辨/删改。
- **与同步方向的关系**：知识库纯本地，不和任何项目的 SSH 同步混在一起，不触碰你的「云端开发→下载、本地开发→上传」策略。
- 默认 6 分类沿用原占位：架构设计/开发文档/运维笔记/AI工具指南/项目模板/灵感收集，可新建、可扩展。

## Agent 管理（2026-08-01）
- `agent` 现在是**受管实体**（config.json 顶层 `agents` 数组），不再是项目上的自由文本。每 agent 字段：id/name/icon/color/bg/desc/servers[](部署服务器)/llm_provider/llm_model/llm_api_key（大模型单独填，不绑定 llm_configs）。
- 后端：`/api/agents`(GET/POST/PUT/DELETE) CRUD；`/api/config/full` 返回完整 config（含 agents），前端 `fetchConfig()` 把 servers/projects/agents 并入 state。**坑**：前端加载函数叫 `fetchConfig`，不是 loadConfig（曾误用 loadConfig 导致 ReferenceError，已修）。
- 前端「Agent 中心」(viewAgents) 重写：卡片展示每个 agent 的厂商/模型；Agent×服务器矩阵；新建/编辑弹窗（含大模型字段）；**防撞车**：按 (llm_provider, llm_model, llm_api_key) 分组，≥2 个相同即标红面板（同厂商+同模型+同 Key 算撞车，符合用户要求）。
- 项目详情弹窗 `bot` 按钮改为 `openAgentFromProject(pid)`：打开该项目绑定 agent 的编辑器；卡片显示 `agentModelBadge`（agent 用的模型）。
- 派生逻辑：首次从各项目 `agent` 文本值生成 agent 种子（"Claude Code"/"qoder"/"mimo"/"openclaw" 等），llm 字段留空由用户填。注意 `Openclaw`/`openclaw` 大小写不一致，需用户在 UI 里合并。
- 实时配置（%APPDATA%）已注入 8 个 agents 种子；打包 exe 读的是 appdata config（非项目目录 config.json）。

## 资产模块（2026-08-02，v1.4.14）
- 「个人资产」是内置模块（导航 `assets`），四类：SIM卡(sim) / 信用卡(credit_card) / 邮箱(email) / 会员订阅(membership)。数据存 `%APPDATA%/Personal AI Dev Center/config.json` 的 `assets` 数组，后端 `/api/assets`(GET/POST/PUT/DELETE)，前端 `state.assets` 由 `fetchAssets()` 加载。
- `AssetModel` 字段（含本次新增）：通用(name/provider/status/note/total_fee/currency/expiry)；SIM(phone_number/home_region/data_allowance/throttle_speed/sms_capable/voice_capable/roaming/roaming_data/contract_months/**registrations**[{name,url}]）；信用卡(card_network/card_form/last_four/issuing_bank/annual_fee/credit_limit/billing_day/payment_due_day/**prepaid**[bool]/**prepaid_balance**[float])；会员(plan_type/trial_expiry/paid_expiry/auto_renew)。
- 一览(`viewAssets`)顶部有统计卡 `assetSummaryHTML()`：SIM 总费用合计、信用卡总授信额度合计、预付卡总余额合计（均按汇率折算 ¥）。
- SIM 一览显示可点击注册网站 chip（`openReg`→`window.open`）；预付卡右侧显示余额；SIM/信用卡均显示备注第一行（li-note）。
- 编辑弹窗：SIM 注册网站动态增删（`data-reg-name`/`data-reg-url`，`collectAssetRegs()` 读取）；信用卡「是否预付卡」开关 +「卡内余额」；备注为独立多行文本框。
- **汇率**：`exchangeRates` 硬编码默认值，`/api/exchange-rate` 可覆盖；`toCNY(amount,currency)` 折算。
- **邮箱类别**（v1.4.15）：第四类 `email`（email/register_date + registrations 申请网站可点击 chip）。
- **跨类别关联**（v1.4.16，主键-外键式）：`linked_phone_id`（email/credit_card/membership → SIM id）、`linked_email_id`（membership → email id）。真源只在引用方；SIM 侧派生（simLinkedEmails/Cards/Members）。下拉选项由 state.assets 派生 → 增删自动更新；deleteAsset 自动清悬空引用；显示端对失效 id 视为未设置。SIM 编辑页有「该号码注册的邮箱」勾选列表，保存时 reconcile 双向同步邮箱的 linked_phone_id。
- **详情卡片**（v1.4.16）：点击资产行 → `viewAsset(id)` 详情卡（modal-sm 560px），编辑按钮才进 `editAsset`；空字段不渲染；关联 chips 可互跳。

## 下载引擎（v1.4.16 重写 sync_download）
- 扫描：`_remote_stat_tree_fast`（远程一条 `find -printf '%T@\t%s\t%p\0'`，只读；失败回退 SFTP 递归 `_sftp_stat_tree`）。
- 传输：`tar --null -cf - -C <path> -T -`，文件清单走 **stdin**（无 ARG_MAX、服务器零写入）；`tarfile mode='r|'` 流式解盘（不占内存）；stderr 后台线程排空（防通道死锁——多文件卡死主因）；逐文件 `phase='file'` 进度；路径穿越防护。
- 取消：`_DOWNLOAD_CANCEL` 注册表 + `POST /api/sync-download-cancel/{id}`；前端 `closeSyncProgress` 对 download 先 POST 取消。
- **GitHub 脱敏**（2026-08-09 完成）：全 git 历史已 filter-branch 重写，真实 IP/用户名/域名/密钥/项目名/本地路径 0 残留（含 docs、test_llm.py、count_md.py）；config.example.json=演示模拟数据、config.llm.example.json=纯 llm 示例。push 前可直接推 master。

## 上传引擎（2026-08-11 重写 sync_upload 对齐下载）
- `sync_upload(server, project, local_path, progress_cb, force, selected_files, sync_all, cancel_event)`：扫描本地 `_local_stat_tree` + 远程 `_sftp_stat_tree` 比对（mtime/size，force 跳过），`to_upload` 列表。
- 流式传输：`client.exec_command(f"tar xf - -C {shlex.quote(remote_path)}")` → `tarfile.open(fileobj=stdin, mode='w|')` 逐文件 `tar.add` 增量写入 SSH 通道（内存只留单文件）；后台线程排空 stderr 防死锁（与下载对称）。
- 进度/取消：`phase='file'` 逐文件上报（与下载同名，前端复用）；`_cancelled()` 检查 `cancel_event.is_set()`（传输前 + 每个文件），命中则 `phase='cancelled'` 早退；`_UPLOAD_CANCEL[project_id]` 由 `/api/sync-upload-cancel/{id}` 置位，前端 `closeSyncProgress` 对称触发。
- 验证：`monkeypatch _sftp_connect` 用本机 `tar` 子进程模拟远程解包，实测 默认过滤(.git)/sync_all/取消/空格路径 四项全过 + 无头浏览器零控制台错误回归。

## 构建注意事项（重要）
- 实时测速 `speed-test` 已改为 **TCP 端口(22)探测 + 并行**（绕过阿里云/腾讯云 ICMP 拦截，原 ICMP ping 误报"超时"）。
- **后台 `npm run build` 易孤儿化**：任务记录会丢失，但 `node.exe`(electron-builder) 进程残留并锁住 `dist/Personal-AI-Dev-Center.exe`，导致 exe 停在旧时间戳、新包写不进。重建前务必先 `tasklist` 找残留 `node.exe` 并 `taskkill /F` 结束；同时确认没有「Personal AI Dev Center」主程序在跑（也会锁 exe）。
- 验证打包是否含密钥：portable exe 是 7z 压缩自解包，字节搜不到明文；应改验 `dist/win-unpacked/resources/app.asar`（未压缩，搜真实 IP/Key）+ `resources/` 目录（应只有 config.example.json）。
