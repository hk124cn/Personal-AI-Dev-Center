# -*- coding: utf-8 -*-
"""统一回归入口：一条命令跑完本仓库所有不需要真实服务器的检查。

    python tools/run_checks.py            # 全部
    python tools/run_checks.py --fast     # 只跑静态检查（语法/结构），秒级

覆盖：
  1. index.html 结构（<style>/<script> 配平）
  2. index.html 内联 JS 语法（node --check）
  3. backend/*.py 语法编译
  4. 前端逻辑自测 tools/test_syncbar.mjs（从 index.html 抽真实函数）
  5. 同步合并自测 tools/test_syncmerge.mjs（问题不污染功能清单 / 内容时间换源 / 首页徽标）
  6. R8/R9 集成测试   tools/test_r8_r9.py
  7. R6/R7/R10 集成测试 tools/test_security_robustness.py
  8. 保密自查：config.json 未被 git 跟踪
  9. 弹窗草稿回归     tools/test_asset_modal.py（需 Chrome 无头）

依赖：6/7 需要 fastapi / paramiko 等，优先用 backend/build_venv 的解释器。
"""
import argparse
import glob
import io
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, 'tools')

PY_CANDIDATES = [
    os.path.join(ROOT, 'backend', 'build_venv', 'Scripts', 'python.exe'),
    os.path.join(ROOT, 'backend', 'build_venv', 'bin', 'python'),
]
NODE_CANDIDATES = [
    r'C:\Users\qw_20\.workbuddy\binaries\node\versions\22.22.2-3\node.exe',
    r'C:\Program Files\nodejs\node.exe',
]


def pick(cands, name, fallback=None):
    for c in cands:
        if os.path.exists(c):
            return c
    w = shutil.which(name)
    return w or fallback


GIT_CANDIDATES = [
    r'C:\Program Files\Git\cmd\git.exe',
    r'C:\Program Files (x86)\Git\cmd\git.exe',
]

PY = pick(PY_CANDIDATES, 'python', sys.executable)
NODE = pick(NODE_CANDIDATES, 'node', None)
GIT = pick(GIT_CANDIDATES, 'git', None)

RESULTS = []


def record(name, ok, detail=''):
    RESULTS.append((name, ok, detail))
    print('  %-5s %s%s' % ('PASS' if ok else 'FAIL', name, ('  — ' + detail) if detail else ''))
    return ok


def run(cmd, cwd=ROOT, timeout=300):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, (r.stdout or '') + (r.stderr or '')


# ---------- 1. index.html 结构 ----------
def check_html_structure():
    src = io.open(os.path.join(ROOT, 'index.html'), encoding='utf-8').read()
    # 注意：开始标签可能带属性（如 <script src=...>），所以只数前缀 <script / <style
    pairs = [('style', src.count('<style'), src.count('</style>')),
             ('script', src.count('<script'), src.count('</script>'))]
    for tag, o, c in pairs:
        record('index.html <%s> 配平' % tag, o == c, '%d/%d' % (o, c))

    # 历史上出过「CSS 被插到 </style> 之后」的事故：CSS 文本漏在正文里会被当文字渲染出来。
    # 判据：把最后一个 </style> 之后的 <script>…</script> 内容剔掉，若还残留
    # 「.类名{ … 声明 }」形状的文本，说明确实有裸 CSS 泄漏到了正文。
    tail = src[src.rfind('</style>'):]
    tail = re.sub(r'<script\b[^>]*>.*?</script>', '', tail, flags=re.S)
    leaked = re.findall(r'\.[a-zA-Z][\w-]*\s*\{[^{}]*:', tail)
    record('</style> 之后没有裸 CSS 泄漏', not leaked,
           ('疑似泄漏: ' + ', '.join(leaked[:3])) if leaked else '')


# ---------- 2. 内联 JS 语法 ----------
def check_js_syntax():
    if not NODE:
        return record('内联 JS 语法', False, '找不到 node')
    src = io.open(os.path.join(ROOT, 'index.html'), encoding='utf-8').read()
    blocks = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', src, re.S)
    tmp = os.path.join(os.environ.get('TEMP', '.'), '_run_checks_inline.js')
    bad = []
    for i, b in enumerate(blocks):
        io.open(tmp, 'w', encoding='utf-8').write(b)
        rc, out = run([NODE, '--check', tmp])
        if rc != 0:
            bad.append('block%d: %s' % (i, out.strip().splitlines()[:2]))
    record('index.html 内联 JS 语法 (%d 块)' % len(blocks), not bad, '; '.join(bad)[:200])


# ---------- 3. backend 语法 ----------
def check_backend_syntax():
    files = sorted(glob.glob(os.path.join(ROOT, 'backend', '*.py')))
    rc, out = run([PY, '-m', 'py_compile'] + files)
    record('backend/*.py 语法 (%d 个文件)' % len(files), rc == 0, out.strip().splitlines()[-1] if rc else '')


# ---------- 7. 保密自查 ----------
def check_secrets():
    if not GIT:
        record('config.json 未被 git 跟踪', False, '找不到 git')
    else:
        rc, out = run([GIT, 'ls-files', '--error-unmatch', 'config.json'])
        record('config.json 未被 git 跟踪', rc != 0)
    gi = os.path.join(ROOT, '.gitignore')
    txt = io.open(gi, encoding='utf-8').read() if os.path.exists(gi) else ''
    miss = [k for k in ('config.json', 'dist', 'backend/data') if k not in txt]
    record('.gitignore 覆盖关键项', not miss, ('缺: ' + ','.join(miss)) if miss else '')


# ---------- 4/5/6. 脚本 ----------
def run_script(name, cmd, label):
    try:
        rc, out = run(cmd)
    except subprocess.TimeoutExpired:
        return record(label, False, '超时')
    tail = [l for l in out.strip().splitlines() if l.strip()][-1:] or ['']
    record(label, rc == 0, tail[0][:160])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fast', action='store_true', help='只跑静态检查')
    args = ap.parse_args()

    print('repo : %s' % ROOT)
    print('py   : %s' % PY)
    print('node : %s' % (NODE or '(未找到)'))
    print()

    print('== 静态检查 ==')
    check_html_structure()
    check_js_syntax()
    check_backend_syntax()
    check_secrets()

    if not args.fast:
        print()
        print('== 行为测试 ==')
        if NODE:
            run_script('test_syncbar', [NODE, os.path.join(TOOLS, 'test_syncbar.mjs')],
                       'tools/test_syncbar.mjs')
            run_script('test_syncmerge', [NODE, os.path.join(TOOLS, 'test_syncmerge.mjs')],
                       'tools/test_syncmerge.mjs (问题不污染功能清单 / 内容时间换源)')
        else:
            record('tools/test_syncbar.mjs', False, '找不到 node')
        run_script('test_r8_r9', [PY, os.path.join(TOOLS, 'test_r8_r9.py')],
                   'tools/test_r8_r9.py (R8/R9)')
        run_script('test_security', [PY, os.path.join(TOOLS, 'test_security_robustness.py')],
                   'tools/test_security_robustness.py (R6/R7/R10)')
        # 需要 Chrome 无头；没有 Chrome 就跳过而不是判失败
        run_script('test_asset_modal', [PY, os.path.join(TOOLS, 'test_asset_modal.py')],
                   'tools/test_asset_modal.py (弹窗草稿不回丢)')

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print()
    print('结果: %d/%d 通过' % (passed, total))
    for n, ok, d in RESULTS:
        if not ok:
            print('   FAILED: %s %s' % (n, d))
    return 0 if passed == total else 1


if __name__ == '__main__':
    sys.exit(main())
