# -*- coding: utf-8 -*-
"""下载浮窗 stats 显示的渲染回归（真浏览器 + 真实 SSE 事件形状）。

背景（2026-10-31）：用户截图里浮窗显示 `15000 sync_files_done` ——
**裸 i18n key**。原因是 `sync_files_done` 这个 key 在 I18N 表里根本不存在，
而 `t()` 在缺 key 时**静默返回 key 本身**，不报任何错。

这个测试直接用真实的 SSE 事件 payload 喂给前端的渲染逻辑，断言：
  1. stats 里不出现任何裸 i18n key
  2. 「已下载」带上总数（downloaded / to_transfer）
  3. 「已跳过」正常出现
  4. 失败项数正常出现

用法：
    python tools/test_sync_stats.py
"""
import os
import re
import sys
import io
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

    # 抽出 runSyncWithProgress 里的 stats 渲染片段 —— 它是前端唯一渲染 stats 的地方。
    # 这里不跑完整下载（要 3GB），而是把片段单独喂真实事件，断言渲染结果。
    INJECT = r"""
<script>
// ⚠️ 必须等 load 事件：
//   ① 注入在 </head> 前执行，此时 document.body 还是 null → appendChild 抛错
//   ② 主脚本里的 `const I18N` 此刻处于 **TDZ（暂时性死区）**，
//      `typeof I18N` 对 TDZ 变量会抛 ReferenceError（typeof 只对 undeclared 安全）
window.addEventListener('load', function(){
  window.__OUT = {};
  function setStats(html){ window.__OUT.stats = html; }
  function setPhase(p, done){ window.__OUT.phase = p; }
  function setBar(p){ }
  function addLog(l){ (window.__OUT.logs = window.__OUT.logs||[]).push(l); }
  function showDone(a,b){ }
  function elapsedStr(){ return '1m 4s'; }
  function formatBytes(b){ return (b/1048576).toFixed(1)+' MB'; }
  function speedStr(b){ return '3.2 MB/s'; }
  function t(key){
    const I = (typeof I18N!=='undefined')?I18N:{};
    const v=(I[key]&&I[key]['zh'])||(I[key]&&I[key]['en']);
    if(v===undefined){ if(typeof console!=='undefined') console.warn('[i18n] 缺少文案 key:', key); return key; }
    return v;
  }
  // 复制前端真实的 batch_done 分支逻辑（保持同步，改动要一起改）
  function renderBatchDone(event){
    var bOk = event.batch_ok;
    addLog((bOk ? '\u2713 ' : '\u2717 ') + t('sync_batch_n') + ' ' + (event.batch || 0) + '/' + (event.batches || 1)
      + (bOk ? ' ' + t('sync_batch_ok') : ' ' + t('sync_batch_fail'))
      + ' \u00b7 ' + (event.downloaded || 0) + '/' + (event.to_transfer || 0) + ' ' + t('sync_files_unit')
      + ' \u00b7 ' + formatBytes(event.bytes || 0)
      + (event.skipped ? ' \u00b7 ' + t('sync_files_skipped') + ' ' + event.skipped : '')
      + (event.failed ? ' \u00b7 ' + t('ls_failed') + ' ' + event.failed : ''));
    setStats([
      '<span class="dot" style="background:var(--accent)"></span>'
        + t('sync_files_done') + ' ' + (event.downloaded || 0)
        + (event.to_transfer ? ' / ' + event.to_transfer : '') + ' ' + t('sync_files_unit'),
      '<span class="dot" style="background:var(--text-tertiary)"></span>' + (event.skipped || 0) + ' ' + t('sync_files_skipped'),
      (event.failed ? '<span class="dot" style="background:var(--amber)"></span>' + event.failed + ' ' + t('ls_failed') : '')
    ].filter(Boolean));
  }
  // 用户截图里的真实事件形状
  renderBatchDone({batch:25, batches:34, batch_ok:true, downloaded:15000, to_transfer:20147, bytes:369000000, skipped:55591, failed:0});
  // ⚠️ --dump-dom 只序列化 DOM，**JS 变量取不到** —— 必须写进 DOM 才能被 dump 出来
  var holder = document.createElement('div');
  holder.id = '__result';
  holder.setAttribute('data-x', '1');
  holder.textContent = JSON.stringify({stats: window.__OUT.stats || '', logs: window.__OUT.logs || []});
  document.body.appendChild(holder);
});
</script>
"""
    v = src.replace("</head>", INJECT + "</head>")
    vpath = os.path.join(tempfile.gettempdir(), "padc_stats_check.html")
    io.open(vpath, "w", encoding="utf-8").write(v)

    prof = tempfile.mkdtemp(prefix="bstat_")
    try:
        # ⚠️ 必须加 --virtual-time-budget：变体页会**一直轮询后端**，
        #    不强制结束的话 --dump-dom 永远等不到"页面稳定"（实测卡死 120s 超时）。
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                            "--hide-scrollbars", "--disable-extensions",
                            "--virtual-time-budget=8000",
                            "--user-data-dir=" + prof, "--dump-dom",
                            "file:///" + vpath.replace("\\", "/")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        dom = r.stdout or ""
    finally:
        shutil.rmtree(prof, ignore_errors=True)

    import html as _html
    m = re.search(r'<div id="__result"[^>]*>(.*?)</div>', dom, re.S)
    stats_html = ""
    logs_html = ""
    if m:
        blob = _html.unescape(m.group(1))
        try:
            data = json.loads(blob)
            raw_stats = data.get("stats", "")
            stats_html = raw_stats if isinstance(raw_stats, str) else " ".join(raw_stats or [])
            logs_html = " ".join(data.get("logs") or [])
        except Exception as e:
            print("  (解析失败: %s) blob=%r" % (e, blob[:200]))
    else:
        print("  !! 没找到 __result 容器")

    print("  stats : %s" % stats_html[:220])
    print("  log   : %s" % logs_html[:220])
    print()

    # ---- 断言 ----
    # 1) 不能出现裸 i18n key
    bare = re.findall(r"(?<![A-Za-z0-9_])(?:sync_[a-z_]+|ls_[a-z_]+|qa_[a-z_]+|bench_[a-z_]+)", stats_html + " " + logs_html)
    check("stats / 日志里没有裸 i18n key", not bare, bare[:5])

    # 2) 已下载要带总数
    check("显示「已下载」", "已下载" in stats_html, stats_html[:120])
    check("已下载带总数（15000 / 20147）",
          "15000" in stats_html and "20147" in stats_html, stats_html[:120])

    # 3) 已跳过
    check("显示「已跳过 55591」", "55591" in stats_html and "已跳过" in stats_html, stats_html[:120])

    # 4) 日志行里的批次信息也要正常
    check("日志行显示批次 25/34", "25" in logs_html and "34" in logs_html, logs_html[:120])
    check("日志行没有裸 key", "sync_batch_n" not in logs_html and "sync_files_unit" not in logs_html)

    for ok, msg in results:
        print("%s  %s" % ("PASS" if ok else "FAIL", msg))
    bad = [m2 for ok, m2 in results if not ok]
    print("----")
    print("合计 %d 项，失败 %d 项" % (len(results), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
