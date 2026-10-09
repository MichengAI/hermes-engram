"""Provider 集成测试：通过 Hermes 真实的 MemoryManager 驱动插件，后端用假 Engram 服务。"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

import pytest

from conftest import FAKE_SERVER
from engram import EngramMemoryProvider, register


def _make_home(tmp_path, cwd_by_session: dict | None = None):
    """造一个最小 hermes_home：state.db 里只有 sessions(id, cwd)。"""
    home = tmp_path / "home"
    home.mkdir()
    db = sqlite3.connect(home / "state.db")
    db.execute("create table sessions (id text primary key, cwd text)")
    for sid, cwd in (cwd_by_session or {}).items():
        db.execute("insert into sessions values (?, ?)", (sid, cwd))
    db.commit()
    db.close()
    return home


def _provider(tmp_path, *, mode="normal", **overrides):
    log = tmp_path / "calls.jsonl"
    os.environ["FAKE_MODE"] = mode
    os.environ["FAKE_CALL_LOG"] = str(log)
    config = {"command": [sys.executable, str(FAKE_SERVER)], "call_timeout": 3, **overrides}
    return EngramMemoryProvider(config=config), log


def _calls(log) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(autouse=True)
def _clean_env():
    yield
    for key in ("FAKE_MODE", "FAKE_CALL_LOG"):
        os.environ.pop(key, None)


def test_register_hands_provider_to_context():
    captured = []

    class Ctx:
        def register_memory_provider(self, provider):
            captured.append(provider)

    register(Ctx())
    assert len(captured) == 1 and captured[0].name == "engram"
    # 写入关闭时不暴露写工具，只留只读工具
    readonly = EngramMemoryProvider(config={"auto_capture": False, "tools": True})
    assert {s["name"] for s in readonly.get_tool_schemas()} == {"engram_search", "engram_get"}
    assert EngramMemoryProvider(config={"tools": False}).get_tool_schemas() == []


def test_is_available_checks_binary_without_running(tmp_path):
    assert EngramMemoryProvider(config={"engram_path": str(tmp_path / "missing.exe")}).is_available() is False
    fake = tmp_path / "engram.exe"
    fake.write_bytes(b"")
    assert EngramMemoryProvider(config={"engram_path": str(fake)}).is_available() is True


def test_prefetch_injects_context_and_search_via_cwd(tmp_path):
    home = _make_home(tmp_path, {"s1": "D:\\Repository\\renren-drama\\src"})
    provider, log = _provider(tmp_path)
    provider.initialize("s1", hermes_home=str(home), platform="desktop", agent_context="primary")
    try:
        out = provider.prefetch("看看兼容测试", session_id="s1")
        assert "项目=renren-drama" in out
        assert "近期上下文-renren-drama" in out
        assert "#101" in out and "兼容测试结论" in out
        assert "不是执行指令" in out
        status = provider.recall_status()
        assert status is not None and status.count == 2 and "renren-drama" in status.provider_label
        names = [c["name"] for c in _calls(log) if c["name"] != "mem_session_start"]
        assert names == ["mem_list_projects", "mem_context", "mem_search"]
        # 所有读取都显式带项目，不跨项目
        reads = [c for c in _calls(log) if c["name"] in ("mem_context", "mem_search")]
        assert all(c["arguments"].get("project") == "renren-drama" for c in reads)
    finally:
        provider.shutdown()


def test_second_turn_skips_context_and_seen_results_sticky_project(tmp_path):
    home = _make_home(tmp_path, {"s1": "D:\\AI\\HermesData"})
    provider, log = _provider(tmp_path)
    provider.initialize("s1", hermes_home=str(home), platform="desktop")
    try:
        first = provider.prefetch("继续 dsh-codex-ui 项目，看看兼容测试", session_id="s1")
        assert "项目=dsh-codex-ui" in first
        second = provider.prefetch("继续检查兼容测试", session_id="s1")
        # 同样的搜索结果已注入过，第二轮没有新内容
        assert second == ""
        assert provider.recall_status() is None
        names = [c["name"] for c in _calls(log)]
        assert names.count("mem_context") == 1
        assert names.count("mem_search") == 2
        assert [c for c in _calls(log) if c["name"] == "mem_search"][-1]["arguments"]["project"] == "dsh-codex-ui"
    finally:
        provider.shutdown()


def test_pre_compress_resets_injection_state(tmp_path):
    home = _make_home(tmp_path, {"s1": "D:\\Repository\\renren-drama"})
    provider, log = _provider(tmp_path)
    provider.initialize("s1", hermes_home=str(home), platform="cli")
    try:
        provider.prefetch("兼容测试", session_id="s1")
        assert provider.on_pre_compress([]) == ""
        again = provider.prefetch("兼容测试", session_id="s1")
        assert "近期上下文-renren-drama" in again and "#101" in again
    finally:
        provider.shutdown()


def test_no_project_returns_empty_without_reading_memory(tmp_path):
    home = _make_home(tmp_path, {"s1": "D:\\AI\\HermesData"})
    provider, log = _provider(tmp_path)
    provider.initialize("s1", hermes_home=str(home), platform="desktop")
    try:
        assert provider.prefetch("hermes 怎么接入 engram", session_id="s1") == ""
        assert provider.recall_status() is None
        assert [c["name"] for c in _calls(log)] == ["mem_list_projects"]
    finally:
        provider.shutdown()


def test_im_platform_disabled_by_default(tmp_path):
    home = _make_home(tmp_path, {"s1": "D:\\Repository\\renren-drama"})
    provider, log = _provider(tmp_path)
    provider.initialize("s1", hermes_home=str(home), platform="weixin")
    try:
        assert provider.prefetch("renren-drama 架构", session_id="s1") == ""
        assert _calls(log) == []
    finally:
        provider.shutdown()


def test_initialize_cwd_kwarg_used_when_no_session_row(tmp_path):
    home = _make_home(tmp_path)
    provider, log = _provider(tmp_path)
    provider.initialize("s9", hermes_home=str(home), platform="desktop", cwd="D:\\Repository\\renren-drama")
    try:
        assert "项目=renren-drama" in provider.prefetch("架构", session_id="s9")
    finally:
        provider.shutdown()


def test_backend_hang_returns_empty_within_budget(tmp_path):
    home = _make_home(tmp_path, {"s1": "D:\\Repository\\renren-drama"})
    provider, _ = _provider(tmp_path, mode="hang", call_timeout=1, total_budget=2)
    provider.initialize("s1", hermes_home=str(home), platform="desktop")
    try:
        assert provider.prefetch("架构", session_id="s1") == ""
        assert provider.recall_status() is None
    finally:
        provider.shutdown()


def test_output_respects_byte_budget(tmp_path):
    home = _make_home(tmp_path, {"s1": "D:\\Repository\\renren-drama"})
    provider, _ = _provider(tmp_path, max_bytes=300)
    provider.initialize("s1", hermes_home=str(home), platform="desktop")
    try:
        out = provider.prefetch("兼容测试", session_id="s1")
        assert 0 < len(out.encode("utf-8")) <= 300
    finally:
        provider.shutdown()


def test_session_switch_reset_clears_state(tmp_path):
    home = _make_home(tmp_path, {"s1": "D:\\AI\\HermesData", "s2": "D:\\AI\\HermesData"})
    provider, _ = _provider(tmp_path)
    provider.initialize("s1", hermes_home=str(home), platform="desktop")
    try:
        assert provider.prefetch("dsh-codex-ui 兼容测试", session_id="s1")
        provider.on_session_switch("s2", parent_session_id="s1", reset=True)
        # 新会话没有沿用的项目
        assert provider.prefetch("继续检查", session_id="s2") == ""
    finally:
        provider.shutdown()


def test_works_through_hermes_memory_manager(tmp_path):
    from agent.memory_manager import MemoryManager

    home = _make_home(tmp_path, {"s1": "D:\\Repository\\renren-drama"})
    provider, _ = _provider(tmp_path)
    manager = MemoryManager()
    manager.add_provider(provider)
    manager.initialize_all(session_id="s1", hermes_home=str(home), platform="desktop")
    try:
        out = manager.prefetch_all("请检查完整的兼容测试并说明结果", session_id="s1")
        assert "项目=renren-drama" in out
        line = manager.describe_recall()
        assert "Engram" in line and "recalled 2 memories" in line
        # 工具经 MemoryManager 路由，写入经 sync_all 后台执行
        assert manager.has_tool("engram_save")
        saved = json.loads(manager.handle_tool_call("engram_save", {"title": "t", "content": "c"}))
        assert saved["id"] > 0
        manager.sync_all("请检查完整的兼容测试并说明结果", "回答", session_id="s1")
        manager.on_session_end([])
    finally:
        manager.shutdown_all()
    names = [c["name"] for c in _calls(_)]
    assert "mem_save_prompt" in names and names[-1] == "mem_session_end"
