# -*- coding: utf-8 -*-
"""VPS 跑分评分引擎：把原始跑分数值映射成 0-100 分与总分。

设计取舍（重要，改之前先读）：
  · **VPSBenchmarks 的分数是相对分**——同一指标在他们全库里的百分位（top 18% = A，
    bottom 18% = F）。我们没有那个库，所以这里做的是**绝对分**：靠行业实测阈值校准。
    因此本模块的分数只能跟「同档位的 VPS」横向比，不能直接跟 VPSBenchmarks 的 0-100 总分比。
  · **阈值全部可配置**（config.json 的 `bench_thresholds`）。默认值是行业经验值，
    真实机器跑一轮后应当校准 —— 宁可让用户改阈值，也别把拍脑袋的分数当成真理。
  · 主指标用 **Geekbench 6 单核分**（跨机器标准化好，VPSBenchmarks 也用它），
    sysbench 只作辅助参考。原因：sysbench 的 events/s 强依赖 `--cpu-max-prime`
    参数（10000 vs 20000 能差 2 倍），不同来源的"参考值"根本不在一个量纲上，
    拿来当主指标会得出误导性结论。

维度与权重参考 VPSBenchmarks v2026.04 官方指标权重表，做了适配：
    CPU 30% · 磁盘 25% · 网络 20% · 内存 15% · 稳定性 10%
"""
import math

# ---------------------------------------------------------------------------
# 默认阈值锚点：(原始值, 得分)
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLDS = {
    # --- CPU ---
    "geekbench_single": [(400, 5), (700, 20), (1000, 40), (1200, 55),
                         (1500, 70), (1800, 85), (2100, 95), (2500, 100)],
    "geekbench_multi": [(800, 5), (1600, 20), (2600, 40), (3600, 55),
                        (5000, 70), (6500, 85), (8500, 95), (12000, 100)],
    "sysbench_cpu_single": [(300, 5), (600, 25), (900, 45), (1300, 62),
                             (1800, 78), (2400, 90), (3200, 100)],
    # --- 磁盘 ---
    "fio_4k_rand_write_iops": [(100, 5), (500, 20), (1500, 35), (5000, 55),
                               (12000, 72), (25000, 85), (50000, 95), (90000, 100)],
    "fio_4k_rand_read_iops": [(200, 5), (1000, 20), (3000, 38), (8000, 58),
                              (20000, 75), (40000, 88), (80000, 100)],
    "disk_seq_write_mbps": [(30, 5), (80, 20), (150, 40), (300, 58),
                             (600, 75), (1000, 88), (2000, 100)],
    "disk_seq_read_mbps": [(40, 5), (120, 20), (250, 42), (500, 60),
                            (900, 76), (1500, 90), (3000, 100)],
    "disk_mixed_64k_mbps": [(40, 5), (100, 25), (250, 48), (500, 65),
                            (900, 80), (1500, 92), (2500, 100)],
    # 内存容量（MB）：够不够跑的直觉分
    "mem_capacity": [(512, 10), (1024, 30), (2048, 55), (4096, 72),
                     (8192, 88), (16384, 96), (32768, 100)],
    # 多核线性度（%）：多核分 / (单核分 × 核数)，100% = 完全线性
    "multicore_efficiency": [(40, 10), (60, 30), (80, 55), (95, 78),
                              (105, 92), (120, 100)],
    # --- 网络 ---
    "net_down_mbps": [(50, 10), (200, 30), (500, 48), (1000, 63),
                      (3000, 78), (10000, 92), (25000, 100)],
    "net_up_mbps": [(30, 10), (120, 30), (300, 48), (600, 63),
                    (2000, 78), (5000, 90), (10000, 100)],
    # ⚠️ 延迟是**越小越好**：锚点首项得分(100) > 末项得分(0)，score_value 会自动走反向插值
    # 阈值按「国内访问 VPS 的真实体感」定：
    #   <25ms 优秀 · 25-45ms 良好 · 45-70ms 一般 · 70-100ms 偏慢 · >100ms 差
    "net_latency_ms": [(10, 100), (25, 92), (45, 80), (70, 65),
                       (100, 50), (150, 35), (250, 20), (400, 8), (600, 0)],
    # --- 内存 ---
    "mem_bandwidth_mbps": [(500, 10), (1500, 28), (3000, 45), (6000, 62),
                           (10000, 76), (15000, 88), (25000, 100)],
    # --- 稳定性 ---
    # ⚠️ CPU 抢占率也是越小越好（0% = 完全没被抢 = 满分）
    "cpu_steal_pct": [(0.2, 100), (1, 95), (3, 85), (6, 68),
                      (12, 45), (25, 22), (45, 5)],
    # 跑分波动（%）：同样越小越好
    "score_cv_pct": [(2, 100), (5, 92), (10, 78), (20, 58),
                     (35, 35), (60, 12)],
}

# 维度权重（合计 1.0）
DIMENSION_WEIGHTS = {
    "cpu": 0.30,
    "disk": 0.25,
    "network": 0.20,
    "memory": 0.15,
    "stability": 0.10,
}

DIMENSION_LABELS = {
    "cpu": "CPU 算力",
    "disk": "磁盘 IO",
    "network": "网络",
    "memory": "内存",
    "stability": "稳定性",
}


# ---------------------------------------------------------------------------
# 基础打分
# ---------------------------------------------------------------------------
def score_value(value, anchors):
    """按锚点表给一个原始值打分（0-100）。取锚点间线性插值。

    anchors: [(原始值, 得分), ...]，按原始值升序。

    ⚠️ **自动识别反向指标**：如果 anchors 的首项得分 > 末项得分，说明这是
    「越小越好」的指标（如 CPU 抢占率、延迟），走反向插值：
    低于最小锚点 → 满分，高于最大锚点 → 最低分。
    （之前没处理，CPU 抢占率 0% 反而得 0 分，把「完全没被抢」判成最差。）
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not anchors:
        return None
    anchors = sorted(anchors, key=lambda x: x[0])
    lo_v, lo_s = anchors[0]
    hi_v, hi_s = anchors[-1]
    reverse = lo_s > hi_s

    if reverse:
        # 越小越好
        if v <= lo_v:
            return float(lo_s)
        if v >= hi_v:
            return float(hi_s)
        for i in range(len(anchors) - 1):
            v0, s0 = anchors[i]
            v1, s1 = anchors[i + 1]
            if v0 <= v <= v1:
                if v1 == v0:
                    return float(s1)
                ratio = (v - v0) / (v1 - v0)
                return round(s0 + ratio * (s1 - s0), 1)
        return float(hi_s)

    # 越大越好
    if v <= lo_v:
        # 低于最低锚点：按比例给一个很低的分（最低锚点得分 * 比例）
        if lo_s <= 0:
            return 0.0
        ratio = max(0.0, v / lo_v) if lo_v > 0 else 0.0
        return round(lo_s * ratio, 1)
    if v >= hi_v:
        return float(hi_s)
    for i in range(len(anchors) - 1):
        v0, s0 = anchors[i]
        v1, s1 = anchors[i + 1]
        if v0 <= v <= v1:
            if v1 == v0:
                return float(s1)
            ratio = (v - v0) / (v1 - v0)
            return round(s0 + ratio * (s1 - s0), 1)
    return float(hi_s)


def _get(thresholds, key, default=None):
    if thresholds and key in thresholds:
        return thresholds[key]
    return DEFAULT_THRESHOLDS.get(key, default)


def _fmt(value, unit=""):
    if value is None:
        return "—"
    if isinstance(value, float):
        if value >= 100:
            return "%d%s" % (round(value), unit)
        if value >= 10:
            return "%.1f%s" % (value, unit)
        return "%.2f%s" % (value, unit)
    return "%s%s" % (value, unit)


def grade_word(score):
    """把 0-100 分转成人话评价。"""
    if score is None:
        return "未知"
    if score >= 90:
        return "优秀"
    if score >= 78:
        return "良好"
    if score >= 62:
        return "够用"
    if score >= 45:
        return "偏低"
    if score >= 28:
        return "较差"
    return "很差"


# ---------------------------------------------------------------------------
# 从 YABS JSON 提取指标
# ---------------------------------------------------------------------------
def extract_metrics(yabs, extra=None):
    """从 YABS 的 JSON 结果里抽出统一格式的指标字典。

    返回 {"raw": {...}, "fio": {...}, "iperf": {...}, "geekbench": {...}}
    """
    yabs = yabs or {}
    extra = extra or {}

    raw = {
        "cpu_model": (yabs.get("cpu") or {}).get("model"),
        "cpu_cores": (yabs.get("cpu") or {}).get("cores"),
        "cpu_freq_mhz": None,
        "ram_kib": (yabs.get("mem") or {}).get("ram"),
        "swap_kib": (yabs.get("mem") or {}).get("swap"),
        "disk_kb": (yabs.get("mem") or {}).get("disk"),
        "os_distro": (yabs.get("os") or {}).get("distro"),
        "kernel": (yabs.get("os") or {}).get("kernel"),
        "vm_type": (yabs.get("os") or {}).get("vm"),
        "uptime": (yabs.get("os") or {}).get("uptime"),
        "isp": (yabs.get("ip_info") or {}).get("isp"),
        "asn": (yabs.get("ip_info") or {}).get("asn"),
        "org": (yabs.get("ip_info") or {}).get("org"),
        "city": (yabs.get("ip_info") or {}).get("city"),
        "country": (yabs.get("ip_info") or {}).get("country"),
        "ipv4": (yabs.get("net") or {}).get("ipv4"),
        "ipv6": (yabs.get("net") or {}).get("ipv6"),
        "yabs_version": yabs.get("version"),
    }
    freq = (yabs.get("cpu") or {}).get("freq") or ""
    try:
        raw["cpu_freq_mhz"] = float(str(freq).split()[0])
    except (ValueError, IndexError):
        pass

    # --- fio：按 block size 归一 ---
    fio = {}
    for entry in (yabs.get("fio") or []):
        bs = str(entry.get("bs") or "").lower()
        if not bs:
            continue
        fio[bs] = {
            "speed_r_mbps": (entry.get("speed_r") or 0) / 1024.0,
            "speed_w_mbps": (entry.get("speed_w") or 0) / 1024.0,
            "speed_rw_mbps": (entry.get("speed_rw") or 0) / 1024.0,
            "iops_r": entry.get("iops_r"),
            "iops_w": entry.get("iops_w"),
            "iops_rw": entry.get("iops_rw"),
        }
    # 64k 是 YABS 里最接近"日常混合读写"的一档；512k/1m 视为顺序读写
    f4 = fio.get("4k") or {}
    f64 = fio.get("64k") or {}
    fseq = fio.get("1m") or fio.get("512k") or {}

    # --- iperf3：取所有 location 里最快的那个（代表"这机能跑多快"） ---
    # ⚠️ YABS JSON 里 iperf 的 recv/send 单位就是 **Mbit/s**（不是 bytes/s），
    #    之前误除了一次 1000，导致 950 Mbps 被算成 0.95 Mbps。
    net_down = net_up = None
    net_latency = None
    best_down_loc = best_up_loc = ""
    for entry in (yabs.get("iperf") or []):
        recv = entry.get("recv")
        send = entry.get("send")
        lat = entry.get("latency")
        try:
            recv = float(recv) if recv is not None else None
        except (TypeError, ValueError):
            recv = None
        try:
            send = float(send) if send is not None else None
        except (TypeError, ValueError):
            send = None
        try:
            lat = float(lat) if lat is not None else None
        except (TypeError, ValueError):
            lat = None
        if recv is not None and (net_down is None or recv > net_down):
            net_down = recv
            best_down_loc = entry.get("loc") or ""
        if send is not None and (net_up is None or send > net_up):
            net_up = send
            best_up_loc = entry.get("loc") or ""
        if lat is not None and (net_latency is None or lat < net_latency):
            net_latency = lat

    # --- geekbench ---
    gb = {}
    for entry in (yabs.get("geekbench") or []):
        try:
            gb["single"] = entry.get("single")
            gb["multi"] = entry.get("multi")
        except (AttributeError, TypeError):
            pass
        gb["url"] = entry.get("url")
        gb["version"] = entry.get("version")
        break

    # --- 轻量档补测的指标（quick profile 走这里） ---
    for k, v in (extra or {}).items():
        raw[k] = v

    # ⚠️ 关键：把 quick 档落在 raw 里的指标**提升到顶层**，
    # 否则评分引擎读顶层字段时拿不到，磁盘/网络维度会整块算不出来
    # （表现为「可信度只有 25%」，总分虚高但其实没测到）。
    promoted = {}
    if raw.get("disk_seq_write_mbps") is not None:
        promoted["seq_write_mbps"] = raw["disk_seq_write_mbps"]
    if raw.get("disk_seq_read_mbps") is not None:
        promoted["seq_read_mbps"] = raw["disk_seq_read_mbps"]
    if raw.get("net_down_mbps") is not None:
        promoted["net_down_mbps"] = raw["net_down_mbps"]
    if raw.get("net_up_mbps") is not None:
        promoted["net_up_mbps"] = raw["net_up_mbps"]
    if raw.get("net_latency_ms") is not None:
        promoted["net_latency_ms"] = raw["net_latency_ms"]

    metrics = {
        "raw": raw,
        "fio": fio,
        "f4": f4,
        "f64": f64,
        "fseq": fseq,
        "net_down_mbps": net_down if net_down is not None else promoted.get("net_down_mbps"),
        "net_up_mbps": net_up if net_up is not None else promoted.get("net_up_mbps"),
        "net_latency_ms": net_latency if net_latency is not None else promoted.get("net_latency_ms"),
        "seq_write_mbps": promoted.get("seq_write_mbps"),
        "seq_read_mbps": promoted.get("seq_read_mbps"),
        "best_down_loc": best_down_loc,
        "best_up_loc": best_up_loc,
        "geekbench": gb,
    }
    return metrics


# ---------------------------------------------------------------------------
# 评分
# ---------------------------------------------------------------------------
def score(metrics, thresholds=None):
    """给一组指标打分。返回完整评分结果 dict。"""
    th = thresholds or {}
    m = metrics or {}
    raw = m.get("raw") or {}
    f4 = m.get("f4") or {}
    f64 = m.get("f64") or {}
    fseq = m.get("fseq") or {}

    items = []          # 每个子项的明细
    dims = {}           # 维度 -> {score, items}

    def add(dim, key, label, value, unit, display=None, weight_in_dim=1.0):
        anchors = _get(th, key)
        sc = score_value(value, anchors) if anchors else None
        if sc is None:
            return None
        rec = {
            "key": key,
            "label": label,
            "value": value,
            "display": display if display is not None else _fmt(value, unit),
            "score": sc,
            "grade": grade_word(sc),
            "weight": weight_in_dim,
        }
        items.append(rec)
        dims.setdefault(dim, {"items": [], "weight_sum": 0.0})
        dims[dim]["items"].append(rec)
        dims[dim]["weight_sum"] += weight_in_dim
        return rec

    # ---- CPU ----
    gb = m.get("geekbench") or {}
    add("cpu", "geekbench_single", "Geekbench 6 单核", gb.get("single"), "")
    add("cpu", "geekbench_multi", "Geekbench 6 多核", gb.get("multi"), "")
    add("cpu", "sysbench_cpu_single", "sysbench 单核", raw.get("sysbench_single"), " events/s")
    # 多核扩展性：多核分 / (单核分 × 核数)，超线程调度好会 >1
    try:
        cores = float(raw.get("cpu_cores") or 0)
        if cores > 0 and gb.get("single") and gb.get("multi"):
            ratio = float(gb["multi"]) / (float(gb["single"]) * cores)
            raw["multicore_efficiency"] = round(ratio, 3)
            add("cpu", "multicore_efficiency", "多核线性度",
                min(ratio, 1.2) * 100, "%",
                display="%d%%（%.2f×核数）" % (round(min(ratio, 1.2) * 100), ratio),
                weight_in_dim=0.5)
    except (TypeError, ValueError, ZeroDivisionError):
        pass

    # ---- 磁盘 ----
    # fio 的 4k 随机 IOPS 只有 standard/full 档有；quick 档用 dd 的顺序速度顶上
    add("disk", "fio_4k_rand_write_iops", "4K 随机写 IOPS", f4.get("iops_w"), "")
    add("disk", "fio_4k_rand_read_iops", "4K 随机读 IOPS", f4.get("iops_r"), "")
    add("disk", "disk_seq_write_mbps", "顺序写",
        fseq.get("speed_w_mbps") if fseq.get("speed_w_mbps") is not None else m.get("seq_write_mbps"),
        " MB/s")
    add("disk", "disk_seq_read_mbps", "顺序读",
        fseq.get("speed_r_mbps") if fseq.get("speed_r_mbps") is not None else m.get("seq_read_mbps"),
        " MB/s")
    add("disk", "disk_mixed_64k_mbps", "64K 混合读写", f64.get("speed_rw_mbps"), " MB/s")

    # ---- 网络 ----
    add("network", "net_down_mbps", "下行带宽", m.get("net_down_mbps"), " Mbps")
    add("network", "net_up_mbps", "上行带宽", m.get("net_up_mbps"), " Mbps")
    add("network", "net_latency_ms", "最低延迟", m.get("net_latency_ms"), " ms")

    # ---- 内存 ----
    add("memory", "mem_bandwidth_mbps", "内存带宽", raw.get("mem_bandwidth_mbps"), " MB/s")
    # 内存容量按「够不够跑」的直觉给个参考分（2G 起步，8G 良好，16G+ 优秀）
    ram_mb = None
    try:
        if raw.get("ram_kib"):
            ram_mb = float(raw["ram_kib"]) / 1024.0
            raw["ram_mb"] = round(ram_mb, 1)
    except (TypeError, ValueError):
        pass
    if ram_mb:
        # ⚠️ 这里必须用 _get(th, ...)，不能另写一份 anchors ——
        #    否则用户自定义的 mem_capacity 阈值会被这段硬编码覆盖掉（改配置没反应）。
        cap_rec = add("memory", "mem_capacity", "内存容量", ram_mb, " MB",
                      display="%d MB" % round(ram_mb), weight_in_dim=0.6)
        if cap_rec:
            cap_anchors = _get(th, "mem_capacity")
            if cap_anchors:
                cap_rec["score"] = score_value(ram_mb, cap_anchors)

    # ---- 稳定性 ----
    add("stability", "cpu_steal_pct", "CPU 抢占率", raw.get("cpu_steal_pct"), "%")
    add("stability", "score_cv_pct", "跑分波动", raw.get("score_cv_pct"), "%")

    # ---- 汇总维度分 ----
    dim_results = {}
    for dim, info in dims.items():
        if info["weight_sum"] <= 0:
            continue
        total = 0.0
        wsum = 0.0
        for rec in info["items"]:
            if rec["score"] is None:
                continue
            total += rec["score"] * rec["weight"]
            wsum += rec["weight"]
        dim_score = round(total / wsum, 1) if wsum > 0 else None
        dim_results[dim] = {
            "score": dim_score,
            "grade": grade_word(dim_score),
            "label": DIMENSION_LABELS.get(dim, dim),
            "weight": DIMENSION_WEIGHTS.get(dim, 0),
            "items": info["items"],
        }

    # ---- 总分：按维度权重加权，缺项的维度按 0 计入（并降低可信度） ----
    total_score = None
    covered = 0.0
    acc = 0.0
    for dim, dw in DIMENSION_WEIGHTS.items():
        dr = dim_results.get(dim)
        if not dr or dr["score"] is None:
            continue
        acc += dr["score"] * dw
        covered += dw
    if covered > 0:
        # 按「已覆盖维度」重新归一，避免缺项把总分压低
        total_score = round(acc / covered, 1)
    # 可信度：覆盖了多少权重
    confidence = round(covered * 100)

    return {
        "total_score": total_score,
        "total_grade": grade_word(total_score),
        "confidence": confidence,
        "dimensions": dim_results,
        "items": items,
        "weights": dict(DIMENSION_WEIGHTS),
    }


def summarize(result):
    """给出一句人话总结（用于报告和界面顶部）。"""
    if not result or result.get("total_score") is None:
        return "数据不足，无法评分。"
    t = result["total_score"]
    word = result["total_grade"]
    dims = result.get("dimensions") or {}
    strong = []
    weak = []
    for dim, dr in dims.items():
        if dr.get("score") is None:
            continue
        if dr["score"] >= 80:
            strong.append(dr["label"])
        elif dr["score"] < 50:
            weak.append(dr["label"])
    parts = ["综合 %s 分（%s）" % (t, word)]
    if strong:
        parts.append("强项：" + "、".join(strong))
    if weak:
        parts.append("短板：" + "、".join(weak))
    if result.get("confidence", 100) < 80:
        parts.append("（数据覆盖 %d%%，部分维度缺测）" % result["confidence"])
    return "；".join(parts) + "。"
