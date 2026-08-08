import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


KICK_THRESHOLD = 3

PROTECTED_NOTICE_TERMS = (
    "重要通知",
    "紧急通知",
    "官方通知",
    "群通知",
    "管理员通知",
    "全体通知",
    "重要公告",
    "紧急公告",
    "官方公告",
    "群公告",
)

CONTACT_CARD_APPS = (
    "com.tencent.contact.lua",
    "com.tencent.troopsharecard",
    "com.tencent.mobileqq.cardshare",
)

GENERIC_FRAUD_KEYWORDS = frozenset(
    {
        "qq",
        "qq号",
        "二维码",
        "兼职",
        "付款",
        "写作",
        "名片",
        "咨询",
        "广告",
        "微信",
        "微信号",
        "服务",
        "毕业论文",
        "群聊",
        "联系",
        "论文",
        "赚钱",
        "转账",
        "通知",
        "重要通知",
    }
)

FALLBACK_FRAUD_PHRASES = (
    "毕业论文写作",
    "论文代写",
    "论文写作",
    "论文辅导",
    "盲审修改",
    "答辩修改",
    "包通过",
    "保证通过",
    "不过退款",
    "不通过退款",
    "刷单",
    "好评返现",
    "先垫付",
    "垫付后返",
    "解冻金",
    "安全账户",
    "代收验证码",
    "高佣",
    "高回报",
    "日结",
    "做任务",
)

CONTACT_KEYWORD_PATTERN = re.compile(
    r"(?:微信|vx|v信|qq)(?:号|群)?\s*[:：+]?\s*[a-z0-9_-]{5,20}",
    re.IGNORECASE,
)

DIRECT_FRAUD_PATTERN = re.compile(
    r"刷单|好评返现|先垫付|垫付后返|解冻金|保证金|安全账户|"
    r"共享屏幕.{0,12}(?:转账|验证码)|代收.{0,8}验证码|"
    r"投资.{0,8}(?:稳赚|保本)|(?:稳赚|保本).{0,8}投资|裸聊"
)

FRAUD_SIGNALS = (
    (
        "论文代写服务",
        re.compile(
            r"论文(?:代写|写作|辅导|指导|修改)|"
            r"(?:硕|博)(?:士)?(?:毕业|学位)?论文|盲审修改|答辩修改"
        ),
    ),
    (
        "包过承诺",
        re.compile(r"包通过|保证通过|不过退款|不通过退款|包修改|导师满意"),
    ),
    (
        "兼职招聘",
        re.compile(
            r"招.{0,10}(?:兼职|临时工|小时工|代理)|"
            r"(?:线上|网络|居家|在家|寝室).{0,10}(?:兼职|临时工|赚钱|副业)|"
            r"兼职|日结|做任务"
        ),
    ),
    (
        "收益诱导",
        re.compile(
            r"日入|日赚|高佣|高回报|返利|佣金|躺赚|无门槛|"
            r"\d{2,6}(?:元)?(?:左右)?(?:一天|每天|日薪|每单)"
        ),
    ),
    (
        "站外引流",
        re.compile(
            r"加.{0,8}(?:微信|微|vx|v信|qq|q群)|"
            r"(?:微信|vx|v信|qq)号?[:：+]?\d{5,12}|"
            r"(?:qq|q)群[:：+]?\d{5,12}|扫码|私聊.{0,8}(?:联系|咨询|领取)"
        ),
    ),
    (
        "资金诱导",
        re.compile(r"转账|汇款|付款|定金|费用|充值|入金|提现|银行卡|验证码|借款|贷款|征信"),
    ),
    (
        "可疑链接",
        re.compile(r"https?://|www\.|(?:点击|打开).{0,8}(?:链接|网址)"),
    ),
)


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _normalize_keyword_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[\W_]+", "", normalized)


def _clean_keyword(keyword: str) -> str:
    return unicodedata.normalize("NFKC", keyword).strip(" \t\r\n,，。.!！?？:：;；、'\"“”‘’()（）[]【】")


def filter_fraud_keywords(source_text: str, candidates: Iterable[str]) -> list[str]:
    normalized_source = _normalize_keyword_text(source_text)
    valid: list[tuple[str, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        keyword = _clean_keyword(candidate)
        normalized = _normalize_keyword_text(keyword)
        if (
            not 2 <= len(normalized) <= 32
            or normalized in GENERIC_FRAUD_KEYWORDS
            or normalized not in normalized_source
            or normalized in seen
        ):
            continue
        seen.add(normalized)
        valid.append((keyword, normalized))

    return [
        keyword
        for keyword, normalized in valid
        if not any(normalized != other and normalized in other for _, other in valid)
    ][:8]


def extract_fallback_keywords(source_text: str) -> list[str]:
    candidates = [phrase for phrase in FALLBACK_FRAUD_PHRASES if _normalize_keyword_text(phrase) in _normalize_keyword_text(source_text)]
    candidates.extend(re.sub(r"[\s:：+]+", "", match.group(0)) for match in CONTACT_KEYWORD_PATTERN.finditer(source_text))
    return filter_fraud_keywords(source_text, candidates)


def detect_protected_notice(text: str) -> bool:
    compact = _compact_text(text)
    return any(term in compact for term in PROTECTED_NOTICE_TERMS)


def detect_fraud_text(text: str) -> str | None:
    compact = _compact_text(text)
    if not compact:
        return None
    if DIRECT_FRAUD_PATTERN.search(compact):
        return "高风险诈骗话术"

    matched = [name for name, pattern in FRAUD_SIGNALS if pattern.search(compact)]
    if len(matched) >= 2:
        return f"疑似诈骗广告（{'、'.join(matched)}）"
    return None


def _segment_parts(segment: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(segment, dict):
        return str(segment.get("type", "")), segment.get("data", {})
    return str(getattr(segment, "type", "")), getattr(segment, "data", {})


def has_contact_card(segments: Iterable[Any]) -> bool:
    for segment in segments:
        segment_type, data = _segment_parts(segment)
        if segment_type == "light_app":
            values = (data.get("app_name", ""), data.get("json_payload", ""))
        elif segment_type == "xml":
            values = (data.get("xml_payload", ""),)
        else:
            continue

        payload = " ".join(str(value).lower() for value in values)
        if any(app in payload for app in CONTACT_CARD_APPS):
            return True
    return False


def text_from_segments(segments: Iterable[Any]) -> str:
    parts: list[str] = []
    for segment in segments:
        segment_type, data = _segment_parts(segment)
        if segment_type == "text" and data.get("text") is not None:
            parts.append(str(data["text"]))
    return "".join(parts).strip()


async def kick_member_with_confirmation(bot: Any, group_id: int, user_id: int) -> None:
    try:
        await bot.kick_group_member(group_id=group_id, user_id=user_id)
        return
    except Exception as kick_error:
        try:
            members = await bot.get_group_member_list(group_id=group_id, no_cache=True)
        except Exception:
            raise kick_error

        for member in members:
            member_id = member.get("user_id") if isinstance(member, dict) else getattr(member, "user_id", None)
            if int(member_id or 0) == user_id:
                raise kick_error


class ViolationStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._items = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"无法读取反诈违规记录：{self.path}") from error
        if not isinstance(data, dict) or not all(isinstance(value, dict) for value in data.values()):
            raise RuntimeError(f"反诈违规记录格式无效：{self.path}")
        return data

    @staticmethod
    def _key(group_id: int, user_id: int) -> str:
        return f"{group_id}:{user_id}"

    def add(self, group_id: int, user_id: int, reason: str) -> int:
        key = self._key(group_id, user_id)
        item = self._items.get(key, {})
        count = int(item.get("count", 0)) + 1
        self._items[key] = {
            "count": count,
            "last_reason": reason,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save()
        return count

    def get_count(self, group_id: int, user_id: int) -> int:
        return int(self._items.get(self._key(group_id, user_id), {}).get("count", 0))

    def get_last_reason(self, group_id: int, user_id: int) -> str | None:
        value = self._items.get(self._key(group_id, user_id), {}).get("last_reason")
        return str(value) if value else None

    def clear(self, group_id: int, user_id: int) -> bool:
        key = self._key(group_id, user_id)
        if key not in self._items:
            return False
        del self._items[key]
        self._save()
        return True

    def _save(self) -> None:
        temp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temp_path.write_text(
            json.dumps(self._items, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, self.path)


class FraudKeywordStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._terms = self._load()

    def _load(self) -> list[str]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"无法读取反诈违规词：{self.path}") from error
        if not isinstance(data, list) or not all(isinstance(item, str) and item.strip() for item in data):
            raise RuntimeError(f"反诈违规词格式无效：{self.path}")
        return list(dict.fromkeys(_clean_keyword(item) for item in data))

    def terms(self) -> list[str]:
        return list(self._terms)

    def add_many(self, terms: Iterable[str]) -> list[str]:
        normalized_existing = {_normalize_keyword_text(term) for term in self._terms}
        added: list[str] = []
        for term in terms:
            keyword = _clean_keyword(term)
            normalized = _normalize_keyword_text(keyword)
            if not 2 <= len(normalized) <= 32 or normalized in normalized_existing:
                continue
            self._terms.append(keyword)
            normalized_existing.add(normalized)
            added.append(keyword)
        if added:
            self._save()
        return added

    def delete(self, term: str) -> bool:
        normalized = _normalize_keyword_text(term)
        for index, existing in enumerate(self._terms):
            if _normalize_keyword_text(existing) == normalized:
                del self._terms[index]
                self._save()
                return True
        return False

    def match(self, text: str) -> str | None:
        normalized_text = _normalize_keyword_text(text)
        if not normalized_text:
            return None
        for term in sorted(self._terms, key=lambda item: len(_normalize_keyword_text(item)), reverse=True):
            if _normalize_keyword_text(term) in normalized_text:
                return term
        return None

    def _save(self) -> None:
        temp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temp_path.write_text(
            json.dumps(self._terms, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, self.path)
