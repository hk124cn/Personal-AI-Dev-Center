# -*- coding: utf-8 -*-
"""首页跑分徽章 + 跑分历史趋势图的真实浏览器回归。

覆盖两个新功能（2026-11-01）：
  1. 首页服务器卡片显示最近一次跑分分数徽章（颜色按评级）
  2. 跑分弹窗里显示历史趋势图（折线 + 最高/最低/平均/波动）
     —— **波动幅度是判断宿主机超售的硬证据**：
        白天 85 分、晚高峰 55 分 → 差 30 分 → 严重超售。

真实浏览器（Chrome 无头）+ 真实 DOM 查询，不用字符串匹配 ——
`--dump-dom` 会把 <script> 源码也序列化进 DOM，字符串匹配是假阳性。

用法：
    python tools/test_bench_trend.py
"""
import io
import os
import re
import sys
import json
import time
import shutil
import tempfile
import subprocess
import threading
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

results = []


def check(name, cond, extra=""):
    results.append((bool(cond), "%s%s" % (name, (" -> " + str(extra)) if extra else "")))


CHROME_CANDIDATES = [
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
]
CHROME = next((c for c in CHROME_CANDIDATES if os.path.exists(c)), None)


def main():
    if not CHROME:
        print("跳过：找不到 Chrome")
        return 0

    src = io.open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()

    # ── 静态断言：结构 ──────────────────────────────────────
    check("状态里有 benchScores", "benchScores:{}" in src)
    check("有 loadBenchScores 函数", "function loadBenchScores(" in src)
    check("有 benchScoreBadge 函数", "function benchScoreBadge(" in src)
    check("有 benchDrawTrend 函数", "function benchDrawTrend(" in src)
    check("历史容器 id 唯一（不能出现两次）", src.count('id="bench-hist"') == 1,
          src.count('id="bench-hist"'))
    # 历史容器必须在**弹窗固定结构**里（跑分前也要能看到趋势）
    m = re.search(r"function benchHTML\(s\)\{.*?</div>\s*\n\s*<div id=\"bench-hist\"", src, re.S)
    check("历史容器在弹窗固定结构里（打开就能看趋势）", m is not None)
    # 徽章必须挂在首页两种渲染模式上
    check("环形模式挂了徽章", "dmc-title\"><h4>${s.name} ${benchScoreBadge(s.id)}" in src)
    check("进度条模式挂了徽章", "dmr-info\"><h4>${s.name} ${benchScoreBadge(s.id)}" in src)
    # 波动必须格式化（否则显示 11.300000000000004）
    check("波动值已格式化", "${gap.toFixed(1)}" in src)
    check("平均值已格式化", "avg.toFixed(1)" in src)

    # ── 真实浏览器：渲染徽章 + 趋势图 ─────────────────────
    INJECT = r"""
<script>
(function(){
  window.__steps=[];
  function step(s){ window.__steps.push(s); document.title='S|'+window.__steps.join('>'); }
  var _si=window.setInterval; window.setInterval=function(){return 0;};
  var tries=0;
  var iv=_si(function(){
    tries++;
    if(!(state.servers && state.servers.length)){ if(tries>80){clearInterval(iv); step('NO_SERVERS');} return; }
    clearInterval(iv);
    try{
      // 造一份可控的分数数据：有分数的 + 没分数的
      state.benchScores={
        'srv-a':{score:82.5, grade:'良好', at:1700000000, profile:'quick'},
        'srv-b':{score:61.5, grade:'够用', at:1700000000, profile:'quick'},
        'srv-c':{score:null, grade:'', at:null, profile:''}
      };
      var badgeHtml=benchScoreBadge('srv-a')+benchScoreBadge('srv-c');
      var d=document.createElement('div'); d.id='__probe';
      d.setAttribute('data-badge-a', badgeHtml);
      d.setAttribute('data-badge-c', benchScoreBadge('srv-c'));
      document.body.appendChild(d);

      // 趋势图：喂 4 个点（含一次明显波动）
      var items=[
        {collected_at:1700000000, score:{total_score:85}},
        {collected_at:1700003600, score:{total_score:80}},
        {collected_at:1700007200, score:{total_score:58}},
        {collected_at:1700010800, score:{total_score:60}}
      ];
      var wrap=document.createElement('div');
      wrap.innerHTML='<div class="bench-trend"><canvas id="bench-trend-cv"></canvas></div>'
        +'<div class="bench-trend-sum" id="bench-trend-sum"></div>'
        +'<div class="bench-trend-note" id="bench-trend-note"></div>';
      document.body.appendChild(wrap);
      benchDrawTrend(items);
      var sum=document.getElementById('bench-trend-sum');
      var note=document.getElementById('bench-trend-note');
      var cv=document.getElementById('bench-trend-cv');
      d.setAttribute('data-sum', sum?sum.textContent:'');
      d.setAttribute('data-note', note?note.textContent:'');
      d.setAttribute('data-cv', cv?(cv.width+'x'+cv.height):'null');
      step('PROBE_OK');
    }catch(e){ step('EXCEPTION|'+String(e)); }
  }, 200);
})();
</script>
"""
    v = src.replace("</head>", INJECT + "</head>")
    vpath = os.path.join(tempfile.gettempdir(), "padc_bench_trend_probe.html")
    io.open(vpath, "w", encoding="utf-8").write(v)

    prof = tempfile.mkdtemp(prefix="btrend_")
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                            "--hide-scrollbars", "--disable-extensions",
                            "--virtual-time-budget=9000",
                            "--user-data-dir=" + prof, "--dump-dom",
                            "file:///" + vpath.replace("\\", "/")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        dom = r.stdout or ""
    finally:
        shutil.rmtree(prof, ignore_errors=True)

    def attr(name):
        mm = re.search(r'id="__probe"[^>]*\s%s="([^"]*)"' % name, dom)
        if not mm:
            return ""
        import html as _html
        return _html.unescape(mm.group(1))

    badge_a = attr("data-badge-a")
    badge_c = attr("data-badge-c")
    summ = attr("data-sum")
    note = attr("data-note")
    cvs = attr("data-cv")

    print("  徽章(有分) : %s" % badge_a[:150])
    print("  徽章(无分) : %s" % badge_c[:150])
    print("  趋势摘要   : %s" % summ[:150])
    print("  提示       : %s" % note[:150])
    print("  canvas     : %s" % cvs)
    print()

    check("徽章渲染出分数", "82.5" in badge_a, badge_a[:80])
    check("有分数的徽章带颜色样式", "background:" in badge_a)
    check("无分数的徽章显示 —", "—" in badge_c, badge_c[:80])
    check("无分数的徽章是灰态", "bench-badge-none" in badge_c)
    check("趋势摘要有最高分", "85" in summ, summ[:80])
    check("趋势摘要有最低分", "58" in summ, summ[:80])
    check("趋势摘要有平均分", "70.8" in summ, summ[:80])   # (85+80+58+60)/4 = 70.75 → 70.8
    check("波动值已格式化（不是 27.000000000000004）", "27.0" in summ and "27.0000" not in summ, summ[:80])
    check("波动大时给出警告提示", "波动" in note or "超售" in note, note[:80])
    check("canvas 有实际尺寸", bool(re.match(r"\d+x\d+", cvs)) and cvs != "0x0", cvs)

    for ok, msg in results:
        print("%s  %s" % ("PASS" if ok else "FAIL", msg))
    bad = [m2 for ok, m2 in results if not ok]
    print("----")
    print("合计 %d 项，失败 %d 项" % (len(results), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
