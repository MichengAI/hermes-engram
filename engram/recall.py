"""召回逻辑的纯函数部分：项目判定、关键词提取、截断与格式化。

全部无副作用、不依赖 Hermes，单元测试直接覆盖。
"""

from __future__ import annotations

import json
import ntpath
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# 项目名边界只看 ASCII 单词字符和连字符：中文紧贴项目名（如「继续renren-drama项目」）也要能识别。
_NAME_LEFT = r"(?<![A-Za-z0-9_\-])"
_NAME_RIGHT = r"(?![A-Za-z0-9_\-])"

# 用户写了「项目: xxx」但 xxx 不是已知项目时跳过，绝不回退到其他项目。
_PROJECT_HINT_RE = re.compile(r"(?:项目|project)\s*[:：=]\s*\S+", re.IGNORECASE)

_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]{1,39}")
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")

# 含这些字的中文二元组基本是虚词、代词，不参与检索。
_CJK_STOP_CHARS = set("的了吗呢吧啊么你我他她它们这那是在和把被给让就都也还一个下")
_CJK_STOP_BIGRAMS = {"看看", "继续", "帮忙", "怎么", "什么", "为什", "如何", "可以", "现在", "然后", "还是", "没有", "来看"}
_ASCII_STOPWORDS = {"the", "and", "for", "with", "this", "that", "what", "how", "why", "can", "please", "is", "are", "to", "of", "in", "on"}


# ---- 目录绑定 ----

def path_key(path: str) -> str:
    """统一盘符大小写、斜杠和尾部分隔符，便于前缀比较。"""
    if not path or not str(path).strip():
        return ""
    normalized = ntpath.normpath(str(path).strip().replace("/", "\\"))
    return normalized.rstrip("\\").lower()


def resolve_project_from_dirs(cwd: str, catalog: Sequence[Dict[str, Any]],
                              broad_dirs: Optional[Set[str]] = None) -> Optional[str]:
    """用 Engram 项目目录绑定表匹配 cwd。

    cwd 等于或位于某个绑定目录下即命中，取最长前缀；最长前缀对应多个项目时返回 None（不猜）。
    broad_dirs（已规范化的路径键，如用户主目录）和盘符根只允许精确匹配：
    否则绑定在主目录上的项目会兜住主目录下所有子目录（临时目录、HERMES_HOME 等）。
    """
    cwd_key = path_key(cwd)
    if not cwd_key:
        return None
    broad = broad_dirs or set()
    best_len, best_names = -1, set()
    for item in catalog:
        name = str(item.get("name") or "")
        for directory in item.get("directories") or []:
            dir_key = path_key(directory)
            if not dir_key:
                continue
            exact_only = dir_key in broad or re.fullmatch(r"[a-z]:", dir_key) is not None
            if not (cwd_key == dir_key or (not exact_only and cwd_key.startswith(dir_key + "\\"))):
                continue
            if len(dir_key) > best_len:
                best_len, best_names = len(dir_key), {name}
            elif len(dir_key) == best_len:
                best_names.add(name)
    return next(iter(best_names)) if len(best_names) == 1 else None


def mentioned_projects(message: str, names: Iterable[str]) -> List[str]:
    """消息里按完整单词出现的项目名（不区分大小写）。"""
    found = []
    for name in names:
        if name and re.search(_NAME_LEFT + re.escape(name) + _NAME_RIGHT, message, re.IGNORECASE):
            found.append(name)
    return found


def choose_project(message: str, catalog: Sequence[Dict[str, Any]], cwd_candidates: Sequence[str],
                   sticky: Optional[str], *, broad_dirs: Optional[Set[str]] = None) -> Tuple[Optional[str], str]:
    """决定本轮读取哪个项目，返回 (项目名或 None, 原因)。

    优先级：消息点名唯一项目 > 工作目录绑定 > 本会话上次使用的项目。
    点名多个、写了未知项目、都判断不了时返回 None，绝不读取全部项目。
    """
    names = [str(item.get("name")) for item in catalog if item.get("name")]
    mentioned = mentioned_projects(message, names)
    if len(mentioned) == 1:
        return mentioned[0], "mentioned"
    if len(mentioned) > 1:
        return None, "ambiguous_mention"
    if _PROJECT_HINT_RE.search(message):
        return None, "unknown_project"
    for cwd in cwd_candidates:
        bound = resolve_project_from_dirs(cwd, catalog, broad_dirs)
        if bound:
            return bound, "cwd"
    if sticky and sticky in names:
        return sticky, "sticky"
    return None, "no_project"


# ---- 关键词 ----

def extract_query_tokens(message: str, *, exclude: Set[str], limit: int = 8) -> List[str]:
    """把用户消息拆成检索词。

    Engram 的 FTS 对整段中文（如「看看兼容测试」）基本搜不到，所以中文按重叠二元组切分，
    再去掉虚词；英文按单词。项目名本身不作为检索词。
    """
    excluded = {e.lower() for e in exclude}
    tokens: List[str] = []

    def _add(token: str) -> None:
        if token.lower() not in excluded and token not in tokens:
            tokens.append(token)

    for match in _ASCII_TOKEN_RE.finditer(message):
        word = match.group(0).strip(".-")
        if len(word) >= 2 and word.lower() not in _ASCII_STOPWORDS:
            _add(word)
    for run in _CJK_RUN_RE.findall(message):
        if len(run) == 1:
            continue
        for i in range(len(run) - 1):
            bigram = run[i:i + 2]
            if bigram in _CJK_STOP_BIGRAMS or any(ch in _CJK_STOP_CHARS for ch in bigram):
                continue
            _add(bigram)
    return tokens[:limit]


# ---- 截断与格式化 ----

def limit_utf8(text: str, budget: int) -> str:
    """按 UTF-8 字节数截断，不切断多字节字符。"""
    data = text.encode("utf-8")
    if len(data) <= budget:
        return text
    return data[:max(0, budget)].decode("utf-8", errors="ignore")


def parse_tool_json(text: str) -> Dict[str, Any]:
    """Engram 工具返回 JSON 文本；解析失败时当作纯文本结果。"""
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {"result": text}
    return value if isinstance(value, dict) else {"result": text}


def format_search_results(results: Sequence[Dict[str, Any]], *, seen: Set[int],
                          preview_chars: int = 200) -> Tuple[str, List[int]]:
    """把 mem_search 的 compact 结果格式化成列表，跳过本会话已注入过的条目。

    返回 (文本, 新注入的 observation id 列表)。
    """
    lines: List[str] = []
    new_ids: List[int] = []
    for item in results:
        obs_id = item.get("id")
        if not isinstance(obs_id, int) or obs_id in seen:
            continue
        preview = " ".join(str(item.get("preview") or "").split())
        if len(preview) > preview_chars:
            preview = preview[:preview_chars] + "…"
        title = str(item.get("title") or "").strip()
        kind = str(item.get("type") or "").strip()
        lines.append(f"- #{obs_id} [{kind}] {title}：{preview}".rstrip("："))
        new_ids.append(obs_id)
    return "\n".join(lines), new_ids
