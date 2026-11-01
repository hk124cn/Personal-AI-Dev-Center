# -*- coding: utf-8 -*-
"""LLM 分析失败时的降级行为回归测试。

背景（2026-11-01 真实发现的缺陷）：
    `apply_llm_analysis` 在 LLM 调用失败时走 `api_failed` 分支，
    直接把 `llm_features` / `llm_summary` / `llm_tech_stack` 置空
    → 写进 `latest.json` → **首页的 AI 分析整块消失**。

    触发条件很常见：远端 md 一改（指纹变了）+ provider 超时/限流 → 必现。
    当时侥幸躲过，是因为指纹恰好没变走了「复用」路径。

正确行为：
    · 有历史分析结果 → 沿用旧数据，status 标 `stale`（过期但可用）
    · 没有任何历史     → status 标 `api_failed`（确实没数据）

另外测：
    · 超时错误消息里的秒数必须是**动态**的（原来硬编码 "(30s)"，实际是 120s）
    · 超时要走退避重试

用法：
    python tools/test_llm_fallback.py
"""
import io
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

results = []


def check(name, cond, extra=""):
    results.append((bool(cond), "%s%s" % (name, (" -> " + str(extra)) if extra else "")))


def main():
    from backend import sync as S
    from backend import llm_analyzer as L

    # ---------- 1) 源码层面：失败分支必须沿用旧数据 ----------
    src = io.open(os.path.join(ROOT, "backend", "sync.py"), encoding="utf-8").read()
    idx = src.find('remote_data["llm_status"] = "api_failed"')
    check("存在 api_failed 分支", idx > 0)
    branch = src[max(0, idx - 1500):idx + 200]
    check("失败分支会沿用上次的 llm_features", 'prev.get("llm_features"' in branch,
          "沿用 features" if 'prev.get("llm_features"' in branch else "没沿用")
    check("失败分支会沿用上次的 llm_summary", 'prev.get("llm_summary"' in branch)
    check("失败分支会沿用上次的 llm_tech_stack", 'prev.get("llm_tech_stack"' in branch)
    check("沿用时把状态标成 stale", 'remote_data["llm_status"] = "stale"' in src)
    check("stale 时 llm_analyzed 仍为 True（数据可用）",
          'remote_data["llm_analyzed"] = True' in branch)

    # ---------- 2) 超时错误消息必须动态 ----------
    lsrc = io.open(os.path.join(ROOT, "backend", "llm_analyzer.py"), encoding="utf-8").read()
    check("没有硬编码的 \"请求超时 (30s)\"", '"请求超时 (30s)"' not in lsrc)
    check("超时消息用实际超时值", 'f"请求超时 ({LLM_HTTP_TIMEOUT}s)"' in lsrc)
    check("实际超时配置是 120s（不是 30s）", L.LLM_HTTP_TIMEOUT == 120, L.LLM_HTTP_TIMEOUT)

    # ---------- 3) 超时退避重试 ----------
    check("有超时重试次数配置", hasattr(L, "LLM_TIMEOUT_RETRIES"),
          getattr(L, "LLM_TIMEOUT_RETRIES", "无"))
    check("有超时退避时长配置", hasattr(L, "LLM_TIMEOUT_BACKOFF"),
          getattr(L, "LLM_TIMEOUT_BACKOFF", "无"))
    check("有 _is_timeout_err 判定", hasattr(L, "_is_timeout_err"))
    check("_is_timeout_err 能认出超时", L._is_timeout_err("请求超时 (120s)") is True)
    check("_is_timeout_err 不误判 429", L._is_timeout_err("HTTP 429 rate limit") is False)
    check("_is_timeout_err 不误判鉴权失败",
          L._is_timeout_err("HTTP 401 invalid key") is False)

    # ---------- 4) 超时退避重试的行为（用假 fn，不打真网络）----------
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            return None, "请求超时 (120s)"
        return {"summary": "ok"}, None

    old_backoff = L.LLM_TIMEOUT_BACKOFF
    L.LLM_TIMEOUT_BACKOFF = 0  # 测试里不等
    try:
        r, e = L._call_with_timeout_retry(flaky)
    finally:
        L.LLM_TIMEOUT_BACKOFF = old_backoff
    check("超时后会重试", calls["n"] == 2, "调用 %d 次" % calls["n"])
    check("重试成功后拿到结果", r == {"summary": "ok"}, r)

    # 一直超时 → 不无限重试
    calls2 = {"n": 0}

    def always_timeout():
        calls2["n"] += 1
        return None, "请求超时 (120s)"

    L.LLM_TIMEOUT_BACKOFF = 0
    try:
        r2, e2 = L._call_with_timeout_retry(always_timeout)
    finally:
        L.LLM_TIMEOUT_BACKOFF = old_backoff
    check("一直超时时重试次数有上限", calls2["n"] == L.LLM_TIMEOUT_RETRIES,
          "调用 %d 次 / 上限 %d" % (calls2["n"], L.LLM_TIMEOUT_RETRIES))
    check("一直超时时返回错误原因", bool(e2), e2)

    # 非超时错误不重试
    calls3 = {"n": 0}

    def auth_fail():
        calls3["n"] += 1
        return None, "HTTP 401 invalid key"

    r3, e3 = L._call_with_timeout_retry(auth_fail)
    check("非超时错误不重试", calls3["n"] == 1, "调用 %d 次" % calls3["n"])

    # ---------- 5) 大 prompt 自适应超时 ----------
    check("有大 prompt 阈值配置", hasattr(L, "LLM_BIG_PROMPT_CHARS"),
          getattr(L, "LLM_BIG_PROMPT_CHARS", "无"))
    check("大 prompt 超时比默认宽", L.LLM_BIG_PROMPT_TIMEOUT > L.LLM_HARD_TIMEOUT,
          "%d > %d" % (L.LLM_BIG_PROMPT_TIMEOUT, L.LLM_HARD_TIMEOUT))

    for ok, msg in results:
        print("%s  %s" % ("PASS" if ok else "FAIL", msg))
    bad = [m2 for ok, m2 in results if not ok]
    print("----")
    print("合计 %d 项，失败 %d 项" % (len(results), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
