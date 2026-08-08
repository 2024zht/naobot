import asyncio

import nao_bot.deepseek as deepseek
from nao_bot.deepseek import extract_fraud_keywords, markdown_to_plain_text, parse_fraud_keyword_response


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
