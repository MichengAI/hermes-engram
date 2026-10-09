"""自动存取测试：对照 Engram 官方 Codex 钩子的时机，验证 Hermes provider 对应钩子的写入行为。

| Codex 钩子          | Hermes 钩子                     | 行为                                   |
|---------------------|----------------------------------|----------------------------------------|
| SessionStart        | 首次确定项目时（prefetch 内）    | mem_session_start 注册会话 + 注入协议  |
| UserPromptSubmit    | sync_turn                        | mem_save_prompt 记录用户提问           |
| （主动保存）        | 工具 engram_save 等              | 带会话 id 和项目写入                   |
| SubagentStop        | on_delegation                    | mem_capture_passive 被动捕获           |
| SessionStart:compact| on_pre_compress                  | 压缩前存会话总结 + 下一轮重新召回      |
| SessionEnd          | on_session_end                   | mem_session_end 关闭会话               |
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

import pytest

from conftest import FAKE_SERVER
from engram import EngramMemoryProvider

REPO = "D:\\Repository\\renren-drama"


def _home(tmp_path, rows):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    db = sqlite3.connect(home / "state.db")
    db.execute("create table sessions (id text primary key, cwd text)")
    db.executemany("insert into sessions values (?, ?)", list(rows.items()))
    db.commit()
    db.close()
    return home


@pytest.fixture
def env(tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CALL_LOG", str(log))
    monkeypatch.delenv("FAKE_MODE", raising=False)

    def calls(name=None):
        if not log.exists():
            return []
        items = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
        return [c for c in items if name is None or c["name"] == name]

    return calls


def _make(tmp_path, *, platform="desktop", agent_context="primary", rows=None, **cfg):
    home = _home(tmp_path, rows if rows is not None else {"s1": REPO})
    provider = EngramMemoryProvider(config={"command": [sys.executable, str(FAKE_SERVER)], "call_timeout": 3, **cfg})
    provider.initialize("s1", hermes_home=str(home), platform=platform, agent_context=agent_context)
    return provider


def _flush(provider):
    """等后台写入线程完成。"""
    provider._join_writer(timeout=10)


def _dispatch(provider, goal):
    """模拟宿主真实派发 observer，合法完成路径必须有来源。"""
    provider.on_post_tool_call(tool_name="delegate_task", session_id=provider._session_id,
                              args={"tasks": [{"goal": goal}]}, result={"status": "dispatched"})


# ---- 来源回合与首次项目登记 ----

def test_host_delayed_sync_cannot_borrow_later_project(tmp_path, env):
    """真实宿主队列尚未执行时，下一轮确认不能重新授权旧拒绝正文。"""
    import threading
    from agent.memory_manager import MemoryManager
    p = _make(tmp_path)
    manager = MemoryManager()
    manager.add_provider(p)
    entered, release = threading.Event(), threading.Event()
    original = p.sync_turn
    def delayed(user, assistant, **kwargs):
        entered.set()
        assert release.wait(5)
        original(user, assistant, **kwargs)
    p.sync_turn = delayed
    rejected = "project: unknown-project 请检查完整架构并说明结果"
    try:
        p.prefetch("renren-drama 检查完整架构", session_id="s1")
        p.prefetch(rejected, session_id="s1")
        manager.sync_all(rejected, "回答", session_id="s1")
        assert entered.wait(5)
        p.prefetch("继续检查完整架构和测试", session_id="s1")
        release.set()
        assert manager.flush_pending(timeout=10)
        _flush(p)
        assert env("mem_save_prompt") == []
    finally:
        release.set()
        manager.shutdown_all()


def test_delayed_valid_prompt_keeps_source_project(tmp_path, env):
    p = _make(tmp_path)
    first = "renren-drama 检查完整架构和测试"
    try:
        p.prefetch(first, session_id="s1")
        p.prefetch("dsh-codex-ui 检查完整架构和测试", session_id="s1")
        p.sync_turn(first, "回答", session_id="s1")
        _flush(p)
        assert env("mem_save_prompt")[-1]["arguments"]["session_id"] == "hermes-s1-renren-drama"
    finally:
        p.shutdown()


def test_repeated_text_with_conflicting_decisions_is_not_reauthorized(tmp_path, env, monkeypatch):
    p = _make(tmp_path)
    text = "请检查完整架构和所有测试结果"
    try:
        monkeypatch.setattr(p, "_session_cwd", lambda sid: "")
        p.prefetch(text, session_id="s1")
        p.prefetch("renren-drama 检查完整架构", session_id="s1")
        p.prefetch(text, session_id="s1")
        p.sync_turn(text, "回答", session_id="s1")
        _flush(p)
        assert env("mem_save_prompt") == []
    finally:
        p.shutdown()


def test_turn_start_revokes_inflight_recall(tmp_path, env, monkeypatch):
    import threading
    p = _make(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = p._resolve_project
    def delayed(*args):
        value = original(*args)
        entered.set()
        assert release.wait(5)
        return value
    monkeypatch.setattr(p, "_resolve_project", delayed)
    worker = threading.Thread(target=p.prefetch, args=("renren-drama 检查完整架构",))
    try:
        worker.start()
        assert entered.wait(5)
        p.on_turn_start(2, "继续")  # 宿主可能跳过这个 trivial 回合的 prefetch
        release.set()
        worker.join(5)
        assert p._state("s1").confirmed_project is None
    finally:
        release.set()
        worker.join(5)
        p.shutdown()


def test_async_delegation_uses_dispatch_project_after_switch(tmp_path, env):
    p = _make(tmp_path)
    body = "## Key Learnings:\n1. Background completion stays attached to the project that dispatched it."
    try:
        p.prefetch("renren-drama 检查完整架构")
        p.on_post_tool_call(tool_name="delegate_task", session_id="s1", args={"tasks": [{"goal": "origin-task"}]},
                            result={"status": "dispatched"})
        p.prefetch("dsh-codex-ui 检查完整架构")
        p.on_delegation("origin-task", body)
        _flush(p)
        assert env("mem_capture_passive")[0]["arguments"]["session_id"] == "hermes-s1-renren-drama"
    finally:
        p.shutdown()


def test_repeated_turn_without_prefetch_cannot_reuse_old_proof(tmp_path, env):
    p = _make(tmp_path)
    text = "renren-drama 检查完整架构和所有测试结果"
    try:
        p.prefetch(text)
        p.on_turn_start(2, text)  # 新轮没有可配对的 ID，宿主也可能跳过 prefetch
        p.sync_turn(text, "回答")
        _flush(p)
        assert env("mem_save_prompt") == []
    finally:
        p.shutdown()


def test_source_capacity_fails_closed(tmp_path, env):
    p = _make(tmp_path)
    try:
        state = p._state("s1")
        for index in range(2049):
            p._record_source(state, f"source-{index}", "renren-drama")
        p.prefetch("renren-drama 检查完整架构", session_id="s1")
        p.sync_turn("renren-drama 检查完整架构", "回答", session_id="s1")
        _flush(p)
        assert state.source_limit_reached and len(state.prompt_sources) == 2048
        assert env("mem_save_prompt") == []
    finally:
        p.shutdown()


def test_unobserved_sync_is_not_authorized(tmp_path, env):
    p = _make(tmp_path)
    try:
        p.prefetch("renren-drama 检查完整架构")
        p.sync_turn("未经来源确认的完整用户正文", "回答")
        _flush(p)
        assert env("mem_save_prompt") == []
    finally:
        p.shutdown()


@pytest.mark.parametrize("blocked", [False, True])
def test_fresh_git_project_registration_uses_engram_identity(tmp_path, env, monkeypatch, blocked):
    import subprocess
    repo = tmp_path / "local-folder"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    monkeypatch.setenv("FAKE_SESSION_PROJECT", "canonical-remote-name")
    p = _make(tmp_path, rows={"s1": str(repo)}, auto_capture=not blocked)
    try:
        out = p.prefetch("检查完整项目架构和所有测试", session_id="s1")
        if blocked:
            assert env("mem_session_start") == [] and out == ""
        else:
            assert "项目=canonical-remote-name" in out
            starts = env("mem_session_start")
            assert len(starts) == 1
            from pathlib import Path
            assert Path(starts[0]["arguments"]["directory"]) == repo
            p.sync_turn("检查完整项目架构和所有测试", "回答", session_id="s1")
            _flush(p)
            assert env("mem_save_prompt")[0]["arguments"]["session_id"] == starts[0]["arguments"]["id"]
    finally:
        p.shutdown()


def test_unknown_text_does_not_create_git_project(tmp_path, env, monkeypatch):
    import subprocess
    repo = tmp_path / "fresh"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    p = _make(tmp_path, rows={"s1": str(repo)})
    try:
        assert p.prefetch("project: invented-name 请检查完整架构", session_id="s1") == ""
        assert env("mem_session_start") == []
    finally:
        p.shutdown()


# ---- SessionStart：注册会话 + 注入协议 ----

def test_first_recall_registers_session_and_injects_protocol(tmp_path, env):
    p = _make(tmp_path)
    try:
        out = p.prefetch("看看兼容测试", session_id="s1")
        starts = env("mem_session_start")
        assert len(starts) == 1
        assert starts[0]["arguments"]["directory"] == REPO
        assert starts[0]["arguments"]["id"] == "hermes-s1-renren-drama"
        assert "engram_save" in out  # 协议提示随首轮注入
        p.prefetch("继续看发布流程", session_id="s1")
        assert len(env("mem_session_start")) == 1  # 同一会话同一项目只注册一次
    finally:
        p.shutdown()


def test_session_not_registered_when_directory_resolves_to_other_project(tmp_path, env, monkeypatch):
    # 点名项目，但会话目录被 Engram 解析成别的项目：不能把写入记到错的项目上
    monkeypatch.setenv("FAKE_SESSION_PROJECT", "dsh-codex-ui")
    p = _make(tmp_path)
    try:
        p.prefetch("renren-drama 架构", session_id="s1")
        p.sync_turn("renren-drama 架构", "好的", session_id="s1")
        _flush(p)
        assert env("mem_save_prompt") == []
    finally:
        p.shutdown()


def test_no_directory_binding_uses_explicit_project_without_session(tmp_path, env):
    # 会话目录未绑定（HermesData）+ 点名：读可以，写入只走显式 project，不注册会话
    p = _make(tmp_path, rows={"s1": "D:\\AI\\HermesData"})
    try:
        p.prefetch("继续 dsh-codex-ui 项目", session_id="s1")
        assert env("mem_session_start") == []
        p.sync_turn("继续 dsh-codex-ui 项目", "ok", session_id="s1")
        _flush(p)
        assert env("mem_save_prompt") == []  # 无会话不记提示词，避免落到 dir_basename 项目
        result = json.loads(p.handle_tool_call("engram_save", {"title": "t", "content": "c", "type": "decision"}))
        assert result.get("project") == "dsh-codex-ui"
        save = env("mem_save")[-1]["arguments"]
        assert save["project"] == "dsh-codex-ui" and "session_id" not in save
    finally:
        p.shutdown()


@pytest.mark.parametrize("query", [
    "比较 renren-drama 和 dsh-codex-ui 项目的完整架构",
    "project: unknown-project 请检查完整架构并说明结果",
])
def test_rejected_turn_blocks_capture_and_default_tools_then_recovers(tmp_path, env, monkeypatch, query):
    p = _make(tmp_path)
    body = "## Key Learnings:\n1. Rejected turn must never leak into the previously confirmed project."
    try:
        p.prefetch("renren-drama 兼容测试", session_id="s1")
        # 已注册会话仍合法；撤掉 cwd 线索以证明恢复来自历史 sticky 而非目录回退。
        monkeypatch.setattr(p, "_session_cwd", lambda sid: "")
        assert p.prefetch(query, session_id="s1") == ""
        p.on_post_tool_call(tool_name="terminal", session_id="s1", result=body)
        p.on_delegation("调查", body)
        p.sync_turn(query, "回答", session_id="s1")
        _flush(p)
        assert env("mem_capture_passive") == []
        assert env("mem_save_prompt") == []
        for tool, args in [("engram_save", {"title": "t", "content": "c"}),
                           ("engram_session_summary", {"content": "c"}),
                           ("engram_search", {"query": "架构"})]:
            assert "error" in json.loads(p.handle_tool_call(tool, args))
        p.on_session_switch("s1", parent_session_id="s1", reason="compression")
        p.sync_turn("继续", "回答", messages=[_formal_summary("Rejected turn summary.")])
        _flush(p)
        assert env("mem_session_summary") == []
        # 历史归属保留，但只有下一次合法解析后才恢复权限（即使召回无新正文）。
        p.prefetch("继续检查完整的兼容测试并说明结果", session_id="s1")
        _dispatch(p, "恢复后新调查")
        p.on_delegation("恢复后新调查", body)
        p.sync_turn("继续检查完整的兼容测试并说明结果", "回答")
        _flush(p)
        assert len(env("mem_capture_passive")) == 1
        assert len(env("mem_save_prompt")) == 1
        assert env("mem_capture_passive")[0]["arguments"]["session_id"] == "hermes-s1-renren-drama"
    finally:
        p.shutdown()


@pytest.mark.parametrize("failure", ["transport", "parser", "empty"])
def test_project_resolution_failure_revokes_turn_not_history(tmp_path, env, monkeypatch, failure):
    from engram.mcp_client import McpError
    p = _make(tmp_path)
    try:
        p.prefetch("renren-drama 兼容测试")
        with monkeypatch.context() as m:
            if failure != "empty":
                def fail(*args, **kwargs):
                    raise McpError("unavailable") if failure == "transport" else ValueError("invalid catalog")
                m.setattr(p, "_get_catalog", fail)
            assert p.prefetch("" if failure == "empty" else "请检查完整项目架构") == ""
        p.on_delegation("t", "## Key Learnings:\n1. Failed project resolution cannot reuse an old confirmed session.")
        p.sync_turn("请检查完整项目架构并说明结果", "回答")
        _flush(p)
        assert env("mem_capture_passive") == env("mem_save_prompt") == []
        assert "error" in json.loads(p.handle_tool_call("engram_save", {"title": "t", "content": "c"}))
        p.prefetch("继续检查完整项目架构并说明结果")
        p.sync_turn("继续检查完整项目架构并说明结果", "回答")
        _flush(p)
        assert len(env("mem_save_prompt")) == 1
    finally:
        p.shutdown()


def test_queued_writes_keep_confirmed_project_when_next_turn_changes(tmp_path, env, monkeypatch):
    p = _make(tmp_path)
    try:
        p.prefetch("请检查完整的兼容测试并说明结果")
        jobs = []
        monkeypatch.setattr(p, "_enqueue", jobs.append)
        p.sync_turn("请检查完整的兼容测试并说明结果", "回答")
        p.on_post_tool_call(tool_name="terminal", session_id="s1", result="x" * 100)
        _dispatch(p, "t")
        p.on_delegation("t", "## Key Learnings:\n1. Queued completion remains scoped to its confirmed project.")
        p.on_session_switch("s1", parent_session_id="s1", reason="compression")
        p.sync_turn("继续", "回答", messages=[_formal_summary("Queued formal summary.")])
        assert len(jobs) == 4
        p.prefetch("dsh-codex-ui 架构")
        p.prefetch("project: missing-project 请检查完整架构")
        for job in jobs:
            job()
        for name in ("mem_save_prompt", "mem_capture_passive", "mem_session_summary"):
            assert env(name)
            assert all(c["arguments"]["session_id"] == "hermes-s1-renren-drama" for c in env(name))
        assert env("mem_session_summary")[0]["arguments"]["project"] == "renren-drama"
    finally:
        p.shutdown()


# ---- UserPromptSubmit：记录用户提问 ----

def test_sync_turn_saves_prompt_in_background(tmp_path, env):
    p = _make(tmp_path)
    try:
        p.prefetch("请检查完整的兼容测试并说明结果", session_id="s1")
        started = time.monotonic()
        p.sync_turn("请检查完整的兼容测试并说明结果", "这是回答", session_id="s1")
        assert time.monotonic() - started < 0.5  # 不阻塞
        _flush(p)
        prompts = env("mem_save_prompt")
        assert len(prompts) == 1
        assert prompts[0]["arguments"] == {"content": "请检查完整的兼容测试并说明结果", "session_id": "hermes-s1-renren-drama"}
    finally:
        p.shutdown()


@pytest.mark.parametrize("text", [
    "[System: continue the internal tool execution now]",
    "[CONTEXT COMPACTION — REFERENCE ONLY] Historical summary here",
    "[ASYNC DELEGATION] Internal delegated result is ready",
    "Cronjob Response: scheduled internal follow-up",
])
def test_synthetic_prompt_is_not_saved(tmp_path, env, text):
    p = _make(tmp_path)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        p.sync_turn(text, "回答", session_id="s1")
        _flush(p)
        assert env("mem_save_prompt") == []
    finally:
        p.shutdown()


def test_host_plain_continuation_nudge_is_not_saved(tmp_path, env):
    from agent.context_compressor import COMPRESSION_CONTINUATION_USER_CONTENT
    p = _make(tmp_path)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        p.sync_turn(COMPRESSION_CONTINUATION_USER_CONTENT, "回答")
        _flush(p)
        assert env("mem_save_prompt") == []
    finally:
        p.shutdown()


def test_bot_authored_prompt_is_not_saved(tmp_path, env):
    p = _make(tmp_path)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        p.sync_turn("This is an automated bot follow-up", "回答", turn_author={"id": "bot:alpha", "is_bot": True})
        _flush(p)
        assert env("mem_save_prompt") == []
    finally:
        p.shutdown()


def test_private_query_not_sent_as_search_tokens(tmp_path, env):
    p = _make(tmp_path)
    try:
        p.prefetch("请检查架构 <private>supersecrettoken</private>", session_id="s1")
        query = env("mem_search")[0]["arguments"]["query"]
        assert "supersecrettoken" not in query
    finally:
        p.shutdown()


def test_private_prompt_redacted_before_truncation(tmp_path, env):
    p = _make(tmp_path)
    try:
        text = "public instructions " + "x" * 19960 + "<PRIVATE>" + "SECRET" * 100 + "</PRIVATE> tail"
        p.prefetch(text, session_id="s1")
        p.sync_turn(text,
                    "回答", session_id="s1")
        _flush(p)
        content = env("mem_save_prompt")[0]["arguments"]["content"]
        assert "SECRET" not in content and "[REDACTED]" in content
    finally:
        p.shutdown()


def test_prompt_length_filter(tmp_path, env):
    p = _make(tmp_path)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        p.prefetch("1234567890", session_id="s1")
        p.sync_turn("1234567890", "回答", session_id="s1")
        p.prefetch("12345678901", session_id="s1")
        p.sync_turn("12345678901", "回答", session_id="s1")
        _flush(p)
        assert [c["arguments"]["content"] for c in env("mem_save_prompt")] == ["12345678901"]
    finally:
        p.shutdown()


def test_sync_turn_skips_trivial_and_unknown_project(tmp_path, env):
    p = _make(tmp_path, rows={"s1": "D:\\AI\\HermesData"})
    try:
        p.prefetch("hermes 怎么配置", session_id="s1")  # 判断不了项目
        p.sync_turn("hermes 怎么配置", "回答", session_id="s1")
        p.sync_turn("ok", "回答", session_id="s1")
        _flush(p)
        assert env("mem_save_prompt") == []
    finally:
        p.shutdown()


def test_prompt_capture_can_be_disabled(tmp_path, env):
    p = _make(tmp_path, capture_prompts=False)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        p.sync_turn("看看兼容测试", "回答", session_id="s1")
        _flush(p)
        assert env("mem_save_prompt") == []
    finally:
        p.shutdown()


# ---- 主动保存工具 ----

def test_tools_registered_and_save_binds_session_and_project(tmp_path, env):
    p = _make(tmp_path)
    try:
        names = {s["name"] for s in p.get_tool_schemas()}
        assert names == {"engram_save", "engram_search", "engram_get", "engram_session_summary", "engram_judge"}
        p.prefetch("看看兼容测试", session_id="s1")
        result = json.loads(p.handle_tool_call("engram_save", {"title": "决定", "content": "**What**: x", "type": "decision"}))
        assert result["id"] > 0
        args = env("mem_save")[-1]["arguments"]
        assert args["session_id"] == "hermes-s1-renren-drama"
        assert args["project"] == "renren-drama"
        assert args["capture_prompt"] is True
    finally:
        p.shutdown()


def test_save_without_project_returns_error_not_guess(tmp_path, env):
    p = _make(tmp_path, rows={"s1": "D:\\AI\\HermesData"})
    try:
        result = json.loads(p.handle_tool_call("engram_save", {"title": "t", "content": "c"}))
        assert "error" in result
        assert env("mem_save") == []
        # 显式传入已知项目就可以写
        ok = json.loads(p.handle_tool_call("engram_save", {"title": "t", "content": "c", "project": "renren-drama"}))
        assert ok["id"] > 0
        # 未知项目被拒
        bad = json.loads(p.handle_tool_call("engram_save", {"title": "t", "content": "c", "project": "foo"}))
        assert "error" in bad
    finally:
        p.shutdown()


def test_search_tool_requires_project_scope(tmp_path, env):
    p = _make(tmp_path)
    try:
        p.prefetch("兼容测试", session_id="s1")
        out = json.loads(p.handle_tool_call("engram_search", {"query": "发布"}))
        assert out["results"]
        assert env("mem_search")[-1]["arguments"]["project"] == "renren-drama"
        p.handle_tool_call("engram_get", {"id": 101})
        assert env("mem_get_observation")[-1]["arguments"]["id"] == 101
    finally:
        p.shutdown()


def test_tools_hidden_and_writes_disabled_for_subagent_and_cron(tmp_path, env):
    for ctx in ("subagent", "cron"):
        p = _make(tmp_path / ctx, agent_context=ctx)
        try:
            p.prefetch("看看兼容测试", session_id="s1")
            p.sync_turn("看看兼容测试", "回答", session_id="s1")
            p.on_delegation("t", "r")
            p.on_session_end([])
            _flush(p)
            assert json.loads(p.handle_tool_call("engram_save", {"title": "t", "content": "c"})).get("error")
        finally:
            p.shutdown()
    names = {c["name"] for c in env()}
    assert not names & {"mem_save", "mem_save_prompt", "mem_session_start", "mem_capture_passive", "mem_session_end"}


def test_im_platform_no_tools_effect_and_no_writes(tmp_path, env):
    p = _make(tmp_path, platform="weixin")
    try:
        p.sync_turn("renren-drama 架构", "回答", session_id="s1")
        _flush(p)
        assert env() == []
    finally:
        p.shutdown()


# ---- SubagentStop：被动捕获 ----

def test_provider_loader_registers_working_post_tool_hook(tmp_path, env, monkeypatch):
    import engram
    from hermes_cli import plugins
    from hermes_cli.lifecycle import invoke_hook
    from plugins.memory import _load_provider_from_dir
    from pathlib import Path

    home = _home(tmp_path, {"s1": REPO})
    monkeypatch.setenv("HERMES_HOME", str(home))
    manager = plugins.PluginManager()
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: manager)
    from hermes_cli import config
    monkeypatch.setattr(config, "load_config_readonly", lambda: {"plugins": {"engram": {
        "command": [sys.executable, str(FAKE_SERVER)], "call_timeout": 3,
    }}})
    p = _load_provider_from_dir(Path(engram.__file__).parent)
    assert p is not None
    p.initialize("s1", hermes_home=str(home), platform="desktop", agent_context="primary")
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        assert manager.has_hook("post_tool_call")
        invoke_hook("post_tool_call", tool_name="terminal", args={}, session_id="s1",
                    tool_call_id="tool-1", result="## Key Learnings:\n1. A verified tool result with enough words to be passively captured.")
        _flush(p)
        cap = env("mem_capture_passive")
        assert len(cap) == 1 and cap[0]["arguments"]["source"] == "terminal"
        assert cap[0]["arguments"]["session_id"] == "hermes-s1-renren-drama"
    finally:
        p.shutdown()


@pytest.mark.parametrize("name,sid,result", [
    ("memory", "s1", "x" * 100),
    ("engram_save", "s1", "x" * 100),
    ("mcp__engram__mem_search", "s1", "x" * 100),
    ("mem_context", "s1", "x" * 100),
    ("terminal", "child", "x" * 100),
    ("terminal", "", "x" * 100),
    ("terminal", "s1", "x" * 50),
    ("terminal", "s1", None),
])
def test_tool_capture_skips_memory_unrelated_sessions_and_small_results(tmp_path, env, name, sid, result):
    p = _make(tmp_path)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        p.on_post_tool_call(tool_name=name, session_id=sid, result=result)
        _flush(p)
        assert env("mem_capture_passive") == []
    finally:
        p.shutdown()


@pytest.mark.parametrize("cfg", [{"capture_tools": False}, {"auto_capture": False},
                                 {"agent_context": "subagent"}, {"agent_context": "cron"},
                                 {"platform": "weixin"}, {"rows": {"s1": "D:\\AI\\HermesData"}}])
def test_tool_capture_preserves_write_boundaries(tmp_path, env, cfg):
    p = _make(tmp_path, **cfg)
    try:
        p.prefetch("renren-drama 架构", session_id="s1")
        p.on_post_tool_call(tool_name="terminal", session_id="s1", result="x" * 100)
        _flush(p)
        assert env("mem_capture_passive") == []
    finally:
        p.shutdown()


def test_structured_tool_result_redacted_before_capture(tmp_path, env):
    p = _make(tmp_path)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        p.on_post_tool_call(tool_name="web_extract", session_id="s1",
                            result={"items": [{"text": "x" * 80 + "<PRIVATE>SECRET</PRIVATE>"}], "count": 1})
        _flush(p)
        content = env("mem_capture_passive")[0]["arguments"]["content"]
        assert "SECRET" not in content and "[REDACTED]" in content
        assert content == "x" * 80 + "[REDACTED]"
    finally:
        p.shutdown()


@pytest.mark.parametrize("encode", [False, True])
@pytest.mark.parametrize("tool_first", [False, True])
def test_host_delegate_wrapper_captures_multiline_bodies_once(tmp_path, env, encode, tool_first):
    p = _make(tmp_path)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        bodies = ["## Key Learnings:\n1. First verified synchronous delegation learning is a real multiline body.",
                  "## Key Learnings:\n1. Second verified synchronous delegation learning is separate. <private>SECRET</private>"]
        wrapped = {"results": [{"task_index": i, "status": "completed", "summary": body,
                                "duration_seconds": 1.0} for i, body in enumerate(bodies)],
                   "total_duration_seconds": 2.0}
        result = json.dumps(wrapped) if encode else wrapped
        callbacks = [lambda: p.on_post_tool_call(tool_name="delegate_task", session_id="s1", result=result),
                     lambda: [p.on_delegation("t", body) for body in bodies]]
        for call in callbacks if tool_first else reversed(callbacks):
            call()
        _flush(p)
        contents = [c["arguments"]["content"] for c in env("mem_capture_passive")]
        assert contents == [bodies[0], bodies[1].replace("<private>SECRET</private>", "[REDACTED]")]
    finally:
        p.shutdown()


def test_nested_json_tool_body_and_bounded_queue(tmp_path, env, monkeypatch):
    p = _make(tmp_path)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        jobs = []
        monkeypatch.setattr(p, "_enqueue", jobs.append)
        body = "## Key Learnings:\n1. Nested JSON contains real multiline output. <private>SECRET</private>\n" + "大" * 40000
        p.on_post_tool_call(tool_name="terminal", session_id="s1", result=json.dumps({
            "result": json.dumps({"output": body, "exit_code": 0}), "metadata": "ignored"}))
        _dispatch(p, "t")
        p.on_delegation("t", body)
        assert len(jobs) == 2
        for job in jobs:
            strings = [cell.cell_contents for cell in job.__closure__ if isinstance(cell.cell_contents, str)]
            assert all(len(s.encode("utf-8")) <= 30000 and "SECRET" not in s for s in strings)
            job()
        contents = [c["arguments"]["content"] for c in env("mem_capture_passive")]
        assert len(contents) == 2
        assert all(c.startswith("## Key Learnings:\n1.") and "[REDACTED]" in c for c in contents)
    finally:
        p.shutdown()


def test_background_delegation_completion_with_registered_hook(tmp_path, env):
    from types import SimpleNamespace
    from tools.delegate_tool_results import _notify_memory_manager
    p = _make(tmp_path)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        p._tool_capture_hook_registered = True
        p.on_post_tool_call(tool_name="delegate_task", session_id="s1", args={"tasks": [{"goal": "调查"}]}, result=json.dumps({
            "status": "dispatched", "mode": "background", "count": 1,
            "delegation_id": "d1", "goals": ["调查"], "note": "Background task accepted",
        }))
        body = "## Key Learnings:\n1. Background completion must survive a registered post tool hook."
        _notify_memory_manager([{"task_index": 0, "summary": body}], [{"goal": "调查"}],
                               {0: SimpleNamespace(session_id="c1")}, SimpleNamespace(_memory_manager=p))
        _flush(p)
        caps = env("mem_capture_passive")
        assert [c["arguments"]["content"] for c in caps] == [body]
    finally:
        p.shutdown()


@pytest.mark.parametrize("tool_first", [True, False])
def test_delegation_same_body_deduplicated_not_other_completion(tmp_path, env, tool_first):
    p = _make(tmp_path)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        p._tool_capture_hook_registered = True
        body = "## Key Learnings:\n1. Identical synchronous completion is captured only once per session and project."
        callbacks = [lambda: p.on_post_tool_call(tool_name="delegate_task", session_id="s1", args={"tasks": [{"goal": "t"}]}, result=body),
                     lambda: p.on_delegation("t", body)]
        for call in callbacks if tool_first else reversed(callbacks):
            call()
        other = body + "\n2. Another background completion is not suppressed."
        p.on_delegation("t", other)
        _flush(p)
        contents = [c["arguments"]["content"] for c in env("mem_capture_passive")]
        assert contents == [body, other]
    finally:
        p.shutdown()


def test_delegation_result_passively_captured(tmp_path, env):
    p = _make(tmp_path)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        _dispatch(p, "调查一下")
        p.on_delegation("调查一下", "结论...\n## Key Learnings:\n1. 第一条", child_session_id="c1")
        _flush(p)
        cap = env("mem_capture_passive")
        assert len(cap) == 1
        assert cap[0]["arguments"]["source"] == "subagent-stop"
        assert cap[0]["arguments"]["session_id"] == "hermes-s1-renren-drama"
        assert "Key Learnings" in cap[0]["arguments"]["content"]
    finally:
        p.shutdown()


def test_delegation_without_session_is_skipped(tmp_path, env):
    p = _make(tmp_path, rows={"s1": "D:\\AI\\HermesData"})
    try:
        p.on_delegation("t", "## Key Learnings:\n1. x")
        _flush(p)
        assert env("mem_capture_passive") == []
    finally:
        p.shutdown()


# ---- 压缩：存总结 + 重新召回 ----

def _formal_summary(content):
    from agent.context_compressor import SUMMARY_PREFIX, HISTORICAL_TASK_HEADING, _SUMMARY_END_MARKER
    return {"role": "assistant", "_compressed_summary": True,
            "content": f"{SUMMARY_PREFIX}\n\n{HISTORICAL_TASK_HEADING}\n{content}\n\n{_SUMMARY_END_MARKER}"}


def test_completed_turn_archives_formal_compression_summary(tmp_path, env):
    from agent.memory_manager import MemoryManager
    p = _make(tmp_path, rows={"s1": REPO, "s2": REPO}, capture_prompts=False)
    manager = MemoryManager()
    manager.add_provider(p)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        manager.on_pre_compress([{"role": "user", "content": "原始提问"}])
        manager.on_session_switch("s2", parent_session_id="s1", reset=False, reason="compression")
        manager.sync_all("继续", "完成", session_id="s2", messages=[_formal_summary("Official generated summary <private>SECRET</private>" )])
        assert manager.flush_pending(timeout=10)
        _flush(p)
        cap = env("mem_session_summary")
        assert len(cap) == 1
        assert cap[0]["arguments"] == {"content": "Official generated summary [REDACTED]",
                                        "project": "renren-drama", "session_id": "hermes-s1-renren-drama"}
    finally:
        manager.shutdown_all()


def test_formal_summary_deduplicated_across_compression_lineage(tmp_path, env):
    p = _make(tmp_path, rows={"s1": REPO, "s2": REPO}, capture_prompts=False)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        p.on_session_switch("s2", parent_session_id="s1", reason="compression")
        messages = [_formal_summary("The official summary is stable.")]
        p.sync_turn("继续", "完成", session_id="s2", messages=messages)
        p.sync_turn("继续", "完成", session_id="s2", messages=messages)
        _flush(p)
        p.on_session_switch("s2", parent_session_id="s2", reason="compression")
        p.sync_turn("继续", "完成", session_id="s2", messages=messages)
        _flush(p)
        assert len(env("mem_session_summary")) == 1
        assert list(p.compaction_status().values()) == ["saved"]
    finally:
        p.shutdown()


def test_summary_rejection_records_failure_without_automatic_replay(tmp_path, env, monkeypatch):
    monkeypatch.setenv("FAKE_SUMMARY_MODE", "error")
    p = _make(tmp_path, capture_prompts=False)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        p.on_session_switch("s1", parent_session_id="s1", reason="compression")
        messages = [_formal_summary("A summary whose archive is rejected.")]
        p.sync_turn("继续", "完成", messages=messages)
        _flush(p)
        p.sync_turn("继续", "完成", messages=messages)
        _flush(p)
        assert list(p.compaction_status().values()) == ["failed"]
        assert len(env("mem_session_summary")) == 1
    finally:
        p.shutdown()


def test_summary_timeout_records_unknown_without_replay(tmp_path, env, monkeypatch):
    monkeypatch.setenv("FAKE_SUMMARY_MODE", "hang")
    p = _make(tmp_path, capture_prompts=False, write_timeout=0.2)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        p.on_session_switch("s1", parent_session_id="s1", reason="compression")
        messages = [_formal_summary("A summary with uncertain transport outcome.")]
        p.sync_turn("继续", "完成", messages=messages)
        _flush(p)
        p.sync_turn("继续", "完成", messages=messages)
        _flush(p)
        assert list(p.compaction_status().values()) == ["unknown"]
        assert len(env("mem_session_summary")) == 1
    finally:
        p.shutdown()


def test_summary_uses_project_at_compression_not_next_prompt(tmp_path, env):
    p = _make(tmp_path, capture_prompts=False)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        p.on_session_switch("s1", parent_session_id="s1", reason="compression")
        p.prefetch("dsh-codex-ui 架构", session_id="s1")
        p.sync_turn("继续", "完成", messages=[_formal_summary("Previous project work summary.")])
        _flush(p)
        assert env("mem_session_summary")[0]["arguments"]["project"] == "renren-drama"
    finally:
        p.shutdown()


@pytest.mark.parametrize("cfg", [{"compaction_summary": False}, {"auto_capture": False},
                                 {"agent_context": "subagent"}, {"agent_context": "cron"},
                                 {"platform": "weixin"}])
def test_summary_preserves_write_boundaries(tmp_path, env, cfg):
    p = _make(tmp_path, **cfg)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        p.on_session_switch("s1", parent_session_id="s1", reason="compression")
        p.sync_turn("继续", "完成", messages=[_formal_summary("Official summary.")])
        _flush(p)
        assert env("mem_session_summary") == []
    finally:
        p.shutdown()


def test_no_summary_without_confirmed_boundary_or_formal_carrier(tmp_path, env):
    p = _make(tmp_path)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        p.sync_turn("继续", "完成", messages=[_formal_summary("No boundary yet.")])
        p.on_session_switch("s1", parent_session_id="s1", reason="compression")
        p.sync_turn("继续", "完成", messages=[{"role": "assistant", "content": "Ordinary answer"}])
        _flush(p)
        assert env("mem_session_summary") == []
    finally:
        p.shutdown()


def test_pre_compress_resets_recall_without_archiving_excerpt(tmp_path, env):
    p = _make(tmp_path)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        assert p.on_pre_compress([
            {"role": "user", "content": "原始提问不是正式摘要"},
            {"role": "assistant", "content": "原始回答不是正式摘要"},
        ]) == ""
        _flush(p)
        assert env("mem_session_summary") == []
        again = p.prefetch("看看兼容测试", session_id="s1")
        assert "近期上下文-renren-drama" in again
    finally:
        p.shutdown()


# ---- SessionEnd：关闭会话 ----

def test_session_end_closes_registered_sessions_once(tmp_path, env):
    p = _make(tmp_path)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        p.on_session_end([{"role": "user", "content": "x"}])
        p.on_session_end([])
        _flush(p)
        ends = env("mem_session_end")
        assert [e["arguments"]["id"] for e in ends] == ["hermes-s1-renren-drama"]
    finally:
        p.shutdown()


def test_compression_switch_keeps_engram_session(tmp_path, env):
    # Hermes 压缩会换 session_id（reset=False）：沿用同一个 Engram 会话，不关闭也不重开
    p = _make(tmp_path, rows={"s1": REPO, "s2": REPO})
    try:
        p.prefetch("请继续完整的兼容测试并说明结果", session_id="s1")
        p.on_session_switch("s2", parent_session_id="s1", reset=False, reason="compression")
        p.sync_turn("请继续完整的兼容测试并说明结果", "ok", session_id="s2")
        _flush(p)
        assert env("mem_save_prompt")[-1]["arguments"]["session_id"] == "hermes-s1-renren-drama"
        assert len(env("mem_session_start")) == 1 and env("mem_session_end") == []
    finally:
        p.shutdown()


def test_new_session_switch_closes_old_and_registers_new(tmp_path, env):
    p = _make(tmp_path, rows={"s1": REPO, "s2": REPO})
    try:
        p.prefetch("兼容测试", session_id="s1")
        p.on_session_end([])  # Hermes /new 先调 on_session_end 再 switch(reset=True)
        p.on_session_switch("s2", parent_session_id="s1", reset=True)
        p.prefetch("兼容测试", session_id="s2")
        _flush(p)
        assert [s["arguments"]["id"] for s in env("mem_session_start")] == ["hermes-s1-renren-drama", "hermes-s2-renren-drama"]
    finally:
        p.shutdown()


def test_already_ended_session_gets_suffix(tmp_path, env, monkeypatch):
    monkeypatch.setenv("FAKE_ENDED", "hermes-s1-renren-drama")
    p = _make(tmp_path)
    try:
        p.prefetch("请检查完整的兼容测试并说明结果", session_id="s1")
        ids = [s["arguments"]["id"] for s in env("mem_session_start")]
        assert ids[0] == "hermes-s1-renren-drama" and ids[-1].startswith("hermes-s1-renren-drama-r")
        p.sync_turn("请检查完整的兼容测试并说明结果", "ok", session_id="s1")
        _flush(p)
        assert env("mem_save_prompt")[-1]["arguments"]["session_id"] == ids[-1]
    finally:
        p.shutdown()


def test_auto_capture_master_switch_off(tmp_path, env):
    p = _make(tmp_path, auto_capture=False)
    try:
        p.prefetch("兼容测试", session_id="s1")
        p.sync_turn("兼容测试", "ok", session_id="s1")
        p.on_delegation("t", "## Key Learnings:\n1. x")
        p.on_pre_compress([{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}])
        p.on_session_end([])
        _flush(p)
        names = {c["name"] for c in env()}
        assert names <= {"mem_list_projects", "mem_context", "mem_search"}
    finally:
        p.shutdown()
