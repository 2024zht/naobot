from typing import Iterable


HELP_TEXT = """nao 可用指令：
@nao 帮助 - 查看指令
@nao 状态 - 检查运行状态
@nao 关于 - 了解 nao
@nao 问 问题 - 使用 DeepSeek AI 问答
@nao 禁言 @成员 [分钟] - 默认禁言 10 分钟
@nao 踢出 @成员 - 将成员移出群聊
@nao 撤回 - 回复一条消息后撤回它
@nao 表情包制作 - 查看表情模板
😂+🥺 - 合成两个 Emoji
@nao 关键词 - 查看关键词库用法
@nao 反诈状态 - 查看当前反诈规则
@nao 反诈记录 @成员 - 查看累计违规次数
@nao 清除违规 @成员 - 清除误判记录
@nao 添加违规 内容 - 管理员提取违规词（也可回复消息）
@nao 违规词列表 - 管理员查看违规词黑名单
@nao 删除违规词 词条 - 管理员删除违规词
发送“你好”也可以和我打招呼。"""

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
    if not is_tome:
        return None
    command_question = command_argument(text, "问")
    return text.strip() if command_question is None else command_question


def parse_mute_duration(text: str) -> int | None:
    argument = command_argument(text, "禁言")
    if argument is None:
        return None
    if not argument:
        return 10 * 60
    if not argument.isdecimal():
        raise ValueError("禁言时间必须是整数分钟")

    minutes = int(argument)
    if not 0 <= minutes <= 43200:
        raise ValueError("禁言时间必须在 0 到 43200 分钟之间")
    return minutes * 60


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
