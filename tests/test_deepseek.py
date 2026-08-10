import asyncio
from datetime import datetime

import nao_bot.deepseek as deepseek
from nao_bot.deepseek import (
    AIAnswer,
    ask_deepseek,
    extract_fraud_keywords,
    markdown_to_plain_text,
    parse_ai_response,
    parse_fraud_keyword_response,
    parse_reminder_tool_call,
    request_reminder_command,
)
from nao_bot.reminders import CHINA_TIMEZONE


def test_markdown_to_plain_text():
    markdown = """## 防骗建议

**不要转账**，先查看[官方网站](https://example.com)。

> 对方要求提供验证码。

- [x] 停止付款
- `联系银行`

```python
print("保留代码内容")
```

| 项目 | 风险 |
| --- | --- |
| 转账 | 高 |
"""

    result = markdown_to_plain_text(markdown)

    assert "防骗建议" in result
    assert "不要转账" in result
    assert "官方网站（https://example.com）" in result
    assert "对方要求提供验证码。" in result
    assert "• 已完成：停止付款" in result
    assert 'print("保留代码内容")' in result
    assert "项目 / 风险" in result
    assert "转账 / 高" in result
    assert "| --- |" not in result
    for marker in ("##", "**", "```", "[x]", "[官方网站]"):
        assert marker not in result


def test_plain_text_is_preserved():
    text = "先停止转账。\n然后通过官方电话核实。"
    assert markdown_to_plain_text(text) == text


def test_parse_ai_response_returns_plain_text_and_reaction_metadata():
    answer = parse_ai_response(
        '{"reply":"**恭喜你**，这次完成得很好！",'
        '"reaction":{"scene":"庆祝","context":"playful","confidence":0.92}}'
    )

    assert answer == AIAnswer(
        text="恭喜你，这次完成得很好！",
        reaction_scene="庆祝",
        reaction_context="playful",
        reaction_confidence=0.92,
    )


def test_parse_ai_response_falls_back_to_text_without_a_reaction():
    answer = parse_ai_response("普通纯文本回答")

    assert answer == AIAnswer(text="普通纯文本回答")


def test_parse_ai_response_accepts_json_with_no_matching_scene():
    answer = parse_ai_response(
        '{"reply":"这是一个严肃的技术说明。",'
        '"reaction":{"scene":null,"context":"serious","confidence":0}}'
    )

    assert answer == AIAnswer(text="这是一个严肃的技术说明。")


def test_parse_fraud_keyword_response_accepts_json_and_code_fences():
    assert parse_fraud_keyword_response('["论文代写", "包通过"]') == ["论文代写", "包通过"]
    assert parse_fraud_keyword_response('```json\n["刷单", "先垫付"]\n```') == ["刷单", "先垫付"]


def test_extract_fraud_keywords_uses_deterministic_json_request(monkeypatch):
    requests = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '["论文代写", "不过退款"]'}}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers, json):
            requests.append((url, headers, json))
            return Response()

    monkeypatch.setattr(deepseek.httpx, "AsyncClient", lambda **kwargs: Client())

    result = asyncio.run(extract_fraud_keywords("key", "model", "提供论文代写，不过退款"))

    assert result == ["论文代写", "不过退款"]
    assert requests[0][2]["temperature"] == 0
    assert requests[0][2]["max_tokens"] == 1200
    assert requests[0][2]["response_format"] == {"type": "json_object"}


def test_ask_deepseek_requests_reply_and_reaction_in_one_json_response(monkeypatch):
    requests = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"reply":"这也太好笑了！",'
                                '"reaction":{"scene":"好笑","context":"playful",'
                                '"confidence":0.88}}'
                            )
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

    monkeypatch.setattr(deepseek.httpx, "AsyncClient", lambda **kwargs: Client())

    answer = asyncio.run(ask_deepseek("key", "model", "讲个笑话"))

    assert answer.reaction_scene == "好笑"
    assert answer.reaction_context == "playful"
    assert requests[0][2]["response_format"] == {"type": "json_object"}
    assert "reaction" in requests[0][2]["messages"][0]["content"]
    assert "tools" not in requests[0][2]


def test_ask_deepseek_allows_admin_reminder_tool(monkeypatch):
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
                                    "function": {
                                        "name": "create_reminder",
                                        "arguments": (
                                            '{"remind_at":"2026-08-10 21:00",'
                                            '"content":"写donelist","repeat_days":1,'
                                            '"time_defaulted":false}'
                                        ),
                                    }
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

    monkeypatch.setattr(deepseek.httpx, "AsyncClient", lambda **kwargs: Client())
    now = datetime(2026, 8, 10, 16, 22, tzinfo=CHINA_TIMEZONE)

    command = asyncio.run(
        ask_deepseek("key", "model", "每天晚上九点提醒我写donelist", True, now)
    )

    assert command.repeat_days == 1
    assert requests[0][2]["tool_choice"] == "auto"
    assert requests[0][2]["tools"][0]["function"]["name"] == "create_reminder"


def test_parse_reminder_tool_call_accepts_daily_task():
    command = parse_reminder_tool_call(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "create_reminder",
                                    "arguments": (
                                        '{"remind_at":"2026-08-10 21:00",'
                                        '"content":"@夏末秋凉 写donelist","repeat_days":1}'
                                    ),
                                }
                            }
                        ]
                    }
                }
            ]
        },
        datetime(2026, 8, 10, 16, 22, tzinfo=CHINA_TIMEZONE),
    )

    assert command.remind_at == datetime(2026, 8, 10, 21, 0, tzinfo=CHINA_TIMEZONE)
    assert command.content == "@夏末秋凉 写donelist"
    assert command.repeat_days == 1


def test_request_reminder_command_uses_forced_deepseek_tool(monkeypatch):
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
                                    "function": {
                                        "name": "create_reminder",
                                        "arguments": (
                                            '{"remind_at":"2026-08-10 21:00",'
                                            '"content":"写donelist","repeat_days":1}'
                                        ),
                                    }
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

    monkeypatch.setattr(deepseek.httpx, "AsyncClient", lambda **kwargs: Client())
    now = datetime(2026, 8, 10, 16, 22, tzinfo=CHINA_TIMEZONE)

    command = asyncio.run(
        request_reminder_command("key", "model", "每天晚上九点写donelist", now)
    )

    assert command.repeat_days == 1
    payload = requests[0][2]
    assert payload["tool_choice"] == "auto"
    assert payload["tools"][0]["function"]["name"] == "create_reminder"
    assert "2026-08-10 16:22" in payload["messages"][0]["content"]
