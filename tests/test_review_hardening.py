"""第二轮审查回归：可执行文件解析、脱敏、原生恢复、写线程与委派来源边界。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from engram import EngramMemoryProvider
from engram.capture import redact_private
from engram.mcp_client import McpError
from engram.native_runtime import NativeRuntime
from test_capture import REPO, _dispatch, _flush, _make, env


# ---- 可执行文件不能从当前目录或相对路径解析 ----

def test_default_binary_ignores_engram_in_current_directory(tmp_path, monkeypatch):
    planted = tmp_path / "engram.exe"
    planted.write_bytes(b"MZ")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "missing"))
    provider = EngramMemoryProvider(config={"engram_path": ""})
    assert provider._binary() == ""
    assert provider.is_available() is False


def test_default_binary_uses_absolute_path_entry(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    installed = bin_dir / ("engram.exe" if os.name == "nt" else "engram")
    installed.write_bytes(b"MZ")
    installed.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join([".", "relative-bin", str(bin_dir)]))
    resolved = EngramMemoryProvider(config={"engram_path": ""})._binary()
    assert Path(resolved).is_absolute() and Path(resolved).resolve() == installed.resolve()


def test_explicit_relative_engram_path_is_rejected(tmp_path, monkeypatch):
    planted = tmp_path / "engram.exe"
    planted.write_bytes(b"MZ")
    monkeypatch.chdir(tmp_path)
    provider = EngramMemoryProvider(config={"engram_path": "engram.exe"})
    assert provider._binary() == ""
    assert provider.is_available() is False


def test_git_bootstrap_uses_absolute_executable(tmp_path, env, monkeypatch):
    from engram import _system_executable
    seen = []
    original = subprocess.run
    def record(argv, *args, **kwargs):
        seen.append(argv[0])
        return original(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", record)
    repo = tmp_path / "fresh"
    repo.mkdir()
    original(["git", "init", "-q", str(repo)], check=True)
    p = _make(tmp_path, rows={"s1": str(repo)})
    try:
        p.prefetch("请检查新仓库的完整架构")
        git_calls = [argv for argv in seen if Path(argv).name.lower().startswith("git")]
        assert git_calls and all(Path(argv).is_absolute() for argv in git_calls)
        assert _system_executable("git") == git_calls[0]
    finally:
        p.shutdown()


def test_command_must_be_argument_list():
    provider = EngramMemoryProvider(config={"command": "engram mcp"})
    assert provider.is_available() is False


def test_client_version_comes_from_plugin_manifest():
    from engram.mcp_client import _plugin_version
    manifest = (Path(__file__).resolve().parents[1] / "engram" / "plugin.yaml").read_text(encoding="utf-8")
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    version = _plugin_version()
    assert f"version: {version}" in manifest and f'version = "{version}"' in pyproject


# ---- <private> 脱敏宁可多遮，不能漏 ----

@pytest.mark.parametrize("raw, leaked", [
    ("前文 <private>SECRET", "SECRET"),
    ("前文 <private >SECRET</private> 后文", "SECRET"),
    ("<private>a<private>SECRET</private>TAIL</private>", "TAIL"),
    ("<PRIVATE\n>SECRET</ private>", "SECRET"),
])
def test_private_redaction_fails_closed(raw, leaked):
    out = redact_private(raw)
    assert leaked not in out and "[REDACTED]" in out


def test_private_redaction_keeps_surrounding_text():
    assert redact_private("前 <private>x</private> 中 <private>y</private> 后") == "前 [REDACTED] 中 [REDACTED] 后"
    assert redact_private("没有私密标签") == "没有私密标签"


# ---- 原生子进程崩溃后可重新拉起 ----

def test_native_restarts_after_child_exit(tmp_path, monkeypatch):
    runtime = NativeRuntime(sys.executable, env=dict(os.environ), cwd=str(tmp_path))
    runtime._proc = subprocess.Popen([sys.executable, "-c", "pass"])
    runtime._proc.wait()
    runtime._instance = "old-instance"
    launched = []
    class FakeProc:
        pid = 1
        def poll(self):
            return None
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: launched.append(a) or FakeProc())
    monkeypatch.setattr(runtime, "request", lambda *a, **k: {"service": "engram", "status": "ok", "instance_id": "new-instance"})
    with pytest.raises(McpError, match="已退出"):
        runtime._ready(1)
    health = runtime._ready(1)  # 第二次调用重新拉起，不永久失效
    assert launched and health["instance_id"] == "new-instance"


# ---- 写线程退出窗口不能搁置任务 ----

def test_writer_exit_window_does_not_strand_job(tmp_path, env):
    p = _make(tmp_path)
    try:
        started, release = threading.Event(), threading.Event()
        def dying():
            started.set()
            release.wait(2)
        th = threading.Thread(target=dying)
        th.start()
        assert started.wait(2)
        with p._lock:
            p._writer = th
            p._writer_idle = True  # 模拟已从 get 超时、尚未真正退出
        ran = []
        p._enqueue(lambda: ran.append(1))
        release.set()
        th.join(2)
        assert p._join_writer(timeout=3) is True
        assert ran == [1]
    finally:
        p.shutdown()


def test_join_writer_reports_unfinished_queue(tmp_path, env):
    p = _make(tmp_path)
    try:
        block = threading.Event()
        p._enqueue(lambda: block.wait(2))
        assert p._join_writer(timeout=0.1) is False
        block.set()
        assert p._join_writer(timeout=3) is True
    finally:
        p.shutdown()


# ---- 委派来源随会话清理，上限不永久关闭捕获 ----

def test_delegation_sources_reset_with_new_session(tmp_path, env):
    p = _make(tmp_path, rows={"s1": REPO, "s2": REPO})
    try:
        p.prefetch("看看完整兼容测试")
        for index in range(2050):
            _dispatch(p, f"任务 {index}")
        p.on_session_end([])
        p.on_session_switch("s2", reset=True)
        p.prefetch("看看完整兼容测试", session_id="s2")
        _dispatch(p, "新会话任务")
        p.on_delegation("新会话任务", "## Key Learnings:\n1. " + "New session delegation must still be captured. " * 2)
        _flush(p)
        assert len(env("mem_capture_passive")) == 1
        assert all(target is None or target[0] == "s2" for target in p._delegation_sources.values())
        # 已结束会话的状态保留 closing 守卫：迟到的旧 ID 召回不能重新获得写权限。
        assert p._state("s1").closing
        p.prefetch("看看完整兼容测试", session_id="s1")
        assert p._state("s1").confirmed_project is None
    finally:
        p.shutdown()
