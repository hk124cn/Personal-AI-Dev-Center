"""
Dev Center - FastAPI Backend
提供 API 接口供前端驾驶舱读取数据，并支持手动触发同步。
"""

import json
import os
import shutil
import subprocess
import sys
import time
import socket
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "backend" / "data"


def _get_pkg_version():
    """从 package.json 读取版本号，保证与发布版本一致"""
    try:
        with open(BASE_DIR / "package.json", "r", encoding="utf-8") as f:
            return json.load(f).get("version", "1.0.0")
    except Exception:
        return "1.0.0"


APP_VERSION = _get_pkg_version()

# 使用用户数据目录存储配置，确保打包后配置能持久化
# Windows: %APPDATA%/Personal AI Dev Center/
# macOS: ~/Library/Application Support/Personal AI Dev Center/
# Linux: ~/.config/Personal AI Dev Center/
def get_user_data_dir():
    if sys.platform == 'win32':
        return Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming')) / 'Personal AI Dev Center'
    elif sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'Personal AI Dev Center'
    else:
        return Path.home() / '.config' / 'Personal AI Dev Center'

USER_DATA_DIR = get_user_data_dir()
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = USER_DATA_DIR / "config.json"

# 如果用户配置不存在，从资源目录复制默认配置
RESOURCE_CONFIG = BASE_DIR / "config.json"
RESOURCE_CONFIG_EXAMPLE = BASE_DIR / "config.example.json"
if not CONFIG_PATH.exists():
    src = RESOURCE_CONFIG if RESOURCE_CONFIG.exists() else RESOURCE_CONFIG_EXAMPLE
    if src.exists():
        shutil.copy2(src, CONFIG_PATH)

LATEST_JSON = DATA_DIR / "latest.json"

app = FastAPI(title="Personal AI Dev Center API", version=APP_VERSION)

# CORS - 允许前端 HTML 文件直接调用
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def load_json(path: Path):
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ==================== Pydantic Models ====================

class ServerModel(BaseModel):
    id: str
    name: str
    provider: str = ""
    providerHue: int = 0
    cpu: str = ""
    ram: int = 0
    disk: str = ""
    ip: str = ""
    host: str = ""  # alias for ip in config
    region: str = ""
    os: str = ""
    tools: List[str] = []
    expiry: str = ""
    status: str = "online"
    projects: List[str] = []
    sshPort: Optional[int] = 22
    sshUser: Optional[str] = None
    sshKey: Optional[str] = None


class ProjectModel(BaseModel):
    id: str
    name: str
    desc: str = ""
    server: str = ""
    status: str = "dev"
    priority: str = "medium"
    progress: int = 0
    techStack: List[str] = []
    features: List[dict] = []
    todos: List[dict] = []
    lastUpdate: str = ""
    agent: str = "-"
    remote_path: str = ""
    local_path: str = ""
    localPath: str = ""
    url: str = ""


class DomainModel(BaseModel):
    id: str
    name: str  # 主域名，如 example.com
    registrar: str = ""  # 注册商
    expiry: str = ""  # 到期时间
    auto_renew: bool = False  # 自动续费
    status: str = "active"  # active, expired, pending
    subdomains: List[dict] = []  # [{name: "www", type: "A", value: "1.2.3.4", note: ""}]
    note: str = ""


class AssetModel(BaseModel):
    id: str
    category: str = "sim"  # sim, credit_card, membership
    name: str = ""  # 名称/运营商/平台
    provider: str = ""  # 运营商/银行/平台
    status: str = "active"  # active, suspended, expired, pending
    note: str = ""
    # --- 费用（通用）---
    total_fee: float = 0  # 总费用
    currency: str = "CNY"  # 货币单位 CNY/USD/JPY/GBP/EUR
    expiry: str = ""  # 到期时间（用于计算月费）
    # --- SIM 卡专属 ---
    phone_number: str = ""  # 手机号（前端只显示后4位）
    home_region: str = ""  # 归属地（日本/英国/中国等）
    data_allowance: str = ""  # 流量额度描述（如 3G高速/月）
    throttle_speed: str = ""  # 超量降速（如 200Kbps）
    sms_capable: bool = True  # 能否收短信
    voice_capable: bool = False  # 能否打电话
    roaming: bool = False  # 海外漫游
    roaming_data: str = ""  # 海外流量额度（如 1G高速/月）
    contract_months: int = 0  # 合约期限（月）
    # --- 信用卡专属 ---
    card_network: str = ""  # VISA/Mastercard/JCB/UnionPay/Amex
    card_form: str = "physical"  # physical/virtual
    last_four: str = ""  # 卡号后四位
    issuing_bank: str = ""  # 发卡银行
    annual_fee: float = 0  # 年费
    credit_limit: float = 0  # 信用额度
    billing_day: int = 0  # 账单日
    payment_due_day: int = 0  # 还款日
    # --- 会员订阅专属 ---
    plan_type: str = "paid"  # trial/paid
    trial_expiry: str = ""  # 试用到期日
    paid_expiry: str = ""  # 正式付费到期日
    auto_renew: bool = False  # 自动续费


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def sync_active_llm_config(config: dict) -> None:
    llm_configs = config.get("llm_configs")
    active_id = config.get("llm_active_id")
    if not isinstance(llm_configs, list):
        return

    active = next((c for c in llm_configs if c.get("id") == active_id), None)
    if active:
        config["llm"] = {k: v for k, v in active.items() if k not in {"id", "name"}}
        config["llm"]["enabled"] = True
    else:
        previous = config.get("llm", {})
        config["llm"] = {
            "enabled": False,
            "provider": previous.get("provider", "anthropic"),
            "api_key": previous.get("api_key", ""),
            "model": previous.get("model", "claude-3-5-sonnet-20241022"),
        }


# ==================== API Routes ====================

@app.get("/api/data")
def get_latest_data():
    """获取最新的同步数据"""
    data = load_json(LATEST_JSON)
    if data is None:
        return {
            "synced_at": None,
            "project_count": 0,
            "projects": [],
            "message": "尚未同步数据，请先运行同步或等待定时任务执行",
        }
    return data


@app.get("/api/config")
def get_config():
    """获取服务器和项目配置（隐藏敏感信息）"""
    config = load_json(CONFIG_PATH)
    if config is None:
        raise HTTPException(status_code=404, detail="Config not found")
    # 隐藏 IP 和密钥路径
    safe_servers = []
    for s in config.get("servers", []):
        safe_servers.append({
            "id": s["id"],
            "name": s["name"],
            "host": s["host"][:4] + ".xx.xx.xx",
            "user": s["user"],
        })
    result = {"servers": safe_servers, "projects": config.get("projects", [])}
    if "llm" in config:
        llm_safe = {k: v for k, v in config["llm"].items() if k != "api_key"}
        result["llm"] = llm_safe
    if "llm_configs" in config:
        result["llm_configs"] = [
            {k: v for k, v in llm_config.items() if k != "api_key"}
            for llm_config in config.get("llm_configs", [])
        ]
        result["llm_active_id"] = config.get("llm_active_id")
    return result


@app.post("/api/config")
def update_config(payload: dict):
    """更新配置（仅支持 LLM 配置）"""
    config = load_json(CONFIG_PATH)
    if config is None:
        raise HTTPException(status_code=404, detail="Config not found")

    if "llm_configs" in payload:
        existing_keys = {
            c.get("id"): c.get("api_key", "")
            for c in config.get("llm_configs", [])
            if c.get("id")
        }
        config["llm_configs"] = payload["llm_configs"]
        for llm_config in config["llm_configs"]:
            if "api_key" not in llm_config and llm_config.get("id") in existing_keys:
                llm_config["api_key"] = existing_keys[llm_config["id"]]
        active_id = payload.get("llm_active_id")
        if not active_id:
            enabled_config = next((c for c in config["llm_configs"] if c.get("enabled")), None)
            active_id = enabled_config.get("id") if enabled_config else None
        config["llm_active_id"] = active_id
        sync_active_llm_config(config)
        save_config(config)
        return {"success": True, "message": "LLM configs updated"}

    if "llm" in payload:
        if "api_key" not in payload["llm"] and "llm" in config:
            payload["llm"]["api_key"] = config["llm"].get("api_key", "")
        config["llm"] = payload["llm"]
        default_id = config.get("llm_active_id") or "cfg-default"
        existing = next((c for c in config.get("llm_configs", []) if c.get("id") == default_id), {})
        default_config = {"id": default_id, "name": existing.get("name", "默认配置"), **payload["llm"]}
        config["llm_configs"] = [
            default_config if c.get("id") == default_id else c
            for c in config.get("llm_configs", [])
        ]
        if not any(c.get("id") == default_id for c in config["llm_configs"]):
            config["llm_configs"].append(default_config)
        config["llm_active_id"] = default_id if payload["llm"].get("enabled", False) else None
        save_config(config)
        return {"success": True, "message": "LLM config updated"}

    raise HTTPException(status_code=400, detail="Only LLM config can be updated")


@app.post("/api/llm-test")
def test_llm_connection(payload: dict):
    """测试 LLM 配置是否可用"""
    try:
        from backend.llm_analyzer import test_connection
    except ImportError:
        sys.path.insert(0, str(BASE_DIR))
        from backend.llm_analyzer import test_connection

    llm_config = payload.get("llm", payload)
    return test_connection(llm_config)


@app.post("/api/sync")
def trigger_sync():
    """手动触发一次数据同步"""
    try:
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "backend" / "sync.py")],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(BASE_DIR),
        )
        output = result.stdout + result.stderr
        data = load_json(LATEST_JSON)
        return {
            "success": result.returncode == 0,
            "output": output,
            "data": data,
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Sync timed out (120s)")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sync/{project_id}")
def trigger_sync_single(project_id: str):
    """只同步指定项目"""
    try:
        from backend.sync import sync_single
    except ImportError:
        sys.path.insert(0, str(BASE_DIR))
        from backend.sync import sync_single

    try:
        result = sync_single(project_id)
        if "error" in result and "not found" in result["error"]:
            raise HTTPException(404, result["error"])
        return {"success": result.get("sync_error") is None, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _get_project_and_server(project_id: str):
    config = load_json(CONFIG_PATH)
    if not config:
        raise HTTPException(404, "Config not found")
    project = next((p for p in config["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    server = next((s for s in config["servers"] if s["id"] == project.get("server")), None)
    if not server:
        raise HTTPException(404, f"Server not found for project {project_id}")
    return project, server


@app.post("/api/sync-download/{project_id}")
def sync_download_project(project_id: str):
    """从远程服务器增量下载到本地目录（SSE 流式进度）"""
    from fastapi.responses import StreamingResponse
    try:
        from backend.sync import sync_download
    except ImportError:
        sys.path.insert(0, str(BASE_DIR))
        from backend.sync import sync_download

    project, server = _get_project_and_server(project_id)
    local_path = project.get("local_path", "")
    if not local_path:
        raise HTTPException(400, "未配置本地目录，请先在项目设置中填写 local_path")

    local_path = os.path.expanduser(local_path)
    import queue, threading
    q = queue.Queue()

    def progress_cb(data):
        q.put(data)

    def run():
        max_retries = 1
        for attempt in range(max_retries + 1):
            try:
                result = sync_download(server, project, local_path, progress_cb=progress_cb)
                # 检查是否有网络传输中断错误
                network_err = any("网络传输中断" in e or "连接失败" in e for e in result.get("errors", []))
                if network_err and attempt < max_retries:
                    q.put({"phase": "retrying", "attempt": attempt + 1, "error": result["errors"][-1]})
                    time.sleep(2)
                    continue
                q.put({"phase": "complete", "success": not result["errors"], "data": result})
                break
            except Exception as e:
                err_msg = str(e)
                if attempt < max_retries:
                    q.put({"phase": "retrying", "attempt": attempt + 1, "error": err_msg})
                    time.sleep(2)
                else:
                    q.put({"phase": "error", "error": err_msg})
        q.put(None)

    threading.Thread(target=run, daemon=True).start()

    def stream():
        while True:
            try:
                event = q.get(timeout=5)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/project-files/{project_id}")
def list_project_files(project_id: str):
    """列出项目本地目录下的文件（用于上传前预览）"""
    project, server = _get_project_and_server(project_id)
    local_path = project.get("local_path", "")
    if not local_path:
        raise HTTPException(400, "未配置本地目录")
    local_path = os.path.expanduser(local_path)
    if not os.path.isdir(local_path):
        raise HTTPException(404, f"本地目录不存在: {local_path}")
    files = []
    for root, dirs, filenames in os.walk(local_path):
        # 跳过忽略的目录（不屏蔽点文件）
        dirs[:] = [d for d in dirs if d not in {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.idea', '.vscode'}]
        for f in filenames:
            ext = os.path.splitext(f)[1].lower()
            if ext in {'.csv', '.log', '.pyc', '.pyo', '.class', '.o', '.so', '.zip', '.tar', '.gz', '.rar', '.7z', '.exe', '.dll', '.whl'}:
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, local_path).replace('\\', '/')
            size = os.path.getsize(full)
            mtime = os.path.getmtime(full)
            files.append({"path": rel, "size": size, "mtime": mtime})
    files.sort(key=lambda x: x["path"])
    return {"files": files, "total": len(files), "total_size": sum(f["size"] for f in files)}


@app.post("/api/sync-upload/{project_id}")
def sync_upload_project(project_id: str, force: bool = False, files: str = ""):
    """从本地目录增量上传到远程服务器（SSE 流式进度）。force=True 时强制上传所有文件。files=JSON 文件列表时只上传选中文件"""
    from fastapi.responses import StreamingResponse
    import json as _json
    try:
        from backend.sync import sync_upload
    except ImportError:
        sys.path.insert(0, str(BASE_DIR))
        from backend.sync import sync_upload

    project, server = _get_project_and_server(project_id)
    local_path = project.get("local_path", "")
    if not local_path:
        raise HTTPException(400, "未配置本地目录，请先在项目设置中填写 local_path")

    local_path = os.path.expanduser(local_path)
    import queue, threading
    q = queue.Queue()

    def progress_cb(data):
        q.put(data)

    # 解析选中的文件列表
    selected_files = []
    if files:
        try:
            selected_files = _json.loads(files)
        except Exception:
            pass

    def run():
        max_retries = 1
        for attempt in range(max_retries + 1):
            try:
                result = sync_upload(server, project, local_path, progress_cb=progress_cb, force=force, selected_files=selected_files)
                # 检查是否有网络传输中断错误
                network_err = any("网络传输中断" in e or "连接失败" in e for e in result.get("errors", []))
                if network_err and attempt < max_retries:
                    q.put({"phase": "retrying", "attempt": attempt + 1, "error": result["errors"][-1]})
                    time.sleep(2)
                    continue
                q.put({"phase": "complete", "success": not result["errors"], "data": result})
                break
            except Exception as e:
                err_msg = str(e)
                if attempt < max_retries:
                    q.put({"phase": "retrying", "attempt": attempt + 1, "error": err_msg})
                    time.sleep(2)
                else:
                    q.put({"phase": "error", "error": err_msg})
        q.put(None)

    threading.Thread(target=run, daemon=True).start()

    def stream():
        while True:
            try:
                event = q.get(timeout=5)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/analyze/{project_id}")
def analyze_local_project(project_id: str):
    """分析本地已同步的文件，SSE 流式返回进度"""
    from fastapi.responses import StreamingResponse

    def sse(data_dict):
        return f"data: {json.dumps(data_dict, ensure_ascii=False)}\n\n"

    def run():
        try:
            from backend.llm_analyzer import build_analysis_prompt, call_llm_api
        except ImportError:
            sys.path.insert(0, str(BASE_DIR))
            from backend.llm_analyzer import build_analysis_prompt, call_llm_api

        yield sse({"step": "config", "message": "正在加载配置...", "status": "progress"})
        config = load_json(CONFIG_PATH)
        if not config:
            yield sse({"step": "error", "message": "配置文件不存在", "status": "error"}); return
        project = next((p for p in config["projects"] if p["id"] == project_id), None)
        if not project:
            yield sse({"step": "error", "message": f"项目 {project_id} 不存在", "status": "error"}); return

        local_path = project.get("local_path", "")
        if not local_path:
            yield sse({"step": "error", "message": "未配置本地目录", "status": "error"}); return
        local_path = os.path.expanduser(local_path)
        if not os.path.isdir(local_path):
            yield sse({"step": "error", "message": f"本地目录不存在: {local_path}", "status": "error"}); return

        yield sse({"step": "reading", "message": f"正在扫描 {local_path} ...", "status": "progress"})
        md_contents = {}
        # 优先扫描项目文档，也可以看其他文件，但限制长度
        target_files = {"README.md", "readme.md", "TODO.md", "todo.md", "PROGRESS.md", "progress.md", "ISSUES.md", "issues.md", "CLAUDE.md", "claude.md"}
        max_file_size = 100 * 1024  # 单个文件最大 100KB
        max_files = 30  # 最多 30 个文件
        for root, dirs, files in os.walk(local_path):
            dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "node_modules", ".venv"}]
            for f in files:
                if f.lower().endswith(".md") and len(md_contents) < max_files:
                    # 排除数字开头的文件
                    if f[0].isdigit():
                        continue
                    # 排除文件名含"歌词"的文件
                    if "歌词" in f:
                        continue
                    # 排除含"xueqiu"的文件
                    if "xueqiu" in f.lower():
                        continue
                    # 排除风格/文章/模板/内容文件
                    lower_f = f.lower()
                    if any(k in lower_f for k in ["style_", "article_", "template_", "_raw", "_topics", "buffett", "雪球", "通知", "小学一年级", "侠客行", "侠客", "通知"]):
                        continue
                    # 排除纯汉字文件名（保留带英文/数字的）
                    base_name = f[:-3]
                    if all('一' <= ch <= '鿿' for ch in base_name):
                        continue
                    full = os.path.join(root, f)
                    try:
                        if os.path.getsize(full) > max_file_size:
                            continue
                        with open(full, "r", encoding="utf-8", errors="replace") as fh:
                            content = fh.read()
                            # 项目文档保留完整内容，其他文件取前 3000 字符
                            if f not in target_files:
                                content = content[:3000] + "\n...\n(文件过长已截断)"
                            md_contents[f] = content
                    except Exception:
                        pass

        if not md_contents:
            yield sse({"step": "error", "message": "本地目录中没有找到合适的 .md 文件", "status": "error"}); return
        yield sse({"step": "reading", "message": f"找到 {len(md_contents)} 个文件: {', '.join(md_contents.keys())}", "status": "progress"})

        # LLM 配置
        llm_configs = config.get("llm_configs")
        active_id = config.get("llm_active_id")
        if isinstance(llm_configs, list) and active_id:
            active = next((c for c in llm_configs if c.get("id") == active_id), None)
            if active:
                llm_config = {k: v for k, v in active.items() if k not in {"id", "name"}}
                llm_config["enabled"] = True
            else:
                llm_config = config.get("llm", {})
        else:
            llm_config = config.get("llm", {})

        provider = llm_config.get("provider", "")
        model = llm_config.get("model", "")
        if not llm_config.get("enabled"):
            yield sse({"step": "error", "message": "LLM 未启用", "status": "error"}); return
        if not llm_config.get("api_key"):
            yield sse({"step": "error", "message": "API Key 未配置", "status": "error"}); return

        yield sse({"step": "llm", "message": f"正在调用 LLM: {provider} / {model} ...", "status": "progress"})
        try:
            prompt = build_analysis_prompt(project.get("name", project_id), md_contents)
            yield sse({"step": "llm", "message": f"提示词 {len(prompt)} 字符，等待响应...", "status": "progress"})
            llm_result = call_llm_api(prompt, llm_config)
        except Exception as e:
            yield sse({"step": "error", "message": f"LLM 异常: {type(e).__name__}: {e}", "status": "error"}); return

        if not llm_result:
            yield sse({"step": "error", "message": "LLM 返回为空或解析失败", "status": "error"}); return

        # 写入结果
        try:
            if LATEST_JSON.exists():
                with open(LATEST_JSON, "r", encoding="utf-8") as f:
                    output = json.load(f)
            else:
                output = {"projects": []}

            entry = {
                "id": project["id"], "name": project.get("name", ""),
                "llm_analyzed": True, "llm_status": "analyzed",
                "llm_features": llm_result.get("features", []),
                "llm_architecture": llm_result.get("architecture", {}),
                "llm_routes": llm_result.get("routes", []),
                "llm_summary": llm_result.get("summary", ""),
                "synced_at": datetime.now().isoformat(),
            }
            if llm_result.get("issues"):
                entry["issues"] = llm_result["issues"]
            if llm_result.get("todos"):
                entry["todos"] = llm_result["todos"]

            projects = output.get("projects", [])
            for i, p in enumerate(projects):
                if p.get("id") == project_id:
                    projects[i].update(entry); break
            else:
                projects.append(entry)

            output["projects"] = projects
            output["synced_at"] = datetime.now().isoformat()
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(LATEST_JSON, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=2)
        except Exception as e:
            yield sse({"step": "error", "message": f"写入失败: {e}", "status": "error"}); return

        yield sse({"step": "done", "message": "分析完成！", "status": "success", "data": entry})

    return StreamingResponse(run(), media_type="text/event-stream")


@app.post("/api/pick-directory")
def pick_directory():
    """打开系统原生目录选择对话框"""
    import tkinter as tk
    from tkinter import filedialog
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title="选择项目本地目录")
        root.destroy()
        if path:
            return {"success": True, "path": path}
        return {"success": False, "path": ""}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/speed-test")
def speed_test():
    """测试所有服务器的连接延迟（TCP 端口探测，绕过 ICMP 被防火墙拦截的情况）"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    config = load_json(CONFIG_PATH) or {"servers": []}
    results = {}

    def probe(server):
        sid = server.get("id")
        host = server.get("host") or server.get("ip")
        if not host or host in ("localhost", "待购"):
            return sid, {"status": "skip", "reason": "无有效地址"}
        try:
            port = int(server.get("port") or server.get("sshPort") or 22)
        except (TypeError, ValueError):
            port = 22
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            start = time.perf_counter()
            rc = sock.connect_ex((host, port))
            elapsed = (time.perf_counter() - start) * 1000
            sock.close()
            if rc == 0:
                return sid, {"status": "ok", "latency": round(elapsed)}
            return sid, {"status": "error", "reason": f"端口 {port} 不可达"}
        except socket.timeout:
            return sid, {"status": "timeout", "reason": f"{port} 端口连接超时"}
        except Exception as e:
            return sid, {"status": "error", "reason": str(e)}

    servers = config.get("servers", [])
    if not servers:
        return results
    with ThreadPoolExecutor(max_workers=min(8, len(servers))) as ex:
        futures = [ex.submit(probe, s) for s in servers]
        for f in as_completed(futures):
            try:
                sid, res = f.result()
                results[sid] = res
            except Exception:
                pass
    return results


@app.get("/api/health")
def health():
    """健康检查"""
    return {
        "status": "ok",
        "version": APP_VERSION,
        "time": datetime.now().isoformat(),
        "data_exists": LATEST_JSON.exists(),
    }


# ==================== Config CRUD ====================

@app.get("/api/config/full")
def get_full_config():
    """获取完整配置（含敏感信息，仅本地使用）"""
    config = load_json(CONFIG_PATH)
    if config is None:
        return {"servers": [], "projects": []}
    # Map config servers to frontend format
    servers = []
    for s in config.get("servers", []):
        servers.append({
            "id": s["id"], "name": s["name"], "provider": s.get("provider", ""),
            "providerHue": s.get("providerHue", 0),
            "cpu": s.get("cpu", ""), "ram": s.get("ram", 0),
            "disk": s.get("disk", ""), "ip": s.get("host", ""),
            "host": s.get("host", ""), "region": s.get("region", ""),
            "os": s.get("os", ""), "tools": s.get("tools", []),
            "expiry": s.get("expiry", ""), "status": s.get("status", "online"),
            "projects": s.get("projects", []),
            "sshPort": s.get("port", 22), "sshUser": s.get("user"),
            "sshKey": s.get("key_path"),
        })
    projects = []
    for p in config.get("projects", []):
        projects.append({
            "id": p["id"], "name": p["name"], "desc": p.get("desc", ""),
            "server": p.get("server", ""), "status": p.get("status", "dev"),
            "priority": p.get("priority", "medium"), "progress": p.get("progress", 0),
            "techStack": p.get("tech_stack", []), "features": p.get("features", []),
            "todos": p.get("todos", []), "lastUpdate": p.get("lastUpdate", ""),
            "agent": p.get("agent", "-"), "remote_path": p.get("remote_path", ""),
            "localPath": p.get("local_path", ""),
            "url": p.get("url", ""),
        })
    result = {"servers": servers, "projects": projects}
    if "llm" in config:
        result["llm"] = config["llm"]
    if "llm_configs" in config:
        result["llm_configs"] = config["llm_configs"]
        result["llm_active_id"] = config.get("llm_active_id")
    return result


@app.post("/api/servers")
def add_server(server: ServerModel):
    config = load_json(CONFIG_PATH) or {"servers": [], "projects": []}
    new_s = {
        "id": server.id, "name": server.name, "provider": server.provider,
        "providerHue": server.providerHue, "cpu": server.cpu, "ram": server.ram,
        "disk": server.disk, "host": server.host or server.ip,
        "region": server.region, "os": server.os, "tools": server.tools,
        "expiry": server.expiry, "status": server.status,
        "projects": server.projects, "port": server.sshPort or 22,
        "user": server.sshUser, "key_path": server.sshKey,
        "projects_root": "/root/projects"  # default
    }
    config["servers"].append(new_s)
    save_config(config)
    return {"success": True, "id": server.id}


@app.put("/api/servers/{server_id}")
def update_server(server_id: str, server: ServerModel):
    config = load_json(CONFIG_PATH)
    if not config:
        raise HTTPException(404, "Config not found")
    for i, s in enumerate(config["servers"]):
        if s["id"] == server_id:
            config["servers"][i] = {
                "id": server.id, "name": server.name, "provider": server.provider,
                "providerHue": server.providerHue, "cpu": server.cpu, "ram": server.ram,
                "disk": server.disk, "host": server.host or server.ip,
                "region": server.region, "os": server.os, "tools": server.tools,
                "expiry": server.expiry, "status": server.status,
                "projects": server.projects, "port": server.sshPort or 22,
                "user": server.sshUser, "key_path": server.sshKey,
                "projects_root": s.get("projects_root", "/root/projects")
            }
            save_config(config)
            return {"success": True}
    raise HTTPException(404, "Server not found")


@app.delete("/api/servers/{server_id}")
def delete_server(server_id: str):
    config = load_json(CONFIG_PATH)
    if not config:
        raise HTTPException(404, "Config not found")
    config["servers"] = [s for s in config["servers"] if s["id"] != server_id]
    save_config(config)
    return {"success": True}


@app.post("/api/projects")
def add_project(project: ProjectModel):
    config = load_json(CONFIG_PATH) or {"servers": [], "projects": []}
    new_p = {
        "id": project.id, "name": project.name, "desc": project.desc,
        "server": project.server, "status": project.status,
        "priority": project.priority, "progress": project.progress,
        "tech_stack": project.techStack, "features": project.features,
        "todos": project.todos, "lastUpdate": project.lastUpdate,
        "agent": project.agent, "remote_path": project.remote_path or project.id,
        "local_path": project.local_path or project.localPath, "url": project.url,
    }
    config["projects"].append(new_p)
    save_config(config)
    return {"success": True, "id": project.id}


@app.put("/api/projects/{project_id}")
def update_project(project_id: str, project: ProjectModel):
    config = load_json(CONFIG_PATH)
    if not config:
        raise HTTPException(404, "Config not found")
    for i, p in enumerate(config["projects"]):
        if p["id"] == project_id:
            existing = config["projects"][i]
            # Preserve existing fields when frontend sends empty/default values
            new_local = project.local_path or project.localPath or existing.get("local_path", "")
            new_tech = project.techStack if project.techStack else existing.get("tech_stack", [])
            config["projects"][i] = {
                "id": project.id, "name": project.name, "desc": project.desc,
                "server": project.server, "status": project.status,
                "priority": project.priority, "progress": project.progress,
                "tech_stack": new_tech, "features": project.features,
                "todos": project.todos, "lastUpdate": project.lastUpdate,
                "agent": project.agent,
                "remote_path": project.remote_path or existing.get("remote_path", project.id),
                "local_path": new_local,
                "url": project.url or existing.get("url", ""),
            }
            save_config(config)
            return {"success": True}
    raise HTTPException(404, "Project not found")


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str):
    config = load_json(CONFIG_PATH)
    if not config:
        raise HTTPException(404, "Config not found")
    config["projects"] = [p for p in config["projects"] if p["id"] != project_id]
    save_config(config)
    return {"success": True}


# ==================== Domains CRUD ====================

@app.get("/api/domains")
def list_domains():
    config = load_json(CONFIG_PATH) or {"domains": []}
    return config.get("domains", [])


@app.post("/api/domains")
def create_domain(domain: DomainModel):
    config = load_json(CONFIG_PATH) or {"domains": []}
    if "domains" not in config:
        config["domains"] = []
    config["domains"].append(domain.dict())
    save_config(config)
    return {"success": True, "id": domain.id}


@app.put("/api/domains/{domain_id}")
def update_domain(domain_id: str, domain: DomainModel):
    config = load_json(CONFIG_PATH)
    if not config:
        raise HTTPException(404, "Config not found")
    domains = config.get("domains", [])
    for i, d in enumerate(domains):
        if d["id"] == domain_id:
            domains[i] = domain.dict()
            config["domains"] = domains
            save_config(config)
            return {"success": True}
    raise HTTPException(404, "Domain not found")


@app.delete("/api/domains/{domain_id}")
def delete_domain(domain_id: str):
    config = load_json(CONFIG_PATH)
    if not config:
        raise HTTPException(404, "Config not found")
    config["domains"] = [d for d in config.get("domains", []) if d["id"] != domain_id]
    save_config(config)
    return {"success": True}


# ==================== Assets CRUD ====================

@app.get("/api/assets")
def list_assets():
    config = load_json(CONFIG_PATH) or {"assets": []}
    return config.get("assets", [])


@app.post("/api/assets")
def create_asset(asset: AssetModel):
    config = load_json(CONFIG_PATH) or {"assets": []}
    if "assets" not in config:
        config["assets"] = []
    config["assets"].append(asset.dict())
    save_config(config)
    return {"success": True, "id": asset.id}


@app.put("/api/assets/{asset_id}")
def update_asset(asset_id: str, asset: AssetModel):
    config = load_json(CONFIG_PATH)
    if not config:
        raise HTTPException(404, "Config not found")
    assets = config.get("assets", [])
    for i, a in enumerate(assets):
        if a["id"] == asset_id:
            assets[i] = asset.dict()
            config["assets"] = assets
            save_config(config)
            return {"success": True}
    raise HTTPException(404, "Asset not found")


@app.delete("/api/assets/{asset_id}")
def delete_asset(asset_id: str):
    config = load_json(CONFIG_PATH)
    if not config:
        raise HTTPException(404, "Config not found")
    config["assets"] = [a for a in config.get("assets", []) if a["id"] != asset_id]
    save_config(config)
    return {"success": True}


# ==================== Exchange Rate ====================

@app.get("/api/exchange-rate")
def get_exchange_rate():
    """获取最新汇率（以 CNY 为基准），用于外币换算人民币显示"""
    import urllib.request
    try:
        # 使用免费汇率 API
        url = "https://open.er-api.com/v6/latest/CNY"
        req = urllib.request.Request(url, headers={"User-Agent": "DevCenter/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        rates = data.get("rates", {})
        # 返回 1 外币 = 多少 CNY
        result = {}
        for cur in ["USD", "JPY", "GBP", "EUR", "KRW", "HKD", "TWD"]:
            if cur in rates and rates[cur] > 0:
                result[cur] = round(1.0 / rates[cur], 4)
        result["CNY"] = 1.0
        return {"success": True, "rates": result, "time": datetime.now().isoformat()}
    except Exception as e:
        # 返回备用汇率
        return {"success": False, "error": str(e), "rates": {
            "USD": 7.25, "JPY": 0.048, "GBP": 9.15, "EUR": 7.85,
            "KRW": 0.0053, "HKD": 0.93, "TWD": 0.23, "CNY": 1.0
        }}


# ==================== SSH Launch ====================

class SSHLaunchRequest(BaseModel):
    host: str
    port: int = 22
    user: str = "root"
    key_path: Optional[str] = None


@app.post("/api/ssh-launch")
def launch_ssh(req: SSHLaunchRequest):
    """打开本地终端并自动 SSH 到远程服务器"""
    import platform
    import os
    import glob as globmod

    # 找到 ssh.exe 的完整路径
    ssh_exe = "ssh"
    system = platform.system()
    if system == "Windows":
        for candidate in [
            r"C:\Windows\System32\OpenSSH\ssh.exe",
            r"C:\Program Files\OpenSSH\ssh.exe",
        ]:
            if os.path.isfile(candidate):
                ssh_exe = candidate
                break
        if ssh_exe == "ssh":
            try:
                r = subprocess.run(["where", "ssh"], capture_output=True, text=True)
                if r.returncode == 0 and r.stdout.strip():
                    ssh_exe = r.stdout.strip().splitlines()[0]
            except Exception:
                pass

    ssh_args = [ssh_exe, f"{req.user}@{req.host}", "-p", str(req.port)]
    if req.key_path:
        expanded = os.path.expanduser(req.key_path)
        ssh_args.extend(["-i", expanded])

    ssh_display = f"ssh {req.user}@{req.host} -p {req.port}"
    if req.key_path:
        ssh_display += f" -i {req.key_path}"

    try:
        if system == "Windows":
            # 优先用 Windows Terminal
            wt_path = None
            for candidate in [
                r"C:\Program Files\WindowsApps\Microsoft.WindowsTerminal_*\wt.exe",
                r"C:\Users\{}\AppData\Local\Microsoft\WindowsApps\wt.exe".format(os.environ.get("USERNAME", "")),
            ]:
                matches = globmod.glob(candidate)
                if matches:
                    wt_path = matches[0]
                    break
            if not wt_path:
                try:
                    subprocess.run(["where", "wt"], capture_output=True, check=True)
                    wt_path = "wt"
                except Exception:
                    pass

            if wt_path:
                subprocess.Popen([wt_path, "new-tab", "--title", f"{req.user}@{req.host}"] + ssh_args)
            else:
                # 回退到 cmd
                subprocess.Popen(["cmd", "/c", "start", "cmd", "/k"] + ssh_args)
        elif system == "Darwin":
            subprocess.Popen(["open", "-a", "Terminal"] + ssh_args)
        else:
            # Linux
            for term in ["gnome-terminal", "konsole", "xterm"]:
                try:
                    subprocess.Popen([term, "--"] + ssh_args)
                    break
                except FileNotFoundError:
                    continue

        return {"success": True, "command": ssh_display}
    except Exception as e:
        return {"success": False, "error": str(e), "command": ssh_display}


# ==================== Static Files ====================

# 提供前端 HTML
@app.get("/")
def serve_frontend():
    index = BASE_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    raise HTTPException(status_code=404, detail="Frontend not found")


# ==================== Entry Point ====================

if __name__ == "__main__":
    import uvicorn

    print("=" * 50)
    print("  Personal AI Dev Center API")
    print("  http://localhost:8765")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8765)
