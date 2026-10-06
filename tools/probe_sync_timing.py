# -*- coding: utf-8 -*-
"""只读计时探测：定位单个项目「同步」耗时的分布（SSH 各步 vs 其它）。

不改任何数据、不下载文件、不碰 crontab —— 只跑 find / cat / stat。

用法：
  python tools/probe_sync_timing.py 打字游戏
"""
import json
import os
import shlex
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import paramiko  # noqa: E402
from backend import sync as S  # noqa: E402


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "打字游戏"
    d = os.path.join(os.environ["APPDATA"], "Personal AI Dev Center")
    cfg = json.load(open(os.path.join(d, "config.json"), encoding="utf-8"))
    srvmap = {s["id"]: s for s in cfg["servers"]}
    projs = [p for p in cfg["projects"] if p.get("name") == name]
    if not projs:
        print("找不到项目:", name)
        return 2
    proj = projs[0]
    srv = srvmap.get(proj.get("server_id") or proj.get("server"))
    if not srv:
        print("找不到服务器配置")
        return 2
    pd = proj["remote_path"]
    print(f"project = {name}")
    print(f"server  = {srv.get('name')}")
    print(f"remote  = {pd}")
    print("-" * 52)

    t = time.time()
    key = paramiko.Ed25519Key.from_private_key_file(os.path.expanduser(srv["key_path"]))
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(hostname=srv["host"], port=srv["port"], username=srv["user"],
                pkey=key, timeout=15)
    print(f"[1] SSH connect        : {time.time() - t:7.1f}s")

    t = time.time()
    _, so, _ = cli.exec_command(f"find {S._shell_path(pd)} -maxdepth 1 -name '*.md' -type f")
    out = so.read().decode("utf-8", errors="replace")
    files = [x for x in out.splitlines() if x.strip()]
    print(f"[2] find *.md          : {time.time() - t:7.1f}s  ({len(files)} 个)")

    for f in files:
        t = time.time()
        _, so, _ = cli.exec_command(f"cat {shlex.quote(f)}")
        c = so.read().decode("utf-8", errors="replace")
        print(f"[3] cat {os.path.basename(f):<14}: {time.time() - t:7.1f}s  ({len(c)} 字符)")

    t = time.time()
    _, so, _ = cli.exec_command(f"stat -c '%Y' {S._shell_path(pd)} 2>/dev/null || echo '0'")
    so.read()
    print(f"[4] stat 目录 mtime     : {time.time() - t:7.1f}s")

    prune = " -o ".join(f"-name {shlex.quote(x)}" for x in sorted(S.DEFAULT_SYNC_IGNORE))
    skip_ext = " ".join(f"! -name {shlex.quote('*' + e)}" for e in sorted(S.DEFAULT_SYNC_IGNORE_EXT))
    t = time.time()
    _, so, _ = cli.exec_command(
        f"cd {S._shell_path(pd)} 2>/dev/null && "
        f"find . \\( {prune} \\) -prune -o -type f {skip_ext} "
        f"-printf '%T@\\n' 2>/dev/null | sort -rn | head -1", timeout=180)
    v = so.read().decode("utf-8", errors="replace").strip()
    print(f"[5] content_mtime find : {time.time() - t:7.1f}s  -> {v[:24]}")

    cli.close()
    print("-" * 52)
    print("以上为 SSH 侧全部耗时；剩余时间即为 LLM 分析。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
