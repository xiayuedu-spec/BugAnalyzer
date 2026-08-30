"""权限策略（config/policy.toml）+ ssh_run 命令白名单。

对应设计 §7：
- 策略是 allowlist（默认拒绝），只放行显式列出的能力，不维护 deny 黑名单；
- ssh_run 的命令（含参数前缀）必须命中 [ssh_run.whitelist]，参数独立传参、
  shell 转义由 ssh_client 负责；
- 放行判定是确定性代码，不依赖模型判断（双重校验的 Server 侧硬约束）。

决策语义：
- "allow"   ：在 allow_auto 中，自动放行
- "confirm" ：在 allow_confirm 中，Server 放行；交互确认由 Claude Code 权限层
              （settings.json）把关，属 Phase 2「确认弹窗」接入点
- "deny"    ：未放行，拒绝并说明原因
"""
from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass
from pathlib import Path


class PolicyError(Exception):
    """策略配置错误。"""


@dataclass(frozen=True)
class TierPolicy:
    tier: str
    allow_auto: tuple[str, ...]
    allow_confirm: tuple[str, ...]


@dataclass(frozen=True)
class Policy:
    tiers: dict[str, TierPolicy]
    ssh_whitelist_readonly: tuple[str, ...]
    ssh_whitelist_confirm: tuple[str, ...]

    # ---- ssh_run 白名单 ----

    def ssh_level(self, argv: list[str]) -> str | None:
        """argv（command + args）命中哪个白名单分组：confirm / readonly / None。

        先匹配 confirm（更具体、更严格），再匹配 readonly——例如白名单同时有
        `docker`（readonly）与 `docker exec`（confirm）时，`docker exec ...`
        归入 confirm。
        """
        for entry in self.ssh_whitelist_confirm:
            tokens = entry.split()
            if argv[: len(tokens)] == tokens:
                return "confirm"
        for entry in self.ssh_whitelist_readonly:
            tokens = entry.split()
            if argv[: len(tokens)] == tokens:
                return "readonly"
        return None

    # ---- 工具级决策 ----

    def decision(self, tool: str, level: str | None, tier: str) -> str:
        """对 (工具, 级别, 环境分级) 的放行决策：allow / confirm / deny。"""
        tp = self.tiers.get(tier)
        if tp is None:
            return "deny"
        for pattern in tp.allow_auto:
            if _match(pattern, tool, level):
                return "allow"
        for pattern in tp.allow_confirm:
            if _match(pattern, tool, level):
                return "confirm"
        return "deny"


def _match(pattern: str, tool: str, level: str | None) -> bool:
    """策略条目（如 `ssh_run:readonly:*`、`list_*`、`fetch_logs`）对工具+级别匹配。"""
    parts = pattern.split(":")
    if not fnmatch.fnmatch(tool, parts[0]):
        return False
    if len(parts) > 1 and not fnmatch.fnmatch(level or "", parts[1]):
        return False
    return True


def load_policy(path: Path) -> Policy:
    if not path.exists():
        raise PolicyError(
            f"策略文件不存在: {path}\n"
            "请从 config/policy.example.toml 复制为 config/policy.toml。"
        )
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    tiers: dict[str, TierPolicy] = {}
    for tier, raw in data.get("tier", {}).items():
        tiers[tier] = TierPolicy(
            tier=tier,
            allow_auto=tuple(str(x) for x in raw.get("allow_auto", [])),
            allow_confirm=tuple(str(x) for x in raw.get("allow_confirm", [])),
        )
    wl = data.get("ssh_run", {}).get("whitelist", {})
    return Policy(
        tiers=tiers,
        ssh_whitelist_readonly=tuple(str(x) for x in wl.get("readonly", [])),
        ssh_whitelist_confirm=tuple(str(x) for x in wl.get("confirm", [])),
    )
