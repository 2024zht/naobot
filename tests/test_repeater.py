from nao_bot.repeater import MAX_REPEAT_STATES, RepeatTracker, repeatable_message_text


def test_three_distinct_members_trigger_one_repeat():
    tracker = RepeatTracker()

    assert tracker.record(1, 101, "收到", now=0) is False
    assert tracker.record(1, 102, "收到", now=10) is False
    assert tracker.record(1, 103, "收到", now=20) is True
    assert tracker.record(1, 104, "收到", now=21) is False
    assert tracker.generation(1) == 1


def test_same_member_does_not_increase_repeat_count():
    tracker = RepeatTracker()

    assert tracker.record(1, 101, "收到", now=0) is False
    assert tracker.record(1, 101, "收到", now=5) is False
    assert tracker.record(1, 101, "收到", now=10) is False
    assert tracker.record(1, 102, "收到", now=15) is False


def test_repeat_tracking_is_isolated_by_group_and_exact_text():
    tracker = RepeatTracker()

    assert tracker.record(1, 101, "收到", now=0) is False
    assert tracker.record(1, 102, "收到", now=1) is False
    assert tracker.record(2, 201, "收到", now=2) is False
    assert tracker.record(2, 202, "收到", now=3) is False
    assert tracker.record(1, 103, "收 到", now=4) is False
    assert tracker.record(1, 104, "收到", now=5) is True
    assert tracker.record(2, 203, "收到", now=6) is True


def test_repeat_window_expires_members_and_allows_a_new_round_after_silence():
    tracker = RepeatTracker()

    assert tracker.record(1, 101, "收到", now=0) is False
    assert tracker.record(1, 102, "收到", now=20) is False
    assert tracker.record(1, 103, "收到", now=31) is False
    assert tracker.record(1, 104, "收到", now=32) is True
    assert tracker.record(1, 105, "收到", now=33) is False

    assert tracker.record(1, 201, "收到", now=64) is False
    assert tracker.record(1, 202, "收到", now=65) is False
    assert tracker.record(1, 203, "收到", now=66) is True
    assert tracker.generation(1) == 2


def test_repeatable_message_only_accepts_ordinary_plain_text():
    assert repeatable_message_text("  收到  ", False, False, False, True, False) == "收到"
    assert repeatable_message_text("收到", True, False, False, True, False) is None
    assert repeatable_message_text("收到", False, True, False, True, False) is None
    assert repeatable_message_text("收到", False, False, True, True, False) is None
    assert repeatable_message_text("收到", False, False, False, False, False) is None
    assert repeatable_message_text("收到", False, False, False, True, True) is None
    assert repeatable_message_text("https://example.com", False, False, False, True, False) is None
    assert repeatable_message_text("www.example.com", False, False, False, True, False) is None
    assert repeatable_message_text("ftp://example.com/file", False, False, False, True, False) is None
    assert repeatable_message_text("example.com/path", False, False, False, True, False) is None
    assert repeatable_message_text("x" * 201, False, False, False, True, False) is None


def test_repeat_tracker_caps_active_message_states():
    tracker = RepeatTracker()

    for index in range(MAX_REPEAT_STATES + 1):
        tracker.record(1, index, f"message-{index}", now=float(index) / 100)

    assert len(tracker._states) == MAX_REPEAT_STATES
    assert (1, "message-0") not in tracker._states
