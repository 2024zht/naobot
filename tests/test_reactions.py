from pathlib import Path

import pytest

from nao_bot.reactions import reaction_name_for_text, select_reaction_asset


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
    assert select_reaction_asset("恭喜你成功了！", 1001, last_sent, tmp_path, now=189) is None
    assert select_reaction_asset("恭喜你成功了！", 1002, last_sent, tmp_path, now=101) == asset
    assert select_reaction_asset("恭喜你成功了！", 1001, last_sent, tmp_path, now=190) == asset


def test_missing_asset_falls_back_without_consuming_cooldown(tmp_path: Path):
    last_sent: dict[int, float] = {}

    assert select_reaction_asset("抱歉，我理解错了。", 1001, last_sent, tmp_path, now=100) is None
    assert last_sent == {}

    asset = tmp_path / "sorry.png"
    asset.touch()
    assert select_reaction_asset("抱歉，我理解错了。", 1001, last_sent, tmp_path, now=100) == asset
