"""MCP Server 入口：注册 buganalyzer_* 工具（Phase 1）。

工具清单（设计 §5，全部带 buganalyzer_ 前缀防撞名）：
- buganalyzer_list_environments
- buganalyzer_list_services
- buganalyzer_ssh_run      —— 命令白名单 + 分级授权（Server 侧硬约束）
- buganalyzer_fetch_logs   —— 固定形态 tail + 本地过滤 + 输出截断
- buganalyzer_kb_search    —— 知识库案例检索

Phase 1 硬边界（设计 §9）：拒绝连接 tier=prod 的环境。

核心逻辑（*_core）与 FastMCP 装饰解耦，便于无 SSH 的纯逻辑测试。
"""
from __future__ import annotations

import shlex
import threading

from . import env_registry, kb_tools, log_tools, paths, policy
from .env_registry import Env, EnvConfigError
from .kb_tools import kb_search as kb_search_impl
from .policy import Policy, PolicyError
from .ssh_client import SshError, SshPool

CONFIRM_GATE_NOTE = (
    "confirm 级操作已由 Server 放行；交互确认由 Claude Code 权限层"
    "（settings.json）把关，属 Phase 2「确认弹窗」接入点。"
)


class _State:
    """懒加载的环境清单/策略 + 连接池（进程内单例）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.envs: dict[str, Env] | None = None
        self.policy: Policy | None = None
        self.pool: SshPool = SshPool()

    def load(self) -> tuple[dict[str, Env], Policy]:
        with self._lock:
            if self.envs is None:
                self.envs = env_registry.load_envs(paths.config_dir() / "envs.toml")
                self.policy = policy.load_policy(paths.config_dir() / "policy.toml")
        return self.envs, self.policy


_state = _State()


# ---------------------------------------------------------------- 核心逻辑

def _require_env(envs: dict[str, Env], name: str) -> Env:
    if name not in envs:
        raise ValueError(
            f"环境 {name!r} 不在环境清单中。可用：{sorted(envs)}"
            "（config/envs.toml）"
        )
    return envs[name]


def _guard_phase1(env: Env) -> None:
    """Phase 1 硬边界：拒绝连接生产环境（设计 §9）。"""
    if env.is_prod:
        raise ValueError(
            f"Phase 1 不接入生产环境：{env.name}（tier=prod 的连接被 Server 拒绝，"
            "见设计文档 §9）"
        )


def list_environments_core(state: _State) -> list[dict]:
    envs, _ = state.load()
    return [
        {
            "name": e.name,
            "tier": e.tier,
            "mode": e.mode,
            "services": list(e.services),
            "prod": e.is_prod,
        }
        for e in sorted(envs.values(), key=lambda x: x.name)
    ]


def list_services_core(state: _State, env: str) -> list[str]:
    envs, _ = state.load()
    e = _require_env(envs, env)
    return list(e.services)


def ssh_run_core(
    state: _State,
    env: str,
    command: str,
    args: list[str] | None = None,
) -> dict:
    envs, pol = state.load()
    e = _require_env(envs, env)
    _guard_phase1(e)
    if not command or not command.strip():
        raise ValueError("command 不能为空")

    argv = [command] + list(args or [])
    if any("\x00" in a for a in argv):
        raise ValueError("参数含非法字符（NUL）")

    level = pol.ssh_level(argv)
    if level is None:
        raise ValueError(
            f"命令不在白名单: {command!r}（policy.toml [ssh_run.whitelist]，"
            "只读命令可申请加入 readonly，有影响操作加入 confirm）"
        )
    decision = pol.decision("ssh_run", level, e.tier)
    if decision == "deny":
        raise ValueError(
            f"策略拒绝: ssh_run:{level} 在 {e.tier} 环境未放行"
            "（policy.toml，allowlist 默认拒绝）"
        )

    out = state.pool.run(e, argv, timeout=e.timeout)
    return {
        "env": e.name,
        "tier": e.tier,
        "command": " ".join(shlex.quote(a) for a in argv),
        "decision": "auto" if decision == "allow" else "confirm",
        "note": CONFIRM_GATE_NOTE if decision == "confirm" else None,
        "output": out,
    }


def fetch_logs_core(
    state: _State,
    env: str,
    service: str,
    pattern: str | None = None,
    since: str | None = None,
    tail: int = 200,
) -> dict:
    envs, pol = state.load()
    e = _require_env(envs, env)
    _guard_phase1(e)
    if pol.decision("fetch_logs", None, e.tier) == "deny":
        raise ValueError(f"策略拒绝: fetch_logs 在 {e.tier} 环境未放行（policy.toml）")
    if e.services and service not in e.services:
        raise ValueError(
            f"服务 {service!r} 不在环境 {env} 的 services 清单 {list(e.services)}"
        )
    log_path = e.log_path_for(service)
    if not log_path:
        raise ValueError(
            f"环境 {env} 未配置 {service} 的日志路径：请在 envs.toml 添加"
            " log_path（或 log_paths.<service>）"
        )
    tail = max(1, min(int(tail), 5000))
    n = max(tail, 2000) if since else tail  # since 需要更多行做时间过滤
    cmd = log_tools.build_tail_command(log_path, n)
    try:
        raw = state.pool.run_command(e, cmd, timeout=e.timeout)
    except SshError as exc:
        raise ValueError(str(exc)) from exc

    lines, matched, truncated = log_tools.filter_and_truncate(
        raw.splitlines(), pattern, since, e.timezone
    )
    return {
        "env": e.name,
        "service": service,
        "tail": tail,
        "pattern": pattern,
        "since": since,
        "command": cmd,
        "matched": matched,
        "returned": len(lines),
        "truncated": truncated,
        "lines": lines,
    }


def kb_search_core(
    state: _State,
    query: str,
    tags: list[str] | None = None,
    top_k: int = 5,
    kb_dir=None,
) -> list[dict]:
    del state  # 检索不依赖环境/策略
    if not query or not query.strip():
        raise ValueError("query 不能为空")
    if kb_dir is None:
        kb_dir = paths.knowledge_base_dir()
    return kb_search_impl(query, tags=tags, top_k=top_k, kb_dir=kb_dir)


# ---------------------------------------------------------------- MCP 注册

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("buganalyzer")


@mcp.tool()
def buganalyzer_list_environments() -> list[dict]:
    """列出环境清单中的可用环境及分级（tier/mode/services/prod）。"""
    return list_environments_core(_state)


@mcp.tool()
def buganalyzer_list_services(env: str) -> list[str]:
    """列出环境上的服务（来自 envs.toml 静态清单）。"""
    return list_services_core(_state, env)


@mcp.tool()
def buganalyzer_ssh_run(
    env: str, command: str, args: list[str] | None = None
) -> dict:
    """在目标环境执行白名单内的命令。

    command 必须命中 policy.toml [ssh_run.whitelist]（按前缀匹配，如 tail / jps /
    docker exec）；args 作为独立参数传递并做 shell 转义，不做字符串拼接。
    Phase 1 拒绝连接 tier=prod 的环境。
    """
    return ssh_run_core(_state, env, command, args)


@mcp.tool()
def buganalyzer_fetch_logs(
    env: str,
    service: str,
    pattern: str | None = None,
    since: str | None = None,
    tail: int = 200,
) -> dict:
    """拉取/搜索服务日志（tail 最近 N 行 + pattern/since 过滤）。

    pattern 为正则（大小写不敏感）；since 按环境 timezone 解释
    （'YYYY-MM-DD HH:MM:SS' 或 ISO）；输出最多 200 行、单行截断。
    日志路径取 envs.toml 的 log_path（或 log_paths.<service>）。
    """
    return fetch_logs_core(_state, env, service, pattern, since, tail)


@mcp.tool()
def buganalyzer_kb_search(
    query: str, tags: list[str] | None = None, top_k: int = 5
) -> list[dict]:
    """检索知识库案例：输入症状/关键词，返回 Top-K 相关案例（标题+tags+摘要+路径）。"""
    return kb_search_core(_state, query, tags, top_k)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
