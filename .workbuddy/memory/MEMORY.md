# Personal AI Dev Center — 项目长期记忆

## 定位 / 维护
- 本地「多服务器 / 多项目管理面板」：Electron 外壳 + FastAPI(127.0.0.1:8765) + 纯静态 SPA(index.html，无构建) + Paramiko SSH 同步(tar-over-SSH) + 可选 LLM(商汤 SenseNova，OpenAI 兼容)。
- owner=千问老大(qw_20)，2026-07-26 由 WorkBuddy 接手（前任 Qorder）。

## 开发 / 发布 / 推送
- **推送规则（用户明确）**：不主动推 GitHub，仅用户要求才推；本地 commit 照常做、做完告知即可。远程 `git@github.com:hk124cn/Personal-AI-Dev-Center.git`(public, main)。
- 本地跑：`pip install -r backend/requirements.txt` → `python backend/app.py`。Electron 启动时 spawn 该 python（R5 计划改 PyInstaller 把后端打进 exe 内置）。
- 发布：`npm run build`(portable, dist/) 或 `npm run build:setup`(NSIS)。package.json `extraResources` 把 `backend/`、`index.html`、`config.example.json`(脱敏) 复制进 `resources/`；真实 `config.json` 不进包（运行时读 `%APPDATA%/Personal AI Dev Center/config.json`）。
- ⚠️ **构建坑**：`npm run build` 后 `node.exe`(electron-builder) 易孤儿化并锁 `dist/Personal-AI-Dev-Center.exe`；重建前先 `tasklist`/`taskkill` 清残留 node.exe + 关闭 live app。`dist/` 与 `dist_new/` 两目录并存，清理时只删 `dist_new`。验证打包是否含密钥：解包 `resources/` 查（不要只 grep 压缩的 asar）。

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
| R5 | 后端依赖本机 python | ⬜ 待做(PyInstaller，硬骨头) |
| R6 | 配置/数据并发写非原子 | ✅ atomic_write_json + 锁 |
| R7 | 知识库 Markdown XSS | ✅ sanitize_html |
| R8 | 全量同步阻塞/子进程残留 | ✅ Popen+超时杀进程树(_kill_proc_tree) |
| R9 | 扫描阶段不响应取消 | ✅ 扫描函数增量读+周期检查 cancel_event |
| R10 | 取消注册表缺锁 | ✅ 加锁 |
- 验证脚本：`test_security_robustness.py`(19 项)、`test_r8_r9.py`(12 项)。详见 `docs/system-review.md`。
- **剩余唯一待做：R5（PyInstaller 把后端打进 exe，换无 python 机器也能跑）**——硬骨头，建议单独一轮充分测，本机有 python 当前无需。打包杂项（目录选择改 Electron dialog + 清 dist_new）已完成。

## 模块要点
- **知识库**：`%APPDATA%/…/knowledge/<分类>/<id>.md`；API `/api/kb/*`；AI 可经 `/api/kb/doc` 写(`author:"ai"`) 但绝不自动灌，经用户点头才写。
- **Agent 管理**：`config.json` 顶层 `agents[]`；防撞车按(厂商,模型,key)分组 ≥2 标红；前端加载函数 `fetchConfig`（非 loadConfig）。
- **资产**：四类 sim/credit_card/email/membership，存 `config.json.assets`；跨类关联 `linked_phone_id`/`linked_email_id`(主键-外键，删时清悬空)；统计卡按汇率折¥；点击出 `viewAsset` 详情卡而非直接编辑。
- **下载引擎**：`_remote_stat_tree_fast`(远程 `find` 只读) + tar stdin 清单 + `tarfile` 流式 + stderr 后台排空防死锁 + `_DOWNLOAD_CANCEL` 取消。
- **上传引擎**：`_local_stat_tree` 比对 + 流式 tar 写 SSH stdin + `shlex.quote` + `_UPLOAD_CANCEL` 取消；支持 force/selected_files/sync_all。
