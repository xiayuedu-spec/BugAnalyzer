"""环境清单解析（config/envs.toml）。

对应设计 §7「环境清单分级」：tier 枚举校验 + prod 条目提示（Phase 1 由
server 侧拒绝连接 prod）。
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

VALID_TIERS = ("test", "staging", "prod")
VALID_MODES = ("vm", "docker", "k8s")


class EnvConfigError(Exception):
    """环境清单配置错误（缺文件、字段非法等）。"""


@dataclass(frozen=True)
class Env:
    name: str
    tier: str
    ssh: str
    mode: str
    services: tuple[str, ...]
    timeout: int = 30
    timezone: str = "UTC"
    exec_user: str | None = None  # Phase 2：容器内/目标机执行用户
    sudo: bool = False            # Phase 2：是否需要 sudo
    log_path: str | None = None   # 服务日志路径（可用 * ? 通配符）
    log_paths: dict[str, str] = field(default_factory=dict)  # 按服务覆盖 log_path

    @property
    def is_prod(self) -> bool:
        return self.tier == "prod"

    def log_path_for(self, service: str) -> str | None:
        return self.log_paths.get(service) or self.log_path


def load_envs(path: Path) -> dict[str, Env]:
    """解析 envs.toml，返回 {环境名: Env}。

    `[env.test-order]` 是点分表头，TOML 解析后嵌套在顶层 `env` 表下。
    """
    if not path.exists():
        raise EnvConfigError(
            f"环境清单不存在: {path}\n"
            "请从 config/envs.example.toml 复制为 config/envs.toml 并填写你的环境。"
        )
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    tables = data.get("env", {})
    if not isinstance(tables, dict) or not tables:
        raise EnvConfigError(
            f"环境清单为空或格式不对: {path}\n"
            "请按 [env.<名称>] 表头填写，参考 config/envs.example.toml。"
        )
    envs: dict[str, Env] = {}
    for name, raw in tables.items():
        tier = str(raw.get("tier", "")).lower()
        if tier not in VALID_TIERS:
            raise EnvConfigError(f"[env.{name}] tier 非法: {tier!r}（允许 {VALID_TIERS}）")
        mode = str(raw.get("mode", "vm")).lower()
        if mode not in VALID_MODES:
            raise EnvConfigError(f"[env.{name}] mode 非法: {mode!r}（允许 {VALID_MODES}）")
        if "ssh" not in raw:
            raise EnvConfigError(f"[env.{name}] 缺少必填字段 ssh")
        services = tuple(str(s) for s in raw.get("services", []))
        log_paths = {str(k): str(v) for k, v in raw.get("log_paths", {}).items()}
        envs[name] = Env(
            name=name,
            tier=tier,
            ssh=str(raw["ssh"]),
            mode=mode,
            services=services,
            timeout=int(raw.get("timeout", 30)),
            timezone=str(raw.get("timezone", "UTC")),
            exec_user=raw.get("exec_user"),
            sudo=bool(raw.get("sudo", False)),
            log_path=raw.get("log_path"),
            log_paths=log_paths,
        )
    return envs
