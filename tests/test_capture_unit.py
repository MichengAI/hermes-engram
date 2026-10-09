"""capture.py 纯函数测试。"""

from __future__ import annotations

from engram.capture import WRITE_TOOL_SCHEMAS, READ_TOOL_SCHEMAS, build_compaction_summary, engram_session_id


def test_session_id_is_stable_safe_and_per_project():
    a = engram_session_id("20261009_085122_d40732", "dsh-codex-ui")
    assert a == "hermes-20261009_085122_d40732-dsh-codex-ui"
    unsafe = engram_session_id("s 1/x", "p:q")
    assert unsafe.startswith("hermes-s_1_x-p_q-") and unsafe == engram_session_id("s 1/x", "p:q")
    assert unsafe != engram_session_id("s_1_x", "p_q")
    assert engram_session_id("s1", "a") != engram_session_id("s1", "b")
    assert len(engram_session_id("s" * 300, "p")) <= 120


def test_compaction_summary_keeps_user_assistant_text_only():
    msgs = [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "修复登录 bug"},
        {"role": "assistant", "content": [{"type": "text", "text": "根因是 token 过期没刷新"}]},
        {"role": "tool", "content": "工具输出"},
        {"role": "user", "content": "[CONTEXT COMPACTION — REFERENCE ONLY] 旧摘要"},
        {"role": "user", "content": "再补测试"},
    ]
    s = build_compaction_summary(msgs)
    assert s.startswith("## Goal\n修复登录 bug")
    assert "根因是 token 过期没刷新" in s and "再补测试" in s
    assert "系统提示" not in s and "工具输出" not in s and "旧摘要" not in s


def test_compaction_summary_empty_and_budget():
    assert build_compaction_summary([]) == ""
    msgs = [{"role": "assistant", "content": f"进展{i} " + "x" * 300} for i in range(100)]
    s = build_compaction_summary(msgs, max_chars=2000)
    assert len(s) < 3000
    assert "进展99" in s and "进展0 " not in s  # 预算内保留最近的


def test_tool_schemas_have_unique_prefixed_names():
    names = [s["name"] for s in READ_TOOL_SCHEMAS + WRITE_TOOL_SCHEMAS]
    assert len(names) == len(set(names))
    assert all(n.startswith("engram_") for n in names)
