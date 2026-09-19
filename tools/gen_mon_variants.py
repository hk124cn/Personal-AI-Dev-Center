# -*- coding: utf-8 -*-
"""生成「服务器实时监控」弹窗的截图变体页，供系统 Chrome 无头截图做视觉回归。

为什么不用 Electron：本机沙箱里 Electron 跑不起来（GPU 进程崩 + file:// 被拒），
所以走「变体页 + Chrome 无头」路线。详见 .workbuddy/memory/MEMORY.md。

用法：
    python tools/gen_mon_variants.py                 # 用当前 index.html
    python tools/gen_mon_variants.py --source old.html   # 传旧稿做「改动前」对照

输出目录：%TEMP%/padc_variants/*.html
截图（示例，路径按实际 Chrome 位置调整）：
    chrome.exe --headless=new --disable-gpu --no-sandbox \
      --allow-file-access-from-files --force-device-scale-factor=1.5 \
      --virtual-time-budget=9000 --window-size=1400,1100 \
      --screenshot=out.png file:///.../mon-light.html
"""
import argparse
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(os.environ.get('TEMP', '.'), 'padc_variants')

INJECT = r'''
<script>
setTimeout(function () {
  try {
    // 1) 冻结页面自身的定时器，否则注入状态会被它的轮询覆盖
    for (var i = 1; i < 200000; i++) { clearInterval(i); clearTimeout(i); }
    // 2) 禁动画：.fade-in 停在中间帧会让整页发暗
    var st = document.createElement('style');
    st.textContent = '*,*::before,*::after{animation:none!important;transition:none!important;backdrop-filter:none!important}';
    document.head.appendChild(st);

    // 造一段带真实波动感的历史：CPU 双频正弦 / 内存缓慢漂移 / 磁盘几乎不动
    var now = Date.now(), hist = [], N = 40;
    for (var k = 0; k < N; k++) {
      var ph = k / N * Math.PI * 3;
      var iso = new Date(now - (N - 1 - k) * 60000).toISOString().slice(0, 19);
      hist.push({
        cpu_percent:  Math.round((35 + 25 * Math.sin(ph) + 8 * Math.sin(ph * 3.7)) * 10) / 10,
        mem_percent:  Math.round((58 + 7 * Math.sin(ph * 0.6) + 3 * Math.cos(ph * 2.1)) * 10) / 10,
        disk_percent: Math.round((71 + 1.2 * Math.sin(ph * 0.2)) * 10) / 10,
        ts: iso
      });
    }

    if (typeof applyTheme === 'function') applyTheme(__THEME__);
    state.view = 'servers';
    var s = (state.servers || []).filter(function (x) { return x && x.id !== 'future'; })[0]
            || { id: 'demo', name: 'demo-server' };
    state.modal = { title: '实时监控 · ' + (s.name || 'demo'), body: monitorHTML(s), size: 'modal-xl' };
    _monHistory = hist;
    _monData = hist.map(function (h) { return h.cpu_percent; });
    __EXTRA__
    render();
    // 3) 清掉 modal/toast 残留与 fade-in
    document.querySelectorAll('.fade-in').forEach(function (n) { n.classList.remove('fade-in'); });
    document.querySelectorAll('.toast, #toast-el').forEach(function (n) { n.remove(); });
    if (window.lucide) lucide.createIcons();
    if (typeof updateMonChart === 'function') updateMonChart();
    document.title = 'READY-MON';
  } catch (e) {
    document.title = 'INJECT_ERR: ' + (e && e.message);
  }
}, 1500);
</script>
'''

# name, theme, 额外注入（在图例/画布就绪前执行）
VARIANTS = [
    ('mon-dark',        "'violet'", ''),
    ('mon-light',       "'light'",  ''),
    ('mon-all3',        "'violet'", "_monSeriesOn = { cpu:true, mem:true, disk:true };"),
    ('mon-light-hover', "'light'",  "_monSeriesOn = { cpu:true, mem:true, disk:true }; _monHover = 26;"),
    ('mon-hover',       "'violet'", "_monHover = 26;"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', default=os.path.join(ROOT, 'index.html'),
                    help='源 HTML（默认 index.html；可传旧稿做对照）')
    args = ap.parse_args()

    src = io.open(args.source, encoding='utf-8').read()
    if '</body>' not in src:
        print('ERROR: 源文件里找不到 </body>', file=sys.stderr)
        return 2
    if 'function updateMonChart' not in src:
        print('ERROR: 源文件里没有 updateMonChart（监控图表改造未生效？）', file=sys.stderr)
        return 2

    os.makedirs(OUT, exist_ok=True)
    for name, theme, extra in VARIANTS:
        html = src.replace(
            '</body>',
            INJECT.replace('__THEME__', theme).replace('__EXTRA__', extra) + '</body>',
            1)
        p = os.path.join(OUT, name + '.html')
        io.open(p, 'w', encoding='utf-8').write(html)
        print('wrote', p)
    print('OUT=' + OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
