import json
import os

from nao_bot.faq import MAX_FAQ_FILE_SIZE, FaqStore, format_help_with_faq


def _write_faq(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


def test_missing_faq_file_is_empty(tmp_path):
    store = FaqStore(tmp_path / "lab_faq.json")

    assert store.questions() == ()
    assert store.get("怎么预约仪器？") is None


def test_faq_store_hot_reloads_questions_and_answers(tmp_path):
    path = tmp_path / "lab_faq.json"
    _write_faq(path, {"怎么预约仪器？": "在实验室系统中预约。"})
    store = FaqStore(path)

    assert store.questions() == ("怎么预约仪器？",)
    assert store.get("怎么预约仪器？") == "在实验室系统中预约。"

    _write_faq(path, {"门禁怎么申请？": "联系实验室管理员。"})

    assert store.questions() == ("门禁怎么申请？",)
    assert store.get("怎么预约仪器？") is None
    assert store.get("门禁怎么申请？") == "联系实验室管理员。"


def test_invalid_faq_update_keeps_last_valid_content(tmp_path):
    path = tmp_path / "lab_faq.json"
    _write_faq(path, {"怎么预约仪器？": "在实验室系统中预约。"})
    store = FaqStore(path)
    assert store.get("怎么预约仪器？") == "在实验室系统中预约。"

    path.write_text("{broken", encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000))

    assert store.questions() == ("怎么预约仪器？",)
    assert store.get("怎么预约仪器？") == "在实验室系统中预约。"


def test_invalid_faq_entries_are_ignored_without_replacing_valid_content(tmp_path):
    path = tmp_path / "lab_faq.json"
    _write_faq(path, {"怎么预约仪器？": "在实验室系统中预约。"})
    store = FaqStore(path)
    assert store.questions() == ("怎么预约仪器？",)

    _write_faq(path, {"": "空问题", "合法问题？": 123})

    assert store.questions() == ("怎么预约仪器？",)


def test_oversized_faq_update_keeps_last_valid_content(tmp_path):
    path = tmp_path / "lab_faq.json"
    _write_faq(path, {"怎么预约仪器？": "在实验室系统中预约。"})
    store = FaqStore(path)
    assert store.questions() == ("怎么预约仪器？",)

    path.write_text(" " * (MAX_FAQ_FILE_SIZE + 1), encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000))

    assert store.questions() == ("怎么预约仪器？",)


def test_help_appends_faq_question_index_without_answers():
    help_text = format_help_with_faq(
        "nao 可用指令：\n@nao 帮助 - 查看指令",
        ("怎么预约仪器？", "门禁怎么申请？"),
    )

    assert help_text.endswith(
        "实验室问答：\n@nao 怎么预约仪器？\n@nao 门禁怎么申请？"
    )
    assert "在实验室系统中预约" not in help_text
