from pathlib import Path

import pytest

from nao_bot.reactions import (
    list_reaction_pack_assets,
    reaction_image_base64,
    reaction_name_for_text,
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


def test_reaction_asset_can_be_selected_for_consecutive_replies(tmp_path: Path):
    asset = tmp_path / "celebrate.png"
    asset.touch()

    assert select_reaction_asset("恭喜你成功了！", tmp_path) == asset
    assert select_reaction_asset("恭喜你成功了！", tmp_path) == asset


def test_missing_asset_falls_back_to_text(tmp_path: Path):
    assert select_reaction_asset("抱歉，我理解错了。", tmp_path) is None

    asset = tmp_path / "sorry.png"
    asset.touch()
    assert select_reaction_asset("抱歉，我理解错了。", tmp_path) == asset


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


def test_random_reaction_asset_can_trigger_for_consecutive_replies(tmp_path: Path, monkeypatch):
    asset = tmp_path / "01.webp"
    asset.touch()
    monkeypatch.setattr("nao_bot.reactions.random.random", lambda: 0.19)

    assert select_random_reaction_asset(tmp_path, chance=0.2) == asset
    assert select_random_reaction_asset(tmp_path, chance=0.2) == asset


def test_random_reaction_asset_skips_roll_at_or_above_chance(tmp_path: Path, monkeypatch):
    (tmp_path / "01.webp").touch()
    monkeypatch.setattr("nao_bot.reactions.random.random", lambda: 0.2)

    assert (
        select_random_reaction_asset(tmp_path, chance=0.2)
        is None
    )
