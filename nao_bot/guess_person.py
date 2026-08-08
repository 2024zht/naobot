import json
import re
from dataclasses import dataclass
from time import monotonic

import httpx

from .deepseek import API_URL


FIRST_QUESTION = "你想的人物是真实存在的吗？"
GAME_START_MESSAGE = "我已经想好一个人物，请开始提问。"
MAX_QUESTIONS = 20
SESSION_TIMEOUT_SECONDS = 5 * 60
VALID_ANSWERS = frozenset({"是", "否", "不知道", "可能", "可能不是"})

SYSTEM_PROMPT = """你正在主持一个二十问猜人物游戏。玩家已经想好一个真实或虚构人物。
历史消息中 assistant 是你已经问过的问题，紧随其后的 user 是玩家对该问题的回答。你现在必须根据最新答案推进游戏。
每次只做一件事：提出一个能用“是、否、不知道、可能、可能不是”回答的新问题，或者直接猜一个人物。
问题应优先缩小人物范围，严禁重复历史中的 assistant 问题。确定性足够时尽早猜测。
必须调用 next_turn 工具返回提问或猜测，不要输出普通文本。"""

NEXT_TURN_TOOL = {
    "type": "function",
    "function": {
        "name": "next_turn",
        "description": "提出下一个问题或猜测人物",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["question", "guess"]},
                "text": {"type": "string"},
            },
            "required": ["type", "text"],
            "additionalProperties": False,
        },
    },
}


@dataclass(frozen=True)
class GuessPersonTurn:
    type: str
    text: str


@dataclass(frozen=True)
class PreparedGuessPersonAnswer:
    history: list[dict[str, str]]
    force_guess: bool


@dataclass
class _GuessPersonSession:
    history: list[dict[str, str]]
    questions_asked: int
    updated_at: float


def parse_guess_person_response(content: str) -> GuessPersonTurn:
    stripped = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1)

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as error:
        raise ValueError("DeepSeek returned invalid guess-person JSON") from error

    if not isinstance(payload, dict) or set(payload) != {"type", "text"}:
        raise ValueError("DeepSeek returned an invalid guess-person response")
    if payload["type"] not in {"question", "guess"} or not isinstance(payload["text"], str):
        raise ValueError("DeepSeek returned an invalid guess-person response")

    text = payload["text"].strip()
    if not text:
        raise ValueError("DeepSeek returned an empty guess-person response")
    return GuessPersonTurn(payload["type"], text)


async def request_guess_person_turn(
    api_key: str,
    model: str,
    history: list[dict[str, str]],
    force_guess: bool = False,
) -> GuessPersonTurn:
    system_prompt = SYSTEM_PROMPT
    if force_guess:
        system_prompt += "\n已经问满 20 个问题。你必须立刻猜测一个最可能的人物，type 必须是 guess。"

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "system", "content": system_prompt}, *history],
                "temperature": 0,
                "max_tokens": 500,
                "thinking": {"type": "disabled"},
                "tools": [NEXT_TURN_TOOL],
                "tool_choice": {"type": "function", "function": {"name": "next_turn"}},
            },
        )
        response.raise_for_status()

    message = response.json()["choices"][0]["message"]
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or len(tool_calls) != 1:
        raise ValueError("DeepSeek returned an invalid guess-person tool call")
    function = tool_calls[0].get("function")
    if not isinstance(function, dict) or function.get("name") != "next_turn":
        raise ValueError("DeepSeek returned an invalid guess-person tool call")
    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        raise ValueError("DeepSeek returned invalid guess-person tool arguments")

    turn = parse_guess_person_response(arguments)
    if force_guess and turn.type != "guess":
        raise ValueError("DeepSeek did not guess after the final question")
    if turn.type == "question":
        question = turn.text.rstrip("？?。.!！ ")
        previous_questions = {
            message["content"].rstrip("？?。.!！ ")
            for message in history
            if message.get("role") == "assistant"
        }
        if question in previous_questions:
            raise ValueError("DeepSeek repeated a guess-person question")
    return turn


class GuessPersonSessions:
    def __init__(self) -> None:
        self._sessions: dict[tuple[int, int], _GuessPersonSession] = {}

    def start(self, group_id: int, user_id: int, now: float | None = None) -> str:
        timestamp = monotonic() if now is None else now
        self._sessions[(group_id, user_id)] = _GuessPersonSession(
            history=[
                {"role": "user", "content": GAME_START_MESSAGE},
                {"role": "assistant", "content": FIRST_QUESTION},
            ],
            questions_asked=1,
            updated_at=timestamp,
        )
        return FIRST_QUESTION

    def has_active(self, group_id: int, user_id: int, now: float | None = None) -> bool:
        return self._get(group_id, user_id, now) is not None

    def prepare_answer(
        self,
        group_id: int,
        user_id: int,
        answer: str,
        now: float | None = None,
    ) -> PreparedGuessPersonAnswer:
        session = self._get(group_id, user_id, now)
        if session is None:
            raise KeyError("guess-person session is not active")

        normalized = answer.strip()
        if normalized not in VALID_ANSWERS:
            raise ValueError("invalid guess-person answer")
        return PreparedGuessPersonAnswer(
            history=[*session.history, {"role": "user", "content": normalized}],
            force_guess=session.questions_asked >= MAX_QUESTIONS,
        )

    def apply_turn(
        self,
        group_id: int,
        user_id: int,
        prepared: PreparedGuessPersonAnswer,
        turn: GuessPersonTurn,
        now: float | None = None,
    ) -> int | None:
        if prepared.force_guess and turn.type != "guess":
            raise ValueError("the final turn must be a guess")

        session = self._get(group_id, user_id, now)
        if session is None:
            raise KeyError("guess-person session is not active")
        if turn.type == "guess":
            self._sessions.pop((group_id, user_id), None)
            return None

        session.history = [*prepared.history, {"role": "assistant", "content": turn.text}]
        session.questions_asked += 1
        session.updated_at = monotonic() if now is None else now
        return session.questions_asked

    def end(self, group_id: int, user_id: int) -> bool:
        return self._sessions.pop((group_id, user_id), None) is not None

    def _get(
        self,
        group_id: int,
        user_id: int,
        now: float | None,
    ) -> _GuessPersonSession | None:
        key = (group_id, user_id)
        session = self._sessions.get(key)
        if session is None:
            return None

        timestamp = monotonic() if now is None else now
        if timestamp - session.updated_at >= SESSION_TIMEOUT_SECONDS:
            self._sessions.pop(key, None)
            return None
        return session
