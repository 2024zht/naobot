from dataclasses import dataclass, field
from time import monotonic

from .rules import proactive_message_text


REPEAT_WINDOW_SECONDS = 30
REPEAT_DISTINCT_USERS = 3
MAX_REPEAT_STATES = 500


@dataclass
class _RepeatState:
    senders: dict[int, float] = field(default_factory=dict)
    last_seen: float = 0
    repeated: bool = False


class RepeatTracker:
    def __init__(self) -> None:
        self._states: dict[tuple[int, str], _RepeatState] = {}
        self._generations: dict[int, int] = {}

    def record(
        self,
        group_id: int,
        user_id: int,
        text: str,
        now: float | None = None,
    ) -> bool:
        timestamp = monotonic() if now is None else now
        self._cleanup(timestamp)
        key = (group_id, text)
        if key not in self._states and len(self._states) >= MAX_REPEAT_STATES:
            oldest_key = min(self._states, key=lambda item: self._states[item].last_seen)
            del self._states[oldest_key]
        state = self._states.setdefault(key, _RepeatState(last_seen=timestamp))
        state.senders = {
            sender_id: seen_at
            for sender_id, seen_at in state.senders.items()
            if timestamp - seen_at <= REPEAT_WINDOW_SECONDS
        }
        state.senders[user_id] = timestamp
        state.last_seen = timestamp
        if state.repeated or len(state.senders) < REPEAT_DISTINCT_USERS:
            return False

        state.repeated = True
        self._generations[group_id] = self.generation(group_id) + 1
        return True

    def generation(self, group_id: int) -> int:
        return self._generations.get(group_id, 0)

    def _cleanup(self, now: float) -> None:
        expired = [
            key
            for key, state in self._states.items()
            if now - state.last_seen > REPEAT_WINDOW_SECONDS
        ]
        for key in expired:
            del self._states[key]


def repeatable_message_text(
    text: str,
    is_tome: bool,
    is_self: bool,
    has_automatic_reply: bool,
    is_plain_text: bool,
    is_reply: bool,
) -> str | None:
    if not is_plain_text or is_reply:
        return None
    return proactive_message_text(text, is_tome, is_self, has_automatic_reply)
