import json

import pytest

from nao_bot.keywords import MAX_KEYWORDS, KeywordCommand, KeywordStore, parse_keyword_command


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("关键词 添加 早上好=早上好呀", KeywordCommand("add", "早上好", "早上好呀")),
        ("关键词 添加 晚安＝做个好梦", KeywordCommand("add", "晚安", "做个好梦")),
        ("关键词 删除 早上好", KeywordCommand("delete", "早上好")),
        ("关键词 列表", KeywordCommand("list")),
        ("/关键词 列表", None),
        ("普通消息", None),
    ],
)
def test_parse_keyword_command(message, expected):
    assert parse_keyword_command(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        "关键词",
        "关键词 添加 缺少分隔符",
        "关键词 添加 =空触发",
        "关键词 添加 /状态=冲突",
        "关键词 未知操作",
    ],
)
def test_invalid_keyword_command(message):
    with pytest.raises(ValueError):
        parse_keyword_command(message)


def test_keyword_store_persists_updates_and_deletes(tmp_path):
    path = tmp_path / "keywords.json"
    store = KeywordStore(path)

    assert store.add("早上好", "早上好呀") is True
    assert store.add("早上好", "今天也要开心") is False
    assert KeywordStore(path).get("早上好") == "今天也要开心"
    assert store.delete("不存在") is False
    assert store.delete("早上好") is True
    assert KeywordStore(path).get("早上好") is None


def test_keyword_store_writes_utf8_json(tmp_path):
    path = tmp_path / "keywords.json"
    store = KeywordStore(path)
    store.add("晚安", "做个好梦")

    assert json.loads(path.read_text(encoding="utf-8")) == {"晚安": "做个好梦"}


def test_keyword_store_limit(tmp_path):
    store = KeywordStore(tmp_path / "keywords.json")
    for index in range(MAX_KEYWORDS):
        store.add(f"词{index}", f"回复{index}")

    with pytest.raises(ValueError):
        store.add("超出限制", "不会保存")
