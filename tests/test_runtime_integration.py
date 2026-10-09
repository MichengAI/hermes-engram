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
