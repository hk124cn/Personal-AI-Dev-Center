#!/usr/bin/env python3
import os
import sys

# 用法: python count_md.py <项目目录>
path = sys.argv[1] if len(sys.argv) > 1 else r"D:\path\to\your\project"

target_files = {"README.md", "readme.md", "TODO.md", "todo.md", "PROGRESS.md", "progress.md", "ISSUES.md", "issues.md", "CLAUDE.md", "claude.md"}
max_file_size = 100 * 1024
max_files = 30
keep_list = []
skip_list = []

for root, dirs, files in os.walk(path):
    dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "node_modules", ".venv"}]
    for f in files:
        if f.lower().endswith(".md") and len(keep_list) < max_files:
            # 排除数字开头
            if f[0].isdigit():
                skip_list.append((f, "数字开头"))
                continue
            # 排除含"歌词"
            if "歌词" in f:
                skip_list.append((f, "含歌词"))
                continue
            # 排除含"xueqiu"
            if "xueqiu" in f.lower():
                skip_list.append((f, "含xueqiu"))
                continue
            # 排除风格/文章/模板/内容文件
            lower_f = f.lower()
            if any(k in lower_f for k in ["style_", "article_", "template_", "_raw", "_topics", "buffett", "雪球", "通知", "小学一年级", "侠客行", "侠客", "通知"]):
                skip_list.append((f, "内容/模板文件"))
                continue
            # 排除纯汉字文件名
            base_name = f[:-3]
            if all('一' <= ch <= '鿿' for ch in base_name):
                skip_list.append((f, "纯汉字文件名"))
                continue
            full = os.path.join(root, f)
            try:
                if os.path.getsize(full) > max_file_size:
                    skip_list.append((f, "超过100KB"))
                    continue
                keep_list.append(f)
            except Exception as e:
                skip_list.append((f, f"读取错误: {e}"))

print(f"符合条件的文件: {len(keep_list)} 个")
for f in keep_list:
    print(f"  [+] {f}")
print(f"\n跳过的文件: {len(skip_list)} 个")
for f, reason in skip_list[:30]:
    print(f"  [-] {f} ({reason})")
if len(skip_list) > 30:
    print(f"  ... 还有 {len(skip_list)-30} 个")
