# -*- coding: utf-8 -*-
"""cron 模块**真机**测试：枚举 + 一次性沙盒路径上的完整写入链路。

与 backend/test_cron_manager.py 的分工：
  · 那边的 FakeSSHClient 单测覆盖逻辑分支（快、无需网络）
  · 这边打真实服务器，覆盖「真实系统到底长什么样」——例如 Debian 的
    /var/spool/cron 下只有 crontabs/atjobs/atspool 子目录，
    曾导致 UI 出现假的「用户 crontabs」来源。

安全设计：
  · 枚举（list_sources / get）纯只读
  · 写入链路**不碰任何真实 crontab**，而是用自建临时来源
    scope = "system:/tmp/devcenter_cron_probe.txt"（走 `cat 临时文件 > 目标` 分支），
    验证 备份 → SFTP 落盘 → 写回 → 读回一致 → 条数统计，最后删掉探针文件
  · 不打印任何密钥内容；不删 /tmp/cron.bak.*（那是用户真实 crontab 的备份）

用法：python tools/test_cron_realmachine.py
需要项目 backend/build_venv（含 paramiko），并已配置 %APPDATA% 下的 config.json。
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import paramiko  # noqa: E402
from backend.cron_manager import CronManager  # noqa: E402

CFG = os.path.join(os.environ["APPDATA"], "Personal AI Dev Center", "config.json")
PROBE_SCOPE = "system:/tmp/devcenter_cron_probe.txt"
PROBE_PATH = "/tmp/devcenter_cron_probe.txt"
PROBE_OK = "# devcenter cron probe\n0 3 * * * /bin/true\n"
PROBE_BAD = "this line is not a valid crontab entry\n"
# 这些是 spool 目录名，不是用户；出现即说明 _spool_users 过滤失效
FAKE_NAMES = {"crontabs", "atjobs", "atspool", "anacron", "tmp", "last"}


def connect(server):
    key_path = os.path.expanduser(server.get("key_path") or server.get("sshKey") or "")
    pkey, last = None, None
    for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            pkey = cls.from_private_key_file(key_path)
            break
        except Exception as e:
            last = e
    if pkey is None:
        raise RuntimeError(f"无法加载私钥: {last}")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(hostname=server.get("host") or server.get("ip"),
              port=int(server.get("port") or server.get("sshPort") or 22),
              username=server.get("user") or server.get("sshUser"),
              pkey=pkey, timeout=15)
    return c


def main():
    cfg = json.load(io.open(CFG, encoding="utf-8"))
    summary, fails = [], 0

    for sv in cfg.get("servers", []):
        name = sv.get("name")
        print("=" * 74)
        print(f"### {name} ({sv.get('host') or sv.get('ip')})")
        try:
            client = connect(sv)
        except Exception as e:
            print(f"  连接失败: {type(e).__name__}: {e}")
            summary.append((name, "连接失败", "-", "-", "-"))
            fails += 1
            continue

        mgr = CronManager(client)
        try:
            info = mgr.list_sources()
            scopes = [s["scope"] for s in info["sources"]]
            fake = [s for s in scopes
                    if s.startswith("user:") and s[len("user:"):] in FAKE_NAMES]
            print(f"  连接用户: {info['connection_user']}  is_root={info['is_root']}  "
                  f"sudo_available={info['sudo_available']}  writable={info['writable']}")
            for s in info["sources"]:
                print(f"    - {s['scope']:38s} {s['entries']:>3d} 条  "
                      f"{'可写' if s['writable'] else '只读'}"
                      + (f"  错误: {s['error']}" if s.get('error') else ""))
            print("  cron.* 目录: " + ", ".join(f"{k}={len(v)}"
                                              for k, v in info["cron_dirs"].items()))
            if fake:
                print(f"  ❌ 出现假用户来源（spool 目录名被当成用户名）: {fake}")
                fails += 1
            else:
                print("  ✅ 无假用户来源")

            for s in info["sources"]:
                g = mgr.get(s["scope"])
                n = len((g.get("content") or "").splitlines())
                print(f"    get({s['scope']}) -> {n} 行 / 列表 {s['entries']} 条")

            if not info["writable"]:
                print("  [跳过写入探针] 当前用户不可写 cron（非 root 且无 NOPASSWD sudo）")
                summary.append((name, "枚举通过", info["connection_user"], "不可写", "跳过写入"))
                continue

            r1 = mgr.save(PROBE_SCOPE, PROBE_OK)
            print(f"  写入探针 save(合法内容) -> ok={r1.get('ok')} "
                  f"entries={r1.get('entries')} backup={r1.get('backup')} err={r1.get('error')}")
            r2 = mgr.save(PROBE_SCOPE, PROBE_BAD)
            print(f"  写入探针 save(非法内容) -> ok={r2.get('ok')} err={r2.get('error')} "
                  f"validation={r2.get('validation_errors')}")
            out, _, _ = mgr._run(f"cat {PROBE_PATH} 2>/dev/null")
            print(f"  目标文件实际内容: {out!r}")
            # 只删自己造的探针文件；/tmp/cron.bak.* 是真实 crontab 备份，绝不能碰
            mgr._run(f"rm -f {PROBE_PATH}")
            gone, _, _ = mgr._run(f"test -f {PROBE_PATH} && echo exists || echo gone")
            print(f"  清理探针: {gone.strip()}")

            if not (r1.get("ok") and r1.get("entries") == 1 and not r2.get("ok")
                    and out == PROBE_OK and gone.strip() == "gone"):
                print("  ❌ 写入链路未达预期")
                fails += 1
            summary.append((name, "枚举+写入均通过", info["connection_user"],
                            "可写", f"save_ok={r1.get('ok')} 非法拦截={not r2.get('ok')}"))
        except Exception as e:
            print(f"  执行异常: {type(e).__name__}: {e}")
            summary.append((name, f"异常 {type(e).__name__}", "-", "-", "-"))
            fails += 1
        finally:
            try:
                client.close()
            except Exception:
                pass

    print("=" * 74)
    print("汇总：")
    for row in summary:
        print("  ", " | ".join(str(x) for x in row))
    print("=" * 74)
    print("失败项:", fails, "→", "PASS" if fails == 0 else "FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
