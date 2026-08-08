import asyncio

from nao_bot.moderation import (
    FraudKeywordStore,
    ViolationStore,
    detect_fraud_text,
    detect_protected_notice,
    extract_fallback_keywords,
    filter_fraud_keywords,
    has_contact_card,
    kick_member_with_confirmation,
    text_from_segments,
)


def test_protected_notice_detection():
    assert detect_protected_notice("【重要通知】今晚八点开会") is True
    assert detect_protected_notice("紧急公告：请全体成员查看") is True
    assert detect_protected_notice("明天八点正常上课") is False


def test_fraud_text_detection():
    image_text = "招线上临时工，180左右一天，日结，在家寝室都可以，有兴趣加QQ群1234567890"
    academic_ad = (
        "985博士团队指导硕、博毕业论文写作，保证质量，包修改，包通过，按阶段付款。"
        "服务范围包括选题、开题、初稿、盲审修改和答辩指导，微信号：13800138000，承诺不过退款。"
    )
    assert detect_fraud_text(image_text) is not None
    assert detect_fraud_text("刷单做任务，先垫付后返现") is not None
    assert detect_fraud_text(academic_ad) is not None
    assert detect_fraud_text("周末一起打球，有兴趣的群里说一声") is None
    assert detect_fraud_text("我的毕业论文正在准备开题，导师让我先整理参考文献") is None


def test_kick_error_is_treated_as_success_when_member_is_gone():
    class Member:
        def __init__(self, user_id):
            self.user_id = user_id

    class Bot:
        async def kick_group_member(self, **kwargs):
            raise RuntimeError("API returned -500 after kick")

        async def get_group_member_list(self, **kwargs):
            return [Member(111), Member(222)]

    asyncio.run(kick_member_with_confirmation(Bot(), 123456789, 987654321))


def test_kick_error_is_raised_when_member_remains():
    class Member:
        user_id = 987654321

    class Bot:
        async def kick_group_member(self, **kwargs):
            raise RuntimeError("kick failed")

        async def get_group_member_list(self, **kwargs):
            return [Member()]

    try:
        asyncio.run(kick_member_with_confirmation(Bot(), 123456789, 987654321))
    except RuntimeError as error:
        assert str(error) == "kick failed"
    else:
        raise AssertionError("kick failure should be raised when the member is still present")


def test_contact_card_detection():
    assert has_contact_card(
        [
            {
                "type": "light_app",
                "data": {
                    "app_name": "com.tencent.contact.lua",
                    "json_payload": '{"app":"com.tencent.contact.lua","view":"contact"}',
                },
            }
        ]
    )
    assert has_contact_card(
        [
            {
                "type": "light_app",
                "data": {
                    "json_payload": '{"app":"com.tencent.troopsharecard"}',
                },
            }
        ]
    )
    assert has_contact_card([{"type": "text", "data": {"text": "推荐一本书"}}]) is False


def test_violation_store_kick_threshold_persists(tmp_path):
    path = tmp_path / "moderation.json"
    store = ViolationStore(path)

    assert store.add(123456789, 123456, "疑似诈骗广告") == 1
    assert store.add(123456789, 123456, "发送QQ名片") == 2
    assert store.add(123456789, 123456, "冒充重要通知") == 3
    assert ViolationStore(path).get_count(123456789, 123456) == 3

    assert store.clear(123456789, 123456) is True
    assert ViolationStore(path).get_count(123456789, 123456) == 0


def test_fraud_keyword_store_persists_matches_and_deletes(tmp_path):
    path = tmp_path / "fraud_keywords.json"
    store = FraudKeywordStore(path)

    assert store.add_many(["论文代写", "不过退款", "论文代写"]) == ["论文代写", "不过退款"]
    assert FraudKeywordStore(path).terms() == ["论文代写", "不过退款"]
    assert store.match("提供论 文、代 写服务") == "论文代写"
    assert store.match("正常讨论毕业论文") is None
    assert store.delete("论文代写") is True
    assert store.delete("论文代写") is False
    assert FraudKeywordStore(path).terms() == ["不过退款"]


def test_filter_fraud_keywords_rejects_generic_and_hallucinated_terms():
    source = "提供毕业论文写作和盲审修改，保证包通过，可添加微信号咨询。"

    assert filter_fraud_keywords(
        source,
        ["论文", "论文写作", "盲审修改", "包通过", "微信号", "不存在的词", "论文写作"],
    ) == ["论文写作", "盲审修改", "包通过"]


def test_fallback_keyword_extraction_is_high_signal_and_deterministic():
    source = "毕业论文写作，盲审修改，包通过，不过退款，微信号：13800138000"
    fallback = extract_fallback_keywords(source)

    assert fallback == ["毕业论文写作", "盲审修改", "包通过", "不过退款", "微信号13800138000"]
    assert filter_fraud_keywords(source, ["13800138000", *fallback]) == fallback


def test_text_from_segments_reads_replied_plain_text():
    segments = [
        {"type": "text", "data": {"text": "论文代写"}},
        {"type": "mention", "data": {"user_id": 123}},
        {"type": "text", "data": {"text": "，包通过"}},
    ]

    assert text_from_segments(segments) == "论文代写，包通过"
