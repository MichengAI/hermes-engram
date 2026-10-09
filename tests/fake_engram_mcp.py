"""假的 Engram MCP stdio 服务，仅供测试。

行为由环境变量控制：
- FAKE_MODE=normal（默认）：正常应答。
- FAKE_MODE=hang：收到 tools/call 后不应答，用于验证超时。
- FAKE_MODE=crash：收到 tools/call 后直接退出，用于验证重启。
- FAKE_CALL_LOG：若设置，把每次 tools/call 的名称和参数追加写入该文件（JSON 行）。
- FAKE_ENDED：逗号分隔的「已结束」会话 id，对它们 mem_session_start 返回 session_already_ended。
- FAKE_SESSION_PROJECT：若设置，mem_session_start 一律报告解析到该项目（模拟目录解析与目录表不一致）。
"""

from __future__ import annotations

import json
import os
import sys

PROJECTS = [
    {"name": "dsh-codex-ui", "directories": ["D:\\Repository\\deepseek-harness-plugin\\dsh-codex-ui"]},
    {"name": "renren-drama", "directories": ["D:\\Repository\\renren-drama"]},
    {"name": "no-dir-project", "directories": None},
]
_DIR_TO_PROJECT = {"d:\\repository\\deepseek-harness-plugin\\dsh-codex-ui": "dsh-codex-ui",
                   "d:\\repository\\renren-drama": "renren-drama"}


def _text(payload) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}]}


def _error(code: str) -> dict:
    return {"content": [{"type": "text", "text": json.dumps({"error_code": code, "message": code})}], "isError": True}


class State:
    def __init__(self) -> None:
        self.next_obs = 500
        self.ended = {s for s in os.environ.get("FAKE_ENDED", "").split(",") if s}
        self.started: set = set()


def _handle_tool(state: State, name: str, args: dict) -> dict:
    known = {p["name"] for p in PROJECTS}
    if args.get("project") and args["project"] not in known:
        return _error("unknown_project")
    sid = args.get("session_id")
    if sid and sid not in state.started and name in ("mem_save", "mem_save_prompt", "mem_session_summary", "mem_capture_passive"):
        return _error("unknown_session")
    if name == "mem_list_projects":
        return _text({"count": len(PROJECTS), "projects": PROJECTS})
    if name == "mem_context":
        return _text({"project": args.get("project"), "result": f"## Memory from Previous Sessions\n近期上下文-{args.get('project')}"})
    if name == "mem_search":
        results = [
            {"id": 101, "title": "兼容测试结论", "type": "decision", "preview": "peer 范围要全测"},
            {"id": 102, "title": "发布流程", "type": "pattern", "preview": "五平台全绿才建 Release"},
        ]
        return _text({"project": args.get("project"), "result": "Found 2 memories.", "results": results})
    if name == "mem_session_start":
        if args.get("id") in state.ended:
            return _error("session_already_ended")
        state.started.add(args.get("id"))
        directory = str(args.get("directory") or "").replace("/", "\\").rstrip("\\").lower()
        project = os.environ.get("FAKE_SESSION_PROJECT") or _DIR_TO_PROJECT.get(directory, "dir-basename")
        if project not in {p["name"] for p in PROJECTS}:
            PROJECTS.append({"name": project, "directories": [args.get("directory")]})
        return _text({"project": project, "result": "started"})
    if name == "mem_session_end":
        state.ended.add(args.get("id"))
        return _text({"result": "completed"})
    if name in ("mem_save", "mem_session_summary"):
        if name == "mem_session_summary" and os.environ.get("FAKE_SUMMARY_MODE") == "error":
            return _error("summary_rejected")
        state.next_obs += 1
        return _text({"id": state.next_obs, "judgment_required": False, "project": args.get("project"), "result": "Memory saved"})
    if name == "mem_save_prompt":
        return _text({"result": "Prompt saved"})
    if name == "mem_capture_passive":
        return _text({"result": "Fake transport acknowledgement; extraction is not simulated"})
    if name == "mem_get_observation":
        return _text({"id": args.get("id"), "content": "完整内容"})
    if name == "mem_judge":
        return _text({"result": "judged"})
    return _error("unknown_tool")


def main() -> None:
    mode = os.environ.get("FAKE_MODE", "normal")
    log_path = os.environ.get("FAKE_CALL_LOG")
    state = State()
    stdout = sys.stdout.buffer
    for raw in sys.stdin.buffer:
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
            if params.get("name") == "mem_session_summary" and os.environ.get("FAKE_SUMMARY_MODE") == "hang":
                continue
            if mode == "crash":
                sys.exit(3)
            result = _handle_tool(state, params.get("name", ""), params.get("arguments") or {})
        else:
            stdout.write((json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "no"}}) + "\n").encode())
            stdout.flush()
            continue
        stdout.write((json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}, ensure_ascii=False) + "\n").encode("utf-8"))
        stdout.flush()


if __name__ == "__main__":
    main()
