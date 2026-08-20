import re
from typing import Iterable


PROACTIVE_CHECK_INTERVAL_SECONDS = 8
PROACTIVE_REPLY_COOLDOWN_SECONDS = 45

LINK_PATTERN = re.compile(
    r"(?:https?|ftp)://|www\.|(?:[a-z0-9-]+\.)+(?:com|net|org|cn|io|me|top|xyz|site|online|cc|tv|app)(?::\d+)?(?:[/\\?#]\S*)?",
    re.IGNORECASE,
)


HELP_TEXT = """nao 可用指令：
@nao 帮助 - 查看指令

@nao 表情包制作 - 查看表情模板
😂+🥺 - 合成两个 Emoji
@nao 今日人品 - 查看每日人品
@nao 猜成语 - 开始猜成语游戏
@nao 人生重开 - 开始人生重开模拟
@nao 猜人物 - 开始 DeepSeek 猜人物游戏
@nao 关键词 - 查看关键词库用法"""

COMMAND_REPLIES = {
    "帮助": HELP_TEXT,
    "状态": "nao 在线，运行正常。",
    "关于": "我是 nao 机器人助手，目前正在本群测试。",
}

PLAIN_REPLIES = {
    "你好": "你好，我是 nao。",
}


def is_allowed_group(peer_id: int, allowed_group_id: int) -> bool:
    return peer_id == allowed_group_id


def reply_for_text(text: str, is_tome: bool = False) -> str | None:
    stripped = text.strip()
    if is_tome and stripped in COMMAND_REPLIES:
        return COMMAND_REPLIES[stripped]
    return PLAIN_REPLIES.get(stripped)


def command_argument(text: str, command: str) -> str | None:
    stripped = text.strip()
    if stripped == command:
        return ""
    prefix = f"{command} "
    if stripped.startswith(prefix):
        return stripped[len(prefix) :].strip()
    return None


def ai_question(text: str, is_tome: bool) -> str | None:
    stripped = text.strip()
    if is_tome:
        return stripped

    prefix = "@nao"
    lowered = stripped.casefold()
    if lowered == prefix:
        return ""
    if lowered.startswith(prefix) and len(stripped) > len(prefix):
        suffix = stripped[len(prefix) :]
        if suffix[0].isspace():
            return suffix.strip()
    return None


def proactive_message_text(
    text: str,
    is_tome: bool,
    is_self: bool,
    has_automatic_reply: bool,
) -> str | None:
    stripped = text.strip()
    lowered = stripped.casefold()
    if (
        not stripped
        or len(stripped) > 200
        or is_self
        or is_tome
        or has_automatic_reply
        or ai_question(stripped, False) is not None
        or LINK_PATTERN.search(lowered)
    ):
        return None
    return stripped


def proactive_check_allowed(now: float, last_check: float, last_reply: float) -> bool:
    return (
        now - last_check >= PROACTIVE_CHECK_INTERVAL_SECONDS
        and now - last_reply >= PROACTIVE_REPLY_COOLDOWN_SECONDS
    )


def parse_qq_ids(value: str) -> frozenset[int]:
    if not value.strip():
        return frozenset()

    ids: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if not item.isdecimal() or int(item) <= 0:
            raise ValueError("QQ 管理员列表必须是逗号分隔的 QQ 号")
        ids.add(int(item))
    return frozenset(ids)


def has_management_permission(sender_id: int, role: str | None, admin_ids: frozenset[int]) -> bool:
    if admin_ids:
        return sender_id in admin_ids
    return role in {"admin", "owner"}


def select_target_user_id(
    mentioned_ids: Iterable[int],
    self_id: int,
    reply_sender_id: int | None,
) -> int | None:
    for user_id in mentioned_ids:
        if user_id != self_id:
            return user_id
    return reply_sender_id
