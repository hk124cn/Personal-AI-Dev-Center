# -*- coding: utf-8 -*-
"""VPS 跑分采集：通过 SSH 在远端执行测试，采集原始指标。

三档 profile：
  quick    — 零依赖快速体检（/proc + dd + socket），~15 秒，任何 Linux 都能跑
  standard — YABS 全套（fio + sysbench + iperf3 + Geekbench），~10 分钟
  full     — standard + NextTrace 路由 + IP 质量 + 流媒体解锁

低优先级模式（low_priority=True）：
  用 `nice -n 19` + `ionice -c 3` 包裹执行。**这两个会被子进程继承**，
  所以 fio / sysbench 全部自动降权，不会抢线上业务的 CPU 与磁盘 IO。
  另外在低优先级下**跳过 iperf3**（YABS -i）—— iperf3 会把带宽打满，
  nice 管不住它；改用 quick 档的轻量带宽测试（限速下载）代替。
"""
import io
import json
import os
import re
import shlex
import time

try:
    import paramiko
except ImportError:  # pragma: no cover
    paramiko = None

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
YABS_URL = "https://yabs.sh"
YABS_VERSION_MARK = "v2026"

PROFILES = ("quick", "standard", "full")

# 低优先级：nice 19（最低）+ ionice idle class（磁盘 IO 最低优先级）
NICE_PREFIX = "nice -n 19 ionice -c 3"

# quick 档的轻量带宽测试目标（各自家的大文件，稳定的 CDN）
BANDWIDTH_TARGETS = [
    ("cloudflare", "https://speed.cloudflare.com/__down?bytes=25000000"),
    ("apple", "https://speedtest.tele2.net/25MB.zip"),
]

# quick 档的延迟测试目标（TCP 443，纯 bash /dev/tcp，无需 ping）
# ⚠️ 目标必须**分组**，不能混在一起算中位数 —— 混了没有业务含义：
#   实测腾讯云首尔那台：到 1.1.1.1 anycast 只有 4ms（它就在首尔），
#   但到国内 119.29.29.29 要 41ms。混算中位数会得到「很快」的假象，
#   而用户真正在意的是**到国内**的延迟。
#   → 评分用国内组（D 组），国际组（I 组）单独展示用于判断是否绕路。
# 目标列表可配：config.json 的 `bench_latency_targets`（格式同上，group 字段区分）。
DEFAULT_LATENCY_TARGETS = [
    {"group": "domestic", "name": "阿里云DNS", "host": "223.5.5.5", "port": 443},
    {"group": "domestic", "name": "腾讯DNSPod", "host": "119.29.29.29", "port": 443},
    {"group": "domestic", "name": "114DNS", "host": "114.114.114.114", "port": 443},
    {"group": "intl", "name": "Cloudflare", "host": "1.1.1.1", "port": 443},
    {"group": "intl", "name": "Google", "host": "8.8.8.8", "port": 443},
]

# 延迟探测超时（秒）。超过这个值的都当成"不可达"丢弃。
LATENCY_TIMEOUT = 4

# ---------------------------------------------------------------------------
# 远端脚本：quick 档（纯 POSIX sh，零依赖）
# ---------------------------------------------------------------------------
# 输出格式：每行 KEY=VALUE，本地解析。
# 注意：全部用 sh 兼容语法，不能用 bash 数组 / [[ ]]。
QUICK_SCRIPT = r'''
set -u
echo "BEGIN_QUICK"

# ---- CPU 型号 / 核数 / 频率 ----
CPU_MODEL=$(grep -m1 "^model name" /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ *//')
[ -z "$CPU_MODEL" ] && CPU_MODEL=$(grep -m1 "^Processor" /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ *//')
CPU_CORES=$(grep -c "^processor" /proc/cpuinfo 2>/dev/null || echo 0)
CPU_FREQ=$(grep -m1 "cpu MHz" /proc/cpuinfo 2>/dev/null | cut -d: -f2 | tr -d ' ' | cut -d. -f1)
[ -z "$CPU_FREQ" ] && CPU_FREQ=$(awk '/cpu MHz/{print int($4); exit}' /proc/cpuinfo 2>/dev/null || echo 0)
echo "CPU_MODEL=$CPU_MODEL"
echo "CPU_CORES=$CPU_CORES"
echo "CPU_FREQ_MHZ=$CPU_FREQ"

# ---- CPU steal（超售的直接证据） ----
# /proc/stat 第一行：user nice system idle iowait irq softirq steal
STEAL=$(awk '/^cpu /{print $9; exit}' /proc/stat 2>/dev/null)
TOTAL=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8+$9; exit}' /proc/stat 2>/dev/null)
if [ -n "${STEAL:-}" ] && [ -n "${TOTAL:-}" ] && [ "$TOTAL" -gt 0 ]; then
  echo "CPU_STEAL_PCT=$(awk -v s="$STEAL" -v t="$TOTAL" 'BEGIN{printf "%.2f", s*100/t}')"
else
  echo "CPU_STEAL_PCT="
fi

# ---- 内存 ----
MEM_TOTAL=$(awk '/^MemTotal/{print $2; exit}' /proc/meminfo 2>/dev/null)
MEM_AVAIL=$(awk '/^MemAvailable/{print $2; exit}' /proc/meminfo 2>/dev/null)
SWAP_TOTAL=$(awk '/^SwapTotal/{print $2; exit}' /proc/meminfo 2>/dev/null)
echo "MEM_TOTAL_KIB=${MEM_TOTAL:-}"
echo "MEM_AVAIL_KIB=${MEM_AVAIL:-}"
echo "SWAP_TOTAL_KIB=${SWAP_TOTAL:-}"

# ---- 磁盘顺序写 / 读（dd，64MB）----
# 用 oflag=direct 绕过 page cache；若文件系统不支持则退回普通写
# ⚠️ dd 输出有两种格式，必须都支持（coreutils 8.x 新旧都有）：
#   新: "67108864 bytes (67 MB, 64 MiB) copied, 0.172269 s, 390 MB/s"   ← 直接给了速度
#   旧: "64+0 records in / 64+0 records out / 67108864 bytes (67 MB) copied, 0.172269 s"
# 优先用新格式里直接给的 MB/s，取不到再自己用 秒数 算。
DISK_BASE=/tmp/.devcenter_bench
DF=$(df -k /tmp 2>/dev/null | awk 'NR==2{print $4}')
if [ -z "${DF:-}" ] || [ "$DF" -lt 262144 ]; then
  echo "DISK_WRITE_MBPS="; echo "DISK_READ_MBPS="; echo "DISK_SKIP=1"
else
  sync
  DD_OUT=$(dd if=/dev/zero of=$DISK_BASE bs=1M count=64 oflag=direct 2>&1 | tail -1)
  WB=$(echo "$DD_OUT" | sed -n 's/.*copied, \([0-9.]*\) s.*/\1/p')
  WS=$(echo "$DD_OUT" | sed -n 's/.*copied, \([0-9.]*\) s, \([0-9.]*\) MB\/s.*/\2/p')
  if [ -z "${WB:-}" ]; then
    DD_OUT=$(dd if=/dev/zero of=$DISK_BASE bs=1M count=64 conv=fsync 2>&1 | tail -1)
    WB=$(echo "$DD_OUT" | sed -n 's/.*copied, \([0-9.]*\) s.*/\1/p')
    WS=$(echo "$DD_OUT" | sed -n 's/.*copied, \([0-9.]*\) s, \([0-9.]*\) MB\/s.*/\2/p')
  fi
  if [ -n "${WS:-}" ] && [ "$WS" != "0" ]; then
    echo "DISK_WRITE_MBPS=$WS"
  elif [ -n "${WB:-}" ] && [ "$WB" != "0" ]; then
    echo "DISK_WRITE_MBPS=$(awk -v s="$WB" 'BEGIN{printf "%.1f", 64/s}')"
  else
    echo "DISK_WRITE_MBPS="
  fi
  sync
  RD=$(dd if=$DISK_BASE of=/dev/null bs=1M iflag=direct 2>&1 | tail -1)
  RB=$(echo "$RD" | sed -n 's/.*copied, \([0-9.]*\) s.*/\1/p')
  RS=$(echo "$RD" | sed -n 's/.*copied, \([0-9.]*\) s, \([0-9.]*\) MB\/s.*/\2/p')
  if [ -z "${RB:-}" ]; then
    RD=$(dd if=$DISK_BASE of=/dev/null bs=1M 2>&1 | tail -1)
    RB=$(echo "$RD" | sed -n 's/.*copied, \([0-9.]*\) s.*/\1/p')
    RS=$(echo "$RD" | sed -n 's/.*copied, \([0-9.]*\) s, \([0-9.]*\) MB\/s.*/\2/p')
  fi
  if [ -n "${RS:-}" ] && [ "$RS" != "0" ]; then
    echo "DISK_READ_MBPS=$RS"
  elif [ -n "${RB:-}" ] && [ "$RB" != "0" ]; then
    echo "DISK_READ_MBPS=$(awk -v s="$RB" 'BEGIN{printf "%.1f", 64/s}')"
  else
    echo "DISK_READ_MBPS="
  fi
  rm -f $DISK_BASE
  echo "DISK_SKIP=0"
fi

# ---- TCP 延迟（bash /dev/tcp，无需 ping）----
# ⚠️ 两个坑，都踩过：
#  1) timeout 的秒数是**占位符**（__TSEC__），本文件是 Python 字符串不做 % 格式化，
#     之前直接写 `timeout %d` 会让远端执行 `timeout %d`（参数非法→立即失败→假延迟 3ms）
#  2) timeout 到点时 TIMEFORMAT 输出的就是超时秒数，那**不是延迟**，必须丢弃
#     （用 __TMS__ 毫秒阈值判断）
# 目标清单由 Python 侧动态生成（__TARGETS__ 占位），按组打前缀：LAT_D_<host>_MS / LAT_I_<host>_MS
for T in __TARGETS__; do
  set -- $T
  G=$1; H=$2; P=$3
  R=$( { TIMEFORMAT=%R; time timeout __TSEC__ bash -c "exec 3<>/dev/tcp/$H/$P" 2>/dev/null; } 2>&1 )
  if [ -n "${R:-}" ] && [ "$R" != "0" ]; then
    MS=$(awk -v r="$R" 'BEGIN{printf "%.1f", r*1000}')
    # 丢弃超时值（>= 阈值毫秒说明连不上，不是延迟）
    if awk -v m="$MS" -v t="__TMS__" 'BEGIN{exit !(m+0 < t+0)}' 2>/dev/null; then
      echo "LAT_${G}_${H}_MS=$MS"
    fi
  fi
done

# ---- ICMP ping 备份（TCP 443 不通时，ICMP 往往还能用）----
if command -v ping >/dev/null 2>&1; then
  for T in __PING_TARGETS__; do
    set -- $T
    G=$1; H=$2
    P=$(timeout 6 ping -c 3 -W 2 "$H" 2>/dev/null | awk -F'/' '/rtt|round-trip/{print $(NF-1); exit}')
    if [ -n "${P:-}" ]; then
      echo "PING_${G}_${H}_MS=$(awk -v p="$P" 'BEGIN{printf "%.1f", p}')"
    fi
  done
fi

# ---- 轻量带宽（限速下载 25MB，算 Mbps）----
DOWN=""
for NAME in cloudflare apple; do
  case $NAME in
    cloudflare) URL="https://speed.cloudflare.com/__down?bytes=25000000";;
    apple) URL="https://speedtest.tele2.net/25MB.zip";;
  esac
  if command -v curl >/dev/null 2>&1; then
    R=$(timeout 40 curl -s -o /dev/null -w "%{speed_download}" --max-time 35 "$URL" 2>/dev/null)
  elif command -v wget >/dev/null 2>&1; then
    R=$(timeout 40 wget -q -O /dev/null "$URL" 2>/dev/null; echo)
    R=""
  else
    R=""
  fi
  case "$R" in
    ''|*[!0-9.]*) ;;
    *) echo "BANDWIDTH_${NAME}_MBPS=$(awk -v b="$R" 'BEGIN{printf "%.1f", b*8/1000000}')";;
  esac
done

echo "END_QUICK"
'''


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _ssh_connect(server):
    """建立 SSH 连接（与 sync.py 同口径：Ed25519 私钥）。"""
    if paramiko is None:
        raise RuntimeError("paramiko 未安装")
    key_path = os.path.expanduser(server["key_path"])
    key = paramiko.Ed25519Key.from_private_key_file(key_path)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=server["host"],
        port=server["port"],
        username=server["user"],
        pkey=key,
        timeout=30,
        banner_timeout=30,
        auth_timeout=30,
    )
    transport = client.get_transport()
    if transport:
        transport.set_keepalive(30)
    return client


def _run(client, cmd, timeout=1800, cancel_event=None):
    """执行一条命令，返回 (stdout, stderr, returncode)。"""
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    try:
        rc = stdout.channel.recv_exit_status()
    except Exception:
        rc = -1
    return out, err, rc


def _parse_kv(text):
    """解析 KEY=VALUE 行。"""
    data = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if not k or k.startswith("#"):
            continue
        data[k] = v.strip()
    return data


def _num(value):
    if value is None:
        return None
    value = str(value).strip()
    if value == "":
        return None
    try:
        f = float(value)
    except ValueError:
        return None
    return int(f) if f == int(f) and abs(f) < 1e15 else f


def fix_yabs_json(raw):
    """修 YABS 的 JSON 空值 bug。

    YABS 偶尔会输出 `"ram":,` 这种（键后面直接跟逗号、没有值），
    标准 JSON 解析器会直接报错。这里用正则补成 null。
    """
    if not raw:
        return None
    text = raw.strip()
    # 有些版本会把整个 JSON 包在 ``` 或前后有杂讯，取最外层 {...}
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    text = re.sub(r'"(\w+)"\s*:\s*,', r'"\1": null,', text)
    text = re.sub(r',(\s*[}\]])', r'\1', text)      # 去掉尾逗号
    try:
        return json.loads(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 各档位采集
# ---------------------------------------------------------------------------
def _collect_quick(client, progress_cb=None, cancel_event=None, low_priority=True,
                   latency_targets=None):
    """quick 档：零依赖体检。"""
    if progress_cb:
        progress_cb({"phase": "running", "step": "quick", "message": "快速体检中（零依赖）"})

    targets = latency_targets or DEFAULT_LATENCY_TARGETS
    # 生成 shell 目标清单：GROUP HOST PORT
    shell_targets = []
    for t in targets:
        g = (t.get("group") or "domestic")[:1].upper()   # domestic→D, intl→I
        shell_targets.append("%s %s %s" % (g, shlex.quote(t["host"]), int(t.get("port") or 443)))
    ping_targets = []
    for t in targets:
        g = (t.get("group") or "domestic")[:1].upper()
        ping_targets.append("%s %s" % (g, shlex.quote(t["host"])))

    # 脚本用 base64 传，避免引号/换行的层层转义问题
    import base64
    script = (QUICK_SCRIPT
              .replace("__TSEC__", str(LATENCY_TIMEOUT))
              .replace("__TMS__", str(LATENCY_TIMEOUT * 1000))
              .replace("__TARGETS__", " ".join('"%s"' % t for t in shell_targets))
              .replace("__PING_TARGETS__", " ".join('"%s"' % t for t in ping_targets)))
    b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    prefix = (NICE_PREFIX + " ") if low_priority else ""
    cmd = ("echo %s | base64 -d > /tmp/.devcenter_quick.sh && "
           "chmod +x /tmp/.devcenter_quick.sh && "
           "%sbash /tmp/.devcenter_quick.sh; rm -f /tmp/.devcenter_quick.sh"
           % (shlex.quote(b64), prefix))

    out, err, rc = _run(client, cmd, timeout=300, cancel_event=cancel_event)
    kv = _parse_kv(out)

    if "BEGIN_QUICK" not in out:
        raise RuntimeError("quick 档执行失败：%s" % (err.strip()[:300] or out.strip()[:300] or "无输出"))

    result = {
        "profile": "quick",
        "low_priority": low_priority,
        "raw": {
            "cpu_model": kv.get("CPU_MODEL") or None,
            "cpu_cores": _num(kv.get("CPU_CORES")),
            "cpu_freq_mhz": _num(kv.get("CPU_FREQ_MHZ")),
            "cpu_steal_pct": _num(kv.get("CPU_STEAL_PCT")),
            "ram_kib": _num(kv.get("MEM_TOTAL_KIB")),
            "ram_available_kib": _num(kv.get("MEM_AVAIL_KIB")),
            "swap_kib": _num(kv.get("SWAP_TOTAL_KIB")),
            "disk_write_mbps": _num(kv.get("DISK_WRITE_MBPS")),
            "disk_read_mbps": _num(kv.get("DISK_READ_MBPS")),
            "net_latency_ms": _num(kv.get("NET_LATENCY_MS")),
        },
        "latency_detail": {},
        "ping_detail": {},
        "bandwidth_detail": {},
        "yabs": None,
    }
    for k, v in kv.items():
        # 新格式：LAT_D_<host>_MS / LAT_I_<host>_MS（带组前缀）
        if k.startswith("LAT_") and k.endswith("_MS"):
            mid_part = k[4:-3]
            if "_" in mid_part:
                grp, host = mid_part.split("_", 1)
            else:
                grp, host = "D", mid_part
            result["latency_detail"].setdefault(grp, {})[host] = _num(v)
        elif k.startswith("PING_") and k.endswith("_MS"):
            mid_part = k[5:-3]
            if "_" in mid_part:
                grp, host = mid_part.split("_", 1)
            else:
                grp, host = "D", mid_part
            result["ping_detail"].setdefault(grp, {})[host] = _num(v)
        elif k.startswith("BANDWIDTH_") and k.endswith("_MBPS"):
            name = k[10:-5]
            result["bandwidth_detail"][name] = _num(v)

    # ---- 延迟代表值：按组分别算中位数 ----
    # ⚠️ 为什么分组：延迟目标「就近」不代表线路好。
    #   实测腾讯云首尔那台：到 1.1.1.1 anycast 只要 4ms（在首尔），
    #   但到国内 119.29.29.29 要 41ms。混在一起算中位数会得到「很快」的假象，
    #   而用户（在国内、用户也在国内）真正在意的是**到国内**的延迟。
    #   → 评分用国内组（D）；国际组（I）单独展示，用来判断是否绕路。
    def _median(values):
        vals = sorted(v for v in values if v and v < 10000)
        if not vals:
            return None, 0
        mid = len(vals) // 2
        if len(vals) % 2:
            return float(vals[mid]), len(vals)
        return (vals[mid - 1] + vals[mid]) / 2.0, len(vals)

    groups = {}
    for grp in set(list(result["latency_detail"].keys()) + list(result["ping_detail"].keys())):
        vals = list((result["latency_detail"].get(grp) or {}).values()) + \
               list((result["ping_detail"].get(grp) or {}).values())
        med, n = _median(vals)
        groups[grp] = {"median_ms": round(med, 1) if med is not None else None, "samples": n}
    result["latency_groups"] = groups

    domestic = groups.get("D") or {}
    intl = groups.get("I") or {}
    # 评分取国内组；没有国内组才退回国际组
    if domestic.get("median_ms") is not None:
        result["raw"]["net_latency_ms"] = domestic["median_ms"]
        result["latency_group_used"] = "D"
    elif intl.get("median_ms") is not None:
        result["raw"]["net_latency_ms"] = intl["median_ms"]
        result["latency_group_used"] = "I"
    else:
        result["raw"]["net_latency_ms"] = None
        result["latency_group_used"] = None
    result["latency_samples"] = (domestic.get("samples") or 0) + (intl.get("samples") or 0)

    # 磁盘顺序速度换算成评分引擎认的字段
    if result["raw"].get("disk_write_mbps"):
        result["raw"]["disk_seq_write_mbps"] = result["raw"]["disk_write_mbps"]
    if result["raw"].get("disk_read_mbps"):
        result["raw"]["disk_seq_read_mbps"] = result["raw"]["disk_read_mbps"]

    # 带宽：取最快的一个作为该机的代表值
    bw = [v for v in result["bandwidth_detail"].values() if v]
    if bw:
        result["raw"]["net_down_mbps"] = max(bw)
    if progress_cb:
        progress_cb({"phase": "done", "message": "快速体检完成"})
    return result


def _yabs_command(profile, low_priority, result_path):
    """拼 YABS 执行命令。

    - 低优先级：nice + ionice 包裹（会被子进程继承）
    - 低优先级时跳过 iperf3（-i）：iperf3 打满带宽，nice 管不住
    """
    flags = []
    if profile == "full":
        pass  # full 档 YABS 跑全套
    elif low_priority:
        flags.append("-i")
    else:
        pass
    if low_priority:
        flags.append("-r")     # 减少 iperf 地点（配合 -i 时无意义，但无害）
    flag_str = (" " + " ".join(flags)) if flags else ""
    prefix = (NICE_PREFIX + " ") if low_priority else ""
    return ("cd /tmp && rm -f %s && "
            "curl -sL %s | %sbash -s --%s -w %s; "
            "cat %s 2>/dev/null; rm -f %s"
            % (shlex.quote(result_path), shlex.quote(YABS_URL),
               prefix, flag_str, shlex.quote(result_path),
               shlex.quote(result_path), shlex.quote(result_path)))


def _collect_standard(client, profile="standard", progress_cb=None,
                      cancel_event=None, low_priority=True):
    """standard / full 档：跑 YABS，解析 JSON。"""
    if progress_cb:
        progress_cb({"phase": "running", "step": "yabs",
                     "message": "YABS 跑分中（fio / sysbench / iperf3 / Geekbench），约 10 分钟"})

    result_path = "/tmp/.devcenter_yabs.json"
    cmd = _yabs_command(profile, low_priority, result_path)

    t0 = time.time()
    out, err, rc = _run(client, cmd, timeout=1800, cancel_event=cancel_event)
    elapsed = time.time() - t0

    # stdout 里混着 YABS 的文本输出，JSON 在 cat 出来的部分。整体再试一次解析。
    yabs = fix_yabs_json(out)
    if not yabs:
        # 兜底：再单独 cat 一次（YABS 可能在最后才写文件）
        raw2, _, _ = _run(client, "cat %s 2>/dev/null" % shlex.quote(result_path),
                          timeout=60, cancel_event=cancel_event)
        yabs = fix_yabs_json(raw2)
        _run(client, "rm -f %s" % shlex.quote(result_path), timeout=30)

    if not yabs:
        tail = (err or out or "").strip()[-400:]
        raise RuntimeError("YABS 未产出可解析的 JSON：%s" % tail)

    result = {
        "profile": profile,
        "low_priority": low_priority,
        "elapsed_sec": round(elapsed, 1),
        "yabs": yabs,
        "raw": {},
    }
    if progress_cb:
        progress_cb({"phase": "done", "message": "YABS 完成（%.0f 秒）" % elapsed})

    if profile == "full":
        extra = _collect_full_extra(client, progress_cb=progress_cb,
                                     cancel_event=cancel_event, low_priority=low_priority)
        result["extra"] = extra
        result["raw"].update(extra.get("raw") or {})
    return result


def _collect_full_extra(client, progress_cb=None, cancel_event=None, low_priority=True):
    """full 档附加项：路由（NextTrace）+ IP 质量 + 流媒体解锁。"""
    extra = {"raw": {}, "route": None, "ip_quality": None, "unlock": None}
    prefix = (NICE_PREFIX + " ") if low_priority else ""

    # --- 路由：优先 NextTrace，回退 besttrace ---
    if progress_cb:
        progress_cb({"phase": "running", "step": "route", "message": "路由追踪中（NextTrace）"})
    try:
        cmd = ("cd /tmp && curl -fL -m 60 https://nxtrace.org/nt -o nt.sh 2>/dev/null && "
               "chmod +x nt.sh && %s./nt.sh --tcp 223.5.5.5 2>&1 | head -60; rm -f nt.sh"
               % prefix)
        out, err, _ = _run(client, cmd, timeout=180, cancel_event=cancel_event)
        hops = []
        for line in out.splitlines():
            m = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
            if m and "AS" in line or (m and "ms" in line):
                hops.append(line.strip()[:160])
        if hops:
            extra["route"] = {"hops": hops[:40]}
            # 粗略识别是否走 CN2（AS4809）
            extra["raw"]["route_cn2"] = 1 if "AS4809" in out else 0
    except Exception:
        pass

    # --- IP 质量 ---
    if progress_cb:
        progress_cb({"phase": "running", "step": "ip", "message": "IP 质量检测中"})
    try:
        cmd = ("%sbash <(curl -Ls --max-time 90 https://IP.Check.Place) 2>&1 | head -120"
               % prefix)
        out, err, _ = _run(client, cmd, timeout=180, cancel_event=cancel_event)
        if out.strip():
            extra["ip_quality"] = {"raw_text": out[-3000:]}
    except Exception:
        pass

    # --- 流媒体解锁 ---
    if progress_cb:
        progress_cb({"phase": "running", "step": "unlock", "message": "流媒体解锁检测中"})
    try:
        cmd = ("%sbash <(curl -L -s --max-time 90 https://check.unlock.media) 2>&1 | head -80"
               % prefix)
        out, err, _ = _run(client, cmd, timeout=180, cancel_event=cancel_event)
        if out.strip():
            extra["unlock"] = {"raw_text": out[-2000:]}
    except Exception:
        pass

    return extra


# ---------------------------------------------------------------------------
# 对外主入口
# ---------------------------------------------------------------------------
def run_benchmark(server, profile="standard", low_priority=True,
                  progress_cb=None, cancel_event=None, latency_targets=None):
    """对一台服务器跑分，返回结构化结果。

    Returns:
        {"profile":..., "low_priority":..., "yabs":..., "raw":..., "collected_at":...}
    """
    if profile not in PROFILES:
        raise ValueError("未知 profile: %s" % profile)

    client = _ssh_connect(server)
    try:
        if profile == "quick":
            result = _collect_quick(client, progress_cb=progress_cb,
                                    cancel_event=cancel_event, low_priority=low_priority,
                                    latency_targets=latency_targets)
        else:
            result = _collect_standard(client, profile=profile, progress_cb=progress_cb,
                                       cancel_event=cancel_event, low_priority=low_priority)
    finally:
        try:
            client.close()
        except Exception:
            pass

    result["collected_at"] = time.time()
    result["server_id"] = server.get("id")
    result["server_name"] = server.get("name")
    return result


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------
def bench_dir(data_dir):
    return os.path.join(str(data_dir), "benchmarks")


def save_result(data_dir, server_id, result):
    """保存跑分结果，返回文件路径。"""
    import pathlib
    root = pathlib.Path(bench_dir(data_dir))
    root.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = root / ("%s_%s.json" % (server_id, ts))
    payload = dict(result)
    payload["saved_at"] = time.time()
    tmp = str(path) + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, str(path))
    return str(path)


def list_results(data_dir, server_id=None, limit=50):
    """列出历史跑分记录（新的在前）。"""
    import pathlib
    root = pathlib.Path(bench_dir(data_dir))
    if not root.exists():
        return []
    out = []
    for p in sorted(root.glob("*.json"), reverse=True):
        if server_id and not p.name.startswith(str(server_id) + "_"):
            continue
        try:
            with io.open(str(p), encoding="utf-8") as fh:
                out.append(json.load(fh))
        except (ValueError, OSError):
            continue
        if len(out) >= limit:
            break
    return out


def load_result(data_dir, filename):
    import pathlib
    p = pathlib.Path(bench_dir(data_dir)) / filename
    if not p.exists():
        return None
    with io.open(str(p), encoding="utf-8") as fh:
        return json.load(fh)
