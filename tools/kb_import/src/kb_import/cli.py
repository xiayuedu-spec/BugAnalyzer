"""kb-import 命令行入口。

用法：
    kb-import import --file 问题单导出.csv [--out-dir <目录>] [--force]
    kb-import rebuild-index [--kb-dir <目录>]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    env = os.environ.get("BUGANALYZER_HOME")
    return Path(env).resolve() if env else Path.cwd().resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kb-import",
        description="问题单导出文件 → 案例 Markdown + 索引重建（设计 §8）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    imp = sub.add_parser("import", help="导入问题单导出文件（CSV）")
    imp.add_argument("--file", required=True, help="CSV 文件路径（utf-8）")
    imp.add_argument("--out-dir", default=None, help="案例输出目录，默认 <仓库根>/knowledge-base/cases")
    imp.add_argument("--force", action="store_true", help="重复导入时覆盖（默认跳过）")

    idx = sub.add_parser("rebuild-index", help="重建 knowledge-base/INDEX.md")
    idx.add_argument("--kb-dir", default=None, help="知识库目录，默认 <仓库根>/knowledge-base")

    args = parser.parse_args(argv)
    root = _repo_root()
    kb_dir = Path(args.kb_dir) if getattr(args, "kb_dir", None) else root / "knowledge-base"

    if args.cmd == "import":
        from .importer import import_csv

        out_dir = Path(args.out_dir) if args.out_dir else kb_dir / "cases"
        try:
            result = import_csv(Path(args.file), out_dir, force=args.force)
        except (FileNotFoundError, ValueError) as exc:
            print(f"导入失败: {exc}", file=sys.stderr)
            return 2
        for f in result.created:
            print(f"  创建 {f}")
        for tid, reason in result.skipped:
            print(f"  跳过 {tid}（{reason}）")
        for lineno, reason in result.failed:
            print(f"  失败 第{lineno}行：{reason}", file=sys.stderr)
        if result.created:
            n = _rebuild(kb_dir)
            print(f"已重建 {kb_dir / 'INDEX.md'}（{n} 条）")
        print(f"完成：创建 {len(result.created)}，跳过 {len(result.skipped)}，失败 {len(result.failed)}")
        return 0 if result.ok else 1

    # rebuild-index
    n = _rebuild(kb_dir)
    print(f"已重建 {kb_dir / 'INDEX.md'}（{n} 条）")
    return 0


def _rebuild(kb_dir: Path) -> int:
    from .index import rebuild_index

    return rebuild_index(kb_dir)


if __name__ == "__main__":
    raise SystemExit(main())
