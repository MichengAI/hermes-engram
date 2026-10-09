"""可选原生 HTTP 增强：插件自管回环服务，复用 Engram 正式恢复与写结果协议。

默认仍走 MCP；不连接用户任意 URL，不安装或升级 Engram。所有出站参数先脱敏，
保存响应不确定时仅查询原 operation_id，不重新提交 POST。
"""
from __future__ import annotations

import http.client
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from .capture import redact_private
from .mcp_client import McpError, McpToolError


class UnknownWrite(McpError):
    """写入结果不确定，保留只读恢复标识，不暴露正文。"""
    def __init__(self, operation_id: str) -> None:
        super().__init__("保存结果不确定，禁止盲目重放")
        self.operation_id = operation_id


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise McpError("原生 Engram 不允许重定向")


class NativeRuntime:
    """只启动自己管理的 Engram HTTP 服务，地址和实例身份不可随写入变化。"""
    def __init__(self, binary: str, *, env: dict[str, str], cwd: str) -> None:
        self._binary, self._env, self._cwd = binary, env, cwd
        self._url = ""
        self._instance = ""
        self._proc: subprocess.Popen | None = None
        self._lock = threading.RLock()
        self._test = False
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    @classmethod
    def for_test(cls, url: str) -> NativeRuntime:
        """测试注入本机 HTTP 服务；生产配置不暴露 URL 参数。"""
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("只允许无额外参数的 IPv4 回环 URL")
        obj = cls("", env={}, cwd="")
        obj._url, obj._test = url, True
        return obj

    def _ready(self, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self._lock:
            if not self._test and self._proc is None:
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    port = sock.getsockname()[1]
                self._url = f"http://127.0.0.1:{port}"
                try:
                    self._proc = subprocess.Popen([self._binary, "serve", str(port)], env=self._env,
                        cwd=self._cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                except OSError as exc:
                    raise McpError("原生 Engram 启动失败") from exc
            while True:
                if self._proc is not None and self._proc.poll() is not None:
                    raise McpError("原生 Engram 服务已退出")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise McpError("原生 Engram 启动超时")
                try:
                    health = self.request("GET", "/health", timeout=min(remaining, 0.5))
                    break
                except McpError:
                    if self._instance or self._test:
                        raise
                    time.sleep(min(0.05, max(0, deadline - time.monotonic())))
            instance = health.get("instance_id")
            if health.get("service") != "engram" or health.get("status") != "ok" or not isinstance(instance, str) or not instance:
                raise McpError("无法验证原生 Engram 实例身份")
            if self._instance and self._instance != instance:
                raise McpError("原生 Engram 实例已改变，拒绝继续写入")
            self._instance = instance
            return health

    def request(self, method: str, path: str, body: dict[str, Any] | None = None, *, timeout: float) -> dict[str, Any]:
        """执行一次有界 JSON 请求；协议错误只保留错误码，日志不携带正文。"""
        if not self._url or not path.startswith("/") or path.startswith("//"):
            raise McpError("无效原生 Engram 请求")
        raw = json.dumps(redact_private(body), ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self._url + path, data=raw, method=method,
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self._opener.open(req, timeout=max(0.05, timeout)) as response:
                data = response.read(2_000_001)
                encoding = response.headers.get_content_charset() or "utf-8"
            if len(data) > 2_000_000:
                raise McpError("原生 Engram 响应超出限制")
            value = json.loads(data.decode(encoding))
            if not isinstance(value, dict):
                raise McpError("原生 Engram 响应不是对象")
            return value
        except urllib.error.HTTPError as exc:
            try:
                value = json.loads(exc.read(10000).decode("utf-8"))
                code = str(value.get("code") or value.get("error_code") or f"http_{exc.code}")
            except (ValueError, AttributeError, UnicodeError):
                code = f"http_{exc.code}"
            raise McpToolError(code) from None
        except (OSError, http.client.HTTPException, ValueError, UnicodeError) as exc:
            raise McpError("原生 Engram 传输或响应异常") from exc

    def register(self, root: str, project: str, directory: str, *, isolated: bool = False, timeout: float) -> str:
        """能力探测后注册；仅采用服务端确认的 root 或其 continuation。"""
        deadline = time.monotonic() + timeout
        health = self._ready(timeout)
        caps = health.get("capabilities") or {}
        if isolated and caps.get("isolated_session_registration") is not True:
            raise McpError("缺少 isolated_session_registration 能力")
        body = {"id": root, "project": project, "directory": "" if isolated else directory,
                "ownership_mode": "project_owned", "resume": caps.get("root_session_resume") is True,
                "isolated": isolated}
        response = self.request("POST", "/sessions", body, timeout=self._remaining(deadline))
        effective = response.get("id")
        if response.get("status") != "created" or not isinstance(effective, str) or not (
                effective == root or re.fullmatch(re.escape(root) + r":resume:[0-9]+", effective)):
            raise McpError("服务端未确认合法会话身份")
        return effective

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise McpError("原生 Engram 超过调用预算")
        return remaining

    def recover_save(self, operation_id: str, *, timeout: float) -> dict[str, Any]:
        """只读查询一次原保存结果，绝不重新派发写入。"""
        uuid.UUID(operation_id)
        result = self.request("GET", "/observations/save-result?" + urllib.parse.urlencode({"operation_id": operation_id}), timeout=timeout)
        if isinstance(result.get("id"), bool) or not isinstance(result.get("id"), int) or result["id"] <= 0:
            raise McpError("保存查询未确认有效 observation ID")
        return result

    def save(self, body: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        """为一次保存冻结正文和 operation_id，丢失应答时只读恢复。"""
        deadline = time.monotonic() + timeout
        self._ready(timeout)
        operation_id = str(uuid.uuid4())
        # 先检查查询协议，旧核心不能静默忽略 operation_id 后冒充幂等支持。
        try:
            self.recover_save(operation_id, timeout=self._remaining(deadline))
            raise McpError("未使用的 operation_id 已存在")
        except McpToolError as exc:
            if exc.error_code != "observation_save_result_not_found":
                raise McpError("当前 Engram 不支持保存结果查询，请升级核心") from None
        frozen = redact_private({**body, "operation_id": operation_id})
        try:
            result = self.request("POST", "/observations", frozen, timeout=self._remaining(deadline))
            if isinstance(result.get("id"), bool) or not isinstance(result.get("id"), int) or result["id"] <= 0:
                raise McpError("保存响应未确认有效 ID")
            return result
        except McpToolError as exc:
            if exc.error_code not in ("http_409", "http_410"):
                raise
        except McpError:
            pass
        for _ in range(3):
            try:
                return self.recover_save(operation_id, timeout=self._remaining(deadline))
            except McpError:
                if deadline - time.monotonic() <= 0.05:
                    break
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        raise UnknownWrite(operation_id)

    def compaction_context(self, session_id: str, *, timeout: float) -> str:
        """读取当前会话的专用压缩恢复上下文，不改变系统提示。"""
        self._ready(timeout)
        result = self.request("GET", "/context/compaction?" + urllib.parse.urlencode({"session_id": session_id}), timeout=timeout)
        return str(result.get("context") or "")

    def tool(self, name: str, args: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        """只转换已核实的正式端点，其余工具仍交给 MCP。"""
        self._ready(timeout)
        if name == "mem_save":
            return self.save(args, timeout=timeout)
        if name == "mem_session_summary":
            return self.save({**args, "title": "Compaction recovery summary", "type": "session_summary",
                              "topic_key": "session/compaction-recovery", "capture_prompt": False}, timeout=timeout)
        if name == "mem_save_prompt":
            return self.request("POST", "/prompts", args, timeout=timeout)
        if name == "mem_capture_passive":
            return self.request("POST", "/observations/passive", args, timeout=timeout)
        if name == "mem_session_end":
            return self.request("POST", "/sessions/" + urllib.parse.quote(str(args["id"]), safe="") + "/end", {}, timeout=timeout)
        raise McpError("未支持的原生工具")

    def close(self) -> None:
        """仅停止本对象启动的子进程，不碰用户已有服务。"""
        with self._lock:
            proc, self._proc = self._proc, None
            if proc is None:
                return
            if proc.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
