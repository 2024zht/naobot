from pathlib import Path

import pytest

from nao_bot.reactions import (
    list_reaction_pack_assets,
    reaction_image_base64,
    reaction_name_for_text,
    record_reaction_sent,
    select_random_reaction_asset,
    select_reaction_asset,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("你好，很高兴见到你。", "hello"),
        ("这次做得真不错，我也很开心。", "happy"),
        ("哈哈，这个说法确实很好笑。", "laugh"),
        ("让我想想，这件事需要仔细分析。", "thinking"),
        ("加油，坚持下去，你一定可以。", "cheer"),
        ("恭喜你顺利完成，太棒了！", "celebrate"),
        ("抱歉，这次是我理解错了。", "sorry"),
        ("哇，没想到结果居然是这样！", "surprise"),
    ],
)
def test_reaction_name_requires_a_strong_tone(text: str, expected: str):
    assert reaction_name_for_text(text) == expected


def test_reaction_name_ignores_neutral_answers():
    assert reaction_name_for_text("可以先检查配置文件，然后重新启动服务。") is None


def test_reaction_asset_has_a_per_user_cooldown(tmp_path: Path):
    asset = tmp_path / "celebrate.png"
    asset.touch()
    last_sent: dict[int, float] = {}

    assert select_reaction_asset("恭喜你成功了！", 1001, last_sent, tmp_path, now=100) == asset
    record_reaction_sent(last_sent, 1001, now=100)
    assert select_reaction_asset("恭喜你成功了！", 1001, last_sent, tmp_path, now=189) is None
    assert select_reaction_asset("恭喜你成功了！", 1002, last_sent, tmp_path, now=101) == asset
    record_reaction_sent(last_sent, 1002, now=101)
    assert select_reaction_asset("恭喜你成功了！", 1001, last_sent, tmp_path, now=190) == asset


def test_missing_asset_falls_back_without_consuming_cooldown(tmp_path: Path):
    last_sent: dict[int, float] = {}

    assert select_reaction_asset("抱歉，我理解错了。", 1001, last_sent, tmp_path, now=100) is None
    assert last_sent == {}

    asset = tmp_path / "sorry.png"
    asset.touch()
    assert select_reaction_asset("抱歉，我理解错了。", 1001, last_sent, tmp_path, now=100) == asset


def test_selection_does_not_consume_cooldown_before_send(tmp_path: Path):
    asset = tmp_path / "happy.png"
    asset.touch()
    last_sent: dict[int, float] = {}

    assert select_reaction_asset("这真不错！", 1001, last_sent, tmp_path, now=100) == asset
    assert last_sent == {}


def test_reaction_asset_can_be_embedded_as_base64(tmp_path: Path):
    asset = tmp_path / "hello.png"
    asset.write_bytes(b"sticker-bytes")

    assert reaction_image_base64(asset) == "c3RpY2tlci1ieXRlcw=="


def test_reaction_pack_lists_supported_images_in_order(tmp_path: Path):
    (tmp_path / "02.webp").touch()
    (tmp_path / "01.gif").touch()
    (tmp_path / "03.png").touch()
    (tmp_path / "notes.txt").touch()
    (tmp_path / "04.webp").mkdir()

    assert [asset.name for asset in list_reaction_pack_assets(tmp_path)] == [
        "01.gif",
        "02.webp",
        "03.png",
    ]


def test_random_reaction_asset_respects_chance_and_cooldown(tmp_path: Path, monkeypatch):
    asset = tmp_path / "01.webp"
    asset.touch()
    last_sent: dict[int, float] = {}
    monkeypatch.setattr("nao_bot.reactions.random.random", lambda: 0.19)

    assert (
        select_random_reaction_asset(
            1001,
            last_sent,
            tmp_path,
            now=100,
            chance=0.2,
        )
        == asset
    )
    assert last_sent == {}

    record_reaction_sent(last_sent, 1001, now=100)
    assert (
        select_random_reaction_asset(
            1001,
            last_sent,
            tmp_path,
            now=189,
            chance=0.2,
        )
        is None
    )


def test_random_reaction_asset_skips_roll_at_or_above_chance(tmp_path: Path, monkeypatch):
    (tmp_path / "01.webp").touch()
    monkeypatch.setattr("nao_bot.reactions.random.random", lambda: 0.2)

    assert (
        select_random_reaction_asset(
            1001,
            {},
            tmp_path,
            now=100,
            chance=0.2,
        )
        is None
    )
