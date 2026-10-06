# -*- coding: utf-8 -*-
"""同步提速三件套的回归测试：硬超时 / 增量跳过 / 失败熔断。

背景：一次定时同步实测 6848.9s（114 分钟），其中 92% 耗在 2 个 `api_failed`
的项目上（单项目最长 4669s），而 SSH 侧实测只要 1.4s —— 时间全浪费在
「等一个注定失败的 LLM 请求」上。本测试守这三个机制不回退。

用法：
  python tools/test_llm_speedup.py
"""
import io
import json
import os
import sys
import time
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend import sync as S  # noqa: E402
from backend import llm_analyzer as L  # noqa: E402

results = []


def check(name, cond, extra=""):
    results.append((bool(cond), f"{name}{(' -> ' + extra) if extra else ''}"))


# --------------------------------------------------------------------------
# 1) md 内容指纹
# --------------------------------------------------------------------------
def test_fingerprint():
    a = {"TODO.md": "x", "PROGRESS.md": "y"}
    b = {"PROGRESS.md": "y", "TODO.md": "x"}
    check("指纹：内容相同则一致（与 dict 顺序无关）",
          S._md_fingerprint(a) == S._md_fingerprint(b))
    check("指纹：正文变化则变化",
          S._md_fingerprint(a) != S._md_fingerprint({"TODO.md": "x", "PROGRESS.md": "y2"}))
    check("指纹：文件名变化则变化",
          S._md_fingerprint(a) != S._md_fingerprint({"TODO.md": "x", "README.md": "y"}))
    check("指纹：空输入不报错", isinstance(S._md_fingerprint({}), str))


# --------------------------------------------------------------------------
# 2) 模型签名
# --------------------------------------------------------------------------
def test_signature():
    base = {"provider": "openai", "model": "m1", "base_url": "https://a"}
    check("签名：相同配置一致", S._llm_signature(base) == S._llm_signature(dict(base)))
    check("签名：换 model 则变",
          S._llm_signature(base) != S._llm_signature({**base, "model": "m2"}))
    check("签名：换 base_url 则变",
          S._llm_signature(base) != S._llm_signature({**base, "base_url": "https://b"}))
    check("签名：换 provider 则变",
          S._llm_signature(base) != S._llm_signature({**base, "provider": "qwen"}))


# --------------------------------------------------------------------------
# 3) 熔断推进
# --------------------------------------------------------------------------
def test_breaker():
    th = S.LLM_BREAKER_THRESHOLD

    f, o = 0, False
    for _ in range(th):
        f, o = S._update_llm_breaker(f, o, "api_failed")
    check(f"熔断：连续 {th} 次 api_failed 后打开", o is True, f"consec={f}")

    # 未达阈值不应打开
    f2, o2 = 0, False
    for _ in range(max(th - 1, 1)):
        f2, o2 = S._update_llm_breaker(f2, o2, "api_failed")
    if th > 1:
        check(f"熔断：只失败 {th - 1} 次不打开", o2 is False, f"consec={f2}")

    # analyzed → 归零并撤销熔断
    f3, o3 = S._update_llm_breaker(3, True, "analyzed")
    check("熔断：analyzed 后计数归零且撤销熔断", f3 == 0 and o3 is False, f"({f3},{o3})")

    # reused 不算成功：没发请求，不能证明 provider 恢复
    f4, o4 = S._update_llm_breaker(1, False, "reused")
    check("熔断：reused 不改变计数", f4 == 1 and o4 is False, f"({f4},{o4})")

    # 其它状态不改计数
    for st in ("disabled", "missing_api_key", "no_markdown_content", "skipped_breaker"):
        f5, o5 = S._update_llm_breaker(1, False, st)
        check(f"熔断：{st} 不改变计数", f5 == 1 and o5 is False)


# --------------------------------------------------------------------------
# 4) 增量复用（md 未变 + 模型未变 → 不发请求）
# --------------------------------------------------------------------------
def test_reuse():
    md = {"TODO.md": "- [ ] a\n", "PROGRESS.md": "50%"}
    fp = S._md_fingerprint(md)
    llm = {"provider": "openai", "model": "m1", "base_url": "https://x",
           "api_key": "k", "enabled": True}
    sig = S._llm_signature(llm)

    prev = {
        "id": "p1", "name": "p1",
        "llm_analyzed": True, "llm_input_fp": fp, "llm_config_sig": sig,
        "llm_summary": "上次摘要", "llm_features": ["f1"],
        "llm_architecture": {"tech_stack": ["py"]}, "llm_routes": ["/a"],
        "llm_tech_stack": ["py"],
        "todos": {"pending": [], "completed": [{"name": "x", "done": True}],
                  "total": 1, "done_count": 1},
        "issues": {"current": ["i1"], "pending": [], "resolved": [], "total": 1},
    }
    latest = os.path.join(ROOT, "backend", "data", "_test_latest_tmp.json")
    os.makedirs(os.path.dirname(latest), exist_ok=True)
    with io.open(latest, "w", encoding="utf-8") as fh:
        json.dump({"projects": [prev]}, fh)

    orig_path, orig_cache, orig_analyze = S.OUTPUT_PATH, S._LLM_PREV_CACHE, S.analyze_project
    calls = []

    def fake_analyze(name, mdc, cfg, timeout=None):
        calls.append(name)
        return {"summary": "新摘要", "features": [], "architecture": {}, "routes": []}, None

    try:
        S.OUTPUT_PATH = Path(latest)
        S._LLM_PREV_CACHE = None
        S.analyze_project = fake_analyze

        # ① md 未变 + 模型未变 → 复用
        out = S.apply_llm_analysis("p1", {"raw_files": md}, {"llm": llm}, "p1")
        check("增量：命中缓存 → llm_status=reused",
              out.get("llm_status") == "reused", str(out.get("llm_status")))
        check("增量：命中缓存 → 一个请求都不发", len(calls) == 0, f"calls={calls}")
        check("增量：复用上次摘要", out.get("llm_summary") == "上次摘要")
        check("增量：一起带回上次 todos/issues",
              out.get("todos") is not None and out.get("issues") is not None)
        check("增量：llm_analyzed 仍为 True", out.get("llm_analyzed") is True)

        # ② md 变了 → 重新分析
        out2 = S.apply_llm_analysis(
            "p1", {"raw_files": {"TODO.md": "- [ ] a\n- [ ] b\n", "PROGRESS.md": "50%"}},
            {"llm": llm}, "p1")
        check("增量：md 变了 → 重新调用 LLM", len(calls) == 1, f"calls={calls}")
        check("增量：md 变了 → status=analyzed", out2.get("llm_status") == "analyzed")

        # ③ 换模型 → 重新分析
        calls.clear()
        out3 = S.apply_llm_analysis("p1", {"raw_files": md},
                                    {"llm": {**llm, "model": "m2"}}, "p1")
        check("增量：换模型 → 重新调用 LLM", len(calls) == 1, f"calls={calls}")
        check("增量：换模型 → 不是 reused", out3.get("llm_status") != "reused")

        # ④ 无 project_id → 不做增量（不能定位上次结果）
        calls.clear()
        out4 = S.apply_llm_analysis("p1", {"raw_files": md}, {"llm": llm}, None)
        check("增量：无 project_id 时走正常分析", len(calls) == 1, f"calls={calls}")
    finally:
        S.OUTPUT_PATH, S._LLM_PREV_CACHE, S.analyze_project = orig_path, orig_cache, orig_analyze
        if os.path.isfile(latest):
            os.remove(latest)


# --------------------------------------------------------------------------
# 5) LLM 硬超时
# --------------------------------------------------------------------------
def test_hard_timeout():
    orig = L.call_llm_api

    def slow(prompt, cfg):
        time.sleep(30)
        return {"summary": "x"}, None

    try:
        L.call_llm_api = slow
        t = time.time()
        r, e = L.analyze_project("p", {"a.md": "hi"},
                                 {"enabled": True, "api_key": "k"}, timeout=2)
        dt = time.time() - t
        check("硬超时：30s 挂起被 2s 放弃", dt < 5, f"{dt:.2f}s")
        check("硬超时：返回 None + 明确原因", r is None and "硬超时" in (e or ""), str(e))
    finally:
        L.call_llm_api = orig

    # 正常路径不受影响
    ok_calls = []

    def fast(prompt, cfg):
        ok_calls.append(1)
        return {"summary": "ok"}, None

    try:
        L.call_llm_api = fast
        r, e = L.analyze_project("p", {"a.md": "hi"},
                                 {"enabled": True, "api_key": "k"}, timeout=5)
        check("硬超时：正常返回不受影响", r and r.get("summary") == "ok" and e is None)
    finally:
        L.call_llm_api = orig


def main():
    test_fingerprint()
    test_signature()
    test_breaker()
    test_reuse()
    test_hard_timeout()

    for ok, msg in results:
        print(f"{'PASS' if ok else 'FAIL'}  {msg}")
    bad = [m for ok, m in results if not ok]
    print("----")
    print(f"合计 {len(results)} 项，失败 {len(bad)} 项")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
