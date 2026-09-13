# -*- coding: utf-8 -*-
"""打包产物校验器（每次发布前跑一遍）。

覆盖本项目历史上踩过的坑：
  · 运行中的 app 会锁 dist/ → 这里只读，不写
  · asar 是未压缩的，可直接搜字节，但不能只信它（真源是 resources/）
  · 必须确认没有真实 config.json / backend/data 混进包
  · 后端 exe 必须与 backend/dist 的源产物逐字节一致，否则发的是旧后端
  · PyInstaller 归档里的关键标识要真的在（不能只看源码里有没有）

用法：
  python tools/verify_build.py                       # 静态校验 + 启动 exe 探路由
  python tools/verify_build.py --no-run              # 只做静态校验（不占 8765 端口）
  python tools/verify_build.py --html-needle 新前端标识 --exe-needle 新后端标识
                                                     # 追加本轮改动关键字；
                                                     # 前端改动查 index.html，后端改动查 exe 字节码，
                                                     # 两者必须分开传（混着传必然误报）
"""
import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 稳定存在的关键路由：缺任何一条都说明后端没打全
CORE_ROUTES = [
    "/api/cron/{server_id}/list",
    "/api/cron/{server_id}/get",
    "/api/cron/{server_id}/save",
    "/api/cron/{server_id}/backup",
    "/api/monitor/{server_id}",
    "/api/sync",
]

results = []


def check(name, cond, extra=""):
    results.append((bool(cond), f"{name}{(' -> ' + extra) if extra else ''}"))


def md5(path):
    h = hashlib.md5()
    with io.open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(path):
    return io.open(path, encoding="utf-8", errors="replace").read()


# --------------------------------------------------------------------------
# 1) 静态产物
# --------------------------------------------------------------------------
def verify_static(html_needles):
    pkg = json.loads(read_text(os.path.join(ROOT, "package.json")))
    ver = pkg["version"]
    print(f"package.json version = {ver}")

    res = os.path.join(ROOT, "dist", "win-unpacked", "resources")
    if not os.path.isdir(res):
        check("dist/win-unpacked/resources 存在", False)
        return
    check("dist/win-unpacked/resources 存在", True)

    # index.html
    idx = os.path.join(res, "index.html")
    if os.path.isfile(idx):
        html = read_text(idx)
        check("index.html 版本号与 package.json 一致", f"'{ver}'" in html,
              f"期望 '{ver}'")
        check("index.html 无旧版本号残留",
              not re.search(r"APP_VERSION\s*=\s*'\d+\.\d+\.\d+';", html)
              or re.search(r"APP_VERSION\s*=\s*'([\d.]+)';", html).group(1) == ver)
        src_html = read_text(os.path.join(ROOT, "index.html"))
        check("index.html 与源码一致", html == src_html)
        for n in html_needles:
            check(f"index.html 含 {n}", n in html)
    else:
        check("resources/index.html 存在", False)

    # 后端 exe
    src_exe = os.path.join(ROOT, "backend", "dist", "devcenter-backend.exe")
    dst_exe = os.path.join(res, "backend", "devcenter-backend.exe")
    if os.path.isfile(dst_exe) and os.path.isfile(src_exe):
        a, b = md5(src_exe), md5(dst_exe)
        check("backend exe 与源产物 MD5 一致", a == b, f"{os.path.getsize(dst_exe)} bytes")
    else:
        check("resources/backend/devcenter-backend.exe 存在", False)

    # app.asar
    asar = os.path.join(res, "app.asar")
    if os.path.isfile(asar):
        blob = read_text(asar)
        for n in ["closeLogFile", "isAnotherInstanceRunning", "devcenter-backend", ver]:
            check(f"app.asar 含 {n}", n in blob)
    else:
        check("resources/app.asar 存在", False)

    # 泄漏
    leaks = []
    for dirpath, _, filenames in os.walk(res):
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), res).replace("\\", "/")
            if fn == "config.json" or rel.startswith("backend/data/"):
                leaks.append(rel)
    check("无真实 config.json / backend/data 泄漏", not leaks, ",".join(leaks))
    check("config.example.json 已随包", os.path.isfile(os.path.join(res, "config.example.json")))

    pexe = os.path.join(ROOT, "dist", "Personal-AI-Dev-Center.exe")
    check("dist/Personal-AI-Dev-Center.exe 已生成", os.path.isfile(pexe),
          f"{os.path.getsize(pexe)} bytes" if os.path.isfile(pexe) else "")


# --------------------------------------------------------------------------
# 2) 启动后端 exe 探真实注册的路由
# --------------------------------------------------------------------------
def verify_routes():
    exe = os.path.join(ROOT, "backend", "dist", "devcenter-backend.exe")
    if not os.path.isfile(exe):
        check("后端 exe 存在（路由探测）", False)
        return
    out = os.path.join(tempfile.gettempdir(), "devcenter_verify_boot.log")
    fh = io.open(out, "w", encoding="utf-8", errors="replace")
    p = subprocess.Popen([exe], stdout=fh, stderr=subprocess.STDOUT, cwd=ROOT)
    spec = None
    try:
        import urllib.request
        for _ in range(40):
            time.sleep(1)
            if p.poll() is not None:
                break
            try:
                with urllib.request.urlopen("http://127.0.0.1:8765/openapi.json", timeout=3) as r:
                    spec = json.loads(r.read().decode("utf-8"))
                    break
            except Exception:
                continue
    finally:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
        time.sleep(1)
        fh.close()

    if spec is None:
        check("后端 exe 能启动并暴露 openapi.json", False, io.open(out, encoding="utf-8",
                                                              errors="replace").read()[-300:])
        return
    check("后端 exe 能启动并暴露 openapi.json", True, f"{len(spec.get('paths', {}))} 条路由")
    paths = set(spec.get("paths", {}).keys())
    for r in CORE_ROUTES:
        check(f"路由已注册 {r}", r in paths)


# --------------------------------------------------------------------------
# 3) PyInstaller 归档字节码里的关键字
# --------------------------------------------------------------------------
def walk_code(code, pool, depth=0):
    if depth > 6:
        return
    for c in getattr(code, "co_consts", ()):
        if isinstance(c, str):
            pool.append(c)
        elif isinstance(c, (tuple, list, set, frozenset)):
            for x in c:
                if isinstance(x, str):
                    pool.append(x)
                elif isinstance(x, tuple):
                    for y in x:
                        if isinstance(y, str):
                            pool.append(y)
        elif hasattr(c, "co_consts"):
            walk_code(c, pool, depth + 1)
    for n in getattr(code, "co_names", ()):
        pool.append(n)


def verify_bytecode(needles):
    if not needles:
        return
    try:
        from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    except Exception as e:
        check("PyInstaller 归档读取可用", False, str(e))
        return
    import marshal
    exe = os.path.join(ROOT, "backend", "dist", "devcenter-backend.exe")
    if not os.path.isfile(exe):
        check("后端 exe 存在（字节码校验）", False)
        return
    arc = CArchiveReader(exe)
    pool = []
    pyz_names = [n for n in arc.toc if n.lower().endswith(".pyz")]
    tmp = None
    if pyz_names:
        fd, tmp = tempfile.mkstemp(suffix=".pyz")
        with os.fdopen(fd, "wb") as fh:
            fh.write(arc.extract(pyz_names[0]))
        pyz = ZlibArchiveReader(tmp)
        for mod in pyz.toc:
            try:
                code = pyz.extract(mod)
            except Exception:
                continue
            if hasattr(code, "co_consts"):
                walk_code(code, pool)
    for name in arc.toc:
        if name.lower().endswith(".pyc"):
            try:
                walk_code(marshal.loads(arc.extract(name)[16:]), pool)
            except Exception:
                pass
    if tmp and os.path.isfile(tmp):
        os.remove(tmp)
    blob = "\n".join(pool)
    check("已解析 exe 字节码常量池", len(pool) > 1000, f"{len(pool)} 项")
    for n in needles:
        check(f"exe 字节码含 {n}", n in blob)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-run", action="store_true", help="不启动后端 exe")
    ap.add_argument("--html-needle", action="append", default=[],
                    help="本轮前端改动关键字（查 index.html），可重复")
    ap.add_argument("--exe-needle", action="append", default=[],
                    help="本轮后端改动关键字（查 exe 字节码），可重复")
    args = ap.parse_args()

    verify_static(args.html_needle)
    if not args.no_run:
        verify_routes()
    verify_bytecode(args.exe_needle)

    print("\n".join(f"{'PASS' if ok else 'FAIL'}  {msg}" for ok, msg in results))
    bad = [m for ok, m in results if not ok]
    print("----")
    print(f"合计 {len(results)} 项，失败 {len(bad)} 项")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    sys.exit(main())
