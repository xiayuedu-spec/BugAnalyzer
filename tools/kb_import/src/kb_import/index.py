"""重建 knowledge-base/INDEX.md（设计 §8：索引自动维护，导入后自动重建）。"""
from __future__ import annotations

from pathlib import Path

from .frontmatter import parse_frontmatter


def rebuild_index(kb_dir: Path) -> int:
    """扫描 cases/ 与 playbooks/ 生成 INDEX.md；返回索引条目数。"""
    sections: list[str] = []
    total = 0
    for sub in ("cases", "playbooks"):
        dirp = kb_dir / sub
        if not dirp.is_dir():
            continue
        items: list[str] = []
        for f in sorted(dirp.rglob("*.md")):
            text = f.read_text(encoding="utf-8", errors="replace")
            fm = parse_frontmatter(text)
            rel = f"{sub}/{f.relative_to(dirp).as_posix()}"
            title = fm.get("title", f.stem)
            meta = []
            if fm.get("symptom"):
                meta.append(fm["symptom"])
            if fm.get("service"):
                meta.append(fm["service"])
            if fm.get("env"):
                meta.append(fm["env"])
            if fm.get("status") == "deprecated":
                meta.append("⚠️ deprecated")
            items.append(f"- [{title}]({rel})" + (f" — {'，'.join(meta)}" if meta else ""))
            total += 1
        sections.append(f"## {sub}/\n")
        sections.append("\n".join(items) if items else "（暂无）\n")
    content = (
        "# BugAnalyzer 知识库索引\n\n"
        "> 本文件由 `kb-import rebuild-index` 自动生成/重建，请勿手改。\n"
        "> 导入历史问题单：`kb-import import --file 问题单导出.csv`\n\n"
        + "\n\n".join(sections)
        + "\n"
    )
    (kb_dir / "INDEX.md").write_text(content, encoding="utf-8")
    return total
