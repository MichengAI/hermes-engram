"""把插件复制到 Hermes 用户插件目录：$HERMES_HOME/plugins/engram/。

只复制文件，不修改 config.yaml。启用需另外执行：
    hermes config set memory.provider engram
    hermes config set plugins.engram.engram_path D:/Tools/engram/engram.exe

用法：python scripts/install.py [--hermes-home 路径]
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def default_hermes_home() -> Path:
    explicit = os.environ.get("HERMES_HOME")
    if explicit:
        return Path(explicit)
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "hermes"
    return Path.home() / ".hermes"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-home", type=Path, default=default_hermes_home())
    args = parser.parse_args()

    target = args.hermes_home / "plugins" / "engram"
    if target.exists():
        # 以 . 开头：Hermes 发现逻辑会跳过，避免备份被当成另一个 provider
        backup = target.with_name(".engram.bak")
        shutil.rmtree(backup, ignore_errors=True)
        target.rename(backup)
        print(f"已备份旧版本到 {backup}")
    shutil.copytree(REPO / "engram", target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(f"已安装到 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
