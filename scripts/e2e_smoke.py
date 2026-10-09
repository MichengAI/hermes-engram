"""端到端冒烟：在隔离的临时 HERMES_HOME 中，用 Hermes 自己的加载器加载插件，并读真实 Engram 库。

用法（需要 Hermes 的 venv Python）：
    python scripts/e2e_smoke.py --engram D:/Tools/engram/engram.exe --cwd D:/Repository/deepseek-harness-plugin/dsh-codex-ui

只输出字节数、耗时、召回提示和项目名，不打印记忆正文。不会修改真实的 HERMES_HOME。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engram", required=True, help="engram 可执行文件路径")
    parser.add_argument("--cwd", required=True, help="模拟会话工作目录（应绑定某个 Engram 项目）")
    parser.add_argument("--unbound-cwd", default="D:\\AI\\HermesData", help="未绑定任何项目的目录")
    parser.add_argument("--hermes-agent", default=str(Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "hermes-agent"))
    args = parser.parse_args()

    home = Path(tempfile.mkdtemp(prefix="hermes-engram-e2e-"))
    try:
        shutil.copytree(REPO / "engram", home / "plugins" / "engram", ignore=shutil.ignore_patterns("__pycache__"))
        (home / "config.yaml").write_text(
            "memory:\n  provider: engram\nplugins:\n  engram:\n"
            f"    engram_path: '{args.engram}'\n", encoding="utf-8")
        db = sqlite3.connect(home / "state.db")
        db.execute("create table sessions (id text primary key, cwd text)")
        db.executemany("insert into sessions values (?, ?)", [("e2e-1", args.cwd), ("e2e-2", args.unbound_cwd)])
        db.commit()
        db.close()

        os.environ["HERMES_HOME"] = str(home)
        sys.path.insert(0, args.hermes_agent)
        from agent.memory_manager import MemoryManager
        from plugins.memory import find_provider_dir, load_memory_provider

        print("provider 目录:", find_provider_dir("engram"))
        provider = load_memory_provider("engram")
        assert provider is not None, "Hermes 加载器没有加载出 provider"
        print("is_available:", provider.is_available())

        manager = MemoryManager()
        manager.add_provider(provider)
        manager.initialize_all(session_id="e2e-1", hermes_home=str(home), platform="desktop")
        try:
            for label, sid, query in [
                ("仓库目录·首轮", "e2e-1", "看看兼容测试和 peer 范围"),
                ("仓库目录·追问", "e2e-1", "继续检查发布流程"),
                ("未绑定目录·不点名", "e2e-2", "hermes 怎么接入 engram"),
                ("未绑定目录·点名", "e2e-2", "继续 dsh-codex-ui 项目，看看兼容测试"),
            ]:
                started = time.monotonic()
                out = manager.prefetch_all(query, session_id=sid)
                ms = int((time.monotonic() - started) * 1000)
                marker = out.split("]", 1)[0] + "]" if out else ""
                print(f"[{label}] bytes={len(out.encode('utf-8'))} ms={ms} {marker} 提示={manager.describe_recall()!r}")
        finally:
            manager.shutdown_all()
        return 0
    finally:
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
