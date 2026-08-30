"""标准库测试 runner：cd mcp && python -m tests"""
from __future__ import annotations

import pathlib
import sys
import traceback

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
KB_IMPORT_SRC = pathlib.Path(__file__).resolve().parents[2] / "tools" / "kb_import" / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(KB_IMPORT_SRC))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import test_core  # noqa: E402


def main() -> int:
    tests = [getattr(test_core, n) for n in dir(test_core) if n.startswith("test_")]
    failed = 0
    for t in sorted(tests, key=lambda f: f.__name__):
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
    total = len(tests)
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
