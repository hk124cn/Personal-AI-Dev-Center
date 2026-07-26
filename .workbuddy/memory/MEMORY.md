# Personal AI Dev Center — 项目长期记忆

## 项目定位
本地「多服务器 / 多项目管理面板」。技术栈：Electron 外壳 + FastAPI 后端（localhost:8765）+ 纯静态 SPA（index.html，无构建步骤）+ SSH/Paramiko 同步引擎 + 可选 LLM 分析。
用途：管理 4 台服务器、十几个远程项目的 Markdown 文档（TODO/PROGRESS/ISSUES）同步、双向文件同步（tar-over-SSH）、LLM 智能分析、域名/资产台账。

## 维护交接
- 前任维护者：**Qorder**。2026-07-26 起由我（WorkBuddy）接手，owner=千问老大(qw_20)。

## 开发与发布流程
- **无 git 仓库**（整目录无版本控制）。
- 本地运行：`pip install -r backend/requirements.txt` → `python backend/app.py`。Electron 启动时会 spawn 该 python。
- 发布：`npm run build`（portable exe，dist/）→ 或 `npm run build:setup`（NSIS）。产物约 75MB，会把 config.json/index.html/backend 一起打进 exe。
- 打包后首次运行：把随包 config.json 复制到 `%APPDATA%/Personal AI Dev Center/config.json`，之后以 APPDATA 版本为准。

## 关键架构约定
- config.json = 服务器/项目清单的 source of truth；backend/data/latest.json = 同步结果。
- 同步是**手动触发**（UI 按钮 /api/sync、单项目 /api/sync/{id}、CLI sync.py），**没有自动调度**。
- LLM 配置支持多套（llm_configs + llm_active_id），自动 sync 时把激活配置写回 legacy `llm` 字段。
- 当前 LLM 用商汤 SenseNova（OpenAI 兼容），非 docs 里写的 Anthropic/Qwen。

## 已知风险 / 待修
- config.json 含真实服务器 IP、SSH key 路径、API Key，会随 exe 分发 → 安全债。
- 版本号漂移：真实 1.4.11，但 electron「关于」与 FastAPI app.version 写死 1.0.0。
- latest.json（07-20）比 config 多 3 个项目，下次同步会丢。
- docs 多处与现状不符（提供商、sync.py 参数）。
