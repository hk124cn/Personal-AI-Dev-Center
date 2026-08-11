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
from datetime import datetime
from pathlib import Path

# Windows 控制台 GBK 编码无法打印 emoji 等 Unicode 字符，强制使用 UTF-8
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import paramiko

# Import LLM analyzer
try:
    from llm_analyzer import analyze_project
except ImportError:
    analyze_project = None

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
DATA_DIR = BASE_DIR / "backend" / "data"
OUTPUT_PATH = DATA_DIR / "latest.json"

# MD files to look for in each project directory
MD_FILES = ["TODO.md", "PROGRESS.md", "ISSUES.md"]


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


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


def ssh_read_files(server: dict, project: dict) -> dict:
    """SSH 到服务器读取项目 MD 文件（自动发现所有 .md 文件）"""
    result = {
        "todos": None,
        "progress": None,
        "issues": None,
        "md_files": [],
        "summaries": {},
        "raw_files": {},
        "error": None,
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
            f"find '{project_dir}' -maxdepth 1 -name '*.md' -type f"
        )
        find_output = stdout.read().decode("utf-8", errors="replace").strip()
        discovered_paths = [p.strip() for p in find_output.splitlines() if p.strip()]

        # Build list of (filename, full_path) pairs
        md_entries = []
        for fpath in discovered_paths:
            fname = fpath.rsplit("/", 1)[-1]  # basename
            md_entries.append((fname, fpath))

        # Ensure we also attempt the 3 known files even if find returns nothing
        known_files = {"TODO.md", "PROGRESS.md", "ISSUES.md"}
        discovered_names = {fname for fname, _ in md_entries}
        for kf in known_files:
            if kf not in discovered_names:
                md_entries.append((kf, f"{project_dir}/{kf}"))

        result["md_files"] = sorted(set(fname for fname, _ in md_entries))

        # --- Step 2: Read and parse each MD file ---
        for md_file, remote_path in md_entries:
            try:
                stdin, stdout, stderr = client.exec_command(f"cat '{remote_path}'")
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
                        if generic["items"] and result["issues"] is None:
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
            f"stat -c '%Y' '{project_dir}' 2>/dev/null || echo '0'"
        )
        mtime_str = stdout.read().decode().strip()
        try:
            mtime = int(mtime_str)
            result["remote_mtime"] = datetime.fromtimestamp(mtime).isoformat()
        except (ValueError, OSError):
            result["remote_mtime"] = None

        client.close()

    except Exception as e:
        result["error"] = str(e)

    return result


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


def apply_llm_analysis(project_name: str, remote_data: dict, config: dict) -> dict:
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

    # 调用 LLM 分析
    llm_result = analyze_project(project_name, md_contents, llm_config)
    
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

        if remote_data["llm_tech_stack"]:
            print(f"[LLM] 识别到技术栈: {', '.join(remote_data['llm_tech_stack'])}")

        print(f"[LLM] [OK] Analysis applied for {project_name}")
    else:
        remote_data["llm_analyzed"] = False
        remote_data["llm_status"] = "api_failed"
    
    return remote_data


def sync_single(project_id: str) -> dict:
    """只同步指定项目"""
    config = load_config()
    servers = {s["id"]: s for s in config["servers"]}
    project = next((p for p in config["projects"] if p["id"] == project_id), None)
    if not project:
        return {"error": f"Project {project_id} not found"}

    server_id = project["server"]
    server = servers.get(server_id)
    if not server:
        return {"error": f"Server {server_id} not found"}

    print(f"[sync] {project['name']} @ {server['name']} ({server['host']})...")
    start = time.time()
    remote_data = ssh_read_files(server, project)
    
    # 应用 LLM 分析（如果启用）
    remote_data = apply_llm_analysis(project['name'], remote_data, config)
    
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
        # LLM 分析结果
        "llm_features": remote_data.get("llm_features", []),
        "llm_architecture": remote_data.get("llm_architecture", {}),
        "llm_routes": remote_data.get("llm_routes", []),
        "llm_tech_stack": llm_tech_stack,
        "llm_summary": remote_data.get("llm_summary", ""),
        "llm_analyzed": remote_data.get("llm_analyzed", False),
        "llm_status": remote_data.get("llm_status", "disabled"),
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
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    status = "OK" if not remote_data["error"] else f"ERROR: {remote_data['error']}"
    llm_status = " +LLM" if remote_data.get("llm_analyzed") else ""
    print(f"  -> {status}{llm_status} ({elapsed}s)")
    return project_entry


def sync_all():
    """同步所有服务器上的所有项目"""
    config = load_config()
    servers = {s["id"]: s for s in config["servers"]}
    projects_data = []

    for project in config["projects"]:
        server_id = project["server"]
        if server_id not in servers:
            continue

        server = servers[server_id]
        print(f"[sync] {project['name']} @ {server['name']} ({server['host']})...")

        start = time.time()
        remote_data = ssh_read_files(server, project)
        # 应用 LLM 分析（如果启用）
        remote_data = apply_llm_analysis(project['name'], remote_data, config)
        
        elapsed = round(time.time() - start, 2)

        # LLM 检测到技术栈时，自动更新 config
        llm_tech_stack = remote_data.get("llm_tech_stack", [])
        if llm_tech_stack:
            for i, p in enumerate(config["projects"]):
                if p["id"] == project["id"]:
                    config["projects"][i]["tech_stack"] = llm_tech_stack
                    print(f"[sync] 已更新 {project['name']} 的技术栈: {', '.join(llm_tech_stack)}")
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
            # LLM 分析结果
            "llm_features": remote_data.get("llm_features", []),
            "llm_architecture": remote_data.get("llm_architecture", {}),
            "llm_routes": remote_data.get("llm_routes", []),
            "llm_tech_stack": llm_tech_stack,
            "llm_summary": remote_data.get("llm_summary", ""),
            "llm_analyzed": remote_data.get("llm_analyzed", False),
            "llm_status": remote_data.get("llm_status", "disabled"),
        }
        projects_data.append(project_entry)

        status = "OK" if not remote_data["error"] else f"ERROR: {remote_data['error']}"
        llm_status = " +LLM" if remote_data.get("llm_analyzed") else ""
        print(f"  -> {status}{llm_status} ({elapsed}s)")

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
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    llm_count = sum(1 for p in projects_data if p.get("llm_analyzed"))
    print(f"\n[sync] Done. {len(projects_data)} projects synced{f', {llm_count} with LLM analysis' if llm_count else ''} -> {OUTPUT_PATH}")
    return output


# ==================== 双向文件同步引擎 ====================

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


def _sftp_stat_tree(sftp, remote_dir: str, remote_root: str = "", sync_all: bool = False) -> dict:
    """递归获取远程目录下所有文件的 (mtime, size)，返回 {完整路径: (mtime, size)}。sync_all=True 时不做任何过滤"""
    if not remote_root:
        remote_root = remote_dir
    result = {}
    try:
        entries = sftp.listdir_attr(remote_dir)
    except Exception:
        return result
    for entry in entries:
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


def _remote_stat_tree_fast(client, remote_path: str, sync_all: bool = False):
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
        data = stdout.read()
        status = stdout.channel.recv_exit_status()
        if status != 0 or not data:
            return None
        out = {}
        base = remote_path.rstrip("/")
        for rec in data.split(b"\0"):
            if not rec:
                continue
            parts = rec.split(b"\t", 2)
            if len(parts) != 3:
                continue
            try:
                mtime = float(parts[0])
                size = int(parts[1])
            except ValueError:
                continue
            rel = parts[2].decode("utf-8", "surrogateescape")
            if rel.startswith("./"):
                rel = rel[2:]
            if not rel or (not sync_all and _should_ignore(rel)):
                continue
            out[f"{base}/{rel}"] = (mtime, size)
        return out
    except Exception as e:
        print(f"[sync-download] 快速扫描失败，回退 SFTP 递归: {e}")
        return None


def _local_stat_tree(local_dir: str, sync_all: bool = False) -> dict:
    """获取本地目录下所有文件的 (mtime, size)，返回 {相对路径: (mtime, size)}。sync_all=True 时不过滤"""
    result = {}
    local_dir = os.path.normpath(local_dir)
    for root, dirs, files in os.walk(local_dir):
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
        remote_files = None if _cancelled() else _remote_stat_tree_fast(client, remote_path, sync_all=sync_all)
        if remote_files is None and not _cancelled():
            remote_files = _sftp_stat_tree(sftp, remote_path, sync_all=sync_all)
        if remote_files is None:
            remote_files = {}
        print(f"[sync-download] 远程文件数: {len(remote_files)}（sync_all={sync_all}）")
        local_files = _local_stat_tree(local_path, sync_all=sync_all) if os.path.isdir(local_path) else {}
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

        _report(phase='transferring', total=result["total"], to_transfer=total_to_transfer,
                skipped=result["skipped"], current=0, total_bytes=total_bytes)

        if total_to_transfer == 0:
            _report(phase='done', **{k: result[k] for k in ('downloaded', 'skipped', 'failed', 'total', 'errors')})
            return result

        # --- tar-over-SSH：文件清单经 stdin 传入（--null -T -），不受命令行长度限制 ---
        # 相对路径 + -C remote_path，解包时 member.name 即为相对路径；服务器端零写入。
        cmd = f"tar --null -cf - -C {shlex.quote(remote_path)} -T -"
        print(f"[sync-download] 执行: {cmd}（{total_to_transfer} 个文件经 stdin 传入）")

        try:
            stdin, stdout, stderr = client.exec_command(cmd, bufsize=262144)

            # 后台线程持续排空 stderr，防止 tar 告警塞满通道窗口导致死锁
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

            # 写入 null 分隔的相对路径清单，然后关闭写端让 tar 开始打包
            payload = b"".join(rel.encode("utf-8", "surrogateescape") + b"\0" for _, rel in to_download)
            stdin.write(payload)
            stdin.flush()
            stdin.channel.shutdown_write()

            os.makedirs(local_path, exist_ok=True)
            transferred = 0
            current = 0
            last_report = 0.0

            # 流式解包：边下边写盘，不占内存，逐文件报进度
            with tarfile.open(fileobj=stdout, mode='r|') as tar:
                for member in tar:
                    if _cancelled():
                        raise _DownloadCancelled()
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
                                    raise _DownloadCancelled()
                                out.write(buf)
                                transferred += len(buf)
                    os.utime(target, (member.mtime, member.mtime))
                    result["downloaded"] += 1
                    current += 1
                    now = time.time()
                    if now - last_report > 0.15 or current == total_to_transfer:
                        last_report = now
                        _report(phase='file', current=current, to_transfer=total_to_transfer,
                                file=rel, bytes=transferred, total_bytes=total_bytes)

            err_thread.join(timeout=3)
            err_output = b"".join(err_chunks).decode("utf-8", errors="replace").strip()
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0 and err_output:
                print(f"[sync-download] tar stderr: {err_output[:500]}")
                result["errors"].append(f"tar 警告: {err_output[:200]}")
        except _DownloadCancelled:
            result["errors"].append("已取消")
            print("[sync-download] 用户取消")
            try:
                client.close()
            except Exception:
                pass
            _report(phase='cancelled', **{k: result[k] for k in ('downloaded', 'skipped', 'failed', 'total')})
            return result
        except (IOError, OSError, EOFError) as e:
            result["failed"] = total_to_transfer - result["downloaded"]
            err_msg = f"网络传输中断: {type(e).__name__}: {e}"
            result["errors"].append(err_msg)
            print(f"[sync-download] {err_msg}")
        except tarfile.TarError as e:
            result["failed"] = total_to_transfer - result["downloaded"]
            result["errors"].append(f"解包失败: {e}")
            print(f"[sync-download] 解包错误: {e}")

    finally:
        try:
            sftp.close()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass

    print(f"[sync-download] 完成: 下载 {result['downloaded']}, 跳过 {result['skipped']}, 失败 {result['failed']}")
    if result["errors"]:
        print("[sync-download] 失败详情:")
        for err in result["errors"][:20]:
            print(f"  - {err}")
    _report(phase='done', **{k: result[k] for k in ('downloaded', 'skipped', 'failed', 'total', 'errors')})
    return result


class _DownloadCancelled(Exception):
    """下载被用户取消"""
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
        local_files = _local_stat_tree(local_path, sync_all=sync_all)
        remote_files = _sftp_stat_tree(sftp, remote_path, sync_all=sync_all)

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
