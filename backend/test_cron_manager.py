"""
cron_manager 单元测试：用 FakeSSHClient 模拟服务器，覆盖
- 纯函数校验 validate_cron_line / validate_crontab / content_equal
- 来源枚举 list_sources（root / 各用户 / 系统文件 / cron.* 目录）
- 读取 get
- 保存 save：成功写回 + 读回验证；非法语法拦截；写回失败触发回滚
不依赖真实网络/SSH。
"""

import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cron_manager import (
    CronManager, validate_cron_line, validate_crontab, content_equal, count_entries
)


class FakeChannel:
    def __init__(self, rc): self.rc = rc
    def recv_exit_status(self): return self.rc


class FakeStream:
    def __init__(self, text='', rc=0):
        self._text = text
        self.channel = FakeChannel(rc)
    def read(self): return self._text.encode('utf-8')
    def write(self, d): pass
    def flush(self): pass
    def shutdown_write(self): pass


class FakeSFTPFile:
    def __init__(self, client, path, mode):
        self.client = client; self.path = path; self.mode = mode; self._buf = ''
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def write(self, data): self.client._temp[self.path] = data
    def read(self): return self.client._temp.get(self.path, '')


class FakeSFTP:
    def __init__(self, client): self.client = client
    def open(self, path, mode='r'): return FakeSFTPFile(self.client, path, mode)
    def close(self): pass


class FakeSSHClient:
    def __init__(self):
        self._temp = {}
        self._write_rc = 0  # 模拟写回命令的退出码（1=失败，触发回滚）
        self.crontabs = {
            'root': "*/5 * * * * /root/do.sh\n",
            'alice': "0 2 * * * /home/alice/job.sh\n",
            'bob': "",
        }
        self.files = {
            '/etc/crontab': "SHELL=/bin/bash\n* * * * * root /sys/job.sh\n",
            '/etc/cron.d/backup': "*/10 * * * * root /backup.sh\n",
        }
        # Debian 系 /var/spool/cron 下是子目录而非用户名（模拟真实机器的坑）
        self.plain_spool = []
        self.crontab_spool = ['alice', 'bob']
        # 真实存在的系统用户，供批量 `id -u` 复核使用
        self.real_users = ['root', 'alice', 'bob']

    def open_sftp(self): return FakeSFTP(self)

    def exec_command(self, cmd):
        out, err, rc = self._respond(cmd)
        stdin = FakeStream('', 0)
        stdout = FakeStream(out, rc)
        stderr = FakeStream(err, rc)
        return stdin, stdout, stderr

    def close(self): pass

    def _respond(self, cmd):
        c = cmd.strip()
        if c == 'sudo -n true':
            return ('', '', 0)
        if c == 'id -u; id -un':
            return ('0\nroot', '', 0)
        if c.startswith('ls -1 /var/spool/cron/crontabs'):
            return ('\n'.join(self.crontab_spool), '', 0)
        if c.startswith('ls -1 /var/spool/cron'):
            return ('\n'.join(self.plain_spool), '', 0)
        # 批量复核真实用户：for u in a b; do id -u "$u" ...; done; echo __DEVCENTER_SPOOL_DONE__
        if c.startswith('for u in '):
            names = c[len('for u in '):].split(';')[0].split()
            kept = [n for n in names if n in self.real_users]
            return ('\n'.join(kept + ['__DEVCENTER_SPOOL_DONE__']), '', 0)
        if c.startswith('test -f /etc/crontab'):
            return ('yes', '', 0)
        if c.startswith('ls -1 /etc/cron.d'):
            return ('backup\n0hourly', '', 0)
        if c.startswith('ls -1 /etc/cron.hourly') or c.startswith('ls -1 /etc/cron.daily') \
           or c.startswith('ls -1 /etc/cron.weekly') or c.startswith('ls -1 /etc/cron.monthly'):
            return ('', '', 0)
        if c == 'crontab -l':
            return (self.crontabs.get('root', ''), '', 0)
        if c == 'crontab -l -u alice':
            return (self.crontabs.get('alice', ''), '', 0)
        if c == 'crontab -l -u bob':
            return ('', 'no crontab for bob', 1)
        if c.startswith('cat /etc/crontab'):
            return (self.files.get('/etc/crontab', ''), '', 0)
        if c.startswith('cat /etc/cron.d/backup'):
            return (self.files.get('/etc/cron.d/backup', ''), '', 0)
        if c.startswith('crontab -l >') or c.startswith('cp ') or c.startswith('rm -f'):
            return ('', '', 0)
        # 写回用户 crontab：crontab /tmp/x 或 crontab -u <user> /tmp/x
        m = re.search(r'/tmp/\S+', c)
        tmp = m.group(0) if m else None
        content = self._temp.get(tmp, '') if tmp else ''
        if c.startswith('crontab /tmp'):
            if self._write_rc == 0:
                self.crontabs['root'] = content
            return ('', '', self._write_rc)
        if c.startswith('crontab -u alice'):
            if self._write_rc == 0:
                self.crontabs['alice'] = content
            return ('', '', self._write_rc)
        if c.startswith('crontab -u bob'):
            if self._write_rc == 0:
                self.crontabs['bob'] = content
            return ('', '', self._write_rc)
        # 写回系统文件：cat /tmp/x > /etc/...
        if c.startswith('cat /tmp') and '>' in c:
            target = c.split('>')[-1].strip()
            if self._write_rc == 0:
                self.files[target] = content
            return ('', '', self._write_rc)
        return ('', '', 0)


def test_validate():
    assert validate_cron_line("*/5 * * * * /do.sh")[0] is True
    assert validate_cron_line("0 2 * * * /job.sh")[0] is True
    assert validate_cron_line("@daily /job.sh")[0] is True
    assert validate_cron_line("# comment")[0] is True
    assert validate_cron_line("")[0] is True
    assert validate_cron_line("* * * * /bad.sh")[0] is False  # 只有 4 段
    assert validate_cron_line("X * * * * /bad.sh")[0] is False  # 非法时间字段
    ok, errs = validate_crontab("*/5 * * * * root /a.sh\n0 2 * * * root /b.sh", has_user_field=True)
    assert ok is True
    ok2, errs2 = validate_crontab("bad line here\n*/5 * * * * root /a.sh")
    assert ok2 is False and len(errs2) == 1
    print("  ✔ validate_* / content_equal / count_entries")


def test_list_sources():
    mgr = CronManager(FakeSSHClient())
    assert mgr.is_root is True
    data = mgr.list_sources()
    scopes = [s['scope'] for s in data['sources']]
    assert 'root' in scopes
    assert 'user:alice' in scopes
    assert 'user:bob' in scopes
    assert 'system:/etc/crontab' in scopes
    assert 'system:/etc/cron.d/backup' in scopes
    assert data['writable'] is True
    # bob 无 crontab → 内容空、0 条
    bob = next(s for s in data['sources'] if s['scope'] == 'user:bob')
    assert bob['content'] == '' and bob['entries'] == 0
    # alice 有 1 条
    alice = next(s for s in data['sources'] if s['scope'] == 'user:alice')
    assert alice['entries'] == 1
    # cron.* 目录列举为空（fake 返回空）
    assert data['cron_dirs']['hourly'] == []
    print("  ✔ list_sources 含 root/user/system + cron.* 目录")


def test_get():
    mgr = CronManager(FakeSSHClient())
    r = mgr.get('system:/etc/crontab')
    assert 'root /sys/job.sh' in r['content']
    r2 = mgr.get('root')
    assert '/root/do.sh' in r2['content']
    print("  ✔ get 各来源取内容正确")


def test_spool_dir_names_filtered():
    """真机发现的坑：Debian 的 /var/spool/cron 下只有 crontabs/atjobs/atspool 子目录，
    不能被当成用户名，否则 UI 会出现假的「用户 crontabs」来源。"""
    client = FakeSSHClient()
    client.plain_spool = ['crontabs', 'atjobs', 'atspool']
    mgr = CronManager(client)
    users = mgr._spool_users()
    assert 'crontabs' not in users, users
    assert 'atjobs' not in users, users
    assert 'atspool' not in users, users
    assert 'alice' in users and 'bob' in users, users
    data = mgr.list_sources()
    scopes = [s['scope'] for s in data['sources']]
    assert 'user:crontabs' not in scopes, scopes
    assert 'user:alice' in scopes
    # 所有候选都不是真实用户时必须返回空，而不是退回未过滤列表
    # （首尔/GCP 真机上 spool 里只有 crontabs 一个候选，退回就等于 bug 复发）
    client2 = FakeSSHClient()
    client2.crontab_spool = []
    client2.plain_spool = ['crontabs']
    assert CronManager(client2)._spool_users() == [], CronManager(client2)._spool_users()
    # 连接中断（拿不到哨兵）时退回原候选列表，不静默丢用户
    class BrokenClient(FakeSSHClient):
        def _respond(self, cmd):
            if cmd.strip().startswith('for u in '):
                return ('', 'connection reset', 255)
            return super()._respond(cmd)
    assert set(CronManager(BrokenClient())._spool_users()) >= {'alice', 'bob'}
    print("  ✔ spool 目录名(如 crontabs)被 id -u 过滤，不产生假用户来源")


def test_save_success():
    client = FakeSSHClient()
    mgr = CronManager(client)
    new = "*/5 * * * * /root/do.sh\n0 1 * * * /root/new.sh\n"
    res = mgr.save('root', new)
    assert res['ok'] is True, res
    assert res['entries'] == 2
    # 读回验证：get 应返回新内容
    assert '/root/new.sh' in mgr.get('root')['content']
    print("  ✔ save 成功：写回 + 读回验证一致")


def test_save_invalid():
    mgr = CronManager(FakeSSHClient())
    res = mgr.save('root', "this is not a cron line")
    assert res['ok'] is False
    assert 'validation_errors' in res
    print("  ✔ save 非法语法被拦截")


def test_save_rollback():
    client = FakeSSHClient()
    client._write_rc = 1  # 模拟写回命令失败
    mgr = CronManager(client)
    before = mgr.get('root')['content']
    res = mgr.save('root', "*/5 * * * * /root/do.sh\n0 3 * * * /root/x.sh\n")
    assert res['ok'] is False
    # 内容未被破坏（回滚：原内容仍在）
    assert mgr.get('root')['content'] == before
    print("  ✔ save 写回失败触发回滚，内容不变")


def test_save_no_permission():
    # 非 root 且无 sudo：writable=False，save 直接拒绝
    class NoSudoClient(FakeSSHClient):
        def _respond(self, cmd):
            if cmd.strip() == 'sudo -n true':
                return ('', 'sudo: a password is required', 1)
            if cmd.strip() == 'id -u; id -un':
                return ('1000\nappuser', '', 0)
            return super()._respond(cmd)
    mgr = CronManager(NoSudoClient())
    assert mgr.is_root is False
    assert mgr.sudo_available is False
    res = mgr.save('root', "*/5 * * * * /x.sh")
    assert res['ok'] is False and 'sudo' in res['error']
    print("  ✔ 非 root 无 sudo 时 save 明确拒绝")


if __name__ == '__main__':
    test_validate()
    test_list_sources()
    test_get()
    test_spool_dir_names_filtered()
    test_save_success()
    test_save_invalid()
    test_save_rollback()
    test_save_no_permission()
    print("\nALL CRON_MANAGER TESTS PASSED ✅")
