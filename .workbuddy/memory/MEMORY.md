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
- ✅ **版本号漂移已解决**：当前 `1.4.12`，`app.py`(FastAPI+health)、`electron/main.js`(关于)、`index.html`(APP_VERSION) 三处统一读 `package.json`。版本 bump 只改 `package.json` + `index.html` 两处即可。
- ⏸ **latest.json（07-20）比 config 多 3 个项目**：用户选择手动同步（因项目同步方向各异：云端开发→下载、本地开发→上传），暂不自动处理。
- ✅ **docs 与现状不符已解决**：3 份 docs + `config.llm.example.json` 已对齐商汤，并修正不存在的 `sync.py --project` 命令。

## 知识库（2026-07-31 MVP）
- 本地知识库 `backend/data/knowledge/<分类>/<id>.md`（frontmatter: title/category/tags/author/created/updated）。
- 前端：左栏分类 + 搜索 + 文档列表 + 弹窗编辑器(左写右预览) + 导出/导入 JSON 备份。
- 后端 API：`/api/kb/categories`(含计数), `/api/kb/docs`(按分类/搜索过滤), `/api/kb/doc`(CRUD), `/api/kb/render`(Markdown→HTML), `/api/kb/export`, `/api/kb/import`。
- **AI 维护约定**：agent 可经同一 `/api/kb/doc` 写入（`author: "ai"`），但绝不自动灌，只在用户点头时写。文档上 `author` 显示来源（human/ai），用户可随时分辨/删改。
- **与同步方向的关系**：知识库纯本地，不和任何项目的 SSH 同步混在一起，不触碰你的「云端开发→下载、本地开发→上传」策略。
- 默认 6 分类沿用原占位：架构设计/开发文档/运维笔记/AI工具指南/项目模板/灵感收集，可新建、可扩展。

## 构建注意事项（重要）
- 实时测速 `speed-test` 已改为 **TCP 端口(22)探测 + 并行**（绕过阿里云/腾讯云 ICMP 拦截，原 ICMP ping 误报"超时"）。
- **后台 `npm run build` 易孤儿化**：任务记录会丢失，但 `node.exe`(electron-builder) 进程残留并锁住 `dist/Personal-AI-Dev-Center.exe`，导致 exe 停在旧时间戳、新包写不进。重建前务必先 `tasklist` 找残留 `node.exe` 并 `taskkill /F` 结束；同时确认没有「Personal AI Dev Center」主程序在跑（也会锁 exe）。
- 验证打包是否含密钥：portable exe 是 7z 压缩自解包，字节搜不到明文；应改验 `dist/win-unpacked/resources/app.asar`（未压缩，搜真实 IP/Key）+ `resources/` 目录（应只有 config.example.json）。
