# -*- coding: utf-8 -*-
"""本地侧测速：测「你从这台电脑 → 候选 VPS」的真实延迟与下载速度。

为什么需要这个（重要）：
    服务器侧跑分测的是「那台 VPS 到各公共服务的延迟」，反映的是它的**出口网络质量**；
    但你真正在意的是「**从你家/公司访问它**有多快」—— 这两件事不一样！
    同一台首尔 VPS，国内访问可能 40ms，欧洲访问 200ms；
    而它自己到 Cloudflare anycast 只要 4ms（看着很漂亮，但对你没意义）。

用法（Windows）：
    python tools/local_speedtest.py 1.2.3.4 5.6.7.8 --port 22 --http http://1.2.3.4/
    python tools/local_speedtest.py 1.2.3.4 --http http://1.2.3.4/ --download http://1.2.3.4/test.bin

参数：
    目标 IP/域名（可多个）
    --port N        TCP 端口（默认 22，用于测握手往返延迟 RTT）
    --http URL      HTTP 基地址，用来做真实 HTTP 计时（更接近浏览体验）
    --download URL  大文件直链，测下载带宽
    --repeat N      每个目标重复次数（默认 3，取中位数）
"""
import argparse
import json
import os
import socket
import statistics
import sys
import time
import urllib.error
import urllib.request


def tcp_rtt(host, port, timeout=5.0):
    """测 TCP 三次握手往返时间（毫秒）。这是最接近「ping」的 RTT 指标。"""
    try:
        t0 = time.perf_counter()
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return (time.perf_counter() - t0) * 1000.0
    except Exception:
        return None


def icmp_ping(host, count=4, timeout=5):
    """调系统 ping（Windows 是 ping -n，Linux 是 ping -c）。失败返回 None。

    ⚠️ Windows 的 ping 输出**可能是中文**（"平均 = 28ms"）也可能是英文
       ("Average = 28ms")，两种都得认 —— 只匹配英文会静默返回 None。
    """
    import subprocess
    import shutil
    exe = shutil.which('ping')
    if not exe:
        return None
    if os.name == 'nt':
        cmd = [exe, '-n', str(count), '-w', str(int(timeout * 1000)), host]
    else:
        cmd = [exe, '-c', str(count), '-W', str(int(timeout)), host]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             encoding='utf-8', errors='replace', timeout=timeout * count + 5)
    except Exception:
        return None
    txt = (out.stdout or '') + (out.stderr or '')
    import re
    # 中英文都认；也认 Linux 的 "rtt min/avg/max = 1.2/2.3/3.4 ms"
    for pat in (r'Average\s*=\s*(\d+)\s*ms',
                r'平均\s*=\s*(\d+)\s*ms',
                r'=.*?/([\d.]+)/([\d.]+)/([\d.]+)'):
        m = re.search(pat, txt)
        if m:
            if pat.startswith('=.*?'):
                return float(m.group(2))
            return float(m.group(1))
    return None


def tracert_route(host, max_hops=20, wait_ms=1000):
    """用 tracert 测真实路径与每跳 RTT。

    ⚠️ **为什么必须用 tracert 而不是 ping**（2026-10-24 真实踩过）：
    本机 ping 会被某些代理/加速软件**伪造应答**（实测输出 `time<1ms TTL=64`，
    TTL=64 是 Windows 默认值 → 数据包根本没离开本机）。
    tracert 走的是 TCP SYN，ICMP 劫持不了它，测出来的路径和 RTT 才是真的。

    返回 (hops列表, 末跳RTT)。hops 里每项形如 {'hop':1,'ip':'...','rtt':12.3}
    """
    import subprocess
    import shutil
    import re
    exe = shutil.which('tracert')
    if not exe:
        return None, None
    if os.name == 'nt':
        cmd = [exe, '-d', '-h', str(max_hops), '-w', str(wait_ms), host]
    else:
        cmd = [exe, '-n', '-m', str(max_hops), '-w', str(wait_ms), host]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             encoding='utf-8', errors='replace', timeout=90)
    except Exception:
        return None, None
    txt = (out.stdout or '') + (out.stderr or '')
    hops = []
    for line in txt.splitlines():
        m = re.match(r'\s*(\d+)\s+(\S+)\s+(?:(\d+)\s*ms)?', line)
        if not m:
            continue
        try:
            hop = int(m.group(1))
        except (TypeError, ValueError):
            continue
        ip = m.group(2)
        rtt = None
        if m.group(3):
            try:
                rtt = float(m.group(3))
            except ValueError:
                rtt = None
        else:
            # Linux: "1  gateway (192.168.1.1)  0.512 ms  0.402 ms"
            ms = re.findall(r'([\d.]+)\s*ms', line)
            if ms:
                try:
                    rtt = float(ms[0])
                except ValueError:
                    rtt = None
        hops.append({'hop': hop, 'ip': ip, 'rtt': rtt})
    if not hops:
        return None, None
    # 末跳 RTT：**优先取 IP 与目标匹配的那一跳**，不能简单取最后一跳 ——
    # 中间有 "*"（超时无响应）的跳时，最后一跳可能不是目标。
    final = None
    for h in hops:
        if h['ip'] == host and h['rtt'] is not None:
            final = h['rtt']
            break
    if final is None:
        for h in reversed(hops):
            if h['rtt'] is not None:
                final = h['rtt']
                break
    return hops, final


def detect_proxy():
    """检测本机是否处于代理/TUN 接管状态。

    ⚠️ **这是本地测速的头号坑**（2026-10-24 真实踩过）：
    开了 v2rayN 的 TUN 模式后，所有 TCP 流量都被代理节点接管，
    这时测出来的「延迟」是**到代理节点的延迟**，不是到 VPS 的 ——
    实测把首尔 VPS 测成 2.3ms（实际从国内过去要 40ms+），完全误导。
    所以必须检测并提醒用户关掉代理再测。
    """
    reasons = []
    # 1) 环境变量
    for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        v = os.environ.get(k)
        if v:
            reasons.append('环境变量 %s=%s' % (k, v))
    # 2) Windows WinINET 代理
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as k:
                val, _ = winreg.QueryValueEx(k, 'ProxyEnable')
                if val:
                    reasons.append('Windows 系统代理已开启')
        except Exception:
            pass
    # 3) TUN 虚拟网卡（v2rayN / clash 之类）
    try:
        import subprocess
        out = subprocess.run(['ipconfig'], capture_output=True, text=True,
                             encoding='utf-8', errors='replace', timeout=15)
        low = (out.stdout or '').lower()
        for marker in ('tun', 'wintun', 'wintun', 'sing-box', 'utun', 'tap'):
            if marker in low:
                reasons.append('检测到 TUN/虚拟网卡（可能全局接管流量）')
                break
    except Exception:
        pass
    return reasons



def http_timing(url, timeout=15):
    """测一次 HTTP 请求耗时（毫秒）+ 状态码。"""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'DevCenter-LocalSpeedTest/1.0'})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(2048)
            code = r.status
        return (time.perf_counter() - t0) * 1000.0, code
    except urllib.error.HTTPError as e:
        return (time.perf_counter() - t0) * 1000.0, e.code
    except Exception:
        return None, None


def download_speed(url, seconds=10):
    """限时下载，测 Mbps。"""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'DevCenter-LocalSpeedTest/1.0'})
        total = 0
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=30) as r:
            while time.time() - t0 < seconds:
                chunk = r.read(262144)
                if not chunk:
                    break
                total += len(chunk)
        dt = time.time() - t0
        if dt <= 0 or total == 0:
            return None
        return total * 8.0 / dt / 1000.0   # Mbps
    except Exception:
        return None


def median(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return statistics.median(vals)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('targets', nargs='+', help='IP 或域名（可多个）')
    ap.add_argument('--port', type=int, default=22, help='TCP 端口（默认 22）')
    ap.add_argument('--http', action='append', default=[], help='HTTP URL（可多次）')
    ap.add_argument('--download', action='append', default=[], help='大文件直链（可多次）')
    ap.add_argument('--repeat', type=int, default=3, help='每个目标重复次数（默认 3）')
    ap.add_argument('--json', action='store_true', help='输出 JSON（便于程序消费）')
    args = ap.parse_args()

    # ⚠️ 代理检测放最前面 —— 有代理时后面所有数字都是假的
    proxy_reasons = detect_proxy()
    if proxy_reasons:
        print('!!' + '=' * 60)
        print('!! 检测到代理 / TUN 接管，测出来的延迟【不可信】！')
        for r in proxy_reasons:
            print('!!   · ' + r)
        print('!!   TUN 模式下所有 TCP 流量都被代理节点接管，测到的是「到代理节点」')
        print('!!   的延迟，不是到目标 VPS 的。例如首尔 VPS 会被测成 2ms，')
        print('!!   但国内实际访问要 40ms+。')
        print('!!   → 请先关闭代理（v2rayN TUN / 系统代理），再跑一次。')
        print('!!' + '=' * 60)
        print()

    results = []
    for target in args.targets:
        print('=' * 62)
        print('目标: %s' % target)
        print('-' * 62)

        tcps = []
        pings = []
        ping_suspect = False
        for i in range(args.repeat):
            r = tcp_rtt(target, args.port)
            if r is not None:
                tcps.append(r)
                print('  TCP 握手 RTT  #%d: %7.1f ms' % (i + 1, r))
            p = icmp_ping(target)
            if p is not None:
                pings.append(p)
                # <1ms 基本可判定是本机伪造应答（真链路不可能 <1ms）
                if p < 1:
                    ping_suspect = True
                print('  ICMP ping       : %7.1f ms%s'
                      % (p, '   ⚠️ 疑似本机应答' if p < 1 else ''))
            if i < args.repeat - 1:
                time.sleep(0.4)

        rec = {
            'target': target,
            'tcp_rtt_ms': round(median(tcps), 1) if median(tcps) else None,
            'icmp_ms': round(median(pings), 1) if median(pings) else None,
            'tcp_samples': len(tcps),
            'icmp_samples': len(pings),
            'icmp_suspect': ping_suspect,
        }

        # tracert：唯一可靠的路径 / 真实 RTT（走 TCP，ICMP 劫持不了）
        hops, final_rtt = tracert_route(target)
        if hops:
            rec['tracert_final_ms'] = final_rtt
            rec['hop_count'] = len(hops)
            rec['hops'] = [{'hop': h['hop'], 'ip': h['ip'], 'rtt': h['rtt']} for h in hops]
            print('  tracert 末跳 RTT: %s ms（%d 跳）'
                  % (final_rtt if final_rtt is not None else '—', len(hops)))
            if final_rtt is not None:
                print('  路径: ' + ' → '.join(
                    '%s(%sms)' % (h['ip'], h['rtt'] if h['rtt'] is not None else '?')
                    for h in hops[:8]) + (' …' if len(hops) > 8 else ''))

        if args.http:
            hs = []
            for url in args.http:
                t, code = http_timing(url)
                if t is not None:
                    hs.append(t)
                    print('  HTTP 首字节     : %7.1f ms  (HTTP %s)  %s' % (t, code, url))
            rec['http_ms'] = round(median(hs), 1) if median(hs) else None

        if args.download:
            sp = []
            for url in args.download:
                s = download_speed(url)
                if s is not None:
                    sp.append(s)
                    print('  下载带宽        : %7.1f Mbps  %s' % (s, url))
            rec['download_mbps'] = round(median(sp), 1) if median(sp) else None

        results.append(rec)
        print()

    print('=' * 62)
    print('汇总')
    print('-' * 62)
    print('%-22s %11s %10s %12s %10s' % ('目标', 'TCP RTT', 'ICMP', 'tracert末跳', '下载 Mbps'))
    for r in results:
        icmp_txt = ('%.1f ms' % r['icmp_ms']) if r.get('icmp_ms') is not None else '—'
        if r.get('icmp_suspect'):
            icmp_txt += '*'
        print('%-22s %11s %10s %12s %10s' % (
            r['target'],
            ('%.1f ms' % r['tcp_rtt_ms']) if r.get('tcp_rtt_ms') is not None else '—',
            icmp_txt,
            ('%.1f ms' % r['tracert_final_ms']) if r.get('tracert_final_ms') is not None else '—',
            ('%.1f' % r['download_mbps']) if r.get('download_mbps') is not None else '—'))
    print()
    print('* = ICMP 结果可疑（本机有软件伪造了 ping 应答）')
    print('提示：这是「你 → VPS」的真实延迟，比服务器侧跑分更贴近实际使用体验。')
    print('     tracert 末跳 RTT 最可信（走 TCP，ICMP 劫持不了）。')
    print('     晚高峰（20:00-23:00）再测一次，超售机器的差距会暴露出来。')

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))

    return 0


if __name__ == '__main__':
    sys.exit(main())
