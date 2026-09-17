#!/usr/bin/env python3
"""为「首页同步状态条」生成若干注入变体页面，供无头浏览器截图。

为什么不在真跑应用里截：本机沙箱环境下 Electron 反复被杀（GPU 进程退出 + file:// 被拒），
而系统 Chrome 的无头截图稳定得多。变体页 = index.html + 一小段注入脚本，
注入脚本只做三件事：把状态条模式钉住、塞一份演示 lastSync、重绘一次。

用法:
    python tools/gen_syncbar_variants.py [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

NOW = int(time.time() * 1000)
HOUR = 3600 * 1000

DEMO_OK = {
    "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((NOW - 2 * HOUR) / 1000)),
    "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((NOW - 2 * HOUR - 41000) / 1000)),
    "source": "auto",
    "success": 12,
    "failed": 0,
    "total": 12,
    "llm_analyzed": 3,
    "duration_sec": 41,
    "errors": [],
}
DEMO_BAD = dict(DEMO_OK, success=11, failed=1, errors=[
    {"name": "demo-project", "error": "connect timeout after 3 retries"}
])

# 每个变体: 文件名 -> (模式, 数据, 视图, 说明)
VARIANTS = {
    "v0-old-home": ("collapsed", DEMO_OK, "dashboard", "改动前的首页（对照）"),
    "v1-home-collapsed-ok": ("collapsed", DEMO_OK, "dashboard", "首页默认·全部成功"),
    "v2-home-collapsed-bad": ("collapsed", DEMO_BAD, "dashboard", "首页默认·有失败"),
    "v3-home-expanded-bad": ("expanded", DEMO_BAD, "dashboard", "首页展开详情"),
    "v4-home-hidden": ("hidden", DEMO_OK, "dashboard", "首页隐藏后仅剩小入口"),
    "v5-schedules-full": ("hidden", DEMO_OK, "schedules", "定时同步页·完整形态"),
}

INJECT = """
<script>
/* 截图用注入：
   1) 先冻结页面自身所有定时器 —— 否则轮询/自动刷新会在注入之后又 render 一次，把状态覆盖掉；
   2) 钉住状态条模式、塞演示数据、指定视图；
   3) 重绘并清掉遮罩/浮层、禁用动画与过渡 —— 无头浏览器快进虚拟时间时，
      fade-in 之类动画会停在中间帧（整页发暗、糊一层），必须关掉才能拿到真实样式。 */
setTimeout(function () {
  try {
    for (var i = 1; i < 200000; i++) { clearInterval(i); clearTimeout(i); }
    var st = document.createElement('style');
    st.textContent = '*,*::before,*::after{animation:none!important;transition:none!important;backdrop-filter:none!important}';
    document.head.appendChild(st);
    document.querySelectorAll('.fade-in').forEach(function (n) { n.classList.remove('fade-in'); });
    document.querySelectorAll('.modal-overlay, .toast, #toast-el').forEach(function (n) { n.remove(); });
    getSyncBarMode = function () { return %(mode)s; };
    state.modal = null;
    state.lastSync = %(data)s;
    state.view = %(view)s;
    render();
    document.querySelectorAll('.modal-overlay, .toast, #toast-el').forEach(function (n) { n.remove(); });
    if (window.lucide) lucide.createIcons();
    document.title = 'READY-' + %(tag)s;
  } catch (e) {
    document.title = 'INJECT_ERR: ' + (e && e.message);
  }
}, 1500);
</script>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.environ.get("TEMP", "."), "padc_variants"))
    ap.add_argument("--source", default=str(ROOT / "index.html"),
                    help="用作底稿的 HTML（对照旧版时传改动前的副本）")
    ap.add_argument("--only", default="", help="只生成这些变体（逗号分隔）")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    src = Path(args.source).read_text(encoding="utf-8")
    if "</body>" not in src:
        print(f"FAIL: {args.source} 里没有 </body>，无法注入", file=sys.stderr)
        return 1

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    for name, (mode, data, view, desc) in VARIANTS.items():
        if only and name not in only:
            continue
        inject = INJECT % {
            "mode": json.dumps(mode),
            "data": json.dumps(data, ensure_ascii=False),
            "view": json.dumps(view),
            "tag": json.dumps(name),
        }
        page = src.replace("</body>", inject + "</body>", 1)
        (out / f"{name}.html").write_text(page, encoding="utf-8")
        print(f"wrote {name}.html  [{desc}]  mode={mode} view={view}")

    print(f"\nOUT={out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
