# -*- coding: utf-8 -*-
"""跑分弹窗的**真实浏览器**回归测试（Chrome 无头 + 真点「开始跑分」按钮）。

为什么必须有它（2026-10-25 真实事故）：
    跑分弹窗用 `EventSource` 发 POST —— 但 **EventSource 只支持 GET**，
    浏览器直接拒绝 → 立刻 onerror → 界面显示「连接中断」。
    而当时：
      · 后端 API 测试 **全绿**（Python 用 urllib 手动发 POST 当然通）
      · 静态检查 **全绿**（JS 语法没问题）
    这个 bug **只有真浏览器点一次按钮**才暴露。

验证分两层：
  ① **前端层**：title 里记录步骤轨迹。断言拿到了后端的 `start` 事件
     （老 bug 下 EventSource 被拒，连 start 都收不到 → 立刻 CONN_ERROR）。
  ② **后端层**：断言 `benchmarks/` 目录新增了结果文件 —— 浏览器只负责发请求，
     真正干活的是后端。EventSource 被拒时后端**根本收不到请求** → 不会新增文件。

用法：
    python tools/test_bench_browser.py
    需要 Chrome；截图留在 %TEMP%/padc_bench_real/
"""
import os
import re
import sys
import io
import glob
import json
import time
import shutil
import tempfile
import threading
import subprocess
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PORT = int(os.environ.get("BENCH_TEST_PORT", "8799"))
OUT = os.path.join(tempfile.gettempdir(), "padc_bench_real")
os.makedirs(OUT, exist_ok=True)
os.environ.setdefault("DEV_CENTER_DATA_DIR", os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "Personal AI Dev Center", "data"))

results = []


def check(name, cond, extra=""):
    results.append((bool(cond), "%s%s" % (name, (" -> " + str(extra)) if extra else "")))


CHROME_CANDIDATES = [
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
]
CHROME = next((c for c in CHROME_CANDIDATES if os.path.exists(c)), None)

# 步骤**累积**到 title（只留最后一步会丢掉前面所有证据）
INJECT = r"""
<script>
(function(){
  window.__steps=[];
  function step(s){ if(window.__last===s) return; window.__last=s; window.__steps.push(s); document.title='BENCH_STEP|'+window.__steps.join('>'); }
  step('INJECTED');
  var _si=window.setInterval; window.setInterval=function(){return 0;};
  var tries=0;
  var iv=_si(function(){
    tries++;
    if(!(state.servers && state.servers.length)){
      if(tries>80){ clearInterval(iv); step('NO_SERVERS'); }
      return;
    }
    clearInterval(iv);
    step('HAVE_SERVERS');
    try{
      state.view='servers'; render();
      setTimeout(function(){
        step('OPENING');
        openServerBench(state.servers[0].id);
        setTimeout(function(){
          var btn=document.getElementById('bench-run-btn');
          if(!btn){ step('NO_BUTTON'); return; }
          step('CLICKING');
          btn.click();
          step('CLICKED');
          var t2=0;
          var iv2=_si(function(){
            t2++;
            // 结果态渲染检测：必须用**真实 DOM 查询**，不能用字符串匹配 ——
            // --dump-dom 会把 <script> 源码也序列化进 DOM，源码里的 "bench-ring"
            // 会被误判成"已渲染"（假阳性）。
            if(document.querySelectorAll('.bench-ring').length>0
               && document.querySelectorAll('.bench-dim').length>0){
              var st=(typeof _benchState!=='undefined')?_benchState:null;
              var sc=(st&&st.result&&st.result.score)||{};
              step('DONE|score='+(sc.total_score===null||sc.total_score===undefined?'NOSCORE':sc.total_score)
                   +'|dims='+document.querySelectorAll('.bench-dim').length
                   +'|rows='+document.querySelectorAll('.bench-items tbody tr').length);
              clearInterval(iv2);
              return;
            }
            var msgEl=document.getElementById('bench-prog-msg');
            var msg=msgEl?msgEl.textContent:'';
            if(msg && (msg.indexOf('连接失败')>=0 || msg.indexOf('连接中断')>=0
                       || msg.indexOf('出错')>=0)){
              step('CONN_ERROR|'+msg); clearInterval(iv2);
            } else if(msg && msg.indexOf('开始跑分')>=0){
              step('GOT_START');
            } else if(msg){
              step('PROG|'+msg);
            }
            if(t2>600){ clearInterval(iv2); }
          }, 300);
        }, 400);
      }, 250);
    }catch(e){ step('EXCEPTION|'+String(e)); }
  }, 200);
})();
</script>
"""


def main():
    if not CHROME:
        print("跳过：找不到 Chrome")
        return 0

    from backend import app as APP
    import uvicorn
    cfg = uvicorn.Config(APP.app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    base = "http://127.0.0.1:%d" % PORT
    for _ in range(60):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen(base + "/openapi.json", timeout=3):
                break
        except Exception:
            continue
    else:
        print("!! 后端没起来")
        return 1

    src = io.open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    v = src.replace("</head>", INJECT + "</head>")
    v = v.replace("const API_BASE = 'http://localhost:8765';",
                  "const API_BASE = '%s';" % base)
    vpath = os.path.join(OUT, "variant_real.html")
    io.open(vpath, "w", encoding="utf-8").write(v)

    # 后端侧基线
    bdir = os.path.join(os.environ["DEV_CENTER_DATA_DIR"], "benchmarks")

    def bench_files():
        return set(glob.glob(os.path.join(bdir, "*.json"))) if os.path.isdir(bdir) else set()

    before = bench_files()

    # 虚拟时间给足（60 秒）：SSE 长连接期间虚拟时间会暂停，
    # 所以这个预算实际能覆盖真实跑分的 15 秒。
    flags = ['--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
             '--disable-extensions', '--force-device-scale-factor=1.5',
             '--window-size=1680,1150', '--virtual-time-budget=60000']

    prof = tempfile.mkdtemp(prefix="bbench_")
    try:
        r = subprocess.run([CHROME] + flags + ['--user-data-dir=' + prof, '--dump-dom',
                           'file:///' + vpath.replace("\\", "/")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300)
        dom = r.stdout or ""
    except subprocess.TimeoutExpired as e:
        out = e.stdout
        dom = out.decode("utf-8", "replace") if isinstance(out, bytes) else (out or "")
    finally:
        shutil.rmtree(prof, ignore_errors=True)

    m = re.search(r"<title>([^<]*)</title>", dom)
    title = m.group(1) if m else ""
    print("  页面 title: %s" % title[:200])

    check("注入脚本执行了", title.startswith("BENCH_STEP|"), title[:80])
    check("拿到了服务器列表", "HAVE_SERVERS" in title, title[:150])
    check("打开了跑分弹窗",
          any(k in title for k in ("OPENING", "CLICKING", "CLICKED", "GOT_START", "DONE")),
          title[:150])
    check("真的点了「开始跑分」", "CLICKED" in title, title[:150])
    check("前端没有报连接错误", "CONN_ERROR" not in title and "EXCEPTION" not in title, title[:150])
    # 拿到后端 start 事件 → SSE 流通了（EventSource 老 bug 的直接反证）
    check("前端收到了后端的 start 事件（SSE 流通了）",
          "GOT_START" in title or "开始跑分" in title, title[:180])
    # ---- 后端侧：让 Chrome **保持存活**跑完整个流程 ----
    # ⚠️ 第 1 轮的 Chrome（dump-dom 模式）到点就退出会**掐断 SSE 连接**，
    #    后端生成器随之中断 → 跑分不落盘。所以这里用 `--remote-debugging-port` 模式
    #    （不截图、不 dump、不设 virtual-time）让 Chrome 一直活着。
    keep_flags = ['--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
                  '--disable-extensions', '--window-size=1680,1150',
                  '--remote-debugging-port=9222']
    prof2 = tempfile.mkdtemp(prefix="bbench2_")
    proc = subprocess.Popen([CHROME] + keep_flags + ['--user-data-dir=' + prof2,
                             'file:///' + vpath.replace("\\", "/")],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("  等后端跑完并落盘（Chrome 保持存活，最多 150 秒）...", flush=True)
    try:
        deadline = time.time() + 150
        after = bench_files()
        while time.time() < deadline:
            time.sleep(3)
            after = bench_files()
            if len(after) > len(before):
                break
            if proc.poll() is not None:
                print("    !! Chrome 意外退出", flush=True)
                break
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(prof2, ignore_errors=True)

    after = bench_files()
    check("后端真的收到请求并完成了跑分（新增了结果文件）",
          len(after) > len(before), "before=%d after=%d" % (len(before), len(after)))

    # 落盘文件里必须真有评分结果（这才是结果态的数据源）。
    # 不查第 1 轮的 title：那一轮 Chrome 在跑分完成前就 dump 了，抓不到结果态。
    new_files = sorted(after - before, key=os.path.getmtime)
    if new_files:
        try:
            payload = io.open(new_files[-1], encoding="utf-8").read()
            j = json.loads(payload)
            raw = j.get("raw") or {}
            check("落盘结果含 CPU 型号", bool(raw.get("cpu_model")), raw.get("cpu_model"))
            check("落盘结果含磁盘速度", raw.get("disk_seq_write_mbps") is not None,
                  raw.get("disk_seq_write_mbps"))
            check("落盘结果含延迟分组", bool(j.get("latency_groups")), j.get("latency_groups"))
        except Exception as e:
            check("落盘结果可解析", False, "%s: %s" % (type(e).__name__, e))

    # 源码里不该再有真的 EventSource 调用（只允许出现在注释里）
    src_calls = [l for l in src.split("\n")
                 if "new EventSource" in l and not l.strip().startswith("//")]
    check("源码里没有真的 EventSource 调用", not src_calls, src_calls[:1])

    for ok, msg in results:
        print("%s  %s" % ("PASS" if ok else "FAIL", msg))
    bad = [m2 for ok, m2 in results if not ok]
    print("----")
    print("合计 %d 项，失败 %d 项" % (len(results), len(bad)))
    print("截图目录: %s" % OUT)

    server.should_exit = True
    time.sleep(1)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
