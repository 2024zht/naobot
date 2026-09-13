import pytest

from nao_bot.rules import (
    HELP_TEXT,
    TSUNDERE_NUDGE_REPLIES,
    ai_question,
    command_argument,
    has_management_permission,
    is_allowed_group,
    is_nudge_for_bot,
    parse_qq_ids,
    proactive_check_allowed,
    proactive_message_text,
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


def test_help_only_lists_public_commands():
    assert HELP_TEXT == """nao 可用指令：
@nao 帮助 - 查看指令

@nao 表情包制作 - 查看表情模板
😂+🥺 - 合成两个 Emoji
@nao 今日人品 - 查看每日人品
@nao 猜成语 - 开始猜成语游戏
@nao 人生重开 - 开始人生重开模拟
@nao 猜人物 - 开始 DeepSeek 猜人物游戏
@nao 关键词 - 查看关键词库用法"""
    assert "@nao 状态" not in HELP_TEXT
    assert "@nao 定时" not in HELP_TEXT
    assert "@nao 添加违规" not in HELP_TEXT
    assert "/" not in HELP_TEXT


def test_target_user_skips_bot_mention_and_falls_back_to_reply():
    assert select_target_user_id([123456789, 987654321], 123456789, None) == 987654321
    assert select_target_user_id([123456789], 123456789, 987654321) == 987654321
    assert select_target_user_id([123456789], 123456789, None) is None


@pytest.mark.parametrize(
    ("message", "is_tome", "is_self", "has_automatic_reply", "expected"),
    [
        ("绷不住了", False, False, False, "绷不住了"),
        ("@nao 绷不住了", False, False, False, None),
        ("绷不住了", True, False, False, None),
        ("绷不住了", False, True, False, None),
        ("你好", False, False, True, None),
        ("https://example.com", False, False, False, None),
        ("   ", False, False, False, None),
    ],
)
def test_proactive_message_keeps_mention_mode_separate(
    message,
    is_tome,
    is_self,
    has_automatic_reply,
    expected,
):
    assert (
        proactive_message_text(message, is_tome, is_self, has_automatic_reply) == expected
    )


def test_proactive_check_respects_request_and_reply_cooldowns():
    assert proactive_check_allowed(100, last_check=90, last_reply=50) is True
    assert proactive_check_allowed(100, last_check=96, last_reply=0) is False
    assert proactive_check_allowed(100, last_check=0, last_reply=80) is False


def test_is_nudge_for_bot():
    assert is_nudge_for_bot(receiver_id=3256024695, self_id=3256024695, sender_id=1991620780) is True
    assert is_nudge_for_bot(receiver_id=1991620780, self_id=3256024695, sender_id=3256024695) is False
    assert is_nudge_for_bot(receiver_id=3256024695, self_id=3256024695, sender_id=3256024695) is False
    assert is_nudge_for_bot(receiver_id=1111111111, self_id=3256024695, sender_id=2222222222) is False


def test_tsundere_nudge_replies_content():
    assert len(TSUNDERE_NUDGE_REPLIES) >= 5
    for reply in TSUNDERE_NUDGE_REPLIES:
        assert isinstance(reply, str)
        assert len(reply) > 3

