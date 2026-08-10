import html
import json
import re
from dataclasses import dataclass
from datetime import datetime

import httpx

from .reminders import (
    CHINA_TIMEZONE,
    MAX_REMINDER_CONTENT_LENGTH,
    REMINDER_TIME_FORMAT,
    ReminderCommand,
)


API_URL = "https://api.deepseek.com/chat/completions"
SYSTEM_PROMPT = """你是 QQ 群里的机器人助手 nao。
请使用简体中文直接回答，默认保持简洁；需要步骤时再分点说明。
不要声称自己已经执行现实操作或群管理操作。"""


PLAIN_TEXT_PROMPT = "reply 字段必须是适合 QQ 展示的纯文本，不要使用 Markdown、代码围栏、表格或富文本格式。"
REACTION_SCENES = (
    "开心",
    "无语",
    "委屈",
    "震惊",
    "吃瓜",
    "摸鱼",
    "加班",
    "鼓励",
    "道歉",
    "晚安",
    "拒绝",
    "阴阳怪气",
    "问候",
    "思考",
    "庆祝",
    "好笑",
)
REACTION_CONTEXTS = frozenset({"serious", "casual", "playful"})
REACTION_PROMPT = f"""只输出 JSON 对象，不要输出代码围栏或额外文字。
格式：{{"reply":"给用户的纯文本回答",\
"reaction":{{"scene":null,"context":"serious","confidence":0.0}}}}
reaction.scene 只能是 null 或以下场景之一：{'、'.join(REACTION_SCENES)}。
context 只能是 serious、casual、playful：知识解释、求助和严肃话题用 serious；日常聊天用 casual；接梗、玩笑和强烈情绪用 playful。
confidence 表示表情与整段对话的匹配把握，范围 0 到 1。没有真正贴切的表情时 scene 必须为 null 且 confidence 为 0。
不要为了发表情而改变回答内容。"""
TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
HORIZONTAL_RULE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")
FRAUD_KEYWORD_PROMPT = """从用户提供的违规广告原文中提取 3 到 8 个高风险短语。
短语必须在原文中真实出现，优先选择诈骗手法、承诺、引流账号和有辨识度的组合词。
不要提取“微信”“论文”“服务”“联系”“通知”等单独出现时可能正常的宽泛词。
只输出 JSON，格式为 {"keywords":["短语1","短语2"]}。"""
PROACTIVE_PROMPT = """你是 QQ 群里的“小火人”群聊搭子，负责判断是否值得主动接当前这句话。
只在当前消息有明显的梗、反差、调侃、抛话题或适合自然接话时回复；普通陈述、技术讨论、严肃求助、争吵、隐私、广告、链接和看不懂的内容保持沉默。
回复要像熟人群聊：5 到 30 个汉字，短、自然、有网感，可以接流行梗，但不要解释梗、强行玩梗、冒犯成员或编造事实。
只输出 JSON 对象，不要代码围栏或额外文字。格式：{"should_reply":true,"reply":"接梗短句","confidence":0.9}。
不应回复时使用：{"should_reply":false,"reply":"","confidence":0.0}。confidence 表示主动插话自然且合适的把握，范围 0 到 1。"""
PROACTIVE_MIN_CONFIDENCE = 0.75
REMINDER_TOOL = {
    "type": "function",
    "function": {
        "name": "create_reminder",
        "description": "创建一次性、每天或每周重复的 QQ 群定时提醒",
        "parameters": {
            "type": "object",
            "properties": {
                "remind_at": {
                    "type": "string",
                    "description": "下一次执行的北京时间，格式 YYYY-MM-DD HH:MM",
                },
                "content": {
                    "type": "string",
                    "description": "到点发送的提醒内容，不包含时间和重复周期",
                },
                "repeat_days": {
                    "type": "integer",
                    "enum": [0, 1, 7],
                    "description": "0 表示一次性，1 表示每天，7 表示每周",
                },
                "time_defaulted": {
                    "type": "boolean",
                    "description": "用户没有说明具体时刻、因而使用 09:00 时为 true",
                },
            },
            "required": ["remind_at", "content", "repeat_days", "time_defaulted"],
            "additionalProperties": False,
        },
    },
}


@dataclass(frozen=True)
class AIAnswer:
    text: str
    reaction_scene: str | None = None
    reaction_context: str = "serious"
    reaction_confidence: float = 0.0


def _plain_link(match: re.Match[str]) -> str:
    label = match.group(1).strip()
    url = match.group(2).strip()
    return label if label == url else f"{label}（{url}）"


def markdown_to_plain_text(content: str) -> str:
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"图片：\1（\2）", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _plain_link, text)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"</?[^>\n]+>", "", text)
    text = html.unescape(text)

    lines: list[str] = []
    for line in text.split("\n"):
        if re.match(r"^\s*(```|~~~)", line):
            continue
        if TABLE_SEPARATOR.match(line) or HORIZONTAL_RULE.match(line):
            continue

        line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        line = re.sub(r"^\s{0,3}>\s?", "", line)

        task = re.match(r"^(\s*)[-*+]\s+\[([xX ])\]\s+(.*)$", line)
        if task:
            state = "已完成" if task.group(2).lower() == "x" else "待处理"
            line = f"{task.group(1)}• {state}：{task.group(3)}"
        else:
            line = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", line)

        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2:
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            line = " / ".join(cell for cell in cells if cell)

        line = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", line)
        line = re.sub(r"__([^_\n]+)__", r"\1", line)
        line = re.sub(r"~~([^~\n]+)~~", r"\1", line)
        line = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", line)
        line = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", line)
        line = re.sub(r"\\([\\`*_{}\[\]()#+\-.!>])", r"\1", line)
        lines.append(line.rstrip())

    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _trim_answer(answer: str) -> str:
    if len(answer) <= 1500:
        return answer
    return f"{answer[:1500]}\n\n（回答较长，已截断）"


def parse_ai_response(content: str) -> AIAnswer:
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        answer = markdown_to_plain_text(content)
        if not answer:
            raise ValueError("DeepSeek returned an empty plain-text response")
        return AIAnswer(text=_trim_answer(answer))

    if not isinstance(data, dict) or not isinstance(data.get("reply"), str):
        raise ValueError("DeepSeek returned an invalid AI response")
    answer = markdown_to_plain_text(data["reply"])
    if not answer:
        raise ValueError("DeepSeek returned an empty plain-text response")

    reaction = data.get("reaction")
    if not isinstance(reaction, dict):
        return AIAnswer(text=_trim_answer(answer))

    scene = reaction.get("scene")
    context = reaction.get("context")
    confidence = reaction.get("confidence")
    if (
        scene not in REACTION_SCENES
        or context not in REACTION_CONTEXTS
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        return AIAnswer(text=_trim_answer(answer))

    return AIAnswer(
        text=_trim_answer(answer),
        reaction_scene=scene,
        reaction_context=context,
        reaction_confidence=float(confidence),
    )


def parse_fraud_keyword_response(content: str) -> list[str]:
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1)
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("keywords")
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise ValueError("DeepSeek returned invalid fraud keywords")
    return [item.strip() for item in data if item.strip()]


def parse_proactive_response(content: str) -> str | None:
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError("DeepSeek returned an invalid proactive response") from error
    if not isinstance(data, dict):
        raise ValueError("DeepSeek returned an invalid proactive response")

    should_reply = data.get("should_reply")
    reply = data.get("reply")
    confidence = data.get("confidence")
    if (
        not isinstance(should_reply, bool)
        or not isinstance(reply, str)
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        raise ValueError("DeepSeek returned an invalid proactive response")
    if not should_reply or confidence < PROACTIVE_MIN_CONFIDENCE:
        return None

    answer = " ".join(markdown_to_plain_text(reply).split())
    if not answer:
        raise ValueError("DeepSeek returned an empty proactive reply")
    return answer[:80]


async def request_proactive_reply(
    api_key: str,
    model: str,
    recent_messages: list[str],
    current_message: str,
) -> str | None:
    context = "\n".join(recent_messages[-6:]) or "（没有更早的上下文）"
    user_content = f"最近群聊：\n{context}\n\n当前消息：\n{current_message[:200]}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": PROACTIVE_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 300,
        "temperature": 0.8,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = httpx.Timeout(60, connect=10)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(API_URL, headers=headers, json=payload)
        response.raise_for_status()

    content = response.json()["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError("DeepSeek returned an empty proactive response")
    return parse_proactive_response(content)


def parse_reminder_tool_call(data: dict, now: datetime) -> ReminderCommand:
    try:
        tool_call = data["choices"][0]["message"]["tool_calls"][0]
        function = tool_call["function"]
        arguments = json.loads(function["arguments"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("DeepSeek returned an invalid reminder tool call") from error
    if function.get("name") != "create_reminder" or not isinstance(arguments, dict):
        raise ValueError("DeepSeek returned an invalid reminder tool call")

    remind_at_text = arguments.get("remind_at")
    content = arguments.get("content")
    repeat_days = arguments.get("repeat_days")
    time_defaulted = arguments.get("time_defaulted", False)
    if not isinstance(remind_at_text, str) or not isinstance(content, str):
        raise ValueError("DeepSeek returned invalid reminder details")
    if isinstance(repeat_days, bool) or repeat_days not in {0, 1, 7}:
        raise ValueError("DeepSeek returned an unsupported reminder schedule")
    if not isinstance(time_defaulted, bool):
        raise ValueError("DeepSeek returned invalid reminder details")

    try:
        remind_at = datetime.strptime(remind_at_text, REMINDER_TIME_FORMAT).replace(
            tzinfo=CHINA_TIMEZONE
        )
    except ValueError as error:
        raise ValueError("DeepSeek returned an invalid reminder time") from error
    current_time = now.astimezone(CHINA_TIMEZONE) if now.tzinfo else now.replace(tzinfo=CHINA_TIMEZONE)
    content = content.strip()
    if remind_at <= current_time:
        raise ValueError("提醒时间必须晚于当前时间")
    if not content:
        raise ValueError("请写上提醒内容")
    if len(content) > MAX_REMINDER_CONTENT_LENGTH:
        raise ValueError(f"提醒内容不能超过 {MAX_REMINDER_CONTENT_LENGTH} 个字符")
    return ReminderCommand(
        action="add",
        remind_at=remind_at,
        content=content,
        time_defaulted=time_defaulted,
        repeat_days=repeat_days,
    )


def _reminder_prompt(current_time: datetime) -> str:
    return f"""你可以为管理员调用 create_reminder 创建定时任务。
当前北京时间：{current_time.strftime('%Y-%m-%d %H:%M')}，星期{'一二三四五六日'[current_time.weekday()]}。
一次性任务 repeat_days=0；“每天”任务为 1；“每周”任务为 7，并计算下一次执行时间。
如果用户没有给出具体时刻，使用 09:00，并将 time_defaulted 设为 true。
提醒内容只保留到点需要发送的事情，不要包含日期、时刻或“提醒我”等调度描述。
只有用户明确要求创建提醒或定时任务时才调用工具；不要声称已经执行工具。"""


async def request_reminder_command(
    api_key: str,
    model: str,
    request: str,
    now: datetime | None = None,
) -> ReminderCommand:
    current_time = now or datetime.now(CHINA_TIMEZONE)
    current_time = (
        current_time.astimezone(CHINA_TIMEZONE)
        if current_time.tzinfo
        else current_time.replace(tzinfo=CHINA_TIMEZONE)
    )
    prompt = _reminder_prompt(current_time)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": request[:1000]},
        ],
        "max_tokens": 800,
        "temperature": 0,
        "tools": [REMINDER_TOOL],
        "tool_choice": "auto",
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = httpx.Timeout(30, connect=10)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(API_URL, headers=headers, json=payload)
        response.raise_for_status()

    return parse_reminder_tool_call(response.json(), current_time)


async def extract_fraud_keywords(api_key: str, model: str, source_text: str) -> list[str]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": FRAUD_KEYWORD_PROMPT},
            {"role": "user", "content": source_text[:4000]},
        ],
        "max_tokens": 1200,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = httpx.Timeout(30, connect=10)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(API_URL, headers=headers, json=payload)
        response.raise_for_status()

    content = response.json()["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError("DeepSeek returned an empty fraud keyword response")
    return parse_fraud_keyword_response(content)


async def ask_deepseek(
    api_key: str,
    model: str,
    question: str,
    allow_reminder: bool = False,
    now: datetime | None = None,
) -> AIAnswer | ReminderCommand:
    current_time = now or datetime.now(CHINA_TIMEZONE)
    current_time = (
        current_time.astimezone(CHINA_TIMEZONE)
        if current_time.tzinfo
        else current_time.replace(tzinfo=CHINA_TIMEZONE)
    )
    system_prompt = f"{SYSTEM_PROMPT}\n{PLAIN_TEXT_PROMPT}\n{REACTION_PROMPT}"
    if allow_reminder:
        system_prompt = f"{system_prompt}\n{_reminder_prompt(current_time)}"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {"role": "user", "content": question},
        ],
        "max_tokens": 1200,
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }
    if allow_reminder:
        payload["tools"] = [REMINDER_TOOL]
        payload["tool_choice"] = "auto"
    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = httpx.Timeout(60, connect=10)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(API_URL, headers=headers, json=payload)
        response.raise_for_status()

    data = response.json()
    message = data["choices"][0]["message"]
    if message.get("tool_calls"):
        return parse_reminder_tool_call(data, current_time)
    content = message["content"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError("DeepSeek returned an empty response")

    return parse_ai_response(content)
