# -*- coding: utf-8 -*-
"""i18n key 完整性检查：代码里用的每个 t('xxx') 都必须有对应文案。

为什么需要（2026-10-31 真实事故）：
    `t(key)` 在 key 不存在时**静默返回 key 本身**，
    界面就显示成 "sync_files_done" 这种裸 key，
    而且**没有任何报错** —— 整整一天没人发现（用户截图报障才发现）。
    一次性补齐 5 个 key 还不够，必须有测试守住这条线。

检查两件事：
    1. 缺失：用了但没定义 → 界面会显示裸 key（**必须 0**）
    2. 未使用：定义了但没用（仅提示，不算失败）

用法：
    python tools/test_i18n_keys.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index.html")


def collect():
    s = io.open(INDEX, encoding="utf-8").read()
    i = s.index("const I18N = {")
    j = s.index("\n};", i)
    # key 可以一行写多个（wd_mon/wd_tue... 就是），所以不能要求行首
    defined = set(re.findall(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*\{", s[i:j]))
    # 负向前瞻排除 createElement('div') 这类（前一个字符是字母）
    used = set(re.findall(r"(?<![A-Za-z0-9_])t\(\s*'([a-zA-Z_][a-zA-Z0-9_]*)'\s*\)", s))
    used |= set(re.findall(r'(?<![A-Za-z0-9_])t\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"\s*\)', s))
    return s, defined, used


def main():
    s, defined, used = collect()
    missing = sorted(used - defined)
    unused = sorted(d for d in defined - used if not d.startswith("_"))

    print("  I18N 已定义 key : %d" % len(defined))
    print("  代码里使用 key  : %d" % len(used))
    print()

    if missing:
        print("  ❌ 缺失 %d 个（界面会显示裸 key）:" % len(missing))
        for k in missing:
            ln = [i + 1 for i, l in enumerate(s.split("\n")) if ("t('%s')" % k) in l]
            print("     %-26s 行 %s" % (k, ln[:3]))
    else:
        print("  ✅ 无缺失 key")

    if unused:
        print("  ℹ️ 已定义但未使用: %d 个（不影响功能，仅提示）" % len(unused))

    # 顺带检查 t() 的兜底逻辑在不在 —— 缺了它，下次漏 key 还是静默
    has_warn = "[i18n] 缺少文案 key" in s
    print("  %s t() 缺 key 时有控制台警告（防再次静默）" % ("✅" if has_warn else "❌"))
    if not has_warn:
        missing.append("__t_warn_missing__")

    print("----")
    print("合计 %d 项，失败 %d 项" % (2, len(missing)))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
