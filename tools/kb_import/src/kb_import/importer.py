"""问题单行数据 → 案例 Markdown（幂等导入）。

设计 §8：
- 每个历史问题单 = 一个标准案例，related_mr 让 AI 能顺藤摸瓜找到修改代码；
- 幂等：以 ticket_id 为唯一键，重复导入跳过（--force 覆盖）；
- 转换产出人可读 Markdown，业务组 review 后 commit 入库，非黑盒写入。

字段映射为骨架默认值；拿到真实导出样例后按 §11 未决事项调整。
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from .frontmatter import frontmatter_value

# 期望的 CSV 列（DictReader 按表头名取，缺失的列视为空）
EXPECTED_COLUMNS = (
    "ticket_id",   # 问题单号（必填，幂等键）
    "title",       # 标题（必填）
    "symptom",     # 症状/现象
    "root_cause",  # 定位结论/根因
    "service",     # 服务
    "env",         # 环境
    "severity",    # 级别
    "tags",        # 标签（逗号分隔）
    "related_mr",  # 修复 MR 链接
    "created",     # 日期（YYYY-MM-DD）
    "evidence",    # 定位过程（证据链）
    "conclusion",  # 修复方案
    "verification",  # 验证方法
    "reuse",       # 复用提示
)
REQUIRED = ("ticket_id", "title")


@dataclass
class ImportResult:
    created: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[int, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


def import_csv(csv_path: Path, out_dir: Path, force: bool = False) -> ImportResult:
    if not csv_path.exists():
        raise FileNotFoundError(f"导入文件不存在: {csv_path}")
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError("CSV 无表头（需要列: " + ", ".join(EXPECTED_COLUMNS) + "）")
        rows = [dict(row) for row in reader]
    return import_rows(rows, out_dir, force)


def import_rows(rows: list[dict], out_dir: Path, force: bool = False) -> ImportResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    result = ImportResult()
    existing = _index_ticket_ids(out_dir)
    for i, row in enumerate(rows, start=2):  # 行号从 2 开始（第 1 行是表头）
        # 行内列数多于表头时 DictReader 会产生 None 键，直接忽略
        clean = {k.strip(): (v or "").strip() for k, v in row.items() if k is not None}
        missing = [c for c in REQUIRED if not clean.get(c)]
        if missing:
            result.failed.append((i, f"缺少必填列: {missing}"))
            continue
        ticket_id = clean["ticket_id"]
        if ticket_id in existing and not force:
            result.skipped.append((ticket_id, "已存在（--force 可覆盖）"))
            continue
        fname = f"imported-{ticket_id}-{_slug(clean['title'])}.md"
        (out_dir / fname).write_text(render_case(clean), encoding="utf-8")
        existing.add(ticket_id)
        result.created.append(fname)
    return result


def render_case(row: dict[str, str]) -> str:
    tags = _norm_tags(row.get("tags"))
    return f"""---
title: {row['title']}
tags: [{', '.join(tags)}]
symptom: {row.get('symptom', '')}
root_cause: {row.get('root_cause', '')}
service: {row.get('service', '')}
env: {row.get('env', '')}
severity: {row.get('severity', '')}
created: {row.get('created', '')}
source: ticket
related_mr: {row.get('related_mr', '')}
ticket_id: {row['ticket_id']}
status: active
verified_at: {row.get('created', '')}
---

## 现象

{row.get('symptom', '')}

## 定位过程（证据链）

{row.get('evidence', '')}

## 根因

{row.get('root_cause', '')}

## 修复方案（含 MR）

{row.get('conclusion', '')}
{row.get('related_mr', '')}

## 验证方法

{row.get('verification', '')}

## 复用提示

{row.get('reuse', '')}
"""


def _index_ticket_ids(cases_dir: Path) -> set[str]:
    ids: set[str] = set()
    for f in cases_dir.rglob("*.md"):
        tid = frontmatter_value(f.read_text(encoding="utf-8", errors="replace"), "ticket_id")
        if tid:
            ids.add(tid)
    return ids


def _slug(title: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", title).strip("-").lower()
    return (s[:40] or "case").rstrip("-")


def _norm_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    raw = raw.strip().strip("[]").strip()
    return [t.strip() for t in raw.split(",") if t.strip()]
