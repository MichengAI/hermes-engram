"""纯逻辑单元测试：项目判定、关键词提取、字节截断、结果格式化。"""

from __future__ import annotations

from engram.recall import (
    choose_project,
    extract_query_tokens,
    format_search_results,
    limit_utf8,
    parse_tool_json,
    resolve_project_from_dirs,
)

CATALOG = [
    {"name": "dsh-codex-ui", "directories": ["D:\\Repository\\deepseek-harness-plugin\\dsh-codex-ui", "D:/Repository/deepseek-harness-plugin/dsh-codex-ui"]},
    {"name": "dsh-codex-desktop", "directories": ["d:\\repository\\deepseek-harness-plugin\\dsh-codex-desktop"]},
    {"name": "codex", "directories": ["D:\\AI\\Codex"]},
    {"name": "waoowaoo-audit-source", "directories": ["D:\\AI\\Codex"]},
    {"name": "renren-drama", "directories": ["D:\\Repository\\renren-drama"]},
]


# ---- 目录绑定 ----

def test_dir_exact_and_child_match_case_and_slash_insensitive():
    assert resolve_project_from_dirs("d:/repository/deepseek-harness-plugin/dsh-codex-ui", CATALOG) == "dsh-codex-ui"
    assert resolve_project_from_dirs("D:\\Repository\\renren-drama\\src\\app", CATALOG) == "renren-drama"


def test_dir_prefix_must_stop_at_separator():
    # dsh-codex-ui-extra 不应命中 dsh-codex-ui
    assert resolve_project_from_dirs("D:\\Repository\\deepseek-harness-plugin\\dsh-codex-ui-extra", CATALOG) is None


def test_dir_ambiguous_same_length_returns_none():
    assert resolve_project_from_dirs("D:\\AI\\Codex\\x", CATALOG) is None


def test_broad_binding_only_matches_exactly():
    catalog = CATALOG + [{"name": "yujiyu", "directories": ["C:\\Users\\YUJIYU"]}, {"name": "root", "directories": ["D:\\"]}]
    broad = {"c:\\users\\yujiyu"}
    # 主目录、盘符根只能精确匹配，不能兜住所有子目录
    assert resolve_project_from_dirs("C:\\Users\\YUJIYU\\AppData\\Local\\hermes", catalog, broad_dirs=broad) is None
    assert resolve_project_from_dirs("D:\\AI\\HermesData", catalog, broad_dirs=broad) is None
    assert resolve_project_from_dirs("C:\\Users\\YUJIYU", catalog, broad_dirs=broad) == "yujiyu"
    # 更具体的绑定仍然正常
    assert resolve_project_from_dirs("D:\\Repository\\renren-drama\\x", catalog, broad_dirs=broad) == "renren-drama"


def test_dir_empty_or_unbound():
    assert resolve_project_from_dirs("", CATALOG) is None
    assert resolve_project_from_dirs("D:\\AI\\HermesData", CATALOG) is None


# ---- 项目选择 ----

def test_choose_explicit_mention_wins_over_cwd():
    project, reason = choose_project("继续 renren-drama 项目，看看架构", CATALOG,
                                     ["D:\\Repository\\deepseek-harness-plugin\\dsh-codex-ui"], None)
    assert (project, reason) == ("renren-drama", "mentioned")


def test_choose_mention_requires_word_boundary():
    # dsh-codex-ui 不应因包含 codex 而同时命中 codex
    project, _ = choose_project("看看 dsh-codex-ui 的兼容测试", CATALOG, [], None)
    assert project == "dsh-codex-ui"


def test_choose_multiple_mentions_skip():
    assert choose_project("对比 renren-drama 和 dsh-codex-ui", CATALOG, [], None) == (None, "ambiguous_mention")


def test_choose_unknown_project_hint_skip():
    assert choose_project("项目: foo-bar 怎么样", CATALOG, ["D:\\Repository\\renren-drama"], "renren-drama") == (None, "unknown_project")


def test_choose_cwd_binding_then_sticky():
    assert choose_project("看看架构", CATALOG, ["", "D:\\Repository\\renren-drama"], None) == ("renren-drama", "cwd")
    assert choose_project("继续检查", CATALOG, ["D:\\AI\\HermesData"], "dsh-codex-ui") == ("dsh-codex-ui", "sticky")


def test_choose_sticky_must_still_exist():
    assert choose_project("继续检查", CATALOG, [], "deleted-project") == (None, "no_project")


def test_choose_nothing():
    assert choose_project("hermes 怎么接入 engram", CATALOG, ["D:\\AI\\HermesData"], None) == (None, "no_project")


# ---- 关键词 ----

def test_tokens_split_cjk_into_bigrams_and_drop_stopwords():
    tokens = extract_query_tokens("你来看看兼容测试", exclude=set())
    assert "兼容" in tokens and "测试" in tokens
    assert "看看" not in tokens and "你来" not in tokens


def test_tokens_keep_ascii_words_and_exclude_project():
    tokens = extract_query_tokens("dsh-codex-ui 的 peer 范围 rc.2", exclude={"dsh-codex-ui"})
    assert "dsh-codex-ui" not in tokens
    assert "peer" in tokens and "范围" in tokens


def test_tokens_capped_and_unique():
    tokens = extract_query_tokens("测试 测试 " + " ".join(f"word{i}" for i in range(30)), exclude=set(), limit=8)
    assert len(tokens) == 8
    assert len(set(tokens)) == len(tokens)


# ---- 截断与格式化 ----

def test_limit_utf8_never_splits_multibyte_char():
    text = "中文" * 10
    out = limit_utf8(text, 7)
    assert len(out.encode("utf-8")) <= 7
    assert out == "中文"


def test_limit_utf8_passthrough():
    assert limit_utf8("abc", 10) == "abc"


def test_parse_tool_json_falls_back_to_text():
    assert parse_tool_json('{"a": 1}') == {"a": 1}
    assert parse_tool_json("plain") == {"result": "plain"}


def test_format_search_results_skips_seen_and_reports_ids():
    results = [
        {"id": 1, "title": "A", "type": "decision", "preview": "aaa\nbbb"},
        {"id": 2, "title": "B", "type": "bugfix", "preview": "ccc"},
    ]
    text, ids = format_search_results(results, seen={1})
    assert ids == [2]
    assert "#2" in text and "B" in text and "#1" not in text
