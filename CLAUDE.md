# CLAUDE.md

> **本仓库的权威上下文说明在 [`AGENTS.md`](./AGENTS.md)，请以它为准。**
>
> 这里不再重复维护一份副本 —— 历史上 `AGENTS.md` 与 `CLAUDE.md` 各存一份近乎相同的长篇内容，
> 结果两边一起过时（还在描述早已不存在的目录结构，完全没提 Electron 打包、后端 exe、
> 安全加固 R1–R10、知识库/资产/Cron/监控等模块）。
> 为避免再次分叉，内容统一收敛到 `AGENTS.md`。

开始改代码前，请务必先读 `AGENTS.md` 的这两节：

- **§1 铁律** —— 仓库是 public，禁止把真实服务器 IP / SSH 用户名 / 私钥路径 / API Key / 密码
  写进任何入库文件（**包括 `.workbuddy/memory/*.md`**）；`config.json` 永不提交；
  **不主动 push GitHub**（本地 commit 完成后告知用户即可）。
- **§5–§6 打包与进程模型** —— `dist/` 会被运行中的 app 锁住；
  两个同名的 `devcenter-backend.exe` 是 PyInstaller onefile 的正常形态；
  **绝不按镜像名 `taskkill`**（会把自己杀掉）。

其余章节：常用命令（§3）、架构地图（§4）、前端约定与两个固定坑（§7）、
如何验证（§8，含视觉回归的既定路线）、数据落在哪（§9）、版本号纪律（§10）。
