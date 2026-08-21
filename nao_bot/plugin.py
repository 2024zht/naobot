import asyncio
import os
from collections import deque
from datetime import datetime
from pathlib import Path
from time import monotonic

import httpx
from nonebot import get_bots, get_driver, logger, on_message, on_notice
from nonebot.adapters import Event
from nonebot.adapters.milky import Bot, Message, MessageSegment
from nonebot.adapters.milky.event import GroupFileUploadEvent, GroupMemberIncreaseEvent, GroupMessageEvent
from nonebot.adapters.milky.exception import NetworkError
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.message import event_preprocessor
from nonebot.rule import Rule

from .deepseek import (
    ask_deepseek,
    extract_fraud_keywords,
    request_proactive_decision,
    request_reminder_command,
    request_searched_proactive_reply,
)
from .faq import FaqStore, format_help_with_faq
from .guess_person import GuessPersonSessions, VALID_ANSWERS, request_guess_person_turn
from .image_scan import (
    ImageScanResult,
    first_video_file_id,
    is_video_file_name,
    scan_image_url,
    scan_video_url,
)
from .keywords import MAX_KEYWORDS, KeywordStore, parse_keyword_command
from .moderation import (
    AUTO_KICK_VIOLATION_THRESHOLD,
    FraudKeywordStore,
    ViolationStore,
    detect_fraud_text,
    detect_protected_notice,
    extract_fallback_keywords,
    filter_fraud_keywords,
    has_contact_card,
    should_auto_kick,
    text_from_segments,
)
from .reactions import (
    ReactionCatalog,
    reaction_image_base64,
    select_reaction_asset,
)
from .reminders import (
    CHINA_TIMEZONE,
    ReminderCommand,
    ReminderStore,
    format_reminder_time,
    parse_reminder_command,
    reminder_command_text,
)
from .repeater import RepeatTracker, repeatable_message_text
from .rules import (
    HELP_TEXT,
    ai_question,
    command_argument,
    has_management_permission,
    is_allowed_group,
    parse_qq_ids,
    proactive_check_allowed,
    proactive_message_text,
    reply_for_text,
    select_target_user_id,
)


try:
    TEST_GROUP_ID = int(os.environ["NAO_TEST_GROUP_ID"])
except (KeyError, ValueError) as error:
    raise RuntimeError("NAO_TEST_GROUP_ID must be a valid QQ group number") from error

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()

try:
    ADMIN_QQ_IDS = parse_qq_ids(os.environ.get("NAO_ADMIN_QQ_IDS", ""))
except ValueError as error:
    raise RuntimeError("NAO_ADMIN_QQ_IDS must be comma-separated QQ numbers") from error

AI_COOLDOWN_SECONDS = 10
REACTION_HISTORY_SIZE = 3
last_ai_requests: dict[int, float] = {}
PROACTIVE_HISTORY_SIZE = 6
recent_group_messages: dict[int, deque[str]] = {}
last_proactive_checks: dict[int, float] = {}
last_proactive_replies: dict[int, float] = {}
proactive_groups_in_flight: set[int] = set()
REACTION_ASSET_DIR = Path(__file__).parent / "assets" / "reactions"
REACTION_PACK_ROOT = Path(os.environ.get("NAO_REACTION_PACK_ROOT", "/data/reaction_packs"))
reaction_catalog = ReactionCatalog(
    Path(os.environ.get("NAO_REACTION_CATALOG_FILE", "/data/reaction_catalog.json")),
    REACTION_PACK_ROOT,
)
recent_reactions: dict[int, deque[Path]] = {}
keyword_store = KeywordStore(Path(os.environ.get("NAO_KEYWORDS_FILE", "/data/keywords.json")))
violation_store = ViolationStore(Path(os.environ.get("NAO_MODERATION_FILE", "/data/moderation.json")))
fraud_keyword_store = FraudKeywordStore(
    Path(os.environ.get("NAO_FRAUD_KEYWORDS_FILE", "/data/fraud_keywords.json"))
)
guess_person_sessions = GuessPersonSessions()
reminder_store = ReminderStore(Path(os.environ.get("NAO_REMINDERS_FILE", "/data/reminders.sqlite3")))
faq_store = FaqStore(Path(os.environ.get("NAO_LAB_FAQ_FILE", "/data/lab_faq.json")))
repeat_tracker = RepeatTracker()
reminder_scheduler_task: asyncio.Task[None] | None = None

try:
    reaction_catalog.sync()
except (OSError, ValueError):
    logger.exception("Reaction catalog synchronization failed")


def group_id_from_event(event: Event) -> int | None:
    if isinstance(event, GroupMessageEvent):
        return event.data.peer_id
    return getattr(event.data, "group_id", None)


@event_preprocessor
async def restrict_group_events(event: Event) -> None:
    group_id = group_id_from_event(event)
    if group_id is not None and not is_allowed_group(group_id, TEST_GROUP_ID):
        raise IgnoredException("group is not enabled")


async def is_test_group(event: GroupMessageEvent) -> bool:
    return is_allowed_group(event.data.peer_id, TEST_GROUP_ID)


def sender_role(event: GroupMessageEvent) -> str | None:
    member = event.data.group_member
    return member.role if member else None


def mentioned_user_id(event: GroupMessageEvent) -> int | None:
    mentioned_ids = (
        int(segment.data["user_id"])
        for segment in event.get_message()
        if segment.type == "mention"
    )
    reply_sender_id = event.reply.sender_id if event.reply else None
    return select_target_user_id(mentioned_ids, event.self_id, reply_sender_id)


async def can_manage(bot: Bot, event: GroupMessageEvent) -> bool:
    role = sender_role(event)
    if role is None and not ADMIN_QQ_IDS:
        member = await bot.get_group_member_info(
            group_id=event.data.peer_id,
            user_id=event.data.sender_id,
            no_cache=True,
        )
        role = member.role
    return has_management_permission(event.data.sender_id, role, ADMIN_QQ_IDS)


async def can_manage_group_file(bot: Bot, event: GroupFileUploadEvent) -> bool:
    if ADMIN_QQ_IDS:
        return event.data.user_id in ADMIN_QQ_IDS
    member = await bot.get_group_member_info(
        group_id=event.data.group_id,
        user_id=event.data.user_id,
        no_cache=True,
    )
    return has_management_permission(event.data.user_id, member.role, ADMIN_QQ_IDS)


async def _send_due_reminders() -> None:
    bot = next(
        (connected_bot for connected_bot in get_bots().values() if isinstance(connected_bot, Bot)),
        None,
    )
    if bot is None:
        return
    for reminder in reminder_store.due(datetime.now(CHINA_TIMEZONE)):
        try:
            await bot.send_group_message(
                group_id=reminder.group_id,
                message=[
                    MessageSegment.mention(reminder.creator_id),
                    MessageSegment.text(f" 定时提醒（任务 #{reminder.id}）：\n{reminder.content}"),
                ],
            )
        except Exception:
            logger.exception(f"Scheduled reminder delivery failed for task {reminder.id}")
            continue
        reminder_store.complete(reminder.id, datetime.now(CHINA_TIMEZONE))


async def _reminder_scheduler_loop() -> None:
    while True:
        try:
            await _send_due_reminders()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled reminder poll failed")
        await asyncio.sleep(15)


driver = get_driver()


@driver.on_startup
async def start_reminder_scheduler() -> None:
    global reminder_scheduler_task
    if reminder_scheduler_task is None or reminder_scheduler_task.done():
        reminder_scheduler_task = asyncio.create_task(
            _reminder_scheduler_loop(),
            name="nao-reminder-scheduler",
        )


@driver.on_shutdown
async def stop_reminder_scheduler() -> None:
    global reminder_scheduler_task
    if reminder_scheduler_task is None:
        return
    reminder_scheduler_task.cancel()
    try:
        await reminder_scheduler_task
    except asyncio.CancelledError:
        pass
    reminder_scheduler_task = None


def is_moderation_command(event: GroupMessageEvent) -> bool:
    text = event.get_plaintext().strip()
    return event.is_tome() and (
        text == "反诈状态"
        or any(
            command_argument(text, command) is not None
            for command in ("反诈记录", "清除违规")
        )
    )


def command_target_id(event: GroupMessageEvent, command: str) -> int | None:
    target_id = mentioned_user_id(event)
    if target_id is not None:
        return target_id
    argument = command_argument(event.get_plaintext(), command)
    if argument and argument.isdecimal():
        return int(argument)
    return None


moderation_command_matcher = on_message(
    rule=Rule(is_test_group) & Rule(is_moderation_command),
    priority=3,
    block=True,
)


@moderation_command_matcher.handle()
async def handle_moderation_command(bot: Bot, event: GroupMessageEvent) -> None:
    if not await can_manage(bot, event):
        await moderation_command_matcher.finish("你没有管理反诈记录的权限。")

    text = event.get_plaintext().strip()
    if text == "反诈状态":
        await moderation_command_matcher.finish(
            "反诈防护已开启：普通成员的重要通知、诈骗话术、QQ名片、二维码以及图片和视频中的诈骗内容会被撤回并累计违规记录。"
        )

    command = "反诈记录" if command_argument(text, "反诈记录") is not None else "清除违规"
    target_id = command_target_id(event, command)
    if target_id is None:
        await moderation_command_matcher.finish(f"用法：@nao {command} @成员，或 @nao {command} QQ号")

    group_id = event.data.peer_id
    if command == "清除违规":
        cleared = violation_store.clear(group_id, target_id)
        message = "已清除该成员的反诈违规记录。" if cleared else "该成员没有反诈违规记录。"
        await moderation_command_matcher.finish(message)

    count = violation_store.get_count(group_id, target_id)
    reason = violation_store.get_last_reason(group_id, target_id) or "无"
    await moderation_command_matcher.finish(
        f"QQ {target_id} 当前累计 {count} 次，最近原因：{reason}"
    )


def is_fraud_keyword_command(event: GroupMessageEvent) -> bool:
    text = event.get_plaintext().strip()
    return event.is_tome() and (
        text == "违规词列表"
        or any(
            command_argument(text, command) is not None
            for command in ("添加违规", "删除违规词")
        )
    )


fraud_keyword_management_matcher = on_message(
    rule=Rule(is_test_group) & Rule(is_fraud_keyword_command),
    priority=3,
    block=True,
)


@fraud_keyword_management_matcher.handle()
async def handle_fraud_keyword_management(bot: Bot, event: GroupMessageEvent) -> None:
    if not await can_manage(bot, event):
        await fraud_keyword_management_matcher.finish("你没有管理违规词黑名单的权限。")

    text = event.get_plaintext().strip()
    if text == "违规词列表":
        terms = fraud_keyword_store.terms()
        if not terms:
            await fraud_keyword_management_matcher.finish("违规词黑名单还是空的。")
        lines = "\n".join(f"{index}. {term}" for index, term in enumerate(terms, 1))
        await fraud_keyword_management_matcher.finish(f"违规词黑名单（{len(terms)}）：\n{lines}")

    delete_argument = command_argument(text, "删除违规词")
    if delete_argument is not None:
        if not delete_argument:
            await fraud_keyword_management_matcher.finish("用法：@nao 删除违规词 词条")
        deleted = fraud_keyword_store.delete(delete_argument)
        message = f"已删除违规词：{delete_argument}" if deleted else f"没有找到违规词：{delete_argument}"
        await fraud_keyword_management_matcher.finish(message)

    source_text = command_argument(text, "添加违规") or ""
    if not source_text and event.reply:
        source_text = text_from_segments(event.reply.segments)
    if not source_text:
        await fraud_keyword_management_matcher.finish(
            "用法：@nao 添加违规 违规内容，或回复违规消息后发送 @nao 添加违规。"
        )

    candidates: list[str] = []
    if DEEPSEEK_API_KEY:
        try:
            candidates = await extract_fraud_keywords(DEEPSEEK_API_KEY, DEEPSEEK_MODEL, source_text)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            logger.exception("DeepSeek fraud keyword extraction failed")

    fallback_keywords = extract_fallback_keywords(source_text)
    keywords = filter_fraud_keywords(source_text, [*candidates, *fallback_keywords])
    if not keywords:
        await fraud_keyword_management_matcher.finish(
            "没有提取到可安全加入黑名单的高风险短语，请提供更完整的违规原文。"
        )

    added = fraud_keyword_store.add_many(keywords)
    if not added:
        await fraud_keyword_management_matcher.finish(f"提取到的违规词已在黑名单中：{'、'.join(keywords)}")
    await fraud_keyword_management_matcher.finish(f"已添加违规词（{len(added)}）：{'、'.join(added)}")


async def _first_media_url(
    bot: Bot,
    event: GroupMessageEvent,
    media_type: str,
) -> str | None:
    segments = [
        segment
        for segment in event.get_message()
        if segment.type == media_type
        and not (media_type == "image" and segment.data.get("sub_type") == "sticker")
    ]
    if media_type == "video" and segments:
        logger.info(f"Anti-fraud video segment received: count={len(segments)}")

    for segment in segments:
        if url := segment.data.get("temp_url"):
            return str(url)
        if resource_id := segment.data.get("resource_id"):
            return await bot.get_resource_temp_url(resource_id=str(resource_id))
    return None


async def _first_image_url(bot: Bot, event: GroupMessageEvent) -> str | None:
    return await _first_media_url(bot, event, "image")


async def _first_video_url(bot: Bot, event: GroupMessageEvent) -> str | None:
    return await _first_media_url(bot, event, "video")


def _first_video_file_id(event: GroupMessageEvent) -> str | None:
    segments = [
        segment
        for segment in event.get_message()
        if segment.type == "file" and is_video_file_name(str(segment.data.get("file_name", "")))
    ]
    if segments:
        logger.info(f"Anti-fraud video file segment received: count={len(segments)}")

    return first_video_file_id(segments)


def _video_violation_reason(video_result: ImageScanResult) -> str | None:
    if video_result.has_qr_code:
        return "普通成员发送含二维码的视频"
    if keyword := fraud_keyword_store.match(video_result.text):
        return f"视频命中违规词黑名单（{keyword}）"
    if detect_protected_notice(video_result.text):
        return "普通成员通过视频冒充重要通知或公告"
    return detect_fraud_text(video_result.text)


def _video_frame_has_violation(result: ImageScanResult) -> bool:
    return (
        result.has_qr_code
        or fraud_keyword_store.match(result.text) is not None
        or detect_protected_notice(result.text)
        or detect_fraud_text(result.text) is not None
    )


async def _detect_violation(bot: Bot, event: GroupMessageEvent) -> str | None:
    if has_contact_card(event.get_message()):
        return "普通成员发送QQ好友或群名片"

    text = event.get_plaintext()
    if keyword := fraud_keyword_store.match(text):
        return f"命中违规词黑名单（{keyword}）"
    if detect_protected_notice(text):
        return "普通成员冒充重要通知或公告"
    if reason := detect_fraud_text(text):
        return reason

    image_url = await _first_image_url(bot, event)
    if image_url:
        try:
            image_result = await scan_image_url(image_url)
        except Exception:
            logger.exception("Anti-fraud image scan failed")
        else:
            if image_result.has_qr_code:
                return "普通成员发送二维码图片"
            if keyword := fraud_keyword_store.match(image_result.text):
                return f"图片命中违规词黑名单（{keyword}）"
            if detect_protected_notice(image_result.text):
                return "普通成员通过图片冒充重要通知或公告"
            if reason := detect_fraud_text(image_result.text):
                return reason

    scan_started = monotonic()
    video_file_id: str | None = None
    try:
        video_url = await _first_video_url(bot, event)
        video_source = "video"
        if not video_url:
            video_file_id = _first_video_file_id(event)
            if video_file_id:
                video_url = await bot.get_group_file_download_url(
                    group_id=event.data.peer_id,
                    file_id=video_file_id,
                )
                video_source = "video_file"
        if not video_url:
            return None
        logger.info(f"Anti-fraud video scan started: source={video_source}")
        video_result = await scan_video_url(video_url, _video_frame_has_violation)
    except Exception:
        logger.exception(f"Anti-fraud video scan failed after {monotonic() - scan_started:.2f}s")
        return None
    finally:
        if video_file_id:
            try:
                await bot.delete_group_file(
                    group_id=event.data.peer_id,
                    file_id=video_file_id,
                )
                logger.info(f"Anti-fraud video file deleted: file_id={video_file_id}")
            except Exception:
                logger.exception("Anti-fraud video file deletion failed")

    logger.info(
        f"Anti-fraud video scan completed after {monotonic() - scan_started:.2f}s: "
        f"source={video_source} qr={video_result.has_qr_code} "
        f"text_chars={len(video_result.text)}"
    )
    return _video_violation_reason(video_result)


async def _try_auto_kick(bot: Bot, group_id: int, user_id: int, bot_id: int, count: int) -> bool:
    try:
        target = await bot.get_group_member_info(group_id=group_id, user_id=user_id, no_cache=True)
        bot_member = await bot.get_group_member_info(group_id=group_id, user_id=bot_id, no_cache=True)
    except Exception:
        logger.exception("Auto-kick permission check failed")
        return False

    if not should_auto_kick(count, target.role, bot_member.role):
        logger.warning(
            f"Auto-kick skipped: count={count} target_role={target.role} bot_role={bot_member.role}"
        )
        return False

    try:
        await bot.kick_group_member(group_id=group_id, user_id=user_id)
    except Exception:
        logger.exception("Auto-kick anti-fraud member failed")
        return False
    logger.warning(f"Auto-kick anti-fraud member completed: count={count}")
    return True


async def _record_violation(
    bot: Bot,
    group_id: int,
    user_id: int,
    bot_id: int,
    reason: str,
    message_seq: int | None = None,
    message_action: str = "撤回",
) -> None:
    if message_seq is not None:
        try:
            await bot.recall_group_message(group_id=group_id, message_seq=message_seq)
        except Exception:
            logger.exception("Recall anti-fraud message failed")

    count = violation_store.add(group_id, user_id, reason)
    kicked = (
        await _try_auto_kick(bot, group_id, user_id, bot_id, count)
        if count >= AUTO_KICK_VIOLATION_THRESHOLD
        else False
    )
    action = (
        f" 已达到第 {AUTO_KICK_VIOLATION_THRESHOLD} 次违规，已移出群聊。"
        if kicked
        else (
            f" 已达到第 {AUTO_KICK_VIOLATION_THRESHOLD} 次违规，但当前未执行自动踢出，请管理员处理。"
            if count >= AUTO_KICK_VIOLATION_THRESHOLD
            else ""
        )
    )
    await bot.send_group_message(
        group_id=group_id,
        message=[
            MessageSegment.mention(user_id),
            MessageSegment.text(f" 该消息已被反诈防护{message_action}（{reason}）。当前累计 {count} 次。{action}"),
        ],
    )


async def _handle_violation(bot: Bot, event: GroupMessageEvent, reason: str) -> None:
    await _record_violation(
        bot,
        group_id=event.data.peer_id,
        user_id=event.data.sender_id,
        bot_id=event.self_id,
        reason=reason,
        message_seq=event.data.message_seq,
    )


moderation_matcher = on_message(rule=Rule(is_test_group), priority=2, block=False)


@moderation_matcher.handle()
async def handle_moderation(bot: Bot, event: GroupMessageEvent, matcher: Matcher) -> None:
    if event.data.sender_id == event.self_id or await can_manage(bot, event):
        return
    reason = await _detect_violation(bot, event)
    if reason is None:
        return
    await _handle_violation(bot, event, reason)
    matcher.stop_propagation()


def is_group_file_upload(event: Event) -> bool:
    return isinstance(event, GroupFileUploadEvent)


file_moderation_matcher = on_notice(rule=Rule(is_group_file_upload), priority=2, block=False)


@file_moderation_matcher.handle()
async def handle_group_file_upload(bot: Bot, event: GroupFileUploadEvent) -> None:
    if not is_allowed_group(event.data.group_id, TEST_GROUP_ID):
        return
    if event.data.user_id == event.self_id or not is_video_file_name(event.data.file_name):
        return

    try:
        if await can_manage_group_file(bot, event):
            return
    except Exception:
        logger.exception("Anti-fraud group file permission check failed")
        return

    scan_started = monotonic()
    logger.info(
        f"Anti-fraud group video file scan started: name={event.data.file_name} "
        f"size={event.data.file_size}"
    )
    try:
        video_url = await bot.get_group_file_download_url(
            group_id=event.data.group_id,
            file_id=event.data.file_id,
        )
        video_result = await scan_video_url(video_url, _video_frame_has_violation)
    except Exception:
        logger.exception(
            f"Anti-fraud group video file scan failed after {monotonic() - scan_started:.2f}s"
        )
        return
    finally:
        try:
            await bot.delete_group_file(
                group_id=event.data.group_id,
                file_id=event.data.file_id,
            )
            logger.info(f"Anti-fraud group video file deleted: name={event.data.file_name}")
        except Exception:
            logger.exception("Anti-fraud group video file deletion failed")

    logger.info(
        f"Anti-fraud group video file scan completed after {monotonic() - scan_started:.2f}s: "
        f"qr={video_result.has_qr_code} text_chars={len(video_result.text)}"
    )
    reason = _video_violation_reason(video_result)
    if reason is None:
        return

    await _record_violation(
        bot,
        group_id=event.data.group_id,
        user_id=event.data.user_id,
        bot_id=event.self_id,
        reason=reason,
        message_action="删除",
    )


def is_keyword_command(event: GroupMessageEvent) -> bool:
    text = event.get_plaintext().strip()
    return event.is_tome() and (text == "关键词" or text.startswith("关键词 "))


keyword_management_matcher = on_message(
    rule=Rule(is_test_group) & Rule(is_keyword_command),
    priority=4,
    block=True,
)


@keyword_management_matcher.handle()
async def handle_keyword_management(bot: Bot, event: GroupMessageEvent) -> None:
    if not await can_manage(bot, event):
        await keyword_management_matcher.finish("你没有管理关键词库的权限。")

    try:
        command = parse_keyword_command(event.get_plaintext())
    except ValueError as error:
        await keyword_management_matcher.finish(str(error))
    if command is None:
        return

    if command.action == "list":
        triggers = keyword_store.triggers()
        if not triggers:
            await keyword_management_matcher.finish("关键词库还是空的。")
        lines = "\n".join(f"{index}. {trigger}" for index, trigger in enumerate(triggers, 1))
        await keyword_management_matcher.finish(f"关键词（{len(triggers)}/{MAX_KEYWORDS}）：\n{lines}")

    if command.action == "delete":
        deleted = keyword_store.delete(command.trigger)
        message = f"已删除关键词：{command.trigger}" if deleted else f"没有找到关键词：{command.trigger}"
        await keyword_management_matcher.finish(message)

    if reply_for_text(command.trigger, True) is not None:
        await keyword_management_matcher.finish("这个触发词与内置回复冲突，请换一个。")
    try:
        created = keyword_store.add(command.trigger, command.reply)
    except ValueError as error:
        await keyword_management_matcher.finish(str(error))
    action = "添加" if created else "更新"
    await keyword_management_matcher.finish(f"已{action}关键词：{command.trigger}")


def is_reminder_command(event: GroupMessageEvent) -> bool:
    return reminder_command_text(event.get_plaintext(), event.is_tome()) is not None


reminder_matcher = on_message(
    rule=Rule(is_test_group) & Rule(is_reminder_command),
    priority=4,
    block=True,
)


@reminder_matcher.handle()
async def handle_reminder_command(bot: Bot, event: GroupMessageEvent) -> None:
    if not await can_manage(bot, event):
        await reminder_matcher.finish("你没有管理定时提醒的权限。")

    text = reminder_command_text(event.get_plaintext(), event.is_tome())
    if text is None:
        return
    try:
        if text.startswith(("定时任务", "定时提醒")):
            if not DEEPSEEK_API_KEY:
                await reminder_matcher.finish("智能定时尚未配置。")
            command = await request_reminder_command(DEEPSEEK_API_KEY, DEEPSEEK_MODEL, text)
        else:
            command = parse_reminder_command(text)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
        logger.exception("DeepSeek reminder request failed")
        await reminder_matcher.finish(f"没有理解这个定时任务：{error}")
    if command is None:
        return

    group_id = event.data.peer_id
    if command.action == "list":
        reminders = reminder_store.list_pending(group_id)
        if not reminders:
            await reminder_matcher.finish("当前没有待发送的定时提醒。")
        visible = reminders[:20]
        lines = []
        for reminder in visible:
            content = " ".join(reminder.content.split())
            if len(content) > 60:
                content = f"{content[:57]}..."
            repeat = {1: " 每天", 7: " 每周"}.get(reminder.repeat_days, "")
            lines.append(
                f"#{reminder.id} {format_reminder_time(reminder.remind_at)}{repeat} {content}"
            )
        if len(reminders) > len(visible):
            lines.append(f"还有 {len(reminders) - len(visible)} 条未显示。")
        await reminder_matcher.finish("待发送的定时提醒：\n" + "\n".join(lines))

    if command.action == "cancel":
        cancelled = reminder_store.cancel(group_id, command.reminder_id)
        message = "已取消该定时提醒。" if cancelled else "没有找到这个群的定时提醒。"
        await reminder_matcher.finish(message)

    try:
        reminder = reminder_store.add(
            group_id,
            event.data.sender_id,
            command.remind_at,
            command.content,
            command.repeat_days,
        )
    except ValueError as error:
        await reminder_matcher.finish(str(error))
    time_note = "（未指定时刻，已按 09:00 设置）" if command.time_defaulted else ""
    repeat_note = {1: "每天", 7: "每周"}.get(command.repeat_days, "仅一次")
    await reminder_matcher.finish(
        f"已设置定时提醒 #{reminder.id}\n"
        f"时间：{format_reminder_time(reminder.remind_at)}{time_note}\n"
        f"重复：{repeat_note}\n"
        f"内容：{reminder.content}"
    )


def is_guess_person_start(event: GroupMessageEvent) -> bool:
    return event.is_tome() and event.get_plaintext().strip() == "猜人物"


guess_person_start_matcher = on_message(
    rule=Rule(is_test_group) & Rule(is_guess_person_start),
    priority=5,
    block=True,
)


@guess_person_start_matcher.handle()
async def handle_guess_person_start(event: GroupMessageEvent) -> None:
    if not DEEPSEEK_API_KEY:
        await guess_person_start_matcher.finish("猜人物功能尚未配置。")
    question = guess_person_sessions.start(event.data.peer_id, event.data.sender_id)
    await guess_person_start_matcher.finish(
        f"请先在心里想好一个人物。第 1 问：{question}\n"
        "直接回复：是 / 否 / 不知道 / 可能 / 可能不是；发送“退出”结束。"
    )


def has_active_guess_person_session(event: GroupMessageEvent) -> bool:
    return guess_person_sessions.has_active(event.data.peer_id, event.data.sender_id)


guess_person_answer_matcher = on_message(
    rule=Rule(is_test_group) & Rule(has_active_guess_person_session),
    priority=6,
    block=True,
)


@guess_person_answer_matcher.handle()
async def handle_guess_person_answer(event: GroupMessageEvent) -> None:
    group_id = event.data.peer_id
    user_id = event.data.sender_id
    answer = event.get_plaintext().strip()
    if answer in {"退出", "结束"}:
        guess_person_sessions.end(group_id, user_id)
        await guess_person_answer_matcher.finish("本局猜人物已结束。")
    if answer not in VALID_ANSWERS:
        await guess_person_answer_matcher.finish(
            "请直接回复：是 / 否 / 不知道 / 可能 / 可能不是；发送“退出”结束。"
        )

    try:
        prepared = guess_person_sessions.prepare_answer(group_id, user_id, answer)
    except KeyError:
        await guess_person_answer_matcher.finish("本局已超时，请重新发送 @nao 猜人物。")

    try:
        turn = await request_guess_person_turn(
            DEEPSEEK_API_KEY,
            DEEPSEEK_MODEL,
            prepared.history,
            prepared.force_guess,
        )
        question_number = guess_person_sessions.apply_turn(
            group_id,
            user_id,
            prepared,
            turn,
        )
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        logger.exception("DeepSeek guess-person request failed")
        await guess_person_answer_matcher.finish("我暂时没想好，请稍后重新发送刚才的答案。")

    if turn.type == "guess":
        await guess_person_answer_matcher.finish(
            f"我猜是：{turn.text}。本局结束，再玩一次请发送 @nao 猜人物。"
        )
    await guess_person_answer_matcher.finish(f"第 {question_number} 问：{turn.text}")


def is_ai_command(event: GroupMessageEvent) -> bool:
    return ai_question(event.get_plaintext(), event.is_tome()) is not None


def faq_answer(event: GroupMessageEvent) -> str | None:
    if event.data.sender_id == event.self_id or not event.is_tome():
        return None
    return faq_store.get(event.get_plaintext().strip())


def is_faq_question(event: GroupMessageEvent) -> bool:
    return faq_answer(event) is not None


faq_matcher = on_message(
    rule=Rule(is_test_group) & Rule(is_faq_question),
    priority=14,
    block=True,
)


@faq_matcher.handle()
async def handle_faq(event: GroupMessageEvent) -> None:
    answer = faq_answer(event)
    if answer is not None:
        await faq_matcher.finish(answer)


ai_matcher = on_message(rule=Rule(is_test_group) & Rule(is_ai_command), priority=15, block=True)


@ai_matcher.handle()
async def handle_ai(bot: Bot, event: GroupMessageEvent) -> None:
    question = ai_question(event.get_plaintext(), event.is_tome())
    if not question:
        await ai_matcher.finish("请在 @我 后面写上你的问题，例如：@nao 你能做什么？")
    if not DEEPSEEK_API_KEY:
        await ai_matcher.finish("AI 问答尚未配置。")

    now = monotonic()
    last_request = last_ai_requests.get(event.data.sender_id, 0)
    if now - last_request < AI_COOLDOWN_SECONDS:
        await ai_matcher.finish("问得太快啦，请过几秒再试。")
    last_ai_requests[event.data.sender_id] = now

    try:
        answer = await ask_deepseek(
            DEEPSEEK_API_KEY,
            DEEPSEEK_MODEL,
            question,
            allow_reminder=await can_manage(bot, event),
        )
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        logger.exception("DeepSeek request failed")
        await ai_matcher.finish("AI 暂时不可用，请稍后再试。")
    if isinstance(answer, ReminderCommand):
        try:
            reminder = reminder_store.add(
                event.data.peer_id,
                event.data.sender_id,
                answer.remind_at,
                answer.content,
                answer.repeat_days,
            )
        except ValueError as error:
            await ai_matcher.finish(str(error))
        time_note = "（未指定时刻，已按 09:00 设置）" if answer.time_defaulted else ""
        repeat_note = {1: "每天", 7: "每周"}.get(answer.repeat_days, "仅一次")
        await ai_matcher.finish(
            f"已设置定时提醒 #{reminder.id}\n"
            f"时间：{format_reminder_time(reminder.remind_at)}{time_note}\n"
            f"重复：{repeat_note}\n"
            f"内容：{reminder.content}"
        )
    catalog_assets: tuple[Path, ...] = ()
    if answer.reaction_scene:
        try:
            reaction_catalog.sync()
            catalog_assets = reaction_catalog.assets_for_scene(answer.reaction_scene)
        except (OSError, ValueError):
            logger.exception("Reaction catalog loading failed")
    history = recent_reactions.get(event.data.sender_id, ())
    reaction_asset = select_reaction_asset(
        answer.reaction_scene,
        answer.reaction_context,
        answer.reaction_confidence,
        REACTION_ASSET_DIR,
        catalog_assets,
        recent_assets=history,
    )
    if reaction_asset is None:
        await ai_matcher.finish(answer.text)
    try:
        await ai_matcher.send(
            Message(
                [
                    MessageSegment.text(answer.text),
                    MessageSegment.image(
                        base64=reaction_image_base64(reaction_asset),
                        sub_type="sticker",
                    ),
                ]
            )
        )
    except (OSError, NetworkError):
        logger.exception("Reaction sticker send failed; falling back to text")
        await ai_matcher.finish(answer.text)
    history = recent_reactions.setdefault(
        event.data.sender_id,
        deque(maxlen=REACTION_HISTORY_SIZE),
    )
    history.append(reaction_asset)
    await ai_matcher.finish()


def is_static_message(event: GroupMessageEvent) -> bool:
    return (
        event.data.sender_id != event.self_id
        and reply_for_text(event.get_plaintext(), event.is_tome()) is not None
    )


static_matcher = on_message(
    rule=Rule(is_test_group) & Rule(is_static_message),
    priority=4,
    block=True,
)


@static_matcher.handle()
async def handle_message(event: GroupMessageEvent) -> None:
    response = reply_for_text(event.get_plaintext(), event.is_tome())
    if response == HELP_TEXT:
        response = format_help_with_faq(HELP_TEXT, faq_store.questions())
    if response is not None:
        await static_matcher.finish(response)


keyword_reply_matcher = on_message(rule=Rule(is_test_group), priority=20, block=False)


@keyword_reply_matcher.handle()
async def handle_keyword_reply(event: GroupMessageEvent) -> None:
    if event.data.sender_id == event.self_id:
        return
    response = keyword_store.get(event.get_plaintext().strip())
    if response is not None:
        await keyword_reply_matcher.finish(response)


def repeater_text(event: GroupMessageEvent) -> str | None:
    text = event.get_plaintext().strip()
    has_automatic_reply = (
        reply_for_text(text, False) is not None or keyword_store.get(text) is not None
    )
    message = event.get_message()
    is_plain_text = bool(message) and all(segment.type == "text" for segment in message)
    return repeatable_message_text(
        text,
        event.is_tome(),
        event.data.sender_id == event.self_id,
        has_automatic_reply,
        is_plain_text,
        event.reply is not None,
    )


def is_repeater_message(event: GroupMessageEvent) -> bool:
    return repeater_text(event) is not None


repeater_matcher = on_message(
    rule=Rule(is_test_group) & Rule(is_repeater_message),
    priority=25,
    block=False,
)


@repeater_matcher.handle()
async def handle_repeater(event: GroupMessageEvent, matcher: Matcher) -> None:
    text = repeater_text(event)
    if text is None:
        return
    now = monotonic()
    if not repeat_tracker.record(
        event.data.peer_id,
        event.data.sender_id,
        text,
        now,
    ):
        return

    last_proactive_replies[event.data.peer_id] = now
    matcher.stop_propagation()
    await repeater_matcher.send(text)


def proactive_text(event: GroupMessageEvent) -> str | None:
    text = event.get_plaintext().strip()
    has_automatic_reply = (
        reply_for_text(text, False) is not None or keyword_store.get(text) is not None
    )
    return proactive_message_text(
        text,
        event.is_tome(),
        event.data.sender_id == event.self_id,
        has_automatic_reply,
    )


def is_proactive_message(event: GroupMessageEvent) -> bool:
    return bool(DEEPSEEK_API_KEY) and proactive_text(event) is not None


proactive_matcher = on_message(
    rule=Rule(is_test_group) & Rule(is_proactive_message),
    priority=30,
    block=False,
)


@proactive_matcher.handle()
async def handle_proactive_message(event: GroupMessageEvent) -> None:
    text = proactive_text(event)
    if text is None:
        return

    group_id = event.data.peer_id
    history = recent_group_messages.setdefault(
        group_id,
        deque(maxlen=PROACTIVE_HISTORY_SIZE),
    )
    context = list(history)
    history.append(text)

    now = monotonic()
    if group_id in proactive_groups_in_flight or not proactive_check_allowed(
        now,
        last_proactive_checks.get(group_id, 0),
        last_proactive_replies.get(group_id, 0),
    ):
        return
    last_proactive_checks[group_id] = now
    repeat_generation = repeat_tracker.generation(group_id)
    proactive_groups_in_flight.add(group_id)

    try:
        decision = await request_proactive_decision(
            DEEPSEEK_API_KEY,
            DEEPSEEK_MODEL,
            context,
            text,
        )
        reply = decision.reply
        if decision.search_query:
            reply = await request_searched_proactive_reply(
                DEEPSEEK_API_KEY,
                DEEPSEEK_MODEL,
                context,
                text,
                decision.search_query,
            )
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        logger.exception("DeepSeek proactive reply request failed")
        return
    finally:
        proactive_groups_in_flight.discard(group_id)
    if reply is None:
        return
    if repeat_generation != repeat_tracker.generation(group_id):
        return

    last_proactive_replies[group_id] = monotonic()
    await proactive_matcher.send(reply)


welcome_matcher = on_notice(priority=10, block=False)


@welcome_matcher.handle()
async def welcome_member(bot: Bot, event: GroupMemberIncreaseEvent) -> None:
    if event.data.group_id != TEST_GROUP_ID or event.data.user_id == event.self_id:
        return
    await bot.send_group_message(
        group_id=event.data.group_id,
        message=[MessageSegment.mention(event.data.user_id), MessageSegment.text(" 欢迎加入本群！")],
    )
