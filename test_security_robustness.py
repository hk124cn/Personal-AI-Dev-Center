"""R6/R7/R10 修复的集成测试：直接调用 backend/app.py 与 backend/sync.py 中的函数。"""
import os, sys, json, tempfile, threading, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend import app as app_mod
from backend import sync as sync_mod

ok = 0
fail = 0
def check(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}")

print("\n=== R7 知识库 Markdown XSS 消毒 ===")
# 危险输入
evil = (
    '<script>alert(1)</script>'
    '<img src=x onerror=alert(2)>'
    '<a href="javascript:alert(3)">click</a>'
    '<iframe src="javascript:alert(4)"></iframe>'
    '<div onclick="steal()">hi</div>'
    '<style>body{display:none}</style>'
)
clean = app_mod.sanitize_html(evil)
print("    清洗结果:", clean)
check("移除 <script>", "<script" not in clean)
check("script 内容不泄漏", "alert(1)" not in clean)
check("移除 onerror 属性", "onerror" not in clean)
check("阻断 javascript: 链接", "javascript:" not in clean)
check("移除 <iframe>", "<iframe" not in clean)
check("移除 onclick 属性", "onclick" not in clean)
check("移除 <style>", "<style" not in clean)

# 合法 Markdown 应保留
md = "# 标题\n\n**加粗** 与 `代码`\n\n- 列表项\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n[链接](https://example.com)"
rendered = app_mod.sanitize_html(app_mod.markdown.markdown(md, extensions=["fenced_code", "tables", "nl2br"]))
check("保留标题 h1", "<h1" in rendered)
check("保留加粗", "<strong>" in rendered)
check("保留代码", "<code>" in rendered)
check("保留表格", "<table" in rendered)
check("保留安全链接", 'href="https://example.com"' in rendered)

# 直接调用 /api/kb/render 端点
resp = app_mod.kb_render({"body": '<script>alert(9)</script>**x**'})
check("kb_render 端点已消毒", "<script" not in resp["html"] and "<strong>" in resp["html"])

print("\n=== R6 原子写入 + 并发安全 ===")
d = tempfile.mkdtemp()
cfg = os.path.join(d, "config.json")
# 1) 原子写入基本正确
app_mod.atomic_write_json(cfg, {"k": "v", "n": 1})
with open(cfg, encoding="utf-8") as f:
    loaded = json.load(f)
check("atomic_write_json 写入正确", loaded == {"k": "v", "n": 1})
check("无残留 .tmp 文件", not any(p.endswith(".tmp") for p in os.listdir(d)))

# 2) 并发写同一文件：不出现半截/损坏 JSON，且最终是某次完整写入
def writer(i):
    for _ in range(50):
        app_mod.atomic_write_json(cfg, {"thread": i, "rand": os.urandom(8).hex()})
threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
for t in threads: t.start()
for t in threads: t.join()
with open(cfg, encoding="utf-8") as f:
    final = json.load(f)  # 若损坏会抛 JSONDecodeError
check("并发写后仍为合法 JSON", isinstance(final, dict) and "thread" in final)

# 3) sync.save_config 同样原子
sync_mod.atomic_write_json(cfg, {"from": "sync"})
with open(cfg, encoding="utf-8") as f:
    check("sync.atomic_write_json 正确", json.load(f)["from"] == "sync")
shutil.rmtree(d, ignore_errors=True)

print("\n=== R10 取消注册表加锁 ===")
# 并发 set/get/pop 同一 project_id 不应抛异常，且 Event 可取可用
pid = "proj_x"
errors = []
def worker():
    try:
        for _ in range(200):
            ev = threading.Event()
            with app_mod._cancel_lock:
                app_mod._DOWNLOAD_CANCEL[pid] = ev
            with app_mod._cancel_lock:
                got = app_mod._DOWNLOAD_CANCEL.get(pid)
            if got is not None:
                got.set()
            with app_mod._cancel_lock:
                app_mod._DOWNLOAD_CANCEL.pop(pid, None)
    except Exception as e:
        errors.append(repr(e))
ts = [threading.Thread(target=worker) for _ in range(6)]
for t in ts: t.start()
for t in ts: t.join()
check("并发访问取消注册表无异常", not errors)
check("锁对象存在且为 Lock", isinstance(app_mod._cancel_lock, threading.Lock))

print(f"\n结果: {ok} 通过, {fail} 失败")
sys.exit(1 if fail else 0)
