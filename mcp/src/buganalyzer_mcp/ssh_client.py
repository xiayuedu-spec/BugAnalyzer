"""SSH 客户端：复用本地 ~/.ssh/config、进程内连接池、非交互执行、强制超时。

对应设计 §5「SSH 执行层」：
- 跳板机/堡垒机：通过 ~/.ssh/config 的 ProxyCommand 透传支持；
- 连接复用：进程内按环境缓存长连接；
- 非交互执行：get_pty=False；
- 强制 timeout：通道读写超时，超时报错而不是卡住工作流。

paramiko 惰性导入：纯逻辑测试/无 SSH 场景不需要它。
"""
from __future__ import annotations

import shlex
import threading

_PARAMIKO = None


def _paramiko():
    global _PARAMIKO
    if _PARAMIKO is None:
        try:
            import paramiko  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - 依赖缺失提示
            raise RuntimeError(
                "缺少依赖 paramiko，请先安装：cd mcp && uv sync"
                "（或 pip install paramiko）"
            ) from exc
        _PARAMIKO = paramiko
    return _PARAMIKO


class SshError(Exception):
    """SSH 连接/执行错误。"""


def split_ssh(ssh: str) -> tuple[str | None, str, int]:
    """把 `user@host[:port]` 拆成 (user, host, port)。"""
    user, host, port = None, ssh, 22
    if "@" in ssh:
        user, host = ssh.rsplit("@", 1)
    if ":" in host:
        host, _, ps = host.rpartition(":")
        try:
            port = int(ps)
        except ValueError:
            pass
    return user, host, port


class SshPool:
    """进程内连接池：同一环境复用长连接（对应设计 §5 连接复用）。"""

    def __init__(self) -> None:
        self._clients: dict[str, object] = {}
        self._lock = threading.Lock()

    def get(self, env) -> object:
        with self._lock:
            if env.name in self._clients:
                return self._clients[env.name]
            client = self._connect(env)
            self._clients[env.name] = client
            return client

    def _connect(self, env):
        pm = _paramiko()
        user, host, port = split_ssh(env.ssh)
        info: dict = {}
        cfg = pm.SSHConfig()
        try:
            with open(pm.util.config_file(), encoding="utf-8") as fh:
                cfg.parse(fh)
            info = cfg.lookup(host) or {}
        except (OSError, ValueError):
            info = {}
        kwargs: dict = {
            "hostname": info.get("hostname", host),
            "port": int(info.get("port", port)),
            "username": info.get("user", user),
            "timeout": env.timeout,
            "banner_timeout": env.timeout,
            "auth_timeout": env.timeout,
            "look_for_keys": True,
            "allow_agent": True,
        }
        identity = info.get("identityfile")
        if identity:
            kwargs["key_filename"] = identity if isinstance(identity, list) else [identity]
        proxycommand = info.get("proxycommand")
        if proxycommand:  # 跳板机/堡垒机（~/.ssh/config 的 ProxyCommand）
            kwargs["sock"] = pm.ProxyCommand(proxycommand)
        client = pm.SSHClient()
        client.set_missing_host_key_policy(pm.AutoAddPolicy())
        client.load_system_host_keys()
        try:
            client.connect(**kwargs)
        except Exception as exc:
            raise SshError(f"SSH 连接失败 [{env.ssh}]: {exc}") from exc
        return client

    def run(self, env, argv: list[str], timeout: int | None = None) -> str:
        """执行命令：argv 逐项 shell 转义后拼接（白名单 + 转义，杜绝注入）。"""
        cmd = " ".join(shlex.quote(a) for a in argv)
        return self._exec(env, cmd, timeout or env.timeout)

    def run_command(self, env, cmd: str, timeout: int | None = None) -> str:
        """执行固定形态的命令串——仅供 fetch_logs 使用（命令由 Server 构造、
        log_path 来自可信配置且经 validate_log_path 校验）。"""
        return self._exec(env, cmd, timeout or env.timeout)

    def _exec(self, env, cmd: str, timeout: int) -> str:
        client = self.get(env)
        try:
            chan = client.get_transport().open_session(timeout=timeout)
            chan.settimeout(timeout)
            chan.exec_command(cmd)
            stdout = b""
            while True:
                try:
                    chunk = chan.recv(65536)
                except Exception as exc:  # socket.timeout 等
                    chan.close()
                    raise SshError(f"命令执行超时（>{timeout}s）: {cmd[:200]}") from exc
                if not chunk:
                    break
                stdout += chunk
            stderr = b""
            while True:
                chunk = chan.recv_stderr(65536)
                if not chunk:
                    break
                stderr += chunk
            rc = chan.recv_exit_status()
        except SshError:
            raise
        except Exception as exc:
            raise SshError(f"命令执行失败: {exc}") from exc
        if rc != 0:
            msg = (stderr or stdout).decode("utf-8", errors="replace").strip()
            raise SshError(f"命令执行失败 (rc={rc}): {msg[:500]}")
        return stdout.decode("utf-8", errors="replace")
