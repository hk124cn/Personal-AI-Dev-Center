# -*- coding: utf-8 -*-
"""回归测试：后台重绘不得破坏正在填写的资产弹窗。

复现的真实 bug（用户报的「添加手机号资产时突然变成『新增项目』、输入全没了」）：
  render() 里有一句 `if(editMode) renderEditModal()`，而 renderEditModal() 只认
  server / project 两种 type —— type='asset' 会掉进它的 else 分支，把资产弹窗
  整块换成「新增项目」表单（标题取值 crud_new_project）。
  触发点：每 5 分钟的 fetchAPIData 轮询、或同步结束后的刷新，都会调 render()。

用法：
    python tools/test_asset_modal.py                     # 测当前 index.html（应全 PASS）
    python tools/test_asset_modal.py --source old.html   # 测旧稿（应出现 FAIL）

实现：把断言脚本注入 index.html 的副本，用系统 Chrome 无头跑 --dump-dom 取回结果。
（本机 Electron 在沙箱里跑不起来，视觉/交互回归统一走 Chrome 无头，见 AGENTS.md §8。）
"""
import argparse
import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shot_variants import find_chrome  # noqa: E402

CHROME_FLAGS = [
    '--headless=new', '--disable-gpu', '--no-sandbox',
    '--allow-file-access-from-files', '--disable-extensions',
    '--virtual-time-budget=9000',
]

INJECT = r'''
<script>
setTimeout(function () {
  var out = [];
  function chk(name, cond) { out.push((cond ? 'PASS' : 'FAIL') + ' ' + name); }
  try {
    for (var i = 1; i < 200000; i++) { clearInterval(i); clearTimeout(i); }
    var st = document.createElement('style');
    st.textContent = '*,*::before,*::after{animation:none!important;transition:none!important}';
    document.head.appendChild(st);

    state.view = 'assets';
    addAsset();

    chk('addAsset 后 editMode.type 是 asset', !!(editMode && editMode.type === 'asset'));
    chk('弹窗标题 = 添加资产', !!(state.modal && state.modal.title === t('asset_add')));

    var b1 = document.querySelector('.modal-body');
    chk('资产表单已渲染 (有 asset_name)', !!(b1 && b1.querySelector('[name="asset_name"]')));

    // 模拟用户输入：写值 + 派发 input 事件（草稿捕获依赖它）
    var nm = b1.querySelector('[name="asset_name"]');
    var ph = b1.querySelector('[name="asset_phone"]');
    nm.value = '测试号码-156xxxx';
    nm.dispatchEvent(new Event('input', { bubbles: true }));
    if (ph) { ph.value = '15600001111'; ph.dispatchEvent(new Event('input', { bubbles: true })); }

    // —— 模拟后台轮询触发的那次 render()（原 bug 的触发点）——
    render();

    var b2 = document.querySelector('.modal-body');
    chk('重绘后标题仍是 添加资产 (不是 新增项目)', !!(state.modal && state.modal.title === t('asset_add')));
    // 用户看到的是 DOM 里的标题（旧版正是这里被 renderEditModal 改成了「新增项目」）
    var h2 = document.querySelector('.modal-header h2');
    chk('重绘后 DOM 可见标题仍是 添加资产', !!(h2 && h2.textContent === t('asset_add')));
    chk('重绘后未出现项目表单字段 [name=desc]', !(b2 && b2.querySelector('[name="desc"]')));
    chk('重绘后未出现项目表单字段 [name=techStack]', !(b2 && b2.querySelector('[name="techStack"]')));
    chk('重绘后仍是资产表单 (有 asset_name)', !!(b2 && b2.querySelector('[name="asset_name"]')));

    var nm2 = b2 && b2.querySelector('[name="asset_name"]');
    var ph2 = b2 && b2.querySelector('[name="asset_phone"]');
    chk('重绘后「名称」输入被保留', !!(nm2 && nm2.value === '测试号码-156xxxx'));
    chk('重绘后「手机号码」输入被保留', !!(ph2 && ph2.value === '15600001111'));

    // 再补一刀：连续两次重绘也要稳住
    render();
    var b3 = document.querySelector('.modal-body');
    var nm3 = b3 && b3.querySelector('[name="asset_name"]');
    chk('连续重绘后输入仍被保留', !!(nm3 && nm3.value === '测试号码-156xxxx'));

    // 切换分类（会走 renderAssetEditModal）后草稿也要在
    if (editMode) { editMode.data.category = 'sim'; renderAssetEditModal(); }
    var b4 = document.querySelector('.modal-body');
    var nm4 = b4 && b4.querySelector('[name="asset_name"]');
    chk('切换分类重绘后输入仍被保留', !!(nm4 && nm4.value === '测试号码-156xxxx'));
  } catch (e) {
    out.push('FAIL 注入异常: ' + (e && e.message));
  }
  var d = document.createElement('div');
  d.id = 'TESTRESULT';
  d.setAttribute('data-result', out.join(' | '));
  document.body.appendChild(d);
  document.title = 'TESTDONE';
}, 1500);
</script>
'''


def run(src_path, chrome, tmpdir):
    src = io.open(src_path, encoding='utf-8').read()
    if '</body>' not in src:
        print('ERROR: 源文件里找不到 </body>', file=sys.stderr)
        return None
    page = os.path.join(tmpdir, 'asset_modal_test.html')
    io.open(page, 'w', encoding='utf-8').write(src.replace('</body>', INJECT + '</body>', 1))

    prof = os.path.join(tmpdir, 'chrome_prof')
    cmd = [chrome] + CHROME_FLAGS + [
        '--user-data-dir=' + prof,
        '--dump-dom',
        'file:///' + page.replace('\\', '/'),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=180)
    dom = r.stdout or ''
    m = re.search(r'data-result="([^"]*)"', dom)
    if not m:
        return ['FAIL 没取到测试结果（注入未执行？）'] + \
               (['DETAIL ' + dom[-300:]] if 'TESTDONE' not in dom else [])
    return [x.strip() for x in m.group(1).split('|') if x.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', default=os.path.join(ROOT, 'index.html'))
    ap.add_argument('--keep', action='store_true', help='保留临时页面')
    args = ap.parse_args()

    chrome = find_chrome()
    if not chrome:
        print('ERROR: 找不到 Chrome/Edge', file=sys.stderr)
        return 2

    import tempfile
    tmpdir = os.path.join(os.environ.get('TEMP', '.'), 'padc_asset_test')
    os.makedirs(tmpdir, exist_ok=True)

    print('源文件: %s' % args.source)
    print('chrome: %s' % chrome)
    print()
    lines = run(args.source, chrome, tmpdir)
    if lines is None:
        return 2
    ok = fail = 0
    for line in lines:
        if line.startswith('PASS'):
            ok += 1
        elif line.startswith('FAIL'):
            fail += 1
        print('  ' + line)
    print()
    print('结果: %d 通过 / %d 失败' % (ok, fail))
    return 0 if (fail == 0 and ok > 0) else 1


if __name__ == '__main__':
    sys.exit(main())
