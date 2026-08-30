"""日志拉取/搜索（buganalyzer_fetch_logs）的纯逻辑部分。

fetch_logs 通过 SSH 在目标环境执行固定形态的 `tail -n N <log_path>`：
- pattern / since 过滤在 Server 进程内完成，pattern 不进入远程 shell（无注入面）；
- log_path 来自可信配置（envs.toml），仅允许安全字符与通配符，经 validate_log_path 校验；
- 输出硬性截断（默认 ≤200 行、单行 ≤500 字符），避免撑爆模型上下文（设计 §5 日志工具约束）。

since 语义（启发式）：日志行首部时间戳（YYYY-MM-DD HH:MM:SS 等常见格式）按环境
timezone 解释后与 since 比较；解析失败的行不过滤（宁可多给，不误删证据）。
"""
from __future__ import annotations

import datetime as dt
import re
import zoneinfo

DEFAULT_MAX_LINES = 200
DEFAULT_MAX_LINE_LEN = 500
LOG_PATH_ALLOWED = re.compile(r"^[A-Za-z0-9_./:*-?]+$")
_TS_RE = re.compile(r"(\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}(?::\d{2})?)")


class LogError(ValueError):
    """日志工具参数/配置错误。"""


def validate_log_path(path: str) -> None:
    """log_path 仅允许字母数字 _ . / : * - ?（通配符用于远程 shell 展开）。"""
    if not LOG_PATH_ALLOWED.match(path):
        raise LogError(
            f"log_path 含不允许的字符: {path!r}（仅允许字母数字 _ . / : * - ?）"
        )


def build_tail_command(log_path: str, lines: int) -> str:
    """构造远程命令：固定形态 `tail -n N <log_path>`，路径来自可信配置。"""
    validate_log_path(log_path)
    return f"tail -n {int(lines)} {log_path}"


def parse_since(since: str | None, timezone: str) -> dt.datetime | None:
    """解析 since（ISO 或 'YYYY-MM-DD HH:MM'），返回环境时区的 naive datetime。

    时区数据缺失（如 Windows 无 IANA 库）时回退 UTC；解析失败返回 None（不过滤）。
    """
    if not since:
        return None
    try:
        tz = zoneinfo.ZoneInfo(timezone)
    except zoneinfo.ZoneInfoNotFoundError:
        tz = dt.timezone.utc
    s = since.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=None)
        except ValueError:
            continue
    try:
        aware = dt.datetime.fromisoformat(since)
        return aware.astimezone(tz).replace(tzinfo=None)
    except ValueError:
        return None


def _line_timestamp(line: str) -> dt.datetime | None:
    m = _TS_RE.search(line)
    if not m:
        return None
    raw = m.group(1).replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def filter_lines(
    lines: list[str],
    pattern: str | None,
    since: dt.datetime | None,
) -> list[str]:
    """pattern 正则过滤（大小写不敏感）+ since 时间过滤（启发式）。"""
    rx = re.compile(pattern, re.IGNORECASE) if pattern else None
    out: list[str] = []
    for line in lines:
        if rx is not None and not rx.search(line):
            continue
        if since is not None:
            ts = _line_timestamp(line)
            if ts is not None and ts < since:
                continue
        out.append(line)
    return out


def truncate_lines(
    lines: list[str],
    max_lines: int = DEFAULT_MAX_LINES,
    max_line_len: int = DEFAULT_MAX_LINE_LEN,
) -> tuple[list[str], bool]:
    """截断到 max_lines 行、单行 max_line_len 字符；返回 (lines, truncated)。"""
    truncated = len(lines) > max_lines
    lines = lines[-max_lines:]
    out: list[str] = []
    for line in lines:
        line = line.rstrip("\n")
        if len(line) > max_line_len:
            line = line[:max_line_len] + "…(截断)"
        out.append(line)
    return out, truncated


def filter_and_truncate(
    raw_lines: list[str],
    pattern: str | None,
    since: str | None,
    timezone: str,
    max_lines: int = DEFAULT_MAX_LINES,
) -> tuple[list[str], int, bool]:
    """组合过滤 + 截断，供 fetch_logs 使用；返回 (lines, matched, truncated)。"""
    since_dt = parse_since(since, timezone) if since else None
    matched = filter_lines(raw_lines, pattern, since_dt)
    lines, truncated = truncate_lines(matched, max_lines=max_lines)
    return lines, len(matched), truncated
