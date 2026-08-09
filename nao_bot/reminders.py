import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from .rules import command_argument


CHINA_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
MAX_REMINDER_CONTENT_LENGTH = 500
MAX_PENDING_REMINDERS_PER_GROUP = 50
REMINDER_TIME_FORMAT = "%Y-%m-%d %H:%M"
ADD_REMINDER_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(.+)$", re.DOTALL)


@dataclass(frozen=True)
class ReminderCommand:
    action: Literal["add", "list", "cancel"]
    remind_at: datetime | None = None
    content: str = ""
    reminder_id: int | None = None


@dataclass(frozen=True)
class Reminder:
    id: int
    group_id: int
    creator_id: int
    remind_at: datetime
    content: str


def parse_reminder_command(text: str, now: datetime | None = None) -> ReminderCommand | None:
    stripped = text.strip()
    if stripped == "定时列表":
        return ReminderCommand(action="list")

    cancel_argument = command_argument(stripped, "取消定时")
    if cancel_argument is not None:
        if not cancel_argument.isdecimal() or int(cancel_argument) <= 0:
            raise ValueError("用法：@nao 取消定时 任务编号")
        return ReminderCommand(action="cancel", reminder_id=int(cancel_argument))

    add_argument = command_argument(stripped, "定时")
    if add_argument is None:
        return None
    match = ADD_REMINDER_RE.fullmatch(add_argument)
    if match is None:
        raise ValueError("用法：@nao 定时 YYYY-MM-DD HH:MM 提醒内容")

    date_text, time_text, content = match.groups()
    try:
        remind_at = datetime.strptime(
            f"{date_text} {time_text}",
            REMINDER_TIME_FORMAT,
        ).replace(tzinfo=CHINA_TIMEZONE)
    except ValueError as error:
        raise ValueError("提醒日期或时间无效，请使用 YYYY-MM-DD HH:MM") from error

    current_time = now or datetime.now(CHINA_TIMEZONE)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=CHINA_TIMEZONE)
    if remind_at <= current_time.astimezone(CHINA_TIMEZONE):
        raise ValueError("提醒时间必须晚于当前时间")
    content = content.strip()
    if len(content) > MAX_REMINDER_CONTENT_LENGTH:
        raise ValueError(f"提醒内容不能超过 {MAX_REMINDER_CONTENT_LENGTH} 个字符")
    return ReminderCommand(action="add", remind_at=remind_at, content=content)


class ReminderStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    creator_id INTEGER NOT NULL,
                    remind_at INTEGER NOT NULL,
                    content TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS reminders_due_idx ON reminders (remind_at, id)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Reminder:
        return Reminder(
            id=row["id"],
            group_id=row["group_id"],
            creator_id=row["creator_id"],
            remind_at=datetime.fromtimestamp(row["remind_at"], CHINA_TIMEZONE),
            content=row["content"],
        )

    def add(
        self,
        group_id: int,
        creator_id: int,
        remind_at: datetime,
        content: str,
    ) -> Reminder:
        with self._connect() as connection:
            pending_count = connection.execute(
                "SELECT COUNT(*) FROM reminders WHERE group_id = ?",
                (group_id,),
            ).fetchone()[0]
            if pending_count >= MAX_PENDING_REMINDERS_PER_GROUP:
                raise ValueError(f"每个群最多保留 {MAX_PENDING_REMINDERS_PER_GROUP} 条定时提醒")
            cursor = connection.execute(
                "INSERT INTO reminders (group_id, creator_id, remind_at, content) VALUES (?, ?, ?, ?)",
                (group_id, creator_id, int(remind_at.timestamp()), content),
            )
            reminder_id = cursor.lastrowid
        return Reminder(reminder_id, group_id, creator_id, remind_at, content)

    def list_pending(self, group_id: int) -> list[Reminder]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM reminders WHERE group_id = ? ORDER BY remind_at, id",
                (group_id,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def cancel(self, group_id: int, reminder_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM reminders WHERE group_id = ? AND id = ?",
                (group_id, reminder_id),
            )
        return cursor.rowcount > 0

    def due(self, now: datetime, limit: int = 50) -> list[Reminder]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM reminders WHERE remind_at <= ? ORDER BY remind_at, id LIMIT ?",
                (int(now.timestamp()), limit),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def complete(self, reminder_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM reminders WHERE id = ?",
                (reminder_id,),
            )
        return cursor.rowcount > 0


def format_reminder_time(value: datetime) -> str:
    return value.astimezone(CHINA_TIMEZONE).strftime(REMINDER_TIME_FORMAT)
