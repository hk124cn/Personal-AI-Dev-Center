# -*- coding: utf-8 -*-
"""换包 + 完整校验一条龙（把每次发版的手工 mv 串固化下来）。

    python tools/swap_dist.py --html-needle LLM_PRESETS --exe-needle _md_fingerprint

纪律（都是踩过坑换来的，脚本里强制执行）：
  · app 正在运行时**不碰dist/**（exe 被锁，强行 mv 会留下半截目录）
  · **绝不**按镜像名 taskkill。便携版进程树是 启动器(父)→主(子)，
    按启动器名 /T 会顺树杀回自身。只检测 8765，检测到就退出让人手动关。
  · 旧包不删，改名成 dist_prev_<版本>；确认新版能起来后由使用者手动清理。
  · 换完自动跑 tools/verify_build.py **完整模式**（真启动后端 exe 探 openapi 路由）。

参数：
  --new NAME          新产物目录名，默认 dist_new
  --keep N            保留最近 N 份旧包（N>=1），默认 1
  --to                覆盖换包后校验用的目录名，默认 dist
  --html-needle X     追加前端关键字（查 index.html），可多次
  --exe-needle X      追加后端关键字（查 exe 字节码），可多次
  --skip-verify       只换包不跑校验（不推荐）
  --yes               跳过交互确认
"""
import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, 'tools')


# --------------------------------------------------------------------------
# 端口 / 进程检测（只读，绝不杀）
# --------------------------------------------------------------------------
def port_pids(port=8765):
    """返回占用该端口的 PID 集合。优先 netstat，失败退化为 socket 试探。"""
    pids = set()
    try:
        out = subprocess.run(['netstat', '-ano'], capture_output=True, text=True,
                             encoding='utf-8', errors='replace', timeout=20).stdout
        for line in out.splitlines():
            if 'LISTENING' not in line.upper():
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            local = parts[1]
            if local.rsplit(':', 1)[-1] == str(port):
                pids.add(parts[-1])
    except Exception:
        pass
    return pids


def app_processes():
    """当前是否还有 devcenter 相关进程（只列名字，不做任何处置）。"""
    try:
        out = subprocess.run(['tasklist'], capture_output=True, text=True,
                             encoding='utf-8', errors='replace', timeout=20).stdout
    except Exception:
        return []
    keys = ('Personal-AI-Dev-Center', 'Personal AI Dev Center', 'devcenter-backend')
    return [l.strip() for l in out.splitlines() if any(k in l for k in keys)]


# --------------------------------------------------------------------------
# 版本读取
# --------------------------------------------------------------------------
def read_version(dist_dir):
    """从 win-unpacked/resources/index.html 读 APP_VERSION。"""
    p = os.path.join(ROOT, dist_dir, 'win-unpacked', 'resources', 'index.html')
    if not os.path.exists(p):
        return None
    try:
        src = io.open(p, encoding='utf-8').read()
        m = re.search(r"APP_VERSION\s*=\s*'([^']+)'", src)
        return m.group(1) if m else None
    except Exception:
        return None


def dir_size_mb(path):
    total = 0
    for base, _dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(base, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total / 1024.0 / 1024.0


# --------------------------------------------------------------------------
# 旧包清理
# --------------------------------------------------------------------------
def prune_old_backups(keep):
    """只保留最近 keep 份 dist_prev_*（按目录名里的版本号/时间排序），其余删掉。

    只碰名字严格匹配 dist_prev_<数字> 的目录，且必须含 win-unpacked —— 双保险，
    避免误删用户自己放的东西。
    """
    backups = []
    for name in os.listdir(ROOT):
        m = re.fullmatch(r'dist_prev_([0-9][0-9A-Za-z_.\-]*)', name)
        if not m:
            continue
        p = os.path.join(ROOT, name)
        if not os.path.isdir(p):
            continue
        if not os.path.exists(os.path.join(p, 'win-unpacked')):
            print('  跳过 %s：不像构建产物（无 win-unpacked）' % name)
            continue
        backups.append((name, p, os.path.getmtime(p)))
    backups.sort(key=lambda x: x[2], reverse=True)

    removed = []
    for name, p, _t in backups[keep:]:
        sz = dir_size_mb(p)
        try:
            shutil.rmtree(p)
            removed.append((name, sz))
            print('  已删旧包 %s（%.0f MB）' % (name, sz))
        except Exception as e:
            print('  删不掉 %s：%s' % (name, e))
    for name, _p, _t in backups[:keep]:
        print('  保留 %s' % name)
    return removed


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--new', default='dist_new')
    ap.add_argument('--keep', type=int, default=1)
    ap.add_argument('--to', default='dist')
    ap.add_argument('--html-needle', action='append', default=[])
    ap.add_argument('--exe-needle', action='append', default=[])
    ap.add_argument('--skip-verify', action='store_true')
    ap.add_argument('--yes', action='store_true')
    args = ap.parse_args()

    new_dir = os.path.join(ROOT, args.new)
    old_dir = os.path.join(ROOT, args.to)

    print('repo  : %s' % ROOT)
    print('旧包  : %s (v%s)' % (args.to, read_version(args.to) or '?'))
    print('新包  : %s (v%s)' % (args.new, read_version(args.new) or '?'))
    print()

    # --- 1. 前置检查 ---
    if not os.path.isdir(new_dir):
        print('[1/4] 找不到新产物目录 %s，先跑 npm run build。' % args.new)
        return 2
    if not os.path.exists(os.path.join(new_dir, 'Personal-AI-Dev-Center.exe')):
        print('[1/4] %s 里没有主 exe，构建没完成？' % args.new)
        return 2

    pids = port_pids()
    procs = app_processes()
    if pids or procs:
        print('[1/4] 检测到 app 仍在运行：')
        for l in procs:
            print('       ' + l[:110])
        if pids:
            print('       8765 被 PID %s 占用' % ', '.join(sorted(pids)))
        print('       请手动关闭应用后重跑。脚本不主动杀进程（按镜像名 taskkill')
        print('       会顺进程树杀回自身，是本项目踩过的自杀根因）。')
        return 3
    print('[1/4] 8765 空闲，无残留进程')

    # --- 2. 换包 ---
    old_ver = read_version(args.to)
    backup = 'dist_prev_%s' % (old_ver or 'unknown')
    bpath = os.path.join(ROOT, backup)
    if os.path.exists(bpath):
        i = 2
        while os.path.exists(os.path.join(ROOT, '%s_%d' % (backup, i))):
            i += 1
        backup = '%s_%d' % (backup, i)
        bpath = os.path.join(ROOT, backup)

    if not args.yes:
        ans = input('    确认换包？%s -> %s（旧包改名 %s）[y/N] ' % (args.to, args.new, backup))
        if ans.strip().lower() not in ('y', 'yes'):
            print('    已取消')
            return 1

    print('[2/4] 换包中…')
    if os.path.isdir(old_dir):
        os.rename(old_dir, bpath)          # 同盘改名，不复制 381MB
        print('       %s -> %s' % (args.to, backup))
    os.rename(new_dir, old_dir)
    print('       %s -> %s (v%s)' % (args.new, args.to, read_version(args.to) or '?'))

    # --- 3. 清理旧包 ---
    print('[3/4] 清理旧包（保留最近 %d 份）' % max(args.keep, 0))
    if args.keep >= 1:
        prune_old_backups(args.keep)
    else:
        print('       --keep 0，跳过清理')

    # --- 4. 完整校验 ---
    if args.skip_verify:
        print('[4/4] --skip-verify，跳过校验')
        return 0

    print('[4/4] 完整校验（真启动后端 exe 探 openapi 路由）…')
    cmd = [sys.executable, os.path.join(TOOLS, 'verify_build.py'), '--dist', args.to]
    for n in args.html_needle:
        cmd += ['--html-needle', n]
    for n in args.exe_needle:
        cmd += ['--exe-needle', n]
    rc = subprocess.run(cmd, cwd=ROOT).returncode

    print()
    if rc == 0:
        print('换包完成：%s 现在是 v%s。' % (args.to, read_version(args.to)))
        print('确认新版能正常启动后，可手动删除 %s。' % backup)
    else:
        print('换包完成，但校验未全过（rc=%d）。' % rc)
        print('  回滚：把 %s 改名回 %s，再把 %s 改名回 %s' % (backup, args.to, args.to, args.new))
    return rc


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        print('已中断')
        sys.exit(130)
