"""真实原生增强冒烟：临时 Git 仓库、临时库与回环子进程，精确回读 SQLite。"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _owners(path: Path) -> str:
    """只读列出命令行包含该临时目录的进程，便于确认是不是自己启动的子进程。"""
    script = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.CommandLine -like '*{path.name}*' }} | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine | Format-List"
    )
    completed = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (completed.stdout or completed.stderr or "").strip()


def _release(path: Path) -> None:
    """尽力删除临时目录。Windows 杀毒扫描可能短时拒绝删除，不把清理失败当成验证失败。"""
    def _writable(function, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        function(target)
    for _ in range(16):
        try:
            if path.exists():
                shutil.rmtree(path, onerror=_writable)
            if not path.exists():
                return
        except PermissionError:
            time.sleep(0.5)
    locked = []
    for current, _dirs, files in os.walk(path):
        for name in files:
            target = Path(current) / name
            try:
                with target.open("a+b"):
                    pass
            except OSError as exc:
                locked.append(f"{target.name}:{getattr(exc, 'winerror', type(exc).__name__)}")
    print(json.dumps({"cleanup": "deferred", "path": str(path), "locked": locked[:8], "owners": _owners(path)}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engram", required=True)
    parser.add_argument("--expect", choices=("save-result", "reject-save-result"), default="save-result")
    parser.add_argument("--hermes-agent", default=str(Path(os.environ["LOCALAPPDATA"]) / "hermes/hermes-agent"))
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, args.hermes_agent)
    from engram import EngramMemoryProvider
    from agent.context_compressor import SUMMARY_PREFIX, HISTORICAL_TASK_HEADING, _SUMMARY_END_MARKER
    from e2e_smoke import _seed_project
    scratch = Path(os.environ["LOCALAPPDATA"]) / "hermes/cache/scratch"
    root = Path(tempfile.mkdtemp(prefix="engram-native-e2e-", dir=scratch))
    provider = None
    old_data = os.environ.get("ENGRAM_DATA_DIR")
    try:
        home, data, repo, other = [root / name for name in ("home", "data", "native-a", "native-b")]
        for path in (home, data, repo, other):
            path.mkdir()
        os.environ["ENGRAM_DATA_DIR"] = str(data)
        for path in (repo, other):
            subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
            _seed_project(args.engram, str(data), str(path))
        with sqlite3.connect(home / "state.db") as conn:
            conn.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY,cwd TEXT)")
            conn.execute("INSERT INTO sessions VALUES(?,?)", ("native-s1", str(repo)))
        config = {"engram_path": args.engram, "native_http": True, "write_timeout": 8}
        provider = EngramMemoryProvider(config=config)
        provider.initialize("native-s1", hermes_home=str(home), platform="desktop")
        query = "请检查 native-a 的完整架构与恢复机制"
        assert provider.prefetch(query)
        assert provider._native is not None
        health = provider._native._ready(4)
        assert health["capabilities"].get("root_session_resume") is True, "核心未声明根恢复能力"
        if args.expect == "reject-save-result":
            rejected = json.loads(provider.handle_tool_call("engram_save", {
                "project": "native-b", "title": "Must not land", "content": "Satellite evidence <private>NATIVE_SECRET</private>", "type": "decision"}))
            assert "不支持保存结果查询" in json.dumps(rejected, ensure_ascii=False), rejected
            provider.shutdown()
            provider = None
            with sqlite3.connect(f"{(data / 'engram.db').as_uri()}?mode=ro", uri=True) as conn:
                secret = conn.execute("SELECT COUNT(*) FROM observations WHERE content LIKE '%NATIVE_SECRET%'").fetchone()[0]
            assert secret == 0
            print(json.dumps({"core": health["version"], "save_result": "rejected", "secret_absent": True}, ensure_ascii=False))
            return
        before = provider._state("native-s1").engram_sessions["native-a"]
        provider.sync_turn(query, "回答")
        provider._join_writer(10)
        saved = json.loads(provider.handle_tool_call("engram_save", {
            "project": "native-b", "title": "Native satellite verification", "content": "Satellite evidence <private>NATIVE_SECRET</private>", "type": "decision"}))
        assert saved.get("id", 0) > 0 and saved["project"] == "native-b", saved
        satellite = provider._state("native-s1").satellite_sessions["native-b"]
        assert satellite != before and "native-b" not in provider._state("native-s1").engram_sessions
        for content in ("## Goal\nNative explicit summary one", "## Goal\nNative explicit summary two"):
            out = json.loads(provider.handle_tool_call("engram_session_summary", {"content": content}))
            assert out.get("id", 0) > 0, out
        provider.on_session_switch("native-s1", reason="compression")
        formal = {"role": "assistant", "_compressed_summary": True, "content":
            SUMMARY_PREFIX + "\n" + HISTORICAL_TASK_HEADING + "\nNative formal summary.\n" + _SUMMARY_END_MARKER}
        provider.sync_turn("继续", "回答", messages=[formal])
        provider._join_writer(10)
        assert list(provider.compaction_status().values()) == ["saved"]
        recall = provider.prefetch("请继续检查 native-a 的压缩恢复机制")
        assert "Engram压缩恢复" in recall
        provider.on_session_end([formal])
        provider._join_writer(10)
        provider.shutdown()
        # 同 Hermes ID 再次启动：采用服务端 continuation，不自选随机 UUID。
        provider = EngramMemoryProvider(config=config)
        provider.initialize("native-s1", hermes_home=str(home), platform="desktop")
        assert provider.prefetch(query)
        after = provider._state("native-s1").engram_sessions["native-a"]
        assert after.startswith(before + ":resume:") and after != before, (before, after)
        provider.on_session_end([])
        provider._join_writer(10)
        provider.shutdown()
        provider = None
        with sqlite3.connect(f"{(data / 'engram.db').as_uri()}?mode=ro", uri=True) as conn:
            content, project, sid = conn.execute("SELECT content,project,session_id FROM observations WHERE id=?", (saved["id"],)).fetchone()
            assert "NATIVE_SECRET" not in content and "[REDACTED]" in content
            assert project == "native-b" and sid == satellite
            assert conn.execute("SELECT COUNT(*) FROM observations WHERE type='session_summary' AND content='Native formal summary.'").fetchone()[0] == 1
            explicit = conn.execute("SELECT topic_key FROM observations WHERE type='session_summary' AND content LIKE '%Native explicit summary%'").fetchall()
            assert len(explicit) == 2 and all(row[0] in (None, "") for row in explicit), explicit  # 主动总结各自独立
            archived = conn.execute("SELECT topic_key FROM observations WHERE content='Native formal summary.'").fetchone()[0]
            assert archived == "session/compaction-recovery", archived
            assert conn.execute("SELECT COUNT(*) FROM user_prompts WHERE project='native-a'").fetchone()[0] >= 1
            assert conn.execute("SELECT COUNT(*) FROM sessions WHERE ended_at IS NULL").fetchone()[0] == 0
        print(json.dumps({"core": health["version"], "satellite_project": project, "resumed": after != before,
            "formal_summary_once": True, "private_redacted": True, "sessions_closed": True}, ensure_ascii=False))
    finally:
        if provider is not None:
            provider.shutdown()
        if old_data is None:
            os.environ.pop("ENGRAM_DATA_DIR", None)
        else:
            os.environ["ENGRAM_DATA_DIR"] = old_data
        _release(root)


if __name__ == "__main__":
    main()
