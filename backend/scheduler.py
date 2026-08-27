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
    """执行一条调度：同步（全量或指定项目）+ 强制 LLM 分析，写声明与「最近同步」状态。

    返回结构化结果：sync(ok/error)、sync_error、last_sync（含成功/失败数、耗时、各项目错误）。
    即使 SSH 部分失败，latest.json 仍会被 sync 引擎重写（带 sync_error），这里据实统计，
    不让上层把失败也当成成功。
    """
    if config is None:
        config = _load_config()

    scope = schedule.get("scope", "all")
    started = datetime.now()
    sync_ok = True
    sync_err = None
    try:
        from backend.sync import sync_all, sync_single, sync_progress
        if scope == "all" or not schedule.get("project_ids"):
            # 全量：进度由 sync_all 内部掌管
            sync_all()
        else:
            # 指定项目：由调度器掌管一次进度生命周期，避免逐个 sync_single 各自重置
            servers = {s["id"]: s for s in config.get("servers", [])}
            sel = []
            for pid in schedule.get("project_ids", []):
                p = next((x for x in config.get("projects", []) if x.get("id") == pid), None)
                if not p:
                    continue
                pv = dict(p)
                srv = servers.get(p.get("server"))
                pv["server_name"] = srv.get("name", "") if srv else ""
                sel.append(pv)
            if sel:
                sync_progress.start("selected", sel)
                try:
                    for pv in sel:
                        sync_single(pv["id"])
                finally:
                    sync_progress.done()
            # 若没有有效项目，则不产生任何同步（不更新进度）
    except Exception as e:  # noqa: BLE001
        sync_ok = False
        sync_err = str(e)
    finished = datetime.now()
    duration = round((finished - started).total_seconds(), 1)

    # 据实统计结果（sync 引擎把每个项目的 sync_error 写进了 latest.json）
    latest = _load_latest()
    projects = latest.get("projects", []) or []
    success = sum(1 for p in projects if not p.get("sync_error"))
    failed = sum(1 for p in projects if p.get("sync_error"))
    analyzed = sum(1 for p in projects if p.get("llm_analyzed"))
    errors = [
        {"name": p.get("name"), "error": p.get("sync_error")}
        for p in projects
        if p.get("sync_error")
    ][:10]

    last_sync = {
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_sec": duration,
        "source": schedule.get("name") or "立即同步",
        "scope": scope,
        "total": len(projects),
        "success": success,
        "failed": failed,
        "llm_analyzed": analyzed,
        "errors": errors,
    }
    latest["last_sync"] = last_sync

    # 写「最新系统开发情况」声明到 latest.json
    latest["dev_status"] = {
        "generated_at": finished.isoformat(),
        "schedule_id": schedule.get("id"),
        "schedule_name": schedule.get("name"),
        "note": DISCLAIMER,
        "llm_analyzed_projects": analyzed,
        "total_projects": len(projects),
    }
    _save_latest(latest)

    return {
        "schedule_id": schedule.get("id"),
        "sync": "ok" if sync_ok else "error",
        "sync_error": sync_err,
        "last_sync": last_sync,
    }


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
