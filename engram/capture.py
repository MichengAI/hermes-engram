"""自动写入辅助：会话 id、脱敏、工具 schema，以及经核实的 Hermes 输入/摘要格式。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterator, List

# 与 Engram 官方 Codex 插件 session-start 注入的协议同义，按 Hermes 工具名改写。
MEMORY_PROTOCOL = (
    "### Engram 记忆协议（项目={project}）\n"
    "- 主动保存：做出决定、修复 bug（写根因）、发现坑、确立约定、用户表达偏好或确认/否决方案后，"
    "立即调用 engram_save（content 用 **What** / **Why** / **Where** / **Learned** 结构，标题简短可搜索）。\n"
    "- engram_save 返回 judgment_required 时，对 candidates 里每一项用 engram_judge 给出判断。\n"
    "- 需要更早的细节用 engram_search，看完整内容用 engram_get。\n"
    "- 委派子代理时，要求它在结果末尾输出 `## Key Learnings:` 编号列表（每条一句完整的话，中英文词语之间留空格），"
    "结束后会被自动捕获。\n"
    "- 一段工作完成、准备说「做完了」之前，调用 engram_session_summary"
    "（## Goal / ## Instructions / ## Discoveries / ## Accomplished / ## Next Steps / ## Relevant Files）。\n"
    "- 记忆操作是内部记账：先完成记忆写入，再给出最终答复；写入失败也照常答复。"
)

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")
# 开/闭标签允许空白与大小写变体；标签本身按配对计数处理，见 _redact_text。
_PRIVATE_TAG_RE = re.compile(r"<\s*(/?)\s*private\b[^>]*>", re.IGNORECASE)


def _redact_text(text: str) -> str:
    """配对替换 private 块；未闭合或嵌套时宁可多遮，不让秘密漏出。

    官方 Pi 与 Engram 存储层只认严格的 ``<private>...</private>``；这里在其约定上
    fail-closed：未闭合的开标签从开标签遮到结尾，嵌套按深度计数到最外层闭标签。
    """
    if "private" not in text.lower():
        return text
    out: List[str] = []
    depth = 0
    cursor = 0
    for match in _PRIVATE_TAG_RE.finditer(text):
        closing = bool(match.group(1))
        if depth == 0:
            if closing:
                continue  # 孤立闭标签不含秘密，原样保留
            out.append(text[cursor:match.start()])
            depth = 1
        elif closing:
            depth -= 1
            if depth == 0:
                out.append("[REDACTED]")
                cursor = match.end()
        else:
            depth += 1
    if depth:
        out.append("[REDACTED]")  # 未闭合：从开标签起全部遮掉
        return "".join(out)
    out.append(text[cursor:])
    return "".join(out)


def redact_private(value: Any) -> Any:
    """递归替换显式 private 块；不是通用密钥扫描器。"""
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [redact_private(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_private(item) for key, item in value.items()}
    return value


def tool_result_texts(value: Any, *, _depth: int = 0, _text: bool = True) -> Iterator[str]:
    """解开工具 JSON 包装，递归提取正文；不把状态/计数/目标等元数据当作正文。"""
    if _depth >= 20:
        return
    if isinstance(value, str) and _text:
        stripped = value.strip()
        if stripped.startswith(("{", "[", '"')):
            try:
                decoded = json.loads(stripped)
            except (ValueError, RecursionError):
                pass
            else:
                yield from tool_result_texts(decoded, _depth=_depth + 1)
                return
        if stripped:
            yield stripped
    elif isinstance(value, dict):
        fields = {"text", "content", "summary", "output", "stdout", "stderr", "result"}
        for key, item in value.items():
            if key in fields or isinstance(item, (dict, list)):
                yield from tool_result_texts(item, _depth=_depth + 1, _text=key in fields)
    elif isinstance(value, list):
        for item in value:
            yield from tool_result_texts(item, _depth=_depth + 1, _text=_text)

# Hermes context_compressor 的内部用户行标记；OUT-OF-BAND 可能包含真人输入，不排除它。
_SYNTHETIC_PREFIXES = (
    "[System:", "[CONTEXT", "[PRIOR CONTEXT", "[IMPORTANT: Background",
    "[Your active task list", "[Planning state preserved", "[ASYNC DELEGATION", "Cronjob Response:",
)


def is_synthetic_input(text: str) -> bool:
    """只识别宿主已有的内部标记，不推断未提供的 extension/source 元数据。"""
    if text.lstrip().startswith(_SYNTHETIC_PREFIXES):
        return True
    try:
        from agent.context_compressor import (
            COMPRESSION_CONTINUATION_USER_CONTENT, MAX_ITERATIONS_SUMMARY_REQUEST,
        )
        return text.strip() in (COMPRESSION_CONTINUATION_USER_CONTENT, MAX_ITERATIONS_SUMMARY_REQUEST)
    except (ImportError, AttributeError):
        return False


def engram_session_id(hermes_session_id: str, project: str) -> str:
    """Hermes 会话 + 项目 → Engram 会话 id。

    同一个 Hermes 会话可能先后涉及多个项目，按项目拆开，保证每个 Engram 会话只属于一个项目。
    """
    raw = f"hermes-{hermes_session_id}-{project}"
    safe = _SAFE_ID_RE.sub("_", raw)
    # 普通旧 ID 保持兼容；替换、截断及分隔歧义必须由原始二元身份消歧。
    if safe == raw and len(raw) <= 120 and "-" not in hermes_session_id:
        return raw
    digest = hashlib.sha256(json.dumps([hermes_session_id, project], ensure_ascii=False).encode("utf-8")).hexdigest()[:24]
    return safe[:95] + "-" + digest


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # OpenAI 多模态分段
        return "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def extract_formal_summary(messages: List[Dict[str, Any]]) -> str:
    """从宿主正式压缩 carrier 提取最新摘要，绝不合成对话摘录。

    标记常量只认宿主当前定义；宿主重构或依赖缺失时返回空，不用猜测的兜底标记，
    以免误把普通文本当摘要归档。
    """
    try:
        from agent.context_compressor import SUMMARY_PREFIX, HISTORICAL_TASK_HEADING, _SUMMARY_END_MARKER
    except (ImportError, AttributeError):
        return ""
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") not in ("assistant", "user", "system"):
            continue
        text = _text_of(msg.get("content"))
        start = text.find(SUMMARY_PREFIX)
        if start < 0 or (start > 0 and not msg.get("_compressed_summary")):
            continue
        if msg.get("role") == "user" and not msg.get("_compressed_summary"):
            continue
        end = text.find(_SUMMARY_END_MARKER, start + len(SUMMARY_PREFIX))
        if end < 0:
            continue
        body = text[start + len(SUMMARY_PREFIX):end].strip()
        if body.startswith(HISTORICAL_TASK_HEADING):
            body = body[len(HISTORICAL_TASK_HEADING):].strip()
        return redact_private(body)
    return ""


def build_compaction_summary(messages: List[Dict[str, Any]], *, max_chars: int = 6000,
                             item_chars: int = 400) -> str:
    """已弃用：provider 不再调用，保留只为兼容外部引用；新代码请用 extract_formal_summary。

    只取用户与助手的文字（不含工具输出和系统消息），按时间顺序保留最近的部分，
    对应 Codex post-compaction 钩子要求的「把压缩摘要存进 mem_session_summary」。
    """
    asks: List[str] = []
    answers: List[str] = []
    for msg in messages:
        role = msg.get("role") if isinstance(msg, dict) else None
        text = " ".join(redact_private(_text_of(msg.get("content"))).split()) if role in ("user", "assistant") else ""
        if not text or text.startswith("[CONTEXT COMPACTION"):
            continue
        clipped = text if len(text) <= item_chars else text[:item_chars] + "…"
        (asks if role == "user" else answers).append(clipped)
    if not asks and not answers:
        return ""
    goal = asks[0] if asks else "（无用户提问）"

    def _section(title: str, items: List[str]) -> str:
        return f"## {title}\n" + "\n".join(f"- {x}" for x in items) if items else ""

    # 从最新往回装，保证预算内保留最近的进展
    def _fit(items: List[str], budget: int) -> List[str]:
        kept: List[str] = []
        used = 0
        for item in reversed(items):
            used += len(item) + 3
            if used > budget:
                break
            kept.append(item)
        return list(reversed(kept))

    budget = max(500, max_chars - len(goal) - 200)
    parts = [
        f"## Goal\n{goal}",
        "## Instructions\n（Hermes 上下文压缩前自动存档，内容为对话摘录）",
        _section("Discoveries", _fit(answers, budget // 2)),
        _section("Accomplished", _fit(asks[1:], budget // 2)),
    ]
    return "\n\n".join(p for p in parts if p)


def _schema(name: str, description: str, properties: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required}}


_PROJECT_PROP = {"type": "string", "description": "Engram 项目名；省略时使用当前会话已确定的项目。"}

READ_TOOL_SCHEMAS = [
    _schema("engram_recover_save", "保存结果不确定时，只读查询原 operation_id；不会重新派发保存。需要 native_http。",
            {"operation_id": {"type": "string", "description": "原生保存返回的 UUID 操作标识"}}, ["operation_id"]),
    _schema("engram_search", "在 Engram 中按项目检索历史决定、bug 修复、约定等记忆。自动召回不够时使用。",
            {"query": {"type": "string", "description": "关键词或自然语言"},
             "project": _PROJECT_PROP,
             "limit": {"type": "integer", "description": "最多返回条数，默认 5，最大 20"}},
            ["query"]),
    _schema("engram_get", "按 observation id 读取一条 Engram 记忆的完整内容（检索结果只显示预览时使用）。",
            {"id": {"type": "integer", "description": "observation id，如召回结果里的 #123"}},
            ["id"]),
]

WRITE_TOOL_SCHEMAS = [
    _schema("engram_save",
            "主动保存一条记忆到 Engram：决定、bug 修复、发现、约定、用户偏好等，不要等用户要求。"
            "content 用 **What** / **Why** / **Where** / **Learned** 结构。",
            {"title": {"type": "string", "description": "简短可搜索的标题"},
             "content": {"type": "string", "description": "**What** / **Why** / **Where** / **Learned** 结构的正文"},
             "type": {"type": "string", "description": "decision | architecture | bugfix | pattern | config | discovery | learning"},
             "topic_key": {"type": "string", "description": "可选，同一主题反复更新时使用（如 architecture/auth-model）"},
             "project": _PROJECT_PROP},
            ["title", "content"]),
    _schema("engram_session_summary",
            "保存本次工作的会话总结到 Engram。一段工作完成、准备结束前调用。",
            {"content": {"type": "string",
                         "description": "## Goal / ## Instructions / ## Discoveries / ## Accomplished / ## Next Steps / ## Relevant Files"},
             "project": _PROJECT_PROP},
            ["content"]),
    _schema("engram_judge",
            "engram_save 返回 judgment_required 时，对 candidates 中每一项记录判断。",
            {"judgment_id": {"type": "string", "description": "candidates[].judgment_id"},
             "relation": {"type": "string", "description": "related | compatible | scoped | conflicts_with | supersedes | not_conflict"},
             "reason": {"type": "string", "description": "判断理由"},
             "confidence": {"type": "number", "description": "0~1，默认 1"}},
            ["judgment_id", "relation"]),
]
