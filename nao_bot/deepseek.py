import html
import json
import re
from dataclasses import dataclass

import httpx


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


async def ask_deepseek(api_key: str, model: str, question: str) -> AIAnswer:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": f"{SYSTEM_PROMPT}\n{PLAIN_TEXT_PROMPT}\n{REACTION_PROMPT}",
            },
            {"role": "user", "content": question},
        ],
        "max_tokens": 1200,
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = httpx.Timeout(60, connect=10)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(API_URL, headers=headers, json=payload)
        response.raise_for_status()

    content = response.json()["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError("DeepSeek returned an empty response")

    return parse_ai_response(content)
