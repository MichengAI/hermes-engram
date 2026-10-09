"""持久会话身份与原生增强的 provider 集成回归。"""
from __future__ import annotations

import json
import sys

from conftest import FAKE_SERVER
from engram import EngramMemoryProvider
from test_capture import REPO, _flush, _formal_summary, _make, env
from test_native_runtime import server


def test_confirmed_session_identity_survives_provider_restart(tmp_path, env, monkeypatch):
    monkeypatch.setenv("FAKE_ENDED", "hermes-s1-renren-drama")
    p = _make(tmp_path)
    p.prefetch("检查完整兼容测试")
    identity = p._state("s1").engram_sessions["renren-drama"]
    # 模拟进程重启：只关连接，不伪造已收到 session_end。
    p.shutdown()
    restored = EngramMemoryProvider(config={"command": [sys.executable, str(FAKE_SERVER)]})
    try:
        restored.initialize("s1", hermes_home=str(tmp_path / "home"), platform="desktop")
        restored.prefetch("检查完整兼容测试")
        assert restored._state("s1").engram_sessions["renren-drama"] == identity
        assert (tmp_path / "home" / "plugins-state" / "engram.sqlite3").is_file()
    finally:
        restored.shutdown()


def test_branch_does_not_reuse_parent_registered_identity(tmp_path, env):
    p = _make(tmp_path, rows={"s1": REPO, "s2": REPO})
    try:
        p.prefetch("检查完整兼容测试")
        original = p._state("s1").engram_sessions["renren-drama"]
        p.on_session_switch("s2", parent_session_id="s1", reset=False)
        p.prefetch("检查分支完整兼容测试", session_id="s2")
        assert p._state("s2").engram_sessions["renren-drama"] != original
    finally:
        p.shutdown()


def test_native_satellite_preserves_explicit_target(server, tmp_path, env):
    from engram.native_runtime import NativeRuntime
    p = _make(tmp_path)
    p._native = NativeRuntime.for_test(server[0])
    try:
        p.prefetch("检查完整兼容测试")
        result = json.loads(p.handle_tool_call("engram_save", {
            "project": "no-dir-project", "title": "跨项目决定", "content": "正文"}))
        assert result.get("id") == 42
        payload = next(e[2] for e in server[1] if e[0] == "POST" and e[1] == "/sessions" and e[2]["project"] == "no-dir-project")
        assert payload["isolated"] is True and payload["directory"] == ""
        assert payload["ownership_mode"] == "project_owned"
    finally:
        p.shutdown()


def test_native_auto_capture_never_uses_isolated_session(server, tmp_path, env):
    """与 MCP 模式一致：被动捕获、提问、委派、摘要只写目录绑定的会话；卫星会话只服务显式工具。"""
    from engram.native_runtime import NativeRuntime
    p = _make(tmp_path)
    p._native = NativeRuntime.for_test(server[0])
    query = "请检查 dsh-codex-ui 的完整架构和测试结果"
    try:
        p.prefetch(query)  # 会话目录绑定 renren-drama，本轮只点名 dsh-codex-ui
        assert p._state("s1").confirmed_project == "dsh-codex-ui"
        p.on_post_tool_call(tool_name="terminal", session_id="s1", result="## Key Learnings:\n1. " + "Foreign repo output. " * 5)
        p.sync_turn(query, "回答")
        _flush(p)
        isolated = [e for e in server[1] if e[0] == "POST" and e[1] == "/sessions" and e[2].get("isolated")]
        writes = [e for e in server[1] if e[0] == "POST" and e[1] in ("/observations/passive", "/prompts")]
        assert isolated == [] and writes == []
    finally:
        p.shutdown()


def test_native_explicit_summary_is_not_compaction_upsert(server, tmp_path, env):
    from engram.native_runtime import NativeRuntime
    p = _make(tmp_path)
    p._native = NativeRuntime.for_test(server[0])
    try:
        p.prefetch("检查完整兼容测试")
        p.handle_tool_call("engram_session_summary", {"content": "## Goal\n主动总结"})
        p.on_session_switch("s1", reason="compression")
        p.sync_turn("继续", "完成", messages=[_formal_summary("Formal generated summary.")])
        _flush(p)
        bodies = [e[2] for e in server[1] if e[0] == "POST" and e[1] == "/observations"]
        explicit = next(b for b in bodies if "主动总结" in b["content"])
        archived = next(b for b in bodies if "Formal generated summary." in b["content"])
        assert "topic_key" not in explicit and explicit["title"] == "Session summary"
        assert archived["topic_key"] == "session/compaction-recovery"
        assert "compaction" not in explicit and "compaction" not in archived
    finally:
        p.shutdown()


def test_mcp_summary_never_receives_native_only_flag(tmp_path, env):
    p = _make(tmp_path)
    try:
        p.prefetch("检查完整兼容测试")
        p.on_session_switch("s1", reason="compression")
        p.sync_turn("继续", "完成", messages=[_formal_summary("Formal generated summary.")])
        _flush(p)
        assert all("compaction" not in c["arguments"] for c in env("mem_session_summary"))
    finally:
        p.shutdown()


def test_native_restart_recovers_writes_after_child_crash(server, tmp_path, env, monkeypatch):
    from engram.native_runtime import NativeRuntime
    from engram.mcp_client import McpError
    p = _make(tmp_path)
    p._native = NativeRuntime.for_test(server[0])
    try:
        p.prefetch("检查完整兼容测试")
        original = p._native.request
        calls = {"n": 0}
        def flaky(method, path, *args, **kwargs):
            if path == "/health" and calls["n"] == 0:
                calls["n"] += 1
                raise McpError("原生 Engram 服务已退出")
            return original(method, path, *args, **kwargs)
        monkeypatch.setattr(p._native, "request", flaky)
        first = json.loads(p.handle_tool_call("engram_save", {"title": "t", "content": "c"}))
        second = json.loads(p.handle_tool_call("engram_save", {"title": "t", "content": "c"}))
        assert "error" in first and second.get("id") == 42
    finally:
        p.shutdown()


def test_recover_save_tool_contract(server, tmp_path, env):
    from engram.native_runtime import NativeRuntime, UnknownWrite
    p = _make(tmp_path)
    p._native = NativeRuntime.for_test(server[0])
    try:
        p.prefetch("检查完整兼容测试")
        def unknown(*args, **kwargs):
            raise UnknownWrite("2f1c6f0e-4c0f-4a77-9a3a-9d1f0b8b2c11")
        p._native.save = unknown
        out = json.loads(p.handle_tool_call("engram_save", {"title": "t", "content": "c"}))
        assert out["outcome"] == "unknown" and out["operation_id"]
        missing = json.loads(p.handle_tool_call("engram_recover_save", {"operation_id": out["operation_id"]}))
        assert "error" in missing  # 假服务账本里没有：返回错误而不是重新保存
        assert not [e for e in server[1] if e[0] == "POST" and e[1] == "/observations"]
    finally:
        p.shutdown()


def test_readonly_native_hides_recover_tool():
    from engram import EngramMemoryProvider
    names = {s["name"] for s in EngramMemoryProvider(config={"native_http": True, "auto_capture": False}).get_tool_schemas()}
    assert "engram_recover_save" not in names


def test_compaction_recovery_notice_is_recalled_once(server, tmp_path, env):
    from engram.native_runtime import NativeRuntime
    p = _make(tmp_path)
    p._native = NativeRuntime.for_test(server[0])
    try:
        p.prefetch("检查完整兼容测试")
        p.on_session_switch("s1", reason="compression")
        p.sync_turn("继续", "完成", messages=[_formal_summary("Formal generated summary.")])
        _flush(p)
        first = p.prefetch("继续检查压缩后的恢复上下文")
        assert "Engram压缩恢复" in first and "服务器确认的压缩恢复上下文" in first
        assert "Engram压缩恢复" not in p.prefetch("继续检查压缩后的恢复上下文")
    finally:
        p.shutdown()
