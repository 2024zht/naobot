import json
import logging
from pathlib import Path


MAX_FAQ_ENTRIES = 30
MAX_FAQ_QUESTION_LENGTH = 100
MAX_FAQ_ANSWER_LENGTH = 1000
MAX_FAQ_FILE_SIZE = 64 * 1024
_UNSET = object()
logger = logging.getLogger(__name__)


class FaqStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._items: dict[str, str] = {}
        self._signature: object | tuple[int, int] | None = _UNSET

    def get(self, question: str) -> str | None:
        self._refresh()
        return self._items.get(question.strip())

    def questions(self) -> tuple[str, ...]:
        self._refresh()
        return tuple(self._items)

    def _refresh(self) -> None:
        try:
            stat = self.path.stat()
            signature: tuple[int, int] | None = (stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            signature = None
        except OSError as error:
            logger.warning("Unable to inspect lab FAQ file %s: %s", self.path, error)
            return
        if signature == self._signature:
            return
        self._signature = signature
        if signature is None:
            self._items = {}
            return

        try:
            if signature[1] > MAX_FAQ_FILE_SIZE:
                raise ValueError(f"FAQ file cannot exceed {MAX_FAQ_FILE_SIZE} bytes")
            items = self._read()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            logger.warning("Ignoring invalid lab FAQ file %s: %s", self.path, error)
            return
        self._items = items

    def _read(self) -> dict[str, str]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("FAQ root must be a JSON object")
        if len(data) > MAX_FAQ_ENTRIES:
            raise ValueError(f"FAQ cannot contain more than {MAX_FAQ_ENTRIES} entries")

        items: dict[str, str] = {}
        for raw_question, raw_answer in data.items():
            if not isinstance(raw_question, str) or not isinstance(raw_answer, str):
                raise ValueError("FAQ questions and answers must be strings")
            question = raw_question.strip()
            answer = raw_answer.strip()
            if not question or not answer:
                raise ValueError("FAQ questions and answers cannot be empty")
            if "\n" in question or len(question) > MAX_FAQ_QUESTION_LENGTH:
                raise ValueError(
                    f"FAQ questions must be one line and at most {MAX_FAQ_QUESTION_LENGTH} characters"
                )
            if len(answer) > MAX_FAQ_ANSWER_LENGTH:
                raise ValueError(
                    f"FAQ answers cannot exceed {MAX_FAQ_ANSWER_LENGTH} characters"
                )
            if question in items:
                raise ValueError("FAQ contains duplicate normalized questions")
            items[question] = answer
        return items


def format_help_with_faq(help_text: str, questions: tuple[str, ...]) -> str:
    if not questions:
        return help_text
    question_lines = "\n".join(f"@nao {question}" for question in questions)
    return f"{help_text}\n\n实验室问答：\n{question_lines}"
