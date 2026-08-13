"""R8(全量同步子进程清理) / R9(扫描阶段可取消) 集成测试。
直接调用 backend.app 与 backend.sync 中的函数，不依赖真实服务器。
"""
import os, sys, time, subprocess, tempfile, threading, shutil

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

print("== R9: _parse_find_record 解析 ==")
out = {}
sync_mod._parse_find_record(out, "/proj", b"100.0\t10\t./a.txt", False)
check("单条记录解析正确", out.get("/proj/a.txt") == (100.0, 10))
out2 = {}
sync_mod._parse_find_record(out2, "/proj", b"1.0\t2\t./c.log", False)
check("默认过滤 .log 扩展名", "/proj/c.log" not in out2)

print("== R9: _remote_stat_tree_fast 增量读取 + 取消 ==")
class FakeChannel:
    def close(self): self.closed = True
class FakeStdout:
    def __init__(self, data, cancel):
        self._chunks = [data[i:i+65536] for i in range(0, len(data), 65536)] or [b""]
        self._idx = 0
        self._cancel = cancel
        self._reads = 0
        self.channel = FakeChannel()
    def read(self, n):
        self._reads += 1
        if self._cancel is not None and self._cancel.is_set():
            return b""
        chunk = self._chunks[self._idx] if self._idx < len(self._chunks) else b""
        self._idx += 1
        # 第一次返回后立刻置位取消，模拟扫描中点 ×
        if self._reads == 1 and self._cancel is not None:
            self._cancel.set()
        return chunk
class FakeClient:
    def __init__(self, cancel): self._cancel = cancel
    def exec_command(self, cmd, timeout=None):
        return None, FakeStdout(DATA, self._cancel), None

records = [b"100.0\t10\t./a.txt", b"200.0\t20\t./sub/b.txt", b"300.0\t30\t./c.log"]
DATA = b"".join(r + b"\0" for r in records)

# 未取消：应返回 a.txt 和 sub/b.txt（c.log 被过滤）
res = sync_mod._remote_stat_tree_fast(FakeClient(None), "/proj", sync_all=False, cancel_event=None)
check("未取消返回正确文件数(2)", res is not None and len(res) == 2)
check("未取消含 a.txt", res is not None and "/proj/a.txt" in res)
check("未取消过滤 c.log", res is not None and "/proj/c.log" not in res)

# 取消：应返回 None
ev = threading.Event()
res2 = sync_mod._remote_stat_tree_fast(FakeClient(ev), "/proj", sync_all=False, cancel_event=ev)
check("扫描中点取消返回 None", res2 is None)
check("取消事件被置位", ev.is_set())

print("== R9: _local_stat_tree 取消 ==")
tmp = tempfile.mkdtemp()
for i in range(5):
    with open(os.path.join(tmp, f"f{i}.txt"), "w") as f: f.write("x")
ev2 = threading.Event()
ev2.set()
# 取消已置位 -> 一进入遍历即返回空
r_empty = sync_mod._local_stat_tree(tmp, cancel_event=ev2)
check("已置位取消返回空", r_empty == {})
# 未取消 -> 含 5 个文件
r_full = sync_mod._local_stat_tree(tmp, cancel_event=None)
check("未取消返回 5 个文件", len(r_full) == 5)
shutil.rmtree(tmp, ignore_errors=True)

print("== R9: _sftp_stat_tree 取消 ==")
class Entry:
    def __init__(self, name, is_dir):
        self.filename = name
        self.st_mode = 0o040000 if is_dir else 0o100000
        self.st_mtime = 0
        self.st_size = 0
class FakeSFTP:
    def listdir_attr(self, d):
        # 第一次调用返回若干条目，其中含取消入口
        if d == "/proj":
            return [Entry("a.txt", False), Entry("sub", True), Entry("b.txt", False)]
        return [Entry("x.txt", False)]
sftp_cancel = threading.Event()
r_sftp = sync_mod._sftp_stat_tree(FakeSFTP(), "/proj", cancel_event=sftp_cancel)
check("sftp 未取消返回多条", len(r_sftp) >= 1)
sftp_cancel.set()
r_sftp_c = sync_mod._sftp_stat_tree(FakeSFTP(), "/proj", cancel_event=sftp_cancel)
check("sftp 已置位取消返回空", r_sftp_c == {})

print("== R8: _kill_proc_tree 杀掉子进程树 ==")
proc = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(30)"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
time.sleep(0.5)
app_mod._kill_proc_tree(proc)
# 给系统一点时间回收
dead = proc.poll() is not None
for _ in range(20):
    if proc.poll() is not None:
        dead = True
        break
    time.sleep(0.1)
check("子进程被杀(无残留)", dead)

print(f"\n结果: {ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
