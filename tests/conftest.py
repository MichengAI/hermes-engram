"""测试公共配置：把本机 Hermes 源码目录和仓库根目录加入 sys.path。

插件运行时由 Hermes 加载，依赖 ``agent.memory_provider`` 等宿主模块；
测试直接引用宿主源码，保证接口与真实 Hermes 一致。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _hermes_agent_dir() -> Path:
    """宿主源码目录：优先 HERMES_AGENT_DIR，其次默认安装位置。"""
    explicit = os.environ.get("HERMES_AGENT_DIR")
    if explicit:
        return Path(explicit)
    home = os.environ.get("HERMES_HOME") or os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes")
    return Path(home) / "hermes-agent"


for _path in (REPO_ROOT, _hermes_agent_dir()):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

FAKE_SERVER = REPO_ROOT / "tests" / "fake_engram_mcp.py"
