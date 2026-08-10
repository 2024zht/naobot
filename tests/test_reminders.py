import sqlite3
from datetime import datetime, timedelta

import pytest

from nao_bot.reminders import (
    CHINA_TIMEZONE,
    MAX_PENDING_REMINDERS_PER_GROUP,
    ReminderStore,
    parse_reminder_command,
    reminder_command_text,
)


NOW = datetime(2026, 8, 10, 20, 0, tzinfo=CHINA_TIMEZONE)
SUNDAY_NIGHT = datetime(2026, 8, 9, 20, 0, tzinfo=CHINA_TIMEZONE)


def test_parse_add_reminder_command():
    command = parse_reminder_command("定时 2026-08-10 21:30 提交作业", now=NOW)

    assert command is not None
    assert command.action == "add"
    assert command.remind_at == datetime(2026, 8, 10, 21, 30, tzinfo=CHINA_TIMEZONE)
    assert command.content == "提交作业"


def test_parse_natural_weekday_reminder_defaults_to_nine_am():
    command = parse_reminder_command("定时任务，本周五提醒部署网站", now=SUNDAY_NIGHT)

    assert command is not None
    assert command.remind_at == datetime(2026, 8, 14, 9, 0, tzinfo=CHINA_TIMEZONE)
    assert command.content == "部署网站"
    assert command.time_defaulted is True


def test_parse_natural_time_and_next_weekday():
    command = parse_reminder_command("定时提醒，下周五晚上9点提醒我检查服务器", now=NOW)

    assert command is not None
    assert command.remind_at == datetime(2026, 8, 21, 21, 0, tzinfo=CHINA_TIMEZONE)
    assert command.content == "检查服务器"
    assert command.time_defaulted is False


def test_recurring_reminder_advances_after_delivery(tmp_path):
    store = ReminderStore(tmp_path / "reminders.sqlite3")
    reminder = store.add(100, 200, NOW + timedelta(hours=1), "写 donelist", repeat_days=1)

    assert reminder.repeat_days == 1
    assert store.complete(reminder.id, NOW + timedelta(hours=1)) is True
    assert store.list_pending(100)[0].remind_at == NOW + timedelta(days=1, hours=1)


def test_reminder_store_migrates_existing_database(tmp_path):
    path = tmp_path / "reminders.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                creator_id INTEGER NOT NULL,
                remind_at INTEGER NOT NULL,
                content TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO reminders (group_id, creator_id, remind_at, content) "
            "VALUES (?, ?, ?, ?)",
            (100, 200, int((NOW + timedelta(hours=1)).timestamp()), "旧任务"),
        )

    reminders = ReminderStore(path).list_pending(100)

    assert len(reminders) == 1
    assert reminders[0].content == "旧任务"
    assert reminders[0].repeat_days == 0


@pytest.mark.parametrize(
    ("text", "is_tome", "expected"),
    [
        ("定时列表", True, "定时列表"),
        ("@nao 定时列表", False, "定时列表"),
        ("@NAO 定时任务，本周五 09:00 提醒内容", False, "定时任务，本周五 09:00 提醒内容"),
        ("定时列表", False, None),
        ("@nao 普通问题", False, None),
    ],
)
def test_reminder_command_text_supports_plain_nao_prefix(text, is_tome, expected):
    assert reminder_command_text(text, is_tome) == expected


@pytest.mark.parametrize(
    ("text", "action", "reminder_id"),
    [
        ("定时列表", "list", None),
        ("取消定时 12", "cancel", 12),
        ("普通消息", None, None),
    ],
)
def test_parse_reminder_management_commands(text, action, reminder_id):
    command = parse_reminder_command(text, now=NOW)

    if action is None:
        assert command is None
        return
    assert command is not None
    assert command.action == action
    assert command.reminder_id == reminder_id


@pytest.mark.parametrize(
    "text",
    [
        "定时",
        "定时 明天 21:00 提交作业",
        "定时任务，提醒部署网站",
        "定时 2026-08-10 19:59 已经过期",
        "定时 2026-02-30 21:00 不存在的日期",
        "取消定时",
        "取消定时 abc",
        "取消定时 0",
    ],
)
def test_invalid_reminder_commands(text):
    with pytest.raises(ValueError):
        parse_reminder_command(text, now=NOW)


def test_reminder_content_length_limit():
    with pytest.raises(ValueError):
        parse_reminder_command(f"定时 2026-08-10 21:00 {'提' * 501}", now=NOW)


def test_reminder_store_persists_lists_and_completes(tmp_path):
    path = tmp_path / "reminders.sqlite3"
    store = ReminderStore(path)
    first = store.add(100, 200, NOW + timedelta(hours=1), "第一条")
    second = store.add(100, 201, NOW + timedelta(hours=2), "第二条")
    other_group = store.add(101, 202, NOW + timedelta(minutes=30), "其他群")

    reopened = ReminderStore(path)
    assert reopened.list_pending(100) == [first, second]
    assert reopened.due(NOW + timedelta(hours=1, minutes=30)) == [other_group, first]

    reopened.complete(first.id)
    assert reopened.list_pending(100) == [second]
    assert reopened.due(NOW + timedelta(hours=1, minutes=30)) == [other_group]


def test_reminder_store_cancellation_is_scoped_to_group(tmp_path):
    store = ReminderStore(tmp_path / "reminders.sqlite3")
    reminder = store.add(100, 200, NOW + timedelta(hours=1), "开会")

    assert store.cancel(101, reminder.id) is False
    assert store.cancel(100, reminder.id) is True
    assert store.cancel(100, reminder.id) is False


def test_reminder_store_limits_pending_tasks_per_group(tmp_path):
    store = ReminderStore(tmp_path / "reminders.sqlite3")
    for index in range(MAX_PENDING_REMINDERS_PER_GROUP):
        store.add(100, 200, NOW + timedelta(minutes=index + 1), f"任务 {index}")

    with pytest.raises(ValueError):
        store.add(100, 200, NOW + timedelta(days=1), "超出限制")
