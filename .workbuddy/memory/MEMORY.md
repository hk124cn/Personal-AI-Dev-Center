# Personal AI Dev Center — 项目长期记忆

## 项目定位
本地「多服务器 / 多项目管理面板」。技术栈：Electron 外壳 + FastAPI 后端（localhost:8765）+ 纯静态 SPA（index.html，无构建步骤）+ SSH/Paramiko 同步引擎 + 可选 LLM 分析。
用途：管理 4 台服务器、十几个远程项目的 Markdown 文档（TODO/PROGRESS/ISSUES）同步、双向文件同步（tar-over-SSH）、LLM 智能分析、域名/资产台账。

## 维护交接
- 前任维护者：**Qorder**。2026-07-26 起由我（WorkBuddy）接手，owner=千问老大(qw_20)。

## 开发与发布流程
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
- ✅ **版本号漂移已解决**：当前 `1.4.14`，`app.py`(FastAPI+health)、`electron/main.js`(关于)、`index.html`(APP_VERSION) 三处统一读 `package.json`。版本 bump 只改 `package.json` + `index.html` 两处即可。
- ⏸ **latest.json（07-20）比 config 多 3 个项目**：用户选择手动同步（因项目同步方向各异：云端开发→下载、本地开发→上传），暂不自动处理。
- ✅ **docs 与现状不符已解决**：3 份 docs + `config.llm.example.json` 已对齐商汤，并修正不存在的 `sync.py --project` 命令。

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
- 「个人资产」是内置模块（导航 `assets`），三类：SIM卡(sim) / 信用卡(credit_card) / 会员订阅(membership)。数据存 `%APPDATA%/Personal AI Dev Center/config.json` 的 `assets` 数组，后端 `/api/assets`(GET/POST/PUT/DELETE)，前端 `state.assets` 由 `fetchAssets()` 加载。
- `AssetModel` 字段（含本次新增）：通用(name/provider/status/note/total_fee/currency/expiry)；SIM(phone_number/home_region/data_allowance/throttle_speed/sms_capable/voice_capable/roaming/roaming_data/contract_months/**registrations**[{name,url}]）；信用卡(card_network/card_form/last_four/issuing_bank/annual_fee/credit_limit/billing_day/payment_due_day/**prepaid**[bool]/**prepaid_balance**[float])；会员(plan_type/trial_expiry/paid_expiry/auto_renew)。
- 一览(`viewAssets`)顶部有统计卡 `assetSummaryHTML()`：SIM 总费用合计、信用卡总授信额度合计、预付卡总余额合计（均按汇率折算 ¥）。
- SIM 一览显示可点击注册网站 chip（`openReg`→`window.open`）；预付卡右侧显示余额；SIM/信用卡均显示备注第一行（li-note）。
- 编辑弹窗：SIM 注册网站动态增删（`data-reg-name`/`data-reg-url`，`collectAssetRegs()` 读取）；信用卡「是否预付卡」开关 +「卡内余额」；备注为独立多行文本框。
- **汇率**：`exchangeRates` 硬编码默认值，`/api/exchange-rate` 可覆盖；`toCNY(amount,currency)` 折算。

## 构建注意事项（重要）
- 实时测速 `speed-test` 已改为 **TCP 端口(22)探测 + 并行**（绕过阿里云/腾讯云 ICMP 拦截，原 ICMP ping 误报"超时"）。
- **后台 `npm run build` 易孤儿化**：任务记录会丢失，但 `node.exe`(electron-builder) 进程残留并锁住 `dist/Personal-AI-Dev-Center.exe`，导致 exe 停在旧时间戳、新包写不进。重建前务必先 `tasklist` 找残留 `node.exe` 并 `taskkill /F` 结束；同时确认没有「Personal AI Dev Center」主程序在跑（也会锁 exe）。
- 验证打包是否含密钥：portable exe 是 7z 压缩自解包，字节搜不到明文；应改验 `dist/win-unpacked/resources/app.asar`（未压缩，搜真实 IP/Key）+ `resources/` 目录（应只有 config.example.json）。
