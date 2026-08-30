"""能力包路径解析。

优先级：
1. 环境变量 BUGANALYZER_HOME（业务组把能力包放在非标准位置、或 .mcp.json 被复制到业务仓库时使用）
2. 从本文件位置向上推导（src/buganalyzer_mcp/paths.py -> 仓库根），不依赖 cwd
"""
from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    env = os.environ.get("BUGANALYZER_HOME")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[3]


def config_dir() -> Path:
    return repo_root() / "config"


def knowledge_base_dir() -> Path:
    return repo_root() / "knowledge-base"
