# -*- coding: utf-8 -*-
"""清理 config.json 里被误当「功能条目」持久化的 [问题] / [Issue] 条目。

背景
----
旧版前端把同步解析出的问题用 `features.push('[问题]' + issue)` 塞进项目的
features 数组。用户一旦在「编辑项目」里保存，这批条目就被写回 config.json，
把真实功能清单挤空（实测 9 个项目共 292 条，AUTO-STOCK 的 126 条功能条目
全是 [问题]）。

v1.4.23 起前端改用独立的 `_issues` 字段承载问题，并在载入时调用
`sanitizeFeatures()` 清洗；本脚本用于把**已被写坏的历史数据**一次性清干净。

用法
----
  python tools/clean_issue_features.py            # 只统计（默认 dry-run，不写盘）
  python tools/clean_issue_features.py --apply    # 备份后写回
  python tools/clean_issue_features.py --apply --config <path>

安全约束
--------
  · app 运行中拒绝 --apply —— app 退出时会把内存态回写 config.json 覆盖本次结果
  · 写回前自动备份为 config.json.bak.<时间戳>
  · 写回后读回验证，仍有污染则返回 1
"""
import argparse
import io
import json
import os
import shutil
import socket
import sys
import time

PREFIXES = ("[问题]", "[Issue]")


def is_issue_entry(f):
    if not isinstance(f, dict):
        return False
    return str(f.get("name", "")).startswith(PREFIXES)


def default_config_path():
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "Personal AI Dev Center", "config.json")
    return None


def app_running(host="127.0.0.1", port=8765):
    s = socket.socket()
    s.settimeout(0.6)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写回（默认只统计）")
    ap.add_argument("--config", default=None, help="config.json 路径")
    ap.add_argument("--force", action="store_true", help="app 运行中也强行写（不推荐）")
    args = ap.parse_args()

    path = args.config or default_config_path()
    if not path or not os.path.isfile(path):
        print("找不到 config.json：", path)
        return 2
    print("config:", path)

    data = json.load(io.open(path, encoding="utf-8"))
    projects = data.get("projects") or []
    total = 0
    changed = 0
    for pr in projects:
        feats = pr.get("features")
        if not isinstance(feats, list):
            continue
        bad = [f for f in feats if is_issue_entry(f)]
        if not bad:
            continue
        total += len(bad)
        changed += 1
        print("  {:<26} features {} -> {}  (清 {})".format(
            str(pr.get("name", "?"))[:24], len(feats), len(feats) - len(bad), len(bad)))
        if args.apply:
            pr["features"] = [f for f in feats if not is_issue_entry(f)]

    print("----")
    print("受影响项目 {} 个，共 {} 条 [问题]/[Issue] 条目".format(changed, total))

    if not args.apply:
        print("dry-run：未写盘。加 --apply 执行清理。")
        return 0

    if total == 0:
        print("无需清理。")
        return 0

    if app_running() and not args.force:
        print("!! 检测到 app 正在运行（127.0.0.1:8765 有监听）。")
        print("   app 退出时会把内存态回写 config.json，覆盖本次清理。")
        print("   请先关闭 Personal AI Dev Center，再重新执行 --apply。")
        return 3

    bak = path + ".bak." + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, bak)
    print("已备份:", bak)

    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    print("已写回:", path)

    d2 = json.load(io.open(path, encoding="utf-8"))
    left = sum(1 for pr in (d2.get("projects") or [])
               for f in (pr.get("features") or []) if is_issue_entry(f))
    print("读回验证：剩余污染条目 =", left, "OK" if left == 0 else "FAIL")
    return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
