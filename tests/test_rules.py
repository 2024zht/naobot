import pytest

from nao_bot.rules import (
    HELP_TEXT,
    ai_question,
    command_argument,
    has_management_permission,
    is_allowed_group,
    parse_mute_duration,
    parse_qq_ids,
    reply_for_text,
    select_target_user_id,
)


@pytest.mark.parametrize(
    ("peer_id", "allowed_group_id", "expected"),
    [
        (123456789, 123456789, True),
        (123456790, 123456789, False),
    ],
)
def test_group_allowlist(peer_id, allowed_group_id, expected):
    assert is_allowed_group(peer_id, allowed_group_id) is expected


@pytest.mark.parametrize(
    ("message", "is_tome", "expected"),
    [
        ("帮助", True, HELP_TEXT),
        (" 状态 ", True, "nao 在线，运行正常。"),
        ("关于", True, "我是 nao 机器人助手，目前正在本群测试。"),
        ("帮助", False, None),
        ("/帮助", True, None),
        ("你好", False, "你好，我是 nao。"),
        ("状态 额外内容", True, None),
        ("普通聊天", False, None),
        ("", True, None),
    ],
)
def test_reply_rules(message, is_tome, expected):
    assert reply_for_text(message, is_tome) == expected


@pytest.mark.parametrize(
    ("message", "command", "expected"),
    [
        ("问 今天天气如何", "问", "今天天气如何"),
        (" 问 ", "问", ""),
        ("问题", "问", None),
        ("普通聊天", "问", None),
    ],
)
def test_command_argument(message, command, expected):
    assert command_argument(message, command) == expected


@pytest.mark.parametrize(
    ("message", "is_tome", "expected"),
    [
        ("问 今天天气如何", True, "问 今天天气如何"),
        ("问", True, "问"),
        ("介绍一下你自己", True, "介绍一下你自己"),
        ("@nao 我完成了项目", False, "我完成了项目"),
        ("@NAO 你在吗", False, "你在吗"),
        ("", True, ""),
        ("/问 今天天气如何", False, None),
        ("普通群聊", False, None),
    ],
)
def test_ai_question(message, is_tome, expected):
    assert ai_question(message, is_tome) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("禁言", 600),
        ("禁言 5", 300),
        ("禁言 0", 0),
        ("踢出", None),
    ],
)
def test_parse_mute_duration(message, expected):
    assert parse_mute_duration(message) == expected


@pytest.mark.parametrize("message", ["禁言 十", "禁言 -1", "禁言 43201"])
def test_invalid_mute_duration(message):
    with pytest.raises(ValueError):
        parse_mute_duration(message)


def test_parse_qq_ids():
    assert parse_qq_ids("123, 456,123") == frozenset({123, 456})
    assert parse_qq_ids("") == frozenset()
    with pytest.raises(ValueError):
        parse_qq_ids("123,abc")


@pytest.mark.parametrize(
    ("sender_id", "role", "admin_ids", "expected"),
    [
        (123, "admin", frozenset(), True),
        (123, "owner", frozenset(), True),
        (123, "member", frozenset(), False),
        (123, "member", frozenset({123}), True),
        (456, "owner", frozenset({123}), False),
    ],
)
def test_management_permission(sender_id, role, admin_ids, expected):
    assert has_management_permission(sender_id, role, admin_ids) is expected


def test_help_lists_fraud_keyword_commands():
    assert "@nao 添加违规" in HELP_TEXT
    assert "@nao 违规词列表" in HELP_TEXT
    assert "@nao 删除违规词" in HELP_TEXT
    assert "/" not in HELP_TEXT


def test_target_user_skips_bot_mention_and_falls_back_to_reply():
    assert select_target_user_id([123456789, 987654321], 123456789, None) == 987654321
    assert select_target_user_id([123456789], 123456789, 987654321) == 987654321
    assert select_target_user_id([123456789], 123456789, None) is None
