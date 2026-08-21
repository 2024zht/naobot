import asyncio
import importlib
import os
from types import SimpleNamespace

import pytest


pytest.importorskip("nonebot")


def test_group_file_is_deleted_when_video_scan_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("NAO_TEST_GROUP_ID", "123456789")
    monkeypatch.setenv("NAO_ADMIN_QQ_IDS", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("NAO_KEYWORDS_FILE", str(tmp_path / "keywords.json"))
    monkeypatch.setenv("NAO_MODERATION_FILE", str(tmp_path / "moderation.json"))
    monkeypatch.setenv("NAO_FRAUD_KEYWORDS_FILE", str(tmp_path / "fraud_keywords.json"))
    monkeypatch.setenv("NAO_REMINDERS_FILE", str(tmp_path / "reminders.sqlite3"))
    monkeypatch.setenv("NAO_LAB_FAQ_FILE", str(tmp_path / "lab_faq.json"))
    monkeypatch.setenv("NAO_REACTION_CATALOG_FILE", str(tmp_path / "reaction_catalog.json"))
    monkeypatch.setenv("NAO_REACTION_PACK_ROOT", str(tmp_path / "reaction_packs"))

    plugin = importlib.import_module("nao_bot.plugin")
    deleted = []

    class FakeBot:
        async def get_group_file_download_url(self, **_kwargs):
            return "https://example.invalid/video.mp4"

        async def delete_group_file(self, **kwargs):
            deleted.append(kwargs)

    event = SimpleNamespace(
        data=SimpleNamespace(peer_id=123456789),
        get_message=lambda: [
            SimpleNamespace(
                type="file",
                data={"file_id": "file-123", "file_name": "clip.mp4"},
            )
        ],
    )

    async def fail_scan(_url, _should_stop):
        raise RuntimeError("scan failed")

    monkeypatch.setattr(plugin, "_first_video_url", lambda _bot, _event: asyncio.sleep(0, result=None))
    monkeypatch.setattr(plugin, "scan_video_url", fail_scan)

    assert asyncio.run(plugin._detect_violation(FakeBot(), event)) is None
    assert deleted == [{"group_id": 123456789, "file_id": "file-123"}]
