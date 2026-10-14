"""
Dev Center - SSH Sync Engine
通过 SSH 登录各服务器，读取项目目录下的 MD 文档，解析并写入本地 JSON。
支持双向增量文件同步（下载/上传），LLM 分析本地文件。
"""

import json
import re
import os
import io
import shlex
import stat
import sys
import threading
import time
import tarfile
import tempfile
import hashlib
from datetime import datetime
from pathlib import Path

# 让 backend 作为正式包可导入（dev 下需把仓库根加入 sys.path；打包后 backend 已被收集）
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Windows 控制台 GBK 编码无法打印 emoji 等 Unicode 字符，强制使用 UTF-8
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import socket

import paramiko

# Import LLM analyzer（优先按正式包导入，回退到脚本同目录导入，兼容两种运行方式）
try:
    from backend.llm_analyzer import analyze_project
except ImportError:
    try:
        from llm_analyzer import analyze_project
    except ImportError:
        analyze_project = None

# Paths（统一走 common_paths：config 落在 APPDATA，latest.json 落可写数据目录）
from backend.common_paths import CONFIG_PATH, DATA_DIR, LATEST_JSON, USER_DATA_DIR

BASE_DIR = Path(__file__).resolve().parent.parent  # 向后兼容（部分内部逻辑仍引用）
OUTPUT_PATH = LATEST_JSON

# MD files to look for in each project directory
MD_FILES = ["TODO.md", "PROGRESS.md", "ISSUES.md"]


# ==================== 同步进度追踪（实时回报给前端轮询） ====================
# 单例，记录一次同步任务的进展：当前项目、远程目录、各项目状态。
# 状态：pending(等待) / running(同步中) / done(完成) / error(失败)。

class SyncProgress:
    def __init__(self):
        self._lock = threading.Lock()
        self._running = False
        self._started_at = None
        self._finished_at = None
        self._scope = "all"
        self._total = 0
        self._current_id = None
        self._current_dir = None
        self._current_name = None
        self._projects = {}  # pid -> {id,name,server,server_name,remote_path,status,error,synced_at,llm_analyzed}

    def start(self, scope: str, project_list: list):
        """开始一次同步：重置并登记待同步项目列表。"""
        with self._lock:
            self._running = True
            self._started_at = datetime.now().isoformat()
            self._finished_at = None
            self._scope = scope
            self._total = len(project_list)
            self._current_id = None
            self._current_dir = None
            self._current_name = None
            self._projects = {}
            for p in project_list:
                self._projects[p["id"]] = {
                    "id": p["id"],
                    "name": p.get("name", p.get("id")),
                    "server": p.get("server"),
                    "server_name": p.get("server_name") or "",
                    "remote_path": p.get("remote_path") or "",
                    "status": "pending",
                    "error": None,
                    "synced_at": None,
                    "llm_analyzed": False,
                }

    def set_current(self, pid):
        """标记某个项目开始同步。"""
        with self._lock:
            pr = self._projects.get(pid)
            if pr is not None:
                pr["status"] = "running"
                self._current_dir = pr.get("remote_path")
                self._current_name = pr.get("name")
            self._current_id = pid

    def finish_project(self, pid, error=None, llm_analyzed=False):
        """标记某个项目同步结束（error 为 None 表示成功）。"""
        with self._lock:
            pr = self._projects.get(pid)
            if pr is not None:
                pr["status"] = "error" if error else "done"
                pr["error"] = error
                pr["synced_at"] = datetime.now().isoformat()
                pr["llm_analyzed"] = bool(llm_analyzed)
            if self._current_id == pid:
                self._current_id = None
                self._current_dir = None
                self._current_name = None

    def done(self):
        """整次同步结束。"""
        with self._lock:
            self._running = False
            self._finished_at = datetime.now().isoformat()
            self._current_id = None
            self._current_dir = None
            self._current_name = None

    @property
    def running(self):
        with self._lock:
            return self._running

    def snapshot(self):
        """返回当前进度的可序列化快照。"""
        with self._lock:
            done = sum(1 for p in self._projects.values() if p["status"] in ("done", "error"))
            return {
                "running": self._running,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "scope": self._scope,
                "total": self._total,
                "done_count": done,
                "current_id": self._current_id,
                "current_dir": self._current_dir,
                "current_name": self._current_name,
                "projects": [dict(p) for p in self._projects.values()],
            }


# 全局单例（同一进程内，scheduler / API 共享）
sync_progress = SyncProgress()


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# 写入锁：避免配置/数据并发读写导致损坏或互相覆盖
_write_lock = threading.Lock()


def atomic_write_json(path, data):
    """原子写入 JSON：先写唯一临时文件再 os.replace 重命名，避免读到半截文件，
    也避免并发写同一文件时因共用临时文件名而产生竞争。Windows 下并发替换可能
    因文件被短暂占用而偶发“拒绝访问”，对替换步骤做有限重试以吸收瞬时竞争。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.stem + ".tmp", suffix="")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise
    last_err = None
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return
        except (PermissionError, OSError) as e:
            last_err = e
            time.sleep(0.02 * (_ + 1))
    try:
        os.unlink(tmp)
    except Exception:
        pass
    raise last_err


def save_config(config: dict):
    with _write_lock:
        atomic_write_json(CONFIG_PATH, config)


def parse_todo_md(content: str) -> dict:
    """解析 TODO.md，提取待办和已完成事项"""
    todos = []
    done = []
    current_section = None

    for line in content.splitlines():
        line_stripped = line.strip()

        if line_stripped.startswith("## "):
            header = line_stripped[3:].strip()
            if "完成" in header or "done" in header.lower():
                current_section = "done"
            else:
                current_section = "todo"
            continue

        # Match checkbox items: - [ ] text  or  - [x] text
        match = re.match(r"^\s*-\s*\[(x|X| )\]\s+(.+)", line_stripped)
        if match:
            checked = match.group(1).lower() == "x"
            name = match.group(2).strip()
            if name.startswith("示例：") or name.startswith("示例:"):
                continue
            item = {"name": name, "done": checked}
            if checked:
                done.append(item)
            else:
                todos.append(item)

    return {
        "pending": todos,
        "completed": done,
        "total": len(todos) + len(done),
        "done_count": len(done),
    }


def parse_progress_md(content: str) -> dict:
    """解析 PROGRESS.md，提取进度和里程碑"""
    progress_pct = 0
    milestones = []
    last_update = ""
    recent_updates = []

    for line in content.splitlines():
        line_stripped = line.strip()

        # 进度: - 当前进度: 60%
        pct_match = re.search(r"当前进度[：:]\s*(\d+)%", line_stripped)
        if pct_match:
            progress_pct = int(pct_match.group(1))

        # 最后更新
        update_match = re.search(r"最后更新[：:]\s*(.+)", line_stripped)
        if update_match:
            last_update = update_match.group(1).strip()

        # 里程碑: - [ ] v0.1 - 描述  or  - [x] v0.1 - 描述
        ms_match = re.match(r"^\s*-\s*\[(x|X| )\]\s+(.+)", line_stripped)
        if ms_match and "进度" not in line_stripped:
            checked = ms_match.group(1).lower() == "x"
            milestones.append({"name": ms_match.group(2).strip(), "done": checked})

    return {
        "progress": progress_pct,
        "last_update": last_update,
        "milestones": milestones,
    }


def parse_issues_md(content: str) -> dict:
    """解析 ISSUES.md，提取问题列表"""
    current_issues = []
    pending_issues = []
    resolved_issues = []
    current_section = "current"

    for line in content.splitlines():
        line_stripped = line.strip()

        if line_stripped.startswith("## "):
            header = line_stripped[3:].strip()
            if "当前" in header or "current" in header.lower():
                current_section = "current"
            elif "待解决" in header or "pending" in header.lower():
                current_section = "pending"
            elif "已解决" in header or "resolved" in header.lower():
                current_section = "resolved"
            continue

        # Match list items: - text
        if re.match(r"^\s*-\s+\S", line_stripped):
            text = re.sub(r"^\s*-\s+", "", line_stripped).strip()
            if text.startswith("暂无"):
                continue
            if current_section == "current":
                current_issues.append(text)
            elif current_section == "pending":
                pending_issues.append(text)
            else:
                resolved_issues.append(text)

    return {
        "current": current_issues,
        "pending": pending_issues,
        "resolved": resolved_issues,
        "total": len(current_issues) + len(pending_issues),
    }


def parse_generic_md(content: str) -> dict:
    """通用 MD 解析器，提取 headers、checkboxes、items、progress、dates"""
    headers = []
    checkboxes = []
    items = []
    progress_pct = None
    dates = []

    for line in content.splitlines():
        line_stripped = line.strip()

        # H1 / H2 headers
        h_match = re.match(r"^#{1,2}\s+(.+)", line_stripped)
        if h_match:
            headers.append(h_match.group(1).strip())
            continue

        # Checkbox items: - [ ] text  or  - [x] text
        cb_match = re.match(r"^\s*-\s*\[(x|X| )\]\s+(.+)", line_stripped)
        if cb_match:
            checked = cb_match.group(1).lower() == "x"
            checkboxes.append({"name": cb_match.group(2).strip(), "done": checked})
            continue

        # Plain list items: - text (but not checkbox)
        li_match = re.match(r"^\s*-\s+(\S.+)", line_stripped)
        if li_match:
            text = li_match.group(1).strip()
            if text and not text.startswith("示例"):
                items.append(text)
            continue

        # Progress patterns: 进度: 60%, progress: 60%, 当前进度: 60%
        pct_match = re.search(r"(?:进度|progress|当前进度)[：:]\s*(\d+)%", line_stripped, re.IGNORECASE)
        if pct_match:
            progress_pct = int(pct_match.group(1))

        # Date/time patterns: 2024-01-15, 2024/01/15, 2024.01.15, etc.
        date_matches = re.findall(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:\s+\d{1,2}:\d{2})?", line_stripped)
        for d in date_matches:
            dates.append(d)

    return {
        "headers": headers,
        "checkboxes": checkboxes,
        "items": items,
        "progress": progress_pct,
        "dates": dates,
    }


PARSERS = {
    "TODO.md": ("todos", parse_todo_md),
    "PROGRESS.md": ("progress", parse_progress_md),
    "ISSUES.md": ("issues", parse_issues_md),
}


# 连接类（可重试）异常集合：网络抖动 / 会话失效 / 超时 / 拒绝连接等
_RETRYABLE_SSH_EXC = (
    paramiko.SSHException,
    socket.timeout,
    socket.error,
    ConnectionError,
    EOFError,
)
# 明确不可重试的错误关键词（鉴权 / 密钥 / host key 等配置问题）
_NON_RETRYABLE_SSH_KEYWORDS = (
    "authentication", "auth fail", "permission denied", "invalid key",
    "not a valid key", "key cannot be loaded", "host key", "known hosts",
)
# SSH 连接重试次数（首次 + 重试，指数退避 3s / 6s）
_SSH_RETRY_MAX = 3


def ssh_read_files(server: dict, project: dict) -> dict:
    """SSH 到服务器读取项目 MD 文件（自动发现所有 .md 文件）。

    对连接类错误（网络抖动 / 会话失效 / 超时 / 拒绝连接）做指数退避重试，
    最多 _SSH_RETRY_MAX 次；鉴权失败等非连接类错误不重试，直接返回失败。
    """
    last_exc = None
    for attempt in range(1, _SSH_RETRY_MAX + 1):
        try:
            result = _ssh_read_once(server, project)
            result["ssh_attempts"] = attempt
            result["ssh_retried"] = attempt > 1
            return result
        except _RETRYABLE_SSH_EXC as e:
            last_exc = e
            msg = str(e).lower()
            # 鉴权等明确不可重试的错误：直接返回失败，不浪费重试
            if any(k in msg for k in _NON_RETRYABLE_SSH_KEYWORDS):
                return {
                    "todos": None, "progress": None, "issues": None,
                    "md_files": [], "summaries": {}, "raw_files": {},
                    "error": str(e), "auth_error": True,
                    "ssh_attempts": attempt, "ssh_retried": attempt > 1,
                }
            if attempt < _SSH_RETRY_MAX:
                wait = 3 * attempt  # 3s, 6s 指数退避
                print(f"[sync] SSH 连接失败（第 {attempt} 次尝试），{wait}s 后重试: {e}")
                time.sleep(wait)
            else:
                print(f"[sync] SSH 连接失败，已重试 {_SSH_RETRY_MAX} 次: {e}")

    return {
        "todos": None, "progress": None, "issues": None,
        "md_files": [], "summaries": {}, "raw_files": {},
        "error": str(last_exc) if last_exc else "unknown ssh error",
        "ssh_attempts": _SSH_RETRY_MAX, "ssh_retried": True,
    }


def _shell_path(p: str) -> str:
    """把远程路径转成可安全嵌入 shell 命令的表达式。

    ⚠️ shlex.quote() 会连 `~` 一起引起来，导致 `~` 不被展开。
    历史 bug：remote_path 写成 `~/xxx` 的项目，find / cat / stat 全部静默失败
    （find 返回空、stat 返回 0 → remote_mtime 变成 1970），同步读不到任何内容。
    这里对行首 `~` 做显式展开。
    """
    p = (p or "").strip()
    if p == "~":
        return '"$HOME"'
    if p.startswith("~/"):
        return '"$HOME"/' + shlex.quote(p[2:])
    return shlex.quote(p)


def _iso_local(ts):
    """epoch 秒 → 带本地时区偏移的 ISO 字符串（前端 new Date() 可直接解析）。"""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts)).astimezone().isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _ssh_read_once(server: dict, project: dict) -> dict:
    """单次 SSH 读取项目 MD 文件（自动发现所有 .md 文件）。

    约定：连接类错误（网络抖动 / 会话失效 / 超时 / 拒绝连接）直接 raise，
    由 ssh_read_files 决定是否重试；鉴权失败、文件解析等非连接类错误写入
    result["error"] 后正常返回，不重试。
    """
    result = {
        "todos": None,
        "progress": None,
        "issues": None,
        "md_files": [],
        "summaries": {},
        "raw_files": {},
        "error": None,
        "content_mtime": None,
    }

    try:
        key_path = os.path.expanduser(server["key_path"])
        key = paramiko.Ed25519Key.from_private_key_file(key_path)

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=server["host"],
            port=server["port"],
            username=server["user"],
            pkey=key,
            timeout=15,
        )

        # remote_path is already an absolute path, use it directly
        project_dir = project['remote_path']

        # --- Step 1: Auto-discover all .md files in project directory ---
        stdin, stdout, stderr = client.exec_command(
            f"find {_shell_path(project_dir)} -maxdepth 1 -name '*.md' -type f"
        )
        find_output = stdout.read().decode("utf-8", errors="replace").strip()
        discovered_paths = [p.strip() for p in find_output.splitlines() if p.strip()]

        # Build list of (filename, shell-ready path) pairs
        md_entries = []
        for fpath in discovered_paths:
            fname = fpath.rsplit("/", 1)[-1]  # basename
            md_entries.append((fname, shlex.quote(fpath)))

        # Ensure we also attempt the 3 known files even if find returns nothing
        known_files = {"TODO.md", "PROGRESS.md", "ISSUES.md"}
        discovered_names = {fname for fname, _ in md_entries}
        for kf in known_files:
            if kf not in discovered_names:
                md_entries.append((kf, f"{_shell_path(project_dir)}/{shlex.quote(kf)}"))

        result["md_files"] = sorted(set(fname for fname, _ in md_entries))

        # --- Step 2: Read and parse each MD file ---
        for md_file, remote_path in md_entries:
            try:
                stdin, stdout, stderr = client.exec_command(f"cat {remote_path}")
                content = stdout.read().decode("utf-8", errors="replace")
                err = stderr.read().decode("utf-8", errors="replace")

                if content and "No such file" not in err:
                    result["raw_files"][md_file] = content

                    # Use specialized parser for known files
                    if md_file in PARSERS:
                        key_name, parser = PARSERS[md_file]
                        result[key_name] = parser(content)
                    else:
                        # Use generic parser for extra MD files
                        generic = parse_generic_md(content)
                        result["summaries"][md_file] = {
                            "headers": generic["headers"],
                            "checkboxes": generic["checkboxes"],
                            "items": generic["items"],
                        }
                        # If this file has progress info and we don't have one yet
                        if generic["progress"] is not None and result["progress"] is None:
                            result["progress"] = {
                                "progress": generic["progress"],
                                "last_update": "",
                                "milestones": [],
                            }
                        # Aggregate checkbox items into todos if no TODO.md found
                        if generic["checkboxes"] and result["todos"] is None:
                            pending = [
                                {"name": cb["name"], "done": cb["done"]}
                                for cb in generic["checkboxes"]
                                if not cb["done"]
                            ]
                            completed = [
                                {"name": cb["name"], "done": cb["done"]}
                                for cb in generic["checkboxes"]
                                if cb["done"]
                            ]
                            result["todos"] = {
                                "pending": pending,
                                "completed": completed,
                                "total": len(pending) + len(completed),
                                "done_count": len(completed),
                            }
                        # Aggregate list items into issues if no ISSUES.md found
                        # ⚠️ 仅当文件名本身就暗示「问题清单」时才兜底。
                        # 历史 bug：无条件兜底会把任意文档的 bullet 列表（架构说明、因子清单…）
                        # 当成「问题」，再被前端写进功能清单并持久化到 config.json，
                        # 把真实功能列表整份覆盖掉（曾污染 9 个项目共 292 条）。
                        _issue_like = ("issue" in md_file.lower()) or ("问题" in md_file)
                        if _issue_like and generic["items"] and result["issues"] is None:
                            result["issues"] = {
                                "current": generic["items"],
                                "pending": [],
                                "resolved": [],
                                "total": len(generic["items"]),
                            }
                else:
                    result["raw_files"][md_file] = None

            except Exception as e:
                result["raw_files"][md_file] = f"Error: {str(e)}"

        # 获取目录最后修改时间
        stdin, stdout, stderr = client.exec_command(
            f"stat -c '%Y' {_shell_path(project_dir)} 2>/dev/null || echo '0'"
        )
        mtime_str = stdout.read().decode().strip()
        try:
            mtime = int(mtime_str)
            result["remote_mtime"] = datetime.fromtimestamp(mtime).isoformat() if mtime else None
        except (ValueError, OSError):
            result["remote_mtime"] = None

        # 内容最后改动时间：取目录内最新文件的 mtime，过滤规则与「下载」完全一致
        # （所以口径就是「能下载到的那份内容最近一次变动」）。
        # 之所以不用目录 mtime：目录 mtime 只在增删/改名文件时变，改已有文件内容不动它。
        prune = " -o ".join(f"-name {shlex.quote(d)}" for d in sorted(DEFAULT_SYNC_IGNORE))
        skip_ext = " ".join(
            f"! -name {shlex.quote('*' + e)}" for e in sorted(DEFAULT_SYNC_IGNORE_EXT)
        )
        try:
            stdin, stdout, stderr = client.exec_command(
                f"cd {_shell_path(project_dir)} 2>/dev/null && "
                f"find . \\( {prune} \\) -prune -o -type f {skip_ext} "
                f"-printf '%T@\\n' 2>/dev/null | sort -rn | head -1",
                timeout=180,
            )
            newest = stdout.read().decode("utf-8", errors="replace").strip()
            result["content_mtime"] = float(newest.splitlines()[0]) if newest else None
        except Exception as e:
            print(f"[sync] 内容时间探测失败（不影响主流程）: {e}")
            result["content_mtime"] = None

        client.close()

    except paramiko.AuthenticationException as e:
        # 鉴权失败属于配置问题，不应重试
        result["error"] = str(e)
        result["auth_error"] = True
    except _RETRYABLE_SSH_EXC as e:
        # 连接类错误向上抛，由 ssh_read_files 做退避重试
        raise
    except Exception as e:
        result["error"] = str(e)

    return result


# ---------------------------------------------------------------------------
# LLM 增量分析
#
# 每轮同步其实只有少数项目的 md 会变，旧实现却对每个项目都无条件发一次 LLM 请求；
# 一旦某个请求卡住（实测单项目挂了 77 分钟），整批同步就被拖死。
# 这里用「md 内容指纹 + 模型签名」判断能否复用上一轮结果：
#   · md 内容没变 且 用的还是同一个模型 → 直接复用，一个请求都不发
#   · md 改了 / 换了模型 → 重新分析
# ---------------------------------------------------------------------------
_LLM_PREV_CACHE = None

# 一次同步内 LLM 连续失败到此次数就熔断：本轮后续项目不再请求 LLM。
# 目的同上 —— 别让「卡住的请求」把整批同步拖成小时级。
LLM_BREAKER_THRESHOLD = int(os.environ.get("DEV_CENTER_LLM_BREAKER", "2"))

# 项目之间的 LLM 请求节流（秒）。入门档模型普遍有 TPM/RPM 上限，
# 15 个项目连着甩过去必然撞 429（2026-10-14 实测）。设 0 可关掉。
LLM_PROJECT_SPACING = int(os.environ.get("DEV_CENTER_LLM_SPACING", "8"))


def _update_llm_breaker(consec_fail: int, breaker_open: bool, status: str,
                        error: str = None):
    """
    根据本次 LLM 结果推进熔断状态。

    · api_failed  → 连续失败 +1，达到阈值就打开熔断
    · analyzed    → 归零（真的发出请求且成功了，说明 provider 正常）
    · 其它（reused / skipped_breaker / disabled / missing_api_key …）
                  → 计数不变。特别是 **reused 不算成功**：它压根没发请求，
                    不能用来证明 provider 已经恢复。

    ⚠️ **429 限流不计入熔断**（2026-10-14 实测踩坑）：
    商汤对 AUTO-STOCK（46k 字符 / 29k token）直接返回
    `HTTP 429 inference exceeds tpm/rpm limit`。熔断原本把任何 api_failed 一律当
    「provider 挂了」，一轮下来把余下 13 个项目全标成 skipped_breaker —— 但 provider
    一直是好的，只是那 1 分钟的配额用完了。llm_analyzer 现在会先退避重试，
    真到重试用尽才返回失败；那种情况下计数照旧，但错误信息里带 429 字样。

    Returns:
        (新的连续失败数, 熔断是否打开)
    """
    # 限流是「等一会就好」，绝不能据此判定 provider 挂掉
    if status == "api_failed" and error and "429" in str(error) and "limit" in str(error).lower():
        return consec_fail, breaker_open
    if status == "api_failed":
        consec_fail += 1
        if consec_fail >= LLM_BREAKER_THRESHOLD:
            return consec_fail, True
    elif status == "analyzed":
        # 真的发出请求且成功了 → provider 正常：计数归零并撤销熔断
        return 0, False
    return consec_fail, breaker_open


def _md_fingerprint(md_contents: dict) -> str:
    """参与 LLM 分析的 md 内容指纹（按文件名排序后拼 name + 正文）。"""
    h = hashlib.md5()
    for name in sorted(md_contents or {}):
        h.update(name.encode("utf-8", errors="replace"))
        h.update(b"\x00")
        h.update((md_contents[name] or "").encode("utf-8", errors="replace"))
        h.update(b"\x01")
    return h.hexdigest()


def _llm_signature(llm_config: dict) -> str:
    """模型签名：provider / model / base_url 任一变化都必须重新分析。"""
    return "|".join(str(llm_config.get(k, "")) for k in ("provider", "model", "base_url"))


def _prev_llm_entry(project_id: str):
    """上一轮 latest.json 中该项目的条目（懒加载一次，整轮同步复用）。"""
    global _LLM_PREV_CACHE
    if _LLM_PREV_CACHE is None:
        cache = {}
        try:
            if OUTPUT_PATH.exists():
                with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for p in data.get("projects", []):
                    if p.get("id"):
                        cache[p["id"]] = p
        except Exception as e:
            print(f"[sync] 读取上轮同步结果失败（不影响本次同步）: {e}")
        _LLM_PREV_CACHE = cache
    return _LLM_PREV_CACHE.get(project_id)


def get_active_llm_config(config: dict) -> dict:
    llm_configs = config.get("llm_configs")
    active_id = config.get("llm_active_id")
    if isinstance(llm_configs, list) and active_id:
        active = next((c for c in llm_configs if c.get("id") == active_id), None)
        if active:
            llm_config = {k: v for k, v in active.items() if k not in {"id", "name"}}
            llm_config["enabled"] = True
            return llm_config
    return config.get("llm", {})


def apply_llm_analysis(project_name: str, remote_data: dict, config: dict,
                       project_id: str = None) -> dict:
    """
    应用 LLM 分析到远程数据

    Args:
        project_name: 项目名称
        remote_data: SSH 读取的原始数据
        config: 完整配置（包含 LLM 设置）

    Returns:
        更新后的 remote_data
    """
    llm_config = get_active_llm_config(config)

    remote_data["llm_analyzed"] = False
    remote_data["llm_status"] = "disabled"

    if not llm_config.get("enabled", False):
        print("[LLM] Skipped: disabled")
        return remote_data

    if not llm_config.get("api_key"):
        remote_data["llm_status"] = "missing_api_key"
        print("[LLM] Skipped: missing API key")
        return remote_data

    if not analyze_project:
        remote_data["llm_status"] = "module_unavailable"
        print("[LLM] Warning: llm_analyzer module not available")
        return remote_data

    md_contents = remote_data.get("raw_files", {})
    # 过滤掉 None 值（远程文件不存在或内容为空的情况）
    md_contents = {k: v for k, v in md_contents.items() if v is not None}
    if not md_contents:
        remote_data["llm_status"] = "no_markdown_content"
        print("[LLM] Skipped: no markdown content")
        return remote_data

    # 增量判断：md 内容指纹 + 模型签名都没变 → 直接复用上一轮结果，一个请求都不发
    fp = _md_fingerprint(md_contents)
    sig = _llm_signature(llm_config)
    remote_data["llm_input_fp"] = fp
    remote_data["llm_config_sig"] = sig

    prev = _prev_llm_entry(project_id) if project_id else None
    if (prev and prev.get("llm_analyzed")
            and prev.get("llm_input_fp") == fp
            and prev.get("llm_config_sig") == sig):
        remote_data["llm_features"] = prev.get("llm_features", [])
        remote_data["llm_architecture"] = prev.get("llm_architecture", {})
        remote_data["llm_routes"] = prev.get("llm_routes", [])
        remote_data["llm_summary"] = prev.get("llm_summary", "")
        remote_data["llm_tech_stack"] = prev.get("llm_tech_stack", [])
        # 首次分析时 LLM 会顺带结构化出 todos/issues，复用时要一起带回来
        if prev.get("todos"):
            remote_data["todos"] = prev["todos"]
        if prev.get("issues"):
            remote_data["issues"] = prev["issues"]
        remote_data["llm_analyzed"] = True
        remote_data["llm_status"] = "reused"
        print(f"[LLM] 复用上次结果（md 未变、模型未变）: {project_name}")
        return remote_data

    # 调用 LLM 分析
    llm_result, llm_error = analyze_project(project_name, md_contents, llm_config)

    if llm_result:
        if llm_result.get("issues"):
            issues = llm_result["issues"]
            issues["total"] = len(issues.get("current", [])) + len(issues.get("pending", []))
            remote_data["issues"] = issues

        if llm_result.get("todos"):
            todos = llm_result["todos"]
            todos["total"] = len(todos.get("pending", [])) + len(todos.get("completed", []))
            todos["done_count"] = len(todos.get("completed", []))
            remote_data["todos"] = todos

        # 添加新的字段：features, architecture, routes, summary
        remote_data["llm_features"] = llm_result.get("features", [])
        remote_data["llm_architecture"] = llm_result.get("architecture", {})
        remote_data["llm_routes"] = llm_result.get("routes", [])
        remote_data["llm_summary"] = llm_result.get("summary", "")
        # 提取 LLM 识别的技术栈
        arch = llm_result.get("architecture", {})
        remote_data["llm_tech_stack"] = arch.get("tech_stack", []) if isinstance(arch, dict) else []
        remote_data["llm_analyzed"] = True
        remote_data["llm_status"] = "analyzed"
        remote_data["llm_error"] = None

        if remote_data["llm_tech_stack"]:
            print(f"[LLM] 识别到技术栈: {', '.join(remote_data['llm_tech_stack'])}")

        print(f"[LLM] [OK] Analysis applied for {project_name}")
    else:
        remote_data["llm_analyzed"] = False
        remote_data["llm_status"] = "api_failed"
        remote_data["llm_error"] = llm_error or "未知原因"

    return remote_data


def sync_single(project_id: str) -> dict:
    """只同步指定项目，并实时回报进度到 sync_progress。"""
    config = load_config()
    servers = {s["id"]: s for s in config["servers"]}
    project = next((p for p in config["projects"] if p["id"] == project_id), None)
    if not project:
        return {"error": f"Project {project_id} not found"}

    server_id = project["server"]
    server = servers.get(server_id)
    if not server:
        return {"error": f"Server {server_id} not found"}

    # 若没有其它同步正在进行，则由本次调用掌管进度生命周期；否则只更新当前项目状态
    own = not sync_progress.running
    if own:
        pv = dict(project)
        pv["server_name"] = server.get("name", "")
        sync_progress.start("selected", [pv])
    sync_progress.set_current(project_id)

    try:
        print(f"[sync] {project['name']} @ {server['name']} ({server['host']})...")
        start = time.time()
        remote_data = ssh_read_files(server, project)

        # 应用 LLM 分析（如果启用）
        remote_data = apply_llm_analysis(project['name'], remote_data, config, project['id'])

        elapsed = round(time.time() - start, 2)

        # LLM 检测到技术栈时，自动更新 config.json
        llm_tech_stack = remote_data.get("llm_tech_stack", [])
        if llm_tech_stack:
            for i, p in enumerate(config["projects"]):
                if p["id"] == project_id:
                    config["projects"][i]["tech_stack"] = llm_tech_stack
                    save_config(config)
                    print(f"[sync] 已更新 {project['name']} 的技术栈: {', '.join(llm_tech_stack)}")
                    break

        # LLM 摘要回写到项目简介 desc（仅当 desc 为空时自动填充，避免覆盖手动内容）
        llm_summary = remote_data.get("llm_summary", "")
        if llm_summary:
            for i, p in enumerate(config["projects"]):
                if p["id"] == project_id:
                    if not p.get("desc"):
                        config["projects"][i]["desc"] = llm_summary
                        save_config(config)
                        print(f"[sync] 已用 LLM 摘要填充 {project['name']} 的简介")
                    break

        project_entry = {
            "id": project["id"],
            "name": project["name"],
            "server": server_id,
            "server_name": server["name"],
            "tech_stack": llm_tech_stack or project.get("tech_stack", []),
            "agent": project.get("agent", "-"),
            "synced_at": datetime.now().isoformat(),
            "sync_duration_sec": elapsed,
            "sync_error": remote_data["error"],
            "todos": remote_data["todos"],
            "progress": remote_data["progress"],
            "issues": remote_data["issues"],
            "md_files": remote_data.get("md_files", []),
            "summaries": remote_data.get("summaries", {}),
            "remote_mtime": remote_data.get("remote_mtime"),
            # 内容最后改动时间（远端最新文件 mtime，过滤规则同下载）
            "content_updated_at": _iso_local(remote_data.get("content_mtime")),
            # LLM 分析结果
            "llm_features": remote_data.get("llm_features", []),
            "llm_architecture": remote_data.get("llm_architecture", {}),
            "llm_routes": remote_data.get("llm_routes", []),
            "llm_tech_stack": llm_tech_stack,
            "llm_summary": remote_data.get("llm_summary", ""),
            "llm_input_fp": remote_data.get("llm_input_fp"),
            "llm_config_sig": remote_data.get("llm_config_sig"),
            "llm_analyzed": remote_data.get("llm_analyzed", False),
            "llm_status": remote_data.get("llm_status", "disabled"),
            "llm_error": remote_data.get("llm_error"),
        }

        if OUTPUT_PATH.exists():
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                output = json.load(f)
        else:
            output = {"projects": []}

        projects = output.get("projects", [])
        replaced = False
        for index, existing in enumerate(projects):
            if existing.get("id") == project_id:
                projects[index] = project_entry
                replaced = True
                break
        if not replaced:
            projects.append(project_entry)

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        output["projects"] = projects
        output["synced_at"] = datetime.now().isoformat()
        output["project_count"] = len(projects)
        output["server_count"] = len(set(p["server"] for p in projects if p.get("server")))
        with _write_lock:
            atomic_write_json(OUTPUT_PATH, output)

        status = "OK" if not remote_data["error"] else f"ERROR: {remote_data['error']}"
        llm_status = " +LLM" if remote_data.get("llm_analyzed") else ""
        print(f"  -> {status}{llm_status} ({elapsed}s)")
        sync_progress.finish_project(project_id, error=remote_data["error"], llm_analyzed=remote_data.get("llm_analyzed", False))
        return project_entry
    except Exception as e:
        sync_progress.finish_project(project_id, error=str(e), llm_analyzed=False)
        raise
    finally:
        if own:
            sync_progress.done()


def sync_all():
    """同步所有服务器上的所有项目，并实时回报进度到 sync_progress。"""
    config = load_config()
    servers = {s["id"]: s for s in config["servers"]}
    # 构建待同步项目列表（跳过无对应服务器的）
    pending = []
    for project in config["projects"]:
        server_id = project.get("server")
        if server_id not in servers:
            continue
        pv = dict(project)
        pv["server_name"] = servers[server_id].get("name", "")
        pending.append(pv)

    sync_progress.start("all", pending)
    projects_data = []
    llm_consec_fail = 0      # LLM 连续失败次数（仅 api_failed 计入）
    llm_breaker_open = False  # 熔断开关：打开后本轮不再请求 LLM
    try:
        for project in pending:
            server_id = project["server"]
            server = servers[server_id]
            sync_progress.set_current(project["id"])
            print(f"[sync] {project['name']} @ {server['name']} ({server['host']})...")

            start = time.time()
            remote_data = ssh_read_files(server, project)
            # 应用 LLM 分析（如果启用）
            if llm_breaker_open:
                remote_data["llm_analyzed"] = False
                remote_data["llm_status"] = "skipped_breaker"
                print(f"[LLM] 熔断中，跳过: {project['name']}")
            else:
                remote_data = apply_llm_analysis(project['name'], remote_data, config, project['id'])
                # 真发过请求才需要节流：reused 走本地复用，不占配额，别白等
                if remote_data.get("llm_status") in ("analyzed", "api_failed"):
                    time.sleep(LLM_PROJECT_SPACING)
                llm_consec_fail, llm_breaker_open = _update_llm_breaker(
                    llm_consec_fail, llm_breaker_open, remote_data.get("llm_status"),
                    remote_data.get("llm_error"))
                if llm_breaker_open:
                    print(f"[LLM] 连续失败 {llm_consec_fail} 次 → 打开熔断，"
                          f"本轮后续项目不再请求 LLM")

            elapsed = round(time.time() - start, 2)

            # LLM 检测到技术栈时，自动更新 config
            llm_tech_stack = remote_data.get("llm_tech_stack", [])
            if llm_tech_stack:
                for i, p in enumerate(config["projects"]):
                    if p["id"] == project["id"]:
                        config["projects"][i]["tech_stack"] = llm_tech_stack
                        print(f"[sync] 已更新 {project['name']} 的技术栈: {', '.join(llm_tech_stack)}")
                        break

            # LLM 摘要回写到项目简介 desc（仅当 desc 为空时自动填充，sync_all 末尾统一 save_config）
            llm_summary = remote_data.get("llm_summary", "")
            if llm_summary:
                for i, p in enumerate(config["projects"]):
                    if p["id"] == project["id"]:
                        if not p.get("desc"):
                            config["projects"][i]["desc"] = llm_summary
                            print(f"[sync] 已用 LLM 摘要填充 {project['name']} 的简介")
                        break

            project_entry = {
                "id": project["id"],
                "name": project["name"],
                "server": server_id,
                "server_name": server["name"],
                "tech_stack": llm_tech_stack or project.get("tech_stack", []),
                "agent": project.get("agent", "-"),
                "synced_at": datetime.now().isoformat(),
                "sync_duration_sec": elapsed,
                "sync_error": remote_data["error"],
                "todos": remote_data["todos"],
                "progress": remote_data["progress"],
                "issues": remote_data["issues"],
                "md_files": remote_data.get("md_files", []),
                "summaries": remote_data.get("summaries", {}),
                "remote_mtime": remote_data.get("remote_mtime"),
                # 内容最后改动时间（远端最新文件 mtime，过滤规则同下载）
                "content_updated_at": _iso_local(remote_data.get("content_mtime")),
                # LLM 分析结果
                "llm_features": remote_data.get("llm_features", []),
                "llm_architecture": remote_data.get("llm_architecture", {}),
                "llm_routes": remote_data.get("llm_routes", []),
                "llm_tech_stack": llm_tech_stack,
                "llm_summary": remote_data.get("llm_summary", ""),
                "llm_input_fp": remote_data.get("llm_input_fp"),
                "llm_config_sig": remote_data.get("llm_config_sig"),
                "llm_analyzed": remote_data.get("llm_analyzed", False),
                "llm_status": remote_data.get("llm_status", "disabled"),
                # ⚠️ 别漏这个：漏了的话 sync_all 的失败原因会被吞掉，
                # 只剩一个光秃秃的 api_failed，排查时完全看不出是哪一步挂的
                # （sync_single 那份早就有，这里曾长期不一致）
                "llm_error": remote_data.get("llm_error"),
            }
            projects_data.append(project_entry)

            status = "OK" if not remote_data["error"] else f"ERROR: {remote_data['error']}"
            llm_status = " +LLM" if remote_data.get("llm_analyzed") else ""
            print(f"  -> {status}{llm_status} ({elapsed}s)")
            sync_progress.finish_project(
                project["id"], error=remote_data["error"],
                llm_analyzed=remote_data.get("llm_analyzed", False),
            )
    finally:
        sync_progress.done()

    # 保存可能更新的技术栈到 config.json
    save_config(config)

    # 写入 JSON
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "synced_at": datetime.now().isoformat(),
        "server_count": len(set(p["server"] for p in projects_data)),
        "project_count": len(projects_data),
        "projects": projects_data,
    }
    with _write_lock:
        atomic_write_json(OUTPUT_PATH, output)

    llm_count = sum(1 for p in projects_data if p.get("llm_analyzed"))
    print(f"\n[sync] Done. {len(projects_data)} projects synced{f', {llm_count} with LLM analysis' if llm_count else ''} -> {OUTPUT_PATH}")
    return output


# ==================== 双向文件同步引擎 ====================

# 下载分批：每批一个独立 tar。0 表示不分批（回到旧的一次性行为，一般不建议）。
DL_BATCH_SIZE = int(os.environ.get("DEV_CENTER_DL_BATCH", "600"))
# 批间停顿秒数，避免连着开几十个 exec_command 把 SSH 通道窗口打满。
DL_BATCH_GAP = float(os.environ.get("DEV_CENTER_DL_BATCH_GAP", "0.5"))

DEFAULT_SYNC_IGNORE = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".DS_Store", "Thumbs.db", ".idea", ".vscode",
}
DEFAULT_SYNC_IGNORE_EXT = {
    ".csv", ".log",
    ".pyc", ".pyo", ".class", ".o", ".so",
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".exe", ".dll", ".whl",
}


def _should_ignore(rel_path: str) -> bool:
    """判断文件是否应该被忽略（只忽略特定目录，不屏蔽点文件如.env）"""
    parts = Path(rel_path).parts
    for part in parts:
        if part in DEFAULT_SYNC_IGNORE:
            return True
    if Path(rel_path).suffix.lower() in DEFAULT_SYNC_IGNORE_EXT:
        return True
    return False


def _sftp_connect(server: dict):
    """建立 SSH+SFTP 连接，返回 (client, sftp)。启用 keepalive 防止超时。"""
    key_path = os.path.expanduser(server["key_path"])
    key = paramiko.Ed25519Key.from_private_key_file(key_path)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=server["host"],
        port=server["port"],
        username=server["user"],
        pkey=key,
        timeout=30,
        banner_timeout=30,
        auth_timeout=30,
    )
    # 启用 keepalive，每 30 秒发送一次，防止长时间传输时连接超时
    transport = client.get_transport()
    if transport:
        transport.set_keepalive(30)
    sftp = client.open_sftp()
    return client, sftp


def _sftp_stat_tree(sftp, remote_dir: str, remote_root: str = "", sync_all: bool = False, cancel_event=None) -> dict:
    """递归获取远程目录下所有文件的 (mtime, size)，返回 {完整路径: (mtime, size)}。sync_all=True 时不做任何过滤。
    cancel_event 命中时提前停止遍历（返回已扫描的部分结果，调用方会在传输前用 _cancelled() 判定为取消）。"""
    if not remote_root:
        remote_root = remote_dir
    result = {}
    try:
        entries = sftp.listdir_attr(remote_dir)
    except Exception:
        return result
    for entry in entries:
        if cancel_event is not None and cancel_event.is_set():
            return result
        if entry.filename in (".", ".."):
            continue
        full = f"{remote_dir}/{entry.filename}"
        rel = full[len(remote_root):].lstrip("/")
        if stat.S_ISDIR(entry.st_mode):
            # 跳过忽略目录，不递归进去
            if not sync_all and entry.filename in DEFAULT_SYNC_IGNORE:
                continue
            result.update(_sftp_stat_tree(sftp, full, remote_root, sync_all))
        elif stat.S_ISREG(entry.st_mode):
            if not sync_all and Path(rel).suffix.lower() in DEFAULT_SYNC_IGNORE_EXT:
                continue
            result[full] = (entry.st_mtime or 0, entry.st_size or 0)
    return result


def _parse_find_record(out: dict, base: str, rec: bytes, sync_all: bool):
    """把一条 find -printf 记录写入 out（base/rel -> (mtime, size)）。"""
    if not rec:
        return
    parts = rec.split(b"\t", 2)
    if len(parts) != 3:
        return
    try:
        mtime = float(parts[0])
        size = int(parts[1])
    except ValueError:
        return
    rel = parts[2].decode("utf-8", "surrogateescape")
    if rel.startswith("./"):
        rel = rel[2:]
    if not rel or (not sync_all and _should_ignore(rel)):
        return
    out[f"{base}/{rel}"] = (mtime, size)


def _remote_stat_tree_fast(client, remote_path: str, sync_all: bool = False, cancel_event=None):
    """
    用一条远程 find 命令快速获取文件树 {完整路径: (mtime, size)}。
    只读操作，不在服务器上写任何文件。失败（非 GNU find 等）返回 None，调用方回退 SFTP 递归。
    sync_all=True 时不过滤任何目录/文件。
    """
    try:
        if sync_all:
            cmd = f"cd {shlex.quote(remote_path)} && find . -type f -printf '%T@\\t%s\\t%p\\0'"
        else:
            prune = " -o ".join(f"-name {shlex.quote(d)}" for d in sorted(DEFAULT_SYNC_IGNORE))
            cmd = (
                f"cd {shlex.quote(remote_path)} && "
                f"find . \\( {prune} \\) -prune -o -type f -printf '%T@\\t%s\\t%p\\0'"
            )
        stdin, stdout, stderr = client.exec_command(cmd, timeout=180)
        out = {}
        base = remote_path.rstrip("/")
        buf = b""
        while True:
            # 扫描期周期性检查取消：命中则关闭通道并提前返回（调用方以 _cancelled() 判定为取消）
            if cancel_event is not None and cancel_event.is_set():
                try:
                    stdout.channel.close()
                except Exception:
                    pass
                return None
            chunk = stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            # 解析已完整以 \0 结尾的记录，避免把整个输出堆在内存（大目录优化）
            while b"\0" in buf:
                rec, buf = buf.split(b"\0", 1)
                _parse_find_record(out, base, rec, sync_all)
        if buf:
            _parse_find_record(out, base, buf, sync_all)
        if not out:
            return None
        return out
    except Exception as e:
        print(f"[sync-download] 快速扫描失败，回退 SFTP 递归: {e}")
        return None


def _local_stat_tree(local_dir: str, sync_all: bool = False, cancel_event=None) -> dict:
    """获取本地目录下所有文件的 (mtime, size)，返回 {相对路径: (mtime, size)}。sync_all=True 时不过滤。
    cancel_event 命中时提前停止遍历（调用方会在传输前用 _cancelled() 判定为取消）。"""
    result = {}
    local_dir = os.path.normpath(local_dir)
    for root, dirs, files in os.walk(local_dir):
        if cancel_event is not None and cancel_event.is_set():
            return result
        # 跳过忽略目录（不屏蔽点文件）
        if not sync_all:
            dirs[:] = [d for d in dirs if d not in DEFAULT_SYNC_IGNORE]
        for f in files:
            if not sync_all and Path(f).suffix.lower() in DEFAULT_SYNC_IGNORE_EXT:
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, local_dir).replace("\\", "/")
            try:
                st = os.stat(full)
                result[rel] = (st.st_mtime, st.st_size)
            except OSError:
                pass
    return result


def sync_download(server: dict, project: dict, local_path: str, progress_cb=None, cancel_event=None, sync_all: bool = False) -> dict:
    """
    从远程服务器增量下载到本地目录。
    tar-over-SSH 批量传输；文件清单经 stdin 传给 tar（无命令行长度上限、不在服务器写任何文件），
    tar 流式解包（不缓存整个包到内存），逐文件上报进度，支持取消。
    """
    remote_path = project["remote_path"]
    result = {"downloaded": 0, "skipped": 0, "failed": 0, "errors": [], "total": 0}

    def _report(**kw):
        if progress_cb:
            try: progress_cb(kw)
            except Exception: pass

    def _cancelled():
        return cancel_event is not None and cancel_event.is_set()

    _report(phase='connecting')

    try:
        client, sftp = _sftp_connect(server)
    except Exception as e:
        result["errors"].append(f"连接失败: {e}")
        _report(phase='error', error=f"连接失败: {e}")
        return result

    try:
        print(f"[sync-download] 扫描远程 {remote_path} ...")
        _report(phase='scanning')
        # 优先一条 find 命令快速扫描（只读），失败回退 SFTP 递归
        remote_files = None if _cancelled() else _remote_stat_tree_fast(client, remote_path, sync_all=sync_all, cancel_event=cancel_event)
        if remote_files is None and not _cancelled():
            remote_files = _sftp_stat_tree(sftp, remote_path, sync_all=sync_all, cancel_event=cancel_event)
        if remote_files is None:
            remote_files = {}
        print(f"[sync-download] 远程文件数: {len(remote_files)}（sync_all={sync_all}）")
        local_files = _local_stat_tree(local_path, sync_all=sync_all, cancel_event=cancel_event) if os.path.isdir(local_path) else {}
        print(f"[sync-download] 本地文件数: {len(local_files)}")

        to_download = []  # list of (remote_full_path, relative_path)
        for remote_full, (r_mtime, r_size) in remote_files.items():
            rel = remote_full[len(remote_path):].lstrip("/")
            result["total"] += 1
            if rel in local_files:
                l_mtime, l_size = local_files[rel]
                if abs(l_mtime - r_mtime) < 3 and l_size == r_size:
                    result["skipped"] += 1
                    continue
            to_download.append((remote_full, rel))

        total_to_transfer = len(to_download)
        total_bytes = sum(remote_files[rp][1] for rp, _ in to_download)
        print(f"[sync-download] 需下载 {total_to_transfer} 个文件 ({total_bytes / 1024 / 1024:.1f}MB)，跳过 {result['skipped']} 个")

        if _cancelled():
            result["errors"].append("已取消")
            _report(phase='cancelled')
            return result

        n_batches_pre = ((len(to_download) + DL_BATCH_SIZE - 1) // DL_BATCH_SIZE) \
            if (to_download and DL_BATCH_SIZE > 0) else 1
        _report(phase='transferring', total=result["total"], to_transfer=total_to_transfer,
                skipped=result["skipped"], current=0, total_bytes=total_bytes,
                batches=n_batches_pre)

        if total_to_transfer == 0:
            _report(phase='done', **{k: result[k] for k in ('downloaded', 'skipped', 'failed', 'total', 'errors')})
            return result

        # --- 分批 tar-over-SSH ---
        # 为什么分批（2026-10-14 用户实测反馈）：
        #   一次性把 9 万多个文件塞进单个 tar，SSH 通道要连续跑几十分钟，
        #   前端只有「(0 / 95728)」干看着 → 用户以为卡死。而且**一个批次挂掉就全盘皆输**，
        #   前面下好的几千个文件随进程一起废掉。
        # 分批后：每批独立 tar，失败只影响那一批，且每批结束都能上报进度与成功信息。
        # 文件清单仍经 stdin 传入（--null -T -），不受命令行长度限制、不在服务器写文件。
        # DL_BATCH_SIZE<=0 时退化成不分批（旧行为）
        BATCH_SIZE = DL_BATCH_SIZE if DL_BATCH_SIZE > 0 else max(total_to_transfer, 1)
        chunks = [to_download[i:i + BATCH_SIZE] for i in range(0, len(to_download), BATCH_SIZE)]
        n_batches = len(chunks)
        print(f"[sync-download] 共 {total_to_transfer} 个文件，分 {n_batches} 批传输"
              f"（每批 ≤{BATCH_SIZE}，{total_bytes / 1024 / 1024:.1f}MB）")

        os.makedirs(local_path, exist_ok=True)
        transferred = 0
        current = 0
        last_report = 0.0
        t_start = time.time()
        cancelled = False

        for bi, chunk in enumerate(chunks, 1):
            if _cancelled():
                cancelled = True
                break

            chunk_bytes = sum(remote_files[rp][1] for rp, _ in chunk)
            cmd = f"tar --null -cf - -C {shlex.quote(remote_path)} -T -"
            if n_batches > 1:
                print(f"[sync-download] 第 {bi}/{n_batches} 批：{len(chunk)} 个文件 "
                      f"{chunk_bytes / 1024 / 1024:.1f}MB")

            batch_ok = True
            try:
                stdin, stdout, stderr = client.exec_command(cmd, bufsize=262144)

                # 后台线程持续排空 stderr，防止 tar 告警塞满通道窗口导致死锁
                err_chunks = []

                def _drain_err(ch=stderr, sink=err_chunks):
                    try:
                        while True:
                            b = ch.read(65536)
                            if not b:
                                break
                            sink.append(b)
                    except Exception:
                        pass

                err_thread = threading.Thread(target=_drain_err, daemon=True)
                err_thread.start()

                # 写入本批 null 分隔的相对路径清单，然后关闭写端让 tar 开始打包
                payload = b"".join(rel.encode("utf-8", "surrogateescape") + b"\0"
                                   for _, rel in chunk)
                stdin.write(payload)
                stdin.flush()
                stdin.channel.shutdown_write()

                # 流式解包：边下边写盘，不占内存，逐文件报进度
                with tarfile.open(fileobj=stdout, mode='r|') as tar:
                    for member in tar:
                        if _cancelled():
                            cancelled = True
                            break
                        if not member.isreg():
                            continue
                        rel = member.name
                        # 防路径穿越
                        if rel.startswith("/") or ".." in rel.split("/"):
                            continue
                        target = os.path.join(local_path, rel)
                        os.makedirs(os.path.dirname(target) or local_path, exist_ok=True)
                        f = tar.extractfile(member)
                        with open(target, 'wb') as out:
                            if f:
                                while True:
                                    buf = f.read(262144)
                                    if not buf:
                                        break
                                    if _cancelled():
                                        out.close()
                                        cancelled = True
                                        break
                                    out.write(buf)
                                    transferred += len(buf)
                        if cancelled:
                            break
                        os.utime(target, (member.mtime, member.mtime))
                        result["downloaded"] += 1
                        current += 1
                        now = time.time()
                        if now - last_report > 0.15 or current == total_to_transfer:
                            last_report = now
                            # batch 事件：前端靠它显示「已传/总量 + 速率」，
                            # 缺了它界面就只干巴巴一个 (0 / 95728)，看着像卡死。
                            _report(phase='batch', current=current, to_transfer=total_to_transfer,
                                    file=rel, bytes=transferred, total_bytes=total_bytes,
                                    batch=bi, batches=n_batches)
                            _report(phase='file', current=current,
                                    to_transfer=total_to_transfer, file=rel,
                                    bytes=transferred, total_bytes=total_bytes)

                err_thread.join(timeout=3)
                err_output = b"".join(err_chunks).decode("utf-8", errors="replace").strip()
                exit_status = stdout.channel.recv_exit_status()
                if exit_status != 0 and err_output:
                    print(f"[sync-download] tar stderr: {err_output[:500]}")
                    batch_ok = False
            except (IOError, OSError, EOFError) as e:
                err_msg = f"网络传输中断: {type(e).__name__}: {e}"
                print(f"[sync-download] {err_msg}")
                result["failed"] += len(chunk)
                result["errors"].append(err_msg)
                batch_ok = False
            except tarfile.TarError as e:
                print(f"[sync-download] 解包错误: {e}")
                result["failed"] += len(chunk)
                result["errors"].append(f"解包失败: {e}")
                batch_ok = False

            # 每批结束都上报一次成功信息 —— 用户明确要求「下面的成功信息也要有输出」
            elapsed = max(time.time() - t_start, 0.001)
            _report(phase='batch_done', batch=bi, batches=n_batches,
                    batch_ok=batch_ok, batch_files=len(chunk),
                    current=current, to_transfer=total_to_transfer,
                    downloaded=result["downloaded"], skipped=result["skipped"],
                    failed=result["failed"], bytes=transferred, total_bytes=total_bytes)
            if batch_ok:
                print(f"[sync-download] 第 {bi}/{n_batches} 批完成，累计 {result['downloaded']}/"
                      f"{total_to_transfer} 个（{transferred / 1024 / 1024:.1f}MB，"
                      f"{elapsed:.0f}s，{transferred / 1024 / elapsed:.0f}KB/s）")
            if batch_ok and bi < n_batches:
                # 批间稍作停顿，避免连着 15 次 exec_command 把 SSH 通道打满
                time.sleep(DL_BATCH_GAP)

    finally:
        try:
            sftp.close()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass

    if cancelled:
        result["errors"].append("已取消")
        print("[sync-download] 用户取消")
        _report(phase='cancelled', **{k: result[k] for k in ('downloaded', 'skipped', 'failed', 'total')})
        return result

    print(f"[sync-download] 完成: 下载 {result['downloaded']}, 跳过 {result['skipped']}, 失败 {result['failed']}")
    if result["errors"]:
        print("[sync-download] 失败详情:")
        for err in result["errors"][:20]:
            print(f"  - {err}")
    _report(phase='done', **{k: result[k] for k in ('downloaded', 'skipped', 'failed', 'total', 'errors')})
    return result


class _DownloadCancelled(Exception):
    """传输被用户取消。

    ⚠️ 目前只有 `sync_upload` 还用它（走raise 上抛）；`sync_download` 自v1.4.25
    改成**分批 + cancelled 标志**（一批失败/取消不再连坐后面的批次），
    所以 download 侧已不再 raise 这个异常。别再拿它去改 download 路径。
    """
    pass


def _sftp_makedirs(sftp, remote_dir: str, base_dir: str = ""):
    """递归创建远程目录，从 base_dir 开始向下创建"""
    if not base_dir:
        # 如果没有指定 base_dir，从根目录开始
        parts = remote_dir.split("/")
        current = ""
    else:
        # 从 base_dir 开始，只创建 base_dir 之后的部分
        if remote_dir.startswith(base_dir):
            remainder = remote_dir[len(base_dir):].lstrip("/")
            parts = remainder.split("/") if remainder else []
            current = base_dir
        else:
            parts = remote_dir.split("/")
            current = ""
    
    for part in parts:
        if not part:
            if not current:
                current = "/"
            continue
        if current == "/":
            current = "/" + part
        else:
            current = current + "/" + part
        try:
            sftp.stat(current)
        except (FileNotFoundError, IOError, OSError):
            try:
                sftp.mkdir(current)
            except (IOError, OSError) as e:
                # 再次检查是否已被创建
                try:
                    sftp.stat(current)
                except (FileNotFoundError, IOError, OSError):
                    raise IOError(f"无法创建目录 {current}: {e}")


def sync_upload(server: dict, project: dict, local_path: str, progress_cb=None, force: bool = False, selected_files: list = None, sync_all: bool = False, cancel_event=None) -> dict:
    """
    从本地目录增量上传到远程服务器。
    使用 tar-over-SSH 批量传输，比逐文件 SFTP 快 10-50 倍。
    force=True 时跳过比较，强制上传所有文件。
    selected_files 非空时只上传列表中的文件。
    sync_all=True 时不过滤目录/扩展名（默认过滤 .git/node_modules/csv/zip 等）。
    """
    remote_path = project["remote_path"]
    result = {"uploaded": 0, "skipped": 0, "failed": 0, "errors": [], "total": 0}

    def _report(**kw):
        if progress_cb:
            try: progress_cb(kw)
            except Exception: pass

    def _cancelled():
        return cancel_event is not None and cancel_event.is_set()

    if not os.path.isdir(local_path):
        err = f"本地目录不存在: {local_path}"
        result["errors"].append(err)
        _report(phase='error', error=err)
        return result

    _report(phase='connecting')

    try:
        client, sftp = _sftp_connect(server)
    except Exception as e:
        result["errors"].append(f"连接失败: {e}")
        _report(phase='error', error=f"连接失败: {e}")
        return result

    try:
        # 确保远程基础目录存在
        try:
            sftp.stat(remote_path)
        except (FileNotFoundError, IOError, OSError):
            print(f"[sync-upload] 创建远程基础目录: {remote_path}")
            _sftp_makedirs(sftp, remote_path)

        print(f"[sync-upload] 扫描本地 {local_path} ...")
        _report(phase='scanning')
        local_files = _local_stat_tree(local_path, sync_all=sync_all, cancel_event=cancel_event)
        remote_files = _sftp_stat_tree(sftp, remote_path, sync_all=sync_all, cancel_event=cancel_event)

        to_upload = []  # list of (relative_path, local_full_path)
        selected_set = set(selected_files) if selected_files else None
        for rel, (l_mtime, l_size) in local_files.items():
            if not sync_all and _should_ignore(rel):
                result["skipped"] += 1
                continue
            # 只上传选中的文件
            if selected_set and rel not in selected_set:
                result["skipped"] += 1
                continue
            result["total"] += 1
            # force 模式跳过比较，直接上传
            if not force:
                remote_full = f"{remote_path}/{rel}"
                if remote_full in remote_files:
                    r_mtime, r_size = remote_files[remote_full]
                    if abs(l_mtime - r_mtime) < 3 and l_size == r_size:
                        result["skipped"] += 1
                        continue
            to_upload.append((rel, os.path.join(local_path, rel)))

        total_to_transfer = len(to_upload)
        total_bytes = sum(os.path.getsize(lp) for _, lp in to_upload if os.path.isfile(lp))
        print(f"[sync-upload] 需上传 {total_to_transfer} 个文件 ({total_bytes / 1024 / 1024:.1f}MB)，跳过 {result['skipped']} 个")
        _report(phase='transferring', total=result["total"], to_transfer=total_to_transfer,
                skipped=result["skipped"], current=0, total_bytes=total_bytes)

        if _cancelled():
            result["errors"].append("已取消")
            _report(phase='cancelled', **{k: result[k] for k in ('uploaded', 'skipped', 'failed', 'total')})
            return result

        if total_to_transfer == 0:
            _report(phase='done', **{k: result[k] for k in ('uploaded', 'skipped', 'failed', 'total', 'errors')})
            return result

        # --- tar-over-SSH 流式上传 ---
        # 旧实现：先 io.BytesIO() 把整个目录打包进内存再一次性发出，大项目（数万文件/数十 GB）必爆内存。
        # 新实现：本地逐文件打包，经 SSH stdin 流式写入远程 tar 解包，内存只保留单文件。
        # remote_path 经 shlex.quote 处理，杜绝命令注入（旧实现仅套单引号）。
        cmd = f"tar xf - -C {shlex.quote(remote_path)}"
        print(f"[sync-upload] 执行: {cmd}（流式上传 {total_to_transfer} 个文件）")

        try:
            stdin, stdout, stderr = client.exec_command(cmd, bufsize=65536)

            # 后台线程持续排空 stderr，防止 tar 告警塞满通道窗口导致死锁（与下载对称）
            err_chunks = []
            def _drain_err():
                try:
                    while True:
                        b = stderr.read(65536)
                        if not b:
                            break
                        err_chunks.append(b)
                except Exception:
                    pass
            err_thread = threading.Thread(target=_drain_err, daemon=True)
            err_thread.start()

            cancelled = False
            tar = None
            transferred = 0
            last_report = 0.0
            try:
                tar = tarfile.open(fileobj=stdin, mode='w|')
                for rel, full_path in to_upload:
                    if _cancelled():
                        cancelled = True
                        break
                    if os.path.isfile(full_path):
                        fsize = os.path.getsize(full_path)
                        tar.add(full_path, arcname=rel)
                        transferred += fsize
                        result["uploaded"] += 1
                    else:
                        # 符号链接/特殊文件：跳过（to_upload 理论只含常规文件）
                        result["skipped"] += 1
                    now = time.time()
                    if now - last_report > 0.15 or result["uploaded"] == total_to_transfer:
                        last_report = now
                        _report(phase='file', current=result["uploaded"], to_transfer=total_to_transfer,
                                file=rel, bytes=transferred, total_bytes=total_bytes)
                if not cancelled:
                    tar.close()  # 写入结束块
            except (IOError, OSError, EOFError) as e:
                if cancelled or _cancelled():
                    result["errors"].append("已取消")
                    print("[sync-upload] 用户取消")
                    try: stdin.close()
                    except Exception: pass
                    _report(phase='cancelled', **{k: result[k] for k in ('uploaded', 'skipped', 'failed', 'total')})
                    return result
                result["failed"] = total_to_transfer - result["uploaded"]
                err_msg = f"网络传输中断: {type(e).__name__}: {e}"
                result["errors"].append(err_msg)
                print(f"[sync-upload] {err_msg}")
                return result

            if cancelled:
                try: stdin.close()
                except Exception: pass
                result["errors"].append("已取消")
                print("[sync-upload] 用户取消")
                _report(phase='cancelled', **{k: result[k] for k in ('uploaded', 'skipped', 'failed', 'total')})
                return result

            try:
                stdin.close()
            except Exception:
                pass
            err_thread.join(timeout=3)
            err_output = b"".join(err_chunks).decode("utf-8", errors="replace").strip()
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                result["failed"] = total_to_transfer - result["uploaded"]
                err_msg = f"远程解包失败 (exit {exit_status}): {err_output[:200]}"
                result["errors"].append(err_msg)
                print(f"[sync-upload] {err_msg}")
            else:
                print(f"[sync-upload] 批量上传成功: {total_to_transfer} 个文件")
        except (IOError, OSError, EOFError) as e:
            result["failed"] = total_to_transfer - result["uploaded"]
            err_msg = f"网络传输中断: {type(e).__name__}: {e}"
            result["errors"].append(err_msg)
            print(f"[sync-upload] {err_msg}")

    finally:
        sftp.close()
        client.close()

    print(f"[sync-upload] 完成: 上传 {result['uploaded']}, 跳过 {result['skipped']}, 失败 {result['failed']}")
    if result["errors"]:
        print("[sync-upload] 失败详情:")
        for err in result["errors"][:20]:
            print(f"  - {err}")
    _report(phase='done', **{k: result[k] for k in ('uploaded', 'skipped', 'failed', 'total', 'errors')})
    return result


if __name__ == "__main__":
    sync_all()
