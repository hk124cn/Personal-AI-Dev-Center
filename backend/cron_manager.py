"""
服务器 cron（计划任务）管理

与 1Panel 复刻分析 docs/1panel-analysis.md §5.3.2 对齐：
- 来源分三类：① 用户级 crontab（Debian /var/spool/cron/crontabs/<user>、RHEL /var/spool/cron/<user>）
             ② root 的 crontab  ③ 系统级 /etc/crontab、/etc/cron.d/*（含 USER 字段）
- 写回护栏：备份 → 校验 → 回写 → 读回验证 → 失败回滚
- 非 root 需 sudo：连上先 `sudo -n true` 探测 NOPASSWD 能力，无则写操作直接报错

持有一个已连接的 paramiko SSHClient，不负责建连（建连复用 app.py 的 _connect_ssh_monitor）。
所有远程命令通过 exec_command；写文件内容走 SFTP（可靠传任意内容），再用 crontab/cat 落地。
"""

import os
import re
import time

# ---- 时间字段校验：允许 *、*/n、a、a-b、a-b/n、逗号组合、?(部分实现) ----
_TIME_FIELD = re.compile(
    r'^(\*|\?|\d+(-\d+)?(/\d+)?(,\d+(-\d+)?(/\d+)?)*|\*/\d+)$'
)
# 特殊 @keyword（@reboot/@yearly/@annually/@monthly/@weekly/@daily/@midnight/@hourly）
_SPECIAL = re.compile(r'^@(reboot|yearly|annually|monthly|weekly|daily|midnight|hourly)\b')


def validate_cron_line(line: str):
    """校验单行 crontab。返回 (ok, reason)。空行/注释行视为 ok。"""
    s = line.strip()
    if not s or s.startswith('#'):
        return True, ''
    if _SPECIAL.match(s):
        # @keyword cmd  —— 至少 2 段
        return (len(s.split()) >= 2, '' if len(s.split()) >= 2 else '特殊指令行缺少命令')
    parts = s.split()
    # 系统级（/etc/crontab、/etc/cron.d）第 6 字段是 USER，命令在之后 → 至少 6 段
    # 用户级 → 至少 5 段（5 时间字段 + 命令）
    if len(parts) < 5:
        return False, f'字段不足（仅 {len(parts)} 段，crontab 至少需 5 段时间+命令）'
    time_fields = parts[:5]
    for i, f in enumerate(time_fields):
        if not _TIME_FIELD.match(f):
            return False, f'第 {i+1} 个时间字段非法: "{f}"'
    return True, ''


def validate_crontab(content: str, has_user_field: bool = False):
    """校验整段 crontab 内容。返回 (ok, errors:list)"""
    errors = []
    for idx, line in enumerate(content.splitlines(), 1):
        if not line.strip() or line.strip().startswith('#'):
            continue
        # 系统级文件第 6 字段是 user，时间字段仍为前 5 个，校验逻辑不变
        ok, reason = validate_cron_line(line)
        if not ok:
            errors.append(f'第 {idx} 行: {reason}')
    return (len(errors) == 0, errors)


def _norm_lines(text: str):
    return [l.rstrip() for l in text.splitlines()]


def content_equal(a: str, b: str) -> bool:
    """忽略行尾空白与空行比较，用于写回验证"""
    la = [l for l in _norm_lines(a) if l.strip()]
    lb = [l for l in _norm_lines(b) if l.strip()]
    return la == lb


def count_entries(content: str) -> int:
    return sum(1 for l in content.splitlines()
               if l.strip() and not l.strip().startswith('#'))


class CronManager:
    def __init__(self, client, sudo: bool = False):
        self.client = client
        self.sudo = sudo
        self.is_root = False
        self.connection_user = ''
        self._probe()

    # ---------- 基础执行 ----------
    def _run(self, cmd, stdin_data=None):
        stdin, stdout, stderr = self.client.exec_command(cmd)
        if stdin_data is not None:
            try:
                stdin.write(stdin_data)
                stdin.flush()
                stdin.channel.shutdown_write()
            except Exception:
                pass
        out = stdout.read().decode('utf-8', 'replace')
        err = stderr.read().decode('utf-8', 'replace')
        try:
            rc = stdout.channel.recv_exit_status()
        except Exception:
            rc = -1
        return out, err, rc

    def _sp(self, cmd):
        """非 root 且具备 sudo 时加 `sudo -n` 前缀（非交互）"""
        return f"sudo -n {cmd}" if (self.sudo and not self.is_root) else cmd

    def _probe(self):
        """探测当前用户身份与 sudo 能力"""
        out, _, rc = self._run("id -u; id -un")
        lines = out.splitlines()
        if lines:
            try:
                self.is_root = (int(lines[0].strip()) == 0)
            except Exception:
                self.is_root = False
            if len(lines) > 1:
                self.connection_user = lines[1].strip()
        # sudo 探测：sudo -n true 退出 0 表示可免密
        _, _, rc2 = self._run("sudo -n true")
        self.sudo_available = (rc2 == 0)
        # sudo 标志仅在“非 root 但可 sudo”时启用写操作的 sudo 前缀
        self.sudo = self.sudo_available and not self.is_root

    def _write_temp(self, content: str) -> str:
        tmp = f"/tmp/cron.edit.{int(time.time()*1000)}.tmp"
        sftp = self.client.open_sftp()
        try:
            with sftp.open(tmp, 'w') as f:
                data = content if content.endswith('\n') else content + '\n'
                f.write(data)
        finally:
            sftp.close()
        return tmp

    # ---------- 来源解析 ----------
    def _spool_users(self):
        """列出拥有 crontab 的用户（来自 spool 目录），返回用户名列表"""
        users = []
        for base in ("/var/spool/cron/crontabs", "/var/spool/cron"):
            out, _, rc = self._run(f"ls -1 {base} 2>/dev/null")
            if rc == 0 and out.strip():
                for u in out.splitlines():
                    u = u.strip()
                    if u and u not in users:
                        users.append(u)
        return users

    def _read_file(self, path: str) -> str:
        cmd = self._sp(f"cat {path} 2>/dev/null")
        out, _, _ = self._run(cmd)
        return out

    def _list_system(self):
        """返回系统级来源列表：/etc/crontab + /etc/cron.d/*"""
        sources = []
        # /etc/crontab
        out, _, rc = self._run(self._sp("test -f /etc/crontab && echo yes || echo no"))
        if rc == 0 and 'yes' in out:
            sources.append({
                "scope": "system:/etc/crontab",
                "label": "/etc/crontab（含 USER 字段）",
                "has_user_field": True,
            })
        # /etc/cron.d/*
        out, _, rc = self._run(self._sp("ls -1 /etc/cron.d 2>/dev/null"))
        if rc == 0 and out.strip():
            for name in out.splitlines():
                name = name.strip()
                if name and not name.startswith('.'):
                    sources.append({
                        "scope": f"system:/etc/cron.d/{name}",
                        "label": f"/etc/cron.d/{name}（含 USER 字段）",
                        "has_user_field": True,
                    })
        return sources

    def _cron_dirs(self):
        """cron.hourly/daily/weekly/monthly 脚本目录（仅列举，非 crontab 格式）"""
        dirs = {}
        for d in ("hourly", "daily", "weekly", "monthly"):
            path = f"/etc/cron.{d}"
            out, _, rc = self._run(f"ls -1 {path} 2>/dev/null")
            if rc == 0 and out.strip():
                dirs[d] = [x.strip() for x in out.splitlines() if x.strip()]
            else:
                dirs[d] = []
        return dirs

    def list_sources(self):
        """枚举全部 cron 来源，返回结构化结果"""
        sources = []
        # 1) root 的 crontab（连上即当前用户；非 root 走 sudo）
        sources.append({
            "scope": "root",
            "label": f"root 用户 crontab（当前连接: {self.connection_user or '?'}）",
            "has_user_field": False,
        })
        # 2) 各用户 crontab
        for u in self._spool_users():
            if u == 'root':
                continue
            sources.append({
                "scope": f"user:{u}",
                "label": f"用户 {u} 的 crontab",
                "has_user_field": False,
            })
        # 3) 系统级
        sources.extend(self._list_system())
        # 各来源取内容 + 可写判定
        writable = self.is_root or self.sudo_available
        for s in sources:
            try:
                content = self.get(s["scope"])["content"]
            except Exception as e:
                content = ""
            s["content"] = content
            s["entries"] = count_entries(content)
            s["writable"] = writable
            s["error"] = None
        return {
            "connection_user": self.connection_user,
            "is_root": self.is_root,
            "sudo_available": self.sudo_available,
            "writable": writable,
            "sources": sources,
            "cron_dirs": self._cron_dirs(),
        }

    # ---------- 取内容 ----------
    def get(self, scope: str):
        """取某来源的 crontab 原始内容。返回 {scope,label,content,writable,error}"""
        if scope == "root":
            cmd = self._sp("crontab -l")
            out, err, rc = self._run(cmd)
            if rc != 0:
                # 无 crontab 时 crontab -l 返回非 0（stderr 含 no crontab）
                if 'no crontab' in err.lower() or 'no crontab' in out.lower():
                    return {"scope": scope, "label": "root 用户 crontab",
                            "content": "", "writable": self.is_root or self.sudo_available, "error": None}
                return {"scope": scope, "label": "root 用户 crontab",
                        "content": "", "writable": self.is_root or self.sudo_available,
                        "error": f"读取失败: {err.strip()}"}
            return {"scope": scope, "label": "root 用户 crontab",
                    "content": out, "writable": self.is_root or self.sudo_available, "error": None}
        if scope.startswith("user:"):
            user = scope[len("user:"):]
            cmd = self._sp(f"crontab -l -u {user}")
            out, err, rc = self._run(cmd)
            if rc != 0:
                if 'no crontab' in err.lower() or 'no crontab' in out.lower():
                    return {"scope": scope, "label": f"用户 {user} 的 crontab",
                            "content": "", "writable": self.is_root or self.sudo_available, "error": None}
                return {"scope": scope, "label": f"用户 {user} 的 crontab",
                        "content": "", "writable": self.is_root or self.sudo_available,
                        "error": f"读取失败: {err.strip()}"}
            return {"scope": scope, "label": f"用户 {user} 的 crontab",
                    "content": out, "writable": self.is_root or self.sudo_available, "error": None}
        if scope.startswith("system:"):
            path = scope[len("system:"):]
            content = self._read_file(path)
            return {"scope": scope, "label": path,
                    "content": content, "writable": self.is_root or self.sudo_available, "error": None}
        return {"scope": scope, "label": scope, "content": "",
                "writable": False, "error": f"未知来源: {scope}"}

    # ---------- 备份 ----------
    def backup(self, scope: str):
        """手动备份某来源，返回备份路径（在 /tmp 下）"""
        ts = time.strftime("%Y%m%d-%H%M%S")
        bak = f"/tmp/cron.bak.{ts}.{abs(hash(scope)) % 100000}"
        if scope == "root":
            self._run(self._sp(f"crontab -l > {bak} 2>/dev/null"))
        elif scope.startswith("user:"):
            user = scope[len("user:"):]
            self._run(self._sp(f"crontab -l -u {user} > {bak} 2>/dev/null"))
        elif scope.startswith("system:"):
            path = scope[len("system:"):]
            self._run(self._sp(f"cp {path} {bak} 2>/dev/null"))
        else:
            return {"ok": False, "error": f"未知来源: {scope}"}
        return {"ok": True, "backup": bak, "scope": scope}

    # ---------- 写回（含备份/校验/验证/回滚） ----------
    def save(self, scope: str, content: str):
        """保存某来源 crontab。流程：备份 → 校验 → 写回 → 读回验证 → 失败回滚。"""
        writable = self.is_root or self.sudo_available
        if not writable:
            return {"ok": False, "scope": scope,
                    "error": "当前 SSH 用户既非 root 也无 NOPASSWD sudo，无法写入 cron。请为密钥配置 sudo NOPASSWD 或以 root 连接。"}
        # 校验
        has_user_field = scope.startswith("system:")
        ok, errors = validate_crontab(content, has_user_field)
        if not ok:
            return {"ok": False, "scope": scope, "error": "校验未通过",
                    "validation_errors": errors}
        # 备份
        bk = self.backup(scope)
        backup_path = bk.get("backup") if bk.get("ok") else None
        # 写回
        try:
            tmp = self._write_temp(content)
            if scope == "root":
                cmd = self._sp(f"crontab {tmp}")
            elif scope.startswith("user:"):
                user = scope[len("user:"):]
                cmd = self._sp(f"crontab -u {user} {tmp}")
            elif scope.startswith("system:"):
                path = scope[len("system:"):]
                # cat 覆盖写，保留目标文件原有权限/属主
                cmd = self._sp(f"cat {tmp} > {path}")
            else:
                return {"ok": False, "scope": scope, "error": f"未知来源: {scope}", "backup": backup_path}
            _, err, rc = self._run(cmd)
            self._run(f"rm -f {tmp}")
            if rc != 0:
                # 写回失败 → 回滚
                rolled = self._restore(scope, backup_path)
                return {"ok": False, "scope": scope, "error": f"写回失败: {err.strip()}",
                        "backup": backup_path, "rolled_back": rolled}
        except Exception as e:
            self._run(f"rm -f {tmp}")
            rolled = self._restore(scope, backup_path)
            return {"ok": False, "scope": scope, "error": f"写回异常: {e}",
                    "backup": backup_path, "rolled_back": rolled}
        # 读回验证
        try:
            new_content = self.get(scope)["content"]
        except Exception as e:
            new_content = ""
        if not content_equal(content, new_content):
            rolled = self._restore(scope, backup_path)
            return {"ok": False, "scope": scope,
                    "error": "写回后读回不一致，已自动从备份恢复",
                    "backup": backup_path, "rolled_back": rolled}
        return {"ok": True, "scope": scope, "backup": backup_path,
                "entries": count_entries(content), "rolled_back": False}

    def _restore(self, scope: str, backup_path: str):
        """从备份恢复某来源。返回是否成功。"""
        if not backup_path:
            return False
        try:
            tmp = self._write_temp(
                self._read_file(backup_path) if scope.startswith("system:") else
                self._run(self._sp(f"cat {backup_path}"))[0]
            )
            if scope == "root":
                cmd = self._sp(f"crontab {tmp}")
            elif scope.startswith("user:"):
                user = scope[len("user:"):]
                cmd = self._sp(f"crontab -u {user} {tmp}")
            elif scope.startswith("system:"):
                path = scope[len("system:"):]
                cmd = self._sp(f"cat {tmp} > {path}")
            else:
                return False
            _, _, rc = self._run(cmd)
            self._run(f"rm -f {tmp}")
            return rc == 0
        except Exception:
            return False
