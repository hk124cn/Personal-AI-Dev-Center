# -*- coding: utf-8 -*-
"""估算一次「同步 + LLM 分析」的 token 消耗与成本（**只读**，不改任何数据）。

会连每台服务器读项目根目录的 md 文件内容并统计字符数（不下载、不写盘）。

用法：
  python tools/estimate_llm_tokens.py                          # 全部项目
  python tools/estimate_llm_tokens.py 打字游戏 AUTO-STOCK       # 只看指定项目
  python tools/estimate_llm_tokens.py --price-in 0.15 --price-out 0.60
                                                               # 自定义单价（USD / 1M tokens）

注意：这里是「所有项目都重新分析一遍」的**全量**口径。启用增量跳过后，
md 内容没变的项目不再发请求，常态消耗会远低于这个数。
"""
import json
import os
import shlex
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import paramiko  # noqa: E402
from backend import sync as S  # noqa: E402

# build_analysis_prompt() 的模板固定开销（指令 + 输出格式要求），不含 md 正文
PROMPT_OVERHEAD_CHARS = 800
# 中英混合的保守折算（中文约 1 字 1 token，英文约 4 字符 1 token）
CHARS_PER_TOKEN = 1.6
# llm_analyzer.py 里 payload 的 max_tokens
MAX_OUTPUT_TOKENS = 2000


def opt(name, default):
    for a in sys.argv[1:]:
        if a.startswith(name + "="):
            try:
                return float(a.split("=", 1)[1])
            except ValueError:
                pass
    return default


def main():
    names = [a for a in sys.argv[1:] if not a.startswith("--")]
    price_in = opt("--price-in", 0.15)    # USD / 1M input tokens
    price_out = opt("--price-out", 0.60)  # USD / 1M output tokens

    d = os.path.join(os.environ["APPDATA"], "Personal AI Dev Center")
    cfg = json.load(open(os.path.join(d, "config.json"), encoding="utf-8"))
    srvmap = {s["id"]: s for s in cfg["servers"]}
    projects = [p for p in cfg["projects"] if (not names) or p.get("name") in names]

    total_in = 0
    total_out = 0
    clients = {}
    print(f"{'项目':<20} {'md':>3} {'字符数':>8} {'输入tok':>8}")
    print("-" * 44)

    for p in projects:
        name = str(p.get("name"))[:18]
        srv = srvmap.get(p.get("server"))
        if not srv:
            print(f"{name:<20} (无服务器配置，跳过)")
            continue
        try:
            cli = clients.get(srv["id"])
            if cli is None:
                key = paramiko.Ed25519Key.from_private_key_file(
                    os.path.expanduser(srv["key_path"]))
                cli = paramiko.SSHClient()
                cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                cli.connect(hostname=srv["host"], port=srv["port"],
                            username=srv["user"], pkey=key, timeout=15)
                clients[srv["id"]] = cli

            pd = p["remote_path"]
            _, so, _ = cli.exec_command(
                f"find {S._shell_path(pd)} -maxdepth 1 -name '*.md' -type f")
            files = [x for x in so.read().decode("utf-8", "replace").splitlines() if x.strip()]

            chars = 0
            for f in files:
                _, so2, _ = cli.exec_command(f"cat {shlex.quote(f)}")
                chars += len(so2.read().decode("utf-8", "replace"))

            n_in = int((chars + PROMPT_OVERHEAD_CHARS) / CHARS_PER_TOKEN)
            total_in += n_in
            total_out += MAX_OUTPUT_TOKENS
            print(f"{name:<20} {len(files):>3} {chars:>8} {n_in:>8}")
        except Exception as e:
            print(f"{name:<20} ERROR: {type(e).__name__}: {str(e)[:50]}")

    for cli in clients.values():
        try:
            cli.close()
        except Exception:
            pass

    print("-" * 44)
    print(f"全量重算一次: 输入 {total_in:,} tok + 输出上限 {total_out:,} tok")
    cost = total_in / 1e6 * price_in + total_out / 1e6 * price_out
    print(f"按 ${price_in}/${price_out} per 1M 计 ≈ ${cost:.4f}"
          f"（约 ¥{cost * 7.2:.3f}）  ← 单价为示例，请以官网为准")
    print()
    print("启用增量跳过后：md 没变的项目不再发请求，")
    print("常态每轮消耗 ≈（本轮真正改动过的项目数 / 总项目数）× 上面这个数。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
