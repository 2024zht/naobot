import asyncio

import pytest

import nao_bot.guess_person as guess_person
from nao_bot.guess_person import (
    FIRST_QUESTION,
    MAX_QUESTIONS,
    GuessPersonSessions,
    GuessPersonTurn,
    parse_guess_person_response,
    request_guess_person_turn,
)


def test_parse_guess_person_response_accepts_strict_json_and_code_fences():
    assert parse_guess_person_response(
        '{"type":"question","text":"这个人物是中国人吗？"}'
    ) == GuessPersonTurn("question", "这个人物是中国人吗？")
    assert parse_guess_person_response(
        '```json\n{"type":"guess","text":"孙悟空"}\n```'
    ) == GuessPersonTurn("guess", "孙悟空")


@pytest.mark.parametrize(
    "content",
    [
        "孙悟空",
        '{"type":"answer","text":"孙悟空"}',
        '{"type":"guess","text":""}',
        '{"type":"question","text":"问题","extra":true}',
        '[{"type":"guess","text":"孙悟空"}]',
    ],
)
def test_parse_guess_person_response_rejects_invalid_protocol(content):
    with pytest.raises(ValueError):
        parse_guess_person_response(content)


def test_sessions_start_progress_guess_and_exit():
    sessions = GuessPersonSessions()

    assert sessions.start(100, 200, now=10) == FIRST_QUESTION
    prepared = sessions.prepare_answer(100, 200, "是", now=20)
    assert prepared.history[0] == {
        "role": "user",
        "content": "我已经想好一个人物，请开始提问。",
    }
    assert prepared.history[-1] == {"role": "user", "content": "是"}
    assert prepared.force_guess is False

    sessions.apply_turn(
        100,
        200,
        prepared,
        GuessPersonTurn("question", "这个人物是男性吗？"),
        now=30,
    )
    assert sessions.has_active(100, 200, now=30) is True

    prepared = sessions.prepare_answer(100, 200, "可能", now=40)
    sessions.apply_turn(100, 200, prepared, GuessPersonTurn("guess", "李白"), now=50)
    assert sessions.has_active(100, 200, now=50) is False

    sessions.start(100, 200, now=60)
    assert sessions.end(100, 200) is True
    assert sessions.end(100, 200) is False


def test_sessions_are_isolated_by_group_and_user():
    sessions = GuessPersonSessions()
    sessions.start(100, 200, now=10)

    assert sessions.has_active(100, 200, now=10) is True
    assert sessions.has_active(100, 201, now=10) is False
    assert sessions.has_active(101, 200, now=10) is False


def test_session_expires_after_five_minutes():
    sessions = GuessPersonSessions()
    sessions.start(100, 200, now=10)

    assert sessions.has_active(100, 200, now=309) is True
    assert sessions.has_active(100, 200, now=311) is False
    with pytest.raises(KeyError):
        sessions.prepare_answer(100, 200, "是", now=311)


def test_twentieth_answer_forces_a_guess():
    sessions = GuessPersonSessions()
    sessions.start(100, 200, now=0)

    for question_number in range(2, MAX_QUESTIONS + 1):
        prepared = sessions.prepare_answer(100, 200, "不知道", now=question_number)
        sessions.apply_turn(
            100,
            200,
            prepared,
            GuessPersonTurn("question", f"第 {question_number} 个问题？"),
            now=question_number,
        )

    prepared = sessions.prepare_answer(100, 200, "否", now=MAX_QUESTIONS + 1)
    assert prepared.force_guess is True
    with pytest.raises(ValueError):
        sessions.apply_turn(
            100,
            200,
            prepared,
            GuessPersonTurn("question", "还想继续提问？"),
            now=MAX_QUESTIONS + 1,
        )


def test_request_guess_person_turn_uses_deterministic_tool_protocol(monkeypatch):
    requests = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "next_turn",
                                        "arguments": '{"type":"guess","text":"鲁迅"}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers, json):
            requests.append((url, headers, json))
            return Response()

    monkeypatch.setattr(guess_person.httpx, "AsyncClient", lambda **kwargs: Client())

    result = asyncio.run(
        request_guess_person_turn(
            "key",
            "model",
            [
                {"role": "user", "content": "我已经想好一个人物，请开始提问。"},
                {"role": "assistant", "content": FIRST_QUESTION},
                {"role": "user", "content": "是"},
            ],
            force_guess=True,
        )
    )

    assert result == GuessPersonTurn("guess", "鲁迅")
    payload = requests[0][2]
    assert payload["temperature"] == 0
    assert payload["max_tokens"] == 500
    assert payload["thinking"] == {"type": "disabled"}
    assert "response_format" not in payload
    assert payload["tool_choice"] == {
        "type": "function",
        "function": {"name": "next_turn"},
    }
    assert "必须立刻猜测" in payload["messages"][0]["content"]
    assert payload["messages"][-1] == {"role": "user", "content": "是"}


def test_request_guess_person_turn_rejects_repeated_questions(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "next_turn",
                                        "arguments": (
                                            f'{{"type":"question","text":"{FIRST_QUESTION}"}}'
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ]
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers, json):
            return Response()

    monkeypatch.setattr(guess_person.httpx, "AsyncClient", lambda **kwargs: Client())

    with pytest.raises(ValueError, match="repeated"):
        asyncio.run(
            request_guess_person_turn(
                "key",
                "model",
                [
                    {"role": "user", "content": "我已经想好一个人物，请开始提问。"},
                    {"role": "assistant", "content": FIRST_QUESTION},
                    {"role": "user", "content": "是"},
                ],
            )
        )
