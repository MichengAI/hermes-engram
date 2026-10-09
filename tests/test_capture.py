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


# ---- UserPromptSubmit：记录用户提问 ----

def test_sync_turn_saves_prompt_in_background(tmp_path, env):
    p = _make(tmp_path)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        started = time.monotonic()
        p.sync_turn("看看兼容测试", "这是回答", session_id="s1")
        assert time.monotonic() - started < 0.5  # 不阻塞
        _flush(p)
        prompts = env("mem_save_prompt")
        assert len(prompts) == 1
        assert prompts[0]["arguments"] == {"content": "看看兼容测试", "session_id": "hermes-s1-renren-drama"}
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

def test_delegation_result_passively_captured(tmp_path, env):
    p = _make(tmp_path)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
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

def test_pre_compress_saves_checkpoint_summary_and_resets_recall(tmp_path, env):
    p = _make(tmp_path)
    try:
        p.prefetch("看看兼容测试", session_id="s1")
        messages = [
            {"role": "user", "content": "看看兼容测试"},
            {"role": "assistant", "content": "已经跑完兼容测试，全部通过。"},
            {"role": "tool", "content": "很长的工具输出" * 100},
            {"role": "user", "content": "再发布一下"},
            {"role": "assistant", "content": "发布完成，版本 0.1.2。"},
        ]
        assert p.on_pre_compress(messages) == ""
        _flush(p)
        summaries = env("mem_session_summary")
        assert len(summaries) == 1
        content = summaries[0]["arguments"]["content"]
        assert content.startswith("## Goal") and "再发布一下" in content and "很长的工具输出" not in content
        assert summaries[0]["arguments"]["session_id"] == "hermes-s1-renren-drama"
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
        p.prefetch("兼容测试", session_id="s1")
        p.on_session_switch("s2", parent_session_id="s1", reset=False, reason="compression")
        p.sync_turn("继续兼容测试", "ok", session_id="s2")
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
        p.prefetch("兼容测试", session_id="s1")
        ids = [s["arguments"]["id"] for s in env("mem_session_start")]
        assert ids[0] == "hermes-s1-renren-drama" and ids[-1].startswith("hermes-s1-renren-drama-r")
        p.sync_turn("兼容测试", "ok", session_id="s1")
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
