"""知识库检索（buganalyzer_kb_search）。

设计 §8：基于 frontmatter + 正文全文搜索，tags 精确过滤 + 症状关键词模糊匹配，
返回 Top-K（标题 + tags + 摘要 + 路径），由 AI 决定深入读哪个。
MVP 不引入向量库。

实现：有 ripgrep 时用它先筛候选文件（快），评分在进程内完成；无 rg 时纯 Python
回退（结果一致，只是慢）。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def _parse_frontmatter(text: str) -> dict[str, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip()
    return fm


def _norm_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    raw = raw.strip().strip("[]").strip()
    return [t.strip() for t in raw.split(",") if t.strip()]


def _candidate_files(query: str, cases_dir: Path) -> list[Path] | None:
    """rg -l 筛候选；rg 不存在或出错返回 None（调用方回退全量扫描）。"""
    rg = shutil.which("rg")
    if rg is None:
        return None
    try:
        proc = subprocess.run(
            [rg, "-i", "-l", "--", query, str(cases_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode == 1:  # 无匹配
        return []
    if proc.returncode != 0:
        return None
    return [Path(p) for p in proc.stdout.splitlines() if p]


def kb_search(
    query: str,
    tags: list[str] | None = None,
    top_k: int = 5,
    kb_dir: Path | None = None,
) -> list[dict]:
    """检索 knowledge-base/cases/，返回 Top-K 案例摘要。

    score = 查询词（空白切分、小写）在正文中的出现次数；tags 为「任一命中」过滤。
    """
    if kb_dir is None:
        from .paths import knowledge_base_dir

        kb_dir = knowledge_base_dir()
    cases_dir = kb_dir / "cases"
    if not cases_dir.is_dir():
        raise FileNotFoundError(f"知识库案例目录不存在: {cases_dir}")
    files = sorted(cases_dir.rglob("*.md"))
    if not files:
        return []

    candidates = _candidate_files(query, cases_dir)
    if candidates is not None:
        cand_set = {p.resolve() for p in candidates}
        files = [f for f in files if f.resolve() in cand_set]

    words = [w for w in re.split(r"\s+", query.strip().lower()) if w]
    scored: list[tuple[int, Path, dict[str, str], str]] = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        fm = _parse_frontmatter(text)
        if tags:
            file_tags = _norm_tags(fm.get("tags"))
            if not any(t in file_tags for t in tags):
                continue
        body = FRONTMATTER_RE.sub("", text)
        low = body.lower()
        score = sum(low.count(w) for w in words) if words else 1
        if score:
            scored.append((score, f, fm, text))

    scored.sort(key=lambda item: -item[0])
    results: list[dict] = []
    for score, f, fm, text in scored[: max(1, int(top_k))]:
        first_lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:6]
        results.append(
            {
                "title": fm.get("title", f.stem),
                "tags": _norm_tags(fm.get("tags")),
                "symptom": fm.get("symptom", ""),
                "root_cause": fm.get("root_cause", ""),
                "service": fm.get("service", ""),
                "env": fm.get("env", ""),
                "path": f.relative_to(kb_dir).as_posix(),
                "score": score,
                "summary": " ".join(first_lines)[:200],
            }
        )
    return results
