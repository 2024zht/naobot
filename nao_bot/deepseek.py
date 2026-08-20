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
RESPONSES_API_URL = "https://api.deepseek.com/responses"
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
MAX_AI_RECOVERY_LENGTH = 12_000
STRUCTURED_AI_OBJECT = re.compile(
    r'''\{\s*(?:["']|[A-Za-z_][A-Za-z0-9_-]*\s*:)'''
)
EXTRA_AI_FIELD = re.compile(
    r''',\s*(?:"(?:[^"\\]|\\.)+"|'(?:[^'\\]|\\.)+'|[A-Za-z_][A-Za-z0-9_-]*)\s*:'''
)
REACTION_PROMPT = f"""只输出 JSON 对象，不要输出代码围栏或额外文字。
格式：{{"reply":"给用户的纯文本回答",\
"reaction":{{"scene":null,"context":"serious","confidence":0.0}}}}
reply 中的双引号和反斜杠必须按 JSON 规则转义。
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
这是主动群聊模式，消息不会 @ 你；不要仅仅因为没有 @ 就忽略。当前消息出现反问、夸张、明显情绪，或与最近群聊形成反差时，应优先自然接话。
只在当前消息有明显的梗、反差、调侃、抛话题或适合自然接话时回复；普通陈述、技术讨论、严肃求助、争吵、隐私、广告、链接以及不像网络梗又看不懂的内容保持沉默。
回复要像熟人群聊：一到三句、约 20 到 120 个汉字，短而有内容，可以顺着梗补一句或继续抛话题；不要解释梗、强行玩梗、冒犯成员或编造事实。
action 只能是 reply、search、ignore。能直接自然接话时用 reply；明显值得接但涉及近期或陌生网络梗、你无法可靠理解时才用 search，并给出简短搜索词；其他情况用 ignore。
判定示例：前文说绝不加班，当前说“六点零一分通知开会，早一秒都怕我跑了是吧”应使用 reply；当前问“某个突然流行的陌生词到底是什么新梗”应使用 search；当前通知线上数据库故障、要求暂停操作应使用 ignore。
search_query 必须原样包含当前消息中需要核实的人名、短语或梗，不要改写或音译。
只输出 JSON 对象，不要代码围栏或额外文字。格式：{"action":"reply","reply":"一到三句接梗内容","search_query":"","confidence":0.9}。
需要搜索时格式：{"action":"search","reply":"","search_query":"需要核实的梗 搜索词","confidence":0.9}。不应回复时 action 为 ignore。confidence 表示主动插话自然且合适的把握，范围 0 到 1。"""
SEARCHED_PROACTIVE_PROMPT = """你是 QQ 群里的“小火人”群聊搭子。最多执行一次联网搜索，核实指定网络梗的含义和近期用法。
回复必须针对输入中的“当前消息”，不能改成回应搜索结果里的其他话题。只有搜索结果与当前消息中的梗明确匹配时，才生成一到三句、约 20 到 120 个汉字的自然接梗回复，可以顺着梗补一句或继续抛话题。
不要解释搜索过程、展示链接、写成百科说明、强行玩梗、冒犯成员或编造事实。若搜索结果不匹配或搜索后仍没有把握，返回空回复和低置信度。无论是否回复，都只输出符合指定结构的 JSON 对象，不要输出额外文字。"""
PROACTIVE_MIN_CONFIDENCE = 0.75
PROACTIVE_SEARCH_MIN_CONFIDENCE = 0.65
PROACTIVE_MAX_REPLY_LENGTH = 180
SEARCHED_PROACTIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["reply", "confidence"],
    "additionalProperties": False,
}
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


@dataclass(frozen=True)
class ProactiveDecision:
    reply: str | None = None
    search_query: str | None = None


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


def _skip_json_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


def _unescaped_quote_indexes(text: str, start: int):
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if char == '"' and not escaped:
            yield index
        escaped = not escaped if char == "\\" else False


def _decode_recovered_reply(raw_reply: str) -> str:
    repaired: list[str] = []
    escaped = False
    repaired_quote = False
    for char in raw_reply:
        if char == '"' and not escaped:
            repaired.append('\\"')
            repaired_quote = True
        else:
            repaired.append(char)
        escaped = not escaped if char == "\\" else False
    if not repaired_quote:
        raise ValueError("DeepSeek returned an invalid AI response")
    try:
        reply = json.loads(f'"{"".join(repaired)}"')
    except json.JSONDecodeError as error:
        raise ValueError("DeepSeek returned an invalid AI response") from error
    if not isinstance(reply, str):
        raise ValueError("DeepSeek returned an invalid AI response")
    return reply


def _recover_malformed_ai_reply(text: str) -> str:
    if len(text) > MAX_AI_RECOVERY_LENGTH:
        raise ValueError("DeepSeek returned an invalid AI response")
    decoder = json.JSONDecoder()
    index = _skip_json_whitespace(text, 0)
    if index >= len(text) or text[index] != "{":
        raise ValueError("DeepSeek returned an invalid AI response")
    index = _skip_json_whitespace(text, index + 1)
    try:
        key, index = decoder.raw_decode(text, index)
    except json.JSONDecodeError as error:
        raise ValueError("DeepSeek returned an invalid AI response") from error
    index = _skip_json_whitespace(text, index)
    if key != "reply" or index >= len(text) or text[index] != ":":
        raise ValueError("DeepSeek returned an invalid AI response")
    index = _skip_json_whitespace(text, index + 1)
    if index >= len(text) or text[index] != '"':
        raise ValueError("DeepSeek returned an invalid AI response")

    reply_start = index + 1
    recovered: list[str] = []
    for reply_end in _unescaped_quote_indexes(text, reply_start):
        index = _skip_json_whitespace(text, reply_end + 1)
        if index >= len(text) or text[index] != ",":
            continue
        index = _skip_json_whitespace(text, index + 1)
        try:
            reaction_key, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            continue
        index = _skip_json_whitespace(text, index)
        if reaction_key != "reaction" or index >= len(text) or text[index] != ":":
            continue
        index = _skip_json_whitespace(text, index + 1)
        try:
            reaction, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            continue
        index = _skip_json_whitespace(text, index)
        if not isinstance(reaction, dict) or index >= len(text) or text[index] != "}":
            continue
        if _skip_json_whitespace(text, index + 1) != len(text):
            continue
        raw_reply = text[reply_start:reply_end]
        if EXTRA_AI_FIELD.search(raw_reply):
            raise ValueError("DeepSeek returned an invalid AI response")
        recovered.append(_decode_recovered_reply(raw_reply))

    if len(recovered) != 1:
        raise ValueError("DeepSeek returned an invalid AI response")
    return recovered[0]


def parse_ai_response(content: str) -> AIAnswer:
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        if STRUCTURED_AI_OBJECT.search(text):
            try:
                answer = markdown_to_plain_text(_recover_malformed_ai_reply(text))
            except ValueError as recovery_error:
                raise ValueError("DeepSeek returned an invalid AI response") from recovery_error
            if not answer:
                raise ValueError("DeepSeek returned an empty plain-text response") from error
            return AIAnswer(text=_trim_answer(answer))
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


def _proactive_reply(value: str) -> str:
    answer = " ".join(markdown_to_plain_text(value).split())
    if not answer:
        raise ValueError("DeepSeek returned an empty proactive reply")
    return answer[:PROACTIVE_MAX_REPLY_LENGTH]


def parse_proactive_response(content: str) -> ProactiveDecision:
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

    action = data.get("action")
    reply = data.get("reply")
    search_query = data.get("search_query")
    confidence = data.get("confidence")
    if (
        action not in {"reply", "search", "ignore"}
        or not isinstance(reply, str)
        or not isinstance(search_query, str)
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        raise ValueError("DeepSeek returned an invalid proactive response")
    if action == "ignore":
        return ProactiveDecision()
    if action == "search":
        if confidence < PROACTIVE_SEARCH_MIN_CONFIDENCE:
            return ProactiveDecision()
        query = " ".join(search_query.split())
        if not query:
            raise ValueError("DeepSeek returned an empty proactive search query")
        return ProactiveDecision(search_query=query[:100])
    if confidence < PROACTIVE_MIN_CONFIDENCE:
        return ProactiveDecision()
    return ProactiveDecision(reply=_proactive_reply(reply))


async def request_proactive_decision(
    api_key: str,
    model: str,
    recent_messages: list[str],
    current_message: str,
) -> ProactiveDecision:
    context = "\n".join(recent_messages[-6:]) or "（没有更早的上下文）"
    user_content = f"最近群聊：\n{context}\n\n当前消息：\n{current_message[:200]}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": PROACTIVE_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 400,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
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


def parse_responses_output_text(data: dict) -> str:
    if not isinstance(data, dict) or data.get("status") != "completed":
        raise ValueError("DeepSeek web search response did not complete")
    output = data.get("output")
    if not isinstance(output, list):
        raise ValueError("DeepSeek returned an invalid web search response")
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return text
    raise ValueError("DeepSeek returned an empty web search response")


def parse_searched_proactive_response(content: str) -> str | None:
    text = content.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
        decoder = json.JSONDecoder()
        for start in range(len(text) - 1, -1, -1):
            if text[start] != "{":
                continue
            try:
                candidate, end = decoder.raw_decode(text[start:])
            except json.JSONDecodeError:
                continue
            if not text[start + end :].strip():
                data = candidate
                break
    if not isinstance(data, dict):
        raise ValueError("DeepSeek returned an invalid searched proactive response")
    reply = data.get("reply")
    confidence = data.get("confidence")
    if (
        not isinstance(reply, str)
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        raise ValueError("DeepSeek returned an invalid searched proactive response")
    if confidence < PROACTIVE_MIN_CONFIDENCE or not reply.strip():
        return None
    return _proactive_reply(reply)


async def request_searched_proactive_reply(
    api_key: str,
    model: str,
    recent_messages: list[str],
    current_message: str,
    search_query: str,
) -> str | None:
    context = "\n".join(recent_messages[-6:]) or "（没有更早的上下文）"
    input_text = (
        f"最近群聊：\n{context}\n\n当前消息：\n{current_message[:200]}"
        f"\n\n建议核实：\n{search_query[:100]}"
    )
    payload = {
        "model": model,
        "instructions": SEARCHED_PROACTIVE_PROMPT,
        "input": input_text,
        "tools": [{"type": "web_search"}],
        "tool_choice": "auto",
        "max_output_tokens": 2000,
        "reasoning": {"effort": "low"},
        "text": {
            "format": {
                "type": "json_schema",
                "name": "searched_proactive_reply",
                "strict": True,
                "schema": SEARCHED_PROACTIVE_SCHEMA,
            }
        },
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = httpx.Timeout(120, connect=10)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(RESPONSES_API_URL, headers=headers, json=payload)
        response.raise_for_status()

    content = parse_responses_output_text(response.json())
    return parse_searched_proactive_response(content)


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
