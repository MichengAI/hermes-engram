"""原生 HTTP 增强的协议回归：真实本机 HTTP 服务器，不接触用户记忆库。"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.fixture
def server():
    events, ledger = [], {}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            events.append(("GET", self.path, None))
            if self.path == "/health":
                self.reply(200, {"service": "engram", "status": "ok", "instance_id": "probe-instance",
                    "capabilities": {"isolated_session_registration": True, "root_session_resume": True}})
            elif self.path.startswith("/observations/save-result?"):
                from urllib.parse import parse_qs, urlsplit
                key = parse_qs(urlsplit(self.path).query)["operation_id"][0]
                self.reply(200, ledger[key]) if key in ledger else self.reply(404,
                    {"code": "observation_save_result_not_found", "error": "no committed result found for operation_id"})
            elif self.path.startswith("/context/compaction?"):
                self.reply(200, {"context": "服务器确认的压缩恢复上下文"})
            else:
                self.reply(404, {"code": "not_found"})
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
            events.append(("POST", self.path, payload))
            if self.path == "/sessions":
                self.reply(200, {"status": "created", "id": payload["id"] + ":resume:1"})
            elif self.path == "/observations":
                ledger[payload["operation_id"]] = {"id": 42, "project": payload["project"]}
                # 已提交但响应丢失：客户端必须只读查询，不能重新 POST。
                self.close_connection = True
                self.connection.close()
            else:
                self.reply(200, {"status": "completed"})
        def reply(self, status, payload):
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=srv.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{srv.server_port}", events
    srv.shutdown()
    srv.server_close()
    worker.join(5)


def test_save_lost_ack_recovers_committed_result_without_reposting(server):
    from engram.native_runtime import NativeRuntime
    url, events = server
    runtime = NativeRuntime.for_test(url)
    result = runtime.save({"session_id": "s1", "project": "a", "title": "决定", "content": "<private>秘密</private>正文"}, timeout=2)
    assert result["id"] == 42
    posts = [e for e in events if e[0] == "POST" and e[1] == "/observations"]
    assert len(posts) == 1 and posts[0][2]["operation_id"]
    assert posts[0][2]["content"] == "[REDACTED]正文"
    assert any(e[1].startswith("/observations/save-result?") for e in events)


def test_satellite_requires_server_capability(server, monkeypatch):
    from engram.native_runtime import NativeRuntime
    from engram.mcp_client import McpError
    runtime = NativeRuntime.for_test(server[0])
    original = runtime.request
    def no_capability(method, path, *args, **kwargs):
        if path == "/health":
            return {"service": "engram", "status": "ok", "instance_id": "probe-instance", "capabilities": {}}
        return original(method, path, *args, **kwargs)
    monkeypatch.setattr(runtime, "request", no_capability)
    with pytest.raises(McpError, match="isolated_session_registration"):
        runtime.register("root@b", "b", "", isolated=True, timeout=2)
    assert not any(e[0] == "POST" for e in server[1])


def test_root_resume_accepts_only_server_confirmed_identity(server):
    from engram.native_runtime import NativeRuntime
    runtime = NativeRuntime.for_test(server[0])
    assert runtime.register("root", "a", "D:/repo", timeout=2) == "root:resume:1"
    body = next(e[2] for e in server[1] if e[0] == "POST")
    assert body["resume"] is True and body["ownership_mode"] == "project_owned"


def test_unknown_save_outcome_has_operation_id_and_no_replay(server, monkeypatch):
    from engram.native_runtime import NativeRuntime, UnknownWrite
    runtime = NativeRuntime.for_test(server[0])
    original = runtime.request
    lookups = 0
    def lost(method, path, *args, **kwargs):
        nonlocal lookups
        if path.startswith("/observations/save-result?"):
            lookups += 1
            if lookups > 1:  # 写入前协议探测正常，提交后的查询才断开。
                from engram.mcp_client import McpError
                raise McpError("无法确认")
        return original(method, path, *args, **kwargs)
    monkeypatch.setattr(runtime, "request", lost)
    with pytest.raises(UnknownWrite) as exc:
        runtime.save({"session_id": "s1", "project": "a", "title": "决定", "content": "正文"}, timeout=2)
    assert exc.value.operation_id
    assert len([e for e in server[1] if e[0] == "POST"]) == 1


def test_compaction_context_reads_server_context(server):
    from engram.native_runtime import NativeRuntime
    assert NativeRuntime.for_test(server[0]).compaction_context("s1", timeout=2) == "服务器确认的压缩恢复上下文"


def test_runtime_rejects_non_loopback_urls():
    from engram.native_runtime import NativeRuntime
    with pytest.raises(ValueError):
        NativeRuntime.for_test("http://example.com:7437")
