import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


MAX_KEYWORDS = 50
MAX_TRIGGER_LENGTH = 30
MAX_REPLY_LENGTH = 500


@dataclass(frozen=True)
class KeywordCommand:
    action: Literal["add", "delete", "list"]
    trigger: str = ""
    reply: str = ""


def parse_keyword_command(text: str) -> KeywordCommand | None:
    stripped = text.strip()
    if stripped == "关键词 列表":
        return KeywordCommand("list")
    if stripped == "关键词":
        raise ValueError("用法：@nao 关键词 添加 触发词=回复，或 @nao 关键词 删除 触发词，或 @nao 关键词 列表")

    add_prefix = "关键词 添加 "
    if stripped.startswith(add_prefix):
        content = stripped[len(add_prefix) :].replace("＝", "=", 1)
        if "=" not in content:
            raise ValueError("添加格式：@nao 关键词 添加 触发词=回复内容")
        trigger, reply = (part.strip() for part in content.split("=", 1))
        _validate_keyword(trigger, reply)
        return KeywordCommand("add", trigger, reply)

    delete_prefix = "关键词 删除 "
    if stripped.startswith(delete_prefix):
        trigger = stripped[len(delete_prefix) :].strip()
        if not trigger:
            raise ValueError("删除格式：@nao 关键词 删除 触发词")
        return KeywordCommand("delete", trigger)

    if stripped.startswith("关键词"):
        raise ValueError("用法：@nao 关键词 添加 触发词=回复，或 @nao 关键词 删除 触发词，或 @nao 关键词 列表")
    return None


def _validate_keyword(trigger: str, reply: str) -> None:
    if not trigger or not reply:
        raise ValueError("触发词和回复内容都不能为空")
    if trigger.startswith("/"):
        raise ValueError("触发词不能以 / 开头")
    if "\n" in trigger or len(trigger) > MAX_TRIGGER_LENGTH:
        raise ValueError(f"触发词不能换行，且最多 {MAX_TRIGGER_LENGTH} 个字符")
    if len(reply) > MAX_REPLY_LENGTH:
        raise ValueError(f"回复内容最多 {MAX_REPLY_LENGTH} 个字符")


class KeywordStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._items = self._load()

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"无法读取关键词库：{self.path}") from error
        if not isinstance(data, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in data.items()
        ):
            raise RuntimeError(f"关键词库格式无效：{self.path}")
        return data

    def get(self, trigger: str) -> str | None:
        return self._items.get(trigger)

    def add(self, trigger: str, reply: str) -> bool:
        created = trigger not in self._items
        if created and len(self._items) >= MAX_KEYWORDS:
            raise ValueError(f"关键词库最多保存 {MAX_KEYWORDS} 条")
        self._items[trigger] = reply
        self._save()
        return created

    def delete(self, trigger: str) -> bool:
        if trigger not in self._items:
            return False
        del self._items[trigger]
        self._save()
        return True

    def triggers(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))

    def _save(self) -> None:
        temp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temp_path.write_text(
            json.dumps(self._items, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, self.path)
