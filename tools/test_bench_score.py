# -*- coding: utf-8 -*-
"""跑分评分引擎回归测试。

覆盖那些**真踩过**的坑：
  · 反向指标（CPU 抢占率越低越好）—— 之前 0% 反而得 0 分
  · 延迟分组（国内组优先，不能混算）—— 之前混算中位数给出"4ms 满分"的假象
  · quick 档指标没提升到顶层 —— 之前磁盘/网络维度整块算不出来（可信度仅 25%）
  · YABS 空值 JSON（"ram":,）—— 不修就整个解析失败
  · 内存容量项的 IndexError

用法：python tools/test_bench_score.py
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend import bench_score as BS  # noqa: E402
from backend import bench as B  # noqa: E402

results = []


def check(name, cond, extra=""):
    results.append((bool(cond), "%s%s" % (name, (" -> " + str(extra)) if extra else "")))


# --------------------------------------------------------------------------
# 1. 打分函数基础
# --------------------------------------------------------------------------
def test_score_value():
    up = [(0, 0), (100, 100)]          # 越大越好
    check("越大越好：低于最低锚点按比例", BS.score_value(50, up) == 50.0, BS.score_value(50, up))
    check("越大越好：等于最高锚点", BS.score_value(100, up) == 100.0)
    check("越大越好：超过最高锚点封顶", BS.score_value(999, up) == 100.0)
    check("越大越好：None 返回 None", BS.score_value(None, up) is None)
    check("越大越好：非数字返回 None", BS.score_value("abc", up) is None)

    down = [(0, 100), (100, 0)]        # 越小越好
    check("越小越好：0 得满分", BS.score_value(0, down) == 100.0, BS.score_value(0, down))
    check("越小越好：超过最高锚点封底", BS.score_value(999, down) == 0.0)
    check("越小越好：中间插值", BS.score_value(50, down) == 50.0, BS.score_value(50, down))


def test_reverse_metrics():
    """CPU 抢占率与延迟都是越小越好 —— 这是真踩过的 bug。"""
    steal = BS.DEFAULT_THRESHOLDS["cpu_steal_pct"]
    check("CPU 抢占率 0% 得满分", BS.score_value(0, steal) == 100.0, BS.score_value(0, steal))
    check("CPU 抢占率 0.5% 接近满分", BS.score_value(0.5, steal) >= 90)
    check("CPU 抢占率 20% 很低", BS.score_value(20, steal) <= 35, BS.score_value(20, steal))
    check("CPU 抢占率 50% 极差", BS.score_value(50, steal) <= 10, BS.score_value(50, steal))

    lat = BS.DEFAULT_THRESHOLDS["net_latency_ms"]
    check("延迟 5ms 得满分", BS.score_value(5, lat) == 100.0, BS.score_value(5, lat))
    check("延迟 500ms 很低", BS.score_value(500, lat) <= 25, BS.score_value(500, lat))
    check("延迟 5ms 确实比 500ms 高", BS.score_value(5, lat) > BS.score_value(500, lat))


# --------------------------------------------------------------------------
# 2. quick 档指标必须提升到顶层
# --------------------------------------------------------------------------
def test_quick_promotion():
    """quick 档没有 fio 数据，磁盘/网络要靠 raw 里的 dd/curl 结果算出来。"""
    extra = {
        "disk_seq_write_mbps": 300.0,
        "disk_seq_read_mbps": 500.0,
        "net_down_mbps": 800.0,
        "net_latency_ms": 35.0,
        "cpu_steal_pct": 0.1,
        "ram_kib": 2097152,
    }
    m = BS.extract_metrics(None, extra=extra)
    check("quick: 顺序写提升到顶层", m.get("seq_write_mbps") == 300.0, m.get("seq_write_mbps"))
    check("quick: 顺序读提升到顶层", m.get("seq_read_mbps") == 500.0, m.get("seq_read_mbps"))
    check("quick: 下行带宽提升到顶层", m.get("net_down_mbps") == 800.0, m.get("net_down_mbps"))
    check("quick: 延迟提升到顶层", m.get("net_latency_ms") == 35.0, m.get("net_latency_ms"))

    scored = BS.score(m)
    disk = (scored.get("dimensions") or {}).get("disk")
    check("quick: 磁盘维度能算出来", disk is not None and disk.get("score") is not None,
          disk.get("score") if disk else None)
    net = (scored.get("dimensions") or {}).get("network")
    check("quick: 网络维度能算出来", net is not None and net.get("score") is not None,
          net.get("score") if net else None)
    check("quick: 可信度不再只有 25%", scored.get("confidence", 0) >= 60, scored.get("confidence"))
    check("quick: 总分有值", scored.get("total_score") is not None, scored.get("total_score"))


# --------------------------------------------------------------------------
# 3. 延迟分组：国内组优先
# --------------------------------------------------------------------------
def test_latency_group():
    """腾讯云首尔那台：到 1.1.1.1 anycast 只要 4ms，但到国内要 41ms。
    混算会给出「4ms 满分」的假象 —— 必须分组，且评分取国内组。"""
    m = BS.extract_metrics(None, extra={
        "net_latency_ms": 41.0,       # 已按国内组算好的代表值
        "ram_kib": 1048576,
        "cpu_steal_pct": 0.0,
    })
    scored = BS.score(m)
    net = (scored.get("dimensions") or {}).get("network")
    lat_items = [i for i in (net or {}).get("items", []) if i["key"] == "net_latency_ms"]
    check("延迟项存在", len(lat_items) == 1)
    if lat_items:
        s = lat_items[0]["score"]
        check("41ms 延迟不应得满分", s < 95, s)
        # 用**相对关系**而不是绝对阈值断言 —— 以后调锚点不会误报
        s200 = BS.score_value(200.0, BS.DEFAULT_THRESHOLDS["net_latency_ms"])
        check("41ms 应明显优于 200ms", s > s200 + 30, (s, s200))
        check("41ms 落在良好区间（>=70）", s >= 70, s)


def test_latency_group_parsing():
    """脚本输出的 key 必须能正确分组解析。"""
    kv = {
        "LAT_D_223.5.5.5_MS": "7.0",
        "LAT_D_119.29.29.29_MS": "41.0",
        "LAT_I_1.1.1.1_MS": "4.0",
        "PING_D_223.5.5.5_MS": "9.3",
    }
    # 复刻 _collect_quick 里的解析逻辑
    detail = {}
    ping = {}
    for k, v in kv.items():
        if k.startswith("LAT_") and k.endswith("_MS"):
            mid_part = k[4:-3]
            grp, host = mid_part.split("_", 1) if "_" in mid_part else ("D", mid_part)
            detail.setdefault(grp, {})[host] = float(v)
        elif k.startswith("PING_") and k.endswith("_MS"):
            mid_part = k[5:-3]
            grp, host = mid_part.split("_", 1) if "_" in mid_part else ("D", mid_part)
            ping.setdefault(grp, {})[host] = float(v)
    check("延迟分组解析：国内组", set(detail.get("D", {}).keys()) == {"223.5.5.5", "119.29.29.29"},
          detail.get("D"))
    check("延迟分组解析：国际组", set(detail.get("I", {}).keys()) == {"1.1.1.1"}, detail.get("I"))
    check("ping 分组解析", set(ping.get("D", {}).keys()) == {"223.5.5.5"}, ping.get("D"))

    def _median(values):
        vals = sorted(v for v in values if v and v < 10000)
        if not vals:
            return None, 0
        mid = len(vals) // 2
        if len(vals) % 2:
            return float(vals[mid]), len(vals)
        return (vals[mid - 1] + vals[mid]) / 2.0, len(vals)

    d_med, d_n = _median(list(detail.get("D", {}).values()) + list(ping.get("D", {}).values()))
    check("国内组中位数 = (7+9.3+41)/3 ≈ 9.3", d_med == 9.3, d_med)
    i_med, _ = _median(list(detail.get("I", {}).values()))
    check("国际组中位数 = 4.0", i_med == 4.0, i_med)
    check("国内组 ≠ 国际组（不能混算）", d_med != i_med, (d_med, i_med))


# --------------------------------------------------------------------------
# 4. YABS JSON 修复
# --------------------------------------------------------------------------
def test_fix_yabs_json():
    broken = '{"version":"v2026-10-07","cpu":{"model":"Xeon","cores":2},"mem":{"ram":1915896,"swap":,"disk":,}}'
    d = B.fix_yabs_json(broken)
    check("YABS 空值 JSON 能修复", d is not None)
    if d:
        check("修复后 ram 保留", d.get("mem", {}).get("ram") == 1915896)
        check("修复后 swap 为 null", d.get("mem", {}).get("swap") is None)
        check("修复后尾逗号已去", d.get("cpu", {}).get("cores") == 2)

    check("空输入返回 None", B.fix_yabs_json("") is None)
    check("非 JSON 返回 None", B.fix_yabs_json("not json at all") is None)

    good = '{"a":1,"b":[1,2,3]}'
    d2 = B.fix_yabs_json(good)
    check("正常 JSON 不受影响", d2 is not None and d2.get("a") == 1)


# --------------------------------------------------------------------------
# 5. 内存容量项（之前 IndexError）
# --------------------------------------------------------------------------
def test_memory_capacity():
    m = BS.extract_metrics(None, extra={"ram_kib": 2097152})
    scored = BS.score(m)
    mem = (scored.get("dimensions") or {}).get("memory")
    check("内存维度存在", mem is not None)
    items = [i for i in (mem or {}).get("items", []) if i["key"] == "mem_capacity"]
    check("内存容量项存在（不再 IndexError）", len(items) == 1, len(items))
    if items:
        check("2GB 容量有分数", items[0]["score"] is not None, items[0]["score"])
        check("2GB 显示为 MB", "MB" in items[0]["display"], items[0]["display"])


# --------------------------------------------------------------------------
# 6. 完整 YABS JSON 端到端
# --------------------------------------------------------------------------
def test_full_yabs_scoring():
    yabs = {
        "version": "v2026-10-07",
        "os": {"distro": "Ubuntu 22.04", "kernel": "5.15", "vm": "KVM"},
        "net": {"ipv4": "1.2.3.4", "ipv6": "none"},
        "cpu": {"model": "Intel Xeon Platinum", "cores": 2, "freq": "2500.0 MHz"},
        "mem": {"ram": 4194304, "swap": 2097148, "disk": 41104868},
        "ip_info": {"isp": "Test", "asn": "AS1", "city": "Seoul", "country": "South Korea"},
        "fio": [
            {"bs": "4k", "speed_r": 8192, "iops_r": 4096, "speed_w": 8192, "iops_w": 4096,
             "speed_rw": 16384, "iops_rw": 8192},
            {"bs": "64k", "speed_r": 65536, "iops_r": 1024, "speed_w": 65536, "iops_w": 1024,
             "speed_rw": 131072, "iops_rw": 2048},
            {"bs": "1m", "speed_r": 512000, "iops_r": 512, "speed_w": 512000, "iops_w": 512,
             "speed_rw": 1024000, "iops_rw": 1024},
        ],
        "iperf": [
            {"mode": "send", "provider": "Test", "loc": "Amsterdam", "send": 940.0,
             "recv": None, "latency": 38.0},
            {"mode": "recv", "provider": "Test", "loc": "Amsterdam", "send": None,
             "recv": 950.0, "latency": 38.0},
        ],
        "geekbench": [{"version": "6", "single": 1250, "multi": 2600, "url": "http://x"}],
    }
    m = BS.extract_metrics(yabs)
    check("YABS cpu 型号", m["raw"].get("cpu_model") == "Intel Xeon Platinum")
    check("YABS 核数", m["raw"].get("cpu_cores") == 2)
    check("YABS 4k IOPS 提取", m["f4"].get("iops_w") == 4096, m["f4"].get("iops_w"))
    check("YABS iperf 下行(Mbps)", abs((m.get("net_down_mbps") or 0) - 950.0) < 1,
          m.get("net_down_mbps"))
    check("YABS iperf 上行(Mbps)", abs((m.get("net_up_mbps") or 0) - 940.0) < 1,
          m.get("net_up_mbps"))
    check("YABS 延迟", m.get("net_latency_ms") == 38.0, m.get("net_latency_ms"))
    check("YABS geekbench 单核", m["geekbench"].get("single") == 1250)

    scored = BS.score(m)
    check("YABS 端到端总分", scored.get("total_score") is not None, scored.get("total_score"))
    # 稳定性维度没有数据（无 steal / 波动），权重 0.10 未覆盖 → 可信度 90% 是**正确行为**
    check("YABS 端到端可信度 90%（稳定性缺测）", scored.get("confidence") == 90,
          scored.get("confidence"))
    for dim in ("cpu", "disk", "network", "memory"):
        d = (scored.get("dimensions") or {}).get(dim)
        check("YABS 维度 %s 有分" % dim, d is not None and d.get("score") is not None,
              d.get("score") if d else None)
    check("总结文案非空", len(BS.summarize(scored)) > 5, BS.summarize(scored)[:60])


# --------------------------------------------------------------------------
# 7. 阈值可配置
# --------------------------------------------------------------------------
def test_configurable_thresholds():
    m = BS.extract_metrics(None, extra={"ram_kib": 2097152, "cpu_steal_pct": 0.0})
    base = BS.score(m)
    # 把内存容量阈值调到极苛刻，分数应下降
    custom = dict(BS.DEFAULT_THRESHOLDS)
    custom["mem_capacity"] = [(2048, 0), (4096, 10), (8192, 20)]
    strict = BS.score(m, thresholds=custom)
    b = (base.get("dimensions") or {}).get("memory", {}).get("score")
    s = (strict.get("dimensions") or {}).get("memory", {}).get("score")
    check("自定义阈值生效（更严格 → 分数更低）", s is not None and b is not None and s < b, (b, s))


def main():
    test_score_value()
    test_reverse_metrics()
    test_quick_promotion()
    test_latency_group()
    test_latency_group_parsing()
    test_fix_yabs_json()
    test_memory_capacity()
    test_full_yabs_scoring()
    test_configurable_thresholds()

    for ok, msg in results:
        print("%s  %s" % ("PASS" if ok else "FAIL", msg))
    bad = [m for ok, m in results if not ok]
    print("----")
    print("合计 %d 项，失败 %d 项" % (len(results), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
