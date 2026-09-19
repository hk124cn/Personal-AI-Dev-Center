# -*- coding: utf-8 -*-
"""用系统 Chrome 无头模式给「变体页」批量截图（配合 gen_*_variants.py 使用）。

本机 Electron 在沙箱里跑不起来（GPU 崩 + file:// 被拒），所以视觉回归统一走这条路。

用法：
    python tools/shot_variants.py mon-dark mon-light --dir %TEMP%/padc_variants
    python tools/shot_variants.py --all --dir %TEMP%/padc_variants --out %TEMP%/padc_shots

说明：
- 每个变体用独立的 --user-data-dir，避免和用户正在运行的 Chrome 抢 profile。
- 同时带 --dump-dom 抓注入结果，检查页面是否报 INJECT_ERR（比只看图可靠）。
"""
import argparse
import glob
import os
import subprocess
import sys

CHROME_CANDIDATES = [
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
]

FLAGS = [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    '--allow-file-access-from-files', '--disable-extensions',
    '--force-device-scale-factor=1.5', '--virtual-time-budget=9000',
]


def find_chrome():
    for c in CHROME_CANDIDATES:
        if os.path.exists(c):
            return c
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('names', nargs='*', help='变体名（不含 .html）')
    ap.add_argument('--all', action='store_true', help='截该目录下全部 html')
    ap.add_argument('--dir', default=os.path.join(os.environ.get('TEMP', '.'), 'padc_variants'))
    ap.add_argument('--out', default=None, help='截图输出目录（默认 <dir>/shots）')
    ap.add_argument('--size', default='1000,1050')
    args = ap.parse_args()

    chrome = find_chrome()
    if not chrome:
        print('ERROR: 找不到 Chrome/Edge', file=sys.stderr)
        return 2

    outdir = args.out or os.path.join(args.dir, 'shots')
    os.makedirs(outdir, exist_ok=True)

    if args.all:
        names = [os.path.splitext(os.path.basename(p))[0]
                 for p in sorted(glob.glob(os.path.join(args.dir, '*.html')))]
    else:
        names = args.names
    if not names:
        print('ERROR: 没指定变体（用 --all 或列名字）', file=sys.stderr)
        return 2

    rc = 0
    for n in names:
        page = os.path.join(args.dir, n + '.html')
        if not os.path.exists(page):
            print('SKIP %-18s 页面不存在' % n)
            rc = 1
            continue
        png = os.path.join(outdir, n + '.png')
        prof = os.path.join(os.environ.get('TEMP', '.'), 'padc_chrome_prof', n)
        cmd = [chrome] + FLAGS + [
            '--user-data-dir=' + prof,
            '--window-size=' + args.size,
            '--screenshot=' + png,
            '--dump-dom',
            'file:///' + page.replace('\\', '/'),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        dom = r.stdout or ''
        if 'READY-MON' in dom:
            status = 'READY'
        elif 'INJECT_ERR' in dom:
            i = dom.find('INJECT_ERR')
            status = 'INJECT_ERR: ' + dom[i:i + 160].replace('\n', ' ')
            rc = 1
        else:
            status = 'NO-MARKER (注入未执行?) rc=%d' % r.returncode
            rc = 1
        size = os.path.getsize(png) if os.path.exists(png) else 0
        print('%-18s %-28s %8d bytes  %s' % (n, status[:28], size, png))
    print('OUT=' + outdir)
    return rc


if __name__ == '__main__':
    sys.exit(main())
