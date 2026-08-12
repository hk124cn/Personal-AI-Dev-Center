# 系统体检与优化路线图

> 本地文档，不推送远程。最近一次全量代码审查的结论与处置记录。

## 一、风险清单（R1–R10）

| 编号 | 问题 | 影响 | 状态 |
|---|---|---|---|
| R1 | 后端 `uvicorn` 绑 `0.0.0.0` 且 CORS `*`：同网段可读 `/api/config/full`（含 SSH host、LLM api_key）、触发下载、开 SSH 终端 | 高（连公共 WiFi 即成立） | ✅ 已修 |
| R2 | 上传把整个目录 `io.BytesIO` 整包进内存，数万文件/数十 GB 必爆内存 | 高（大项目上传崩溃） | ✅ 已修 |
| R3 | 上传命令 `tar xf - -C '{remote_path}'` 仅套单引号，未 `shlex.quote`，命令注入 | 高（在服务器上执行任意命令） | ✅ 已修 |
| R4 | 上传无取消机制，点 × 只关窗口、后台继续传 | 高（大上传关不掉） | ✅ 已修 |
| R5 | 打包 exe 用 `spawn('python')` 启动后端，目标机无 python/不在 PATH 则起不来 | 中高（换机分发风险） | ⏸ 待处理 |
| R6 | `save_config` / `latest.json` 读写为读-改-写、非原子，并发可能损坏或互相覆盖 | 中 | ✅ 已修 |
| R7 | 知识库 `/api/kb/render` 不过滤原始 HTML，前端 `innerHTML` 直接渲染，`<script>`/`onerror` 会执行 | 中（分享知识库时危险） | ✅ 已修 |
| R8 | 全量同步 `/api/sync` 用 `subprocess.run(timeout=120)` 阻塞，超 120s 返 504 但子进程可能残留 | 中 | ⏸ 待处理 |
| R9 | 扫描阶段（`_remote_stat_tree_fast` 等）不响应取消，9 万文件扫描期点 × 要等扫完 | 中 | ⏸ 待处理 |
| R10 | `_DOWNLOAD_CANCEL` / `_UPLOAD_CANCEL` 字典并发访问缺锁，同项目并发下载/上传互相覆盖取消标志 | 低 | ✅ 已修 |

## 二、已修复明细

### 第一轮（v1.4.17 补丁，R1–R4）
- **R1**：`uvicorn` 由 `0.0.0.0` 改 `127.0.0.1`；CORS `allow_origins` 由 `*` 收紧为本机来源。前端 `loadURL('http://localhost:8765')` 同源，不受影响。
- **R2**：上传改流式 `tarfile.open(fileobj=stdin, mode='w|')` 逐文件写 SSH 通道（与下载对称），内存只留单文件。
- **R3**：`tar xf - -C {remote_path}` 改为 `shlex.quote(remote_path)`。
- **R4**：新增 `_UPLOAD_CANCEL` 注册表 + `POST /api/sync-upload-cancel/{id}` + 前端 `closeSyncProgress` 对称触发；`sync_upload` 加 `cancel_event` 与 `phase='cancelled'`。

### 第二轮（R6 / R7 / R10）
- **R7 知识库 XSS**：`/api/kb/render` 渲染后过一遍白名单消毒器 `sanitize_html()`（stdlib `html.parser` 自写，零新增依赖，兼容打包后的系统 python）。仅保留安全标签/属性，阻断 `<script>`/`<iframe>`/`on*` 属性、`javascript:`/`data:` 等危险 URL，脚本/样式内部文本一并丢弃。
- **R6 原子写入**：新增 `atomic_write_json()`——写**唯一临时文件**（`tempfile.mkstemp`）再 `os.replace` 原子替换；并对 `os.replace` 做有限重试以吸收 Windows 下并发替换偶发的“拒绝访问”。`save_config`（app.py / sync.py）与两处 `latest.json` 写入均改走它；并加 `_write_lock` 串行化写操作。
- **R10 取消注册表加锁**：新增 `_cancel_lock`（`threading.Lock`），`_DOWNLOAD_CANCEL` / `_UPLOAD_CANCEL` 的 get/set/pop 八处访问全部加锁。

## 三、待处理（建议下一轮）

1. **R5 打包健壮性（硬骨头）**：用 PyInstaller 把后端打进 exe，或启动时探测 python 完整路径；否则换台没装 python 的机器起不来。改动大、需重设计 Electron 启动方式，建议单独一轮 + 充分测。
2. **R8 全量同步改 SSE 流式**：`/api/sync` 不再 `subprocess.run(timeout=120)` 阻塞，改后台线程 + 进度流，超时也确保子进程被回收。
3. **R9 扫描期响应取消**：在 `_remote_stat_tree_fast` 与扫描循环里检查 `cancel_event`，点 × 不必等整轮扫描完。
4. **打包杂项**：目录选择改用 Electron `dialog`（现依赖 `tkinter`，精简 python 环境可能无此库）；清理 `dist/` 与 `dist_new/` 两个构建目录残留。

## 四、验证方式（每次改动后必做）

- **单元测试**：`test_security_robustness.py` 直接调用后端函数，覆盖 R7（注入 `<script>`/`onerror`/`javascript:` 被清除、合法 Markdown 保留）、R6（8 线程×50 次并发写仍为合法 JSON、无残留 `.tmp`）、R10（6 线程×200 次并发访问取消注册表无异常）。
- **HTTP 冒烟**：起后端后 `curl` 验证 `/api/health`、`/api/kb/render`（注入载荷应返回已消毒 HTML）、`/api/assets`、`/api/config/full` 均 200 且返回真实数据。
- **无头浏览器回归**：加载页面确认 0 控制台错误、0 页面异常（本机 sandbox 下 Chromium 偶发 teardown 崩溃，属环境限制；页面标题正常渲染即证明 SPA 可加载）。
- **打包验证**：`npm run build` 后解包 `dist/win-unpacked/resources/`（`backend/`、`index.html` 由 `extraResources` 复制，不在 asar 内），grep 确认改动已打入，并扫描无真实密钥/私钥泄漏。
