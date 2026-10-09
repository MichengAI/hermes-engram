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

    scratch = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "hermes" / "cache" / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="hermes-engram-e2e-", dir=scratch))
    data_dir = tempfile.mkdtemp(prefix="engram-e2e-data-", dir=scratch) if args.write else ""
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
        saved_id = 0
        try:
            if args.write:
                # 临时库是空的：先为 cwd 所在仓库建一个项目，模拟真实库里已有的目录绑定
                _seed_project(args.engram, data_dir, args.cwd)
                comparison = home / "comparison-project"
                comparison.mkdir()
                subprocess.run(["git", "init", str(comparison)], check=True, capture_output=True)
                _seed_project(args.engram, data_dir, str(comparison))
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
                assert manager.flush_pending(timeout=10), "回合同步未完成"
                saved = json.loads(manager.handle_tool_call(
                    "engram_save", {"title": "e2e 决定", "content": "**What**: 端到端验证 <private>E2E_SECRET</private>", "type": "decision"}))
                assert saved.get("id", 0) > 0 and not saved.get("error"), saved
                saved_id = int(saved["id"])
                print("engram_save:", {k: saved.get(k) for k in ("id", "project", "error")})
                from hermes_cli.plugins import has_hook
                from model_tools import _emit_post_tool_call_hook
                assert has_hook("post_tool_call"), "provider 加载器未注册工具捕获 hook"
                from types import SimpleNamespace
                from tools.delegate_tool_results import _notify_memory_manager
                parent = SimpleNamespace(_memory_manager=manager)
                project = saved["project"]
                before_projects = {p["name"] for p in getattr(provider, "_catalog")}
                for query in (f"比较 {project} 和 comparison-project 的完整架构", "project: unknown-project 请检查完整架构"):
                    assert manager.prefetch_all(query, session_id="e2e-1") == ""
                    rejected = "## Key Learnings:\n1. E2E rejected turn must not persist into the previously confirmed project."
                    _emit_post_tool_call_hook(function_name="terminal", function_args={}, session_id="e2e-1",
                        tool_call_id="e2e-rejected", result=rejected)
                    _notify_memory_manager([{"task_index": 0, "summary": rejected}], [{"goal": "reject"}], {}, parent)
                    manager.sync_all(query, "回答", session_id="e2e-1")
                    assert manager.flush_pending(timeout=10)
                    assert json.loads(manager.handle_tool_call("engram_save", {"title": "rejected", "content": "rejected"})).get("error")
                _verify_delayed_sync(manager, provider)
                manager.prefetch_all("继续检查完整的兼容测试并说明结果", session_id="e2e-1")
                assert getattr(provider, "_state")("e2e-1").confirmed_project == project
                assert {p["name"] for p in getattr(provider, "_catalog")} == before_projects
                sync_body = "## Key Learnings:\n1. E2E synchronous wrapper capture verifies decoded multiline summaries in isolated storage. <private>E2E_SECRET</private>"
                sync_entries = [{"task_index": 0, "status": "completed", "summary": sync_body, "duration_seconds": 1.0}]
                # 宿主同步路径先通知 memory，再把 results 包装成 JSON 字符串返回工具 hook。
                _notify_memory_manager(sync_entries, [{"goal": "verify synchronous capture"}], {}, parent)
                _emit_post_tool_call_hook(function_name="delegate_task", function_args={}, session_id="e2e-1",
                    tool_call_id="e2e-tool-1", result=json.dumps({"results": sync_entries, "total_duration_seconds": 1.0}))
                # 单独工具正文路径：避免 on_delegation 先捕获掩盖 JSON 换行转义问题。
                tool_body = "## Key Learnings:\n1. E2E tool wrapper capture proves real Engram extraction from decoded JSON results. <private>E2E_SECRET</private>"
                _emit_post_tool_call_hook(function_name="delegate_task", function_args={}, session_id="e2e-1",
                    tool_call_id="e2e-tool-2", result=json.dumps({"results": [{"task_index": 0, "status": "completed",
                    "summary": tool_body, "duration_seconds": 1.0}], "total_duration_seconds": 1.0}))
                # 默认后台路径的工具返回只含 dispatched；完成正文只能走 memory.on_delegation。
                _emit_post_tool_call_hook(function_name="delegate_task", function_args={}, session_id="e2e-1",
                    tool_call_id="e2e-tool-3", result=json.dumps({"status": "dispatched", "mode": "background",
                    "count": 1, "delegation_id": "e2e-bg", "goals": ["verify background capture"], "note": "accepted"}))
                async_body = "## Key Learnings:\n1. E2E asynchronous completion capture survives registered tool hooks and persists only completed summaries. <private>E2E_SECRET</private>"
                _notify_memory_manager([{"task_index": 0, "status": "completed", "summary": async_body}],
                                       [{"goal": "verify background capture"}], {}, parent)
                manager.on_pre_compress([{"role": "user", "content": "看看兼容测试"},
                                         {"role": "assistant", "content": "兼容测试全部通过"}])
                # 测试夹具按宿主正式格式构造摘要；不调用在线模型、不冒充真实 LLM 生成。
                from agent.context_compressor import SUMMARY_PREFIX, HISTORICAL_TASK_HEADING, _SUMMARY_END_MARKER
                formal = {"role": "assistant", "_compressed_summary": True,
                    "content": f"{SUMMARY_PREFIX}\n\n{HISTORICAL_TASK_HEADING}\nE2E formal compression summary <private>E2E_SECRET</private>\n\n{_SUMMARY_END_MARKER}"}
                manager.on_session_switch("e2e-1", parent_session_id="e2e-1", reset=False, reason="compression")
                for _ in range(2):
                    manager.sync_all("继续", "完成", session_id="e2e-1", messages=[formal])
                manager.sync_all("[System: generated internal input]", "回答", session_id="e2e-1")
                assert manager.flush_pending(timeout=10), "压缩后同步未完成"
                getattr(provider, "_join_writer")(timeout=10)
                statuses = getattr(provider, "compaction_status")()
                assert list(statuses.values()) == ["saved"], statuses
                print("压缩归档状态:", list(statuses.values()))
                manager.on_session_end([])
        finally:
            manager.shutdown_all()

        if args.write:
            print("--- 临时 Engram 库写入结果 ---")
            _verify_isolated_db(data_dir, saved_id)
            _verify_first_project(provider, home, data_dir, args.engram)
        return 0
    finally:
        shutil.rmtree(home, ignore_errors=True)
        if data_dir:
            shutil.rmtree(data_dir, ignore_errors=True)


def _verify_delayed_sync(manager, provider) -> None:
    """宿主已经排队但 provider 尚未执行时，后续合法轮不能授权旧拒绝正文。"""
    import threading
    entered, release = threading.Event(), threading.Event()
    original = provider.sync_turn
    def delayed(user, assistant, **kwargs):
        entered.set()
        assert release.wait(5), "宿主同步队列测试等待超时"
        original(user, assistant, **kwargs)
    provider.sync_turn = delayed
    try:
        rejected = "project: unknown-project 请检查延迟同步的来源归属和测试结果"
        assert manager.prefetch_all(rejected, session_id="e2e-1") == ""
        manager.sync_all(rejected, "回答", session_id="e2e-1")
        assert entered.wait(5), "宿主同步队列未启动"
        manager.prefetch_all("继续检查完整的项目架构和测试结果", session_id="e2e-1")
        release.set()
        assert manager.flush_pending(timeout=10)
        provider._join_writer(timeout=10)
    finally:
        release.set()
        manager.flush_pending(timeout=10)
        provider.sync_turn = original


def _verify_first_project(previous, home: Path, data_dir: str, engram: str) -> None:
    """真实空绑定仓库首次登记，不先种项目，不访问远端网络。"""
    repo = home / "fresh-local-folder"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    "https://example.invalid/acme/e2e-canonical-project.git"], check=True, capture_output=True)
    with sqlite3.connect(home / "state.db") as conn:
        conn.execute("INSERT INTO sessions VALUES (?, ?)", ("e2e-fresh", str(repo)))
    provider = type(previous)(config={"engram_path": engram})
    provider.initialize("e2e-fresh", hermes_home=str(home), platform="desktop")
    query = "请检查新仓库的完整项目架构和测试结果"
    try:
        out = provider.prefetch(query, session_id="e2e-fresh")
        assert "项目=e2e-canonical-project" in out, "项目名不是 Engram 解析出的远端仓库名"
        provider.sync_turn(query, "回答", session_id="e2e-fresh")
        provider._join_writer(timeout=10)
        assert provider._state("e2e-fresh").engram_sessions.get("e2e-canonical-project")
        provider.on_session_end([])
    finally:
        provider.shutdown()
    db = next(Path(data_dir).glob("*.db"))
    with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT project, ended_at FROM sessions WHERE id LIKE ?", ("hermes-e2e-fresh-%",)).fetchall()
        prompts = conn.execute("SELECT project, content FROM user_prompts WHERE content = ?", (query,)).fetchall()
    assert len(rows) == 1 and rows[0][0] == "e2e-canonical-project" and rows[0][1]
    assert prompts == [("e2e-canonical-project", query)]
    assert any(p["name"] == "e2e-canonical-project" for p in provider._catalog)
    print("首次登记精确回读通过: canonical 项目名、目录绑定、提问归属与会话关闭均确认")


def _verify_isolated_db(data_dir: str, saved_id: int) -> None:
    """精确回读刚写的记录，不用调用成功代替持久化验证。"""
    dbs = list(Path(data_dir).glob("*.db"))
    assert len(dbs) == 1, f"临时库文件不唯一: {dbs}"
    with sqlite3.connect(f"{dbs[0].as_uri()}?mode=ro", uri=True) as conn:
        observations = conn.execute("SELECT id, type, content, project, session_id FROM observations").fetchall()
        prompts = conn.execute("SELECT content FROM user_prompts").fetchall()
        ended = conn.execute("SELECT ended_at FROM sessions WHERE id LIKE 'hermes-%'").fetchall()
    assert any(row[0] == saved_id and "[REDACTED]" in row[2] for row in observations)
    summaries = [row for row in observations if "E2E formal compression summary" in row[2]]
    assert len(summaries) == 1 and "[REDACTED]" in summaries[0][2], summaries
    saved = next(row for row in observations if row[0] == saved_id)
    for marker in ("E2E synchronous wrapper capture", "E2E tool wrapper capture", "E2E asynchronous completion capture"):
        rows = [row for row in observations if marker in row[2]]
        assert len(rows) == 1, f"{marker} 捕获未落库或重复: {rows}"
        assert "[REDACTED]" in rows[0][2] and rows[0][3:] == saved[3:], "捕获脱敏或项目/会话边界不符"
    assert prompts and all("generated internal input" not in row[0] and row[0] != "继续" for row in prompts)
    assert all("E2E rejected turn" not in row[2] for row in observations)
    assert all("comparison-project" not in row[0] and "unknown-project" not in row[0] for row in prompts)
    assert "E2E_SECRET" not in json.dumps([observations, prompts], ensure_ascii=False)
    assert ended and all(row[0] for row in ended), "会话未关闭"
    print(f"SQLite 精确回读通过: observations={len(observations)} prompts={len(prompts)} summaries={len(summaries)}; 同步去重、JSON 工具正文、异步完成各 1 条，private 脱敏、项目/会话边界、合成跳过、摘要去重、会话关闭已确认")


def _seed_project(engram: str, data_dir: str, cwd: str) -> None:
    """在临时库里为 cwd 所在仓库注册一个项目（目录绑定），模拟真实库已有的项目。"""
    env = {**os.environ, "ENGRAM_DATA_DIR": data_dir, "ENGRAM_CLOUD_AUTOSYNC": "0"}
    proc = subprocess.Popen([engram, "mcp", "--tools=mem_session_start,mem_save,mem_session_end"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, cwd=cwd)
    assert proc.stdin is not None and proc.stdout is not None
    seed_id = "seed-" + Path(cwd).name
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "seed", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "mem_session_start", "arguments": {"id": seed_id, "directory": cwd}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "mem_save", "arguments": {"title": "seed", "content": "兼容测试 发布流程", "session_id": seed_id}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "mem_session_end", "arguments": {"id": seed_id}}},
    ]
    for item in lines:
        proc.stdin.write((json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8"))
        proc.stdin.flush()
        if "id" in item:
            proc.stdout.readline()
    proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
