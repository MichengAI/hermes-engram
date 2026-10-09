"""假的 Engram MCP stdio 服务，仅供测试。

行为由环境变量控制：
- FAKE_MODE=normal（默认）：正常应答 initialize / tools/call。
- FAKE_MODE=hang：收到 tools/call 后不应答，用于验证超时。
- FAKE_MODE=crash：收到 tools/call 后直接退出，用于验证重启。
- FAKE_CALL_LOG：若设置，把每次 tools/call 的名称和参数追加写入该文件（JSON 行）。
"""

from __future__ import annotations

import json
import os
import sys

PROJECTS = {
    "count": 2,
    "projects": [
        {"name": "dsh-codex-ui", "directories": ["D:\\Repository\\deepseek-harness-plugin\\dsh-codex-ui"]},
        {"name": "renren-drama", "directories": ["D:\\Repository\\renren-drama"]},
    ],
}


def _text(payload) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}]}


def _handle_tool(name: str, args: dict) -> dict:
    if name == "mem_list_projects":
        return _text(PROJECTS)
    if name == "mem_context":
        return _text({"project": args.get("project"), "result": f"## Memory from Previous Sessions\n近期上下文-{args.get('project')}"})
    if name == "mem_search":
        results = [
            {"id": 101, "title": "兼容测试结论", "type": "decision", "preview": "peer 范围要全测"},
            {"id": 102, "title": "发布流程", "type": "pattern", "preview": "五平台全绿才建 Release"},
        ]
        return _text({"project": args.get("project"), "result": "Found 2 memories.", "results": results})
    return {"content": [{"type": "text", "text": "unknown tool"}], "isError": True}


def main() -> None:
    mode = os.environ.get("FAKE_MODE", "normal")
    log_path = os.environ.get("FAKE_CALL_LOG")
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    for raw in stdin:
        line = raw.decode("utf-8").strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        if "id" not in msg:
            continue  # 通知无需应答
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "0"}}
        elif method == "tools/call":
            params = msg.get("params") or {}
            if log_path:
                with open(log_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"name": params.get("name"), "arguments": params.get("arguments")}, ensure_ascii=False) + "\n")
            if mode == "hang":
                continue
            if mode == "crash":
                sys.exit(3)
            result = _handle_tool(params.get("name", ""), params.get("arguments") or {})
        else:
            stdout.write((json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "no"}}) + "\n").encode())
            stdout.flush()
            continue
        stdout.write((json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}, ensure_ascii=False) + "\n").encode("utf-8"))
        stdout.flush()


if __name__ == "__main__":
    main()
