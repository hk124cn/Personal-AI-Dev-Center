# AGENTS.md — 项目上下文（AI 协作者必读）

本文件是本仓库**唯一的权威上下文说明**，适用于所有 AI 编码工具（WorkBuddy / Claude Code / Codex 等）。
`CLAUDE.md` 只是指向本文件的短引用，改内容请改这里。

维护者：千问老大（qw_20）。2026-07 起由 WorkBuddy（小汪）接手维护。

---

## 0. 这是什么

**Personal AI Dev Center** — 本地「多服务器 / 多项目管理面板」，Windows 桌面应用（便携版单 exe）。

- **Electron 外壳** → 拉起 **FastAPI 后端**（`127.0.0.1:8765`）→ 加载**纯静态 SPA**（`index.html`，无构建步骤）。
- 通过 **Paramiko SSH**（tar-over-SSH）在远端服务器上同步项目文件、读写计划任务、跑实时监控。
- 可选接入 LLM（商汤 SenseNova，OpenAI 兼容接口）做项目分析。

能力模块：服务器/项目管理 · 项目文件下载与上传 · 实时主机监控 · 计划任务(Cron)管理 ·
知识库 · Agent 配置管理（防撞车） · 个人资产（SIM/信用卡/邮箱/会员） · 定时同步调度。

---

## 1. 铁律 —— 动手前先确认这几条

1. **仓库是 public**（`github.com/hk124cn/Personal-AI-Dev-Center`）。
   严禁把**真实服务器 IP、SSH 用户名、私钥路径、API Key、密码**写进**任何会被 git 跟踪的文件**，
   包括 `.workbuddy/memory/*.md`（它们**在版本控制里**）。
   → 服务器一律用别名（如「腾讯云_上海」），确需写 IP 时用掩码 `47.xx.xx.xx`。
   → 2026-09-13 曾因写进记忆并推送而泄露，最终靠**删库重建**才彻底清除。别再犯。
2. **`config.json` 是真实用户数据，永不提交、永不进安装包**（已在 `.gitignore`）。
   打包只带脱敏的 `config.example.json`；运行时真实配置读 `%APPDATA%/Personal AI Dev Center/config.json`。
3. **不主动 push 到 GitHub**。用户明确要求：本地 commit 照常做，做完**告知**即可，等用户说推才推。
4. **不要删除 `.workbuddy/` 目录**（存项目记忆与技能，不是缓存）。
5. **不要按进程镜像名 `taskkill`**（会把自己杀掉，见 §6）。

---

## 2. 技术栈 / 环境

| 层 | 技术 |
|---|---|
| 外壳 | Electron 33 + electron-builder 25（`electron/main.js`） |
| 后端 | Python 3 + FastAPI 0.115 + uvicorn + Paramiko + markdown + requests |
| 前端 | 单文件 `index.html`（原生 JS，无框架、无打包） |
| 打包 | electron-builder（前端/整包）+ PyInstaller onefile（后端） |

依赖清单：`backend/requirements.txt`。**无 lint、无 pytest 配置**，测试是仓库里的自写脚本（见 §8）。

---

## 3. 常用命令

```bash
# 本地开发跑后端（浏览器访问 http://127.0.0.1:8765）
pip install -r backend/requirements.txt
python backend/app.py

# 或直接跑 Electron 壳（会自动 spawn 后端）
npm start

# 全量同步所有项目 / 单个项目
python backend/sync.py
python -c "from backend.sync import sync_single; sync_single('<project_id>')"

# —— 构建 ——
build_backend.bat                        # 后端 → backend/dist/devcenter-backend.exe
                                         # （双击即可；内部自建 backend/build_venv 并跑 PyInstaller）
npm run build                            # 整包 → dist/Personal-AI-Dev-Center.exe (portable)
npm run build:setup                      # NSIS 安装版

# —— 校验 ——
python tools/run_checks.py               # 统一回归入口（见 §8），改动后先跑这个
python tools/run_checks.py --fast        # 只跑静态检查（秒级：语法/结构/保密自查）
python tools/verify_build.py             # 打包产物完整校验（见 §8）
python backend/test_cron_manager.py      # cron 后端单测
```

> ⚠️ 需要第三方依赖的校验脚本会自动优先使用 `backend/build_venv` 里的解释器
> （含 paramiko / PyInstaller），所以请先跑过一次 `build_backend.bat` 把它建出来。

---

## 4. 架构地图

### 后端 `backend/`

| 文件 | 职责 |
|---|---|
| `app.py` | FastAPI 应用与**全部 HTTP 路由**；服务 `index.html`；配置与数据读写；下载/上传引擎；主机监控；知识库；资产 |
| `sync.py` | SSH 同步引擎（Paramiko + tar-over-SSH），产出 `data/latest.json` |
| `cron_manager.py` | 远程计划任务读写（三类来源、备份-校验-回写-回滚护栏） |
| `scheduler.py` | 定时同步调度（任务生命周期 + 进度） |
| `llm_analyzer.py` | 项目 Markdown → LLM 分析（OpenAI 兼容） |
| `common_paths.py` | **路径与版本号的唯一解析入口**（区分开发态 / PyInstaller 打包态） |
| `test_llm.py` / `test_cron_manager.py` | 诊断与单测脚本（非 pytest） |

### 前端

- `index.html` —— **整个前端就这一个文件**，含 HTML/CSS/JS。无框架、无构建、无 node_modules 依赖（除 lucide 图标 CDN）。
  改样式直接在文件内的 `<style>` 里改。

### 外壳

- `electron/main.js` —— 单实例锁、拉起/关闭后端、窗口、菜单。
- `electron/preload.js` —— 暴露给渲染进程的最小桥。

### 工具 `tools/`

- `run_checks.py` —— **统一回归入口**（改完代码先跑它，见 §8）。
- `verify_build.py` —— 打包产物校验。
- `gen_syncbar_variants.py` / `gen_mon_variants.py` —— 生成截图变体页。
- `shot_variants.py` —— 用系统 Chrome 无头给变体页批量截图。
- `test_syncbar.mjs` —— 前端同步状态条分支自测（从 `index.html` 抽真实函数执行）。
- `test_r8_r9.py` / `test_security_robustness.py` —— R6–R10 安全加固的集成测试。
- `test_cron_realmachine.py` —— 真机 cron 全链路（用 `/tmp` 探针，不碰真实 crontab）。
- `recreate_repo.py` —— GitHub 删库重建（清未引用对象，需 `GH_TOKEN` + `--confirm-delete`）。
- `count_md.py` —— 统计某目录下的 Markdown 文件（跟本应用本身无关的通用小工具）。
- `screenshot_syncbar.js` —— 早期的 Electron 截图方案，本机跑不通，留作备用。

### 其他

- `templates/` —— 远端项目的 Markdown 约定样例（`TODO.md` / `PROGRESS.md` / `ISSUES.md`）。
- `docs/` —— 设计与审查文档（`system-review.md` 记录 R1–R10 安全加固，`1panel-analysis.md` 是功能蓝图）。
- `start.bat` —— 仅起后端（不开 Electron）。
- `deploy_key.sh` / `deploy_key.ps1` —— 把本机公钥部署到配置的服务器上。

---

## 5. 打包与发布（最容易踩坑，重点看）

整包产物是**两段式**：Electron 壳 + 内嵌的后端 exe。

### 5.1 先构建后端 exe

`backend/dist/devcenter-backend.exe` 由 **仓库根目录的 `build_backend.bat`** 打出
（pyinstaller --onefile；它会自建 `backend/build_venv` 并装上 requirements + pyinstaller）。
`package.json` 的 `extraResources` 会把它拷进整包的 `resources/backend/`。

> 目标机**无需安装 Python** —— 这个 exe 自带运行时。Electron 启动时优先 spawn 它，
> 找不到才回退用系统 `python backend/app.py`。

### 5.2 再构建整包

```bash
npm run build        # → dist/Personal-AI-Dev-Center.exe  (portable, 约 381MB)
```

`extraResources` 打进 `resources/` 的内容：`backend/`（**排除** `__pycache__`、`data/`、`build_venv/`、`dist/`、`build/`）、
后端 exe、`index.html`、`config.example.json`。

### 5.3 三个必踩的坑

1. **app 在运行时锁住 `dist/`** —— 直接重建会失败。
   → 临时把 `package.json` 的 `build.directories.output` 改成 `dist_new`，构建后**务必改回 `dist`**。
2. **electron-builder 会让 `node.exe` 孤儿化**，并锁住 `dist/Personal-AI-Dev-Center.exe`。
   → 重建前先 `tasklist` / `taskkill` 清掉残留的 node.exe，并关掉正在运行的 app。
3. **构建完整命令别和 `rm` 写在同一行**（`rm x && npm run build`）—— 会触发沙箱 safe-delete 拦截。
   → 先单独一条命令删干净并确认，再单独跑构建。**不要**用 `dangerouslyDisableSandbox` 跑构建，
   那会让 portable 的压缩阶段被杀软拖到 15 分钟以上。

### 5.4 换新包的标准姿势（**不要直接删 dist**）

```
① mv dist dist_prev_<旧版本号>     # 改名保底
② mv dist_new dist                 # 同盘改名，秒完成，别去复制 381MB
③ python tools/verify_build.py     # 完整校验（会真启动后端 exe 探 /openapi.json）
④ 用户确认新版能正常启动后，才删 dist_prev_*
```

`dist_prev_*/` 已加进 `.gitignore`（便携版单个约 381MB，别误提交）。

---

## 6. 运行期进程模型（**别乱杀进程**）

正常进程树长这样，**看到两个同名 `devcenter-backend.exe` 是正常的**（PyInstaller onefile 的两段式）：

```
Personal-AI-Dev-Center.exe        启动器（便携版外壳）
└─ Personal AI Dev Center.exe     真实主进程
   ├─ …（渲染等子进程）
   └─ devcenter-backend.exe       onefile 引导父进程
      └─ devcenter-backend.exe    真实后端，LISTENING 8765
```

**启动逻辑（硬约束，勿"优化"）：**
- 启动时**只做检测**：`netstat -ano | findstr ":8765"` 看端口是否被占 + `tasklist` 核对镜像名；
  若已在跑 → `dialog.showErrorBox('已在运行')` 后 `app.quit()`，**把清进程的责任交给使用者**
  （提示框里已附 `Stop-Process -Name "devcenter-backend" -Force`）。
- **绝不**按镜像名 `taskkill`：便携版的启动器是主进程的父进程，`taskkill /T` 会顺树杀回自身。
- 退出时 `killBackend()` 用 `execSync('taskkill /PID <pid> /T /F')` 杀掉**自己 spawn 的后端树**，
  挂在 `before-quit` / `app.on('quit')` / `process.on('exit')` 三处兜底，确保 8765 被释放。

---

## 7. 前端约定（`index.html`）

- **无构建**：改完刷新即可，不需要 webpack/vite。
- **主题系统**：6 套主题（`violet` / `blue` / `emerald` / `amber` / `pink` / `light`），
  由 `applyTheme(name)` 把 CSS 变量写到 `document.documentElement`。
  `light` 是**浅色**主题（`isLight:true`）。
- **颜色一律走 CSS 变量**：`--accent` / `--amber` / `--blue` / `--border` / `--text` / `--text-secondary` 等。
  **不要在 JS 里硬编码深色值** —— 那在浅色主题下会直接看不见。
  canvas 绘图用 `cssVar('--x', fallback)` 取色。
- **CSS 必须写在 `<style>...</style>` 之内**。历史上出过「CSS 被插到 `</style>` 之后被当正文渲染」的事故。
  排查 UI 显示异常先数一下 `<style>` 开闭标签是否配平。
- **两个固定坑**：
  1. `escapeHtml` 会把 `'` 转成 `&#39;`，而 HTML 属性里的 `onclick` 又会把它解析回 `'`，
     从而**截断 JS 字符串** → 文本类参数**不要**塞进 `onclick`，用 `data-*` + 事件委托。
  2. lucide 图标由 `createIcons()` 运行时把 `<i data-lucide>` 替换成 SVG。
     **覆写 `innerHTML` 会让图标消失** → 只改内部 `<span>` 的文字。
- **同步状态条**：首页**已按用户要求不再显示**同步状态条，但 `fetchAPIData()` 仍会写 `state.lastSync`
  （后台查询保留），「定时同步」页仍渲染完整形态。改首页展示时别动管理页的形态。

---

## 8. 怎么验证（视觉回归的既定路线）

### 打包产物

```bash
python tools/verify_build.py
```
校验：源码 ↔ 产物逐一比对（含 index.html 新代码）、版本号一致、
**不含真实 `config.json` / `backend/data` 泄漏**、启动后端 exe 探 `/openapi.json` 数路由。
关键字的检查要**前后端分开传**：`--html-needle` / `--exe-needle`。

### 前端视觉

本机 Electron 在沙箱里**跑不起来**（GPU 进程崩 + `file://` 被拒 + 退出不干净），
所以统一走 **「变体页 + 系统 Chrome 无头」**：

```bash
python tools/gen_mon_variants.py        # 或 gen_syncbar_variants.py
python tools/shot_variants.py --all     # 或列具体变体名
```

生成变体页时**必须**：① 冻结页面自身定时器（否则注入状态会被它的轮询覆盖）；
② 禁用动画（`fade-in` 停在中间帧会让整页发暗）；③ 清掉 modal/toast。

Chrome 无头参数（`shot_variants.py` 已内置）：
`--headless=new --disable-gpu --no-sandbox --allow-file-access-from-files
--force-device-scale-factor=1.5 --virtual-time-budget=9000`

> 变体页注入后会把 `document.title` 设成 `READY-<模块>`；发现 `INJECT_ERR` 说明注入脚本报错。

### 一键回归

```bash
python tools/run_checks.py
```

依次跑：`index.html` 结构与内联 JS 语法 → `backend/*.py` 语法 → 保密自查（config.json 未被跟踪、
`.gitignore` 覆盖关键项）→ `test_syncbar.mjs` → `test_r8_r9.py` → `test_security_robustness.py`，
最后汇总通过数。**改完代码先跑它，再谈打包。**

> 其中「`</style>` 之后没有裸 CSS 泄漏」这条是专门盯 §7 那个历史事故的：
> 把 `</style>` 之后的 `<script>` 内容剔掉后，若还残留 `.类名{ 声明 }` 形状的文本即判失败。

### 前端逻辑

`tools/test_syncbar.mjs` —— 从 `index.html` **抽真实函数**执行，不写副本，
避免"测试通过但线上不对"。（新写前端自测请沿用这个做法。）

### 真机

`python tools/test_cron_realmachine.py` —— 连真机跑 cron 全链路，
用 `/tmp` 探针文件走写入分支，**不碰真实 crontab**。

---

## 9. 数据落在哪

| 内容 | 位置 |
|---|---|
| 服务器 / 项目 / Agent / 资产 / LLM 配置 | `config.json`（运行时为 `%APPDATA%/Personal AI Dev Center/config.json`） |
| 同步结果 | `backend/data/latest.json`（运行时在 APPDATA 下） |
| 知识库 | `%APPDATA%/Personal AI Dev Center/knowledge/<分类>/<id>.md` |
| 前端偏好（主题、状态条形态等） | 浏览器 localStorage |
| AI 协作者的项目记忆 | `.workbuddy/memory/`（**在版本控制里，见 §1**） |

**同步是手动触发的**（`/api/sync`、`/api/sync/{id}`、CLI），没有自动调度。
定时同步走 `scheduler.py`，由前端「定时同步」页管理。

---

## 10. 版本号纪律

版本号只有**两个**地方需要改，且必须一致：

1. `package.json` → `version`
   （Electron 读它做 app 版本；经 `DEV_CENTER_APP_VERSION` 环境变量注入后端；
   同时决定 portable 产物名）
2. `index.html` → `const APP_VERSION = 'x.y.z'`（界面左下角显示的那行）

> 后端**没有**硬编码版本号：`common_paths.py` 优先读 `DEV_CENTER_APP_VERSION`，
> 回退读 `package.json`，再回退 `1.0.0`。

改完两端后再走 §5 的构建流程，并用 `verify_build.py` 确认产物里的版本号一致。

---

## 11. 当前状态速览

- 版本：**1.4.22**
- 安全加固 R1–R10 **全部完成**（后端绑 127.0.0.1、流式上传、命令注入防护、
  取消机制、PyInstaller 内置后端、原子写配置、知识库 XSS 消毒、子进程树清理…），
  详见 `docs/system-review.md`。
- 待办与历史：见 `.workbuddy/memory/` 下的日记与 `MEMORY.md`。
