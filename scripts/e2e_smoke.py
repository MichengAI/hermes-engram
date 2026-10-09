"""端到端冒烟：隔离的临时 HERMES_HOME + Hermes 自己的加载器 + 真实 engram 可执行文件。

用法（需要 Hermes 的 venv Python）：
    python scripts/e2e_smoke.py --engram D:/Tools/engram/engram.exe --cwd D:/Repository/deepseek-harness-plugin/dsh-codex-ui
    python scripts/e2e_smoke.py --engram D:/Tools/engram/engram.exe --cwd <仓库目录> --write

- 默认只读：读真实 Engram 库，只输出字节数、耗时、召回提示和项目名，不打印记忆正文。
- --write：完整走一遍自动存取（会话注册、提问记录、主动保存、被动捕获、压缩存档、会话关闭），
  使用临时 ENGRAM_DATA_DIR，绝不写入真实库；结束后打印写入结果并删除临时库。
不会修改真实的 HERMES_HOME。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _engram_json(engram: str, data_dir: str, *args: str) -> str:
    env = {**os.environ, "ENGRAM_DATA_DIR": data_dir, "ENGRAM_CLOUD_AUTOSYNC": "0"}
    return subprocess.run([engram, *args], capture_output=True, env=env, timeout=30).stdout.decode("utf-8", "replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engram", required=True, help="engram 可执行文件路径")
    parser.add_argument("--cwd", required=True, help="模拟会话工作目录（应位于某个 git 仓库内）")
    parser.add_argument("--unbound-cwd", default="D:\\AI\\HermesData", help="未绑定任何项目的目录")
    parser.add_argument("--write", action="store_true", help="在临时 Engram 库里验证自动存取")
    parser.add_argument("--hermes-agent", default=str(Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "hermes-agent"))
    args = parser.parse_args()

    home = Path(tempfile.mkdtemp(prefix="hermes-engram-e2e-"))
    data_dir = tempfile.mkdtemp(prefix="engram-e2e-data-") if args.write else ""
    try:
        if args.write:
            os.environ["ENGRAM_DATA_DIR"] = data_dir  # provider 子进程继承：只写临时库
        shutil.copytree(REPO / "engram", home / "plugins" / "engram", ignore=shutil.ignore_patterns("__pycache__"))
        (home / "config.yaml").write_text(
            "memory:\n  provider: engram\nplugins:\n  engram:\n"
            f"    engram_path: '{args.engram}'\n"
            f"    auto_capture: {'true' if args.write else 'false'}\n", encoding="utf-8")
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
        print("注册的工具:", sorted(s["name"] for s in manager.get_all_tool_schemas()))
        manager.initialize_all(session_id="e2e-1", hermes_home=str(home), platform="desktop")
        try:
            if args.write:
                # 临时库是空的：先为 cwd 所在仓库建一个项目，模拟真实库里已有的目录绑定
                _seed_project(args.engram, data_dir, args.cwd)
                provider._catalog_at = 0  # type: ignore[attr-defined]  # 让 provider 重新拉项目目录表

            for label, sid, query in [
                ("仓库目录·首轮", "e2e-1", "看看兼容测试和 peer 范围"),
                ("仓库目录·追问", "e2e-1", "继续检查发布流程"),
            ]:
                started = time.monotonic()
                out = manager.prefetch_all(query, session_id=sid)
                ms = int((time.monotonic() - started) * 1000)
                marker = out.split("]", 1)[0] + "]" if out else ""
                proto = "含协议" if "engram_save" in out else "无协议"
                print(f"[{label}] bytes={len(out.encode('utf-8'))} ms={ms} {marker} {proto} 提示={manager.describe_recall()!r}")
                if args.write:
                    manager.sync_all(query, "回答", session_id=sid)

            if args.write:
                saved = json.loads(manager.handle_tool_call(
                    "engram_save", {"title": "e2e 决定", "content": "**What**: 端到端验证", "type": "decision"}))
                print("engram_save:", {k: saved.get(k) for k in ("id", "project", "error")})
                manager.on_delegation("调查", "结论\n## Key Learnings:\n1. 端到端被动捕获的一条经验", child_session_id="c1")
                manager.on_pre_compress([{"role": "user", "content": "看看兼容测试"},
                                         {"role": "assistant", "content": "兼容测试全部通过"}])
                manager.on_session_end([])
        finally:
            manager.shutdown_all()

        if args.write:
            print("--- 临时 Engram 库写入结果 ---")
            print(_engram_json(args.engram, data_dir, "context", "--all").strip()[:1500])
        return 0
    finally:
        shutil.rmtree(home, ignore_errors=True)
        if data_dir:
            shutil.rmtree(data_dir, ignore_errors=True)


def _seed_project(engram: str, data_dir: str, cwd: str) -> None:
    """在临时库里为 cwd 所在仓库注册一个项目（目录绑定），模拟真实库已有的项目。"""
    env = {**os.environ, "ENGRAM_DATA_DIR": data_dir, "ENGRAM_CLOUD_AUTOSYNC": "0"}
    proc = subprocess.Popen([engram, "mcp", "--tools=mem_session_start,mem_save,mem_session_end"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, cwd=cwd)
    assert proc.stdin is not None and proc.stdout is not None
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "seed", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "mem_session_start", "arguments": {"id": "seed", "directory": cwd}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "mem_save", "arguments": {"title": "seed", "content": "兼容测试 发布流程", "session_id": "seed"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "mem_session_end", "arguments": {"id": "seed"}}},
    ]
    for item in lines:
        proc.stdin.write((json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8"))
        proc.stdin.flush()
        if "id" in item:
            proc.stdout.readline()
    proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
