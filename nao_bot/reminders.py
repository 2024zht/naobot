import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal

from .rules import ai_question, command_argument


CHINA_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
MAX_REMINDER_CONTENT_LENGTH = 500
MAX_PENDING_REMINDERS_PER_GROUP = 50
REMINDER_TIME_FORMAT = "%Y-%m-%d %H:%M"
ADD_REMINDER_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(.+)$", re.DOTALL)
NATURAL_REMINDER_PREFIXES = ("定时任务", "定时提醒")
NATURAL_DATE_RE = re.compile(
    r"(?P<relative>今天|明天|后天|大后天|本周[一二三四五六日天]|下周[一二三四五六日天])"
    r"|(?P<full_year>\d{4})年(?P<full_month>\d{1,2})月(?P<full_day>\d{1,2})日?"
    r"|(?P<dashed_year>\d{4})[-/]?(?P<dashed_month>\d{1,2})[-/](?P<dashed_day>\d{1,2})"
    r"|(?P<month>\d{1,2})月(?P<day>\d{1,2})日?"
)
NATURAL_TIME_RE = re.compile(
    r"(?P<period>凌晨|早上|上午|中午|下午|晚上)?\s*"
    r"(?P<hour>\d{1,2})"
    r"(?:\s*(?::|：)\s*(?P<colon_minute>\d{1,2})|点(?:(?P<half>半)|\s*(?P<point_minute>\d{1,2})分?)?)"
)


@dataclass(frozen=True)
class ReminderCommand:
    action: Literal["add", "list", "cancel"]
    remind_at: datetime | None = None
    content: str = ""
    reminder_id: int | None = None
    time_defaulted: bool = False
    repeat_days: int = 0


@dataclass(frozen=True)
class Reminder:
    id: int
    group_id: int
    creator_id: int
    remind_at: datetime
    content: str
    repeat_days: int = 0


def reminder_command_text(text: str, is_tome: bool) -> str | None:
    normalized = ai_question(text, is_tome)
    if normalized is None:
        return None
    stripped = normalized.strip()
    if (
        stripped == "定时列表"
        or stripped.startswith(NATURAL_REMINDER_PREFIXES)
        or command_argument(stripped, "定时") is not None
        or command_argument(stripped, "取消定时") is not None
    ):
        return stripped
    return None


def _normalize_now(now: datetime | None) -> datetime:
    current_time = now or datetime.now(CHINA_TIMEZONE)
    if current_time.tzinfo is None:
        return current_time.replace(tzinfo=CHINA_TIMEZONE)
    return current_time.astimezone(CHINA_TIMEZONE)


def _parse_natural_date(match: re.Match[str], current_date: date) -> date:
    relative = match.group("relative")
    if relative in {"今天", "明天", "后天", "大后天"}:
        offset = {"今天": 0, "明天": 1, "后天": 2, "大后天": 3}[relative]
        return current_date + timedelta(days=offset)

    weekday = "一二三四五六日天".index(relative[-1])
    if weekday > 6:
        weekday = 6
    monday = current_date - timedelta(days=current_date.weekday())
    if relative.startswith("下周"):
        monday += timedelta(days=7)
    target = monday + timedelta(days=weekday)
    if relative.startswith("本周") and target < current_date:
        target += timedelta(days=7)
    return target


def _natural_date(match: re.Match[str], current_date: date) -> date:
    if match.group("relative"):
        return _parse_natural_date(match, current_date)
    if match.group("full_year"):
        year = int(match.group("full_year"))
        month = int(match.group("full_month"))
        day = int(match.group("full_day"))
    elif match.group("dashed_year"):
        year = int(match.group("dashed_year"))
        month = int(match.group("dashed_month"))
        day = int(match.group("dashed_day"))
    else:
        year = current_date.year
        month = int(match.group("month"))
        day = int(match.group("day"))
    try:
        return date(year, month, day)
    except ValueError as error:
        raise ValueError("提醒日期无效") from error


def _natural_time(match: re.Match[str]) -> time:
    hour = int(match.group("hour"))
    minute_text = match.group("colon_minute") or match.group("point_minute")
    minute = 30 if match.group("half") else int(minute_text or 0)
    period = match.group("period")
    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    if period == "凌晨" and hour == 12:
        hour = 0
    if period in {"凌晨", "早上", "上午"} and hour > 12:
        raise ValueError("凌晨或上午的小时数必须在 0 到 12 之间")
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("提醒时间无效")
    return time(hour, minute)


def _reminder_content(value: str, spans: list[tuple[int, int]]) -> str:
    content = value
    for start, end in sorted(spans, reverse=True):
        content = f"{content[:start]} {content[end:]}"
    content = re.sub(r"^[\s,，:：;；-]+", "", content)
    content = re.sub(r"^(?:提醒(?:我)?|叫我|记得)\s*", "", content)
    return content.strip()


def _parse_natural_add(argument: str, current_time: datetime) -> ReminderCommand:
    date_match = NATURAL_DATE_RE.search(argument)
    time_match = NATURAL_TIME_RE.search(argument)
    if date_match is None and time_match is None:
        raise ValueError("用法：@nao 定时任务，本周五 21:00 提醒内容")

    target_date = (
        _natural_date(date_match, current_time.date()) if date_match else current_time.date()
    )
    if time_match:
        target_time = _natural_time(time_match)
        time_defaulted = False
    else:
        target_time = time(9, 0)
        time_defaulted = True
    remind_at = datetime.combine(target_date, target_time, tzinfo=CHINA_TIMEZONE)
    if date_match is None and remind_at <= current_time:
        remind_at += timedelta(days=1)
    spans: list[tuple[int, int]] = []
    if date_match:
        spans.append((date_match.start(), date_match.end()))
    if time_match:
        spans.append((time_match.start(), time_match.end()))
    content = _reminder_content(argument, spans)
    if not content:
        raise ValueError("请写上提醒内容，例如：定时任务，明天 09:00 提醒开会")
    return _validate_reminder(remind_at, content, current_time, time_defaulted)


def _validate_reminder(
    remind_at: datetime,
    content: str,
    current_time: datetime,
    time_defaulted: bool = False,
    repeat_days: int = 0,
) -> ReminderCommand:
    if remind_at <= current_time:
        raise ValueError("提醒时间必须晚于当前时间")
    if len(content) > MAX_REMINDER_CONTENT_LENGTH:
        raise ValueError(f"提醒内容不能超过 {MAX_REMINDER_CONTENT_LENGTH} 个字符")
    if repeat_days not in {0, 1, 7}:
        raise ValueError("重复周期只支持一次、每天或每周")
    return ReminderCommand(
        action="add",
        remind_at=remind_at,
        content=content,
        time_defaulted=time_defaulted,
        repeat_days=repeat_days,
    )


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
        add_argument = next(
            (
                re.sub(r"^[\s,，:：;；]+", "", stripped[len(prefix) :])
                for prefix in NATURAL_REMINDER_PREFIXES
                if stripped.startswith(prefix)
            ),
            None,
        )
        if add_argument is None:
            return None

        return _parse_natural_add(add_argument, _normalize_now(now))

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

    return _validate_reminder(remind_at, content.strip(), _normalize_now(now))


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
                    content TEXT NOT NULL,
                    repeat_days INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(reminders)").fetchall()
            }
            if "repeat_days" not in columns:
                connection.execute(
                    "ALTER TABLE reminders ADD COLUMN repeat_days INTEGER NOT NULL DEFAULT 0"
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
            repeat_days=row["repeat_days"],
        )

    def add(
        self,
        group_id: int,
        creator_id: int,
        remind_at: datetime,
        content: str,
        repeat_days: int = 0,
    ) -> Reminder:
        if repeat_days not in {0, 1, 7}:
            raise ValueError("重复周期只支持一次、每天或每周")
        with self._connect() as connection:
            pending_count = connection.execute(
                "SELECT COUNT(*) FROM reminders WHERE group_id = ?",
                (group_id,),
            ).fetchone()[0]
            if pending_count >= MAX_PENDING_REMINDERS_PER_GROUP:
                raise ValueError(f"每个群最多保留 {MAX_PENDING_REMINDERS_PER_GROUP} 条定时提醒")
            cursor = connection.execute(
                "INSERT INTO reminders "
                "(group_id, creator_id, remind_at, content, repeat_days) VALUES (?, ?, ?, ?, ?)",
                (group_id, creator_id, int(remind_at.timestamp()), content, repeat_days),
            )
            reminder_id = cursor.lastrowid
        return Reminder(reminder_id, group_id, creator_id, remind_at, content, repeat_days)

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

    def complete(self, reminder_id: int, delivered_at: datetime | None = None) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT remind_at, repeat_days FROM reminders WHERE id = ?",
                (reminder_id,),
            ).fetchone()
            if row is None:
                return False
            if row["repeat_days"]:
                next_time = datetime.fromtimestamp(row["remind_at"], CHINA_TIMEZONE)
                current_time = _normalize_now(delivered_at)
                while next_time <= current_time:
                    next_time += timedelta(days=row["repeat_days"])
                cursor = connection.execute(
                    "UPDATE reminders SET remind_at = ? WHERE id = ?",
                    (int(next_time.timestamp()), reminder_id),
                )
            else:
                cursor = connection.execute(
                    "DELETE FROM reminders WHERE id = ?",
                    (reminder_id,),
                )
        return cursor.rowcount > 0


def format_reminder_time(value: datetime) -> str:
    return value.astimezone(CHINA_TIMEZONE).strftime(REMINDER_TIME_FORMAT)
