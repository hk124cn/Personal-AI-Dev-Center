# 1Panel 功能复刻分析 & 实施计划

> 目标：从专业服务器运维面板 1Panel 中，筛选**适合我们系统（个人多服务器开发/同步中心）**的功能进行复刻/吸收，明确实现难度、条件与所需技术变更。
> 状态：分析 + 计划阶段，尚未实现。

---

## 0. 定位对比

| 维度 | 1Panel | 我们的系统（Personal AI Dev Center） |
|------|--------|--------------------------------------|
| 形态 | Web 运维面板（Go + Vue3，Core-Agent 架构，agent 装在被管机） | 桌面端（Electron + FastAPI + 静态 SPA + Paramiko SSH） |
| 核心定位 | 在一台面板**运维/部署**服务器（应用、DB、容器、网站、加固） | **同步你自己项目**到多台服务器 + 个人资产 + 知识库/LLM |
| 我们的多服务器现状 | — | config.json 为服务器 source of truth；tar-over-SSH 同步；仅"连接速度测试"算监控 |

**关键结论**：1Panel 大半是"运维/部署"能力，与我们不在一个赛道；但"**看见并触达服务器**"这一类（监控、文件、终端、定时任务）正好补强我们最弱的"多服务器管理"环节。

---

## 1. 1Panel 功能模块分层判定

| 1Panel 模块 | 我们现状 | 判定 | 理由 |
|---|---|---|---|
| 主机监控（CPU/内存/磁盘IO/网络/负载） | 仅连接速度测试 | ✅ **推荐复刻** | 多服务器管理第一刚需，几乎空白 |
| 文件管理（Web SFTP 浏览/上传下载/编辑） | 仅整目录同步 | ✅ **推荐复刻** | 交互式单文件操作是同步的补充 |
| 计划任务（定时执行） | 无（App 内） | ✅ **推荐复刻（含两部分，见 §5.3）** | 定时同步 + 服务器侧 cron 管理 |
| 终端（Web SSH） | 无 | 🟡 **推荐但高难度（Phase 2）** | 多服务器管理杀手锏，技术改动大 |
| 监控告警（微信/钉钉/飞书） | 无 | 🟡 **可有可无** | 依赖监控先做，Phase 2 增值 |
| 进程管理 | 无 | 🟡 **可有可无** | 可并入主机监控 |
| 一键备份到云（S3/OSS/SFTP） | 仅本地 | 🟡 **可有可无** | 数据本就本地，云备加分项 |
| SSH 登录日志 | 无 | 🟡 **可有可无** | 并入监控的小附加 |
| 数据库管理（MySQL/PG/Redis） | 无 | ❌ **不适用** | 我们同步项目，不运维 DB |
| 容器管理（Docker） | 无 | ❌ **不适用核心** | 独立大领域，除非项目强依赖 |
| 应用商店（一键装 178 应用） | 无 | ❌ **不适用** | 我们同步"自己的项目"，不是部署现成应用 |
| 网站管理（域名/SSL） | 无 | ❌ **不适用** | 我们不是建站面板 |
| 防火墙 / 安全加固 | 无 | ❌ **不适用** | 主机加固超范畴且有风险 |
| 日志审计 | 无 | ❌ **不适用** | 单用户本地 App，审计价值低 |
| AI 模型托管（Ollama/GPU） | 走 API 的 LLM 分析 | ❌ **不适用** | 我们调 API 不托管模型；KB+LLM 是我们的差异化优势 |
| 多机 Agent 架构 | 多服务器配置 + SSH 直连 | ➖ **我们已有（架构不同，无需改）** | SSH 直连比装 agent 轻，保持 |

---

## 2. 推荐复刻项 · 实现评估（速览）

| 功能 | 难度 | 条件是否满足 | 技术是否需变更 | 建议阶段 |
|------|------|--------------|----------------|----------|
| 主机监控 | 中 | ✅ SSH 已有，只需新增"执行命令"封装+轮询 | ❌ 基本不用 | Phase 1 |
| 文件管理（浏览+上下载） | 高（地基已有） | 🟡 SFTP 客户端已有，缺前端树/编辑器 | 🟡 中等（新端点+前端） | Phase 1 |
| 定时同步 | 中 | ✅ 同步逻辑现成，只差定时器 | ❌ 不用 | Phase 1 |
| **服务器 cron 管理** | **中高** | ✅ SSH 已有；**需确认 sudo 能力** | ❌ 不用（纯 SSH 命令+前端表） | Phase 1 |
| 终端（Web SSH） | **高** | 🟡 Paramiko 能开 PTY，需 WebSocket | ✅ **需新增 WebSocket+PTY+xterm.js** | Phase 2 |
| 监控告警/进程/日志 | 中低 | ✅ 建立在监控之上 | ❌ 不用 | Phase 2 |

**技术栈结论**：除"终端"需新增 WebSocket+PTY+xterm.js 外，其余全部能在现有 Python(FastAPI)+Paramiko+Electron 栈内完成，无需换语言、无需引入 agent。

---

## 3. 必须保留的我们自己的优势（1Panel 没有）
- **个人资产管理**：SIM/信用卡/预付费卡/邮箱/会员，主从关联、总额折¥、注册站点跟踪。
- **知识库 + LLM 分析**：Markdown 知识库 + 商汤 SenseNova 分析（R7 已做 XSS 防护）。
- **项目同步引擎**：tar-over-SSH 下载/上传 + 取消机制（R2–R4/R8/R9 已硬化）。
- **免装 Python 的桌面分发**：R5 已完成。

---

## 4. 推进节奏
- **Phase 1（低风险、条件齐、收益高）**：① 主机监控 ② 文件管理（浏览+上下载，编辑可后置）③ 计划任务 = 定时同步 + 服务器 cron 管理。
- **Phase 2（需技术增量）**：④ 终端（Web SSH）⑤ 监控告警/进程/日志。
- **不做**：应用商店 / 网站 / 数据库 / 容器 / 防火墙 / 日志审计 / AI 模型托管。

---

## 5. 具体实施计划

### 5.1 主机监控
- **目标**：每台服务器一个资源仪表盘（CPU、内存、磁盘、网络、负载、磁盘IO），实时/近实时刷新。
- **后端方案**：
  - 新增 SSH "执行命令"封装 `run_remote(cmd, timeout)`，复用现有 Paramiko 连接池。
  - 采集命令：`cat /proc/loadavg`、`free -m`、`df -h`、`top -bn1`、`cat /proc/net/dev`、`iostat -x 1 1`（无 iostat 则降级）。
  - 解析为结构化 JSON；API：`GET /api/monitor/{server_id}`（即时快照）、`GET /api/monitor/{server_id}/history`（轮询点存内存/SQLite）。
  - 可选 WebSocket 推送实时流（与 Phase 2 终端共用通道技术）。
- **前端**：服务器列表页加"监控"入口 → 仪表盘卡片 + Chart.js 折线（每 3–5s 轮询）。
- **难度/条件/变更**：中 / ✅ / ❌。
- **验收**：连测试机，仪表盘数值与 `top`/`free` 一致；断连有提示。

### 5.2 文件管理
- **目标**：交互式浏览远程目录树，单文件上传/下载/删除/重命名/新建，文本文件在线查看与编辑（编辑可后置）。
- **后端方案**（复用 `_remote_stat_tree` 已有的 SFTP 能力）：
  - `GET /api/fs/{server_id}/list?path=` → SFTP `listdir_attr`。
  - `GET /api/fs/{server_id}/download?path=` → SFTP `get` 流式下载。
  - `POST /api/fs/{server_id}/upload` → SFTP `put` 流式上传（计入 R-UPLOAD-CANCEL 取消机制）。
  - `POST /api/fs/{server_id}/mkdir|rename|delete|write` → 对应 SFTP/文件写回。
- **前端**：左侧目录树 + 右侧文件列表/预览；编辑器用 CodeMirror/Monaco（轻量优先 CodeMirror）。
- **难度/条件/变更**：高（地基已有）/ 🟡 部分（缺前端树+编辑器）/ 🟡 中等。
- **验收**：浏览、下载、上传、改名、删文件均生效；大文件传输可取消。

### 5.3 计划任务（两部分）

#### 5.3.1 定时同步
- **目标**：按计划自动触发 `sync_all`（哪台/几点/频率）。
- **后端**：APScheduler 内置调度；config 增加 `schedules[]`；API：`GET/POST/DELETE /api/schedules`。
- **前端**：计划列表 + 增删改（cron 表达式或简易"每天/每小时"选择）。
- **难度/条件/变更**：中 / ✅ / ❌。
- **验收**：设定"每 30 分钟同步服务器 A"，到点自动同步且 latest.json 更新。

#### 5.3.2 服务器定时任务管理（远程 crontab 管理）★ 用户重点需求
- **目标**：在系统里**查看/编辑/增删服务器上的 cron 任务**——包括 root 的、各用户级的、系统级的；改完**自动回写服务器**生效。
- **读取（区分来源与用户）**：
  - **用户级 crontab**：`crontab -l [-u <user>]` 或读文件
    - Debian/Ubuntu：`/var/spool/cron/crontabs/<user>`
    - RHEL/CentOS：`/var/spool/cron/<user>`
    - 无 user 字段（每行直接是 `分 时 日 月 周 command`）。
  - **root 的 crontab**：`crontab -l`（root）或上述 root 文件。
  - **系统级**：
    - `/etc/crontab`：含 **USER 字段**（第 6 字段是用户）。
    - `/etc/cron.d/*`：同含 USER 字段。
    - `/etc/cron.hourly|daily|weekly|monthly/`：目录里的可执行脚本（非 crontab 格式，单独列出）。
  - **如何区分 root vs 用户级**：per-user crontab 无 user 字段；`/etc/crontab`、`/etc/cron.d` 第 6 字段即用户。
- **解析每条任务**：调度表达式（分 时 日 月 周）、user（若有）、command、是否启用（被 `#` 注释）、来源文件/类型、可选 human-readable 调度（"每天 03:30"）。
- **展示"做什么"**：原样显示 command；可选复用我们已有 LLM 接口对 command 做**一句话中文总结**（非必须，先不做）。
- **编辑回写（改完自动反应到服务器）**：
  - 用户级：`crontab -u <user> -`（从 stdin 写入）或写临时文件 `crontab -u <user> <file>`；root 用 `crontab -`。
  - 系统级 `/etc/cron.d/*`、`/etc/crontab`：直接写文件（**需 sudo**）。
  - **安全护栏（必须）**：
    1. 写前自动备份：`crontab -l [-u user] > /tmp/cron.bak.<ts>` 或 `cp` 源文件到备份目录。
    2. 本地语法校验：5 字段（用户级）或 6 字段（系统级）格式校验，拒绝非法行。
    3. 写回后读回验证（`crontab -l` 对比）。
    4. 失败回滚：用备份恢复。
    5. 危险操作确认弹窗（删除/禁用任务）。
  - **权限**：连接用户非 root 时需 `sudo`；需确认我们 config 的 SSH 凭据是否带 sudo 能力（实现前核查，必要时在服务器配置里加 `sudo_password` 或要求 key 已授权 NOPASSWD）。
- **API 草**：
  - `GET /api/cron/{server_id}/list` → 返回分组：root / 各 user / system（/etc/cron.d、/etc/crontab、cron.* 目录）。
  - `GET /api/cron/{server_id}/get?scope=root|user:<name>|system:<path>` → 该来源原始内容 + 解析后条目。
  - `POST /api/cron/{server_id}/save` → `{scope, content}` → 备份+校验+回写+读回验证。
  - `POST /api/cron/{server_id}/backup` → 手动备份。
- **前端**：
  - 分组展示（root / 用户A / 用户B / 系统），表格列：启用? | 调度 | 用户 | 命令 | 来源。
  - 点击某来源 → 文本区编辑（保留 crontab 语法，用户可直观改）或表单式编辑（调度+命令）。
  - "保存"按钮 → 调用 save，提示"已备份+已回写+已验证"。
- **难度/条件/变更**：中高（多来源解析 + 回写 + 权限 + 安全护栏）/ ✅ SSH 已有，**⚠️ 需确认 sudo 能力** / ❌ 不用换架构，纯后端 SSH 命令 + 前端表格。
- **验收**：连一台阿里云测试机 → 列出 root + 某普通用户 crontab + /etc/cron.d；编辑一条 → 服务器实际生效 → 系统读回一致；误改可回滚。

### 5.4（Phase 2）终端（Web SSH）
- **目标**：在 Electron 窗内嵌 xterm.js，经后端 WebSocket 连服务器 PTY，获得交互式 shell。
- **方案**：FastAPI `WebSocket` + `paramiko.Channel.get_pty()` + 后台线程双向转发；前端 xterm.js。
- **难度/条件/变更**：高 / 🟡 / ✅ 需新增 WebSocket+PTY+xterm.js。
- **验收**：打开终端，可执行命令、看输出、支持交互程序（如 `top`、`vim`）。

---

## 6. 技术栈结论
- 后端：现有 Python(FastAPI) + Paramiko 完全够用；仅终端需加 WebSocket（FastAPI 原生支持）。
- 前端：静态 SPA + Chart.js（监控）、CodeMirror（文件编辑）、xterm.js（终端）；无构建步骤，保持现状。
- 不引入 Go / agent / Docker 编排；保持 SSH 直连轻量模型。

## 7. 风险与注意
- **cron 回写权限**：非 root 用户需 sudo，实现前必须核查凭据能力，避免"看着能列但不能改"。
- **破坏性操作护栏**：cron 覆盖、文件删除、终端命令均可能严重影响服务器，统一加确认 + 备份 + 回滚。
- **不偏离定位**：吸收的是"触达服务器"能力，不碰部署/DB/容器/建站，护城河（资产/KB/LLM/同步）不受损。
- **Phase 1 优先低风险高收益**，终端等重技术增量放 Phase 2。
