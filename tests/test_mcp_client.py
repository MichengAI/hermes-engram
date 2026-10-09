"""MCP stdio 客户端测试：使用假服务验证正常调用、超时、崩溃重启。"""

from __future__ import annotations

import os
import sys
import time

import pytest

from conftest import FAKE_SERVER
from engram.mcp_client import McpError, McpStdioClient


def _client(mode: str = "normal") -> McpStdioClient:
    env = {**os.environ, "FAKE_MODE": mode}
    return McpStdioClient([sys.executable, str(FAKE_SERVER)], env=env)


def test_call_tool_returns_text():
    client = _client()
    try:
        text = client.call_tool("mem_list_projects", {}, timeout=10)
        assert "dsh-codex-ui" in text
        # 第二次调用复用同一个进程
        pid = client.pid
        client.call_tool("mem_context", {"project": "x"}, timeout=10)
        assert client.pid == pid
    finally:
        client.close()


def test_tool_error_raises():
    client = _client()
    try:
        with pytest.raises(McpError):
            client.call_tool("nope", {}, timeout=10)
    finally:
        client.close()


def test_timeout_kills_process_and_raises():
    client = _client("hang")
    try:
        started = time.monotonic()
        with pytest.raises(McpError):
            client.call_tool("mem_list_projects", {}, timeout=1.0)
        assert time.monotonic() - started < 5
        assert not client.alive
    finally:
        client.close()


def test_crash_then_restart_on_next_call():
    client = _client("crash")
    try:
        with pytest.raises(McpError):
            client.call_tool("mem_list_projects", {}, timeout=5)
        client._env["FAKE_MODE"] = "normal"
        assert "dsh-codex-ui" in client.call_tool("mem_list_projects", {}, timeout=10)
    finally:
        client.close()


def test_missing_binary_raises_mcp_error():
    client = McpStdioClient(["Z:/definitely/not/engram.exe", "mcp"])
    with pytest.raises(McpError):
        client.call_tool("mem_list_projects", {}, timeout=2)
