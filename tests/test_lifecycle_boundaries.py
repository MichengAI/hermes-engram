"""Pi 对齐回归：结束、回退、委派来源与退出归档的真实 provider 边界。"""
from __future__ import annotations

import threading

from engram.capture import engram_session_id
from test_capture import REPO, _flush, _formal_summary, _make, env


def test_ended_session_revokes_permissions(tmp_path, env):
    p = _make(tmp_path)
    try:
        p.prefetch("看看完整兼容测试", session_id="s1")
        p.on_session_end([])
        assert p._state("s1").confirmed_project is None
        p.on_delegation("没有派发证据的任务", "## Key Learnings:\n1. " + "Closed sessions cannot capture results. " * 3)
        _flush(p)
        assert env("mem_capture_passive") == []
    finally:
        p.shutdown()


def test_end_waits_for_registration_and_closes_it(tmp_path, env, monkeypatch):
    p = _make(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = p._call_write
    def blocked(name, args):
        if name == "mem_session_start":
            entered.set()
            assert release.wait(5)
        return original(name, args)
    monkeypatch.setattr(p, "_call_write", blocked)
    worker = threading.Thread(target=p._ensure_engram_session, args=("s1", "renren-drama"))
    try:
        worker.start()
        assert entered.wait(5)
        p.on_session_end([])
        release.set()
        worker.join(5)
        assert not worker.is_alive()
        _flush(p)
        started = [x["arguments"]["id"] for x in env("mem_session_start")]
        ended = [x["arguments"]["id"] for x in env("mem_session_end")]
        assert started and started == ended
        assert p._state("s1").engram_sessions == {}
    finally:
        release.set()
        worker.join(5)
        p.shutdown()


def test_untracked_old_delegation_cannot_borrow_new_session(tmp_path, env):
    p = _make(tmp_path, rows={"s1": REPO, "s2": REPO})
    try:
        p.prefetch("看看完整兼容测试")
        p.on_session_end([])
        p.on_session_switch("s2", reset=True)
        p.prefetch("看看完整兼容测试", session_id="s2")
        p.on_delegation("旧会话未观察到派发的任务", "## Key Learnings:\n1. " + "This belongs to the old session. " * 3)
        _flush(p)
        assert env("mem_capture_passive") == []
    finally:
        p.shutdown()


def test_undo_invalidates_inflight_prompt_source(tmp_path, env, monkeypatch):
    p = _make(tmp_path)
    entered, release = threading.Event(), threading.Event()
    query = "回退前用户要求检查完整兼容测试"
    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return "renren-drama"
    monkeypatch.setattr(p, "_resolve_project", blocked)
    worker = threading.Thread(target=p.prefetch, args=(query,))
    try:
        worker.start()
        assert entered.wait(5)
        p.on_session_switch("s1", reset=False, rewound=True)
        release.set()
        worker.join(5)
        p.sync_turn(query, "过时回答")
        _flush(p)
        assert env("mem_save_prompt") == []
    finally:
        release.set()
        worker.join(5)
        p.shutdown()


def test_session_id_unicode_and_truncation_do_not_collide():
    assert engram_session_id("s1", "项目甲") != engram_session_id("s1", "项目乙")
    assert engram_session_id("s" * 300, "a") != engram_session_id("s" * 300, "b")
    assert engram_session_id("a-b", "c") != engram_session_id("a", "b-c")
    assert len(engram_session_id("s" * 300, "项目甲")) <= 120


def _break_compressor(monkeypatch):
    """模拟宿主重构或依赖缺失：压缩模块不可导入。"""
    import sys
    monkeypatch.setitem(sys.modules, "agent.context_compressor", None)


def test_formal_summary_degrades_when_host_markers_unavailable(monkeypatch):
    from engram.capture import extract_formal_summary
    _break_compressor(monkeypatch)
    # 不合成摘要，也不猜测宿主标记：拿不到正式协议就当没有摘要。
    assert extract_formal_summary([{"role": "assistant", "_compressed_summary": True, "content": "任意文本"}]) == ""


def test_end_still_closes_when_summary_markers_unavailable(tmp_path, env, monkeypatch):
    p = _make(tmp_path)
    try:
        p.prefetch("看看完整兼容测试")
        p.on_session_switch("s1", reason="compression")
        p._ensure_engram_session("s1", "renren-drama")
        _break_compressor(monkeypatch)
        p.on_session_end([{"role": "assistant", "_compressed_summary": True, "content": "x"}])
        _flush(p)
        assert p._state("s1").closing and p._state("s1").confirmed_project is None
        assert env("mem_session_summary") == []
        assert env("mem_session_end"), "摘要提取失败不能跳过会话关闭"
    finally:
        p.shutdown()


def test_prompt_capture_survives_summary_marker_failure(tmp_path, env, monkeypatch):
    p = _make(tmp_path)
    query = "请检查完整的兼容测试并说明结果"
    try:
        p.prefetch(query)
        p.on_session_switch("s1", reason="compression")
        _break_compressor(monkeypatch)
        p.sync_turn(query, "回答", messages=[{"role": "assistant", "_compressed_summary": True, "content": "x"}])
        _flush(p)
        assert len(env("mem_save_prompt")) == 1
    finally:
        p.shutdown()


def test_exit_archives_available_formal_summary_before_end(tmp_path, env):
    p = _make(tmp_path)
    try:
        p.prefetch("看看完整兼容测试")
        p.on_session_switch("s1", reason="compression")
        p.on_session_end([_formal_summary("Formal summary at immediate exit.")])
        _flush(p)
        names = [x["name"] for x in env()]
        assert names.count("mem_session_summary") == 1
        assert names.index("mem_session_summary") < names.index("mem_session_end")
        p.on_session_end([_formal_summary("Formal summary at immediate exit.")])
        _flush(p)
        assert len(env("mem_session_summary")) == 1
    finally:
        p.shutdown()
