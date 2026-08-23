"""
Dev Center - 定时同步调度器

轻量进程内调度（无第三方依赖，避免 PyInstaller 打包风险）：
后台线程每 30s 扫描 config.json 里的 schedules，到点自动跑同步（远程 MD -> 本地 latest.json），
并强制触发 LLM 分析整理，最后在 latest.json 写入「最新系统开发情况」声明（含免责说明）。

频率类型：
- minutes：每隔 N 分钟（最小 15，适合高频轻量场景）
- weekly ：每周指定的星期几（weekdays: 0=周一..6=周日）在 run_time 运行
- monthly ：每月指定的日期（month_days: 1..31）在 run_time 运行
"""

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from backend.common_paths import CONFIG_PATH, LATEST_JSON

# 固定免责声明：写死，确保一定随定时同步结果出现
DISCLAIMER = (
    "最新系统开发情况：以上内容为各项目远程文档（TODO / PROGRESS / ISSUES 等）"
    "记录的开发进度与分析，仅反映服务器上记录的开发状态，"
    "不代表你本地计算机的程序文件已是最新版本。"
)

MIN_INTERVAL = 15  # 最小间隔（分钟），防止过频给服务器加压

_sched_lock = threading.Lock()
_sched_thread = None
_sched_stop = threading.Event()


# ---------------- 配置 / 数据读写（自带原子写，避免依赖 app 模块造成循环导入） ----------------

def _load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_config(config):
    tmp = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    tmp.replace(CONFIG_PATH)


def _load_latest():
    try:
        with open(LATEST_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_latest(data):
    LATEST_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = LATEST_JSON.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(LATEST_JSON)


def _now_iso():
    return datetime.now().isoformat()


def _parse_time(s):
    """把 'HH:MM' 解析成 (时, 分)，出错回退 09:00。"""
    try:
        hh, mm = str(s or "09:00").split(":")
        return max(0, min(23, int(hh))), max(0, min(59, int(mm)))
    except Exception:
        return 9, 0


def compute_next_run(schedule: dict, from_dt: datetime) -> str:
    """根据频率计算 from_dt 之后的下一次运行时间（ISO 字符串）。"""
    freq = schedule.get("freq", "minutes")

    if freq == "weekly":
        wds = schedule.get("weekdays") or []
        hh, mm = _parse_time(schedule.get("run_time", "09:00"))
        cand = from_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
        for _ in range(8):  # 一周 7 天 + 余量
            if (cand.weekday() in wds) and (cand > from_dt):
                return cand.isoformat()
            cand += timedelta(days=1)
        return cand.isoformat()

    if freq == "monthly":
        mds = schedule.get("month_days") or []
        hh, mm = _parse_time(schedule.get("run_time", "09:00"))
        cand = from_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
        for _ in range(92):  # 三个月余量，覆盖跨小月/2 月等情况
            if (cand.day in mds) and (cand > from_dt):
                return cand.isoformat()
            cand += timedelta(days=1)
        return cand.isoformat()

    # minutes
    interval = max(MIN_INTERVAL, int(schedule.get("interval_minutes", MIN_INTERVAL)))
    return (from_dt + timedelta(minutes=interval)).isoformat()


# ---------------- 核心：执行一条调度 ----------------

def run_schedule(schedule: dict, config: dict = None) -> dict:
    """执行一条调度：同步（全量或指定项目）+ 强制 LLM 分析，写声明。返回状态字典。"""
    if config is None:
        config = _load_config()

    scope = schedule.get("scope", "all")
    result = {"schedule_id": schedule.get("id"), "started_at": _now_iso()}
    try:
        from backend.sync import sync_all, sync_single
        if scope == "all" or not schedule.get("project_ids"):
            sync_all()
        else:
            for pid in schedule.get("project_ids", []):
                sync_single(pid)
        result["sync"] = "ok"
    except Exception as e:  # noqa: BLE001
        result["sync"] = f"error: {e}"

    # 写「最新系统开发情况」声明到 latest.json
    latest = _load_latest()
    analyzed = [p for p in latest.get("projects", []) if p.get("llm_analyzed")]
    latest["dev_status"] = {
        "generated_at": _now_iso(),
        "schedule_id": schedule.get("id"),
        "schedule_name": schedule.get("name"),
        "note": DISCLAIMER,
        "llm_analyzed_projects": len(analyzed),
        "total_projects": len(latest.get("projects", [])),
    }
    _save_latest(latest)
    result["llm_analyzed"] = len(analyzed)
    return result


def run_sync_now(scope="all", project_ids=None):
    """立即同步一次（独立于定时规则）：全量或指定项目 + 强制 LLM 分析 + 写声明。"""
    syn = {"id": "now", "name": "立即同步", "scope": scope, "project_ids": project_ids or []}
    return run_schedule(syn)


def _apply_timings(sid, started_at, status):
    """把运行结果回写到 config 的对应 schedule（重载最新 config 避免覆盖他人改动），并重置下次运行时间。"""
    with _sched_lock:
        config = _load_config()
        schedules = config.setdefault("schedules", [])
        for s in schedules:
            if s.get("id") == sid:
                s["last_run"] = started_at
                s["last_status"] = status
                s["next_run"] = compute_next_run(s, datetime.now())
                break
        _save_config(config)


def run_schedule_by_id(sid: str):
    """手动立即运行一条调度（API 调用）。"""
    config = _load_config()
    schedule = next((s for s in config.get("schedules", []) if s.get("id") == sid), None)
    if not schedule:
        return {"error": f"Schedule {sid} not found"}
    started = _now_iso()
    res = run_schedule(schedule, config)
    status = "ok" if res.get("sync") == "ok" else "error"
    _apply_timings(sid, started, status)
    return res


# ---------------- 后台调度线程 ----------------

def _tick():
    while not _sched_stop.is_set():
        try:
            config = _load_config()
            schedules = config.get("schedules", [])
            for s in schedules:
                if not s.get("enabled", True):
                    continue
                now = datetime.now()
                nxt = s.get("next_run")
                if not nxt:
                    # 首次：按频率计算下次运行时间并持久化（不会立即触发）
                    with _sched_lock:
                        s["next_run"] = compute_next_run(s, now)
                        _save_config(config)
                    continue
                try:
                    is_due = datetime.fromisoformat(nxt) <= now
                except Exception:
                    is_due = True
                if is_due:
                    print(f"[scheduler] 触发调度: {s.get('name')} ({s.get('id')})")
                    started = _now_iso()
                    res = run_schedule(s, config)
                    status = "ok" if res.get("sync") == "ok" else "error"
                    _apply_timings(s.get("id"), started, status)
        except Exception as e:  # noqa: BLE001
            print(f"[scheduler] tick error: {e}")
        _sched_stop.wait(30)


def start_scheduler():
    """启动后台调度线程（幂等）。"""
    global _sched_thread
    with _sched_lock:
        if _sched_thread and _sched_thread.is_alive():
            return
        _sched_stop.clear()
        _sched_thread = threading.Thread(target=_tick, daemon=True)
        _sched_thread.start()
        print("[scheduler] started")
