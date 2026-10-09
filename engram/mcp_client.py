"""极简 MCP stdio 客户端：常驻一个 ``engram mcp`` 子进程，按需重启。

只实现插件需要的部分：initialize 握手 + tools/call。
- 子进程第一次调用时才启动（懒启动），之后复用。
- 单次调用超时会杀掉子进程，下次调用自动重启，避免卡死的进程拖垮后续轮次。
- 不依赖 Hermes，便于独立测试。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("plugins.engram")

_PROTOCOL_VERSION = "2024-11-05"


def _error_code(text: str) -> str:
    """从 Engram 错误文本里取 error_code；不是 JSON 时返回空串。"""
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return ""
    return str(value.get("error_code") or "") if isinstance(value, dict) else ""
_EOF = object()  # 读线程遇到子进程退出时投递的哨兵


class McpError(RuntimeError):
    """MCP 调用失败（启动失败、超时、子进程退出或工具返回错误）。

    消息里只放错误类别，不放记忆正文或用户输入。
    """


class McpToolError(McpError):
    """工具返回 isError：error_code 取自 Engram 的结构化错误（如 session_already_ended）。"""

    def __init__(self, error_code: str = "") -> None:
        super().__init__(f"工具返回错误：{error_code or 'unknown'}")
        self.error_code = error_code


class McpStdioClient:
    """线程安全的 MCP stdio 客户端。

    入参：
        argv: 子进程命令行，例如 ``["engram.exe", "mcp", "--tools=mem_search"]``。
        env:  子进程环境变量；None 表示继承当前进程。
        cwd:  子进程工作目录；None 表示继承。
    """

    def __init__(self, argv: List[str], *, env: Optional[Dict[str, str]] = None, cwd: Optional[str] = None) -> None:
        self._argv = list(argv)
        self._env = dict(env) if env is not None else None
        self._cwd = cwd
        self._proc: Optional[subprocess.Popen] = None
        self._responses: "queue.Queue[Any]" = queue.Queue()
        self._lock = threading.Lock()
        self._next_id = 0

    # ---- 状态 ----

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> Optional[int]:
        return self._proc.pid if self._proc is not None else None

    # ---- 对外接口 ----

    def call_tool(self, name: str, arguments: Dict[str, Any], *, timeout: float) -> str:
        """调用一个工具，返回所有 text 内容拼接后的字符串。

        timeout 是本次调用（含必要时的启动握手）的总秒数。
        """
        deadline = time.monotonic() + max(0.1, float(timeout))
        with self._lock:
            if not self.alive:
                self._start(deadline)
            result = self._request("tools/call", {"name": name, "arguments": arguments}, deadline)
        if not isinstance(result, dict):
            raise McpError("响应格式异常")
        parts = [c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict) and c.get("type") == "text"]
        text = "\n".join(p for p in parts if p)
        if result.get("isError"):
            raise McpToolError(_error_code(text))
        return text

    def close(self) -> None:
        with self._lock:
            self._kill()

    # ---- 内部实现 ----

    def _start(self, deadline: float) -> None:
        self._kill()
        self._responses = queue.Queue()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            self._proc = subprocess.Popen(
                self._argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=self._env, cwd=self._cwd, creationflags=flags,
            )
        except OSError as exc:
            self._proc = None
            raise McpError(f"无法启动 Engram：{type(exc).__name__}") from exc
        reader = threading.Thread(target=self._read_loop, args=(self._proc, self._responses),
                                  name="engram-mcp-reader", daemon=True)
        reader.start()
        self._request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "hermes-engram", "version": "0.2.0"},
        }, deadline)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    @staticmethod
    def _read_loop(proc: subprocess.Popen, out: "queue.Queue[Any]") -> None:
        """后台读取子进程 stdout，每行一个 JSON-RPC 消息。"""
        stream = proc.stdout
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    out.put(json.loads(line))
                except ValueError:
                    continue  # 非 JSON 行（例如升级提示）直接忽略
        except Exception:  # 管道被关闭等
            pass
        finally:
            out.put(_EOF)

    def _send(self, message: Dict[str, Any]) -> None:
        if not self.alive:
            raise McpError("Engram 已退出")
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
        except OSError as exc:
            self._kill()
            raise McpError("写入 Engram 失败") from exc

    def _request(self, method: str, params: Dict[str, Any], deadline: float) -> Any:
        self._next_id += 1
        request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill()
                raise McpError("调用超时")
            try:
                message = self._responses.get(timeout=remaining)
            except queue.Empty:
                self._kill()
                raise McpError("调用超时")
            if message is _EOF:
                self._kill()
                raise McpError("Engram 提前退出")
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue  # 通知或过期响应
            if message.get("error"):
                raise McpError("Engram 返回协议错误")
            return message.get("result")

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                logger.debug("Engram 子进程未能及时退出")
