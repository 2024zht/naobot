import os
from pathlib import Path
from time import monotonic

import httpx
from nonebot import logger, on_message, on_notice
from nonebot.adapters import Event
from nonebot.adapters.milky import Bot, Message, MessageSegment
from nonebot.adapters.milky.event import GroupMemberIncreaseEvent, GroupMessageEvent
from nonebot.adapters.milky.exception import NetworkError
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.message import event_preprocessor
from nonebot.rule import Rule

from .deepseek import ask_deepseek, extract_fraud_keywords
from .guess_person import GuessPersonSessions, VALID_ANSWERS, request_guess_person_turn
from .image_scan import scan_image_url
from .keywords import MAX_KEYWORDS, KeywordStore, parse_keyword_command
from .moderation import (
    FraudKeywordStore,
    KICK_THRESHOLD,
    ViolationStore,
    detect_fraud_text,
    detect_protected_notice,
    extract_fallback_keywords,
    filter_fraud_keywords,
    has_contact_card,
    kick_member_with_confirmation,
    text_from_segments,
)
from .reactions import (
    reaction_image_base64,
    record_reaction_sent,
    select_random_reaction_asset,
    select_reaction_asset,
)
from .rules import (
    ai_question,
    command_argument,
    has_management_permission,
    is_allowed_group,
    parse_mute_duration,
    parse_qq_ids,
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
MONTHLY_SALARY_CAT_CHANCE = 0.2
last_ai_requests: dict[int, float] = {}
REACTION_ASSET_DIR = Path(__file__).parent / "assets" / "reactions"
MONTHLY_SALARY_CAT_DIR = Path("/data/reaction_packs/monthly_salary_cat")
last_reactions: dict[int, float] = {}
keyword_store = KeywordStore(Path(os.environ.get("NAO_KEYWORDS_FILE", "/data/keywords.json")))
violation_store = ViolationStore(Path(os.environ.get("NAO_MODERATION_FILE", "/data/moderation.json")))
fraud_keyword_store = FraudKeywordStore(
    Path(os.environ.get("NAO_FRAUD_KEYWORDS_FILE", "/data/fraud_keywords.json"))
)
guess_person_sessions = GuessPersonSessions()


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


def is_management_command(event: GroupMessageEvent) -> bool:
    text = event.get_plaintext().strip()
    return event.is_tome() and (
        text in {"踢出", "撤回"} or command_argument(text, "禁言") is not None
    )


async def can_manage(bot: Bot, event: GroupMessageEvent) -> bool:
    role = sender_role(event)
    if role is None and not ADMIN_QQ_IDS:
        member = await bot.get_group_member_info(group_id=event.data.peer_id, user_id=event.data.sender_id)
        role = member.role
    return has_management_permission(event.data.sender_id, role, ADMIN_QQ_IDS)


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
            "反诈防护已开启：普通成员的重要通知、诈骗话术、QQ名片、二维码和诈骗图片会被撤回；"
            f"累计 {KICK_THRESHOLD} 次自动踢出。"
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
        f"QQ {target_id} 当前累计 {count}/{KICK_THRESHOLD} 次，最近原因：{reason}"
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


async def _first_image_url(bot: Bot, event: GroupMessageEvent) -> str | None:
    for segment in event.get_message():
        if segment.type != "image" or segment.data.get("sub_type") == "sticker":
            continue
        if url := segment.data.get("temp_url"):
            return str(url)
        if resource_id := segment.data.get("resource_id"):
            return await bot.get_resource_temp_url(resource_id=str(resource_id))
    return None


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
    if not image_url:
        return None
    try:
        image_result = await scan_image_url(image_url)
    except Exception:
        logger.exception("Anti-fraud image scan failed")
        return None
    if image_result.has_qr_code:
        return "普通成员发送二维码图片"
    if keyword := fraud_keyword_store.match(image_result.text):
        return f"图片命中违规词黑名单（{keyword}）"
    if detect_protected_notice(image_result.text):
        return "普通成员通过图片冒充重要通知或公告"
    return detect_fraud_text(image_result.text)


async def _handle_violation(bot: Bot, event: GroupMessageEvent, reason: str) -> None:
    group_id = event.data.peer_id
    user_id = event.data.sender_id
    try:
        await bot.recall_group_message(group_id=group_id, message_seq=event.data.message_seq)
    except Exception:
        logger.exception("Recall anti-fraud message failed")

    count = violation_store.add(group_id, user_id, reason)
    if count >= KICK_THRESHOLD:
        try:
            await kick_member_with_confirmation(bot, group_id, user_id)
        except Exception:
            logger.exception("Automatic anti-fraud kick failed")
            await bot.send_group_message(
                group_id=group_id,
                message=f"QQ {user_id} 已累计 {count} 次反诈违规，但自动踢出失败，请检查机器人权限。",
            )
            return
        await bot.send_group_message(
            group_id=group_id,
            message=f"QQ {user_id} 因累计 {count} 次反诈违规已被自动移出群聊。最近原因：{reason}",
        )
        return

    await bot.send_group_message(
        group_id=group_id,
        message=[
            MessageSegment.mention(user_id),
            MessageSegment.text(
                f" 该消息已被反诈防护撤回（{reason}）。当前 {count}/{KICK_THRESHOLD} 次，"
                f"累计 {KICK_THRESHOLD} 次将自动移出群聊。"
            ),
        ],
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


management_matcher = on_message(
    rule=Rule(is_test_group) & Rule(is_management_command),
    priority=4,
    block=True,
)


@management_matcher.handle()
async def handle_management(bot: Bot, event: GroupMessageEvent) -> None:
    if not await can_manage(bot, event):
        await management_matcher.finish("你没有使用群管理指令的权限。")

    text = event.get_plaintext().strip()
    group_id = event.data.peer_id
    if text == "撤回":
        if not event.reply:
            await management_matcher.finish("请回复需要撤回的消息，再发送 @nao 撤回。")
        try:
            await bot.recall_group_message(group_id=group_id, message_seq=event.reply.message_seq)
        except Exception:
            logger.exception("Recall group message failed")
            await management_matcher.finish("撤回失败，请确认机器人权限和消息时间。")
        await management_matcher.finish("已撤回。")

    target_id = mentioned_user_id(event)
    if target_id is None:
        await management_matcher.finish("请 @目标成员，或回复目标成员的消息。")
    if target_id in {event.self_id, event.data.sender_id}:
        await management_matcher.finish("不能对机器人或你自己执行此操作。")

    if text == "踢出":
        try:
            await bot.kick_group_member(group_id=group_id, user_id=target_id)
        except Exception:
            logger.exception("Kick group member failed")
            await management_matcher.finish("踢出失败，请确认机器人权限及目标成员身份。")
        await management_matcher.finish("已将该成员移出群聊。")

    try:
        duration = parse_mute_duration(text)
    except ValueError as error:
        await management_matcher.finish(str(error))
    if duration is None:
        return
    try:
        await bot.set_group_member_mute(group_id=group_id, user_id=target_id, duration=duration)
    except Exception:
        logger.exception("Mute group member failed")
        await management_matcher.finish("禁言失败，请确认机器人权限及目标成员身份。")
    if duration == 0:
        await management_matcher.finish("已解除禁言。")
    await management_matcher.finish(f"已禁言 {duration // 60} 分钟。")


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


ai_matcher = on_message(rule=Rule(is_test_group) & Rule(is_ai_command), priority=15, block=True)


@ai_matcher.handle()
async def handle_ai(event: GroupMessageEvent) -> None:
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
        answer = await ask_deepseek(DEEPSEEK_API_KEY, DEEPSEEK_MODEL, question)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        logger.exception("DeepSeek request failed")
        await ai_matcher.finish("AI 暂时不可用，请稍后再试。")
    reaction_now = monotonic()
    reaction_asset = select_reaction_asset(
        answer,
        event.data.sender_id,
        last_reactions,
        REACTION_ASSET_DIR,
        now=reaction_now,
    )
    if reaction_asset is None:
        reaction_asset = select_random_reaction_asset(
            event.data.sender_id,
            last_reactions,
            MONTHLY_SALARY_CAT_DIR,
            now=reaction_now,
            chance=MONTHLY_SALARY_CAT_CHANCE,
        )
    if reaction_asset is None:
        await ai_matcher.finish(answer)
    try:
        await ai_matcher.send(
            Message(
                [
                    MessageSegment.text(answer),
                    MessageSegment.image(
                        base64=reaction_image_base64(reaction_asset),
                        sub_type="sticker",
                    ),
                ]
            )
        )
    except (OSError, NetworkError):
        logger.exception("Reaction sticker send failed; falling back to text")
        await ai_matcher.finish(answer)
    record_reaction_sent(last_reactions, event.data.sender_id, now=reaction_now)
    await ai_matcher.finish()


def is_static_message(event: GroupMessageEvent) -> bool:
    return reply_for_text(event.get_plaintext(), event.is_tome()) is not None


static_matcher = on_message(
    rule=Rule(is_test_group) & Rule(is_static_message),
    priority=4,
    block=True,
)


@static_matcher.handle()
async def handle_message(event: GroupMessageEvent) -> None:
    response = reply_for_text(event.get_plaintext(), event.is_tome())
    if response is not None:
        await static_matcher.finish(response)


keyword_reply_matcher = on_message(rule=Rule(is_test_group), priority=20, block=False)


@keyword_reply_matcher.handle()
async def handle_keyword_reply(event: GroupMessageEvent) -> None:
    response = keyword_store.get(event.get_plaintext().strip())
    if response is not None:
        await keyword_reply_matcher.finish(response)


welcome_matcher = on_notice(priority=10, block=False)


@welcome_matcher.handle()
async def welcome_member(bot: Bot, event: GroupMemberIncreaseEvent) -> None:
    if event.data.group_id != TEST_GROUP_ID or event.data.user_id == event.self_id:
        return
    await bot.send_group_message(
        group_id=event.data.group_id,
        message=[MessageSegment.mention(event.data.user_id), MessageSegment.text(" 欢迎加入本群！")],
    )
