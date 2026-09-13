import re
from typing import Any, Iterable



PROACTIVE_CHECK_INTERVAL_SECONDS = 5
PROACTIVE_REPLY_COOLDOWN_SECONDS = 25

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


def is_allowed_group(peer_id: int, allowed_group_id: int | Iterable[int]) -> bool:
    if isinstance(allowed_group_id, int):
        return peer_id == allowed_group_id
    return peer_id in allowed_group_id


def parse_group_ids(value: str) -> frozenset[int]:
    if not value.strip():
        raise ValueError("目标群号不能为空")

    ids: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if not item.isdecimal() or int(item) <= 0:
            raise ValueError("群号列表必须是逗号分隔的 QQ 群号")
        ids.add(int(item))
    return frozenset(ids)


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


TSUNDERE_NUDGE_REPLIES: tuple[str, ...] = (
    "哼，别以为拍拍我，我就会对你态度好一点！笨蛋！",
    "拍什么拍！本小nao也是你能随便乱摸的吗？（脸红扭头）",
    "干嘛啦！正忙着呢……才、才没有在等你找我聊天！",
    "再拍我就要把你的黑历史发到群里了！听到了没有！",
    "拍我干嘛？要是闲得慌就去把代码写了，哼！",
    "呜哇！突然戳我一下干嘛……我、我可不会因此心软的！",
    "有事说事，动手动脚的像什么样子……下不为例哦！",
    "戳一下消耗本机器人 1% 电量！快拿好吃的来赔偿我！",
    "又拍我？本姑娘很贵的，摸一次扣一百万！",
    "哼！无事献殷勤……说吧，又想让我帮你吐槽谁了？",
    "（啪的一下拍回去）礼尚往来！不准再拍了！",
    "别碰我！发型都被你拍乱了啦……笨蛋！",
)


def is_nudge_for_bot(receiver_id: int, self_id: int, sender_id: int) -> bool:
    return receiver_id == self_id and sender_id != self_id


def extract_reply_text(segments: Iterable[Any]) -> str:
    parts: list[str] = []
    for segment in segments:
        seg_type = segment.get("type") if isinstance(segment, dict) else getattr(segment, "type", None)
        seg_data = segment.get("data", {}) if isinstance(segment, dict) else getattr(segment, "data", {})
        if seg_type == "text":
            text = seg_data.get("text", "")
            if text:
                parts.append(str(text))
        elif seg_type == "mention":
            name = seg_data.get("name", "")
            uid = seg_data.get("user_id", "")
            parts.append(f"@{name}" if name else f"@{uid}")
        elif seg_type == "image":
            parts.append("[图片]")
        elif seg_type == "face":
            parts.append("[表情]")
        elif seg_type == "file":
            parts.append(f"[文件: {seg_data.get('file_name', '未知')}]")
    return "".join(parts).strip()


def format_quoted_message(
    sender_name: str,
    content: str,
    question: str,
) -> str:
    cleaned_content = content.strip()
    cleaned_question = question.strip()
    if not cleaned_content:
        return cleaned_question
    if not cleaned_question:
        return f"【引用的消息（发送人: {sender_name}）】：\n{cleaned_content}\n请针对上述引用的内容进行回复。"
    return f"【引用的消息（发送人: {sender_name}）】：\n{cleaned_content}\n【用户的问题/回复】：\n{cleaned_question}"


def format_welcome_message(member_name: str | None = None) -> str:
    if member_name and member_name.strip():
        return f"欢迎 {member_name.strip()} 加入本群！🎉"
    return "欢迎加入本群！🎉"


